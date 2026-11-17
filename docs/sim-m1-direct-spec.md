# M1 直调半边实施规格（SIMB1T）：停点世界直调驱动器 + server 对接 + 三级验收机检

2026-11-17 00:31-01:0x。基线 worktree `fcd381e`（分支 sim-simb1t）。本文是 [sim-bridge-design.md](sim-bridge-design.md)
（SIMBRIDGE 总设计）M1 的**直调半边实施级展开**，照 [d1-window-producer-spec.md](d1-window-producer-spec.md)
（PR2SPEC）的模式写：每条给**接口 + 代码块形状 + 验收断言 + 风险**，目标=实施路「拿到即可写代码」。

三份已齐预研的合成关系：

| 来源 | 本文消费的部分 |
| --- | --- |
| [sim-bridge-design.md](sim-bridge-design.md) | §4.1 server 形态/direct_call 三条纪律/失败恢复/scratch 守卫；§4.2 socket 契约；§5.1 三级 verdict 框架；R1-R16 风险表 |
| [sim-m1-pre-study.md](sim-m1-pre-study.md)（SIMPRE） | T1 setter 直调唯一可行/0x600f85e 读路径可跑写路径必 Blocked/13 setter 恒返 0/0x22 apply 内联=M1-b 全效果/M1-c 收窄 ANC 族/0x300000 消费在 get_event 0x6064c0e/watch PC 集 |
| [rcsp-return-path.md](rcsp-return-path.md)（RCBACK） | R1 拦 0x600b66e（载荷级回执）/R2 拦 0x6009f6a（通知）/状态旁路镜像表（EQ 镜像+段数/ANC 三字节/ANC ctx） |
| SIMB1A 落地（tools/sim_bridge_server.py + SimulationGattLink.java） | socket 契约 proto=1、RcspDeframer、StaticFixtureState 对接点（本文 §3.4 换血）、错误可见性/单活动客户端纪律 |
| tools/probe_irq_entry.py:846-876（S 路先例，只读） | key_chain_direct_call 的世界准备/哨兵/受控步进形态——本文 §2.1 照此抽象 |

**JLEQ 接口假设声明（本规格的上边界）**：probe 五文件（analyze_image_trace/br28_model/probe_boot/
probe_irq_entry/frontier_registry）正被 JLEQ 路在改。本文凡涉及 probe 的部分一律写到**接口假设**层面：
直调驱动器引用 S 先例**现有形态**（probe_irq_entry.py:846-876，本基线只读核对过），世界停点=**实施时点
最新已过线停点**（当前前沿 29,215,313 @ 0x6072b92 写 0xfe1500，JLEQ 合并后会前移——本文不假设其最终
位置与最终开关集），解冻续跑引用 explore 主循环**现有区段形状**（probe_boot.py:2016-2047）。所有这类
引用都标 **[依赖 JLEQ 最终形态]**，JLEQ 合并后按 §5.3 清单校准。

证据强度沿用总设计：**[静态]**=反汇编钉死（引文档）；**[文档]**=既有 docs/HANDOFF 条目；**[设计]**=本文
新设计决策；**[依赖 JLEQ]**=接口假设；**[待实测]**=实施路首跑核对项，不臆造。

## 0. 一句话结论

把 SIMB1A 的 `StaticFixtureState` 换成 **SimWorldDriver**（新文件 `tools/sim_world_driver.py`）：在
`explore(..., post_stop=serve_loop)` 的停点世界里跑 socket 命令循环，**读命令直调分发器 0x600f85e 读
路径**（FE 01 cmdid→getter→包装，全程无 pend，冻结世界可跑 [静态：SIMPRE §2.3]），在 **0x600b66e 入口
watch 拦截**回执载荷（resp 缓冲此刻已组好 `cmdid|n|payload` [静态：RCBACK §1.1+SIMPRE §1.1]），server
自己包 RCSP 响应帧回流 app；**写命令走 T1 setter 直调**（分发器写路径 post+pend 在冻结世界必 Blocked
[静态：SIMPRE §2.3]，setter 全族无 pend），写静默不合成回执（13 setter 恒返 0 [静态：SIMPRE §1.2]），
效果断言走**状态旁路**（EQ 镜像 b[0x1265C0+5356]/段数@+5499；ANC b[0x12666B..D]）。M1 分级验收按
SIMPRE 修正后的口径：**M1-b 的 EQ 0x22 直调返回即全效果**（apply 0x6025118 在 setter 内联），**M1-c
收窄为 ANC 0x17 族**（解冻续跑让 app_core 泵在 get_event 0x6064c0e 内联消费 post 的回调）。server 侧
改动全部落在 tools/ 新文件与 sim_bridge_server.py（非 probe 五文件）；probe 五文件唯一的候选改动=
给 `irq_case` 加 post_stop 透传参（§5.2，等 JLEQ 合并）。

## 1. 数据流总览与读/写判别

```
app（AVD，SimulationGattLink，零改动）
  │ ble_write（GATT 片，b64）                    ← socket 契约 proto=1 不变（SIMB1A 已落地）
  ▼
sim_bridge_server.py 会话层（SIMB1A 骨架保留：RcspDeframer/资源上限/chunk_ack 即收即 ACK）
  │ 重组出完整 RCSP 帧 → 剥 SN → 命令体
  ▼
读/写判别（§3.4，照分发器 0x600f87a 的判别形状 [静态：SIMPRE §1.1]）：
  body[0..2] == FE 01 cmdid ──────────▶ 读命令 → §2.2 读驱动（直调 0x600f85e 读路径）
  body[0]   == cmdid（写形态 cmdid|len|data）─▶ 写命令 → §2.3 写驱动（T1 setter 直调，表 A 查表）
  opcode != 0xFF ────────────────────▶ 标准 opcode：M1 记日志不回（SIMB1A 现状维持；桩应答=设计 §3 可选项）
  ▼
SimWorldDriver（§3，新文件）：direct_call 在停点世界 CPU0 上受控步进（外设时间冻结）
  │ 读：watch 0x600b66e 入口拦载荷 → server 包帧 → ble_notify 回推
  │ 写：setter 直存+（ANC）post 落队 → 不回帧（写静默）→ 断言走 inspect/world_diff
  │ M1-c：resume 解冻步进（含 hardware.advance）→ watch 0x6064c0e 消费铁证 → apply 链 → world_diff
  ▼
失败恢复（§3.3）：Blocked/预算耗尽 → world_dead 错误帧（可见断线）→ 外层循环快照 restore 重建（~8s）→ 复役
```

帧形（全部既有钉死，本文不发明）：app 命令 `FE DC BA C0 FF 00 04 | SN FE 01 cmdid | EF` /
`FE DC BA C0 FF 00 len | SN cmdid dlen data | EF`；设备响应 `FE DC BA 00 FF 00 len | 00 SN cmdid n
payload | EF`（protocol.md:39-40 实测对；SIMB1A `build_response` 已实现同形）。

## 2. 直调驱动器规格（tools/sim_world_driver.py，新文件）

### 2.1 direct_call 原语：世界准备与三条纪律

接口（照 S 先例 `key_chain_direct_call`（probe_irq_entry.py:846-876）抽象，语义逐行对齐；**不 import
该函数**——禁区文件里的场景函数不引用，独立实现同语义 [设计]，先例本身 [依赖 JLEQ 最终形态] 不假设其
在合并后仍原样存在）：

```python
DIRECT_SENTINEL = 0x00100000   # ROM(0..0x20000)与 flash(0x06000100..)之间未映射空洞，从不取指
                            # [文档：probe_irq_entry.py:842-844 注释钉死]

def direct_call(self, pc, args, *, max_steps=1_000_000, watch=(), need_return=True):
    """在停点世界 CPU0 上直调真实固件函数，返回 (r0, steps, watch_hits)。
    世界准备（S 先例形状）：全量保存 pc/sp/rets/flags/R0-R15 → 设 pc 与 r0..r(len(args)-1)
    → rets=哨兵 → 受控步进循环：decode → step → machine.after_step（簿记），
    不调 hardware.advance（外设时间冻结=中断交付冻结；直调段视为无中断窗口，
    Timer0/core-tick 的 pending 只是挂着不推进 [文档：S 先例 docstring]）。"""
```

在 S 先例之上按总设计 §4.1 加三条纪律（codex 复核采纳项，S 先例没有的补齐）：

1. **成功返回先校验再恢复**：到哨兵时校验 `cpu.sp == 入口 sp`（栈平衡）且步进中 sp 未越过下界
   （驱动器记录全程 min_sp，与 §2.5 的栈下界守卫比对），通过后才恢复上下文；不平衡=世界半写，
   走失败恢复（§3.3）。
2. **调用前置门**：`cpu.core_id == 0`（限定 CPU0）；`cpu.sp_known`（已知 SP）；`cpu.active_context`
   为 None（非 IRQ handler 上下文）；`cpu.after_then is None and cpu.repeat is None`（无在途并行对/
   repeat 体）。任一不满足 → 拒绝直调，回 `error(world_not_ready)`，不碰世界。
   （属性面参照 explore 主循环对它们的既有消费 [依赖 JLEQ 最终形态：以届时 CPU 属性名为准]。）
3. **失败语义**：Blocked/预算耗尽 → **世界标记不可继续**（world_dead），`finally` 只恢复寄存器——
   存储副作用留在世界里，靠 §3.3 快照重建收场，不假装「恢复寄存器=恢复世界」。

`need_return=False` 用于只关心副作用、不关心返回值的调用（如 M1-c 后的验证性直调）；True 时把哨兵处
r0 作为 int 返回（非 int 视为 None，如实记录）。

### 2.2 读命令驱动：直调 0x600f85e 读路径（冻结世界可跑）

```python
def rcsp_read(self, sn: int, cmdid: int) -> bytes | None:
    """读命令全固件路径：表 walk + getter + 响应包装（cmdid|n 头由分发器补写）。
    返回回执数据段 cmdid|n|payload；n<1 → None（静默不回，固件同款 0x600f942）。"""
    body = bytes([0xFE, 0x01, cmdid])                       # 读形态 body [静态：RCBACK §1.6]
    self.write_scratch(SCRATCH_BODY, body)
    r0, steps, hits = self.direct_call(
        0x0600f85e, [sn, SCRATCH_BODY, 0, len(body)],       # 入口 ABI r0=SN,r1=body,r2=忽略,r3=len
        watch=(0x600B66E, 0x6009F6A), max_steps=200_000)   # 读链几十条指令量级 [静态：SIMPRE §2.3]
    hit = 首个 pc==0x600B66E 的 watch 命中                  # R1 拦截点：入口在池门之前 [静态：RCBACK §1.1]
    if hit is None: return None                             # n<1 静默（getter 返 -1/<1 同此）
    r0_hit, status, sn_echo, resp = hit['args']             # r0=0xFF,r1=status,r2=SN,r3=resp 基址
    n = self.mem_read(resp + 1, 1)[0]                       # 分发器已补写 resp[0]=cmdid,resp[1]=n
    校验 r0_hit==0xFF、sn_echo==sn、resp[0]==cmdid、1<=n<=126，违者回 error(read_shape)
    return self.mem_read(resp, n + 2)                       # cmdid|n|payload
```

- **为什么拦 0x600b66e 入口而不是等返回后读栈**：分发器把 resp 放在自己的栈帧（sp+44），函数返回后
  栈帧即死区，可能被后续调用覆写；watch 命中时刻缓冲恰已组好（0x600f946-0x600f94e 补头在前、call
  0x600b66e 在后 [静态：SIMPRE §1.1]），一次 `mem_read` 拿全载荷，免解析 wire 帧。
- **池门无阻**：BT 池门（[0x1265C0+1004] 非零+b[pool+24]）在 0x600b66e **体内**，失败仅返 4 丢帧，
  拦截在入口先于门 [静态：SIMPRE §2.3]——池不在位也不影响读命令回执。池在位时 0x6009db0 会真实组
  wire 帧挂 TX 队列（信号量 post 非阻塞，无 pend）——副作用见 §6 风险 D4。
- **读路径不经过 0x12b1a2/查名**：那是写路径（0x600f8b4 起）的前置 [静态：SIMPRE §1.1]，读路径
  （0x600f87a-0x600f8b2→0x600f940）全程纯 RAM。
- **降级备选** [设计]：若 0x600f85e 读直调在实施时撞预期外的墙（静态结论未覆盖的动态形状），回退
  getter 直调（表 A +4 槽，`direct_call(getter, [buf_scratch, 126])`，返回 n、载荷在 buf）——表 walk
  由 server 复刻（R15 残余，如实标注 cmdid 路由无固件执行证据）。0x17 getter 0x6025c84 无前置门
  恒返 3、0x22 getter 0x602580a 返 3+7N（含 3B 头，N=b[0x1265C0+5499]，N*7+3>126 返 -1）[静态：
  SIMPRE §1.1/§1.2]。

### 2.3 写命令驱动：T1 setter 直调（写路径唯一可行形态）

```python
def rcsp_write(self, cmdid: int, data: bytes) -> dict:
    """T1 写：server 查表 A（从世界 flash 实读，非硬编码）→ 直调 setter。
    返回 {'setter','n','steps','watch_hits','stored','posted'} 供验收断言与日志。"""
    entry = self.table_a_entry(cmdid)        # 表 A [0x060a9b78,0x060a9d1c) 15×28B
                                            # {+0 init,+4 getter,+8 setter,+20 恢复默认,+24 cmdid}
                                            # [静态：RCBACK §1.1/设计 §3]——cpu.mem.read 实读解析
    setter = entry.setter
    if setter == 0: return {'setter': 0, 'note': '官方槽空，静默跳尾（固件同款 0x600f8b4）'}
    self.write_scratch(SCRATCH_DATA, data)   # ≤73B（0x22 上界 3+7×10 [文档：eq-cmd §7]）
    r0, steps, hits = self.direct_call(
        setter, [SCRATCH_RESP, 128, SCRATCH_DATA, len(data)],   # setter ABI r0=resp,r1=128,r2=data,r3=len
        watch=WATCH_WRITE)                   # §2.4 的 watch 集
    return {'setter': setter, 'n': r0, 'steps': steps, 'watch_hits': hits, ...}
```

- **写路径为什么绕开 0x600f85e**：分发器写形态在 app_core 存在时走异步路径——post 9 字参数块后
  **pend(sp+176,0) 计数信号量**等 app_core 消费，冻结世界 app_core 永不调度 → 预算耗尽 Blocked 且
  事件已留队（世界半写）[静态：SIMPRE §2.3]。T1 直调 setter 等价覆盖「前置门链+参数直存+异步投递」
  的同步段：0x22 全同步（含内联 apply）；0x17 四门纯读+直存+fire-and-forget post（0x6064978 失败
  不重试不检查，静默丢不 Blocked）；0x1d VM 写+镜像 [静态：SIMPRE §1.2/§2.4]。
- **预期返回值**：13 个 setter 恒返 0（含 0x22 长度校验失败也返 0）[静态：SIMPRE §1.2]——`n==0`
  是**常态**，不是失败；若某 setter 意外返 n≥1（现无反例），按 SIMPRE §1.3 死代码 ABI 组回执
  （载荷应在 resp+2），并在日志标注「setter 返非 0：静态结论外的动态形状，回报主协调」。
- **setter ABI 依据**：分发器同步路径 0x600f932-0x600f93e `setter(r0=resp=sp+44, r1=128, r2=body+2,
  r3=body[1])` [静态：SIMPRE §1.1]——body+2 即剥掉 cmdid|len 两字节头后的 data 段，server 侧对应
  r2=SCRATCH_DATA。
- **0x17 的门是值门不是墙**：四门（0x6008170→0x6003578 连接查询 / 0x6000c6c=[0x1265C0+2444] 判
  id==5 / 0x60065bc=return b[0x1265C0+5] / 0x6018994 零调用）全部纯 RAM 读、无 MMIO [静态：SIMPRE
  §2.4]，但**读值是否放行取决于停点世界状态** [待实测：门值核对，§5.3 第一刀 3]——门不过则
  setter 跳过直存（仍恒返 0）。诊断面：直调前后 inspect 三个门值 RAM 面；若门因桥接世界无 BT 连接
  而不过，处置=按设计 §5.0 P2 的具名 fixture 先例给门值 RAM 显式合成（标注 synthetic，不得称原链
  已验证）。

### 2.4 返回值与副作用采集（watch 拦截 + 镜像读回 + world_diff）

**watch 集**（watch 命中记 pc+r0-r3 [文档：S 先例 :862-863]；全部地址出处见 §7）：

| 用途 | watch PC | 捕获内容 |
| --- | --- | --- |
| 读回执拦截（R1） | 0x600B66E | r3=resp 基址 → mem_read 出 cmdid\|n\|payload（§2.2） |
| 通知拦截（R2，M1-c） | 0x6009F6A / 0x6025652 | 通知载荷（无恳求帧；0x6025652=上层包装 (cmdid,payload,len) [静态：RCBACK §1.1]） |
| EQ apply 内联（M1-b） | 0x6025118 / 0x602503a | 0x22 setter 直调段内触达即「apply 真实执行」的证据 |
| ANC post 投递（M1-b） | 0x6064978 | r0-r3 携任务/参数 → 推导 app_core 队列指针 [待实测：0x606487c 入口 ABI 以实跑为准] |
| 回调消费铁证（M1-c） | 0x6064C0E | 解冻后首个铁证：任何 0x300000 回调事件在 get_event 内联消费 [静态：SIMPRE §3.1] |
| ANC apply 链（M1-c） | 0x6025D6A / 0x6012BE2 / 0x600DCEE | 回调→ ANC 配置落点（ctx [0x1265C0+500]+33 / VM key16 [文档：AO+SIMPRE §3.3]） |
| 分发器 RPC trampoline | 0x60256AA | （仅 0x600f85e 写路径出现；M1 不走，留诊断） [静态：SIMPRE §1.1] |

**镜像读回（world_inspect 白名单）**——写效果断言的主面（比回执链短两跳 [静态：RCBACK §3]）：

| 状态 | 地址 | 写者/时机 |
| --- | --- | --- |
| EQ 曲线镜像 | b[0x1265C0+5356 .. +3+7N) | 0x22 setter memcpy（返回前已落 RAM） |
| EQ 段数 N | b[0x1265C0+5499] | 0x22 setter |
| ANC wire 三字节 | b[0x12666B..D]（=0x1265C0+171） | 0x17 setter 直存（回执读点同址 [静态：RCBACK §3]） |
| ANC ctx 模式 | b[[0x1265C0+500]+33]（两级：先读指针） | apply 链 0x6012be2/0x600dcee（M1-c 后才变） |
| app_core 队列 | [queue+14] 计数、[queue+16] 缓冲 | poster 0x606487c（post 落队断言面 [静态：SIMPRE §3.1]） |
| 池门诊断 | [0x1265C0+1004]、b[pool+24] | 0x600b66e 体内门（读命令诊断用） |

**world_diff**：基线=停点后首条命令前的 `dict(cpu.mem.ram)` 快照（explore 自己按 `c.mem.ram.get`
消费 RAM [文档：probe_boot.py:2118 同面]）；每次命令后差分=「相对基线的写集」，输出按区段归因
（配置镜像/scratch/直调栈窗/池队列/heap），供 §4 各级断言与 §6 D6 归因。规模：RAM 字典全量 diff
一次 ~百 ms 级（Python），M1 命令量（十条级）无压力；若实测慢，退化为白名单区段 diff（口径如实标注）。

### 2.5 scratch 与栈（P3 守卫落地）

- **需求**：读 body 3B；写 data ≤73B + resp 预留 128B（setter 不写它 [静态：SIMPRE §1.2]，预留是
  ABI 义务不是使用预期）。合计 ≤204B，两个固定窗（BODY/DATA 与 RESP 分开，便于 diff 归因）。
- **选址约束**：①已映射可写 RAM（停点世界 bss 域内候选——0x125320..0x12A62C 是已实证映射的 bss
  清零区 [文档：HANDOFF 路 W 段]，从中选无消费者引用的死窗 [待实测]）；②**不得在直调 callee 的栈
  下行路径上**（callee 从入口 sp 向下生长——分发器入口 `sp += -192` [静态：SIMPRE §1.1]，scratch
  放入口 sp 之下会被 callee 栈帧覆写）；③不碰 [0x1265C0+1004] 池域与 §2.4 各镜像面；④不碰 rcsp
  缓冲池域（任务未创建时状态未初始化 [设计 §4.1]）。
- **守卫（首跑必做，P3）**：选址后先跑一次**空直调**（直调一个立即返回的叶子函数，如 gate3
  0x60065bc——单条 `return b[0x1265C0+5]` [静态：SIMPRE §2.4]）→ `world_diff` 必须只含 scratch
  自身与直调栈窗；再跑**深栈探针**（直调一个已知深栈函数，apply 0x6025118 即是天然探针）→ 记录
  min_sp，校验 scratch 窗不与 [min_sp, 入口 sp) 相交。任一不过 → 换址重跑守卫。「空直调 diff=∅」
  只证无静态引用，不证无动态引用——保留换址余地 [设计 §4.1 原文纪律]。

### 2.6 resume_steps：M1-c 解冻续跑 [依赖 JLEQ 最终形态]

```python
def resume_steps(self, n: int, watch=()) -> dict:
    """解冻：退出直调的冻结纪律，按 explore 主循环同款步进跑 n 步——含 hardware.advance
    （外设时间恢复，Timer0/core-tick 到期自然到来），让 app_core 泵消费注入期 post 的事件。"""
```

- **循环形状**＝explore 主循环的最小子集 [依赖 JLEQ 最终形态：以届时 probe_boot 主循环区段为准校准，
  本基线参照 2016-2047 行]：`decode → step → machine.after_step → hardware.advance`；异常面三支——
  CoreStall（note_stall + after_step + advance）、Blocked（停，如实报告停点）、IrqDeliveryRequest
  （**交付分发**：按 dispatch_delivery 的形状复刻——mid_instruction/in_flight_handler 延期、
  deliver_core_tick/deliver_timer0_irq/deliver_software_irq 按 irq 号分派 [依赖 JLEQ 最终形态：
  本基线参照 probe_boot.py:1910-1969]）。交付分发必须在场：app_core 的唤醒就靠 IRQ 交付，停在
  交付边界不解冻等于没解冻。
- **为什么不能复用 explore 继续**：post_stop 在主循环退出后才被调（probe_boot.py:2076-2078），
  explore 没有「继续跑」的公共入口；server 侧复刻 mini 主循环是既定方案 [设计 §4.1 resume_steps]。
  复刻段是新文件内 ~40 行，JLEQ 若改主循环形状（如交付签名），此处同步（§5.3 校准项）。
- **解冻后世界不再回到原停点**：resume 推进了世界，后续直调发生在新上下文（direct_call 每次重跑
  §2.1 前置门）；快照重建（§3.3）会把世界拉回基线停点，resume 的推进随之丢失——可接受（重建=
  回基线，命令可重放），如实记录。

## 3. server 对接设计

### 3.1 SimWorldDriver 内部结构

```python
class SimWorldDriver:                    # tools/sim_world_driver.py（新文件）
    def __init__(self, machine, hardware, cpu, *, scratch_cfg, table_a_range):
        self.machine, self.hardware, self.cpu = ...   # explore post_stop 三元组（活机器）
        self.dead = False                              # world_dead 标记（§3.3）
        self.baseline_ram = None                       # world_diff 基线（首条命令前快照）
        self.watch_log = []                            # 跨命令 watch 命中流水（验收记录）
        # 公共 API 面（全部走 cpu.mem / cpu / machine 公共属性）：
        #   direct_call / rcsp_read / rcsp_write / resume_steps      （§2）
        #   world_inspect(addr,len) / world_diff() / table_a_entry() （§2.4）
        #   describe() -> {'mode':'direct-world','stop_pc','steps','scratch':{...},'dead':False}
```

对接点=SIMB1A 的 `StaticFixtureState` 槽位（tools/sim_bridge_server.py:106-144 现状）：会话层只依赖
`read(cmdid, sn)` / `apply_write(cmdid, data)` / `describe()` 三个方法，**socket 契约 proto=1 与
SimulationGattLink/app 接线零改动**（SIMB1A 交付时明示的对接承诺）。`StaticFixtureState` 保留为
`--static` 模式（AVD 通道测试/无世界冒烟用），`read` 接口加 sn 形参（静态实现忽略之）。

### 3.2 生命周期：起 explore → 停点 → 命令循环 → 直调 → 读状态 →（M1-c）解冻

```python
# tools/sim_world_boot.py（新文件）——世界构造链（[依赖 JLEQ 最终形态]：开关集从
# frontier_registry.scenario_flags('p11_heap_experimental') 单源 import（只读），不手抄）
def build_world(serve_loop, snapshot_dir, capture=False):
    module, app, header, rom, image = <照 probe_irq_entry.main 的装载链（upstream/application/
                                      package_header/flash_image）>
    flags = scenario_flags('p11_heap_experimental', <values>)
    return irq_case(module, app, header, rom, 'sim_bridge_world',
                    **flags, flash_image_bytes=image,
                    post_stop=serve_loop,                    # ← §5.2：irq_case 现无此参，等 JLEQ 后加
                    snapshot=({'capture': snapshot_dir} if capture else
                              {'restore': snapshot_dir, 'expected_context': {...}}))

# tools/sim_bridge_server.py 外层循环（改造点：serve 从一次性调用变世界循环）
def serve_worlds(host, port, snapshot_dir):
    listener = make_listener(host, port)                     # 跨世界存活的 acceptor
    while True:
        serve_loop = make_serve_loop(listener)               # post_stop 回调：accept+handle 单线程循环
        result = build_world(serve_loop, snapshot_dir,
                             capture=not snapshot_exists())  # 首轮全量重放+捕获（~27min 量级
                                                            # [文档：M3FIX 实测]），之后 restore ~8s
        if not result.get('world_dead'): break               # 正常退出（quit/SIGINT）
        log('世界死亡，快照重建后复役')                        # §3.3：下一轮循环即重建
```

- **serve_loop 形状**（post_stop 回调，`post_stop(machine, hardware, stopped_cpu) -> dict|None`
  [文档：probe_boot.py:98-105 docstring、2076-2078 调用点、2140-2141 结果合并]）：进入时先做
  **世界就绪自检**——stopped core==0、sp_known、§2.5 scratch 守卫、烟雾读（直调 0x17 getter
  0x6025c84，期望返 3 [静态：设计 §5.1 M1-a（qcy-command17.asm getter：无前置门、读
  b[0x12666B..D] 三字节、恒返 3）]）——全过才 accept；否则返回
  `{'sim_bridge_world_not_ready': {...诊断...}}` 让外层报错退出（不猜）。
- **首条命令前**校验目标 CPU 与停点上下文，世界横幅（hello_ok 的 world 字段）带
  `{'mode':'direct-world','stop_pc','steps','scratch',...}`——app 侧现状不消费 hello_ok（
  SimulationGattLink.java:173 注释），形状变化无兼容负担；宿主测试客户端消费它做验收记录。
- **优雅退出**：`quit` 消息/SIGINT → 清理 socket、serve_loop 返回摘要 dict（进 explore 最终报告，
  probe_boot.py:2140 `result.update` 先例）；serve_loop 不返回则报告不组装（server 常驻的预期行为
  [设计 §4.1 生命周期]）。

### 3.3 失败恢复：world_dead → 快照重建（SIMBRIDGE R2 落地）

- **触发**：direct_call Blocked/预算耗尽/SP 不平衡、resume 段 Blocked、任何 explore 侧未预期异常
  逃进 serve_loop。处理=置 `dead=True` → 给活动客户端发
  `{"t":"error","code":"world_dead","detail":...}` → 关连接（app 侧 SimulationGattLink 把 error
  映射为可见断线 129 → transport 重试路径接管 [文档：SimulationGattLink.java:176-179 现状]）→
  serve_loop 返回 `{'world_dead': {...}}`。
- **重建**：不做「世界内自愈」——快照 restore 的 machinery 绑在 explore 构造链上（probe_boot.py:
  1871-1908 在主循环前消费），post_stop 里二次调用不成立。**外层循环重建**（§3.2）：serve_loop
  返回 → explore 收尾 → 外层 while 重新 build_world（snapshot restore ~8s [文档：M3FIX 实测]）→
  新 serve_loop 重新 accept。重建期间接入的连接在 listen backlog 排队（M1 规模可接受）。
- **重建语义**：世界回**基线停点**（捕获时点），M1-c 已 resume 的推进丢失、已写命令效果丢失——
  客户端按需重放（app 的读命令幂等；写命令重发语义=真机写静默重发，无害）。日志记录重建次数与
  触发原因，验收时报告。

### 3.4 socket 适配层：ble_write → 直调分发

会话层（SIMB1A 的 `SimBridgeSession.handle`/`handle_frame` 骨架保留）改造点：

1. `hello_ok` 的 world=driver.describe()（mode=direct-world 横幅）。
2. `handle_frame` 读分支：`state.read(cmdid, sn)` 返回数据段 → `build_response(sn, payload)` →
   `ble_notify`（SIMB1A 现状逻辑不动，只是状态源换了血）；返回 None → 静默（n<1 语义）。
3. 写分支：`state.apply_write(cmdid, data)` → 不回帧（写静默），返回的 dict 进日志与
   `last_write_result`（供宿主测试客户端断言取用）。
4. **直调错误传播**：driver 抛 Blocked/预算类异常 → 会话层捕住 → `{"t":"error","code":"blocked"|
   "budget"|"world_dead",...}`（设计 §4.2 契约的 error 族）——不臆造回执、不静默吞（SIMB1A 的
   「error=可见断线」app 侧映射现状保留）。
5. **控制面消息**（宿主测试客户端用；app 不发，SimulationGattLink 对未知消息忽略——handleLine
   只认 chunk_ack/ble_notify/error [文档：SimulationGattLink.java:161-180]）：

| 消息 | 语义 | 回 |
| --- | --- | --- |
| `{"t":"status"}` | 世界横幅刷新 | `{"t":"hello_ok","world":{...}}` |
| `{"t":"inspect","addr":"0x…","len":N}` | world_inspect 白名单代理 | `{"t":"inspect_ok","bytes":[…]}` / error(not_whitelisted/world_dead) |
| `{"t":"world_diff"}` | 写集差异（§2.4） | `{"t":"world_diff_ok","regions":{...}}` |
| `{"t":"resume","steps":N,"watch":["0x…"]}` | M1-c 解冻 | `{"t":"resume_ok","steps":M,"watch_hits":[…]}` / error(blocked) |
| `{"t":"direct","pc":"0x…","args":[…],"watch":[…]}` | 直调裸探针（bring-up/调试；§2.1 前置门全跑） | `{"t":"direct_ok","r0":…,"steps":…,"watch_hits":[…]}` |
| `{"t":"quit"}` | 优雅退出（清理 socket，serve_loop 返回摘要） | 连接关闭 |

6. **单线程纪律**（设计 §4.1）：machine 非线程安全——**直调世界的 accept+handle 全部跑在 post_stop
   线程内**（serve_loop 内联处理消息，不开会话线程）。这与 SIMB1A 的会话线程模型不同（静态源无
   共享可变世界，线程模型只是先把纪律占位）——改造时把 `SimBridgeSession.serve` 的执行点从
   session 线程挪进 serve_loop 循环体；并发客户端在 backlog 排队（或 listener 非阻塞探测回 busy，
   实施二选一，验收记录写明）。静态模式维持 SIMB1A 现状（线程模型不变）。

### 3.5 与 StaticFixtureState 的关系（兼容面）

`--static`（默认无世界冒烟/AVD 通道测试）与 `--world <snapshot_dir>`（直调半边）二选一：
- 静态模式：SIMB1A 行为逐字节保留（fixture 读/写静默/set_fixture 控制面）。
- 世界模式：§3.4 的读写分支 + 控制面；set_fixture 消息在世界模式回 error(not_applicable)。
- 两模式共用：RcspDeframer、build_response、资源上限、busy/error 帧形——协议层零分叉。

## 4. M1 三级验收的机检判据

验收执行器=宿主侧测试客户端（实施路新文件 `tools/test_sim_m1_verdicts.py`，走 socket 协议，
退出码即 verdict；对 `--static` 模式可跑通道子集做契约回归）。每级=消息序列+断言清单，全部机检。

### 4.1 M1-a 读环（通道已过 [文档：SIMB1A]，直调世界重验）

前置：`--world` 起服（首轮捕获或 restore），hello_ok 横幅 `mode=direct-world`、stop_pc/steps 非空。

| # | 命令序列 | 断言 |
| --- | --- | --- |
| A1 | 发 `FE DC BA C0 FF 00 04 \| SN FE 01 17 \| EF`（protocol.md:39 实测命令形，SN 取非常见值如 0x6F） | ①收到 chunk_ack；②收到 ble_notify 帧=`FE DC BA 00 FF 00 07 \| 00 SN 17 03 m s st \| EF`（protocol.md:40 形）；③SN 回显==请求 SN；④数据段==`17 03`+3B |
| A2 | `{"t":"inspect","addr":"0x12666B","len":3}` | 返回 3B == A1 响应里的 m s st（**独立观察面互证**：回执不是罐头，是 RAM 实读——替代静态 fixture 时代的「双配置」反假阳性） |
| A3 | 发 `FE 01 22`（EQ 读） | ①数据段==`22 n <3+7N B>`，n==3+7N；②N==inspect b[0x1265C0+5499]；③payload==inspect b[0x1265C0+5356..5356+3+7N)（第二 cmdid 第二镜像面，双面互证） |
| A4 | A1 重复一次（同 SN 不同 SN 各一次） | 同配置两次读结果一致（确定性）；不同 SN 各自正确回显 |
| A5 | 命令后 `{"t":"world_diff"}` + `{"t":"status"}` | 世界存活（非 dead）；diff 区段 ⊆ {scratch/直调栈窗/池 TX 队列（池在位时每读 +1 节点）}，**镜像面（+5356/+5499/0x12666B..D）零变化** |

verdict=**直调读环通过**。若 A1 无 ble_notify 且 inspect 镜像可读 → 判「getter 静默（n<1）」，按
0x17 getter 恒返 3 的静态结论这属异常，回报形状细节（不猜）。

### 4.2 M1-b 写半程：EQ 0x22 全效果 + ANC 0x17 直存投递

**EQ 主目标**（SIMPRE 上调后：apply 内联同步完成，M1-b 即全效果 [静态：SIMPRE §3.3]）：

| # | 命令序列 | 断言 |
| --- | --- | --- |
| B0 | （准备）inspect b[0x1265C0+5499] 记 N_old；选合法新曲线 N_new≠N_old（3B 头+7B/段，字节公式照 eq-cmd-0x22-layout.md §7，段内值取非平凡组合） | N_old 可读且 1≤N_old≤10 |
| B1 | 发写帧 `FE DC BA C0 FF 00 len \| SN 22 dlen <曲线> \| EF` | ①chunk_ack；②**窗口内无 ble_notify**（写静默——13 setter 恒返 0，server 不合成 [静态：SIMPRE §1.2]） |
| B2 | （宿主客户端）取 last_write_result / direct 日志 | ①setter==0x602583c（表 A 实读）；②n==0（恒返 0 常态）；③watch_hits 含 0x6025118（**apply 内联触达**）；④steps 记录在案（耗时面，R7 依据） |
| B3 | inspect b[0x1265C0+5499] 与 b[0x1265C0+5356..+3+7N_new) | 段数==N_new；镜像==写入曲线（**直存断言**） |
| B4 | 发 `FE 01 22` 读回 | 数据段==写入曲线（读回确认=值已变的读往返，M1-a 面复用） |
| B5 | `{"t":"world_diff"}` | 区段归因：{EQ 镜像+段数}∪{EQ 引擎写集（JLEQ 域，§6 D2）}∪{heap ctx}∪{直调栈窗}∪{scratch}——区段外出现变化=FAIL（不可归因写） |
| B6 | 负例：畸形 EQ 帧（dlen 使 len%7≠3） | ①chunk_ack、无 ble_notify；②inspect 段数/镜像**不变**（固件 setter 真实拒绝路径——绕过 app 侧 EqWritePolicy 形状校验的 server 侧入口，设计 §5.1-c ④② 同款） |

**ANC 写半程**（直存+投递两级，apply 留 M1-c）：

| # | 命令序列 | 断言 |
| --- | --- | --- |
| W1 | 发写帧 `SN 17 03 01 01 02` | chunk_ack、无 ble_notify（写静默同上） |
| W2 | last_write_result | ①setter==0x6025ca4；②n==0；③watch_hits 含 0x6064978（**post 投递触达**） |
| W3 | inspect 0x12666B..D | ==`01 01 02`（**直存断言**；若不变→门值诊断：inspect [0x1265C0+2444]/b[0x1265C0+5] 记录在案，按 §2.3 门值处置，verdict 如实停在半程） |
| W4 | post 落队断言 | watch 0x6064978 捕获参数 → 推导 app_core 队列 [待实测：ABI 核对] → inspect [queue+14] 计数 +1，且 [queue+16] 缓冲出现头字 `(0x300000<<8)|2` + w0=0x6025D6A + w1=flags(1) + w2=参数字 [静态：SIMPRE §3.1 事件字 ABI] |
| W5 | 负例：写 0x2f（官方槽 setter=0） | 静默跳尾（last_write_result.setter==0），无直调发生 |

verdict=**EQ 写全效果通过 / ANC 写半程通过**（分别计）。B2 的 apply watch 未命中或 direct Blocked →
EQ 停在半程，不降级冒充（设计 §5.1-b 纪律）。

### 4.3 M1-c 写全环：ANC 0x17 异步消费（解冻续跑）

| # | 命令序列 | 断言 |
| --- | --- | --- |
| C0 | （接 M1-b W1-W4 之后，队列已有 1 条 0x300000 事件）`{"t":"resume","steps":N_c,"watch":["0x6064c0e","0x6025d6a","0x6012be2","0x600dcee","0x6009f6a","0x6025652"]}`（N_c 首跑标定，起点 1M 步量级 [待实测]） | resume_ok；steps 实跑数记录 |
| C1 | watch_hits 序列 | **0x6064c0e 命中**（回调消费铁证——0x300000 在 get_event 内联执行 [静态：SIMPRE §3.1]）→ **0x6025D6A**（ ANC 回调入口）→ **0x6012BE2/0x600DCEE**（apply 链）按序出现 |
| C2 | inspect b[[0x1265C0+500]+33]（两级读） | 值迁移到写入的 mode（**apply 落点断言** [文档：AO+SIMPRE §3.3]）；对照 C0 前先读一次留基线 |
| C3 | world_diff | ANC ctx 区段+VM key16 面（若触达）变化；归因同 B5 纪律 |
| C4 | （若固件路径产生）watch 0x6009f6a/0x6025652 命中的通知载荷 | server 转发 ble_notify（宿主客户端/app 收到 0x2E/0x17 族异步通知帧）；不产生则如实记「本世界无通知帧」，不作 FAIL（通知是否产生取决于固件路径与世界状态 [未钉]） |
| C5 | resume 后再发一条读命令（如 `FE 01 17`） | 世界存活、直调前置门在新上下文重新通过、回执正常（**解冻后可继续服务**） |
| C6 | 稳定性：连续 ≥10 条混合命令（读写交替+一次 resume） | 每命令后 SP 平衡（direct 日志）、world_diff 可归因、无 world_dead |
| C7 | 负例：`{"t":"direct","pc":<已知撞未映射地址的函数>}` 故意 Blocked | ①收到 error(world_dead)；②连接被关（app 侧可见断线语义）；③重连后 hello_ok 横幅 steps==基线停点步数（**快照重建复役**）；④再发读命令正常（重建后服务恢复） |

verdict=**完整命令通过**（ANC 全环）。C1 铁证缺失（resume 跑完 N_c 步无 0x6064c0e 命中）→ 判
「解冻未消费」，按 SIMPRE §3.2 的未钉项处理（pend 唤醒依赖 tick 的形状细节），加跑一轮 N_c×2
仍无则如实 BLOCKED，回报步数与 watch 流水。

### 4.4 验收记录落档

每级跑完把 {消息序列、断言结果、watch_hits、world_diff 区段、direct steps（耗时面）、世界横幅
（stop_pc/steps/scratch）} 落 JSON（路径实施路定，进 analysis/local-checks/ 惯例区）——EQ apply
若实测 >10⁵×N 步/秒换算超 5s，按 R7 二选一（按超时判失败/可配置超时扩展）记录在案。

## 5. 实施切分：现在就能写 vs 等 JLEQ

### 5.1 现在就能写（零 probe 五文件改动，全部 server/新文件）

| 项 | 文件 | 内容 |
| --- | --- | --- |
| 直调驱动器 | tools/sim_world_driver.py（新） | §2 全部：direct_call/rcsp_read/rcsp_write/resume_steps/world_inspect/world_diff/表 A 实读/scratch 守卫。只 import 公共面（不 import 禁区文件里的场景函数，S 先例同语义独立实现） |
| 驱动器单测 | tools/test_sim_world_driver.py（新） | 纯逻辑部分（表 A 解析/帧包装/diff 归因/watch 流水断言）用合成 machine 形状；世界相关断言留给验收执行器 |
| server 对接 | tools/sim_bridge_server.py（改） | §3.4/§3.5：WorldState 接口化（read 加 sn 形参）、--world 模式、控制面消息、错误传播、单线程改造（世界模式） |
| 世界引导 | tools/sim_world_boot.py（新） | §3.2 构造链（**先按回退案实现**：复刻 irq_case 尾部形状直接调 probe_dualcore.dual_case(..., post_stop=serve_loop)——dual_case 非 probe 五文件且已有 post_stop 透传 [本基线核对：probe_dualcore.py:23]；irq_case 的开关集/fixture 逐项照 probe_irq_entry.py:1282-2682 现形状复刻并集中成显式配置） |
| 验收执行器 | tools/test_sim_m1_verdicts.py（新） | §4 消息序列+断言，退出码 verdict |

注意：回退案（dual_case 直调）**现在就能写且能跑**（构造链复刻的维护成本=R16 既有结论，配置化
缓解）；§5.2 的 irq_case 透传参合并后切换过去，复刻段退役。

### 5.2 等 JLEQ 合并后做（probe 五文件，当前禁区）

| 项 | 文件 | 改动 | 必要性 |
| --- | --- | --- | --- |
| irq_case 通用 post_stop 透传 | probe_irq_entry.py | 签名加 `post_stop=None`；尾部 `dual_case(..., post_stop=显式参 or 键链内部值)`，互斥守卫（显式参与 key_chain_scan/matrix 不同时给）——机械 ~3 行，先例=既有 post_stop 内部传递（:2660-2682） | 消 R16 构造链复刻（server 从「跟前沿场景演化」变「一个参数」）；不做也能跑（回退案） |
| resume 循环校准 | （server 侧改，依据 probe 新形态） | JLEQ 若动 br28_model 交付/advance 形状，§2.6 的 mini 主循环按届时 probe_boot 主循环复核 | 正确性 |
| （其余四文件） | analyze_image_trace/br28_model/frontier_registry/probe_boot | **零改动**——explore 的 post_stop/snapshot 均公共参数（本基线核对：probe_boot.py:98-100/2076-2078/2050-2075/1871-1908），br28_model 若被 JLEQ 改则属其自身演进，server 无需跟进（除非交付签名变，见上行） | — |

**直调驱动器的世界准备不需要 probe_boot 改动**：post_stop 回调内拿到 (machine, hardware, cpu)
三元组即可做全部世界准备（寄存器/栈/SN 缓冲），explore 侧零新挂点——这是 S 先例已验证的通路
（make_key_chain_post_stop 在同通道做完 fixture 安装+多轮直调+观察 [文档：probe_irq_entry.py:877-1042]）。

### 5.3 「JLEQ 合并后第一刀」清单（按序）

1. **重钉世界**：跑过线 p11_heap 场景（JLEQ 后新前沿），`snapshot={'capture': <dir>}` 捕获新停点
   世界（代码指纹已变，旧快照必拒载 [文档：probe_boot.py:1871 匹配条件/R12]）；记录 stop_pc/
   steps/per-core 状态。世界就绪自检：app_core 任务在位（§4.2 W4 队列可推导）、配置 RAM 镜像可读
   （0x1265C0+5356/+5499/0x12666B..D 映射且已初始化——bss 域实证 [文档：HANDOFF 路 W]）、
   §2.5 scratch 守卫（空直调 diff=∅+深栈探针 min_sp）。
2. **irq_case 透传参**（§5.2 表第一行）+ server 从回退案切到直调 irq_case（构造链复刻段退役，
   scenario_flags 单源不变）。
3. **P2 动态试跑**（SIMPRE 静态结论的动态核对）：0x17 四门在停点世界的读值（门值诊断面 §2.3）；
   0x22 setter 直调首跑（watch 0x6025118/0x602503a、镜像断言、JL_EQ 域写集是否放行=JLEQ 模型
   覆盖面）；0x600f85e 读路径首跑（拦 0x600b66e 形状核对）；直调实测 steps（R7 超时判定依据）。
4. **M1-a 直调世界重验**（§4.1）→ **M1-b**（§4.2，EQ 全效果 verdict 视第 3 步 JL_EQ 域结果定档）
   → **M1-c**（§4.3，N_c 标定）。
5. 验收记录落档+HANDOFF 条目（含 EQ verdict 档位与 JLEQ 依赖的如实标注）。

## 6. 风险与依赖

| # | 风险/依赖 | 影响 | 缓解（落点） |
| --- | --- | --- | --- |
| D1 | **JLEQ 在途**（五文件禁改+世界停点会前移+JL_EQ 写模型不定） | 本规格的 resume 循环形状/世界横幅/EQ 深层写集全部条件于其最终形态 | §0 接口假设声明；§5.3 第一刀重钉；切换成本=校准非重写 |
| D2 | **EQ apply 深层 MMIO**：apply 链 0x6025118→0x602503a→0x607538a 静态窗口零 MMIO 立即数，但 JL_EQ 寄存器（0xfe1500 族）可能在更深的节点运行时（堆 ctx/函数指针表）被触达 [静态：SIMPRE §2.4]；当前前沿恰停在 0x6072b92 写 0xfe1500 [文档：HANDOFF] | M1-b EQ verdict 档位：JLEQ 模型覆盖→全效果；不覆盖→直调 Blocked→verdict 停半程 | §4.2 B2/B5 如实分档；不降级冒充（设计 §5.1-b） |
| D3 | **解冻续跑稳定性** [未钉·动态：SIMPRE §3.2] | M1-c 不可达或 world_dead | C1 缺失时加跑翻倍步数仍无则 BLOCKED 回报；C7 重建兜底 |
| D4 | **读命令的 TX 队列累积**：池在位时每读真实组帧挂 BT TX 队列（rcsp 任务冻结不排空） | 多命令后池耗尽/世界漂移（R9 族） | M1 规模（十条级）可承受；验收记录节点数；后续可加 world_reset 扩展位 |
| D5 | **直调耗时 vs 5s 看门狗**（EQ apply 1M 步预算 ~10s 量级 [设计 R7]） | 正确仿真也超时断链 | §4.4 实测落档+二选一显式记录；写命令超时在真机本就发生（写静默 [静态：SIMPRE §1.2]），读命令链短（几十条指令量级）不受影响 |
| D6 | **快照重建耗时/频率**：每次 world_dead 重建 ~8s [文档：M3FIX] | 验收时长增加；频繁重建=世界不稳信号 | C7 单次重建纳入验收；重建次数进报告，>阈值判稳定性 FAIL |
| D7 | **0x17 门值/0x606487c ABI/0x2f 槽 getter** [待实测] | ANC 直存断言/落队断言/电量显示面 | §5.3 第一刀 3 核对；门值处置走具名 fixture 先例（synthetic 标注）；0x2f 从表 A 实读自答 |
| D8 | **构造链复刻腐化**（回退案期间前沿演化） | server 起世界失败 | 配置化+scenario_flags 单源；§5.2 透传参合并后消解 |
| D9 | **scratch 踩活数据**（空直调 diff=∅ 不证无动态引用 [设计 R14]） | 世界损坏 | §2.5 双守卫+换址余地；diff 归因纪律（B5）兜底发现 |
| D10 | **sem 槽 ±1/0x12b1a2 字节级语义** [未钉：SIMPRE §3.5] | 不影响 M1 路线（T1 绕开分发器内部信号量） | 无需动作；留 RAM-code OS 专项引子 |

## 7. 证据锚点索引（复核用）

| 结论 | 锚点 |
| --- | --- |
| S 先例直调形态（save/哨兵 0x100000/受控步进/不调 advance/watch 记 r0-r3） | tools/probe_irq_entry.py:842-876（本基线只读核对） |
| post_stop 契约/调用点/结果合并；snapshot capture/restore 公共参数 | probe_boot.py:98-116（docstring）、2076-2078、2140-2141、2050-2075、1871-1908 |
| resume 循环参照形状/交付分发 | probe_boot.py:2016-2047、1910-1969 [依赖 JLEQ 最终形态] |
| irq_case 无 post_stop 透传/尾部 dual_case 传递；dual_case 有透传 | probe_irq_entry.py:1282-1335（签名）、2660-2682；probe_dualcore.py:23 |
| scenario_flags 单源 | frontier_registry.py:423 |
| 分发器 0x600f85e 全 ABI（读/写双路径/包装/resp 补头/pend 墙） | sim-m1-pre-study.md §1.1（0x600f87a-0x600f8b2/0x600f8b4-0x600f968 逐 PC） |
| 13 setter 恒返 0/setter ABI r0=resp,r1=128,r2=data,r3=len/resp+2 死代码裁决 | sim-m1-pre-study.md §1.2/§1.3 |
| 表 A 布局 [0x060a9b78,0x060a9d1c) 15×28B | rcsp-return-path.md §1.1；设计 §3 |
| R1/R2 拦截点（0x600b66e/0x6009f6a 入口载荷级） | rcsp-return-path.md §2.2 |
| 镜像表（EQ +5356/+5499、ANC 0x12666B..D、ANC ctx +500→+33、池 ctx 几何） | rcsp-return-path.md §3；sim-m1-pre-study.md §3.3 |
| 0x300000 消费=get_event 内 0x6064c0e/事件字 ABI/队列几何 | sim-m1-pre-study.md §3.1 |
| watch PC 集 | sim-m1-pre-study.md §3.4 |
| EQ 0x22 全链（setter/getter/apply/镜像/key34） | sim-m1-pre-study.md §1.2；eq-cmd-0x22-layout.md §7 |
| ANC 0x17 全链（四门/直存/post/回调/apply 落点） | sim-m1-pre-study.md §2.4/§3.3；evidence/anc-cycle/qcy-command17.asm（经设计 §8 引） |
| 帧形/响应形（实测对） | docs/protocol.md:39-40 |
| socket 契约 proto=1/错误可见性/单活动客户端 | tools/sim_bridge_server.py（SIMB1A）；SimulationGattLink.java:161-180/176-179 |
| 快照 ~8s/全量重放 ~27min/restore 匹配条件 | 设计 §4.1（引 snapshot-resume.md/M3FIX）；probe_boot.py:1871 |

## 8. 红线自查（本路）

- 零 tools/ 改动、probe 五文件零触碰（git status 可查）；本文纯新增 docs/。
- HANDOFF 未动（条目草稿随报告交主协调）。
- 不发明：所有 PC/地址注明出处（§7）；JLEQ 依赖项逐处标 [依赖 JLEQ 最终形态]；[待实测] 项集中
  于 §5.3 第一刀 3 与 §6 D7，不臆造值。
- 三级验收全部机检（§4 消息序列+断言+退出码）；写静默不合成回执；BLOCKED 不计通过。
- 提交不加 Co-Authored-By；文档内无私有路径/主机名（示例一律 127.0.0.1）。

## 9. SIMB1F 实施记录与勘误（JLEQ 合并后第一刀实跑，2026-11-17）

基线 main 5e65791（JLEQ 已合并），worktree sim-simb1f。§5.3 五步全部实跑；以下为实测与
本规格的冲突/超出点，逐条勘误（原章节文字不改，以本节为准）。

### 9.1 重钉世界（§5.3-1）

- 新前沿复现：29,601,125 @ 0x606955c 读 0xfd1104（快照 3.2MB，capture ~13.5min）。
- **勘误（快照适配器延伸）**：规格假设「旧快照必拒载→重捕获即可」；实际 JLEQ 新增的三处
  硬件/机器状态不在 world_snapshot 适配器清单里，守卫显形拒载（missing-state guard 设计
  如期工作）：M4 加 `jl_eq_init` 行 + `gpcnt` 行补四个计数器（config_reads/whole_writes/
  enable_writes/completion_reads）、M5 加 `timer2_poll_wakes`。三处都是「恢复缺失会改变
  后续行为」的 STATE，不是 static。
- **勘误（世界就绪自检）**：park 前沿停点在 **Timer0 handler 链内**（事件循环=handler，
  稳态停点天然如此；停点 sp=0x1237d8 在 bootstrap_stack 区）。SIMB1C 纪律②的
  「active_context 必须 None」一刀切拒绝对此世界不可用。处置：handler 上下文不再拒绝，
  改为**上下文寄存器完整性校验**（哨兵处比对 icfg/icfg_known/reti/irq_psr/active_context，
  callee 触碰任一 → world_dead 如实暴露）+ banner `in_irq_context` 如实标注。S 先例
  （probe_irq_entry.py:846-876）本无此门；其上下文敏感性关注点是 callee 依赖中断帧
  （IRQ39 EMA 段不直调），不是调用方上下文污染。

### 9.2 irq_case 透传参（§5.2 第一行）

已落地：签名加 `post_stop=None` + 与键链场景互斥守卫（~6 行含注释）；
sim_world_boot.py 退役 monkeypatch 缝合线改直连。probe_irq_entry.py 其余零触碰。

### 9.3 P2 动态试跑（§5.3-3，三处 [待实测] 实测值）

- **0x17 四门停点读值**：gate2=[0x1265C0+2444]=0（id≠5）；gate3=b[0x1265C0+5]=0
  （直调 0x60065bc 返 0，3 步）；gate1=连接查询 0x6003578 直调返 0（91 步，~10ms）；
  gate4 静态零调用（无动态量）。注意：四门读值不掩 W3——本世界 ANC 直存生效
  （W3 PASS，wire=01 01 02），门值与 setter 行为的精确对应关系留静态面复核。
- **0x22 setter 首跑（JL_EQ 域写集=verdict 档位依据）**：apply 0x6025118 **真实命中**
  （step 66，N=2）→ 深链 0x60642ec 写 0x20（近空指针）撞 unmapped_or_readonly_write。
  非 JL_EQ 域（0xfe1500 族零触达）——是 apply 链的事件机制面（0x6064xxx post 族）在
  park 世界无 EQ 管线节点状态下的真实墙。**M1-b EQ verdict=停半程（D2 如实分档，
  不降级冒充）**；旧世界（EQ 域写入处停点）同调用链 59,634 步全通——两世界档位差异
  的根因=管线节点初始化状态不同，归 EQ 深链世界状态依赖项，留待（消费面/节点来源
  钉死后）复验。
- **0x600f85e 读路径首跑+直调耗时**：分发器直调在停点世界撞**上游 store 模型合并读旧字**
  的未初始化栈假停——0x600f95a `ext.l.u.p∥st.w.sp`（Writes.put 对整字存也读旧字，
  probe_boot:1633 注释钉死这是模型簿记读非 guest 装载）；分发器 216B 帧落在从未写过的
  handler 栈口袋 [0x123700,0x1237d8)。世界已有的 exact_parallel_stores gate 只接受
  pure-ALU 前缀清单（li/mov/add/sub/and/or/xor/div + 扩展 ext.b0.u），`ext.l.u` 不在
  清单（probe_boot 属五文件禁区，本路不扩）。**§2.2 降级备选落地**：getter 直调
  （表 A server 侧 walk + cmdid|n 头 server 组装——R15 残余，cmdid 路由无固件执行
  证据，banner read_mode 如实标注）。复役判形（严格）：direct_call_blocked + 上下文
  面未动 + 墙在死帧窗 [min_sp_seen, entry_sp) 内；寄存器由 finally 恢复，留下的存储
  副作用=死帧区栈字（真机上中止的调用同样留下）。直调实测耗时：getter 12 步、读环
  往返均值 40.7ms（R7 面宽裕，5s 看门狗无虞）。

### 9.4 M1 三级验收（§5.3-4，park 前沿世界）

- **M1-a 读环 5/5 PASS**（A1 字节级往返/A2 镜像互证/A3 EQ 双面/A4 确定性+SN 回显/
  A5 diff 归因⊆{direct_call_stack,scratch} 且镜像面零变化）。勘误：A5 的 regions 期望
  值在本世界无 heap 项（getter 路径不走 BT 池）。
- **M1-b ANC 写半程全 PASS**（setter 0x6025ca4/343 步/post 0x6064978 触达/直存
  0x12666B..D=01 01 02/读回 1703010102/0x2f 官方槽空静默）；W4 落队=INCOMPLETE
  （post 触达但 poster 调度门静默丢，与旧世界同款，固件 fire-and-forget 同语义）。
  **M1-b EQ=停半程**（见 §9.3 第二条：B1 BLOCKED，墙形状入验收记录）。
- **M1-c 解冻全环=C0 steps=0 即撞墙**：前沿停点本身就是未放行的 0xfd1104 读
  （GPCNT NUM 测量值 fixture 三选一归下一路）；CPU0 停在故障指令上，restore+budget=1
  语义下首个 select 必重撞——park 稳态不改变此结构位置（与旧世界撞 0xfe1500 写墙
  同构，墙换了地址）。墙前无 0x300000 消费命中（C1 INCOMPLETE；且 W4 的 post 本就
  未落队——双重前置缺）。**C7 负例全过**：resume 死亡=可见断线、外层快照重建后
  横幅回基线停点 0x606955c、读命令恢复服务。M1-c 全环通过的前置=下一路放行 GPCNT
  NUM fixture（或停点不再压在故障指令上的前沿）+ poster 调度门世界状态。
- 验收器四态化（codex P1 吸收）：PASS/FAIL/BLOCKED/PARTIAL——必需断言被世界状态挡住
  记 INCOMPLETE（note_required），总 verdict 降 PARTIAL（退出码 5），不许靠 NOTE 混
  全绿；输出带 executed/incomplete/notes 计数。

### 9.5 resume 循环校准（§5.2 第二行）

两处与 explore 主循环形状对齐（旧世界未显形、park 世界必踩）：
- **idle 停泊分支**：park 世界两核靠 idle→enter_idle_wait 停泊，SIMB1C 复刻段漏了
  主循环的 idle 检查（idle 不交给模型 step）；
- **停泊分支嵌套 advance 的 IrqDeliveryRequest 必须走交付分发**（codex P1：此前被转成
  upstream_simulator_failure——resume 与主循环不等价，消费铁证不可信）。
另：Blocked+st.* 的未初始化读按主循环同款重分类为 upstream_store_bookkeeping_read
（报告口径对齐，控制流不变）；延期交付补 note_delivery_deferral 簿记。

### 9.6 本路新增/修改文件清单

- tools/probe_irq_entry.py：irq_case post_stop 透传（§5.2 最小改，唯一 probe 五文件改动）。
- tools/sim_world_boot.py：退役 monkeypatch，irq_case 直连。
- tools/sim_world_driver.py：上下文完整性校验/读路径 getter 备选/resume 两处校准/
  错误诊断面（stop_pc+watch_hits 进 detail）/min_sp 步内记账。
- tools/sim_m1_p2_dynamic.py（新）：P2 动态试跑执行器。
- tools/test_sim_m1_verdicts.py：四态验收器+M1-c 独立试跑+连接死亡处理。
- tools/world_snapshot.py + test_world_snapshot.py：M4/M5 三处新状态面。
- tools/test_sim_world_boot.py / test_sim_world_driver.py / test_sim_m1_helpers.py /
  test_image_trace_diff.py：配套单测（透传参守卫/回退判形/四态机/transports 清单）。
- 验收记录：analysis/local-checks/sim-m1-p2-dynamic.json、sim-m1-verdicts.json
  （本地工件，不入 git；关键值已摘入本节与 HANDOFF 条目）。

### 9.7 读路径回全固件证据（路 PARSTOR，2026-11-17）

§9.3 记录的 getter 降级根因（0x600f95a `ext.l.u.p∥st.w.sp` 撞上游 store 模型
合并读旧字）由路 PARSTOR 关闭：probe_boot exact_parallel_stores 新增独立子开关
`exact_parallel_ext_transfers`（ext.b0.u/ext.b0.s/ext.l.u/ext.l.s 传送族前缀，
旧寄存器语义同 FM-1 run3），经 registry `ext_pair`（conditional）进本世界开关集。
重捕获实测：前沿 29,601,125@0x606955c 逐字段零位移；停点世界分发器直调过
0x600f95a 墙，read_mode 保持 dispatcher（§2.2 首选路径恢复，getter 备选仅作
运行时兜底保留）。前缀内 287 次 ext 对从上游 pair 模型迁至新 gate（架构等值，
adapter 计数迁移只落在 opt-in 条件场景报告）。M1 三级验收按此世界刷新（§4 四态
如实：EQ 0x18d03c 堆墙/0x60642ec 空指针墙是另外两族问题，不在本路范围）。

### 9.8 EQPIPE：EQ 深链墙根因钉死（两世界对照复跑，2026-11-17 07:17-08:3x）

SIMB1F 留待项「EQ 管线节点 0x60642ec 世界状态钉源」的结案记录：**档位差异的根因不是
EQ 节点注册表缺初始化，而是 0x22 setter 的 apply 深链是「共享解码工作区 + 任务调度依赖」
的有状态路径**——旧停点（29,215,313）恰是 boot EQ 安装中途的快照，让直调意外相容；
park 前沿（29,601,125）是安装后稳态，投递命中等待者后进入唤醒/让出链，冻结单上下文
直调在第二轮 pend 上破坏挂起链表，经空 next 指针写出 0x20。逐条证据：

**对照设置与 parity（口径显式）**。两臂同一曲线（`eq_curve(2)`，3B 头+2×7B 段）、同一
setter 0x602583c、同 ABI（r0=resp,r1=128,r2=body,r3=len）、同一驱动纪律（§2.1）。
新臂=scenario_flags('p11_heap_experimental') 全开，restore D2AJ 快照（停点 29,601,125 @
0x606955c 读 0xfd1104 复现）；旧臂=同 flags 减 {jl_eq_init_writes, core_tick_freerun_edge_
period, audio_adc_dma_delivery}（后两个是 jl_eq_init_writes 的依赖闭包强制项，registry
单源核算），全量重放 capture 855.6s（~14.3min）至 analysis/snapshots-old/，停点 29,215,313
@ 0x6072b92 写 0xfe1500 值 00000000——与 JLEQ 负例臂逐字段一致；快照 restore 复跑 2.6s，
PC 序列与写集逐字节相同（确定性+资产可复用）。parity 论证：三个差异开关的首触面全部
在旧停点当步或之后（jl_eq_init_writes 的面就是停点指令本身；freerun 墙在 29,229,183；
ADC DMA 交付消费更靠后），两世界共享 29,215,313 前的全部执行——停点世界状态差只能产生
于 (29,215,313, park] 的 385,812 步窗口。

**对照结果**。旧臂 59,634 步 r0=0 完成（SIMB1C 数字精确复现），全程零让出链调用；新臂
26,054 步墙 @ 0x60642ec 写 0x20（size 4，墙时 r0=0x127db8/r3=0x20）。两臂 setter 头的
镜像 memcpy 都已落位（新臂死后 eq_segments=02、镜像 17B 与旧臂终态相同——墙严格在
apply 深链，不在直存半程）。

**首分歧 step 514 = 解码工作区内容差**。0x12af52 `bne.and.rr`（RAM 驻留解码器的位池
消费循环；0x12af40/0x12af4c 是并行对，位池 RMW 写挂在 pc=0x12af4c，目标 0x1294d4..
0x129520）。两臂停点时该工作区（0x1294c0..0x129550）仅 7 字节差：位池残留
w@0x1294d8 新 0x00000bff/旧 0x007fffff、w@0x1294dc 新 7/旧 0、w@0x129520 新 0x46/旧 0、
w@0x129538 新 0x403f0000/旧 0x001f0000、消费计数器 h@0x12954c 新 0x266/旧 0x260——
即旧停点=boot EQ 安装解码进行到一半（池还剩 23 位、计数 0x260），park=安装完成后残态。
apply 深链的解码消费的就是这片共享工作区，位流不同→路径从此全分叉。

**新臂墙的机制链（写集+PC 序列逐条对账）**。深链经 eq lib（0x6074xxx，其 0x60741a2 处
调用 0x6064f18=OS 同步对象 init，appcore-tail-map S11/S14 同族）后：①poster 族
（0x60648xx）投递命中等待者 0x41a390：0x6064264/66 清其 +32/+36（B 链摘除）→
0x60642a6/a8 自链 → 0x60642aa 写槽 A3 → 0x60642b0 就绪位图 |=bit3（0x1268E0 变 0x09）；
②让出链（docs/d1-scene-blueprint「空队列 pend 写面+让出链」钉过的 RTOS 族：0x6064188=
就绪链摘除/0x60642bc=挂起链插入/0x6064340=调度器；链表锚=CONFIG_BASE+0x1B20 起每类型
8B 记录 {就绪头@+6944+8k, 挂起头@+6948+8k}，位图=CONFIG_BASE+800）把「当前任务」
0x127db8 从 A0 摘除挂入 B0（B0 空→自链+B0=0x127db8）；③冻结世界无真实调度，pend 后
同栈继续跑；第二轮 pend 时 0x127db8 已不在 A（A0=0x127e10 单元素自链）→ 0x6064188 跳过
邻居解链与槽修正（0x60641a2/0x60641b0 两分支）但**无条件清 node+32/+36**（0x60641ca/cc
——清掉的恰是第一轮 B0 自链）→ 0x60642bc 再挂 B：B0 残留 0x127db8（陈旧）走非空分支，
head→next=[0x127ddc]=0 → r3=0+32=0x20 → `[r3+0]=r0` 撞 unmapped_or_readonly_write。
真机上该序列不可能发生：任务 pend 后不会再运行，两轮 pend 之间必有唤醒（B 摘+A 挂）
把链表恢复一致——冻结直调违反的正是这个单字假设。

**旧臂为何全通**。12 个 0x60741a4→0x6064f18 调用（新臂 2 个）全部无等待者（旧停点音频管线未起，
同步对象等待域空）——投递纯入队返回，零唤醒/零让出（0x60642a6/0x6064264/0x60642ee/
0x60641c6/0x60642aa/0x60641ca 六个写站点计数全 0），解码按安装中途的位池长路消化两段
后 59,634 步返回 0。

**分歧窗口的槽表时间线（新世界全量重放+观察器，钉「boot 路径分歧点」）**。用既有
br28_machine.d1_window_observer 只读钩子（实验侧包装 attach 后注册——机器侧 attach 会清
遗留注册；纯观察 stub，零 probe 改动）全量重放 844.0s 至同一停点（0xfd1104/29,601,125
复现，第三次前沿确定性佐证），槽窗+位图全程 139 写。旧停点前最后状态：B3=0x419fa8
（28,799,469 停靠）、A1=0x41808c（29,210,337 就绪）——与旧臂预态逐字段一致；分歧窗口
(29,215,313, park] 内：0x41a390 短暂就绪（29,221,402 A3 写入→29,225,763 清回）后入
B3 链尾（非空链插入不触槽写）、0x41808c 反复就绪/挂起翻摆（28.9M-29.6M 多轮）后**最终
停靠 B1 于 29,567,135**（距 park 33,990 步）。即：被 eq lib 投递唤醒的等待者（0x41a390）
与 B1 终态停靠（0x41808c）都形成于旧停点之后——旧臂「无等待者」与新臂「有等待者」的
差异全部产生于这 385,812 步窗口，无一是两世界共享前缀里的分歧。

**修复路径判定（任务三选项逐一）**。(a)「EQ 会话未打开」**不成立**——方向相反：park 的
音频管线恰是「开且活着」，消费任务（0x41a390 等，栈在上静态缓冲区 0x41xxxx）在 eq lib
同步对象上等待正是稳态；不存在可直调的「打开/关闭」函数能把世界带回无等待者态而不发明
状态。(b) 开关/fixture 差异**不成立**——见上 parity 论证。(c) 仿真器模型缺口**不成立**——
墙是 guest 固件自身数据流（RTOS 链表经空指针写 0x20，纯 RAM 语义，零 MMIO 参与），模拟器
如实拦截。**真根因=方法论边界**：SIMPRE「13 setter 恒返 0、直调无 pend」的静态结论只覆盖
setter 本体；apply 深链经 eq lib 同步对象与任务调度交互，在冻结单上下文里不可重入。
SIMB1C 旧世界的 59,634 步 PASS 是安装中途快照的偶然相容，不是 setter 的普适性质。

**处置**。B1 维持 BLOCKED（四态如实）：本路零 tools/probe 改动，且在本 worktree 复跑
M1-a/M1-b 验收实证不变——M1-a 5/5 PASS（getter-fallback 注记同款）、ANC 写半程全 PASS
（W4 INCOMPLETE 同款）、B0 PASS、B1 世界死亡 BLOCKED（墙形状与本节对照记录逐字段相同：
0x60642ec 写 0x20、apply step 66 N=2），总 verdict=BLOCKED 退出码 4，记录
analysis/local-checks/sim-m1-verdicts-eqpipe.json（本地工件）；units EXIT=0。解锁正路=
M1-c 解冻全环（真调度下 post→唤醒→切换→消费→再 pend 一致推进），前置仍是 GPCNT NUM
fixture（SIMB1F 已登记归下一路）。冻结帧内无可行「开链」步骤：直调前改等待者/工作区=
发明世界状态（红线）。

**资产与复现**。旧世界快照 analysis/snapshots-old/p11_heap_experimental-sim_eqpipe_old.bin
（gitignored，restore ~2.6s 可复用）；对照/时间线脚本与记录为本地工件
（~/tmp/eqpipe/{run_eq22.py,eq22-new.json,eq22-old.json,timeline_slots.py,slot-timeline6.json}
——逐字节 PC/写集轨迹不入 git，关键值已摘入本节）；位图/槽表窗口预态与墙时寄存器取自
上述记录。
