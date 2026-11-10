# GPIO、真实 QCY Timer ISR 与新的 MMU 边界

2026-11-10，本机离线推进连续应用入口：暖路径执行73,436个模型step，冷路径74,474个，均停在`PC 0x060350e8`向`0xff0404`写入页表起址`0x120000`。没有连接设备或生成固件，尚未完成整机启动。前置交接初态、REP、虚拟tick等假设仍见[外设模型](hardware-contracts.md)和[ROM执行](rom-execution.md)。

工具：[probe_gpio_irq.py](../tools/probe_gpio_irq.py)、[analyze_qcy_irq.py](../tools/analyze_qcy_irq.py)。公开结果：[gpio-irq-execution.json](../evidence/gpio-irq-execution.json)、[qcy-irq.json](../evidence/qcy-irq.json)。这些结果不足以批准刷机；失败的EQ r2继续禁止使用。

## GPIO 未知位没有补零

真实代码通过RMW操作PB1。模型以`known_value / known_mask`跟踪每个位；OR使对应置1位已知，AND使对应清0位已知，其余位保留未知。启动到MMU边界时，PORTB的DIR、DIE、PU、PD、DIEH均只有bit1已知为1（`value=2, mask=2`），其余31位未知。完整读取仍拒绝，IN也不接受写入。单位测试枚举256种8位初态，独立核对抽象结果。

后续`P3_PORT_SEL0=0xf2c300`读取的返回值会被调用者立即覆盖。投影严格检查：读取地址和指令、栈中返回地址`0x0603502c`、中间唯一pop以及返回后`R0=848`指令。模型让R0保持`unknown`，直到实际覆盖；任何其他消费者都拒绝。三个预算用例分别停在读后、返回后、覆盖后，证明没有给未知值填0。此配置寄存器投影不证明真实外部引脚电平，也不允许泛用跳过未知读取。

## 找到了真实 QCY ISR

从官方应用扫描寄存器保存序列及六项指针表，唯一表位于`0x060a2508`。Timer0～5（IRQ4～9）的RAM入口分别是：

```text
12ece8 12ed0e 12ed34 12ed5a 12ed80 12eda6
```

Timer0的实际软件保存帧为28B：PSR／RETS／RETI共12B，R0～R3共16B。它调用真实临界区代码、向Timer0.CON写bit14清pending，再查`0x126f6c`回调表，恢复寄存器，最后`csync; rti`。这里按RAM执行地址解码相对调用；用SFC镜像地址计算会得到错误目标。它与此前参考SDK的76B公共IRQ veneer不同。

隔离测试从明确给定的中断进入后上下文出发。空回调执行62步，指向实际QCY `rts`函数的回调执行64步；均清pending，恢复全部16个GPR、SP、PSR投影、RETS和RETI，随后严格停在`rti`前。无效回调在未映射取指处拒绝。**没有模拟CPU硬件中断进入、SSP切换、嵌套或RTI**，也没有将隔离测试冒充连续启动中发生的中断。

依据固定[app2硬件记录](https://github.com/jvitkauskas/pi32v2-isa/blob/3608ed19bc21324f8ecfe79cc0018d05e43d9563/hw/fm1-2026-10-24-app2/README.md)和[app3记录](https://github.com/jvitkauskas/pi32v2-isa/blob/3608ed19bc21324f8ecfe79cc0018d05e43d9563/hw/fm1-2026-10-24-app3/README.md)，普通IRQ交付要求ICFG bits8和9同时开启；更新了原先仅检查bit9的筛选。依据[report5-k](https://github.com/jvitkauskas/pi32v2-isa/blob/3608ed19bc21324f8ecfe79cc0018d05e43d9563/hw/report5-k.md)，push PSR保存后清低flags，特殊寄存器按RETI／RETS／PSR次序入栈。来源是AC791N测量，未宣称已在BR28重测。

## 本机复现及下一步

```bash
python3 tools/analyze_qcy_irq.py --report analysis/qcy-irq.json
python3 tools/probe_gpio_irq.py --rom /private/path/rom.bin \
  --report analysis/gpio-irq-execution.json
python3 tools/check_local.py --rom /private/path/rom.bin
```

本机全检查通过，11个外设单位测试、原9组ROM和13组硬件场景、新11组GPIO／ISR场景通过；旧启动／交接／ROM／硬件报告逐字节一致。未运行GitHub Actions。原始ROM、完整SDK、运行日志继续留在忽略目录。

下一段是MMU页表及物理／虚拟内存、RTOS启动和实际中断交付，然后才能把失败EQ镜像的修复带入连续启动验证。`0xff0404`是明确停止点，不能仅增加一个可读写寄存器就宣称MMU已经实现。
