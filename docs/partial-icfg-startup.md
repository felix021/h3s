# 部分已知 ICFG：暴露活动优先级输入，保留首任务 RTI 边界

2026-11-11，新增的显式 `partial_icfg` 模型不再用一个已知完整字表示未知硬件状态。只给出 gates8/9 初值时，真实启动在 **16,093 条已完成指令、CPU0 `0x06000ed2`** 停止：`ext.b0.u` 需要尚未知的活动优先级字节。另一个明确加入“低字节为零”条件的 profile 才能再次到达 **147,268 条指令、CPU1 `0x06066130` 的首任务 RTI 前**。首任务没有进入，整机启动与 EQ 修复仍未完成。

入口工具为 [probe_icfg_startup.py](../tools/probe_icfg_startup.py)，值与来源模型为 [br28_icfg.py](../tools/br28_icfg.py)，精简结果见 [partial-icfg-startup.json](../evidence/partial-icfg-startup.json)。这是独立的 opt-in 路径；[原有10组双核场景](dualcore-startup.md)保留原策略，默认不启用 partial ICFG。此次没有设备操作、刷机或 GitHub CI。

## 来源约束与模型范围

[ICFG 来源检查](icfg-status.md)将固定 AC791N 原始记录、BR28 SDK 定义及可选 ROM 锚点绑定到摘要和记录位置。ISA 来源固定为 `3608ed19bc21324f8ecfe79cc0018d05e43d9563`，SDK 固定为 `67328f4741c69993c88e504c67377b196c724f97`。例如 app2 controls 5、6 的采样结果不能用普通 whole-word RW 寄存器解释；app3 的嵌套记录约束低字节的活动优先级 bitmap 含义。

这些记录只支持限定写法的投影，**没有证明所有硬件状态位只读，也没有给出 BR28 的完整 writable mask**。当前模型支持 literal `0`、`0x200`、`0x300` 的 gate 写投影，以及带可验证来源的 gate 修改；不把其他 literal 或任意 status 写入推广成可用操作。它仍不认证耳机真实 reset 值、异常入口或任务 RTI。

## 两组官方路径

| 显式输入 | 实际停止点 | 停止时的知识 |
| --- | --- | --- |
| 两核 initial known mask=`0x300`，已知 gates 为0；其余状态未知；bootstrap status preserve mask=0 | CPU0 16,093步，`0x06000ed2 ext.b0.u`；CPU1尚未创建 | ICFG 本身 mask=`0x300`、value=0；提取低字节后的表达式 mask=`0xffffff00`、value=0，低8位仍未知 |
| 两核 initial known mask=`0x3ff`、value=0；额外明确活动优先级字节为0；bootstrap preserve mask=`0xff` | 合计147,268步，CPU1 `0x06066130 rti` | 两核 ICFG mask=`0x3ff`、value=`0x200`；bit10及以上仍未知；首任务 RTI 未执行 |

第一组中的 `0xffffff00` 是零扩展结果的已知位掩码，不是 ICFG 的已知位掩码，更不表示活动优先级为零。它使 `cpu_in_irq()` 路径对外部初态的依赖显式可见，不能靠未知值读零继续。

第二组低字节初值及其跨 bootstrap 的保留都是条件输入，不是硬件测量或 thread-mode 证明。bootstrap 使旧 status 快照代次失效，gates 变未知；只有实际 QCY 的 read／AND `~0x300`／write 才重新确定两门关闭。之后真实 CLI／STI 序列更新 bit9，其他未知状态保持未知。该次 bootstrap 仍沿用已有一次性返回条件，没有增加首任务或通用 RTI 许可。

到达较晚边界时，CPU0／CPU1分别完成136,405／10,863条指令，共有2,130次锁等待。CPU1 SP=`0x401bfc`、RETI=`0x12c57e`、RETS=0，USP／SSP仍未知；R0为真实参数0，R1–R15保留未初始化帧来源。ready由固件写出，CPU0 pending mask/value=`0x8`，没有自动 ACK。实际双核路径、缓存及其他外部条件仍见[双核启动说明](dualcore-startup.md)，模型步数不代表物理时间。

## 已知位、来源与严格消费者

- ICFG 状态和读取快照分别携带 value／known mask；快照还保存核号、status generation及未修改位的 identity mask。写回要求同核、同代次，并证明 gates 以外的位仍来自原读值；跨核、过期或改动 status 的表达式在提交前拒绝。
- CLI／STI只修改 bit9；带寄存器的恢复形式只消费源 bit9。源 bit9未知时不能制造“已开启”。完整已知的快照可参与普通数值计算；部分已知快照仅支持明确实现的位操作或搬运，需未知数值的算术、分支、地址及特殊寄存器写入严格停止。
- RAM spill／reload保留逐位已知性及来源，已知字节覆盖只更新对应字节。未知 SP、只读区和 MMIO 访问仍受原有检查；部分值不能让普通未知 RAM 或 MMIO 读取变成合法已知数据。
- IRQ 门判断为三值：任一已知关闭门足以排除投递；两门均已知开启时可继续检查；否则保留未知，并结合 source／priority 等过滤器判断是否确实需要该未知输入。它不执行 IRQ entry，也不清 pending。

外设报告的 `multicore.core_icfg={"0":0,"1":0}` **只表示两核的 gate-demand 为关闭**。它不是整个 ICFG 为0，也不能代替 `machine.cores[*].partial_icfg`。同理，晚期的 known value=`0x200`只描述 mask=`0x3ff`内的位；整个寄存器仍报告 unknown。

## 反例、候选与验证范围

12项定向测试覆盖：限定 literal 保留状态、未知状态与 mask-write、CLI／STI及未知 bit9、完整已知数值计算、部分数值消费者原子停止、低字节按需消费、跨核／过期／status改写拒绝、spill与字节覆盖、未知 SP、RAM重叠下的只读／MMIO限制，以及只检查必需 gate且不 ACK 的 IRQ 路径。新前端的两组官方场景通过；精确 repair 候选与历史失败 r2 各运行一次显式空活动优先级路径，也均停于147,268步首任务 RTI。

候选前缀相同不区分启动健康。原版／r2的实际 Flash guard上限为 `0x060c3338`，repair为 `0x060e31c5`；两核执行取指保护继续检查，未越过首任务 RTI。历史 r2仍禁止重刷，repair仍未刷入，这份证据不能单独批准刷机。

上述12项测试和2+2条前缀已通过，`python3 -O tools/test_icfg.py`也通过全部12项测试。**本轮包含私有 ROM 的完整本机回归已完成并退出0**，日志末行确认为 `Local checks passed. No hardware was accessed.`。21份旧JSON报告与基线清单SHA-256逐一一致，保持逐字节不变；全量检查生成的两组官方场景与含两个候选的合并报告完全一致。日志SHA-256为 `a49f8255d25687cabedce2b5c6f037dbbeed85b0fb778f40a725532a873a607a`；精简证据同时记录基线清单摘要，原始合并报告摘要保持不变。

## 本机复现

另一个独立的[USB ROM 分派核对](usb-rom-dispatch.md)在全量回归结束后单独以 `python -O` 运行通过，公共报告逐字节复现。它已加入后续 `--rom` 检查入口；本轮全量进程不包含这条后来加入的命令。该工具只执行 ROM 尾部的离线模型测试，不执行 RAM payload，也不构成设备测试许可。

准备既有固定输入后运行；变量指向本机持有的精确文件，工具不获取 ROM：

```sh
python3 tools/test_icfg.py
python3 tools/probe_icfg_startup.py --rom "$PRIVATE_ROM" \
  --report analysis/dualcore/partial-icfg-results.json

# 可选：同次报告加入两条精确候选前缀
python3 tools/probe_icfg_startup.py --rom "$PRIVATE_ROM" \
  --repaired-candidate "$REPAIRED_UFW" --failed-r2 "$FAILED_R2_UFW" \
  --report analysis/dualcore/partial-icfg-results.json
```

公开 JSON 仅保留输入摘要、profile 条件、停止点及有限状态字段，附完整私有报告的 SHA-256，不包含原始 ROM、私有路径或完整轨迹。首任务 RTI仍缺立即返回后的 gate8、mode、SP／USP／SSP回写及返回许可契约；部分 ICFG 不补齐这些缺口。
