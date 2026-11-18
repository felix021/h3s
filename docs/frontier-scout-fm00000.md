# 前沿侦察 FM00000：modem 窗 CON 头 0xF00000 RMW 停点（实测 42,061,478 @ 0x60befa8）

路 FMSCOUT / sim-fmscout（基线 main c0a520b，worktree
`~/worktrees/h3s/sim-fmscout`，静态侦察，零 tools/ 改动，不跑条件线，不碰
其他 worktree）。2026-11-19 05:20 开工 / 05:45 材料收口 / 05:34-05:37
codex 参谋（gpt-6.2-astra）记录见 §8 / 05:50 定稿。前置停点=路
TIMERIMPL 实测交接
（timerimpl.json：**42,061,478 @ 0x60befa8 unmapped_memory read 0xf00000**，
fn_config 条件臂 `b[obj+6]==0 → [0xF00000] |= 0x400` 的 RMW 内部读）。
反汇编沿用仓外既有
`~/code/headsets/archive/h3s-all/h3s/analysis/anc-cycle-20261109/app-linear.asm`
（与 disassemble_app.py 钉死源一致，加载基址 0x06000100；li 立即数双显示
勘误沿用 timer §1）。一次性脚本内联 python，不进仓。
证据 JSON：`evidence/sim-routes/frontier-scout-fm00000.json`。

## 0. 停点与一句话结论

- 停点：**42,061,478 @ 0x60befa8**，`[0xF00000] |= 0x400`（rmw.or.modimm
  整字 RMW）的**内部读拍**落 unmapped_memory。位置：fn_config
  （0x60bef46，audio_sync 对象配置函数）尾段条件臂；IPMASK=6 临界区内
  （creator 0x60befae → request_irq(88)〔已过，ICU 0xffe12c 读 0x3030→
  写 0x3035 实测放行〕→ fn_config JL_SYNC 四字事务〔已过，CON1=0x00C80000
  等四值全兑现〕→ 本臂）。
- **读值来源核心结论（问 1）**：本 RMW 是**执行前缀内对窗内头字
  0xF00000（及整个头段 0xF00000..0xF000FF）的第一次访问**——反证法：
  模型对该窗头段零覆盖（timer §0；窗内尾段 0xF00130/40 两写已发生但
  走 TIMERIMPL 容器**精确地址分发**，不触头字），任何更早的头字读写
  （含 btctrler 任务首跑的 400B 整窗快照读 0x6048038——它会读到头字）
  都会把仿真停得更早。因此 RMW 读值**无任何链上来源**：不是 bulk 影子
  （快照读未执行、对应恢复写 0x60481bc 在下电路径）、不是先前单字写
  （不存在）。读值=硅初始态，闭源 modem SDK 零定义，**静态不可知**——
  模型侧必须显式合成初值（先例 FD610C CON3/SRCSYNC CON0、CON1 均合成
  初值 0）或以事务方式服务本 RMW。头字旧值不可追溯≠硅上此刻为 0：
  先前两次尾字写是否间接影响头字，模拟无法回答（codex 措辞修正，§8）。
- **RMW 三拍（问 2）**：①内部读 [0xF00000] 32 位对齐（通用 mmio 分发，
  现停点）②OR 0x400（bit10）③整字写回。另一分支 b[obj+6]!=0 →
  goto 0x60befac 直接 pop 返回，**零窗口访问**；两臂在 0x60befac 汇合，
  放行后下游完全一致。b[obj+6] 运行时已闭包=0（timerimpl 停点即臂内，
  臂命中）；溯源链 §4。
- **新发现（timer 侦察未记）**：fn_config 有**两个调用者**——creator
  0x60bf00c（当前链）与节点 start 函数 0x607e460 内 0x607e46c（框架节点
  walker 0x6073514→0x6073558 派发，链下游）。同一 obj（obj+6 创建后
  不可变）、同臂、**同一指令 PC 0x60befa8**——本 RMW 放行后大概率
  **执行第二次**（容器服务幂等 0x400|0x400=0x400）。fn_resume
  0x60bf14e 是**不同 PC** 的同款镜像臂（同 b[obj+6] 门、同 |=0x400），
  链外（pause/resume 周期才到），按「同址新消费者须重审」应拒。
- 三选一裁决（codex 定稿，§5）：**B——恰 0xF00000 单字容器**：合成
  初值 0（显式假设）+RMW 读拍返回当前容器值（**账本区分首读来源
  synthetic 与写后内部读 last_write**——codex 修正：每次临时返回 0
  会掩盖第二次是否真正读到第一次的结果）+写回更新+**授权单位=一条
  完整指令**（核/PC=0x60befa8/有效地址/对齐 32 位/rmw.or.modimm+立即
  数 0x400 五者全匹配——同 PC 的普通 ld/st 或不同掩码不借用权限，
  codex 修正）+其他 PC 显式读拒绝+邻接成员（头段全部其余偏移、尾段、
  400B 整窗）一概不承接+无副作用（不产生 IRQ88/不改其他寄存器）。
- 放行后下一停点**静态预判**（§6）：墙序=fn_config RTS→三个纯 RAM/
  ROM 调用→creator RTS→adapter 0x607e4fe 续走（全 RAM/ROM）→框架
  0x6077dd6 返回→**表派发段（静态缺口）**→节点 walker→第二次
  fn_config/RMW（容器内幂等）→eq 设计族 copex 站点 0x6072bb8
  （mode16）/0x6072d1a（mode24）——**预判下一停点 dsp_mode_not_modeled**
  （copex_exp22_model 只服务 n==22，16/24 显式 fail-closed），除非框架
  续走中有更早的未建模 SFR 读（表派发段静态不可见）。

## 1. 块定义与访问面（复用 timer §1 勘误 + 本站新扫）

**块身份**（timer §1 勘误沿用）：0xF00000..0xF0018F 是 btctrler 闭源库
硬件窗口（BTMAP §2「modem 窗·0xF00000」22 站条目），SDK 全头文件零
定义；0xF00000 是窗头（CON 头 [推断]，取自其 RMW/整字访问形状与
「配置+状态」的 BTMAP 域标签）。尾段（0xF00100+）逐站审计见 timer §2，
本站不重复。

**基址装载总清单**（本站复扫，含 `#` 尾注格式补漏）：li 双轨（十进制
15728640+十六进制 0xF00000，含 `r = 0xF00000  #` 变体）共 **22 站**
（timer §1 同数）：0x6032332/0x6048038/0x60481bc/0x6048348/0x60488e0/
0x6049d1e/0x605075c/0x60508fa/0x60509ec/0x6050fb4/0x605120e/0x6051650/
0x6053bc2/0x6055ab4/0x605d3ee/0x6085324/0x6085518/0x6085a92/0x6085bb0/
**0x60befa4（fn_config，本站）**/0x60bf14a（fn_resume）/0x60bf238
（IRQ88）。基址下游 40 指令窗偏移深扫（timer §1 ⑥同法）：最大偏移
0x24（0x6055ab4，AFH 域 +C/+10/+14/+24 对），无 ≥0x100 者——头段基址
不间接触达尾段。

**数据字面量扫描（本站新做，盲区收口）**：app.bin 全量 LE32 扫窗值
[0xF00000,0xF0018F) 共 54 处命中：代码区命中全部为 li 立即数字段本身
（与站点一一对应）；**数据区命中判全部假阳性**——0x60a4450-0x60a4818
是 0x55555 步进 16 点满量程斜坡表（0xF00000 为 ramp 采样值，8 处，
间距 0x60 结构性重复），0xF000CF@0x602d408、0xF000E7@0x60014bc 等
非对齐值不可能是寄存器地址（音频采样数据域），0xF00004@0x60c85e4、
0xF000A8@0x60c7fc6、0xF000E0@0x6098b86 落数据段无代码引用佐证。
**timer §7「表驱动基址」盲区担忧对 0xF00000 不成立**（收口而非扩大：
唯一确证的寄存器基址表是 srcsync 的 hw_res 0x60a29c8，条目为
0xFE25xx 系）。（注：扫描用 candidate app.bin sha 8f594d59——官方
18f3d573 未在本机，两者仅差 key 表 3 字节+CRC〔repo 不变量 2〕，字面量
域结论等价，如实标注。）ROM/p11_code.bin 沿用 timer §1 ④⑤：ROM 头段
0xF00064/D4/D8/DC/EC 五处 16 位拼装（非 +0），p11 零命中。

## 2. 头字 [0xF00000]+0 逐站审计（全部通读）

| 域 | 站点 | 访问形状 | 链上状态 |
|---|---|---|---|
| **audio_sync** | **0x60befa4/a8（fn_config，本停点）** | **RMW \|=0x400**（b[obj+6]==0 臂内） | **链上，42,061,478 停点** |
| audio_sync | 0x60bf14a/e（fn_resume 0x60bf12e） | 同款 RMW \|=0x400（同 b[obj+6] 门镜像臂；0x60bf142 先做 JL_SYNC CON \|=0x1000000） | 链外（pause/resume 周期） |
| audio_sync | 0x60bf238（IRQ88 handler） | RMW \|=0x400（abs≥3 分支，timer §2） | 无源不可达（IRQ88 无硬件事件源） |
| btctrler | 0x6048348/0x605075c/0x60508fa/0x60509ec/0x6051650/0x6085bb0 | RMW \|=0x4（bit2） | 域未启动零执行 |
| btctrler | 0x6050fb4/0x605120e/0x6053bc2/0x6085518/0x6085a92/0x6085324 | RMW &=0xFFFFFF8F（清 bit4-6；0x6085324 后接 \|=r10 计算位+\|=0x8） | 同上 |
| btctrler | 0x6032332 | RMW &=0xFFFFEFFF（清 bit12） | 同上 |
| btctrler | 0x60481bc | memcpy 写回 400B（含头字；下电恢复） | 同上（下电路径） |
| （读） | 0x6085324（0x608537e r0=[r12+0]）/0x6049d1e（读 +4/+20 旁路） | 读值进位段组装 | 同上 |

**位归属分解（derived，不解读位义）**：bit10（0x400）全 app 仅
audio_sync 域三站写；bit2/bit3/bit4-6/bit12 为 btctrler 域——bit10 是
audio_sync 私有位。头字是 btctrler 与 audio_sync 共用的多域控制字
[推断]。

## 3. RMW 语义与条件臂两分支（问 2 展开）

- **三拍**：①内部读（通用 mmio，unmapped 停=现停点）②OR 0x400
  ③整字写回（若到达，会落 unmapped write）。容器须给①一个显式值，
  ③作为容器首次记录写。
- **b[obj+6]==0 臂**（运行时命中）：执行 RMW 后 0x60befac pop 返回。
- **!=0 臂**：`0x60befa2 if (r0 != 0) goto 8` → 0x60befac 直接 pop，
  零窗口/零 JL_SYNC 访问差异（JL_SYNC 四字在两臂共同前缀 0x60bef52-9e
  已完成）。**两臂唯一分叉就是这一条 RMW**，放行后两条路都走到
  fn_config RTS→0x60bf00e 完全一致。
- **b[obj+6] 溯源（静态链+运行时闭包）**：creator `0x60befbe
  b[r4+6]=r5`（唯一写者，r5=creator 首参）→ creator 首参 = adapter
  `0x607e4f0-0x607e4f6` 三态门（r1=b[adapter+92]=b[param+4]：**0=跳过
  整个 creator 调用**、2→arg=1〔臂不取〕、其他→arg=0〔臂取〕）→
  param blob 由框架函数 0x6077dd6 在 0x6077f4c-0x6077f9c 装配
  （b[param+4]=b[sp+24]=b[[[r4+4]+20]+9]，节点属性字节）。运行时闭包
  （timerimpl）：臂命中 ⇒ b[obj+6]==0 ⇒ param+4∉{0,2}（具体值未观测，
  1 最可能 [推断]）。
- **obj+6 不可变性**：audio_sync 域全函数通读（creator/fn_config/
  fn_stop 0x60bf03c/fn_pause/fn_resume/install 0x60bf030/getter
  0x60bf16a+/IRQ88），除 creator 外零写者——两次 fn_config 执行（见
  §4）必然同臂。

## 4. 调用链（含 timer 侦察未记的第二调用者）

- creator 链（jlsync §4 沿用）：creator 0x60befae 唯一直接调用者
  0x607e4f8（adapter）←0x6077f9e（框架函数 0x6077dd6 尾部）←表派发
  （0x6077dd6 零直接 call 引用，静态缺口）。
- **fn_config 双调用者（本站新钉）**：①0x60bf00c（creator 内，当前
  链）；②**0x607e46c**——节点 start 函数 0x607e460（`r0=[r4+156]`
  读 adapter 存的 audio_sync obj→重调 fn_config→b[r4+16]=1 启动位），
  由框架节点 walker 0x6073514（0x6073558 call）派发。两路同一指令
  0x60befa8、同一 obj、同臂（§3 不可变性）。
- **放行段全调用目标审计（逐个反汇编通读）**：0x6064f18（纯 RAM：
  b[r0]=2/h[+12]=r1/d[+4]=0）、0x60735d8（list_add_tail@0x125FEC，
  纯 RAM）、0x12b5b0（ROM exit-crit，IPMASK 恢复，前缀已演练的临界
  区对）、0x60bef00（注册表查找/插入：RAM 链表走查+ROM crit 对）、
  0x60bf030（回调安装：[obj+32]=adapter、[obj+36]=0x607f5e2〔audio_sync
  事件回调入口，jlsync 文档「0x607f5f6」实为其体内分支指令地址，本站
  勘误〕，纯 RAM）、0x6064162（纯 RAM：b=3/b[+12]=1/b[+13]=255/d[+4]=0）、
  0x6073502（list 空探针，纯 RAM 读）。**零 MMIO/copex 风险点**。

## 5. 三选一（codex 参谋定稿：B）

| 方案 | 裁决 | 依据 |
|---|---|---|
| A 全窗/头段建模（400B 或 0xF00000-0xF000FF 影子语义） | 否 | 其余 399 字节零语义证据（btctrler 域位义全部 derived）；btctrler 域未启动零消费者验证面；**bulk 快照读 0x6048038 一旦将来执行要读整个窗——A 等于替硅合成 400B 初始状态并静默喂给快照**（比单字合成放大两个数量级，且快照值还会经 0x60481bc 写回污染窗口） |
| **B 恰 0xF00000 单字容器** | **采用** | 链上触达恰一指令（0x60befa8 RMW，双调用路径同 PC）。语义逐条：**合成初值 0**（显式假设 derived-not-measured，FD610C CON3/SRCSYNC CON0·CON1 先例——非硅复位值声明；数学上两次写回 x\|0x400 与 (x\|0x400)\|0x400 对任意 x 相等，选 0 只为给出可复核轨迹——codex 论证）；**RMW 读拍返回当前容器值，账本区分来源**（首执行=synthetic 0→写 0x400；第二次同 PC=last_write 0x400→0x400，**两笔写事务都记账保留写脉冲**——数值幂等只说明结果字相同，硅上重复写效果未知〔codex〕）；**授权单位=一条完整指令**（主核+PC 0x60befa8+有效地址 0xF00000+对齐 32 位+rmw.or.modimm 立即数 0x400 五者全匹配——同 PC 的普通 ld.w/st.w/不同掩码不借用 RMW 权限〔codex 修正〕）；其余 PC/核=消费前停 jl_fm_*_pc_not_audited（fn_resume 0x60bf14e、IRQ88 0x60bf238、btctrler 13 站、bulk 恢复 memcpy 写〔memcpy helper PC 非审计点〕全拒，把「同址新写者须重审」做成可执行边界，jlsync/btclk 先例）；**其他 PC 显式标量读拒绝**（本站链上零标量读者，保守拒读与 BTCLK 一致）；**无副作用**：不产生 IRQ88/不推进计数/不改其他寄存器（含窗内邻接成员与已建模 JL_SYNC 域——地址级分发隔离只证明模拟器写隔离，硅上 modem 写是否联动 JL_SYNC 无依据，若下游依赖该联动在消费者处重审〔codex 缺口声明〕）；邻接成员（头段 +4..+FF、尾段、400B 整窗 bulk 两向）一概不承接，将来独立批准地址不按连续区间映射（jlsync 负例②先例）；复位清除容器态；快照保存恢复不扩大权限（M4 往返） |
| C 推迟（停在 RMW 读） | 否 | 墙就在停点上，停住=前沿冻结；btctrler 域「以后一起做」到不了——timer §5 C 否决同款：不放过本 RMW 就到不了链表插入/adapter 续走/eq 设计族及以后的一切 |

**初值 0 的最弱一环声明（本路认同 codex 点名）**：硅初始态不可知；
btctrler 域将来若放行头字其他位 RMW（bit2/3/4-6/12），会把本容器状态
（0|0x400）当基线读——**初值 0 是「链上无观察面能区分」下的最小假设，
不是硅复位值结论**。若将来发现 btctrler 域头字读消费实际依赖真实初始
值，本容器不足以支撑，须先解决该依赖（timer §7 同款条款）。

**预期账本（静态预测）**：本段**至少一次、至多两次**成功 RMW
（0x60befa8：synthetic 0→0x400 一笔写；若节点 start 先行于 eq open 则
第二笔 last_write 0x400→0x400——第二次是否落在本段取决于框架表派发
顺序，运行时定，账本分别记账）、零显式读、零硬件事件；0x400 适合
轨迹验收断言，不据此解读 bit10 含义。后续受阻不撤销已完成写。

**负例矩阵（codex 13 项，落地路照建）**：①TIMERIMPL 保持开、仅关本站
开关→复现 42,061,478 @ 0x60befa8 unmapped_memory 读 0xF00000 逐键；
②新容器首合法 RMW：事务前未写态，账本注明首读来源=synthetic 0、
0→0x400 恰一笔写，报告不得把 0 列为硅复位值；③原站点连续两次：第二
次旧值来源=上次写回（last_write 标签），累计一次合成读+一次写后内部
读+两笔写（第二次是否实际发生单独记账）；④**非零初值对照**（仅测试
换初值 0x1014）：两次均写 0x1414、第二次旧值 0x1414，两臂局部控制流
相同——抓「固定写 0x400」或「每拍清零」实现错误（codex 新增）；
⑤fn_resume 0x60bf14e/IRQ88 0x60bf238 同款 RMW 不同 PC：即使头字已记
0x400 也停在内部读；⑥btctrler 13 站写者逐个代入（|=0x4/清字段/
寄存器操作数/清 bit12）全不能取得旧值；⑦0x6048038 整窗快照读与
0x60481bc 整窗恢复写：首个不支持的实际总线访问处停，头字不输出合成
字节也不接受 RAM 写回字节，**守卫不能只查 memcpy 调用点**（codex）；
⑧首写前后显式 load/独立 store；同 PC 换 ld.w/st.w/不同掩码：全拒，
同值 store 与诊断读不借用 RMW 权限；⑨CPU1 用合法 PC 与指令；PC/核
来源缺失或过期（含 restore 后 None）：全拒——上次 CPU0 留下的 PC 不能
授权本次；⑩byte/halfword/8B/非对齐/跨字：不取整不拆分无部分提交；
⑪+4/+8/+C、0xF00144/48、窗尾、交叉访问 0xF00130/40（继续只认
TIMERIMPL 原核准写者）：本容器不认领；⑫写后推进时间+保留已注册
IRQ88+复位：本事务贡献的计数/pending/IRQ 投递/其他寄存器变化为零，
复位清除写回值与来源，下次合法首读重标 synthetic；⑬首笔事务前后
保存恢复快照：续跑首读来源/写回值/次数与直跑一致，恢复后未重新确认
当前指令即拒，旧快照不放宽 PC/核/指令权限。依赖破坏：jl_fm 开而
jl_sync/jl_btclk 关→require 先炸（推进顺序依赖沿用）。

## 6. 墙序（放行后，静态预判）

1. （放行）0x60befa8 RMW 完成：读 0→写 0x400（容器首记录）→
   0x60befac pop。
2. fn_config RTS→0x60bf00e：r0=obj+40,r1=0 → 0x6064f18（纯 RAM）→
   0x60bf01a r0=obj+24 → 0x60735d8 list_add_tail（纯 RAM）→
   0x60bf022 → 0x12b5b0 ROM exit-crit（IPMASK 6→恢复）。
3. creator RTS（r0=obj）→adapter 0x607e4fe：[adapter+156]=obj→
   0x607e504 call 0x60bef00（注册表查找/插入）→0x607e50e call
   0x60bf030（回调安装）→0x607e518 call 0x6064162→b[r4+1]=0/
   b[r7+1]=1/[r8+0]=r4→adapter RTS。
4. 框架 0x6077dd6 立即返回（r0=0）→**表派发段（静态缺口：调用者
   不可见）**。
5. 节点 walker 0x6073514→0x607e460 节点 start→0x607e46c **第二次
   fn_config**（同臂）→**第二次 0x60befa8 RMW（容器内幂等）**→
   JL_SYNC 四字重跑（已建模容器内）。
6. eq 设计族 copex 站点 **0x6072bb8（mode16）/0x6072d1a（mode24）**：
   **预判下一停点 dsp_mode_not_modeled**（copex_exp22_model 契约
   n==22 精确，16/24 专用停点 fail-closed）。**顺序不确定**：walker
   （第二次 RMW）与 eq 节点 open（copex 墙）先后静态不可判（都隔着
   表派发段）；若 eq open 先行，下一停点直接是 copex 而第二次 RMW
   更晚。中间是否有更早未建模 SFR 读（表派发段）静态不可见——
   诚实边界。
7. ICU 0xffe12c 已知全 32 字（initial_irq_config=[0]*32）不再构成墙
   （timerimpl 已实证 request_irq 族 RMW 放行）。

## 7. 诚实边界与开放项

- **静态扫描盲区**：0x6077dd6 框架函数调用者表派发不可见（步骤 4-6
  顺序运行时定）；动态计算地址/跨函数传参（0x605d3ee r11=0xF00000
  后 30 指令窗内无直接 [r11 访问，疑作参数传给窗口访问器——域标签
  归 btctrler，链外）；ROM 16 位拼装只排除尾段（timer §1 ④），头字
  +0 的 ROM 访问未单独排除——ROM 仅启动早期执行、当前链深居 app 域，
  风险低但非零；q32s(P11)/DMA 照 timer §7 口径无证据亦无排除。
- **数据字面量判读**：数据段 6+8 处命中判假阳性（§1），其中
  0xF00004@0x60c85e4/0xF000A8@0x60c7fc6/0xF000E0@0x6098b86 无代码
  引用佐证纯属形状归纳（非对齐同类值佐证采样数据解释），不能 100%
  排除某未知表以 0xF00004 为基址——但 +4 字不在本站容器范围，风险
  由「邻接不承接」边界兜住。
- **「btctrler 域未启动」**：运行事实引用（BTMAP/park 世界+本站反证
  法 §0），非本路重放；若将来某链提前触达 btctrler 头字站，PC 守卫
  先停（负例⑥）。
- **第二次 RMW 预测**：节点 start 路径（0x607e460/0x6073514 walker）
  为静态可达性论证（call 图钉死），「放行段内实际执行」依赖表派发
  顺序，运行时验证。
- **初值 0**：显式模型假设（§5 最弱一环），不证明硅复位值、不证明
  bit10 写入无硬件副作用（副作用观察面三问已答：唯一链上读者=本 RMW
  自身；IRQ88 无源；bulk 快照读若执行即停）。
- 停点步数 42,061,478 为 TIMERIMPL 实测（非本路推算）；本路零场景
  运行（红线）。
- official↔b79 parity：纯静态侦察路无放行段，不重跑（预期 delta 0
  边界沿用）。

## 8. codex 参谋记录

gpt-6.2-astra，2026-11-19 05:34-05:37，
`~/tmp/codex/202611190534-fmscout/`（briefing.md/final.md/log.md；只读
分析）。裁决原文要点：「选 B。只放行 CPU0 在 0x60befa8 对 0xF00000
执行的整字 |= 0x400。首拍可用显式合成的 0，同一站点再次执行时读上次
写回值，照常发出第二次写事务。」

**五条修正/增益全部采纳**：①措辞纠错——「第一次触达头字」而非
「第一次触达全窗」（0xF00130/40 两写已在窗内发生；头字旧值不可追溯
≠硅上为 0，先前尾字写是否间接影响头字模拟无法回答）；②初值 0 的
数学论证（两次写回对任意 x 相等，选 0 只为可复核轨迹）+ **账本区分
synthetic/last_write 两种读来源**（每次临时返回 0 会掩盖第二次是否
真正读到第一次的结果）；③**授权单位=一条完整指令**（五元组匹配，
同 PC 的 ld/st/不同掩码不借用）；④负例新增**非零初值对照**（0x1014→
两次均写 0x1414，抓固定值/清零实现错误）+ bulk 守卫「不能只查 memcpy
调用点」；⑤副作用缺口声明——模拟器地址级分发隔离只证明写隔离，
**硅上 modem 写是否联动已建模 JL_SYNC 无依据**，下游若依赖该联动在
消费者处重审。墙序：同意「第二次 RMW=可观测路标、copex mode16/24=
候选停点、先后开放」，补充「退出临界区后可能先交付已有源中断」，
对账三件事=0x607e46c 是否调到同一对象/0x60befa8 完成几次及每次旧值
来源/首个停点 PC 与原因。

**本路与 codex 无实质分歧**（独立判断同为 B）。本路 briefing 未含而
codex 独立复核补强的：A 否决的可传播性论证（离链 memcpy 把合成字节
搬进 RAM 后普通 RAM 读绕开寄存器守卫）；C 表述为「关开关臂留作严格
对照」。本路补充而 briefing 已含的自查项（fn_config 双调用者、
fn_resume 镜像臂、数据字面量假阳性收口、放行段全 RAM 审计）codex
照单采纳并引用。
