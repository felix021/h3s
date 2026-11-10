# 补齐 BR28 外设信息与启动模型

后续：[GPIO和真实QCY ISR探索](gpio-irq.md)推进到MMU边界；本页保留上一轮13组结果。普通IRQ全局使能筛选已修正为ICFG bits8与9同时开启。

2026-11-10，在[真实ROM执行](rom-execution.md)的基础上，从固定SDK头文件、库中LLVM IR、官方BR28 bootloader和既有指令硬件结果补齐外设定义，并实现受限行为模型。**真实`_pll_init`现在能完成两次定时等待并返回；冷、暖两条连续应用入口路径都执行了ROM `mask_init`及6次Timer IRQ关闭，随后停在GPIO初值读取。** 没有操作耳机、写Flash或生成新固件，检查全部在本机执行。

工具：[br28_model.py](../tools/br28_model.py)、[probe_hardware.py](../tools/probe_hardware.py)、[test_br28_model.py](../tools/test_br28_model.py)。结果：[hardware-execution.json](../evidence/hardware-execution.json)。模型是可检查的外设子集，尚未完成整机启动，也未验证实机周期、模拟电路或中断进入／返回。

## 信息来源

| 材料 | 固定位置／用途 |
| --- | --- |
| 同一JL701N参考SDK | `67328f4741c69993c88e504c67377b196c724f97`；不是QCY确切源码 |
| `asm/gptimer_hw.h`、`cpu.a/gptimer_hw_v2.c.o` | Timer寄存器、源选择、分频、pending清除及周期语义 |
| `asm/power/p33/p33_sfr.h`、`cpu.a/p33_io_pinr_v1.c.o` | P33地址映射及引脚复位配置位 |
| `asm/csfr.h`、`asm/cpu.h`、`asm/hwi.h`、`system.a/hwi.c.o` | IRQ配置打包、IPMASK阈值、实际使能与优先级操作 |
| [杰理官方BR28 delay.c](https://github.com/Jieli-Tech/fw-Bootloader/blob/ff3b9299d1b94d834b995fd858b3f75e2faa9840/user_boot/cpu/br28/delay.c) | 独立源码交叉核对Timer0启停、计数、bit15轮询及bit14清除 |
| [固定pi32v2 ISA表](https://github.com/jvitkauskas/pi32v2-isa/blob/3608ed19bc21324f8ecfe79cc0018d05e43d9563/isa/pi32v2.toml) | RMW总线读写、寄存器／flags保持、共享锁AC791N双核测量边界 |
| 私有H3S ROM及精确官方UFW | 实际指令、地址、分支和结果；原始ROM继续保留私有 |

新增文件及提取对象的SHA256保存在[data/dependencies.json](../data/dependencies.json)。SDK／对象／IR与下载的官方参考源码仅放在忽略的缓存里，没有复制整套SDK或第三方模型进入本仓库。公开实现依据寄存器定义和行为证据独立编写。

## `0xf2c11c`已经确认是什么

它是 **`P3_PINR_CON1`**，不是未知的普通RAM位置。固定SDK的[定义](https://gitlab.zh-jieli.com/ai_test/701n/-/blob/67328f4741c69993c88e504c67377b196c724f97/SDK/interface/driver/cpu/br28/asm/power/p33/p33_sfr.h)给出：

```text
P33_ACCESS(x) = *(volatile u32 *)(0xf20000 + 0xc000 + x*4)
P3_PINR_CON1 = P33_ACCESS(0x47) = 0xf2c11c
```

`gpio_longpress_pin1_reset_config`的实际IR写入同一地址，配置公式为`(time_code<<4) | (level<<2) | (release<<1) | enable`，另一引脚选择额外设置bit3。SDK的1／2／4／8／16秒分别对应time_code0／1／2／3／4。因此QCY启动时的`0 → 0x17`序列，按此契约是先关闭，再配置使能、level=1、release=1、time_code=1（2秒），bit3=0。SDK此分支的引脚参数为229；没有据此猜测H3S的物理按钮接线。

地址和配置位已有依据，**真实引脚电平和外部复位事件仍没有现场数据**。模型要求调用者显式选择`reset_inputs_inactive=True`才允许这段配置；否则停在`unmodeled_pin_reset_input`。这是“配置寄存器投影＋无外部触发”的场景，不是模拟真实按压、电气去抖或复位后的整芯片状态。

## Timer0：计数、pending和清除

寄存器为CON=`0xfd0400`、CNT=`+4`、PRD=`+8`。本轮实现以下子集：

- CON bits0～1=0时停止，=1时普通计时；捕获、PWM及其他未实现模式拒绝。
- bit15为pending状态；bit14执行清pending命令，不保留为普通可读配置位。把读出的pending位再写回不会人为制造pending，常见`CON |= BIT(14)`可以正确清除。
- 时钟源在bits10～13；固定头文件确认4为RC16M，ROM使用此源。模型只允许该源的运行模式。
- 分频在bits4～7，编码0～15分别为`1,4,16,64,2,8,32,128,256,1024,4096,16384,512,2048,8192,32768`。ROM的`0x1011`即RC16M／4、普通计时。
- 周期采用`PRD+1`：SDK的`GPTIMER_SYS_SET_PRD(x)`明确写`x-1`。计数达到周期后回绕并置pending；清pending和停止不会伪造一个新的完成事件。

虚拟源tick在**完成一轮CPU模型step之后**推进，读取寄存器本身不会使时间前进。没有“读几次就就绪”的捷径。单位测试先重复读取100次，确认pending不变，再推进到周期边界核对置位和清除。

目前不知道真实CPU周期与RC源tick的关系，所以分别测试每step推进1／4／17个源tick，PLL函数都返回且配置相同；源tick为0时始终停在真实轮询循环，400步预算耗尽，pending没有置位。不能将这三种虚拟进展率称为实机时间测量。

也不能仅从函数名`udelay`推算时钟：官方bootloader的RC延时按16 tick/us配置，参考SDK里另一个辅助函数采用8，而本机ROM辅助函数用了乘5及额外分频。这些不是同一段实现，尚无校准测量；模型保留tick语义，不宣称精确微秒。

## PLL与时钟：真实代码返回，模拟电路仍有边界

PLL0的CON0～CON4已经按固定地址提供RW配置，时钟选择覆盖CLK_CON0/1/2。ROM真实写入、RMW、Timer0等待、CPU倒计数和两条`csync`均由指令执行器运行，没有调用主机PLL初始化函数替代ROM。

24MHz输入以及显式给定的三个时钟寄存器初值0时，独立核对结果：

| 寄存器 | 结果 |
| --- | --- |
| PLL0.CON0／CON1／CON2／CON3／CON4 | `40892d93 / 80a / 5e / 31f / 0` |
| CLK_CON0／CLK_CON1／CLK_CON2 | `30 / 643 / 0` |
| ROM `boot_arg_list + 8` | `48000000` |
| Timer0 | 两次周期完成，随后清pending并停止 |

这验证的是配置和程序控制流。**没有实现PLL模拟电路、锁定时间、真实时钟树传播、缓存和总线周期精度。** 冷路径最早对PLL0.CON0的0值也是显式初态。三个时钟配置寄存器未给初值时，模型确实在ROM `0xc322`读`0xfd0018`时停止，不偷偷补0；电路初态不能从“程序需要一个值”反推出来。

隔离调用时故意把`boot_arg_list + 8`初值设为123，验证真实ROM存储把它改为48000000，避免预先填入期望结果而得到表面通过。

## IRQ与双核锁

IRQ定义与实际IR已补齐到以下程度：

- Timer0的IRQ号为4。CPU0配置字从`0xffe100`开始；IRQ n使用第`n//8`字的第`4*(n%8)`位起的nibble，bit0使能，bits1～3为优先级。
- `bit_clr_ie`只清该nibble的bit0；`reg_set_ip`只替换优先级字段；`request_irq`替换整个nibble并使能。头文件注释说0～6可用，但这份实际库把大于5的普通请求降为5；`irq_unmask_set`另走6／7的特殊路径，模型尚不实现GIEMASK／不可屏蔽中断。
- IPMASK=`0xffe1a8`。固定`cpu.h`明确写“< N的优先级不可以响应”；普通优先级低于阈值不交付。6屏蔽0～5，0恢复普通响应。CLI／STI的可见ICFG bit9模型沿用既有AC791N测量及SDK用法，未新测BR28硬件。
- 已实现pending／enable／优先级／全局使能筛选。若Timer0已到期并满足交付条件，**停在`interrupt_delivery_not_implemented`**，不假装handler已经调用或中断已经消费。缺IRQ配置初值、IPMASK或遇特殊优先级也会停止。
- `lockset/lockclr`现在可选建模为CPU0获取／释放共享锁；初始所有者必须显式给出。未知所有者、其他核占有、递归获取、非所有者释放均停止。固定ISA的AC791N双核实测支持共享排他；BR28实际SDK也在最外层临界区成对使用。没有模拟CPU1并发、锁公平性或隐式内存排序。

连续启动用例实际执行了6次Timer IRQ关闭，每次都有IPMASK `6 → 0`和共享锁获取／释放。IRQ号4～7落在第一个配置字，8～9落在第二个字：6次总线写入全部核对。默认fixture给32个IRQ配置字初值0；不提供这些初值时，模型在第一次`0xffe100`读取处停止。SDK `interrupt_init`会清32字是来源证据，但我们没有执行H3S真实bootloader，不能把fixture宣称为现场初态。

另外检查了固定`hwi.S.o`：参考IRQ4 veneer保存16个GPR及PSR／RETS／RETI，共76B，调用公共分派后恢复并`rti`。按22B桩中18个非重定位字节搜索QCY应用未找到匹配，**没有把参考栈布局当成H3S确切IRQ ABI**。CPU异常进入、SSP切换、优先级嵌套和中断返回仍需沿真实QCY分派与硬件资料核实。

这项匹配由[analyze_irq_reference.py](../tools/analyze_irq_reference.py)复现，结果见[irq-reference.json](../evidence/irq-reference.json)；只比较非重定位字节，不把未解析的`call_irq`重定位值当成有效调用地址。

## 修正宿主模拟器的两类总线差异

上游面向测试scratch RAM的模型有时会先读旧word来合并存储，并省略值未变的写入。对MMIO这两点会改变行为：完整word存储不应凭空要求旧值；即使数据相同，也可能必须产生总线写或命令副作用。

新外设模式中，已覆盖的完整word存储和4种实际RMW形式按真实地址／操作数执行；RMW保留GPR与flags，并**始终写回**。未启用外设模型的旧探针保持不变。ROM `0xa30c`的并行指令也单独核对：给R0装入新CON值，同时PRD存储必须读取配对前的旧R0，否则会把控制字当成周期。遇未覆盖的条件块形式继续停止。

## 十三组场景与当前停止点

| 场景 | 模型step | 结果 |
| --- | ---: | --- |
| PLL时钟寄存器初值未知 | 894 | `0xc322`读CLK_CON0时停止 |
| PLL，虚拟源tick1／4／17 | 3591／1033／383 | 三组均返回，配置相同，两次Timer完成 |
| PLL，虚拟源tick0 | 400 | 在Timer轮询处耗尽预算，未返回 |
| 暖路径，复位引脚事件未知 | 17248 | 在P3_PINR_CON1配置处停止 |
| 暖路径，明确无外部复位事件 | 17329 | 进入真实ROM mask_init，后停严格lockset |
| 同上，IRQ初值未知、仍不建模锁 | 17329 | 同一严格lockset位置 |
| 冷路径仅验证应用clock函数返回 | 1047 | 到`0x06000116`里程碑，尚未继续交接 |
| 冷连续路径，不建模锁 | 18367 | 到同一严格lockset位置 |
| 暖连续路径，共享锁模型 | 17789 | 完成6次IRQ关闭，停PORTB.DIR |
| 共享锁模型，但IRQ初值未知 | 17332 | 第一次读`0xffe100`停止 |
| 冷连续路径，共享锁模型 | 18827 | 完成ROM PLL、mask_init及6次IRQ关闭，停PORTB.DIR |

新边界为应用PC **`0x06084f72`**，读取 **`JL_PORTB.DIR = 0xfd5108`**，准备RMW设置端口方向。名称来自`br28.h`的PORTB基址与结构偏移；未知的是寄存器初态及后续引脚环境。本轮没有把全部GPIO都初始化为0来继续。之前的零计数REP、ICFG、交接对象和保留RAM仍按明确的旧假设运行，这不是从真实复位现场启动。

## 本机复现

```bash
# 固定SDK和官方参考源码；完整对象／IR仍只写入忽略目录
python3 tools/fetch_boot_reference.py --hardware --decode-ir \
  --output-dir analysis/hardware-reference
python3 tools/test_br28_model.py
python3 tools/analyze_irq_reference.py \
  --object analysis/hardware-reference/hwi.S.o \
  --report analysis/hardware-reference/irq-reference.json

# 需要操作者显式提供私有ROM，工具不会下载或公开它
python3 tools/probe_hardware.py \
  --rom /private/path/rom-000000-020000.pass1.bin \
  --report analysis/hardware-execution/results.json
python3 tools/check_local.py \
  --rom /private/path/rom-000000-020000.pass1.bin
```

默认本机检查不需要私有ROM，新增8个外设约束测试；带`--rom`时另运行原9组ROM及本轮13组场景。报告保留初态假设、实际副作用计数、有限MMIO写入和末尾少量指令，不发布完整ROM、私有路径或全RAM镜像。原三轮启动边界和ROM报告保持可复现，GitHub CI没有运行。
