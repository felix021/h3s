# EQ 修复候选离线验证现状矩阵（路 CE / sim-eqstatus，只读文档）

2026-11-13。本页把散在各路的验证证据系统化为一张**验证矩阵**：EQ 修复候选的每个
差异点 × 每个验证层，逐格标注状态与证据等级，并给出 r2 失败机制的定位结论、「从
现在到完整验证」的最短路径和用户决策选项。这是**面向决策的阶段性综合材料**，不是
新的验证执行记录。**只读**：未改 tools/ 任何代码、未跑改报告的 probe；全部结论
转录自既有文档（docs/eq-payload-semantics.md〔路 BT〕、HANDOFF 路 BW/CA/CP 条目、
docs/d2-audio-first-scene.md〔路 BX〕、docs/eq-fence-repair.md、docs/eq-toggle.md、
docs/d1-acceptance.md〔路 BR〕、docs/d2b-framework-task.md〔路 CI/CM/CP〕及启动
前缀 parity 记录〔路 Y/AJ/BK〕）。

**红线不变**：候选 SHA256
`67ba97efb83a15ec9df14de574667c6a77126d43ac6d8e5f5e645cb91126f222`
维持 UNTESTED、未刷入；本页不构成刷机建议，不改变任何授权状态；真机动作一律需
用户显式授权。全部证据等级为 **derived / synthetic-not-measured**——没有任何一条
是硅片实测。

证据等级约定（沿用各路口径）：**[静态]** = 指令锚定反汇编/镜像字节比对；
**[回放]** = 钉死模拟器隔离函数/前缀回放（桩显式）；**[场景]** = opt-in 合成
fixture 场景（synthetic-not-measured）；**[文档]** = 既有 docs/HANDOFF 钉死条目。

## 0. 候选身份与差异点全集

候选 = 历史 r2（SHA `c8dae523…`，已刷失败、继续禁止刷入）**只修 fence 上限立即数**
后的独立镜像：保留原 337B 编译载荷、五个钩子、ANC 两态与提示音资源，整包相对
已验证 b79 共 428 字节变化。相对 b79 的差异点恰为四类（路 BR §3.1 十游程复核 +
路 BT §6 三方字节比对 [静态]）：

| # | 差异点 | 位置 | 内容 |
|---|---|---|---|
| A | 5 个钩子站点替换 | 0x60059ce / 0x6025030 / 0x602510e / 0x6025364 / 0x6078846（各 6B） | 原指令→跳转/调用载荷（0x60e3092/0x60e30b0/0x60e30c2/0x60e3074/0x60e30a0） |
| B | fence 立即数 | 0x6034e98（`li.u32` 高 32 位） | `0x060c3338`→`0x060e31c5`，恰 3 字节立即数差（`c1ff38330c06`→`c1ffc5310e06`） |
| C | config.dat index/表 CRC | 明文 0xe2ed3..0xe2ed5 / 0xe2ee1 | 原末项结束标志改非末项 + 追加文件所需的索引/CRC 元数据 |
| D | 337B 载荷追加 | 0x60e3074（flash+0xe5094，JLFS 文件 `eq_toggle.bin`） | 102 条指令 + 19B 字符串，sha256 `f4e9501e…`；boot-info 覆盖 +370B、全链 CRC 更新 |

## 1. 验证矩阵（差异点 × 验证层）

列定义：**静态证明**＝反汇编/字节级（不改机器状态）；**函数级回放**＝隔离函数
场景；**控制面场景**＝连续上下文 handler 入口注入（状态机/掩码/入队边界）；
**事件注入**＝注入机制本身的覆盖（事件对象 fixture、分发、门）；**三镜像
parity**＝official/b79/候选同前缀同场景逐步差分；**D2 效果节点**＝node+35/ctx[35]
状态对比（「EQ 行为差」的最后一块，BX D2-B）。状态：✅ 已验证（附路/文档）、
⏳ 未开始（依赖 D2 链）、❌ 未开始（有明确阻塞）、➖ 不适用。

| 差异点＼层 | 静态证明 | 函数级回放 | 控制面场景 | 事件注入 | 三镜像 parity | D2 效果节点 |
|---|---|---|---|---|---|---|
| **A. 5 钩子站点** | ✅ BT §2.1 站点表（原始/替换 6B、触发条件、目标 codec 核对）+ BR §3.1 十游程 + BW S1 站点字节零差异设计验证 [静态] | ✅ 五入口在修复 fence 下进入真实补丁、旧 fence 下全 BLOCKED 保持 REJECTED（eq-fence-repair.md probe 五受限入口）[回放] | ✅ **4/5 站点**执行：0x6025364/0x60059ce/0x6025030/0x602510e（BW S1-S4）；0x6078846 见 D2 列 [场景] | ✅ BW S1 从 handler 0x60252c6 入口注入双击、S3 直调 0x60057e6(r0=18)；CA 证音频入口（handler[47]）注入机制可行 [场景] | ✅ 启动前缀 289,849（严格）/447,490（条件）parity：D1 窗口零钩子消费（BR §3.5 P4-P7 动态）[文档：路 Y/AJ/BK] | ✅ 路 CP/D2-C：descriptor walk（0x6074938, scene 0xd96f）建真节点→cmd-11 命中→bypass=0 重开 0x60787ac→钩子 0x6078846 真实执行覆盖（三镜像；repaired 进 eq_open_config） |
| **B. fence 立即数** | ✅ BT §1.1：r2 值 0x060c3338 对全五入口**必拒**（首目标 0x60e3074 超界 130,364B，不依赖端点策略）；修复值 0x060e31c5 在半开/闭合/整指令跨界三种端点策略下均充分（最小充分界 0x060e31b2，裕量 19B＝载荷外延）；硅端点未认证 caveat [静态] | ✅ probe_debug_startup 隔离 126 步配置回放：旧 r2 拒于 0x60e3092、修复候选执行 0x60e3096；probe 从两条 `li.u32` 实测读取上下限（eq-fence-repair.md/platform-startup.md）[回放] | ✅ BW 场景内 fence 模型（Flash 执行 fence 只约束 ≥0x06000000 取指）下五场景全过 [场景] | ✅ 同 BW——注入链真实执行载荷取指 [场景] | ✅ P1 已显形闭合（116,148 @ debug_init，寄存器值差，路 AJ/BK）；平台回放 127,239 两镜像各核对实际 Flash 上限；条件架构回放 110,818 同前缀 [文档] | ✅ 路 CP/D2-C：载荷 eq_open_config（0x60e30f6）在 repaired 重开路径真实执行（bypass=0），修复 fence 约束下的节点 open/重开动态行使（无独立 D2 断言，间接闭合） |
| **C. config.dat index/CRC** | ✅ BR §3.1 游程钉死 + eq-fence-repair.md 审计：旧资源内容/位置不变、boot-info reserved 及无关字节不许变、两个 CRC-valid 负向样本（改 reserved / `Eq0Media`→`Xq0Media`）均被独立审计拒绝 [静态] | ➖ 数据面非代码，函数回放不适用（消费显形在启动前缀 trace 层闭合，见 parity 列） | ❌ 未开始：0x6024fe6 深链（配置子系统→文件读）未建模，BW S4 诚实停于站点尾窗 0x6025026..35 重放；**阻塞＝resfile 变换（路 CD 调研中）** | ❌ 同左（预设装载深链不可达） | ✅ P3 已闭合：169,439 控制流分歧→reconverge，+458 步止步/枚举多一轮（路 AJ）[文档] | ⏳ 部分：stream.bin §5 节点配置读取已真实触达（路 CP/D2-C 的 open-config 0x60750aa→0x60751da 读 file_cfg）；config.dat 消费面仍随预设装载深链阻塞 |
| **D. 337B 载荷追加** | ✅ BT §1：102 条指令连续无洞解码 [0x60e3074,0x60e31b2)+19B 字符串；访问面分类（载荷外代码引用全 <0x060c3338、RAM 引用仅 0x1265EA/0x127AAC）；sha 三方一致 [静态] | ✅ 15 个编译函数用例（8 source/3 toggle/4 node-open）+ BT instrumented 写集（仅 0x1265EA、ctx+35、自身栈；type10 守卫零写）+ eq_toggle 三态表（§2）；DSP/RTOS 桩显式 [回放] | ✅ BW S1 往返（翻开 SEND(2,1)×2 无 RESTORE 出 0x81；翻回 SEND(2,0)×2+RESTORE(0,10) 同步真实执行、终值 0x01）、S2 type10 零写零 post、S5 未对齐 strncpy 真实执行==桩互证 [场景] | ✅ 同 BW [场景] | ✅ 启动前缀 D1 窗口零取指（P8；b79 侧 LIM 0x060c3338 本身禁执行）+ 追加文件资源/CRC 审计（eq-fence-repair.md）[文档] | ✅ 路 CP/D2-C：node+35 翻转（bypass=1→1、bypass=0→重开）+ 三镜像差异（repaired 关态强制 1 vs b79/official 保持 0）走真实 descriptor-walk 建出的节点——「EQ 行为差」验证最后一块闭合 |

**矩阵汇总**：24 格中 ✅20（A 行控制面为 4/5 站点部分覆盖、D2 效果节点列 3 格闭合）、➖1、
❌2（均在 C 行，阻塞＝resfile 变换/config.dat 预设装载深链）、⏳1（C 行 D2 列：config.dat
消费面）。**已绿的五层（静态/函数级/控制面/parity/D2 效果节点）全部是
derived/synthetic-not-measured 等级**；硅片端点策略、真实 DSP/RTOS 行为未认证。

### 1.1 各层边界（诚实收口，逐层）

- **静态证明层**：不执行代码；「修复值对」是访问面论证，不是硅片规范（BT §1.1
  caveat：端点包含关系/低位处理未认证）。
- **函数级回放层**：DSP/RTOS 调用是显式测试桩；callee-saved 寄存器/栈保持已被
  断言，但隔离执行≠连续启动后的自然可达性（BR §3.5 codex 口径）。
- **控制面场景层（BW）**：honest_boundaries 五条随报告输出——post 返回值=合成
  成功（真实队列状态/消费者唤醒归 RTOS/D2）、0x6024fe6 深链不建模、0x60057e6 的
  payload==53 生产者未钉死、0x6078846 归 D2、RAM 非 blob 区=合成零非实测启动态。
  「SEND 消息尚未由消费者执行节点更新」是该层的诚实收口语。
- **事件注入层（BW/CA）**：事件对象/映射表项/语言字等全具名合成 fixture
  （版本 `bw1`/`ca1`，synthetic-not-measured）；注入证明机制与站点执行，不证明
  真实事件源的时序。
- **parity 层**：启动前缀 parity 到 289,849（严格主场景）/447,490（条件场景，
  official↔b79 零分歧、EQ +458 同停点 reconverge）；差异点 P1/P2/P3/P10 已显形
  闭合，P4-P8 在 D1 窗口零消费（动态记录支持，非「静态不可达」——BT 对 BR §3.3
  的修正）。
- **D2 效果节点层**：**已闭合（路 CP/D2-C，2026-11-13）**——把「EQ 行为差」钉到
  节点状态的验证（BT §4、BX D2-B/D2-D）：descriptor walk（0x6074938, 音乐场景
  0xd96f）真实建出 eq 节点 MusicEqBt（uuid 0x737b/subid 1），cmd-11 命中真节点，
  node+35（ctx[35]）三镜像差异走真实链（bypass=1 全等=1；bypass=0 重开→钩子
  0x6078846：b79/official 保持 0、repaired 关态强制 1）。证据等级
  synthetic-not-measured（0x1265EA=0x81 关态 fixture + 单任务桩），见
  docs/d2b-framework-task.md §11。

## 2. r2 失败机制定位结论（解释链）

**结论先行**：r2 的真机失败症状群与「fence 越界导致应用在早期音频/按键路径上
失效」的机制**相容且机制本身已被静态证明+隔离回放钉死**；修复候选通过把上界
改到载荷外延消除该机制。但真机上故障如何具体显形（异常类型/trap 行为）未建模，
无设备日志，**不能声称已证实唯一实际故障点**（AGENTS/eq-toggle.md 红线原文）。

解释链（每步附证据）：

1. **r2 症状群**（2026-11-10 实机，eq-toggle.md）：OTA 传输/E6/E7/1834 数据块全
   过——传输完整性≠运行健康；升级后无关机语音、手动开机后蓝/白灯闪烁、无法正常
   配对、BLE 连接 147 超时、音量双键复位无预期反应、正常关机也失败；能扫到原地址
   广播不证明应用健康。
2. **fence 在启动早期生效**：debug_init 于 0x6034e92/0x6034e98 装 LIM_PC0
   下/上限 0x06000100..0x060c3338，0x6034e9e call RAM setter 0x12b7b8 写入、
   0x6034eac 使能 [静态+平台回放 127,239 步真实执行：两镜像各核对实际 Flash 上限]。
3. **r2 的全部钩子目标越界**：五站点替换把控制流送到 0x60e3074/92/a0/b0/c2，
   全部 > 0x060c3338；首目标超界 130,364B，**不依赖端点策略细节**（BT §1.1 静态
   证明）。任一钩子执行的第一条载荷取指即违例。
4. **钩子位于健康启动的早期必经/高频路径**（BT §2.4 触发域 + BX §1.3）：
   - 0x60059ce 在 EQ 源选择 fn 0x60057e6 内，被 20 处调用，其中**效果节点 open**
     （0x60787ac→0x60787d8 call 0x60057e6）即达——开机音（power_on tone）流水线
     构建按 BX §1.5 含效果节点 open，即触达该钩子（tone 流水线的精确节点清单尚待
     D2-A 运行时记录，BX §5.⑤——不改变「20 个调用点含效果 open」的静态事实）；
   - 0x6025364 在按键 handler 0x60252c6（分发表成员）内：任何多功能键事件（含
     非双击 passthrough）都执行钩子。
   即：不需要用户等到双击，正常启动的第一次音频流水线构建或第一次按键就撞 fence。
5. **违例→症状的映射（推断，未实测）**：无提示音＝流水线构建期失效的预期形状；
   LED 闪烁/无法配对/复位无反应＝应用主循环失效的预期形状。硅片对 LIM_PC 违例的
   精确响应（异常号/trap/复位）不在任何已验证证据内——**此步为相容性推断，非
   归因证明**。
6. **隔离回放直接证据**：probe_debug_startup 126 步隔离配置回放后，同一钩子在
   旧 r2 实际范围下被拒（0x60e3092），修复候选执行第一条载荷指令（0x60e3096）
   [回放]；probe_pi32v2 五入口向量在旧 fence 下全 BLOCKED（archived_r2_verdict
   保持 REJECTED）。
7. **修复如何消除**：上界 0x060e31c5＝载荷（＝应用明文区）末端，三端点策略均
   充分（最小充分 0x060e31b2+19B 裕量）；下限 0x06000100、RAM 范围、guard 使能、
   setter 调用全部保持原字节。扩大连续范围同时覆盖原代码末端到新载荷之间的资源
   区——**此处修的是已知范围错误，不是额外安全强化，也没有证明它是历史故障的
   唯一原因**（eq-fence-repair.md 原文）。

## 3. 还差什么：从现在到完整验证的最短路径

**D2 效果节点状态对比已闭合（路 CP/D2-C，2026-11-13）**——原「缺口」（BX D2-B
断言集）三件事全部落地到节点状态：

- EQ 关态（0x1265EA bit7=1）→ descriptor walk（0x6074938, 音乐场景 0xd96f）真实
  建出 MusicEqBt/Eq0Media 节点，node+35/ctx[35] 三镜像差异走真实链：bypass=1 全等
  =1；bypass=0 重开 0x60787ac → 钩子 0x6078846（b79/official 保持 0、repaired 关态
  强制 1）——「0x6078846 是 5 站点中唯一未执行」转为真实执行；
- cmd-11 参数消息真实命中 MusicEqBt（subcmd2 0x6078a2e），不再是合成节点直调；
- 旁证组（wdrc 的 DRC7/8/9、非目标 eq 实例 EscoDlEq 等的 +35 不翻转）与 D2-A 六 PC
  到达状态见 docs/d2b-framework-task.md §11。

**剩余缺口**（矩阵 ⏳1 + 框架调度形态）：

1. **config.dat 消费面（C 行 D2 列）**：0x6024fe6 深链（配置子系统→文件读）未建模，
   阻塞＝resfile 变换（路 CD 调研中，在途未入 HANDOFF）；stream.bin §5 节点配置读取
   已在 D2-C 真实触达（open-config 0x60750aa→0x60751da 读 file_cfg），config.dat
   预设装载深链仍停，0x1fe30（ROM 深层多参内存 API，具名合成头 fixture 返回 -7）
   的归因同为该链前置。
2. **双击 toggle 往返的框架消息消费形态（D2-D 完整口径）**：D2-C 是单任务直调
   cmd-11 消费（0x6075e22），框架任务 'jlstream' 真实调度消费两条 subcommand-2 消息
   的往返未做（CI §6 fixture #2 任务切换/单任务交替已证可行，落地归 D2-D）——节点
   面已闭合，调度面按 BX 草案 D2-A..D2-D 全套验收仍待 D2-D。

**最短路径结论**：D2-B 核心断言（open 时强制 bypass 写 ctx[35]）已随 D2-C 真实链
闭合，不需要 DAC/PCM 数据流；剩余两件（config.dat 消费面、框架调度往返）分别随
resfile 变换（路 CD）与 D2-D 任务调度收口。**关键路线判断**：BW/CA/CP 已证明
handler 入口注入 + descriptor walk 直调的 opt-in 场景模式可用（不须先走完 D1 全梯）；
正式 D2 验收（D2-A..D2-D 全套）仍按 BX 草案以 BJ 为前提。**当前关键路径＝路 CD 的
resfile 变换**（config.dat 消费面与 0x1fe30 归因的前置），其后是 D2-D 框架调度往返。

## 4. 决策选项（只列证据状态，不推荐）

决策归用户。三个选项的证据分量如下，**本页不预设、不排序、不制造紧迫感**：

| 选项 | 内容 | 当前证据状态 | 前提/代价 |
|---|---|---|---|
| a | 等 D2 完整验证（D2-A..D2-D）后再考虑真机 | 矩阵 D2 效果节点列 3 格已闭合、剩 config.dat 消费面（C 行 D2 列 ⏳）；关键路径＝resfile 变换（CD 在途）→config.dat 消费面→D2-D 调度往返 | 时间依赖 CD 调研结论与后续 D2-D 实施；验证完成前候选保持 UNTESTED 不动 |
| b | 部分验证+控制面全绿的信心下提前评估 | 静态/函数级/控制面/parity/D2 效果节点五层已绿（20/24 格）；剩余＝config.dat 消费面（resfile 变换阻塞）+ 硅端点策略未认证；全部证据 derived/synthetic-not-measured | 仍需用户显式授权 + UNTESTED 状态变更流程；不改 OTA 固定包门槛、不替换 Release（eq-fence-repair.md 红线）；接受「config.dat 消费面未验证」的剩余风险 |
| c | 维持现状，仿真优先 | 主前沿 D1 梯推进中（当前 447,383 @ p3_ana_read_not_modeled，CB/CD 在途）；EQ 验证链随 D2 依赖自然闭合 | 与 a 的差别仅在节奏；D2 关键路径同样经过 CD |

任一选项下：r2（`c8dae523…`）继续禁止刷入；本仓库任何流程都不构成对真机写入
的授权。

## 5. 不确定处（如实标注）

- 硅片 LIM_PC0 端点包含关系/低位处理未认证（矩阵 B 行全部充分性结论的边界）。
- §2 第 5 步（违例→症状映射）是相容性推断；无设备异常日志，不能宣称唯一故障点。
- BW honest_boundaries 五条、CA -7 归因未决（0x1fe30）、BX §5 六条不确定处中仍在途
  的三条（音乐场景门清单逐门枚举、0xfe04xx/0xfe09xx SDK 名、IRQ39 对应）沿用；其余
  三条已由后续路闭合——框架任务创建点（路 CI：jlstream 于 init walker 创建，非惰性）、
  eq/wdrc op1 谁消费 0x737b 消息（路 CM 行为结论 + 路 CP 标签订正：eq op1=0x607898e、
  wdrc=0x6078d0a、vocal_track_separation=0x6078594）、stream.bin 图节拓扑（路 CP/D2-C：
  §0 图节 42 边、§2 目录 53 条、§5 参数节）。
- 路 CD（resfile 变换）在途，本页按「调研中」转录自主会话分工，结论以该路落地
  文档为准。
- L2-a 场景转录/freshness 钉子以 BJ 落地时的 main 为准（BR/BT caveat 原文）。

## 6. 证据锚点

- 候选参数/审计/端点 caveat：[docs/eq-fence-repair.md](eq-fence-repair.md)、
  [evidence/eq-fence-repair.json](../evidence/eq-fence-repair.json)。
- 载荷/钩子语义、fence 静态证明、状态字节、验证分层：
  [docs/eq-payload-semantics.md](eq-payload-semantics.md)（路 BT）。
- 控制面五场景三镜像：HANDOFF「已实现」路 BW（sim-eqctrl）条目；
  tone 场景骨架与 BX 勘误：HANDOFF 路 CA（sim-tonescene）条目 +
  [docs/d2-audio-first-scene.md](d2-audio-first-scene.md) §6。
- D2 断言草案 D2-A..D2-D、SFR 缺口、离线可喂性：
  [docs/d2-audio-first-scene.md](d2-audio-first-scene.md)（路 BX）。
- 差异点全集 P1..P10、L2 设计：[docs/d1-acceptance.md](d1-acceptance.md) §3（路 BR）。
- r2 症状与设计遗漏、历史构建记录：[docs/eq-toggle.md](eq-toggle.md)。
- 启动前缀 parity（289,849 严格 / 447,490 条件；P1/P3 显形步数）：HANDOFF
  「三镜像轨迹差分」节（路 Y/AJ/BK）与 AGENTS.md 状态指针。
- 隔离 debug 回放（r2 拒/修复执行）：[docs/platform-startup.md](platform-startup.md)；
  110,818 条件架构回放：[docs/clock-rtos-startup.md](clock-rtos-startup.md)。
