# D3 差分验收准备：候选实验清单 + 真机观测手段盘点 + 授权请求模板

2026-11-14，路 BS（sim-d3prep，纯设计/盘点）。基线 main `973f4f8`（条件前沿
**3,747,585 @ 0x12b0e8** core_tick_overflow_requires_resolution 的 CNT 读；strict 前沿
289,849）。路号说明：与历史地标路 BS/sim-tickmain（689,402 步含义段）同字母不同路，
按路 BQ 三用先例以 sim 名区分。

**本路性质（红线）**：案头就绪——只做设计/盘点/预填，**未改 tools/ 任何代码、未跑任何
probe、零真机操作**。本页一切「真机会话/读取」均为计划描述，**不构成授权**；设备动作
授权边界以 AGENTS「Device actions and publication」节与当次会话用户明示为准
（d1-acceptance §7：新增真机操作一律单独授权）。

codex 复盘口径（本页的三条总纲）：

1. **D3 定义缺失暂不抢主线**——D3=模型↔真机行为一致性（d1-acceptance §7），比较面的
   观测手段与容差声明是 D2 代表场景跑通后的后续路线；可并行整理的是候选身份/证据/失败
   判据（本页 §1）与观测手段清单（§2）。
2. **涉及真机需另行授权**——离线分析/文档/构建不授权任何设备动作；本页给出的只是请求
   模板（§3）与记录底稿（§4）。
3. **D1/D2 条件仿真通过不能自动转成刷机许可**——机制层已钉死：analyze_image_trace 报告
   固定输出 `device_accessed=False / whole_h3s_boot_simulated=False /
   candidate_whole_boot_validated=False / suitable_for_flash_approval_alone=False`；
   「条件仿真全绿」到「刷机」之间必须经过一次显式的授权对话（§3 模板即为其结构）。

---

## 1. D3 候选实验清单（按就绪度三档）

每项三要素：**身份**（镜像/场景/flag 集如何钉死）、**证据**（预期显形形状与既有锚点）、
**失败判据**（什么算失败/回归，不许事后拟合）。A/B 档是纯仿真侧；C 档等真机授权。
三镜像差分的定位重申（§7）：**同模型内回归**，能发现候选镜像的回归、不能证明模型正确
——A/B 档产出的都是「D3 之前把模型侧证据备齐」，不是 D3 本身。

### 1.0 既有常态机制（不是新实验，先盘点以免重复立项）

| 机制 | 现状 | 与 D3 的关系 |
|---|---|---|
| analyze_image_trace self-check（check_local --rom 内） | official↔b79 双场景（strict crc_bankb + conditional p11_heap_experimental）parity 钉到当前前沿：控制流零分歧、首分歧 155,390（资源表头 CRC 字节）、strict 30 分歧段/5 报告差、conditional 12 报告差（11 CRC+1 watch） | 前沿每推进一路自动重钉；D3 前提=这条链持续绿 |
| 三镜像身份钉死 | UFW/app/plain SHA 各自钉死（PLAIN_LENGTHS+EQ_APP_SHA）；eq-toggle/test.sh、verify_ufw_integrity、check_eq_repair 在 units 内 | 刷机请求的证据包「候选 sha」直接引用（§3） |
| 差异账 | b79↔EQ 10 游程+official↔b79 两族（d1-acceptance §3.1）；P1-P3 已闭合、P10 派生族已归档 | 证据包「差异账」直接引用 |
| 反例/假阳性框架 | tools/test_falsification.py（路 AZ，8 反例全被抓住） | D3 记录的断言面沿用同款「故意破坏应失败」精神 |

### 1.1 A 档：纯仿真侧、执行条件已具备（不等 D1/D2、不等授权）

**A1｜b79↔EQ 条件前沿 parity 重跑**（BK 之后欠账的一次性补齐）

- 身份：`analyze_image_trace --scenario p11_heap_experimental --image tested_b79=… --image
  eq_fence_repair=… --pair tested_b79:eq_fence_repair`——flag 集 import 自
  frontier_registry 单源（AST 单测钉一致），EQ 镜像 SHA 钉死；预算按当前条件前沿
  （~3.7M 步×2 跑）。
- 证据（预期形状，全部有先例锚）：EQ 相对 b79 **+458 步**（169,439 控制流分歧→
  reconvergence，config.dat index/追加 eq_toggle.bin 机制，路 AJ 钉死）；首签名分歧
  116,148 @ 0x6034e98（fence 立即数 0x060c3338 vs 0x060e31c5）；**派生族**=core tick
  CNT 恒差 +1832=458×4 在调度器读数处显形（0x12b0e8/0x12b1fc 族，BK 钉死 8 段）；
  CRC fed/result_reads +1 轮。BK 只跑到 304,036（EQ 304,494）；此后条件前缀
  （+3.44M 步：超时窗口/OS tick 级联/uart_init/场景 walker/音频 init 尾段/延时等待族/
  core-tick PRD/CNT 边界）在 b79↔EQ 方向**尚未重跑**。
- 失败判据：①停点 parity 破坏（两镜像不同停点/停因）；②出现无法归因到「+458 派生族/
  fence 立即数/表头 CRC/CRC 轮次」四族的新分歧段——尤其任何**调度决策差异**（派生差
  影响了期限判断/IRQ 资格/任务切换顺序，§3.4 预测 2 的升级条款）；③差异区域被取指
  执行（P4-P8 钩子站点字节被执行=前缀推进超预期，需重查 §3.3 可达性结论）。
- 备注：这是 L2-a（B1）的半边预演——b79↔EQ 侧的机制证据先延长到当前前沿，B1 落地时
  三对方向一起跑。

### 1.2 B 档：等 D1/D2（仿真侧依赖，D3 的模型侧前置）

**B1｜L2-a：D1 验收场景三镜像差分**（d1-acceptance §3.4 原设计）

- 身份：场景=BJ 落地后的 D1 验收 flag 集（转录自 probe_irq_entry SCENARIOS 单源），
  freshness 按新前沿重钉；三镜像=official/tested_b79/eq_fence_repair。
- 证据：§3.4 预测 1/2/3（official↔b79 零分歧贯穿 D1 窗口；b79↔EQ 派生族；停点 parity）；
  报告侧增补（分发表命中记录/事件入队出队计数，§3.4 codex 行为状态比对——纯报告、
  默认关），属 L2-a 实施路（本页只挂账不实施）。
- 失败判据：§3.4 原文——零控制流分歧+签名段全派生只是「本窗口未消费差异」的动态证据；
  行为状态比对（事件计数/IRQ 交付顺序/任务切换次数/jiffies）任何一项目不等价即 FAIL。

**B2｜L2-b P4-P8：EQ 钩子在 D2 音频首场景显形**

- 身份：D2 音频流场景（d2b-injection-blueprint 实施后），b79↔EQ 对跑。
- 证据：P4-P8 逐点显形形状已钉（d1-acceptance §3.5 表）：0x60e30b0/c2 桩写 0x1265EA
  bit1/bit0、0x60e3092 桩 &0x7F 掩码（真实行为差）、0x60e30a0 桩 call helper+重放、
  b79 侧 LIM 0x060c3338 禁执行载荷区。路 CP 已在 d2c 场景预演一例（node+35：b79/official
  0 vs repaired 1）。
- 失败判据：钩子执行处签名差**超出**钉死形状（如掩码桩改变了不该改的位、状态字节写到
  预期外的地址）；EQ 镜像在钩子外的任何新差异。

**B3｜P9：ANC 键表 0x609e510 输入域显形**（决策点，不预设）

- 身份：触发=按键事件经 key routine 0x6018b60 消费键表（01 03 02→02 03 02 差异）；
  载体=合成输入 fixture（经固件自身 post API 注入，显式合成、opt-in）或输入域建模。
- 证据：键表差异字节被键循环读取→GPR/RAM 写差显形；分支全同预期（b79 实机 ANC 键
  两态用户已确认，方向锚存在）。
- 失败判据：键表差异改变手势识别结果（同一注入序列两镜像 post 不同事件）；归 BJ/D2
  决策是否本轮做（§3.5 原文）。

**B4｜候选镜像 D2 联动 A/B 对比**（d1-acceptance §6.5，用户 2026-11-14 定调）

- 身份：同一 D2 输入注入用例先官方后候选（R2 slotram 906184b2/R4 keyhook 8e9a47d0
  等离线候选，均 UNTESTED）；同一起点快照对跑。
- 证据：差异**精确落在接驳窗口与新载荷区**——多一个字节差异即回归（§6.5 原文）。
- 失败判据：接驳窗口/载荷区外的任何 RAM/寄存器/事件差异；候选新载荷自身的行为断言
  失败。
- 前置：D1 过线+官方镜像 D2 用例过+前沿过 0x0600db3c（R2 窗口）/RCSP 相位（R4 分发）。

### 1.3 C 档：等真机授权（观测手段就绪度见 §2）

| # | 实验 | 比较面 | 手段就绪度 | 一次会话预期产出 |
|---|---|---|---|---|
| C1 | P11 heap 描述符 0xf27f4a..4d 四字节读回 | 寄存器/存储配置 | **工具就绪**（read_rom.py `--p11-heap-probe`，离线单测过，docs/p11-heap-measurement.md §会话计划） | begin/size 从 derived 升 measured；三控制组（0x19fef8 CPU1 入口字/0x1265f5 ready/0xf20080 p11_code 头）先验证 P11 RAM 保持性 |
| C2 | ADC RES 0xfd3104 读回（WBG04 码值） | 寄存器配置 | 通道可读已评估（路 BA：loader FD07 纯 memcpy 无地址检查；RES 保持末次转换值≈WBG04，BU §6） | WBG04 码值升 measured+反解 Vref；电池链（BU §5 疑点 mode 双路径）获真值锚 |
| C3 | P3_ANA_READ 0xf2c064 一读 | 寄存器配置 | 同上（0xf2xxxx 窗 loader 自用已实证） | 充电检测位（bit1/bit2）实测→CB 的 derived_mask=0b110 常量 fixture 获对照（当前=「无外部供电全 0」假设） |
| C4 | cfg523 记录区窗（flash 0x1e0200/CPU 0x61de1e0）读回 | 输入域配置 | FD05 flash 读通道 2026-11-10 会话已实证；**另注**：2026-11-10 私有整片 2MiB 备份物理上已含 bank B 记录区字节，是否作为 fixture 输入=独立决策（单台设备私有数据，AGENTS 发布红线） | 三键阈值/键 id/通道字真值→REC 识别覆盖的合成矩阵从 synthetic 升 measured（临界值「未决」项可解） |
| C5 | core-tick CNT 0xffe0f0/PRD 0xffe0f4 复位值 | 寄存器配置 | 通道可读；**能力边界**：loader 会话=复位态世界，只能答复位值，**答不了「unresolved overflow 期间 CNT 是否冻结」的动态语义**（当前模型 CoreTickStopped 通用 guard 严格停的根据） | 复位值锚（模型 now: con/cnt/prd None→严格停）；动态冻结语义维持「模型不发明、真机不可达」诚实标注（§2.3） |
| C6 | App wire 真机抓包（setKeyFunction 实包/EQ 0x22 写与回执/0x17/0x30 回执形状） | 事件流 | 需 BLE 联调授权（R3/R4 挂账：app-protocol §4.4 两处 [未钉]、r4-command-hook wire 定案、R3-APP-05 blocked_missing） | 按键重映射 wire 定案（ADV type2 vs 0x2b vs 批量 cmdid 2）；「设备已应用」边界（响应≠应用） |
| C7 | 按键时窗/事件日志对照 | 时窗 | **前置缺口**：真机侧无事件日志面（无 UART trace/JTAG/埋点）；§7 时窗行「真机侧观测手段先落实」未满足 | 本轮 NOT_RUN 收口（D3-TIM-01 底稿）；任何时窗断言须先解决观测手段，不得以「手感/录屏」凑数 |

C 档统一失败判据（各记录再具体化）：读回值与模型/推导**不符**时如实记录、不拟合——
按 p11-heap-measurement 判据表三分支（匹配→升级 measured／不符→推导链复审新输入／
控制组失败→inconclusive 记录阻塞，不得重试）。

---

## 2. 真机观测手段盘点

### 2.1 既有真机证据与来源（§7「先对先销」清单）

| 手段 | 已取得内容 | 证据等级/局限 | 复用条件 |
|---|---|---|---|
| USB ROM 模式 FD07 RAM-loader 读（128B 分块×双读逐字节比对） | 128KiB CPU ROM 窗口 0x0..0x20000（已发布 Release，162 导出入口互证；evidence/rom-read.json） | measured（单台设备、单次会话；512B 读会毁 USB 端点——上限 128B 实测值） | 需当次授权+固定 pinned loader（FB06/FB08/FC0A 相位门） |
| USB FD05 flash 读/整片备份 | 2MiB 整片双份备份（私有，analysis/ 忽略目录 0600）——bank A/B 物理布局实证（firmware-layout 实测表）；恢复后 6 扇区读回验证 | measured（单台）；含蓝牙身份/配对/VM——**私有，不发布** | 同上；写类命令（擦/编程/OTP）零授权延续 |
| 用户验收反馈（救砖/读 ROM 后「启动正常」「按键正常」「ANC 两态」） | 功能级确认（b79 ANC 键两态=方向锚，§6.1 引用） | 弱证据：AGENTS 口径「用户反馈不是自动循环测量」；只作方向性对照不作断言 | 无需授权，随会话收尾自然产生 |
| fm1-2026-10-24\* 硬件捕获（pi32v2-isa 上游 hw/，pinned reference-cache） | ISA 语义级：并行对 run3「B 半读 A 旧值」、寄存器计数 rep、跨页双字 v2、copex 编码（AC791N FM-1 开发板，**非 H3S**） | measured（他型芯片）；只证 ISA 层不证 BR28 外设/H3S 板级 | 已 pinned 引用，无需新授权 |
| 发布工件（ROM bin/UFW/ROM-SHA256SUMS） | official/b79 公开镜像+ROM | 公开身份锚 | — |

### 2.2 授权边界（AGENTS 原文口径，逐字要点）

- 「Offline analysis, documentation, building or publishing does not authorize a new
  flash. Only perform device writes when the user's task includes them.」——现有 PROBE
  查询与私有 per-session 刷机确认是两回事；无自动重试/恢复/版本回退。
- 发布红线：原始 JSONL/HCI 日志、设备地址、整片备份不进公开库。
- FD07 专项（p11-heap-measurement/rom-read 钉死）：只走 RAM-loader 路径（ROM 原生
  FD07 是原地变换读改写，严禁指向 P11 窗）；128B；双读比对；零重试。

### 2.3 FD07 loader 会话能力与局限（C 档可答性总闸）

| 维度 | 能力 | 局限 |
|---|---|---|
| 地址面 | 命令层无地址检查（纯 memcpy）；0xf2xxxx 外设窗 loader 自用已实证 | P11 RAM 子窗（0xf20000..0xf2a000）保持性未证——C1 靠控制组先验证 |
| 状态面 | flash/RAM 字节=静态快照；RES 类「保持末次值」寄存器可读 | **SFR=复位态世界**（进入下载模式会复位芯片）：CNT 冻结/PND 完成时序/IRQ 资格等**运行时动态语义不可答** |
| 观测面 | 字节级读回 | **无事件流、无时序、无中断观测**——§7 四比较面中「事件流/时窗」两面的真机侧手段整体缺失（C7 缺口） |

### 2.4 真机对照实验最小集建议（一次授权会话的打包清单）

按 ROI 排序，建议**一次授权会话打包 C1+C2+C3+C5**（同相位列、全只读、同 128B 双读
纪律；C4 走 FD05 同会话可捎带），BLE 抓包（C6）独立授权（不同链路/工具）：

| 优先 | 实验 | 预期升级（条件→真机对照支持） | 失败判据 |
|---|---|---|---|
| 1 | C1 heap 描述符 | p11_heap_info_bytes derived→measured（条件前沿链上唯一「只能靠真机」的字节） | 控制组失败→inconclusive 挂账；不符→推导链复审，不拟合 |
| 2 | C2 RES/WBG04 | 电池门链（BU §5/§6）真值锚；Vref 反解 | 读值使两条 mode 路径任一不过 3200mV 门→模型世界与真机矛盾，重审 |
| 3 | C4 cfg523 窗 | 识别覆盖矩阵 synthetic→measured；去抖/等待窗临界「未决」项可解 | 真值与 AJ 合成矩阵差异大→REC 系列按真值重跑（差异本身是发现） |
| 4 | C3/C5 SFR 锚 | CB 充电位/CNT-PRD 复位值对照 | 不符→相应 fixture 标注 revised，不断言 |
| 5 | C6 BLE 抓包 | wire 定案三处 [未钉] | —（R3/R4 记录既有判据） |

C1-C5 全部只读、零擦写、零 OTA——与「刷机授权」（写入类）是**两类不同授权**，请求时
分开（§3 模板区分读会话/刷机/联调三型）。

---

## 3. 「条件仿真通过→真机授权」请求模板

**触发条件**（全部满足才发起请求）：①对应验收面板全绿（相关记录无 FAIL/无未归因
BLOCKED；NOT_RUN 项与请求范围无交集或已明示）；②三镜像 parity 链在当前前沿绿
（A1/1.0 常态机制）；③候选镜像身份与差异账可逐字节复现（units 内工具复跑）。

**证据包结构**（五件，全部可由仓库工具再生成，不手写数字）：

1. **验收记录汇总**：`tools/acceptance_record.py --summary --dir docs/acceptance-samples/
   <面板>` 的表格输出+结果四值分布（PASS/FAIL/BLOCKED/NOT_RUN 分开计数，BLOCKED
   ≠弱化 PASS 逐条给 blocked_missing）。
2. **候选 sha**：目标镜像 UFW/app/plain 三层 SHA256+与官方镜像的关系（b79=已刷机验证
   的 ANC 两态版；EQ/R2/R4 候选=UNTESTED 离线候选）+构建复现命令（verify_ufw_integrity/
   check_eq_repair 等）。
3. **差异账**：候选 vs 基线逐字节分类账（先例格式：R4-VM-02 的 554B/111B 两级账、
   R2-HEA-02 的 279B 六区账）——「无一例外字节」是硬要求；挂 P1-P10 显形状态表
   （已闭合/派生/不可达的逐点结论）。
4. **执行证据**：候选新载荷在仿真里的执行证据（字节级验证+激活链+场景执行，如
   R2-HEA-01/CP 的 d2c）与**边界声明**——whole_h3s_boot_simulated=False 等四字段
   原样呈现，derived-not-measured 清单不省略。
5. **回滚方案**：usb-recovery 救砖先例流程（固定 pinned loader；进入前**整片 2MiB
   双备份**→离线恢复计划→限定扇区写→读回验证→用户功能确认）；恢复目标=已知良好
   b79（H3S_0.1.1.8_ANC_ON_TRANSPARENT.ufw，sha 1326848B b79cb3a6…）；明示恢复
   的已验证边界（仅该故障场景/仍能响应 EP0 的机器）。

**模板骨架**（请求时填空，三型分开）：

```
请求类型：[A 只读观测会话（FD07/FD05 读）｜B 刷机（写入候选）｜C BLE 联调]
目标：[镜像/读点清单/联调链路]
前置状态：D1=[面板状态] D2=[面板状态] 三镜像 parity=[前沿@步数]
证据包：[1-5 件，各附再生成命令]
本次动作边界：[只读命令清单｜扇区清单]；不自动重试；失败即停记录
回滚：[B 型必填：备份→计划→写→读回→功能确认 五步]
预期获得：[升级项清单]；不预期：[明确列出本次答不了的问题]
```

**固定声明**（模板尾注，不许删）：本请求不因「仿真全绿」而自动生效；会话内每一步以
用户当次明示为准；「条件仿真通过」的证据等级（conditional/derived-not-measured）
在真机对照完成前不升级。

---

## 4. D3 记录底稿（id 规则 + NOT_RUN 预填）

**id 规则**：`D3-XX-NN`——`XX`=比较面缩写，取值：`EVT`（事件流）/`SFR`（寄存器配置）/
`TIM`（时窗）/`AUD`（音频输出）/`PRB`（观测手段会话本身，即 §2 的手段级记录）；
正则与 §8.1 一致（`tools/acceptance_record.py` CASE_ID_RE）。预填全部 **NOT_RUN**
（依赖写「未覆盖项」，BLOCKED 留给实施路撞上无法解除的缺件——d2b README 同款口径）；
观测手段会话完成后 `evidence_level=device_supported` 须挂 `evidence_refs`（具体会话
报告文件）。

**底稿**：`docs/acceptance-samples/d3/`（本路预填 6 条+README 面板）：

| id | 比较面 | 内容 | 等待项 |
|---|---|---|---|
| D3-PRB-01 | 观测手段 | P11 heap 描述符 FD07 probe（C1，工具就绪） | 只读会话授权 |
| D3-SFR-01 | 寄存器配置 | ADC RES/WBG04 读回（C2） | 同上（可与 PRB-01 打包） |
| D3-SFR-02 | 寄存器配置 | core-tick CNT/PRD 复位值（C5；动态冻结语义显式列不可达） | 同上 |
| D3-EVT-01 | 事件流 | App wire 真机抓包对照（C6：setKeyFunction/0x22/0x30 回执） | BLE 联调授权 |
| D3-TIM-01 | 时窗 | 按键识别时窗误差范围对照（C7：**前置=真机事件日志面存在**） | 观测手段本身（暂无 UART trace/JTAG） |
| D3-AUD-01 | 音频输出 | 「有声音」面（§7：输入/采集/容差三明确才开比） | D2-b 场景+容差声明前置 |

复核：`python3 tools/acceptance_record.py --validate docs/acceptance-samples/d3`。

---

## 5. 不确定处（如实标注）

1. **C 档各读点的「保持性」**：P11 RAM/RES/SFR 在进入 USB 下载模式的复位后是否保持
   boot 态内容，离线不可定（p11-heap-measurement flagged unknown）——控制组设计已
   覆盖 heap/RES 两点，其余读点首会话观测。
2. **私有整片备份的可用性**（C4 备注）：字节物理在手但属单台设备私有数据；作为
   fixture 输入前需用户决策（与「擦除态世界」的分歧如何呈现也待定——两台设备记录区
   可能不同）。
3. **观测手段的空白面**：事件流/时窗比较面当前**无任何真机侧手段**（§2.3）；若用户
   未来提供 UART trace/埋点渠道，D3-EVT/TIM 系列才从 NOT_RUN 转入可执行。
4. A1 的预算：~3.7M 步×2 跑单次约十分钟级（BK 先例外推），不进 units（一次性证据
   路跑），结论并入下轮 L2-a。
5. 本页全部「工具就绪」判断基于 main 973f4f8；前沿再推进后 C5 的复位值预期与 A1 的
   前沿数字按新前沿复核，机制结论不变。
