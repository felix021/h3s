# app_core 尾段四簇 + 音频簇 SFR 消费者语义（路 BO / sim-audiosfr，只读调研）

2026-11-13。本页是路 BO 的证据包：对 [docs/appcore-tail-map.md](appcore-tail-map.md)
（路 BH）量化的 4 簇 19 个新 SFR 地址（N1 电源 / N2 电源框架 / N3 P33 窗 / N4 金丝雀树）
加上 §5 的音频簇（0xfe051c / 0xfe0a04）与 0xf2c10c，逐地址给出 SDK 头归属、app 侧
访问点、读写语义预判与三选一倾向，服务后续路的证据先行决策。**只读调研**：未改
tools/ 任何代码、未跑任何 probe；私有 ROM 只在本地用于字面量扫描（路径与字节不进库）。

**方法与自校验。** 反汇编用仓库钉死的 pi32v2-isa codec（与 probe 同 commit），一次性
脚本放本地 tmp 不进仓库。全 app 线性解码 279,401 条指令收集 call 边与 li 字面量，
并用路 BG 已钉死的事实自校验：`0x60115b4` 恰 5 个 call 点（0x60116ee/0x6011706/
0x60120f4/0x6012106/0x6034900）、`0xfd3100` 恰 7 处 li.u32——与 BG 记录逐一一致。
线性扫描在数据段会产生对齐漂移伪迹（本文所有关键边都用「从已知函数入口正向重解」
复核过，app_core 尾段 57 个直接 call 目标已全文核对，与 tail-map 分段图一致）。
trace 侧只读 `analysis/local-checks/irq-entry.json`（p11_heap 场景 304,317 停点）：
**除 0xfd3100 的 6 条写外，本文全部目标地址在前缀内零触达**（顺序/条件段都在停点后）。

## 1. SDK 头归属与地址推导

地址推导规则（参考 SDK 头，非精确 QCY 固件源）：`hs_base=0xfe0000`、`ls_base=0xfd0000`、
`map_adr(grp,adr)=(64*grp+adr)*4`（即 grp×0x100 + adr×4）；P33 窗口
`P33_ACCESS(x)=0xf2c000+x*4`。注意 br28.h 里 `__u16/__u8` 宏全部展开为 4 字节槽，
结构体成员一律 4 字节对齐（首次使用容易踩坑）。

| 地址 | 推导 | SDK 名 | 属性 | 出处（br28.h 除注明外） |
|---|---|---|---|---|
| 0xfd3100 | ls+map_adr(0x31,0) | JL_ADC->CON | __RW | L940-946（路 BG 已钉） |
| 0xfd3104 | +4 | JL_ADC->RES | __RO | 同上 |
| 0xfd0200 | ls+map_adr(0x02,0) | JL_SYSTEM->CHIP_ID | **__WO（头标注，与实读矛盾，见 §2）** | L626-631 |
| 0xfd1400/04 | ls+map_adr(0x14,0/1) | JL_SD0->CON0/CON1 | __RW | L697-708 |
| 0xfd1d00 | ls+map_adr(0x1d,0) | JL_SPI1->CON | __RW | L790-806 |
| 0xfd1e00 | ls+map_adr(0x1e,0) | JL_SPI2->CON | __RW | 同上 |
| 0xfd2408 | ls+map_adr(0x24,2) | JL_IIC->BAUD | __WO | L834-842 |
| 0xfd5f00/04/08/0C/10/14/20/34 | ls grp 0x5f | **SDK 无名孔**（0x57 PORTP 与 0x60 USB_IO 之间整组未定义） | — | — |
| 0xfd5c00/04、0xfd5800 | ls grp 0x5c/0x58 | SDK 无名孔 | — | — |
| 0xfd6100 | ls+map_adr(0x61,0) | JL_PLED->CON0 | __RW | L1249-1272 |
| 0xfd7504 | ls grp 0x75+1 | SDK 无名孔 | — | — |
| 0xfd0600/04/08 | ls+map_adr(0x06,0/1/2) | **JL_TIMER2->CON/CNT/PRD** | __RW | L651-665 |
| 0xf2c240 | P33_ACCESS(0x90) | **P3_ANA_CON0** | 仅地址宏 | p33_sfr.h L130 |
| 0xf2c044 | P33_ACCESS(0x11) | **P3_VLVD_CON** | 仅地址宏 | p33_sfr.h L30 |
| 0xf2c0e0/e4/e8/ec | P33_ACCESS(0x38..0x3b) | P3_SFLAG0/1/2/3 | 仅地址宏 | p33_sfr.h L65-68 |
| 0xf2c10c | P33_ACCESS(0x43) | **P3_PVDD0_AUTO** | 仅地址宏 | p33_sfr.h L78 |
| 0xf2c110 | P33_ACCESS(0x44) | P3_PVDD1_AUTO | 仅地址宏 | p33_sfr.h L79 |
| 0xfe051c | hs+map_adr(0x05,7) | **JL_AUDIO->DAC_VL0** | __RW | br28.h L162-194（DAC_VL0 在 +0x1c） |
| 0xfe0a00/04/08 | hs+map_adr(0x0a,0/1/2) | **JL_ADDA->DAA_CON0/1/2** | __RW | br28.h L286-310 |

无名孔补充（本轮顺带钉死、非 4 簇成员）：0xfd5e08（grp 0x5e+2）、0xf2c1f4
（P33 0x7d）、0xf2c2bc/c0（P33 0xaf/b0）、0xf2c3a0（P33 0xe8）、0xf2c364..390
（P33 0xd9+）——都在下文访问点里出现。

## 2. 四簇逐地址访问面与语义

证据强度标注：**[静态]** = 指令锚定反汇编钉死；**[头]** = SDK 头推断（参考材料）；
**[trace]** = 304,317 前缀实测。

### N1：JL_ADC（主路径，BG 已闭合写侧）

- 0xfd3100/04：路 BG 已全量钉死（6 写 + 读回消费 0x601163a bit7 忙等/bit4 门控、
  caller A 0x60116d0 读 RES）。本轮唯一新增核对：BG 记的「第 7 处 li」就是 0x6067f84
  内 0x6068084 的单写 `=0x40`（0x606808a 落笔），与「power_init 尾段区单写」记录自洽
  [静态]。[trace] 6 写全部在案（值序列 0x20000/0x20040/0x40 ×2）。结论不变：
  **读侧三选一（fixture/钉住/真机测量）是下一路决策点，本路无新证据改变它**。

### N2：电源框架簇（条件路径 S13/S15-S17 充电电池分支 + W1[0] 主路径新发现）

全部写点集中在 0x6067f84（条件充电树，S13 `0x6067dda(12/16)` 到达）单一函数内
[静态]，且多为「外设关断清零」序列：

| 地址 | 站点（0x6067f84 内） | 形状 |
|---|---|---|
| 0xfd1400/04/08 | 0x60680d4..0x60680dc | SD0 CON0/1/2 = 0（JL_SD0 关断） |
| 0xfd1d00 | 0x60680cc | SPI1 CON = 0 |
| 0xfd1e00 | 0x60680d2 | SPI2 CON = 0 |
| 0xfd2408 | 0x60680e2 | IIC BAUD = 0（__WO 口写 0=关时钟） |
| 0xfd6100 | 0x60680e4..0x60680ea | PLED CON0 = 0 ×2 |
| 0xfd1c00 族周边 | 0x60680c0.. | SFC 时钟块 +0x100/+0x200 计算基址派生 |
| 0xfd4600 ×2 | 0x6068096/98 | PLL0 CON = 0 ×2（fd46xx 已有 PLL 投影族） |
| 0xfd5c04 + 0xfd5c00..FC ×64 字、0xfd5800..C0 ×49 字 | 0x606809a..0x60680c0 | 两块无名孔整块清零 |
| 0xfd5f00/04 | 0x6068042..0x6068056 | `[0xfd5f00]|=1`、`[0xfd5f04]` RMW（bits8-9|=3） |
| 0xfe0a00..0x50 | 0x606805a..0x606806c | **JL_ADDA 整块 20 字清零** + DAA_CON2 `|=0x8000`、`|=0x20` + 条件 `|=0x2000` |
| 0xfd3100 | 0x6068084 | ADC CON = 0x40（N1a 第 7 写点） |

要点：

1. **0xfd0200 = JL_SYSTEM->CHIP_ID 是真实读回消费者** [静态]：0x6068076
   `r5=[0xfd0200]; r5&=0xC; if (r5==12) [0xfe0a00]|=0x2000`——芯片身份位
   （bits2-3）门控 ADDA 模拟配置位；同一 idiom 在 0x6069ab0（0x6069xxx 音频/模拟
   init 域，函数入口未钉死，见 §6）重复出现；第三处 0x6024210 读低半字节选版本串
   （info getter 域）。SDK 头标 __WO 与实读矛盾——按头标注不可靠处理，实读为准。
   **live input（芯片身份），硅值不可推导**。三处读者都不在健康主路径上（条件充电树 /
   音频 init 域 / info getter），可推迟到 D1 后决策。
2. **0xfd5f 无名块横跨两条路径**（tail-map 只记了条件侧的 0xfd5f00/04）：W1[0]
   `0x6012e3e`（**S10 主路径**，无条件调用）在 0x6012e9c..0x6012f0e 对
   **0xfd5f08/0C/10/14/20** 做带延时的逐位 RMW（`[r7+8]&=~1`、`[r7+12]|=1`、
   `[r7]|=1`、`[r7+4]|=1`、`[r7+24]|=1`，r7=0xfd5f08，间隔 call RAM 0x12b746/
   0x12b772 对）[静态]；cleanup 0x60682a4 读 [0xfd5f34]>>14&4 折进 SFLAG0（金丝雀
   树内）。主路径成员是**本路最重要的增量发现**：BG 推进到 S10 时会先在 W1[0] 里
   撞上它们（顺序见 §6 停点序列）。
3. 0xfd6100 在别处还有真实读回消费 [静态]：LED 驱动 0x60194a4（0x60194ae 起）
   `r6=[0xfd6100]` 存使能位 → 清 bit0 → 配置 → `if (r6&1) [0xfd6100]|=1` 恢复
   （保存-恢复型 RMW）；0x6069b08 读 bit0 决定 LED 亮度步进。PLED 是「读改写+位消费」
   而非纯写。0x6019xxx LED 驱动域还有 6 处 li（0x60194ae/0x60195e6/0x6019a62/
   0x6019a9e/0x6019bb0/0x601a38c）+ 0x6034abc/0x6034b0c（debug/emu 邻域）。
4. **0xfd7504 不可复现**：全 app/ROM 无字面量、0x6067f84 树（含 callee 0x6067e48/
   0x601209a/0x6067dda）指令锚定反汇编无任何计算写点命中。判定为 tail-map walker
   常量传播伪迹的高概率候选，**从决策清单摘除待重验**。

### N3：0xf2c240 = P3_ANA_CON0（主路径 S10，tail-map 的「unnamed」可修正）

- 归属修正：0xf2c240 = P33_ACCESS(0x90) = **P3_ANA_CON0**，p33_sfr.h 有名（仅地址
  宏）。它就是路 AM `p33_analog_config_projection` 已建模的 P3_ANA_CON2/CON5
  （0xf2c248/0xf2c254）所在 **P3_ANA_CON0..15 = 0xf2c240..0xf2c27c 家族的第 0 个**；
  tail-map 的 4 个「扩展」0xf2c25c/260/264/268 = CON7/8/9/10。
- 访问形状全是**配置 RMW**（不经独立读回）[静态]：
  - 0x6067e16（条件）：`[0xf2c240] &= 0x7FB; |= 4` 然后 `&= 0xFF7`（复合 RMW）；
  - 0x6068152（mode 切换函数，仅 mode 0/1 有效）：先对 CON7/8/9/10（[r2+28..40]）
    做一长串 `&mask|field` RMW，末尾 0x606827e 对 CON0 `&0x3FD3|8` RMW。
  - 0x6068152 的调用者 [静态]：**0x601318e 在 W1[0]=0x6012e3e 函数体内**（函数尾
    0x6013196，正向重解钉死——tail-map 的 W1[0]→0x6068152 边成立），另有
    0x6012c44/0x6012c50（0x6012bfe 内，音频任务层 0x6012832←0x6012af8 链）与
    0x60211d0。即 N3 同时在主路径（S10 W1[0]）与 D1 后音频任务层出现。
- W1[0] 本身的完整边界面（本轮增量枚举，按执行序）[静态]：0x60685ae（P3_WKUP_SRC
  0xf2c2a0 `&=~4` + 无名 0xf2c2bc/c0 `|=4` RMW）→ 0x6067dda(12) 查询 → **0xfd5f08
  族 RMW ×5**（N2 要点 2）→ … → 0x6066c26 任务创建 → 0x6068152（CON7..10+CON0 RMW，
  N3）→ 0x6013196 返回。tail-map 只记了 N3 一项，实际 S10 内 W1[0] 的首个新边界是
  0x60685ae 的 WKUP_SRC 族（0xf2c2a0 已在 reset_history 投影读表里、有具名 0 fixture
  先例；0xf2c2bc/c0 是新 RMW 面）。
- 同族顺带：0xf2c10c = **P3_PVDD0_AUTO**（tail-map §5 记的 off-path 0xf2c10c），
  owner 0x60685fe（PVDD 电压设置：PVDD0/PVDD1/ANA_CON1 组合 RMW+延时爬升，
  callers 0x6068778/0x60687f6，idle/trim 域）与 0x6068662（0x6034c82 调用）。全为
  配置 RMW，无独立读回消费 [静态]。

### N4：金丝雀树/条件尾（维持 watch-only，但地址映射大修）

tail-map 的 N4 地址归属有系统性误记，本轮全部重钉 [静态]：

| tail-map 原记 | 实际（指令锚定） | 形状 |
|---|---|---|
| 0x12cc56 读 0xf2c044 | 0x60c642e 读 **[0xf2c164]=P3_WKUP_CPND1**（并行对 add.s14.rri.p r2=r0-228，r0=0xf2c248） | 读回消费：bit2<<5 → 折进 P3_ANA_CON2 bit7 的 RMW |
| 0x12ced4/da r/w 0xf2c044 | 0x60c66ac 读 [0xf2c164] RMW（`&0x7FFF|64`）；0x60c66b4 RMW **[0xf2c244]=P3_ANA_CON1**（`&0x7FFF|64`） | 配置 RMW；0x12ceb8 另写 0xFF 到 0xf2c280/84/8c |
| 0xfd0600/04/08 「无名外设块」 | **JL_TIMER2->CON/CNT/PRD**，owner 0x12cd26（flash 源 0x60c64fe）= **TIMER2 延时 helper**：CON=0x4000→CNT=1→PRD=秒数→CON=0xC01 启动→`idle`→**轮询 CON 符号位（bit15 完成）**→`|=0x4000`→CON=0 | 读回真实消费（idle 忙等）；**ROM 里还有孪生函数 0xa778**（同样 li 0xfd0600，`[r4]=0x4000;[r4+4]=0` 起手），ROM 侧唯一目标引用 |
| 0xf2c044 r 0x12cc56/0x60682d0 | 0xf2c044 真实读者是 **0x60682c4（cleanup 0x60682a4 内）**：`r1=[0xf2c044]; r1<<3&8` → **bit0 折进 [0xf2c0e0]=P3_SFLAG0 的写入值**（与 [0xfd5f34]>>14&4 合并） | 读回消费（bit0=VLVD 使能状态），仅在金丝雀树 |
| 0xf2c0e4/e8/ec w 0x60682e8..f4 | 确认 [静态]：SFLAG1=0（0x60682e8）、SFLAG2=0（0x60682ee）、SFLAG3=0xF34（0x60682f4），外加 SFLAG0=计算字（0x60682e0）与 P3_PCNT_SET0/1=0（0x60682ea/ee） | 整字纯写 |
| （未记）0xf2c044 的另两处 | RAM 0x60c5950（DVFS 序列：**VLVD_CON=0 整字关断** + P3_ANA_CON5 RMW + 延时）与 0x60c6750（`[0xf2c044]&=0x1FE` 复合 RMW 族 + ANA_KEEP/PCNT_DAT RMW） | 低功耗/电压切换域（0x60c5xxx RAM copy 族） |

结论：N4 的实际访问面几乎全部落在**已建模家族**（P3_WKUP_CPND/P3_ANA_CON/PCNT/
SFLAG 整字写）+ 一个新语义点（TIMER2 idle 轮询）。健康路径不可达的判断不变
（K1/K2 watch-only + S11 条件 `0x12cc40`），维持 tail-map 的钉住建议；但若真到达，
0x12cc40 的停点应是 P3_ANA_CON2/P3_WKUP_CPND1 族（AM/reset 容器内）而非新地址。

## 3. 音频簇消费者分支分析（重点展开）

tail-map §5 说音频簇「SDK 无名」——**可修正**：0xfe051c=JL_AUDIO->DAC_VL0、
0xfe0a04=JL_ADDA->DAA_CON1（§1 表）。两者都是**读回消费真实存在**的 RMW 型寄存器，
消费者逻辑已钉死 [静态]（无 trace，D1 后才触达）：

### 0xfe051c = DAC_VL0（数字音量，双通道半字对）

结构：低 16 位 = 通道 0 音量、高 16 位 = 通道 1 音量（[头] __RW + [静态] 半字插入
形状互证）。

- **getter 0x606daf0**：`r2=0xfe051c; if (r0&1) return [r2].l; if (r0&2) return [r2]>>16`
  ——按参数 bit0/bit1 选半字读回。
- **setter 0x606db36**：读旧值 → `r1=umin(表上限, 新值)`（上限来自配置槽
  `[0x1265c0+1883*4]=0x1272ac`）→ 按通道 insert 新半字 → 整字写回 → 尾跳 0x606b40e
  （联动模拟增益）。**RMW 读旧值参与写**。
- **同步 walker 0x606db74**：逐通道（使能位图 `[cfg+20]`，通道数 `b[cfg+26]`）：
  调 getter 读回 DAC_VL0 半字 → 与期望值 `h[cfg+16+ch*10+2]` 比较 → 不等则调 setter
  修正；再读 DAA_CON1 3 位字段反查表比对 → 不等则走 0x606db6c thunk 调 0x606b40e
  修正。**这是「读回-比对-纠偏」闭环，音量状态漂移检测**。
- **静音/关闭 0x606dd16**（caller 0x6012672，音频任务层 0x60125f4/0x601267a 族）：
  pend 设备互斥（0x6064538）→ **`r0=[0xfe051c]; if (r0==0) goto 收尾`**（读回判零
  门控）→ 条件全过则**整字写 0**（数字音量归零=静音）→ 清软件标志（b[0x1265c0+7684]）
  → post（0x606475a）→ 尾跳 0x606b20e（模拟下电链）。

### 0xfe0a04 = DAA_CON1（模拟增益，双通道 3 位字段）

- **RMW 0x606b40e**：`if (r1>=3) r1=3; r3=b[0x060a8608+r1]`（**4 档增益码表
  {0x00,0x04,0x06,0x07}**，flash 0x060a8608，紧邻 CRC16 表 0x060a860c）→ 读旧值
  `[0xfe0a04]` → 通道 0（r0&1）：保留 bits3-31、低 3 位换表值；通道 1（r0&2）：
  bits4-6 换表值 → 写回。
- **读回消费**（0x606db74 内）：`r0=[0xfe0a04]&7`（或通道 1 `uextra(p:4,l:3)`）→
  在同一 4 档表里反查下标 → 与期望 `b[cfg+16+ch*10+5]` 比较。

### 家族面（比 tail-map 的 2 地址大）

- **JL_ADDA 0xfe0a00 块共 8 个 li 用户** [静态]：0x602ec88（codec 参数填充）、
  0x606805a（N2 充电树整块清零+DAA_CON2 `|=0x8000/0x20`）、0x6069ac0（CHIP_ID 门控
  `|=0x2000`）、0x606ac3c（`&=~0x2000` 清 bit13）、0x606b352（**读 DAA_CON2 `&2`
  与期望比较——状态位读回消费**）、0x606e212（DAA_CON2 `|=0x8000/0x20`）、
  0x6071d80（DAA_CON0 `|=0x20000000;|=0x40000000` 上电对）与 0x607384c
  （`&=0xDFFFFFFF;&=0xBFFFFFFF` 下电对）。
- **JL_AUDIO 0xfe0500 基址共 15 个 li 用户**（0x602ea2c/0x602f40e/0x606a7fc/
  0x606aaf6/0x606ab10/0x606b266/0x606b488/0x606e144/0x607155c/0x6071928/0x6071cb4/
  0x6071e8e/0x6072480/0x6073248/0x6073394）——DAC/ADC DMA、alnk 等音频数字域全在
  0x606axxx..0x6073xxx 驱动层，**D1 之后才有意义**。
- 分发表核对：0x60a89e0/0x60a8c68 两表的 handler 全在 0x6000xxx..0x6026xxx，
  **无 0x606dxxx/0x6071xxx**——音频 SFR 函数在 handler 下 ≥2 层（经 0x6012xxx
  音频任务层间接到达），与 tail-map「任务体/handler 域、D1 后」一致。

## 4. 对 appcore-tail-map.md 的修正清单

1. N3 0xf2c240「P33 unnamed window register」→ **P3_ANA_CON0**（p33_sfr.h L130），
   路 AM 容器同族；其 4 个「扩展」= CON7/8/9/10。
2. N4 的 0x12cc56/0x12ced4/da 地址归属误记（实际 P3_WKUP_CPND1/P3_ANA_CON1，见 §2 表）；
   0xf2c044 的真实读者是 0x60682c4 与 RAM 0x60c5950/0x60c6750。
3. N4 0xfd0600 族「unnamed peripheral block」→ **JL_TIMER2**（延时 helper + ROM 孪生
   0xa778）。
4. N2 多数成员 SDK 有名（SD0/SPI1/SPI2/IIC/PLED/SYSTEM）；0xfd7504 不可复现（walker
   伪迹候选，摘除待重验）。
5. 音频簇「SDK 无名」→ DAC_VL0/DAA_CON1 有名；且家族面大得多（§3）。
6. N2 的 0xfd5f 无名块横跨条件树与主路径（W1[0] 内 08/0C/10/14/20 五个 RMW，tail-map
   只记了条件侧 00/04）；W1[0] 的首个新边界其实是 0x60685ae 的 WKUP_SRC 族而非 N3。
7.（小）0x606dcc6 等 0x606dxxx「音频域函数」确实不在顺序路径（复核成立），但
   0x6068152 除 W1[0] 外还有音频任务层 0x6012bfe 两个调用点——N3 主路径判断不变。

## 5. 三选一倾向与关键未知（建议≠决定，落地仍走证据核验）

| 地址/簇 | 倾向 | 依据与先例 | 关键未知 |
|---|---|---|---|
| N1a 0xfd3100 读侧 | 沿 BG 留下的三选一（fixture/钉住/真机），本路无新证据 | BG 全量钉死 | CON 状态位硅语义、RES 模拟值 |
| N1b 0xfd3104 | fixture（仅真机）或钉住 | RES __RO 模拟量无可推导 | 测量通道（路 BA）是否授权 |
| N2 清零写组（SD0/SPI1/SPI2/IIC/PLED/无名块） | 投影：write-only 整字记录（lp_timer SYS_DIV 族） | 全部纯写 0 [静态]；仅条件充电树到达 | S13 分支运行时是否走到（证据先行不变） |
| N2 0xfd0200 CHIP_ID 读 | 具名 live-input fixture（SDTAP/PINR 族）或钉住——**推迟到 D1 后**（三处读者均不在健康主路径） | `&0xC==12` 门控 + 版本串 | 真机 bits2-3/低半字节实际值 |
| N2/N4 0xfd5f00 块 | 投影：配置 RMW 记录（0xf2c054 先例）；主路径成员（08..20）进 BG-b 清单 | RMW-内部读，无独立消费；cleanup 的 [0xfd5f34] 折叠在金丝雀树 | 块的硬件身份（SDK 无名） |
| N3 0xf2c240 + CON7..10 | 投影：扩展路 AM `p33_analog_config` 容器到 CON0/7/8/9/10 | 同族纯配置 RMW [静态] | 无实质未知（写侧记录即可） |
| W1[0] 新面 0xf2c2bc/c0 | 投影：同族 RMW 记录（与 0xf2c2a0 WKUP_SRC 同容器） | `|=`/`&=~4` 形状 | 无 |
| N4 全簇 | 维持钉住/watch-only；0x12cc40 到达时按 AM/reset 容器家族处理 | 健康路径不可达复核成立 | — |
| 0xf2c044 VLVD_CON | 钉住（主路径不碰）；低功耗域到达时再按配置 RMW 处理 | 写 0 关断+RMW，读回消费仅金丝雀树 bit0 | DVFS 域何时触达 |
| 0xf2c10c PVDD0_AUTO | 投影（到达时）：配置 RMW 记录 | 0x60685fe/0x6068662 全 RMW | off-path，idle/trim 域触达时机 |
| **0xfe051c DAC_VL0** | **投影：读回=最后写值**（lp_timer CON 先例路 AL：SDK __RW + 消费者全链可由固件写侧推出） | getter/setter/同步 walker/静音四消费者钉死 [静态] | 初值（首次 RMW 前有无先导写） |
| **0xfe0a04 DAA_CON1** | 投影（读回=最后写值）+ **初值具名 fixture**（P33_AWKUP_P_IE 首读零先例 BB） | RMW 插 3 位字段 + 反查表比较 | 复位值（0 还是 0x404 型交叠码） |
| 0xfe0a00/08 DAA_CON0/2 | 写侧整字/RMW 记录；DAA_CON2 bit1 读回语义推迟 | 上/下电位对 + 块清零 [静态] | bit1 状态位硅语义 |
| 音频数字域 0xfe05xx 全块 | D1 后另立证据包（15 个 li 站点的 DMA/alnk 域） | — | 整块 |

## 6. 对路线梯的建议（BG/BI 及之后）

- **BG-a 不变**：304,317 的 ADC CON 读侧三选一就是下一路。
- **BG-b 扩容（本轮最大实际影响）**：W1[0]（S10 第一个函数）的边界序列实测为
  0x60685ae WKUP_SRC 族 RMW（含新 0xf2c2bc/c0）→ 0x6067dda 查询 → 0xfd5f08 族
  RMW ×5 → 0x6068152 的 ANA_CON0/7/8/9/10 RMW。全部是「配置 RMW/write-only 记录」
  同族，建议一次容器扩展打包（AM 容器 + 0xf2c2a0 同容器 + 0xfd5f 块），无需逐个
  三选一。
- **BG-c/N4**：0x12cc40 到达时的停点应是 P3_ANA_CON2/P3_WKUP_CPND1（已有容器），
  tail-map 记的 0xf2c044 不会在那出现。
- **BH-a**：N2 决策只剩「CHIP_ID live-input fixture 值」一个真未知，且可推迟
  D1 后；其余全是同族写记录。
- **音频簇归 D1 后**：0xfe051c/0xfe0a04 的「读回=最后写值 + 首读具名 fixture」
  组合先例齐备（AL+BB），建议随首个音频事件 handler 场景一起落地；家族面
  （0xfe0500 15 站点 + 0xfe0a00 8 站点）量级已列，D2（音频流）前不必预建模。

## 7. 不确定处（如实标注）

- 0x6069ab0 所在函数（0x6069xxx 音频/模拟 init 域，含 CHIP_ID idiom + PLED bit0
  读）入口未钉死（反向 prologue 扫描遇错位解码，未再投入）；其调用域按 0x6069xxx
  归类为 D1 后，不影响主路径结论。
- 0x12cd26 内 `[cnum<<11 + r2<<2]=6` 一写的有效地址换算未解（疑 ICFG 槽族，
  off-target 未追）。
- 线性扫描的对齐漂移伪迹：本文所有引用边都经正向重解复核，但「无 caller」结论
  （如 0x6071a70 族）只排除直接调用，不排除间接/表引用（分发表已查无，其他 RAM
  结构表未穷举）。
- br28.h 是参考 SDK 头（路 AC/BG 同一来源），非精确 QCY 固件源；`__WO CHIP_ID`
  与实读的矛盾以实读为准，头标注仅参考。
- 步数/触达预测沿 tail-map 限制（CON/PRD 再写会移动硬期限）不变。

## 8. 路 BV 复核修正（2026-11-13，sim-tailproj，逐条指令锚定重解）

本节是路 BV 实施前对本文（路 BO 证据包）的逐地址复核结果。方法：与探针同 commit 的
钉死 pi32v2 codec 对每个引用点从 li 基址正向重解（含并行对旧值方向），外加全 app+ROM
字面量扫描。**本文三处基址/归属记录有误，修正如下（原文保留，以本节为准）**：

1. **§2-N2 的「W1[0] 主路径 0xfd5f08/0C/10/14/20 五个 RMW」不存在**。0x6012e9c 的
   li 字面量是 **0xfd5708**（0x57 组 PORTP，`GPIO_BASES` 已含 0xfd5700 基址）：
   五个 RMW 实为 `[0xfd5710]&=~1`、`[0xfd5714]|=1`、`[0xfd5708]|=1`、`[0xfd570c]|=1`、
   `[0xfd5720]|=1`（相对偏移 +8/+12/+0/+4/+24 与原文一致，基址差 0x800）——全部落在
   既有 GPIO 投影域（一位 RMW 位记录），**无需新投影**。0xfd5f00 族全 app/ROM 字面量
   0 处、原文所记两个站点（W1[0] 与条件树 0x6068042..56）正向重解均不落 0xfd5fxx：
   **不建模**。条件侧原文记的 0x6068042..56 两访问实为
   **0xfd0018**（|=1）与 **0xfd001c**（bits8-9|=3，ld/st RMW）——JL 时钟族（CLOCK0
   邻域），归路 BH 证据先行，不在路 BV 打包内。
2. **§2-N3 的「W1[0] 首个新边界 0x60685ae WKUP_SRC 族（0xf2c2a0 &=~4 + 无名
   0xf2c2bc/c0 |=4）」误记**。0x60685b6 的 li 字面量是 **0xf2c180**（P3_AWKUP_EN）：
   `[0xf2c19c]（P_CPND）|=4`、`[0xf2c1a0]（N_CPND）|=4`、`[0xf2c180]（EN）&=~4`——
   就是路 BB `P33AwkupRegisters` 注释里已钉死的「独立清除助手 0x60685ae」，投影已
   覆盖。0xf2c2bc 全 app/ROM 零引用；0xf2c2c0=P3_EFUSE_CON0（既有 EFUSE 投影）仅
   0x6034fb6 一处字面量（debug/emu 域）。**WKUP_SRC 新面不存在，不建模**。
   W1[0] 真正的首个新边界是 **0x6012e6e `li r5=0xf2c064` 之后的
   0x6012e74 `rmw.and.modimm [0xf2c068]（P3_CHG_CON0）&=0x1FE`**
   （+0x6012e7c `&=0x3FD`）；同函数 0x60130b0..0x601310e 还有 CHG_CON0/CON1
   （0xf2c068/0xf2c06c）的显式 ld/st RMW 与整字写。P3_ANA_READ（0xf2c064）被
   0x6012f26 轮询（≤1000 轮延时循环）与 0x60131c/0x6013122 位测试（bit1/bit2 门控
   任务创建参数）真实消费——模拟量读，读回钉住（`p3_ana_read_not_modeled`，
   RES 红线同款），不发明 fixture。
3. **§2-N4 的金丝雀树「P3_WKUP_CPND1/P3_ANA_CON1」归属是本包自己的 +0x120 误记**
   （0x60c641a 的 li 是 **0xf2c128**（P3_PCNT_SET0）而非 0xf2c248；0x60c6692 的 li
   是 **0xf2c124**（P3_PCNT_CON）而非 0xf2c244）。tail-map 的原记录反而成立：
   0x12cc56（flash 0x60c642e）读 **P3_VLVD_CON（0xf2c044）**（bit2<<5&0x80 折进
   P3_PCNT_SET0 写值）+ 0x60c643e `[0xf2c044]&=0x7FB`；0x12ced4/da（flash
   0x60c66ac/b4）对 **0xf2c044 与 0xf2c124** 做 `&0x7FBF|0x40` ld/st RMW；cleanup
   0x60682c4 读 [0xf2c044] bit0<<3&8 折进 P3_SFLAG0（另一源是
   **[0xfd6014]=IOMAP_CON0**>>14&4——修正本文「[0xfd5f34]>>14&4」，0xfd5f34 全
   app/ROM 零引用）。P3_PCNT_CON 另有一处复合置位：AWKUP 中断分发 0x6069238
   `rmw.or.modimm [0xf2c124]|=0x40`。P3_ANA_CON1（0xf2c244）字面量真实存在但唯一
   （0x6068676，PVDD/idle-trim 域 0x6068662 内，off-path），本轮不建模。
4. §5 表按上述修正后的落地决策：**AM 容器扩展 = P3_ANA_CON0/7/8/9/10 +
   P3_CHG_CON0/CON1 一次打包**（opt-in `p33_analog_tail_projection`，路 AM 同款
   「合成初始字 0+读回=当前值」；CON7..10/CON0 的访问形状——显式 ld/st RMW+复合
   and RMW+pair 尾写回——本文 §2-N3 的记录经重解全部成立）；**金丝雀树两地址 =
   P3_VLVD_CON+P3_PCNT_CON**（opt-in `p33_canary_projection` + 首次读具名 fixture
   `p33_canary_first_read_words`，BB 的 P3_AWKUP 先例同模式；写侧整字/RMW 位记录，
   watch-only 不变）；**0xfd5f 块/WKUP_SRC 新面/0xfd7504 维持不建模**（零引用）。
   mode 切换函数 0x6068152 头部另有 0xfd0008/0c/10（JL 时钟族，tail-map 的
   family-adjacent 桶）与 0xfd1920（WLA_CON8，OSC 族既有投影）——前者归下一路
   （RES 闭合后场景推进时撞上），不在本包。

## 9. 路 CB 复核（2026-11-13，sim-anaread，P3_ANA_READ 读侧三选一证据包）

本节是路 CB（447,383 停点的三选一决策）的证据包。方法与前路同：钉死 pi32v2 codec
逐指令重解（W1[0] 函数 0x6012e3e..0x6013196 全函数）、全 app+ROM 字面量扫描（私有
ROM 本地、不进库）、pinned SDK commit `67328f4` 的头文件/驱动源/静态库 IR 交叉佐证
（`SDK/cpu/br28/liba` 92 个 bitcode 对象全量抽取 llvm-dis 后常量扫描——0xf2c064 在
全部驱动对象中零引用；charge 域源文件按 gitlab pinned commit 逐个拉取）。

### 9.1 P3_ANA_READ 的 SDK 逐位语义（本路最强新证据）

`SDK/interface/driver/cpu/br28/asm/power/p33/charge_hw.h`（charge 驱动头，pinned
commit 67328f4）给出 P3_ANA_READ 的完整逐位定义：

```c
/************************P3_ANA_READ*****************************/
#define CHARGE_FULL_FLAG_GET()  ((P33_CON_GET(P3_ANA_READ) & BIT(0)) ? 1: 0 )
#define LVCMP_DET_GET()         ((P33_CON_GET(P3_ANA_READ) & BIT(1)) ? 1: 0 )
#define LDO5V_DET_GET()         ((P33_CON_GET(P3_ANA_READ) & BIT(2)) ? 1: 0 )
```

`SDK/cpu/br28/charge/charge.c`（同一 commit）的注释钉死两个检测位的物理阈值：
`if (LVCMP_DET_GET()) {  //ldoin > vbat`（L285）与
`} else if (LDO5V_DET_GET() == 0) { //ldoin<拔出电压（0.6）`（L316）。即
**bit1=1 ⟺ ldoin>vbat（充电输入电压超过电池电压的比较器）、bit2=1 ⟺ ldoin>0.6V
（≈充电线/USB 在位检测）**——不是模拟量数值,是两位数字化比较器状态。

另一处驱动级佐证:system.a `otg.c.o` 的 `fusb_detect`（USB 插入检测去抖）读
P3_ANA_READ &4（LDO5V_DET 参与从机检测判定）——全 92 个驱动 bitcode 对象中唯一
其他引用,live input 位用法,无「完成位」语义。

### 9.2 QCY 消费者全图（反汇编逐指令钉死）

全 app+ROM 对 0xf2c064 的 32 位字面量恰 **5 处**（ROM 零引用）,全部只消费
bit1/bit2（单 bit 测试或 seextra 单位提取,绝无算术、绝无多 bit 字段）：

| 站点 | 形状 | 语义 |
|---|---|---|
| 0x601201a | `ld;uextra(p:2,l:1);rts` | getter:返回 LDO5V_DET（=SDK `get_ldo5v_online_hw` 同形,charge.c L125） |
| 0x6012b58 | `ld;uextra(p:1,l:1);rts` | getter:返回 LVCMP_DET（=SDK `get_lvcmp_det` 同形,charge.c L131） |
| 0x6012e6e（W1[0]） | 两处轮询（下详） | 充电检测主路径 |
| 0x6033f38（函数 0x6033F36 入口） | `ld;&2/&4` 采样计数 | **充电去抖任务**（W1[0] 检测成功时创建,见下）,对 bit1/bit2 分别累计 h[dev+124/126/128] 计数器,阈值取自 0x124BF0 配置区 |
| 0x6035f54 | `ld;&4` | 事件域 handler 的 LDO5V_DET 单测 |

bit0（CHARGE_FULL_FLAG）QCY app 零消费。P3_ANA_READ **零写者**（5 处 li 全是读
基址;模型写拒绝保持）。

### 9.3 W1[0] 的两个轮询循环（循环结构逐指令+分支目标核验）

- **循环1（0x6012f14..0x6012f30）**：`r4=10; goto test; test: if(r4) { delay(1000µs);
  r1=--r4; r0=P3_ANA_READ; r0=seextra(bit2); r4=r0&r1; goto test }`。执行语义=**首读
  LDO5V_DET(bit2)=0 → 1 次 delay(1ms) 退出;=1 → 2 次 delay 退出**（AND 形状使计数在
  第二轮归零,与源码 `do { udelay(1000); } while (LDO5V_DET_GET() & --cnt)` 一致）。
  BV 轮记录的「≤1000 轮延时循环」是误读——1000 是 delay 的微秒参数,轮询预算是
  r4=10 且实际至多 2 轮。
- **循环2（0x601311c..0x6013136）**：`r6=0; do { if (P3_ANA_READ&2) goto 成功;
  if (P3_ANA_READ&4) goto 成功; delay(1000µs); r6++ } while (r6<=19)`——20 轮、
  每轮先测 LVCMP_DET(bit1)再测 LDO5V_DET(bit2)、任一置位即成功、全 0 则 1ms 延时。
  **与 SDK `check_charge_state()`（charge.c L95-107）逐行同形**（20 轮、
  `LVCMP_DET_GET() || LDO5V_DET_GET()`、`udelay(1000)`）——QCY 内联了同一驱动模板。
  - 成功路径（0x6013150）：b[dev+628]=1;若 h[0x125BF8]==0 则
    `0x6066C26(0, 0x6033F36, 2, 1)` 创建充电去抖任务并把句柄存 h[0x125BF8]。
  - 超时路径（0x6013144）：b[0x125BF4]=2（失败码）,不创建任务。
  - 公共尾（0x6013178）：b[dev+640]=1; call 0x601BB0; call 0x6068092(!r)。

### 9.4 三选一决策：方案 (c) 具名常量 live-input fixture（codex 参谋 gpt-6-astra 只读评审采纳）

- **(a) 投影不适用**：P3_ANA_READ 零写者,无 readback=末值可能;bit1/bit2 是 live
  input 比较器状态,无「写后完成」语义（对照 BY 的 PND:SDK 驱动轮询 bit7+RES 有效性
  钉死在 bit7;此处 SDK 驱动把它当外输入位消费）。
- **(c) 成立**:消费者分支真实存在且可达（任务创建/失败码/计数器）;值域被 SDK 位语义
  +物理阈值+场景锚约束。**场景锚=电池启动+无外部供电（ldoin≈0V）**——与路 BY 的
  RES fixture 同一物理世界（电池在位供电 3780mV/标称 4.0V,充电输入引脚无输入;
  两者相容且互补）。注意:**充电窗门 config 99 缺失被前置跳过与 ldoin 无关,不作为
  证据**（codex 修正:那是软件执行路径,不是输入来源）。
- **fixture 形状=常量**（codex 采纳）:轮询语义要求 41 次读（循环1 1 读+循环2 每轮
  2 读×20）,首读 fixture 只会把边界挪到第二读;duration=本场景内持续不变的外部输入,
  不建模插拔/去抖时序。
- **三层标注**（codex 采纳）:derived_mask=0b110（只有 bit1/bit2 有推导链:
  charge_hw.h 位语义+charge.c 阈值+无外部供电锚→两比较器读 0）;其余位（含 bit0
  CHARGE_FULL_FLAG,app 零消费）显式填零非推导,非复位值声明;evidence_status=
  derived-not-measured,真机 FD07 一次 0xf2c064 读可升级为 measured。
- **检测器有效且稳定是显式假设**:W1[0] 在轮询前真实执行 L5V_CON0/1+CHG_CON0/1/
  CHG_CON2 配置（含 CHARGE_DET_EN 使能位,0x103 写值）,但比较器输出时序/迟滞不建模。

### 9.5 fixture 放行后的连锁发现（本路实施时显形）

1. **计算寻址新面**（字面量扫描不可见,fixture 放行后运行态撞上）:
   - **P3_L5V_CON0/CON1（0xf2c284/0xf2c288）**:W1[0] 以 `r0=r5+544`（0x6012fb8）
     计算寻址,0x6012fc0/0x6012fd6 显式 ld/st 配置 RMW（值=配置表字节真实计算）;
     449,751 @ 0x6012fbc 撞上。p33_sfr.h L149/L150 有名,charge_hw.h 位宏
     L5V_LOAD_EN/L5V_IO_MODE/L5V_RES_DET_S_SEL。字面量唯一另处=0x606928c
     （AWKUP 中断分发 0x60691f4 域,D1 后才可达——BB 轮记的 0x60692a6/0x60692b4
     「N_IE/P_IE 消费型读」实为这对 L5V 寄存器,基址记录顺带修正）。
   - **P3_CHG_CON2（0xf2c070）**:`[r5+12]` 计算寻址,0x6013094 ld→&0x7FFF→|r2→
     pair 尾写回（0x601309a `r1=259 ∥ [r5+12]=旧 r1` 并行对）;449,799 @ 0x6013090
     撞上。charge_hw.h 位宏 CHARGE_DET_VOL（bits1-2 检测阈值）/CHARGE_DET_EN
     （bit0）——正是「使能充电检测比较器」的配置,与紧随其后的 bit1/bit2 轮询自洽。
   三者并入 `p33_analog_tail_projection` AM 容器（合成初始字 0+读回=当前值,
   BV 轮同族证据类）。
2. **交付延期机制（共享机器层新投影）**:fixture 放行后世界推进到 ~447,490 core tick
   首溢出,IRQ3 handler（OS tick,执行中会经 0x12b608 软件中断命令请求调度）执行期间
   Timer0 到期合格——交付请求落在在途 handler 上,机器侧「不嵌套」守卫的 ValueError
   从 except 块逃出（probe 崩溃而非诚实停）。落地 **irq_delivery_deferral**:请求挂起
   不丢弃（与 BF 的 masked-pending 逐步重检同族语义）,分发层清模型侧 in-flight
   簿记,handler RTI 后下个 advance 以同一 pending 片重新请求;IRQ 嵌套仍不建模。
   默认行为不变（legacy 世界从未有请求落在在途 handler 上）。
3. **新前沿=601,880 @ 0x6069540 `unknown_gpcnt_configuration_bits`（known_mask
   0x81）**:W1[0] 循环2 第 2-3 次 delay 期间,OS tick 级联（core tick 溢出→软件
   IRQ127→调度器切换任务）把世界带进 0x6069538 函数——`r0=[0xfd1100](GPCNT_CON)`
   读:bit0 使能判定+bit7 轮询+随后 [r4+4] 计数读,上游 0x6069514..0x6069522 是
   `&=~1;|=0x40` 关断/清除对（gpcnt_cleanup 投影已建模的形状）。**GPCNT 读侧是
   新的三选一**（gptimer/gpcnt SDK 头的位语义证据先行）,归下一路;W1[0] 循环2 的
   20 轮超时路径（b[0x125BF4]=2、不创建充电任务、失败码消费链）在新前沿之后,
   随 GPCNT 闭合显形。
