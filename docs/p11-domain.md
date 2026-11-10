# P11 域：已拿到固件，访问握手仍待核实

当前连续主核启动的严格边界是`0x060688f8`写`0xf2af10`。固定SDK及真实QCY代码已确认发送侧序列，但还没有PMU状态位的完整硬件定义。本轮由gpt-6-astra协助独立查证，全部离线，没有连接设备。

## 两条不同阶段的调用链

固定SDK [p11_sfr.h](https://gitlab.zh-jieli.com/ai_test/701n/-/blob/67328f4741c69993c88e504c67377b196c724f97/SDK/interface/driver/cpu/br28/asm/power/p11_sfr.h)定义P11主核窗口基址`0xf2a000`；UART0基址`+0xf00`、CON2偏移`0x10`，所以当前写入是`P11_UART0.CON2`。`p11_init`向它发送`ff ee ab 0e 55`并轮询`JL_PMU.PMU_STA=0xfd3e04`的bit0。

同一`power_driver.c.o`的`low_power_schedule`则向`P11_WDT.DUMMY=0xf2a808`写同样序列，再轮询同一状态位。这支持“通用域访问同步／硬件握手”的推测，不能据此宣称已经理解UART协议，或由P11应用软件回应。公开[AC701N PMU手册](https://doc.zh-jieli.com/KDocs/master/屏融合应用方案入口汇总/AC701N_PMU_介绍.html)说明P11／P33构成独立于主系统的PMU，但没有给出这一个状态位的名字、复位值、置位／清除机制或延迟。

更晚的`power_init`才设置P11时钟，打开`flash/res/p11_code.bin`，把公共段复制到主核窗口`0xf20080`，设置P11 CLOCK.PWR_CON bit5和q32s PMU_CON bit0，等待PWR_CON bit5清除，再读取堆信息。这是另一阶段，不能把当前早期握手和固件装载启动混为一谈。

## QCY 自己的 P11 固件已经在手

工具[analyze_p11.py](../tools/analyze_p11.py)直接从精确官方UFW提取资源位置、解析BANKCB表并校验，不需要私有ROM或SDK。公开摘要见[p11-resource.json](../evidence/p11-resource.json)。

| 项目 | 结果 |
| --- | --- |
| JLFS头，SFC明文坐标 | `0xdd8bc` |
| 文件 | `p11_code.bin`，12,248B |
| SHA256 | `1fe36008118168d2178c0cb3aeec265fc78e47ce70cd652e633236beb8270882` |
| BANKCB头 | 7个，每个16B，`u16 bankNum,size; u32 bankaddr,addr; u16 crc,crc1` |
| 公共段 | 文件偏移`0x70`，长度12,112B，地址字段`0x80` |
| 公共段SHA256 | `6c3b388928ae3b95c970308724c2196b497a5625a93640cc85e4c3a91ce7d6e6` |
| 其他6项 | 各4B，地址字段`0xaa55aa55`，含义未全部解释 |
| 校验 | 7个头前14B CRC及7个数据CRC均通过CCITT(init0) |

地址字段与SDK装载流程支持P11局部`0x80`／主核`0xf20080`映射，不能单独证明复位PC。固定SDK另带的P11固件与QCY版本不同，不可直接替换。

[p11_csfr.h](https://gitlab.zh-jieli.com/ai_test/701n/-/blob/67328f4741c69993c88e504c67377b196c724f97/SDK/interface/driver/cpu/br28/asm/power/p11_csfr.h)明确说明P11是**q32small**，不是现有pi32v2 CPU模型。[固定ghidra-jieli](https://github.com/quarkslab/ghidra-jieli/tree/e1bd0707874b77b759401555d24839ad43af1267)有早期q32s解码支持，但README列出Group7、flags、stack等缺口；不能当作完整可执行模拟器。主核128 KiB ROM窗口也未证明包含P11 ROM。

## 有界条件探索，没有制造回应

[probe_p11_status.py](../tools/probe_p11_status.py)额外提供显式外部PMU状态输入；[p11-status.json](../evidence/p11-status.json)保留三个分支。该选项默认关闭，**不是P11硬件回应模型**：

- 未知：记录真实代码的五次写后，读取未知PMU状态时停止。
- 固定0：重复发送、读取、分支，85,000步预算结束仍没有ready；写入绝不自动把状态改成1。
- 固定1：明确以状态寄存器为1作为外部假设，从真实轮询返回，暖路径83,094步到`0x0600a57c`，在向`P11_WDT.KEY=0xf2a804`写`0x048c159d`前停止。

状态0／1是整word fixture，均非测得现场初态。写入接受范围仅为此精确序列，未知寄存器／错误顺序仍停止。没有执行P11、伪造响应因果、响应时间、watchdog、时钟依赖或跨域副作用；不能用条件1的结果证明严格整机启动已通过。

```bash
python3 tools/analyze_p11.py --report analysis/p11-resource.json
python3 tools/probe_p11_status.py --rom /private/path/rom.bin \
  --report analysis/p11-status.json
```

下一步可沿真实P11公共段核对入口、外部调用与清PWR_CON bit5的代码，确定P11是否另依赖ROM；主核侧则需继续查PMU握手定义和watchdog命令。普通IRQ entry／RTI也仍缺BR28完整契约，不能用恢复整个旧ICFG的主机影子状态代替：AC791N已有反例显示RTI后高位并不完整恢复。
