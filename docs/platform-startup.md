# 连续启动到 CPU1 释放：实际保护配置与任务栈

本页记录释放前的严格基线；更新的147,263步strict基线及147,268步任务RTI边界见[双核条件启动](dualcore-startup.md)。

2026-11-11，本机条件仿真从应用入口连续执行 **127,239 步**，停在 `0x060660da` 设置 CPU1 释放位之前。真实端口初始化、复位／唤醒历史记录、`debug_init`、MPU 配置和 `app_core` 任务创建均已执行；`os_start` 已进入，但尚未切入任务或运行 CPU1。没有访问设备，没有新刷机，没有 GitHub CI。

另一次**隔离**回放执行真实 `debug_init` 的126步前缀，再以同一硬件模型保存的实际范围值执行钩子：旧 r2 在 `0x060e3092` 被软件范围策略拒绝，修复候选能执行第一条载荷指令。它比直接给探针指定范围更接近启动中的实际配置，但不等于完整启动、EQ开关或实机故障已经修复。

| 工具 | 证据 | 范围 |
| --- | --- | --- |
| [probe_platform_startup.py](../tools/probe_platform_startup.py) | [platform-startup.json](../evidence/platform-startup.json) | 16组连续启动条件／严格反例；可选两个精确候选前缀 |
| [probe_debug_startup.py](../tools/probe_debug_startup.py) | [debug-startup.json](../evidence/debug-startup.json) | 真实配置前缀与另行进入的钩子；不需要私有ROM |
| [analyze_cpu_context.py](../tools/analyze_cpu_context.py) | [cpu-context.json](../evidence/cpu-context.json) | 实际QCY trampoline／任务帧，以及固定AC791N测量的局部依据 |

## 端口输入会改变真实配置

沿用[上一阶段](clock-rtos-startup.md)的显式时钟、OTP、缓存、PMU状态等测试输入，另外提供 SDTAP、PSRAM、IOMAP 与 USB IO 初态。它们都是虚拟板级条件，尚未测量真实耳机值。

- `SDTAP.CON (0xff0100)` bit0 开启时，固件读取 `IOMAP.CON0 (0xfd6014)[18:17]`。通道1保留USB调试引脚，跳过其配置；该分支不要求提供USB IO初态。
- `PSRAM_CON0 (0xfe0300)` bit1开启时，保留四个PSRAM引脚。`IOMAP.CON0` bit16还决定Flash端口选择。
- 普通路径先将 `USB.CON0/CON1 (0xfd1800/04)` 写0。模型仅支持这两个关闭命令，读取状态或尝试开启继续停止，没有模拟USB传输。
- `USB_IO.CON0 (0xfd6000)` 用于真实读改写。未提供初态时，先前OR操作只建立 mask `0x5000`，后续完整读取仍停止；不会把未知邻位补零。正例明确给出完整32位初态0，并保留后续真实写入。
- `P11_PORT.PB_DIE/PB_DIEH (0xf2b21c/20)` 由实际 `p11_io_init` 写0；只增加这两个配置地址，不开放整个P11地址空间。

实际栈上 `port_keep[7]` 在首次配置GPIO前被观察，避免读取数组生命周期结束后已被复用的栈内容：

| 条件 | 七个16位掩码 |
| --- | --- |
| 默认虚拟输入 | `ffff ffff ffff ffe0 ffff ffff ffff` |
| PSRAM开启 | `ffff ffff ffff ffe0 ffd8 ffff ffff` |
| 另一Flash端口 | `ffff ffff ffff ffec ffff ffff ffe7` |

普通路径实际115,221步返回 `0x060352f0`；USB调试通道分支114,529步；另两条115,224步。缺少每种输入都有独立严格停止场景。

## 复位历史不是寄存器默认值

`0x06068c80` 先记录复位原因，再记录唤醒原因，并不是等待复位硬件就绪。固定 SDK `reset.c.o`、`power_reset.c.o`、`power_reset.h` 与实际QCY指令用于交叉核对；SDK唤醒函数和QCY并非逐字一致，模型以实际指令为准。

正例明确选择三个域的POR历史：

| 输入 | 完整32位测试值 | 被消费的含义 |
| --- | --- | --- |
| MSYS `PWR_CON/RST_SRC`，`fd0004/fd0000` | `80 / 02` | 复位标志、DVDD POR |
| P11 `PWR_CON/RST_SRC`，`f2a400/f2a404` | `80 / 01` | 复位标志、PVDD POR |
| P33 `PMU_CON0/RST_SRC`，`f2c0c0/f2c048` | `80 / 01` | 复位标志、VDDIO POR |

这些值的其他位也**显式选择为零**，不是从最小有效位推出的真实硬件初值。三个bit7全零在SDK中表示“该域未被复位”，不能称作上电。POR历史和应用入口是否跳过PLL重设是两种不同条件。

清标志后，固件才读取原因：模型保留原因快照，不能同步将 `RST_SRC` 清空。清除命令位自身读回及延迟尚未确认，随后再次读取该标志寄存器会停止。

三个配置操作无需完整初态，按实际AND保留未知邻位：

```text
P11_SYS_CON1 f2a628 &= ffffffef   # M2P_RESET_MASK清bit4
P3_RST_CON0  f2c054 &= 00003fdf   # 不猜bit5的硬件名称
P3_PCNT_SET0 f2c128 &= 0000ff00   # 软件复位标记暂存
```

唤醒历史独立选择为无事件：P2M pending、数字／模拟pending、P3与RTC source快照均提供显式完整零字。固件实际读取和写入清除命令，模型没有自动由POR推导“没有唤醒”。数字／P2M clear仅清选中的已知pending位；没有事件生成或时序模型。RTC `R3_WKUP_CPND=f2d248` 与 `R3_WKUP_SRC=f2d2a0` 是不同寄存器，不能假定清除命令会擦掉source；当前只支持其零值无操作，非零映射继续停止。

另一个P33辅助函数的返回值也证实未使用：R0保持opaque，经过实际返回、调用和配置AND，直到 `0x06068c8c` 的立即数覆盖。检查只允许已验证的具体指令序列；三处改成消费R0的变体都在消费前拒绝。

## 用真实写入控制取指范围

实际 `debug_init` 写入：

| 配置 | CPU0实际值 |
| --- | --- |
| `EMU_CON` | `e0000007 → e000000f → e000001f` |
| `ETM_CON` | `1` |
| SSP下／上限 | `122840 / 123840` |
| ROM PC下／上限 | `4 / 20000` |
| RAM PC下／上限 | `12a640 / 1344c6` |
| 官方／r2 Flash PC下／上限 | `06000100 / 060c3338` |
| 修复候选Flash PC上限 | `060e31c5`，原始奇数值保留 |

模型跟随实际 `EMU_CON` bit4使能，在执行循环检查三个范围的并集；已有解码缓存不能绕过新配置，parallel配对检查完整长度，普通数据读取不受PC范围限制。非执行的解码前瞻不作为执行取指检查。

这是显式的**半开区间、整条指令覆盖**策略，尚未认证BR28端点、低位处理或虚拟／物理PC比较规则。启用保护后的虚拟PC取指仍严格停止。越界是终止诊断，不伪造异常状态、入口或复位。

CPU0最初EMU控制0是外部fixture，缺失时在入口第0步停止；CPU1控制仍未知，没有继承CPU0状态。SSP／USP模式与边界、其他EMU/ETM位副作用尚未执行检查，报告明确保留这些缺口。

## MPU配置已由真实解析程序完成

实际两个MPU写门函数都写key `e7`，分别轮询bit0直到开启／关闭。可选模型要求显式初态0，采用同步toggle、上位读回0的受限投影；它不是已测硅片确认。初态未知有独立反例。

真实 `mpu_set(0, 12a640, 1344c5, "Cxrw0rw1rw", a3, a4)` 解析后得到：

```text
ff0300 CON = 00060607
ff0340 CID = 0000a4a3
ff0380 BEG = 0012a640
ff03c0 END = 001344c5
```

仅选中slot的四个字先清零后写入，其他slot仍未知；共两次key写，最后门关闭。这里的MPU END是调用者给的末字节地址，与LIM_PC原始上限不可混用。目前只模拟配置和写门，**未执行CPU／DMA访问权限检查**。

## 本阶段实际任务已创建，CPU1仍未执行

`task_create` 返回0。实际 `OSTaskStkInit` 构造的app_core帧为虚拟 `0x400bb0`，本模型CPU RAM backing `0x19f7b0`：前16B依次为任务入口 `0x06012028`、RETS=0、PSR=0、pdata=0；后60B的R1–R15槽仍完全未知。

新增完整双字存储只直接写实际8B，避免上游为了合并store读取旧内存；支持实际固件中出现的4B对齐。没有把整个栈补零。帧构造证明任务创建，不证明已调度或进入应用。

CPU0已经真实写入 `0x19fef8=0x12caf4`，将CPU1 ready字节 `0x1265f5` 清0，然后在 `0xffe8c0` bit0释放指令前停止。两个精确候选都通过该相同前缀，并分别核对其实际保护上限、MPU值、任务返回值、unknown栈槽、取指检查计数及opaque轨迹。

此阶段保留释放前strict边界。后续[双核条件回放](dualcore-startup.md)已用共享RAM／MMIO、独立CPU状态执行真实ROM、trampoline、缓存等待及ready写入，保留首任务未知GPR读取的strict基线，并在显式UnknownWord搬运下推进到任务RTI前；其中reset向量、bootstrap RTI和缓存进度均是显式未认证投影。

静态研究补查了固定AC791N app4的9次测量：整个启动序列结束时SP等于USP，而与先前写入的SSP不同；另一RTI测量则保持SP。采样前还执行过CLI和ICFG修改，因此两组都不能单独推出通用RTI规则。QCY trampoline只显式设置USP=SP=`124840`、RETI=`12cad4`，尚未确定其初始SSP／ICFG／模式。首任务恢复另有76B软件帧，不能套用Timer ISR的28B保存或将RTI改成goto。

## 本机复现与实际限制

```bash
python3 tools/fetch_boot_reference.py --hardware --decode-ir
python3 tools/analyze_cpu_context.py --report analysis/cpu-context.json
python3 tools/probe_debug_startup.py --report analysis/debug-startup.json
python3 tools/check_local.py --rom /private/path/rom.bin

# 可选精确候选前缀，以及独立的实际保护配置／钩子对照
python3 tools/probe_platform_startup.py --rom /private/path/rom.bin \
  --repaired-candidate /private/path/repaired.ufw --failed-r2 /private/path/r2.ufw \
  --report analysis/platform-candidates.json
python3 tools/probe_debug_startup.py \
  --repaired-candidate /private/path/repaired.ufw --failed-r2 /private/path/r2.ufw \
  --report analysis/debug-candidates.json
```

41项外设与16项总线／指令测试覆盖未知输入、状态保留、RTC来源分离、取指缓存／parallel长度／数据读取、任务栈未写区域和opaque消费者反例。连续前缀检查也拒绝移除取指检查、伪造成功任务返回、缺失opaque轨迹和异常早停的报告变体。

完整本机回归、两个精确候选前缀、隔离保护对照、固定SDK提取与CPU上下文静态检查均通过。原boot、handoff、ROM、硬件、GPIO、MMU、P11状态、看门狗和时钟报告逐字节一致；原架构场景也与此前保留结果一致。公开证据仅保留有限事件、掩码、摘要和小段合成状态。

本页的127,239步profile仍不运行CPU1；后续双核结果另见[双核启动](dualcore-startup.md)。架构级IRQ／任务RTI、完整MPU／DMA、P11执行及蓝牙音频仍未完成。旧r2仍禁止重刷，修复候选仍未刷入；隔离范围拒绝与函数测试均不代替整机验证。原始ROM、Flash、完整RAM及研究反汇编不发布。
