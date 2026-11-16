#!/usr/bin/env python3
"""SIMB1C 直调驱动器：停点世界里的 RCSP 命令直调面（docs/sim-m1-direct-spec.md §2）。

在 explore(..., post_stop=serve_loop) 的停点世界上，把 app 的 BLE 命令翻译成对真实
固件函数的受控直调（S 先例 tools/probe_irq_entry.py:846-876 的同语义独立实现——禁区
文件里的场景函数不 import，形状逐行对齐）：

  - direct_call：全量保存 pc/sp/rets/flags/R → 设 pc 与 r0..r3 → rets=哨兵 →
    decode→step→machine.after_step 受控步进（不调 hardware.advance，外设时间冻结
    =中断交付冻结）。三条纪律（设计 §4.1，S 先例之上补齐）：
    ① 成功返回先校验 SP 平衡（哨兵处 sp==入口 sp，全程 min_sp 记账）再恢复；
    ② 调用前置门：CPU0 / sp_known / 非 IRQ handler 上下文 / 无在途并行对与
       repeat 体——任一不满足拒绝直调（world_not_ready，不碰世界）；
    ③ Blocked/预算耗尽/SP 不平衡 → 世界标记不可继续（world_dead），finally 只
       恢复寄存器，存储副作用留在世界里（靠快照重建收场）。
  - rcsp_read：读命令直调分发器 0x600f85e 读路径（FE 01 cmdid→表 walk→getter→
    包装，全程无 pend，冻结世界可跑），在 0x600b66e 入口 watch 拦回执载荷。
  - rcsp_write：写命令查表 A（世界 flash 实读）→ T1 setter 直调（分发器写路径
    post+pend 在冻结世界必 Blocked，setter 全族无 pend）；写静默不合成回执。
  - resume_steps：M1-c 解冻续跑 hook（explore 主循环同款步进含 hardware.advance
    与交付分发；全环验收留待 JLEQ 合并后重钉世界）。

地址/形状出处：docs/sim-m1-pre-study.md（SIMPRE）、docs/rcsp-return-path.md（RCBACK）、
docs/sim-m1-direct-spec.md §7 证据锚点索引；本文件不发明地址。
"""
from __future__ import annotations

import struct

from probe_boot import Blocked

DIRECT_SENTINEL = 0x00100000     # ROM(0..0x20000)与 flash(0x06000100..)之间未映射空洞，
                                 # 直调循环 decode 之前检查 pc==哨兵，从不取指 [文档：S 先例注释]

# ---- 固件地址（全部既有钉死，出处见 docstring） ----
DISPATCHER = 0x0600F85E          # RCSP 自定义命令分发器（读/写双路径）
RETURN_EXIT = 0x600B66E          # 恳求回执公共出口（R1 拦截点，入口先于池门）
NOTIFY_EXIT = 0x6009F6A          # 无恳求通知出口（R2 拦截点）
GATE3 = 0x60065BC                # return b[0x1265C0+5]（单读叶子，空直调守卫目标）
EQ_APPLY = 0x6025118             # EQ apply（0x22 setter 内联调用，无参；深栈探针目标）
EQ_APPLY_INNER = 0x602503A       # apply 内层
ANC_GETTER = 0x6025C84           # 0x17 getter：读 b[0x12666B..D] 三字节恒返 3（烟雾读）
POST_WRAPPER = 0x6064978         # post(r0=任务名,r1=event,r2=nwords,r3=参数字指针)
POSTER_QUEUE_READ = 0x60648C2    # poster 体内 r1=h[r4+8] 处（r4=任务队列对象，W4 推导面）
TABLE_A_BEGIN, TABLE_A_END = 0x060A9B78, 0x060A9D1C   # 15×28B 注册表
TABLE_A_STRIDE = 28

# ---- 配置镜像面（world_inspect 白名单 + 写效果断言面） ----
CONFIG_BASE = 0x1265C0
EQ_MIRROR = CONFIG_BASE + 5356            # 0x22 setter memcpy 落点（3B 头+7B/段）
EQ_SEGMENTS = CONFIG_BASE + 5499          # EQ 段数 N
ANC_WIRE = CONFIG_BASE + 171              # 0x12666B..D，0x17 setter 直存
ANC_CTX_PTR = CONFIG_BASE + 500           # ANC ctx（两级：先读指针，+33=模式）
POOL_PTR = CONFIG_BASE + 1004             # BT 缓冲池 ctx（读命令池门诊断）
CONFIG_WINDOW = 8192                      # config 家族白名单窗宽（覆盖上列全部 +5..+7300 面）

WATCH_READ = (RETURN_EXIT, NOTIFY_EXIT)
WATCH_WRITE = (EQ_APPLY, EQ_APPLY_INNER, POST_WRAPPER, POSTER_QUEUE_READ)

# ---- scratch（§2.5：bss 清零区 [0x125320,0x12A62C) 内的死窗候选，守卫可换址） ----
# 选址约束：已映射可写 RAM；不在 callee 下行栈路径上；不碰 config 池域与镜像面。
# 默认取 bss 尾部（远离 0x1265C0 config 家族），空直调守卫+逐命令 diff 归因兜底。
SCRATCH_RESP_DEFAULT = 0x12A540           # setter ABI r0=resp 基址（128B 预留，ABI 义务）
SCRATCH_RESP_SIZE = 128
SCRATCH_BODY_DEFAULT = 0x12A5C0           # 读 body 3B / 写 data ≤73B
SCRATCH_BODY_SIZE = 96                    # bss 顶 0x12A62C 之下留 0xC 余量

# ---- world_diff 区段归因表（§2.4） ----
HEAP_BEGIN, HEAP_END = 0x134500, 0x19FB80          # physical_heap region
STACK_BEGIN, STACK_TOP = 0x123000, 0x123840        # bootstrap stack region
IRQ_VECTOR_BEGIN, IRQ_VECTOR_END = 0x19FE00, 0x1A0000
MMU_PT_BEGIN, MMU_PT_END = 0x120000, 0x122800
BSS_BEGIN, BSS_END = 0x125320, 0x12A62C


class DriverError(RuntimeError):
    """驱动器错误（code 见 §3.4 错误族：world_not_ready/read_shape/not_whitelisted…）。"""

    def __init__(self, code, **detail):
        self.code = code
        self.detail = {k: (hex(v) if isinstance(v, int) else v) for k, v in detail.items()}
        super().__init__('%s %r' % (code, detail))


class WorldDead(DriverError):
    """世界不可继续（direct_call Blocked/预算耗尽/SP 不平衡/resume 段失败）——
    存储副作用留在世界里，靠外层快照重建收场，不假装恢复寄存器=恢复世界。"""

    def __init__(self, code='world_dead', **detail):
        super().__init__(code, **detail)


def _hex(value):
    """寄存器/地址的可 JSON 化形状：int→hex，UnknownWord 等非 int→str（如实标注）。"""
    if value is None:
        return None
    if isinstance(value, int):
        return hex(value)
    return 'unknown:%s' % type(value).__name__


class SimWorldDriver:
    """停点世界直调驱动器（SIMB1C；对接 sim_bridge_server 的状态源槽位）。

    公共 API 面（全部走 cpu/machine 公共属性，不 import 禁区场景函数）：
      read(cmdid, sn) / apply_write(cmdid, data) / describe()
      direct_call / resume_steps / world_inspect / world_diff / table_a_entry
    """

    def __init__(self, machine, hardware, cpu, *, scratch_resp=None, scratch_body=None):
        self.machine = machine
        self.hardware = hardware
        self.cpu = cpu
        self.dead = False
        self.baseline_ram = None            # world_diff 基线（首条命令前快照）
        self.watch_log = []                 # 跨命令 watch 命中流水（验收记录）
        self.command_count = 0
        self.stop_pc = cpu.pc
        self.entry_sp = cpu.sp if getattr(cpu, 'sp_known', False) else None
        self.min_sp_seen = self.entry_sp    # 全部直调段的栈下界记账
        self.queue_base = None              # app_core 任务队列（0x17 post 后由 watch 推导）
        self.extra_windows = []             # 运行时解析的 inspect 白名单窗
        self.scratch_resp = scratch_resp or SCRATCH_RESP_DEFAULT
        self.scratch_body = scratch_body or SCRATCH_BODY_DEFAULT
        self._resolve_pointer_windows()

    # ------------------------------------------------------------------ 基础面
    def _die(self, code, **detail):
        self.dead = True
        raise WorldDead(code, **detail)

    def mem_read(self, address, size):
        try:
            return self.cpu.mem.read(address, size)
        except Blocked as exc:
            self._die('world_dead', cause='mem_read_blocked', stop=exc.details)

    def mem_write(self, address, data):
        try:
            self.cpu.mem.write(address, bytes(data))
        except Blocked as exc:
            self._die('world_dead', cause='mem_write_blocked', stop=exc.details)

    def _resolve_pointer_windows(self):
        """两级指针面解析（ANC ctx / BT 池）：指针可读且指向堆内时加进白名单。
        读失败=面不存在（可选诊断面，不构成世界死亡——直调读写路径不依赖它）。"""
        for name, slot, length in (('anc_ctx', ANC_CTX_PTR, 572), ('bt_pool', POOL_PTR, 96)):
            try:
                data = self.cpu.mem.read(slot, 4)
            except Blocked:
                continue
            pointer = struct.unpack('<I', data)[0]
            if HEAP_BEGIN <= pointer < HEAP_END:
                self.extra_windows.append((name, pointer, min(pointer + length, HEAP_END)))

    # ------------------------------------------------------------------ direct_call（§2.1）
    def _precheck(self):
        cpu = self.cpu
        problems = []
        if cpu.core_id != 0:
            problems.append('core_id=%r' % (cpu.core_id,))
        if not getattr(cpu, 'sp_known', False):
            problems.append('sp_unknown')
        if getattr(cpu, 'active_context', None) is not None:
            problems.append('active_context=%r' % (cpu.active_context,))
        if getattr(cpu, 'after_then', None) is not None or getattr(cpu, 'repeat', None) is not None:
            problems.append('parallel_pair_or_repeat_in_flight')
        if problems:
            raise DriverError('world_not_ready', problems=problems)

    def direct_call(self, pc, args, *, max_steps=1_000_000, watch=(), need_return=True, label=None):
        """在停点世界 CPU0 上直调真实固件函数，返回 (r0, steps, watch_hits)。"""
        if self.dead:
            raise WorldDead('world_already_dead')
        self._precheck()
        cpu, machine = self.cpu, self.machine
        saved = {'pc': cpu.pc, 'sp': cpu.sp, 'rets': cpu.rets, 'flags': cpu.flags, 'R': list(cpu.R)}
        entry_sp = cpu.sp
        cpu.pc = pc
        for index, value in enumerate(args):
            cpu.R[index] = value
        cpu.rets = DIRECT_SENTINEL
        watch_set = set(watch)
        steps = 0
        watch_hits = []
        min_sp = entry_sp
        try:
            while cpu.pc != DIRECT_SENTINEL:
                if watch_set and cpu.pc in watch_set:
                    watch_hits.append({'pc': hex(cpu.pc), 'r': [_hex(value) for value in cpu.R[:7]],
                                       'step': steps, 'label': label})
                if steps >= max_steps:
                    self._die('direct_call_budget', pc=hex(pc), steps=steps, label=label)
                insn, _raw, _size = cpu.decode(cpu.pc)
                if insn.id == 'idle':
                    # 冻结世界无法被交付唤醒：callee 撞 idle = 不可继续（如实报，不猜）。
                    self._die('direct_call_idle', pc=hex(cpu.pc), steps=steps, label=label)
                before = cpu.pc
                cpu.step()
                steps += 1
                if isinstance(cpu.sp, int) and cpu.sp < min_sp:
                    min_sp = cpu.sp
                machine.after_step(cpu, before)
            # 纪律①：成功返回先校验 SP 平衡再恢复。返回值在恢复寄存器之前取
            # （S 先例形状：哨兵处 r0）。
            result = cpu.R[0] if isinstance(cpu.R[0], int) else None
            if cpu.sp != entry_sp:
                self._die('direct_call_sp_unbalanced', pc=hex(pc), entry_sp=_hex(entry_sp),
                          exit_sp=_hex(cpu.sp), label=label)
        except WorldDead:
            raise
        except Blocked as exc:
            self._die('direct_call_blocked', pc=hex(pc), stop=exc.details, label=label)
        except Exception as exc:             # 上游模拟器未预期异常：世界不可继续（§3.3）
            self._die('direct_call_simulator_failure', pc=hex(pc),
                      error_type=type(exc).__name__, message=str(exc)[:200], label=label)
        finally:
            # 纪律③：finally 只恢复寄存器；存储副作用留在世界里。
            cpu.pc = saved['pc']
            cpu.sp = saved['sp']
            cpu.rets = saved['rets']
            cpu.flags = saved['flags']
            cpu.R = saved['R']
        if isinstance(min_sp, int) and (self.min_sp_seen is None or min_sp < self.min_sp_seen):
            self.min_sp_seen = min_sp
        if not need_return:
            result = None
        self.watch_log.extend(watch_hits)
        return result, steps, watch_hits

    # ------------------------------------------------------------------ 读命令（§2.2）
    def read(self, cmdid, sn):
        """读命令全固件路径直调；返回回执数据段 cmdid|n|payload，n<1 → None（静默）。"""
        self.command_count += 1
        body = bytes([0xFE, 0x01, cmdid & 0xff])
        self.mem_write(self.scratch_body, body)
        _r0, _steps, hits = self.direct_call(
            DISPATCHER, [sn & 0xff, self.scratch_body, 0, len(body)],
            max_steps=200_000, watch=WATCH_READ, label='read:0x%02x' % cmdid)
        hit = next((h for h in hits if h['pc'] == hex(RETURN_EXIT)), None)
        if hit is None:
            return None                      # n<1 静默（getter 返 -1/<1 同此）
        try:
            r0_hit, _status, sn_echo, resp = (int(value, 16) for value in hit['r'][:4])
        except ValueError:
            self._die('read_shape', registers=hit['r'][:4])
        head = self.mem_read(resp, 2)
        n = head[1]
        shape = {'r0': _hex(r0_hit), 'sn_echo': _hex(sn_echo), 'resp': _hex(resp),
                 'resp_cmdid': _hex(head[0]), 'n': n}
        if r0_hit != 0xFF or sn_echo != (sn & 0xff) or head[0] != (cmdid & 0xff) or not 1 <= n <= 126:
            self._die('read_shape', **shape)
        return self.mem_read(resp, n + 2)

    # ------------------------------------------------------------------ 写命令（§2.3）
    def table_a_entry(self, cmdid):
        """表 A 实读解析（15×28B，{+0 init,+4 getter,+8 setter,+20 恢复默认,+24 cmdid}）。"""
        cmdid &= 0xff
        for entry in range(TABLE_A_BEGIN, TABLE_A_END, TABLE_A_STRIDE):
            fields = struct.unpack('<IIIII', self.mem_read(entry, 20))
            if self.mem_read(entry + 24, 1)[0] == cmdid:
                return {'cmdid': cmdid, 'init': fields[0], 'getter': fields[1], 'setter': fields[2],
                        'restore': fields[3], 'slot32': fields[4], 'entry': entry}
        return None

    def apply_write(self, cmdid, data):
        """T1 写：表 A 实读 → setter 直调；返回验收断言面 dict（写静默，不合成回执）。"""
        self.command_count += 1
        entry = self.table_a_entry(cmdid)
        if entry is None:
            return {'setter': 0, 'note': 'cmdid 不在表 A（固件同款表 walk 未命中静默）', 'cmdid': cmdid}
        setter = entry['setter']
        if setter == 0:
            return {'setter': 0, 'note': '官方槽空，静默跳尾（固件同款 0x600f8b4）',
                    'cmdid': cmdid, 'entry': hex(entry['entry'])}
        data = bytes(data)
        if len(data) > SCRATCH_BODY_SIZE:
            raise DriverError('write_data_too_long', length=len(data), scratch=SCRATCH_BODY_SIZE)
        self.mem_write(self.scratch_body, data)
        before = self._queue_face()
        r0, steps, hits = self.direct_call(
            setter, [self.scratch_resp, 128, self.scratch_body, len(data)],
            max_steps=2_000_000, watch=WATCH_WRITE, label='write:0x%02x' % cmdid)
        self._note_queue_watch(hits)
        result = {'setter': hex(setter), 'n': r0, 'steps': steps, 'watch_hits': hits,
                  'cmdid': cmdid, 'data_len': len(data), 'entry': hex(entry['entry']),
                  'setter_returned_nonzero': r0 not in (0, None),
                  'queue_before': before, 'queue_after': self._queue_face()}
        if r0 not in (0, None):
            # 静态结论（13 setter 恒返 0）之外的动态形状：如实标注回报，不臆造回执。
            result['note'] = ('setter 返非 0：静态结论外的动态形状，回报主协调'
                              '（死代码 ABI：载荷应在 resp+2，本 server 不合成回执）')
        self.scratch_stack_check()
        return result

    # ------------------------------------------------------------------ 状态面（§2.4）
    def inspect_windows(self):
        windows = [('config', CONFIG_BASE, CONFIG_BASE + CONFIG_WINDOW),
                   ('table_a', TABLE_A_BEGIN, TABLE_A_END),
                   ('scratch_resp', self.scratch_resp, self.scratch_resp + SCRATCH_RESP_SIZE),
                   ('scratch_body', self.scratch_body, self.scratch_body + SCRATCH_BODY_SIZE),
                   ('heap', HEAP_BEGIN, HEAP_END),
                   ('bootstrap_stack', STACK_BEGIN, STACK_TOP)]
        if self.queue_base is not None:
            windows.append(('app_core_queue', self.queue_base, self.queue_base + 64))
        return windows + self.extra_windows

    def world_inspect(self, address, size):
        """白名单代理读：[addr,addr+size) 必须整个落在某个白名单窗内。"""
        if self.dead:
            raise WorldDead('world_already_dead')
        if size <= 0 or size > 4096:
            raise DriverError('bad_size', size=size)
        for name, low, high in self.inspect_windows():
            if low <= address and address + size <= high:
                return self.mem_read(address, size)
        raise DriverError('not_whitelisted', address=hex(address), size=size)

    def take_baseline(self):
        self.baseline_ram = dict(self.cpu.mem.ram)

    def world_diff(self):
        """相对基线（首条命令前）的写集，按区段归因；unattributed 为归因外变化。"""
        if self.baseline_ram is None:
            raise DriverError('no_baseline')
        base, cur = self.baseline_ram, self.cpu.mem.ram
        changed = {addr for addr, value in cur.items() if base.get(addr) != value}
        changed.update(addr for addr in base if addr not in cur)
        regions = {
            'config_mirror': (CONFIG_BASE, CONFIG_BASE + CONFIG_WINDOW),
            'scratch': (min(self.scratch_resp, self.scratch_body),
                        max(self.scratch_resp + SCRATCH_RESP_SIZE, self.scratch_body + SCRATCH_BODY_SIZE)),
            'direct_call_stack': (self.min_sp_seen if self.min_sp_seen is not None else STACK_TOP,
                                  self.entry_sp if self.entry_sp is not None else STACK_TOP),
            'bss_other': (BSS_BEGIN, BSS_END),
            'data_init': (0x124840, BSS_BEGIN),   # qcy_initialized_data：0x17 归一化写 0x124C81..2 等落此域
            'heap': (HEAP_BEGIN, HEAP_END),
            'core1_stack': (STACK_TOP, 0x124840),  # dual_case CORE1_STACK 区（resume 后 CPU1 活动面）
            'bootstrap_stack': (STACK_BEGIN, STACK_TOP),
            'mmu_page_table': (MMU_PT_BEGIN, MMU_PT_END),
            'irq_vector_table': (IRQ_VECTOR_BEGIN, IRQ_VECTOR_END),
        }
        if self.queue_base is not None:
            regions['app_core_queue'] = (self.queue_base, self.queue_base + 64)
        attribution = {name: [] for name in regions}
        unattributed = []
        for addr in sorted(changed):
            for name, (low, high) in regions.items():
                if low <= addr < high:
                    attribution[name].append(addr)
                    break
            else:
                unattributed.append(addr)
        return {'changed_bytes': len(changed),
                'regions': {name: {'bytes': len(addrs),
                                   'first': hex(addrs[0]) if addrs else None,
                                   'last': hex(addrs[-1]) if addrs else None}
                            for name, addrs in attribution.items() if addrs},
                'unattributed': [hex(addr) for addr in unattributed[:32]],
                'unattributed_count': len(unattributed)}

    # ------------------------------------------------------------------ scratch 守卫（§2.5）
    def run_scratch_guard(self, *, deep=False):
        """空直调守卫（必跑）：直调 gate3 单读叶子，diff 必须为空（不含 scratch——
        守卫不写 scratch）。deep=True 追加深栈探针（apply 0x6025118，天然探针）。
        返回守卫记录 dict；探针 Blocked 属 D2 风险面的 verdict 事件，由调用方
        决定是否前置（默认 world 服务只跑空直调守卫，深栈证据随 M1-b B1 采集）。"""
        pre = dict(self.cpu.mem.ram)
        r0, steps, hits = self.direct_call(GATE3, [], max_steps=64, label='guard_empty')
        stray = self._diff_addrs(pre)
        guard = {'empty_call': {'r0': _hex(r0), 'steps': steps, 'watch_hits': hits,
                                'stray_writes': [hex(a) for a in stray[:32]],
                                'stray_count': len(stray)},
                 'entry_sp': _hex(self.entry_sp), 'min_sp_seen': _hex(self.min_sp_seen)}
        if stray:
            self._die('scratch_guard_stray_writes', count=len(stray))
        if deep:
            probe_r0, probe_steps, probe_hits = self.direct_call(
                EQ_APPLY, [], max_steps=2_000_000, watch=(EQ_APPLY_INNER,), label='guard_deep')
            guard['deep_probe'] = {'r0': _hex(probe_r0), 'steps': probe_steps,
                                   'watch_hits': probe_hits, 'min_sp': _hex(self.min_sp_seen)}
        guard['scratch_stack_disjoint'] = self.scratch_stack_check()
        return guard

    def scratch_stack_check(self):
        """scratch 窗不得与任一直调段的栈下探窗 [min_sp, entry_sp) 相交（§2.5 纪律）。"""
        if self.min_sp_seen is None or self.entry_sp is None:
            return True
        low, high = self.min_sp_seen, self.entry_sp
        windows = ((self.scratch_resp, self.scratch_resp + SCRATCH_RESP_SIZE),
                   (self.scratch_body, self.scratch_body + SCRATCH_BODY_SIZE))
        overlap = [hex(a) for a in range(low, high) if any(lo <= a < hi for lo, hi in windows)]
        if overlap:
            self._die('scratch_stack_overlap', overlap=overlap[:16],
                      min_sp=_hex(low), entry_sp=_hex(high))
        return True

    def smoke_read_anc(self):
        """世界就绪烟雾读：直调 0x17 getter（无前置门，恒返 3，读 b[0x12666B..D]）。"""
        buffer = self.scratch_resp + SCRATCH_RESP_SIZE - 16
        r0, steps, hits = self.direct_call(ANC_GETTER, [buffer, 16], max_steps=256,
                                           label='smoke:0x17_getter')
        wire = bytes(self.mem_read(ANC_WIRE, 3))
        payload = bytes(self.mem_read(buffer, 3))
        record = {'r0': r0, 'steps': steps, 'watch_hits': hits, 'wire': list(wire),
                  'payload': list(payload)}
        if r0 != 3 or payload != wire:
            self._die('smoke_read_mismatch', **{k: v for k, v in record.items() if k != 'watch_hits'})
        return record

    # ------------------------------------------------------------------ 队列推导（W4）
    def _queue_face(self):
        """app_core 队列观察面：{计数, 头, 容量, 缓冲指针, 缓冲头 8 字}（未推导时 None）。"""
        if self.queue_base is None:
            return None
        q = self.queue_base
        head = struct.unpack('<H', self.mem_read(q + 8, 2))[0]
        cap = struct.unpack('<H', self.mem_read(q + 12, 2))[0]
        count = struct.unpack('<H', self.mem_read(q + 14, 2))[0]
        buf = struct.unpack('<I', self.mem_read(q + 16, 4))[0]
        words = [struct.unpack('<I', self.mem_read(buf + 4 * i, 4))[0]
                 for i in range(min(head + 8, cap))]
        return {'queue': hex(q), 'count': count, 'head': head, 'capacity': cap,
                'buffer': hex(buf), 'buffer_words': [hex(w) for w in words]}

    def _note_queue_watch(self, hits):
        """从 watch 命中推导 app_core 队列基址（poster 0x60648c2 处 r4=队列对象，
        静态钉死：0x60648b0 r1=h[r4+12] 起的队列几何 +8 头/+12 容量/+14 计数/+16 缓冲）。"""
        for hit in hits:
            if hit['pc'] == hex(POSTER_QUEUE_READ) and len(hit['r']) > 4 and hit['r'][4] is not None:
                candidate = int(hit['r'][4], 16)
                if HEAP_BEGIN <= candidate < HEAP_END:
                    self.queue_base = candidate
                break

    # ------------------------------------------------------------------ resume（§2.6，M1-c hook）
    def resume_steps(self, steps, watch=()):
        """解冻续跑：explore 主循环最小复刻（decode→step→after_step→hardware.advance，
        含交付分发）。JLEQ 合并后按届时 probe_boot 主循环形状校准（§5.3）。"""
        if self.dead:
            raise WorldDead('world_already_dead')
        cpu, machine, hardware = self.cpu, self.machine, self.hardware
        watch_set = set(watch)
        hits = []
        completed = 0
        stop = None
        while completed < steps:
            cpu = machine.select()
            if watch_set and cpu.pc in watch_set:
                hits.append({'pc': hex(cpu.pc), 'r': [_hex(value) for value in cpu.R[:7]],
                             'step': completed, 'label': 'resume'})
            try:
                insn, _raw, _size = cpu.decode(cpu.pc)
                before = cpu.pc
                cpu.step()
                completed += 1
                if isinstance(cpu.sp, int) and (self.min_sp_seen is None or cpu.sp < self.min_sp_seen):
                    self.min_sp_seen = cpu.sp
                machine.after_step(cpu, before)
                hardware.advance(cpu.icfg)
            except Exception as exc:
                name = type(exc).__name__
                if name == 'CoreStall':
                    machine.note_stall(cpu, getattr(exc, 'details', {}))
                    machine.after_step()
                    try:
                        hardware.advance(cpu.icfg)
                    except Exception as advance_exc:
                        stop = self._resume_stop(advance_exc)
                elif name == 'IrqDeliveryRequest':
                    self._dispatch_delivery(getattr(exc, 'details', {}))
                else:
                    stop = self._resume_stop(exc)
            if stop is not None:
                break
        self.watch_log.extend(hits)
        # 解冻推进了世界：后续直调发生在新上下文（direct_call 每次重跑前置门）。
        self.entry_sp = cpu.sp if getattr(cpu, 'sp_known', False) else None
        if stop is not None:
            self._die('resume_stopped', stop=stop, steps=completed)
        return {'steps': completed, 'watch_hits': hits}

    def _resume_stop(self, exc):
        if isinstance(exc, Blocked):
            return exc.details
        return {'kind': 'upstream_simulator_failure', 'error_type': type(exc).__name__,
                'message': str(exc)[:200]}

    def _dispatch_delivery(self, details):
        """交付分发（explore dispatch_delivery 的最小复刻：mid/in-flight 延期 + 按 irq 分派）。"""
        machine = self.machine
        target = machine.cores.get(details.get('core'))
        mid_instruction = (target is not None and
                           (getattr(target, 'after_then', None) is not None or
                            getattr(target, 'repeat', None) is not None))
        in_flight = (target is not None and
                     getattr(target, 'active_context', None) in
                     ('software_irq_handler', 'core_tick_handler', 'timer0_irq_handler'))
        if mid_instruction or in_flight:
            if self.hardware is not None:
                if details.get('irq') == 3:
                    self.hardware.core_ticks[details['core']].delivery_in_flight = False
                elif details.get('irq') == 4:
                    self.hardware.timer.delivery_in_flight = False
            return                                  # 延期：下个 advance 以同一 pending 重新请求
        irq = details.get('irq')
        if irq == 3:
            machine.deliver_core_tick(details['core'])
        elif irq == 4:
            machine.deliver_timer0_irq(details['core'])
        else:
            machine.deliver_software_irq(target, irq)

    # ------------------------------------------------------------------ 横幅
    def describe(self):
        return {'mode': 'direct-world', 'dead': self.dead,
                'stop_pc': hex(self.stop_pc), 'entry_sp': _hex(self.entry_sp),
                'commands': self.command_count,
                'scratch': {'resp': hex(self.scratch_resp), 'body': hex(self.scratch_body)},
                'min_sp_seen': _hex(self.min_sp_seen),
                'queue_base': _hex(self.queue_base)}

    def _diff_addrs(self, base):
        cur = self.cpu.mem.ram
        changed = {addr for addr, value in cur.items() if base.get(addr) != value}
        changed.update(addr for addr in base if addr not in cur)
        return sorted(changed)
