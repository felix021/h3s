# 看门狗、校准、Flash 总线与 OS 初始化回放

2026-11-10，本机离线连续暖启动推进到 **110,818 个模型 step**，停在 `PC 0x06068926` 读取 `JL_SDTAP.CON (0xff0100)`。真实固件已经执行并返回 `wdt_init`、`OSInit`、`tick_timer_init`、`sys_timer_init`，正在初始化端口。实际分配器写出有效页表，并完成 67 次虚拟内存翻译。

这仍是显式虚拟板级输入下的条件路径。尚未执行 `debug_init`、创建应用任务、启动 CPU1 或切入首个任务，也没有完成整机启动、蓝牙、音频或 EQ 行为验证。整个过程没有访问耳机，没有新刷机，没有运行 GitHub CI。

工具与证据：

| 工具 | 结果 | 范围 |
| --- | --- | --- |
| [probe_watchdog.py](../tools/probe_watchdog.py) | [watchdog-execution.json](../evidence/watchdog-execution.json) | 6 组真实看门狗初始化／超时边界 |
| [probe_clock_startup.py](../tools/probe_clock_startup.py) | [clock-startup.json](../evidence/clock-startup.json) | 12 组 OTP／振荡器／计数时钟／配置初态 |
| [probe_arch_startup.py](../tools/probe_arch_startup.py) | [architecture-startup.json](../evidence/architecture-startup.json) | 9 组 Flash／缓存输入／PWM／OS 堆边界，以及可选的两个精确镜像前缀 |
| [analyze_startup_chain.py](../tools/analyze_startup_chain.py) | [startup-chain.json](../evidence/startup-chain.json) | 实际 QCY 调用、CPU1 启动、任务帧和活跃输入的静态锚点 |

## 实际执行到了哪里

暖路径继承此前 ROM、GPIO、MMU 模型和明确标注的 REP／ICFG 假设；P11 的 `PMU_STA` 全字测试输入固定为 1，仍未证明域间握手的响应因果关系。

| 阶段 | 实际结果 |
| --- | --- |
| 看门狗 | 返回调用者 `0x0603518c`；6 组探针分别保留未知、停钟、正常虚拟计时、快速到期和冷启动边界 |
| OTP | 运行实际四页读取程序，16 次 RDAT 读取，再由固件拼字缓存到 RAM |
| 时钟切换 | 运行真实校准、GPCNT 清理、CPU1 暂停检查、电压配置、RAM 中的 Flash 重配置和 ROM 缓存等待代码 |
| OS | `0x060352d8` 调用 OSInit，返回后依次调用 tick_timer_init 和 sys_timer_init；`0x060352ea` 已进入端口初始化 |
| 分配器 | 实际位图索引 `0x13f6 / 0x13f5` 转成 `0x33f6 / 0x33f5`，写出有效 PTE `0xb3f6 / 0xb3f5`，并访问虚拟地址 |
| 当前边界 | 七次半字 REP 初始化栈上数组后，读取未知的 SDTAP 控制状态而停止 |

OS 初始化不等于调度器已经启动：`os_start` 在后续 `0x06035356`，本次尚未执行。

## 看门狗契约

固定 SDK `cpu.a/wdt.c.o` 和头文件给出 `KEY=0x048c159d, 0x26ae37bf`，之后才能写 CON。实际 QCY 初始化的 CON 写序列是 `0, 0, 0, 14, 46`，包括没有改变值的总线写；模型不能把它们省略。最后控制值 `0x2e` 开启计数，名义时间码 14。

投影要求每条 CON 命令前重新观察一对 KEY。这是受限的软件调用契约，**硬件解锁保持多久仍未知**。时间码使用 SDK 查询表的 `2**code` 名义毫秒单位；正常场景显式指定每 step 推进 `1/4000` 名义单位，不宣称真实 CPU 频率或看门狗校准。缺少时钟时拒绝开启，停钟场景必须显式指定 0。快速时钟使真实初始化后立即到期，并停止等待尚未实现的复位／异常交付。

bit16 喂狗命令不保留在配置读回中、启停和喂狗重置相位，都是公开标注的投影。首次显式 CON 初始化之前的计数、bit6／7 的独立含义、到期硬件复位交付没有模拟；不支持的模式继续停止。

## OTP 不能当作死读取跳过

`0xf2c2c0 / c4 / c8` 分别是 EFUSE 页选择、字节控制和读数据。实际读法为选择 `page<<6`，依次写入 `0x82|(byte<<2)`，读取低 8 位，随后清控制寄存器。模型只支持这条读序列，没有编程入口；等待时间没有物理校准。

正例使用四个显式合成的 `0xffffffff`，另一个场景只改 page2 的两个 nibble。**这不是对耳机 OTP 初值的推断。** RDAT 的高位也有明确的非零测试 fixture，实际程序只取低字节。

| 活跃字段 | 固件行为 |
| --- | --- |
| page2 `[15:12]` | `15` 时 XOSC 配置 `0x40000`，否则 `nibble<<15`；测试值 2 得到 `0x10000` |
| page2 `[19:16]` | `15` 时 SYSPLL 基值 `0x408929b0`，否则 `0x008929b0|(nibble<<27)`；测试值 1 得到 `0x088929b0`，后续使能低位成为 `…b3` |

真实缓存顺序为 page0→`0x126860`、page1→`0x126864`、page2→`0x12686c`、page3→`0x126868`。未知输入在首次 RDAT 停止。交接结构中的 16 位 trim 字段与这些 OTP 页没有已证实的等价关系。

## 配置寄存器与外部状态分开处理

- WLA_CON8/9/10/16/21 是有限的配置投影。校准程序采样的是 **WLA_CON30 (`0xfd1978`) bit4**，它是独立的显式状态输入；写配置不能制造 ready。模拟电路、稳定时间和时钟传播仍未知。
- Timer0 新增可选 LSB 时钟，必须单独给出 ticks/step。显式停钟会留在实际轮询，未知时钟源继续停止；RC16M 旧场景不受影响。
- GPCNT 只允许实际观察到的“关使能、清 pending”操作，其他位保持未知。清理后已知 mask 为 `0x81`，没有凭空提供测量值。此处实际引用计数从 0 到 `0xff` 再回 0，退出没有启动测量；NUM 和测频模型尚未实现。
- CPU1 控制必须显式指定初态 0，代码才能判断该核尚未运行。释放 bit0 仍停止；没有写入 ready 标志。
- 两个 core tick 只支持有限配置和显式名义计数。CPU1 计数器还要求单独声明“核暂停时是否计数”，不能从其他型号或寄存器写入猜出。正例使用每 step 4 ticks、暂停时继续计数。CPU1 PRD `479999999` 对应 SDK 公式下的 4 秒空闲唤醒计时器，和硬件看门狗不同。首次溢出就终止，未验证 pending bit7 读回，也未实现唤醒或 IRQ 交付。
- SYS_DIV、5 个 P33／CMNG 配置和 PLL1.CON3 仅接收明确的配置 fixture；没有把整个 P33 或 PLL 地址区变成普通内存。

## 实际 Flash 重配置与取指保护

RAM helper `0x12e1e8` 实际关闭 SFC、写 BAUD、切换模式并重新开启，最终 `CON=0x300001, BAUD=1`，共 4 次写。仿真从已解码的固件映射同步读取，busy 为零仅表示这个模型没有排队事务；没有模拟 SPI 波形、物理时延或缓存一致性。

`0xff0500 / 0xffe400 / 0xffec00` 的缓存等待状态必须单独输入。正例显式提供 `0x80000000`；未知立即停止，持续 busy 留在实际 ROM 轮询，不会自动变 ready。

SFC 关闭时同时禁止 Flash 数据读取和取指，**已有解码缓存也不能绕过开关**。另修复了虚拟页重映射后可能复用旧指令解码的问题：只有不可变 ROM／Flash 可直接按 PC 复用解码；RAM／虚拟取指先按当前映射核对实际字节，变化时重新解码。合成回归先解码旧页，再由 guest 改 PTE，确认执行新页指令；这不意味着已实现硬件 TLB 或指令缓存。

四次 PWM 通道配置读取的值由实际控制流证明不会使用：R0 保持 opaque，直到下一次已验证覆盖。循环、返回地址和下一回调边界均被检查；改成消费 R0 或无法证明调用者时必须拒绝。没有给 PWM 填一个假零值。

## MMU 地址修正来自真实分配

旧模型把位图相对页号当成了硬件页号，额外加了 `0x100000`。真实 OS 路径揭示这个错误：QCY 和 SDK `pmalloc` 在写 PTE 前已加 `0x2000`，当前 CPU RAM backing 为 `((tag&0x7fff)<<7)+offset`。

实际 `VA 0x49fff4, PTE 0xb3f5` 对应 `0x19faf4`，旧公式会错误地写 `0x29faf4`。当前物理堆只映射 `0x134500..0x19fb80`，初值保持未知；缺少该映射的严格场景就在 `0x19faf4` 停止。没有扩充到不存在的 2 MiB RAM 去掩盖错误。详见 [MMU 记录](mmu-startup.md)和更新的[固定对象／函数摘要](../evidence/memory-reference.json)。

## 对失败镜像和修复候选意味着什么

可选参数只接受精确 SHA 的旧失败 r2 `c8dae523…` 和独立修复候选 `67ba97ef…`，重新检查 CRC、交接头一致性和实际 337B payload。两个镜像都从各自的真实 app 字节执行到同一个 `110818 / 0x06068926` 边界；候选同样检查调用里程碑、真实 PTE、分配页号转换、67 次 MMU walk、SFC 写入和 PWM opaque 轨迹，不能因为异常早停而返回通过。

**它们通过的是同一段启动前缀，尚未执行到两者有关键差异的 debug_init 执行范围配置。** 因此这不能区分旧 r2 和修复镜像是否能完整启动，也不证明此前失败只有一个原因。旧 r2 仍禁止重刷，修复候选仍未刷机。已有的[函数级修复检查](eq-fence-repair.md)继续保留，但不能替代整机验证。

## 后续已定位的缺口

当前 SDTAP.CON bit0 决定是否读取 IOMAP.CON0 `[18:17]`；通道号影响后续 USB DP／DM 引脚配置，是活跃输入，不能用死值投影跳过。再往后，PINR_CON bit0 决定是否保留复位引脚，PSRAM_CON0 bit1 决定是否保留四个 PSRAM 引脚。尚未取得这些真实初态。

静态程序还定位了后续 CPU1 启动：将 `0x12caf4` 写入 `0x19fef8`，置 `0xffe8c0` bit0，再等待 CPU1 实际设置 `0x1265f5`。CPU1 经 RTI、ROM I/D cache 初始化后进入自身主函数。不会通过预置 ready 来绕过它。

首任务切换使用 76B 的 RTOS 帧，与实际 Timer C ISR 的 28B 软件保存不同。栈初始化函数只明确写 PC、RETS、PSR、pdata，其他 GPR 槽保持未知；需要执行真实调度，不能强行跳到 app_core。架构级 IRQ／RTI、双核运行、后续外设和蓝牙音频仍是整机仿真的必要工作。

## 本机复现

```bash
python3 tools/fetch_boot_reference.py --hardware --decode-ir
python3 tools/analyze_memory_reference.py
python3 tools/test_br28_model.py
python3 tools/test_boot_bus.py
python3 tools/analyze_startup_chain.py --report analysis/startup-chain.json
python3 tools/check_local.py --rom /private/path/rom.bin

# 可选：两个精确历史镜像的条件启动前缀；不会刷机
python3 tools/probe_arch_startup.py --rom /private/path/rom.bin \
  --repaired-candidate /private/path/repaired.ufw \
  --failed-r2 /private/path/failed-r2.ufw \
  --report analysis/architecture-candidates.json
```

默认检查不需要私有 ROM；提供 `--rom` 才运行真实 ROM 启动回放。公开结果仅保留摘要、有限指令／内存事件及小段合成状态；原始 ROM、设备 Flash、完整 RAM 和研究反汇编仍留在忽略目录。

本轮 `check_local.py --rom …` 全部通过，包括30项外设测试、11项总线／指令测试和全部新旧探针。原 boot、handoff、9组ROM、13组硬件及12组GPIO报告逐字节一致；MMU／P11旧报告更新了翻译说明，冷MMU场景新增适配器计数但执行边界不变。两个精确候选另经同一架构前缀检查，错误步数／PC／异常停止／缺失里程碑的变体均被拒绝。固定SDK提取也已在本机重现。
