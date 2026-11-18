"""jlstream 单点 4B 槽 unknown-word 放行的定向测试(路 EVIMPL)。

两层:
- 纯守卫单测(GuardCpu/GuardMem 鸭子类型,不跑仿真):五要素守卫的授予/
  拒绝/实例失效/部分已知字节/整字真值逐面钉死——另一 PC/另一实例/邻槽/
  栈窗外/映射改变/位移≠8 都取不到来源权限。
- 真指令隔离集成(test_boot_bus 先例,真 app 字节+explore 小预算):真实
  任务体前奏(0x6086980)建立实例与 r10==sp+24;装载门在 0x60869ba 真实
  执行;cmd0 分支序 69be→69c2→6b06(越界丢弃);消费负例 cmd∈{4,5,8,11,12}
  最迟 0x60869c6 停;cmd∈{3,13} 越界对照不进消费;已知 obj 走正常指令;
  具体值对照臂(0/0xffffffff/0x80000000/0x12345678 与授权 unknown 五个世界
  同一 cmd0 片段逐 PC 相等)。

不覆盖(如实边界):活区间内异步中断交付的栈运输(需要完整机器世界,由主
场景开启臂的 window_async 观测覆盖);get-event 解包内部(由主场景 first_load
证据闭合)。
"""
import struct
import unittest
from pathlib import Path

from br28_model import BR28Model, JlstreamKickParam2Unknown
from br28_unknown import UnknownWord
from probe_boot import ENTRY, application, explore
from probe_boot_handoff import SENTINEL_A, package_header, profile
from probe_pi32v2 import ROOT, upstream

FIRMWARE = ROOT / 'firmware/QCY_H3S_7018F_1T2_release_note_V1.1.8_011.ufw'
LOAD_PC = JlstreamKickParam2Unknown.LOAD_PC            # 0x60869ba
TASK_ENTRY = JlstreamKickParam2Unknown.TASK_ENTRY      # 0x6086980(反汇编锚)
INSTALL_PC = JlstreamKickParam2Unknown.INSTALL_PC      # 0x6065f9e
SLOT = JlstreamKickParam2Unknown.SLOT_PHYSICAL         # 0x18ca64
GET_EVENT_ENTRY = 0x6064c8c
HELPER_ENTRY = 0x6075ce0
# 停点世界几何(tonmap 41,909,506 实测+TCB dump):任务结构 0x41a390
# (+64=0x300 栈字数/+68=0x402800 栈基址/+72=名字指针);循环 sp=0x4033c4
# (VA,→物理 0x18ca44);r10=sp+24。隔离测试用恒等映射,VA=PA 几何
# [0x18c800,0x18d400) 模拟同一分配窗。
TCB = 0x18c800
STACK_BASE = 0x18ca00
ENTRY_SP = 0x18ca7c
LOOP_SP = 0x18ca44
BUF = 0x18ca5c                                          # r10(=sp+24)
ID_WORD = struct.pack('<I', 0x00100000)                 # post API id 族


def tcb_words():
    """任务结构三字(栈字数/栈基址/名字指针)——与运行面 dump 相同布局。"""
    return struct.pack('<III', JlstreamKickParam2Unknown.STACK_WORDS, STACK_BASE,
                       JlstreamKickParam2Unknown.TASK_NAME_POINTER)


class GuardMem:
    """authorize() 的 mem 面:identity 翻译 + 可控已知字节 + TCB 结构。"""

    def __init__(self, known=None, tcb=TCB, tcb_bytes=None):
        self.ram = {}
        for base, data in (known or {}).items():
            self.ram.update((base + i, byte) for i, byte in enumerate(data))
        if tcb is not None and tcb_bytes is None:
            self.ram.update((tcb + JlstreamKickParam2Unknown.TCB_STACK_WORDS_OFFSET + i, b)
                            for i, b in enumerate(tcb_words()))
        elif tcb is not None:
            self.ram.update((tcb + JlstreamKickParam2Unknown.TCB_STACK_WORDS_OFFSET + i, b)
                            for i, b in enumerate(tcb_bytes))
        self.unknown_origins = {}

    def region(self, address, size):
        if 0x18c000 <= address and address + size <= 0x18d400:
            return 'fake_jlstream_stack'
        return None

    def abstract_word(self, address, *, task_frame=False):
        values = [self.ram.get(address + i) for i in range(4)]
        if all(v is not None for v in values):
            return int.from_bytes(bytes(values), 'little')
        origins = [self.unknown_origins.get(address + i, address + i)
                   if v is None else None for i, v in enumerate(values)]
        return UnknownWord(values, origins)

    def read(self, address, size):
        values = [self.ram.get(address + i) for i in range(size)]
        if any(v is None for v in values):
            from probe_boot import Blocked
            raise Blocked('uninitialized_memory', address=hex(address))
        return bytes(values)


class GuardCpu:
    def __init__(self, pc=LOAD_PC, sp=LOOP_SP, core=0, r10=None, r0=0, mem=None):
        self.pc = pc
        self.sp = sp
        self.core_id = core
        self.mem = mem if mem is not None else GuardMem()
        self.R = [0] * 16
        self.R[0] = r0
        self.R[10] = BUF if r10 is None else r10


class GuardHardware:
    mmu = None

    def __init__(self, translate_to=None):
        self._translate_to = translate_to

    class _Mmu:
        def __init__(self, target):
            self.target = target

        def translate(self, address, size, read):
            return self.target

    @property
    def mmu(self):
        return None if self._translate_to is None else self._Mmu(self._translate_to)


class GuardUnitTests(unittest.TestCase):
    """五要素守卫纯逻辑面(每次装载逐条重验,非地址豁免)。"""

    def armed_guard(self):
        guard = JlstreamKickParam2Unknown()
        guard.instance_core = 0
        guard.instance_tcb = TCB
        return guard

    def test_exact_geometry_grants_unknown_word_with_physical_origins(self):
        guard = self.armed_guard()
        value = guard.authorize(GuardCpu(mem=GuardMem({BUF: ID_WORD + bytes(4)})),
                                GuardHardware(), 8)
        self.assertIsInstance(value, UnknownWord)
        self.assertEqual(value.bytes, (None,) * 4)
        self.assertEqual(value.origins, (SLOT, SLOT + 1, SLOT + 2, SLOT + 3))
        self.assertEqual((guard.authorized_loads, guard.unknown_word_loads,
                          guard.value_known_loads), (1, 1, 0))
        self.assertEqual(guard.cmd_histogram, {'0x0': 1})
        self.assertIsNotNone(guard.first_load)
        self.assertEqual(guard.first_load['buf_words'][0], '00001000')
        self.assertEqual(guard.first_load['buf_words'][1], '00000000')
        self.assertEqual(guard.first_load['buf_words'][2], '????????')
        self.assertEqual(guard.first_load['stack_window'],
                         f'[{hex(STACK_BASE)},{hex(STACK_BASE + 0xC00)})')

    def test_known_word_answers_true_value_and_partial_write_keeps_unknowns(self):
        guard = self.armed_guard()
        known = GuardMem({BUF: ID_WORD + bytes(4),
                          SLOT: b'\x80\x00\x00\xff'})
        value = guard.authorize(GuardCpu(mem=known), GuardHardware(), 8)
        self.assertEqual(value, 0xff000080)      # 整字已知=真值,不返旧 unknown
        self.assertEqual(guard.value_known_loads, 1)
        partial = GuardMem({BUF: ID_WORD + bytes(4), SLOT: b'\x55'})
        value = guard.authorize(GuardCpu(mem=partial), GuardHardware(), 8)
        self.assertIsInstance(value, UnknownWord)
        self.assertEqual(value.bytes, (0x55, None, None, None))
        self.assertEqual(value.origins[1:], (SLOT + 1, SLOT + 2, SLOT + 3))

    def test_refusals_never_grant_source_permission(self):
        # 无实例观测(任务从未安装)→ 拒绝。
        guard = self.armed_guard()
        guard.instance_tcb = None
        self.assertIsNone(guard.authorize(GuardCpu(), GuardHardware(), 8))
        # 另一核的实例 → 拒绝。
        guard = self.armed_guard()
        guard.instance_core = 1
        self.assertIsNone(guard.authorize(GuardCpu(), GuardHardware(), 8))
        # TCB 栈字数不符(结构改写/重分配为他用)→ 拒绝。
        guard = self.armed_guard()
        cpu = GuardCpu(mem=GuardMem(tcb_bytes=struct.pack(
            '<III', 0x100, STACK_BASE,
            JlstreamKickParam2Unknown.TASK_NAME_POINTER)))
        self.assertIsNone(guard.authorize(cpu, GuardHardware(), 8))
        # TCB 名字指针不符 → 拒绝。
        guard = self.armed_guard()
        cpu = GuardCpu(mem=GuardMem(tcb_bytes=struct.pack(
            '<III', JlstreamKickParam2Unknown.STACK_WORDS, STACK_BASE, 0)))
        self.assertIsNone(guard.authorize(cpu, GuardHardware(), 8))
        # TCB 不可读(结构区已变)→ 拒绝。
        guard = self.armed_guard()
        guard.instance_tcb = 0x18ff00             # 窗外不可读地址
        self.assertIsNone(guard.authorize(GuardCpu(), GuardHardware(), 8))
        # r10 ≠ sp+24(位移后仍可指向同一物理槽)→ 拒绝。
        guard = self.armed_guard()
        cpu = GuardCpu(sp=LOOP_SP - 4)          # r10 不变,sp+24 不再等于 r10
        self.assertIsNone(guard.authorize(cpu, GuardHardware(), 8))
        # sp 在 TCB 栈窗之外 → 拒绝。
        guard = self.armed_guard()
        cpu = GuardCpu(sp=STACK_BASE - 4, r10=BUF - 4)
        cpu.mem.ram.update((TCB + 64 + i, b) for i, b in enumerate(
            struct.pack('<III', 0x300, STACK_BASE - 0x1000,
                        JlstreamKickParam2Unknown.TASK_NAME_POINTER)))
        self.assertIsNone(guard.authorize(cpu, GuardHardware(), 8))
        # 位移 ≠ +8(邻槽/跨界非 4B)→ 拒绝。
        guard = self.armed_guard()
        self.assertIsNone(guard.authorize(GuardCpu(), GuardHardware(), 4))
        self.assertIsNone(guard.authorize(GuardCpu(), GuardHardware(), 0x10))
        # 邻槽(r10 整体偏移,sp+24 关系保持)→ 物理窗不匹配 → 拒绝。
        guard = self.armed_guard()
        cpu = GuardCpu(sp=LOOP_SP - 4, r10=BUF - 4)   # r10==sp+24 但槽=0x18ca60
        self.assertIsNone(guard.authorize(cpu, GuardHardware(), 8))
        # 映射改变:MMU 翻译不再落在钉死物理窗。
        guard = self.armed_guard()
        self.assertIsNone(guard.authorize(
            GuardCpu(), GuardHardware(translate_to=SLOT + 0x100), 8))
        # region 缺失(翻译落点不在任何可写 RAM region)。
        class NoRegionMem(GuardMem):
            def region(self, address, size):
                return None
        guard = self.armed_guard()
        self.assertIsNone(guard.authorize(GuardCpu(mem=NoRegionMem()), GuardHardware(), 8))

    def test_instance_reobservation_replaces_old_authorization_geometry(self):
        guard = self.armed_guard()
        # 旧实例装载授权一次后,任务重装(新 TCB 落在别处、旧 TCB 改写):
        # 旧几何拒绝。
        self.assertIsInstance(guard.authorize(GuardCpu(mem=GuardMem({BUF: ID_WORD + bytes(4)})),
                                              GuardHardware(), 8), UnknownWord)
        guard.instance_tcb = TCB + 0x400
        self.assertIsNone(guard.authorize(GuardCpu(), GuardHardware(), 8))

    def test_note_step_records_install_instance_and_producer_candidates(self):
        guard = JlstreamKickParam2Unknown()
        installer = GuardCpu(pc=INSTALL_PC, sp=ENTRY_SP, core=0)
        installer.R[0] = TASK_ENTRY
        installer.R[4] = TCB
        guard.note_step(installer)
        self.assertEqual((guard.task_installs, guard.instance_core,
                          guard.instance_tcb), (1, 0, TCB))
        # 其他任务的安装点执行(r0 不同)不触碰 jlstream 实例。
        other = GuardCpu(pc=INSTALL_PC, sp=ENTRY_SP, core=0)
        other.R[0] = 0x602d3a0
        other.R[4] = TCB + 0x800
        guard.note_step(other)
        self.assertEqual((guard.task_installs, guard.instance_tcb), (1, TCB))
        guard.note_step(GuardCpu(pc=0x6075078, sp=ENTRY_SP, core=0))
        guard.note_step(GuardCpu(pc=0x6075078, sp=ENTRY_SP, core=0))
        self.assertEqual(guard.producer_executions['open_kick_api_0x6074ffe'], 2)


class IsolatedChainTests(unittest.TestCase):
    """真 app 字节的隔离装载链(小预算 explore,stop_before_pc=helper 入口)。"""

    @classmethod
    def setUpClass(cls):
        cls.module = upstream()
        cls.app = application(FIRMWARE)
        cls.header = package_header(FIRMWARE)

    def world(self, *, cmd=0, slot_bytes=None, entry_pc=0x60869b6,
              initial_sp=LOOP_SP, stop_before=HELPER_ENTRY, budget=64,
              seed_instance=True):
        handoff = profile(self.header, SENTINEL_A)
        handoff['regions'] = list(handoff['regions']) + [
            (0x18c000, 0x18d400, 'fake_jlstream_stack')]
        seeds = [(BUF, ID_WORD), (BUF + 4, struct.pack('<I', cmd & 0xffffffff)),
                 (TCB + JlstreamKickParam2Unknown.TCB_STACK_WORDS_OFFSET, tcb_words())]
        if slot_bytes is not None:
            seeds.append((SLOT, slot_bytes))
        handoff['seeds'] = list(handoff['seeds']) + seeds
        handoff['description'] = dict(handoff['description'],
                                      phase='isolated jlstream slot-load test; '
                                            'not contiguous startup')
        hardware = BR28Model(jlstream_kick_param2_unknown=True)
        guard = hardware.jlstream_kick_param2
        if seed_instance:
            guard.instance_core = 0
            guard.instance_tcb = TCB    # the install-observation machinery is fed
            # the TCB the real install site passes (r4 @ 0x6065f9e, r0=0x6086980).
        regs = [0] * 16
        regs[10] = BUF if entry_pc != TASK_ENTRY else 0
        regs[12] = 0x1264B8
        diagnostics = {'entry_pc': entry_pc, 'initial_sp': initial_sp,
                       'initial_gprs': regs, 'stop_before_pc': stop_before}
        return explore(self.module, self.app, name='jlstream_slot_load',
                       handoff=handoff, hardware=hardware,
                       diagnostics=diagnostics, budget=budget), guard

    def test_real_task_prologue_builds_r10_eq_sp_plus_24(self):
        # 反汇编锚验证:真前奏指令(0x6086980..0x60869a8)建立 r10==sp+24 与
        # 停点世界一致的寄存器几何。注意:真机世界 OS 从不执行该前奏(初帧
        # GPR 由安装代码预置、首派发直入 get-event,类注释勘误)——本测试
        # 验证的是锚几何本身,不是实例绑定路径。
        result, guard = self.world(entry_pc=TASK_ENTRY, initial_sp=ENTRY_SP,
                                   stop_before=GET_EVENT_ENTRY, budget=32)
        self.assertEqual(result['stop'],
                         {'kind': 'diagnostic_milestone_reached',
                          'address': hex(GET_EVENT_ENTRY)})
        self.assertEqual(guard.task_installs, 0)
        # 真指令建立几何:add sp,-56 → 0x18ca44;r10 = sp+24 → 0x18ca5c;
        # 哨兵/名字寄存器同步装载(与停点世界实测寄存器一致)。
        registers = result['last_registers']
        self.assertEqual(registers['sp'], hex(LOOP_SP))
        self.assertEqual(registers['r'][10], hex(BUF))
        self.assertEqual(registers['r'][12], hex(0x1264B8))
        self.assertEqual(registers['r'][9], hex(0x609d3bb))
        self.assertEqual(registers['r'][11], hex(LOOP_SP | 2))

    def test_authorized_unknown_load_runs_the_real_cmd0_drop_path(self):
        result, guard = self.world(cmd=0)
        self.assertEqual(result['stop'],
                         {'kind': 'diagnostic_milestone_reached',
                          'address': hex(HELPER_ENTRY)})
        self.assertEqual(result['instruction_adapters'].get('jlstream_kick_param2_load'), 1)
        self.assertEqual((guard.authorized_loads, guard.unknown_word_loads), (1, 1))
        pcs = [row['pc'] for row in result['last_instructions']]
        self.assertEqual(pcs, ['0x60869b6', '0x60869ba', '0x60869be',
                               '0x60869c2', '0x6086b06'])
        # cmd0:越界丢弃分支真实执行(69c2→6b06),消费指令 69c6/69c8/69cc
        # 一次都不执行;活区间窗口在 helper 入口尚未闭合(6b0c 在其后)。
        self.assertNotIn('0x60869c6', pcs)
        self.assertTrue(guard.window_open)

    def test_consumption_negatives_stop_at_latest_0x60869c6(self):
        for cmd in (4, 5, 8, 11, 12):
            result, guard = self.world(cmd=cmd)
            self.assertEqual(result['stop']['kind'], 'unknown_task_register_consumed',
                             f'cmd={cmd} must stop on the first r6 consumer')
            self.assertEqual(result['stop_pc'], '0x60869c6', f'cmd={cmd}')
            self.assertEqual(result['instruction_adapters'].get('jlstream_kick_param2_load'), 1)
            self.assertEqual(guard.unknown_word_loads, 1)
            self.assertIn('0x18ca64', result['stop'].get('sources', []))

    def test_out_of_range_controls_skip_the_consumer_pair(self):
        # cmd=3/13:无符号越界(3-4 与 13-4 都 >8)走同一丢弃分支——门限按
        # [4,12] 无符号判定,负例不得进 69c6。
        for cmd in (3, 13):
            result, guard = self.world(cmd=cmd)
            self.assertEqual(result['stop'],
                             {'kind': 'diagnostic_milestone_reached',
                              'address': hex(HELPER_ENTRY)})
            pcs = [row['pc'] for row in result['last_instructions']]
            self.assertNotIn('0x60869c6', pcs)
            self.assertEqual(guard.unknown_word_loads, 1)

    def test_known_object_control_runs_past_the_consumer_pair(self):
        # buf[2]=已知合法值:装载返真值,69c6/69c8 真实算术执行,tbb 分派进
        # cmd5 分支体(0x60869f0 起)——证明该 PC 不是无条件 NOP;分支体首
        # call 进入 ROM helper(隔离世界无 ROM 字节)按普通严格 fetch 停。
        result, guard = self.world(cmd=5, slot_bytes=struct.pack('<I', 0x18cb00))
        self.assertEqual(guard.value_known_loads, 1)
        pcs = [row['pc'] for row in result['last_instructions']]
        self.assertIn('0x60869c6', pcs)
        self.assertIn('0x60869c8', pcs)
        self.assertIn('0x60869cc', pcs)
        self.assertIn('0x60869f0', pcs)
        self.assertNotEqual(result['stop']['kind'], 'unknown_task_register_consumed')

    def test_concrete_value_controls_share_one_control_flow(self):
        # 具体值对照臂:缺失 word 设 0/0xffffffff/0x80000000/0x12345678 与
        # 授权 unknown 五个世界跑同一 cmd0 片段,PC 序全等(静态零消费结论的
        # 运行时镜像);这不是具名 fixture——值只进本隔离对照,不进条件栈。
        traces = {}
        for label, slot in (('unknown', None),
                            ('zero', bytes(4)),
                            ('ones', b'\xff' * 4),
                            ('sign', struct.pack('<I', 0x80000000)),
                            ('pattern', struct.pack('<I', 0x12345678))):
            result, guard = self.world(cmd=0, slot_bytes=slot)
            self.assertEqual(result['stop'],
                             {'kind': 'diagnostic_milestone_reached',
                              'address': hex(HELPER_ENTRY)})
            traces[label] = [row['pc'] for row in result['last_instructions']]
            self.assertEqual(guard.authorized_loads, 1)
        self.assertEqual(len(set(map(tuple, traces.values()))), 1, traces)
        self.assertEqual(traces['unknown'], ['0x60869b6', '0x60869ba',
                                             '0x60869be', '0x60869c2',
                                             '0x6086b06'])

    def test_guard_refusal_in_isolated_world_keeps_the_strict_stop(self):
        # 实例未观测(守卫拒绝)→ 落回普通严格路径:停点回到未初始化读。
        result, guard = self.world(seed_instance=False)
        self.assertEqual(result['stop']['kind'], 'uninitialized_memory')
        self.assertEqual(result['stop'].get('address'), hex(SLOT))
        self.assertEqual(result['stop'].get('first_missing'), hex(SLOT))
        self.assertEqual(result['stop_pc'], hex(LOAD_PC))
        self.assertNotIn('jlstream_kick_param2_load', result['instruction_adapters'])
        self.assertEqual(guard.authorized_loads, 0)


if __name__ == '__main__':
    unittest.main()
