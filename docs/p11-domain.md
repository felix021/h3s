# P11 域：已拿到固件，访问握手仍待核实

当前连续主核启动的严格边界是`0x060688f8`写`0xf2af10`。固定SDK及真实QCY代码已确认发送侧序列，但还没有PMU状态位的完整硬件定义。本轮由gpt-6-astra协助独立查证，全部离线，没有连接设备。

后续[条件启动回放](clock-rtos-startup.md)已在明确的PMU状态输入下运行真实看门狗、OTP、时钟和OS初始化，推进至端口状态读取。本页的严格P11边界及状态未知／0／1反例仍保留；新的主核进展不证明P11响应因果或q32s执行已经完成。

## 两条不同阶段的调用链

固定SDK [p11_sfr.h](https://gitlab.zh-jieli.com/ai_test/701n/-/blob/67328f4741c69993c88e504c67377b196c724f97/SDK/interface/driver/cpu/br28/asm/power/p11_sfr.h)定义P11主核窗口基址`0xf2a000`；UART0基址`+0xf00`、CON2偏移`0x10`，所以当前写入是`P11_UART0.CON2`。`p11_init`向它发送`ff ee ab 0e 55`并轮询`JL_PMU.PMU_STA=0xfd3e04`的bit0。

同一`power_driver.c.o`的`low_power_schedule`则向`P11_WDT.DUMMY=0xf2a808`写同样序列，再轮询同一状态位。这支持“通用域访问同步／硬件握手”的推测，不能据此宣称已经理解UART协议，或由P11应用软件回应。公开[AC701N PMU手册](https://doc.zh-jieli.com/KDocs/master/屏融合应用方案入口汇总/AC701N_PMU_介绍.html)说明P11／P33构成独立于主系统的PMU，但没有给出这一个状态位的名字、复位值、置位／清除机制或延迟。

更晚的`power_init`才设置P11时钟，打开`flash/res/p11_code.bin`，把公共段复制到主核窗口`0xf20080`，设置P11 CLOCK.PWR_CON bit5和q32s PMU_CON bit0，等待PWR_CON bit5清除，再读取堆信息。这是另一阶段，不能把当前早期握手和固件装载启动混为一谈。堆信息4字节（P2M消息区`+0xa`，`0xf27f4a..4d`）已从公共段静态推导出候选值：begin=`0x3d0c`、size=`0x39f4`（`0xa12/0xa26`宽立即数`c0ff 0c3d0000`/`c0ff f4390000`逐字节写入`0x7f4a..4d`，SDK `lp_ipc.h`/`p11_app.h`消费宏与`VAD_DVAD_CONFIG_BEGIN`布局算术互证）——推导而非测量（q32s不执行），只作显式实验配置使用。

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

## q32s 静态反汇编已实跑

新增[analyze_q32s.py](../tools/analyze_q32s.py)，用固定ghidra-jieli语言定义和pypcode3.3.3自带的Sleigh编译器，不需要安装完整Ghidra，也不需要GPU。后端源码按SHA256下载到忽略缓存；只反汇编短片段，不执行p-code。公开结果：[q32s-static.json](../evidence/q32s-static.json)。

```bash
python3 -m venv analysis/q32s-env
analysis/q32s-env/bin/pip install pypcode==3.3.3
analysis/q32s-env/bin/python tools/analyze_q32s.py --fetch \
  --report analysis/q32s-static.json
```

如果固定源码已在缓存，省略`--fetch`，仍核对摘要并重新编译。上游编译器提示的NOP构造不据此补任何指令语义。静态结果包括：

- 公共段开始附近有设置SSP／SP／USP为`0x3d00`、RETI=`0xa4`并RTI的初始化形状；`0xa4`附近清BSS，`0xc2`跳转`0x9c0`。这不证明硬件从`0x80`复位。
- 独立可解码片段`0xac0`将R0设为`0xa400`，随后`0xaf8`读取、`0xafa`清bit5、`0xafc`写回。固定`p11_app.h`把该位明确命名为`LP_PWR_INIT_FLAG`，正好与更晚CPU0等待相符。此前`0xa86`存在解码缺口，所以没有把独立片段拼成已执行的连续路径。
- `0xc14 / 0xc1a / 0xc20 / 0xc26`四处直接调用`0x8000 / 0x800a / 0x801a / 0x802a`。工具另用原始半字字段独立重算`PC+4+((signed6(w0&63)<<17)|(w1<<1))`，并核对前方五项跳转表确实定位这些分支及第五分支，排除明显的错位解码和PC基准错误。它仍是依据同一参考ISA规则的静态交叉核对，不是新增硬件测量。

固定`p11_app.h`给出P11 RAM大小`0x8000`；以上调用位于这个RAM上界之外、SFR`0xa000`以下，稳定目标强烈提示独立ROM入口。P11局部RAM／SFR分别有主核映射，但据此外推`0xf28000..0xf2a000`只是**候选窗口**，没有证据证明这个洞可读、属于ROM或与CPU0 ROM共用。现有USB读取工具的固定主核ROM范围没有因此放开，也没有尝试读取该窗口。

后端存在实际缺口：`0x86`的special-register解码与附近ICFG修改形状不符；另一个JGE的p-code方向与mnemonic冲突。工具遇未知即停，不修改后端去迎合预期，也不执行这些有疑问的语义。离整机启动还需要补P11 ROM／主核访问映射、可信q32s指令执行，以及此前的PMU、watchdog和BR28硬件IRQ契约。

看门狗方面，固定`wdt.c.o`的`wdt_key_patch`确实先加软件锁，再写KEY `0x048c159d / 0x26ae37bf`，`wdt_init`随后先写CON=0。后续已有受限KEY命令投影和显式名义时钟下的超时检查，见[看门狗回放](clock-rtos-startup.md)；硬件KEY生命周期、真实时钟校准和复位交付仍未知，不能把它当普通RW存储继续。
