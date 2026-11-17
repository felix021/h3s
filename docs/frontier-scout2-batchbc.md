# 前沿侦察第二棒：批 B（JL_IOMC）+ 批 C（P11_SYSTEM 余员）做深（路 SCOUT2 / sim-scout2，只读静态调研）

2026-11-18 00:15。基线 main `363fded`（前沿 **41,848,382 @ 0x6019508**，p2mclk 路在
途推进）。本页是 [frontier-scout-f2a-batch.md](frontier-scout-f2a-batch.md)（前棒，
批 A 主批 13 字）的直接后继：把**远场批 B（JL_IOMC 映射字族）与批 C（P11_SYSTEM
余员）**做深到批 A 同等深度——访问面全表（PC+指令）、消费链分类、三选一预案
（批 B 补 fixture 值+不敏感性预判）、攒批可行性。**纯静态反汇编，未跑任何仿真**；
本路不改 tools/。

**方法。** 复用前棒工具链（`~/tmp/frontier-scout/`，不进仓）：parse_asm（vendor
objdump 线性解析+call 边）、streamdis（钉定 codec 流式重解，跳表安全）、
globalscan（字面量扫描）。本路新增 **p11scan.py**：逐函数 li/加法/_mov 常量传播
+ `[rN+D]` 操作数匹配，扫**基址衍生访问**（字面量扫描只能抓直接 li，喂狗
[0xf2a608]+4 轮询这类基址+偏移形状会漏——本路用它抓出 power_init 内三站点与
低功耗 save/restore 域的全部 P11_SYSTEM 面）。SDK 头：br28.h（sha `111e29ea…`）、
io_omap.h / io_imap.h、p11_sfr.h / p11_app.h（`~/tmp/p11sdk/`，pinned commit
`67328f4` 同源）。RAM-code 函数运行态地址=flash 母本−0x060c3e18+0x12a640
（既有钉死表）。**[静态·本路]** = 本路指令锚定；**[文档]** = 已合并条目；
**[SDK]** = 参考头；全部 derived-not-measured。

## 0. 一句话结论

**批 B 实际是 8 个字（前棒记 6 字漏了 CON0 与 ICH_CON3）**。本路核实 **CON0
0xfd6014 在当前前沿世界已是活容器**（场景默认 seed 0 + port_initialization_
projection，§2.1——已穿前缀 0x6010b84 RMW 未停的谜底），到达时零动作；其余
OCH 三字/ICH 四字**没有一个能进 CON3 式容器**——OCH 读值进比较分支、ICH 读值
进分支+**跨地址传播**（选择 JL_IMAP 清零目标），全部落「具名 fixture（0=空映射
表）」类，且**触发面全部不在 LED→S19 立即路径**（三组触达面全钉死，§3），与批 A
放行互不依赖。**批 C 补全为 12 字全表**：五字（0xf2a604/614/61c/620/624）零访问
论证闭环（前棒只证了三个）；0xf2a628 有两处 RMW 访问点且**模型已有开关覆盖**
（P11_SYSCON1_MASK_SETS）；0xf2a62c 已建模（mem_pwr_con）；真正待决的只有
**pmu_task 0x60691fc 的 PND&IE 读→bit3 事件分支**（读值进分支，到达时需事件位图
投影而非容器）——维持登记不建模。

## 1. 对前棒批 B/C 表的勘误（本路自查+复核）

1. **勘误③（ICH 字范围错位）**：前棒批 B 表「B-4..6 = 0xfd6020/24/28/2c，
   固件按 0-7/8-15/16-23/24-31 取 0xfd6020..2c 四字」「SDK 名义 ICH_CON0 基是
   0xfd6024——差一字」——**错**。两处固件代码（槽搜索 0x6019694..b6、resolver
   0x6001408..2a）分支形状完全一致：通道 >23→**0xfd6030**、16..23→0xfd602c、
   8..15→0xfd6028、0..7→0xfd6024，与 SDK 结构体名义（ICH_CON0..3=0xfd6024/28/
   2c/30）**一致，无差一字**。前棒把 0xfd6030 误写成 0xfd6020（0xfd6020 只是
   OCH_CON2，ICH 面不触达它）。前棒 §9.4 的「差一字」勘误本身撤回。
2. **勘误④（批 B 字数）**：前棒批 B「6 字」漏了两块——①**CON0 0xfd6014 的整个
   访问面**（bit16 三处进分支消费 + SFC 挂起域 + 低功耗 save/restore，§2）；
   ②**ICH_CON3 0xfd6030**（通道 24-31 的表字）。批 B 实际 **8 字**：
   0xfd6014/18/1c/20/24/28/2c/30。
3. **勘误⑤（批 C 字数）**：前棒批 C 只列 8 个成员，漏了 **0xf2a614 M2P_INT_IE**
   与 **0xf2a624 P11_SYS_CON0**（本路补扫：两者字面量+基址衍生双零访问，§5）；
   且漏了 0xf2a628 的两处 RMW 访问点（pmu_task 开头+低功耗恢复域，§5）。
4. （复核确认）前棒批 B 的 OCH 三字范围、槽搜索比较消费、resolver RMW 保留位
   消费、「0x6019a1a 硬编码 r2=33 恒 case32」触达条件——本路逐一重验**全部成立**
   （0x6019a28 `li.u8 r2,33` 指令锚定复核）。

## 2. 批 B 访问面全表（8 字，全部 [静态·本路] 指令锚定）

JL_IOMC（br28.h：`{CON0, OCH_CON0/1/2, ICH_CON0/1/2/3}`，基 0xfd6014）＝
GPIO 输入/输出通道映射表。io_omap.h `FO_GP_OCHn=(n<<2)|BIT(1)`（输出通道编码，
值≥2）、io_imap.h JL_IMAP 字（0xfd5c00+n*4，FI_GP_ICH0..13 有效）互证语义。

### 2.1 CON0 0xfd6014（前棒漏列，本路新钉）

| 站点 | 函数（运行态） | 指令 | 消费链 |
|---|---|---|---|
| 0x6010b84 | 0x6010b58（SFC 时钟重配，boot 期 162,615 已穿） | `[r1+0] &= 0xFFF7FFFF`（清 bit19） | RMW 内部读只进写回。**已穿前缀零停的真因（本路核实）**：前沿全链场景经 dual_case→platform_case 默认 seed `IOMAP_CON0=0` + `port_initialization_projection` 开（probe_platform_startup.py:27/35，dual_case 不 override）→ additional_configuration 通用容器服务本 RMW（0&~bit19=0 落盘）。同守卫块的 [0xfd1c04]=3 已执行（HANDOFF 162,615）证明块未被跳过——先前「守卫跳过」假说不成立，**0xfd6014 在当前世界已非 unmapped** |
| 0x60c508a/9c/bc | 0x60c5068（=0x12B890，SFC 挂起助手 [文档：路 AP]） | `r5=[r9]; if((r5&0x10000))…×2` | **读值 bit16 进分支 ×2，分支选写地址**（0xfd5308+8 vs +512、r1+520 vs −8——0xfd5300 域 RMW 目标） |
| 0x60c51ce/d0 | 0x60c51a2（=0x12B9CA，RAM code） | `r1=[0xfd6014]; if((r1&65536)) goto` | **读值 bit16 进分支**，两臂分别 RMW 0xfd5340 / 0xfd5540 域 |
| 0x60c54fc | 0x60c54ac（=0x12BCD4） | `[r0+0] \|= 0x2000`（置 bit13） | RMW 内部读只进写回 |
| 0x60c656a/a0 | 0x60c6562（=0x12CD8A） | `r1=[r8]; if((r1&65536)) goto 66` | **读值 bit16 进分支**（0xfd5308 域 RMW 序列形状选择） |
| 0x60c6aea | 0x60c6990（=0x12D1B8，低功耗**save** 大函数） | `r1=[0xfd6014]; [0x12A5A4]=r1`（起 8 字连读） | **跨地址传播（RAM 快照）** |
| 0x60c6dbc/e14 | 0x60c6c92（=0x12D4BA，低功耗**restore** 大函数） | `[r4]=r0`（arg）+`[r4]=r0`（RAM 值） | 整字写（生产者） |

模型现状（本路核实，比前棒认知更进一步）：`IOMAP_CON0=0xfd6014` 已在
br28_model.py:385 定义，开 `port_initialization_projection` 时进
additional_configuration（br28_model.py:7239），且**前沿全链场景已经在 seed 0**
（probe_platform_startup.py platform_case 默认 `iomap=0` → `registers={IOMAP_CON0:0}`，
probe_dualcore.dual_case 不 override）——即 **0xfd6014 在当前世界已是活容器**：
RMW/写落盘、读回=最后写值（初始 0）。三处 bit16 分支消费者到达时自动读到
0 → 走「未启用第二功能」臂——**批 B 到达时 CON0 无需任何新动作**；唯一注意点
是 0x60c54fc `|=0x2000` 落盘后 bit13=1、bit16 仍 0，自洽。批 B 真正 unmapped 的
是 OCH/ICH 七字（0xfd6018..0xfd6030）。

### 2.2 OCH 三字 0xfd6018/1c/20（输出通道映射，16 槽×5 位）

| 站点 | 函数 | 指令（槽→字映射：slot<6→CON0、6..11→CON1、12..15→CON2，基址 r7=r8−8/r10=r7+4/r8=0xfd6020 算术衍生） | 消费链 |
|---|---|---|---|
| 0x601966e/70/74/78 | 0x6019634（OCH 槽搜索，唯一调用者 0x60199d0＝0x60196e8 case 体） | `r1=[r1+0]; r0=r1>>r0; r0&=0x1F; if(r0!=r4) goto` | **读值进比较分支**（匹配→call 0x6019618 写侧+清 RAM 位图 0x126424） |
| 0x60013ae..c2 | 0x6001356（OCH resolver，唯一调用者 0x600180a＝0x6001456 pfun 分发器 case） | `r2=[r0+0]; r1=r2&~(31<<r1); r1\|=r3; [r8+0]=r1` | RMW：读值只承载保留位（**不进分支**——槽选择分支在 RAM 位图 [0x126424] 上） |
| 0x60c6afa/b04/b0e（+b18/b22/b26） | save 0x60c6990 | `r1=[r0+4/8/12/16/20/24]; [RAM]=r1` | **跨地址传播（RAM 快照）** |
| 0x60c6dc6/dd0/dda/de4/dee/df8/e02（+首写 0x60c6dbc） | restore 0x60c6c92 | `[r4+4/8/12/16/20/24/28]=r0`（[r4+0]=arg；值来自 RAM 快照） | 整字写（生产者，8 字全覆盖） |

### 2.3 ICH 四字 0xfd6024/28/2c/30（输入通道映射，32 通道×4 位 nibble）

| 站点 | 函数 | 指令 | 消费链 |
|---|---|---|---|
| 0x60196c2/c4/c8/cc/d0..e2 | 0x6019692（ICH 槽搜索，12 个调用点全在 0x60196e8 case 体：0x6019972..9aa 奇数通道 1..11、0x60199d8..9f0） | `r1=[r1+0]; r0=r1>>r0; r1=r0&0xF; if(r1>13) goto 返回; r0=0xfd5c00+(r1<<2); call 0x601968c（[r0]=0）; [0x126428]&=~(1<<r1)` | **读值进分支 + 跨地址传播**（nibble 选 JL_IMAP 清零目标字 + RAM 位图位序）——三重消费，批 B 最强 |
| 0x6001434..4c | 0x60013ce（ICH resolver，唯一调用者 0x6001820＝分发器 case） | `r2=[r0+0]; r1=r2&~(15<<r1); r1\|=r3; [r8+0]=r1` | RMW：读值只承载保留位 |
| save/restore | 0x60c6990 / 0x60c6c92 | 同 OCH（0xfd6024/28/2c/30 四字读写） | 跨地址（RAM）/生产者 |

（ICH resolver 另写 JL_IMAP：0x60196d8 `r0\|=0xfd5c00` 后 call 0x6001332
`[r4]=r3` 写映射值——JL_IMAP 63 字白名单内 [文档：路 V]。）

## 3. 批 B 触达面（三组，全部不在 LED→S19 立即路径）

1. **引脚卸载 switch 0x60196e8**（tbh 72 案例）：LED 路唯一调用者 0x6019a1a
   **硬编码 r2=33**（0x6019a28 `li.u8 r2,33` [复核成立]）→ 恒 case32→0x6084eac
   （USB_IO，已建模）。其余 4 调用点 0x60342e2/312/37a/396 全在函数 0x6034274
   （RAM 表驱动批量重映射域），r2=表值 `[r8+1580/1592/1604/1616+r4<<2]`（运行态
   数据）——**静态不可解，运行观测项**。
2. **GPIO pfun 分发器 0x6001456**（tbh，间接调用面，resolver 是其 case）：boot
   前缀已穿 uart_init 未停 [文档：路 V：输入侧走 0x609EAB8 查表臂] ⇒ 该域
   resolver 读点已穿前缀零执行。
3. **RAM-code 低功耗/SFC 域**（0x12B890 挂起 / 0x12D1B8 save / 0x12D4BA restore /
   0x12CD8A / 0x12B9CA / 0x12BCD4）：全部在低功耗挂起-恢复路径，健康 boot 前缀
   不到达。唯一非 RAM-code CON0 站点 0x6010b84（SFC 时钟重配内）在已穿前缀
   **真实执行**（同守卫块 [0xfd1c04]=3 已执行 [文档：HANDOFF 162,615 段]）且
   从未停——本路核实其服务来源是场景 seed IOMAP_CON0=0（§2.1），非跳过。

**结论**：批 B 与批 A（LED 效果机链）零交集；批 A 放行不改变批 B 触达条件。

## 4. 批 B 三选一预案（含 fixture 值与不敏感性预判）

| 域 | (a) 容器 | (b) 具名 fixture | (c) live/严格停 | 裁决 |
|---|---|---|---|---|
| CON0 0xfd6014 | 已是活容器（场景 seed 0） | 值语义已定（0=无第二功能，bit13 写自洽） | — | **零动作**（§2.1 核实） |
| OCH 三字 | ✗（读值进比较分支） | ✓ 0x0 | 真需要映射真值时 | **(b)** |
| ICH 四字 | ✗（读值进分支+跨地址） | ✓ 0x0（副作用见下） | 同上 | **(b)** |

**(b) 的值与不敏感性预判（synthetic-not-measured）**：
- **OCH=0**：0x6019634 十六槽全读 0→`0>>s&0x1F=0 ≠ r4`（r4=FO_* 编码值≥2，
  0 非法映射值）→不匹配臂 16 次、零写副作用、≈10² 模型步穿。resolver
  0x6001356 读 0→保留位全 0→写入纯新值（映射建立本义）。**不敏感方向**：任一
  槽非 0 且 ==r4 会触发写侧 0x6019618（调 0x6019574 DUTY 配置器等）+位图清位
  ——行为可分叉，0 值=「无占用」的最小语义。
- **ICH=0**：nibble=0→`0>13` 假→**有写副作用**：清 JL_IMAP 字 [0xfd5c00]+位图
  bit0（0xfd5c00 在路 V 63 字白名单内，幂等清零可接受）；≠「零副作用」，预案
  报告需写明。nibble 1..13 会改清零目标字——**值敏感点=IMAP 目标选择**。
- **CON0=0**：bit16=0 走「无第二功能」臂（SFC 挂起域写 0xfd5308+8 等）；若真值
  bit16=1 走 +504/+520 臂——**值敏感点=0xfd5300 域写目标**。0xf2a610 前例的
  LED_CLK_SEL 位段（bits5-7）在 CON0 无消费者（全 app 无其他位段读者）。
- 实施形状：一开关（如 `jl_iomc_map_fixture`）**OCH/ICH 七字** whitelist 全 0
  初值（CON0 已有挂点，见 §2.1）；写后读回=最后写值（save/restore 与 resolver
  RMW 的保留位语义需要它）；显式读具名返回（非拒绝——槽搜索/resolver 的分支
  消费者需要值）。**与批 A 不同批不同开关**（形状不同：fixture vs 容器；
  触达面不同：远场 vs 立即路径）。

## 5. 批 C：P11_SYSTEM 12 字全表（p11_sfr.h `P11_SYSTEM_TypeDef`，基 0xf2a600）

字面量+基址衍生双扫描 [静态·本路，p11scan.py]。运行态地址=flash−0x60c3e18+0x12a640。

| 字 | SDK 名 | 访问点（全部） | 读值去向 | 处置 |
|---|---|---|---|---|
| 0xf2a600 | P2M_INT_IE __RW | 0x6069204 读（pmu_task）；0x60c6858 读→RAM 0x129D70+0x60c6864 写=2（0x12D072 软关机进入）；0x60c7514 读→RAM、0x60c7520/0x60c7570/0x60c78e0 写（0x12D4BA 恢复）；0x60c672e 写（0x12CEB8 K1/K2 cleanup [文档：路 BV 母本]） | 进 AND→**bit3 事件分支**（pmu_task）+RAM 快照 | 登记（pmu 到达时随 PND 一起投影） |
| 0xf2a604 | P2M_INT_SET | **零访问**（双扫） | — | **不建模论证闭环** |
| 0xf2a608 | P2M_INT_CLR __RW | 0x606920a RMW `\|=`（pmu_task）；**0x60c6488 写 0x1000（喂狗 ack 清，已建模 p2m_mailbox [文档：BM §3]）**；0x6067906 写=PND（power_init，**wake 门控**：0x6067900 `if(r0==0) goto` 跳过，r0=call 0x60674aa wake 判定，首 boot=0 [文档：路 W ①]）；0x60c756e/0x60c78da 写（恢复域，0x78da=0x20） | — | 喂狗面已建模；余员按 kick/清 idiom 扩 |
| 0xf2a60c | P2M_INT_PND __RO | **0x6069202 读（pmu_task 事件环核心：`r4=IE&PND; if((r4&8)==0) goto`）**；0x60c647c 轮询 bit12（喂狗，已建模）；0x6067902 读（power_init wake 门控）；0x60c7558/62 轮询 bit0/bit4、0x60c78d4 轮询 bit5（恢复域死等） | **进分支（事件分发+轮询死等）** | **pmu_task 到达时需事件位图投影**（非容器）：G2 独立决策点 |
| 0xf2a610 | P2M_CLK_CON0 | 0x6019508/0c RMW ×2（在途 sim-p2mclk） | 写回组装 | [在途] |
| 0xf2a614 | M2P_INT_IE | **零访问**（前棒漏列） | — | **不建模论证闭环** |
| 0xf2a618 | M2P_INT_SET | kick 写 ×7：0x602f464=0x80（IRQ68 域）；0x6067a2a=0x8（power_init **wake 门控** 0x6067a28 `if(r0==0) goto`，call 0x60674aa 返回值同上）；0x6069062=0x800（pmu kick [文档：S19 §3.1]）；**0x60c6476=0x400（喂狗 kick，已建模）**；0x60c6732（cleanup）；0x60c6866=2（软关机）；0x60c7522（恢复） | — | kick idiom 已有全套先例 |
| 0xf2a61c | M2P_INT_CLR | **零访问** | — | 不建模论证闭环 |
| 0xf2a620 | M2P_INT_PND __RO | **零访问** | — | 不建模论证闭环 |
| 0xf2a624 | P11_SYS_CON0 | **零访问**（前棒漏列） | — | 不建模论证闭环 |
| 0xf2a628 | P11_SYS_CON1 | 0x6068c88 RMW `&=0xFFFFFFEF`（pmu_task 开头，基址 r2=0xf2a608+32 衍生——前棒漏列）；0x60c73e4 RMW `\|=0x80`（恢复域） | 内部读只进写回（两位站点均无显式读） | **模型已有**：P11_SYSCON1_MASK_SETS（reset 投影 extra_mask_sets，br28_model.py:7211）——到达时核对掩码表覆盖 `~0x10`/`0x80` 两形状即可 |
| 0xf2a62c | MEM_PWR_CON | 0x6067730/52/74/96 RMW `\|=`（power_init **已执行**）；0x6067844 死读（值被下一条指令覆盖）；0x6067e88..be RMW `&=~r0` ×6（power-off 例程） | 死读不消费 | **已建模** mem_pwr_con（RMW 位记录+读回严格停+死读 opaque 门）[文档] |

**power_init 内三站点（0x6067902/0x6067906/0x6067a2a）为 wake 路径专属**：两次
call 0x60674aa（[0x12521C] bit1 wake 判定）返回 0（首 boot [文档：路 W]）时全部
跳过——与已穿前缀从未停在这些点的行为证据自洽。软关机/恢复/cleanup 三函数
（0x12D072/0x12D4BA/0x12CEB8）全在低功耗域，健康前缀不到达。

## 6. 攒批可行性

- **批 A ↔ 批 B：无依赖、不同批**。批 A（CON3 式容器 13 字）放行后的前沿重钉
  一次即可顺带验证批 B「零触达」负例（预期：批 A 放行后的下一停点不在 JL_IOMC）。
  批 B 若需建模（运行态意外触达时），独立开关独立批——fixture 形状与批 A 容器
  形状不同报告键，不合并。
- **批 A ↔ 批 C：无依赖**。批 C 维持登记不建模：五字零访问论证已闭环（0x604/
  614/61c/620/624），0x628/0x62c 模型已有，pmu_task PND 面到达时是**独立 G2
  决策点**（读值进事件分支，需要的是「pmu 事件位图投影」设计，与容器/fixture
  三选一框架里的 (c) live 同类，届时按 s19-final-specs §3.3 idiom 先 codex）。
- **重放验证顺序**：批 A 放行→前沿重钉→（若停点意外落在 JL_IOMC/P11_SYSTEM）
  回读本页对应字预案。一次重放足够，批 B/C 不前置任何开关。

## 7. 与 prior work 交叉核对

| 记录处 | 已有内容 | 本路增量 |
|---|---|---|
| 前棒 frontier-scout-f2a-batch §3 批 B | 6 字+两函数对（0x6019634/0x6019692+resolver） | 修正 ICH 字范围；补 CON0/ICH_CON3 全访问面；补 save/restore、SFC 挂起域消费链；fixture 值+不敏感性预判；触达面第三组（RAM-code 低功耗）钉死 |
| 前棒 §3 批 C | 8 成员登记表 | 补 12 字全表（+0x614/0x624 零访问）；补 0x628 两 RMW 站点与模型开关对应；补 power_init wake 门控三站点；钉 pmu_task PND&IE 读为唯一「读值进分支」核心点 |
| HANDOFF 路 AP（handoff-archive L243） | 0x12B890=SFC 挂起助手（0xfe0200 域序列） | 补其 [0xfd6014] bit16 消费（挂起域还有 IOMC 面） |
| handoff-archive 路 BV 注释 | 0x60c6690=0x12CEB8 K1/K2 cleanup 母本 | 补其 0xf2a600/618 写（IE+kick 对写） |
| s19-final-specs §3.1/3.3 | 喂狗 idiom（kick 0x400/PND bit12/CLR 0x1000）、pmu/IRQ68 kick 登记 | kick 面扩到 7 站点全表；power_init 内 wake 门控 kick 0x8 新登记 |
| br28_model.py | IOMAP_CON0 挂 port_initialization_projection；P11_SYSCON1_MASK_SETS；mem_pwr_con | 确认批 B/C 到达时的模型挂点全部已存在，无新分发需求 |

## 8. 免停认证面（批 B/C 视角补充）

- 批 B 全部触达面（§3 三组）在批 A 放行后的执行流里零交集；JL_IMAP 写
  （ICH 槽搜索/resolver 的副作用）落在 63 字白名单内 [文档：路 V]。
- 批 C 喂狗族（0x12cc7e=0x60c6456 母本）三字（0x608/0x60c/0x618）已由
  p2m_mailbox_ack+wdt_mailbox_feed 域覆盖 [文档：BM §3/WDFEED]。
- 0x60196e8 案例体的 JL_IMAP 整字清零写（0x601980e..0x6019a02）维持路 V 白名单
  结论；线性 asm 在 0x6019774 tbh 表区的 testset 伪迹维持前棒勘误②撤回（本路
  sfrscan 重跑再次复现该伪迹，流式重解无此指令）。

## 9. 诚实边界与未知

1. 全部结论 derived-not-measured；批 B 三组触达面中「RAM 表驱动域 0x6034274
   的 r2 表值」「resolver 分发器 0x6001456 的间接调用者」静态不可穷尽（间接
   call 1189 处），运行观测项。
2. 批 B fixture=0 的不敏感性预判未经验证：OCH 臂（恒不匹配）与 ICH 臂（清
   IMAP 字 0）的行为分叉是静态推断；真值非 0 时的行为差异不建模（真需要映射
   真值时按 (c) 处理）。
3. save/restore（0x12D1B8/0x12D4BA）覆盖的外设面不止 IOMC/P11_SYSTEM（还有
   0xfd5300 域、0xfe0200 域等），本页只钉批 B/C 范围内字；其余域到达再议。
4. 批 C pmu_task 的事件位图投影（PND bit3 等）无任何 SDK 位定义证据
   （p11_sfr.h 只给 __RO/__RW 无位段），届时设计需先 codex 参谋，本页不预设计。
5. 本路一次性脚本 p11scan.py 与其余工具同在 `~/tmp/frontier-scout/`（不进仓）；
   本仓新增本文档与
   [evidence/sim-routes/frontier-scout2-batchbc.json](../evidence/sim-routes/frontier-scout2-batchbc.json)。
