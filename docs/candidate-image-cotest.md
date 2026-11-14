# 候选镜像联动用例设计：候选镜像世界的构造 + R2/R4 注入联动 + A/B 差异白名单（路 BT / sim-coimg）

2026-11-14 路 BT（sim-coimg，基线 973f4f8，纯设计不改 tools/）。把 d1-acceptance §6.5（用户
2026-11-14 定调：D2 用例先官方镜像过闭环，再跑 R2/R4 候选镜像；官方 vs 候选 A/B 差异精确
落在接驳窗口与新载荷区，多一字节=回归）落成实施路拿到即可开工的规格书：候选镜像在仿真器
里的加载入口（第三/四镜像接线）、公共前缀验证、按功能线分组的联动用例集（注入序列/观察点/
预期差异集/回归机器判定）、A/B 双世界接口与**差异白名单的机器闭包规则**、前置链与验收记录
预填。**本路是设计文档，不是实施**：未改 tools/ 任何代码、未跑任何改动场景；所有规格以已
合并实证为据（出处随行），任务卡的两处简化假设按证据修正（§1.4/§2.1）。

证据强度沿用：**[代码]** = 基线 973f4f8 的 tools/ 源码；**[文档]** = 已合并 docs/HANDOFF 条目；
**[静态]** = 既有文档钉死的反汇编结论；**[实测]** = 既有报告实测；**[设计]** = 本文新增工程
决策，实施路落地时按运行观测复核。

## 0. 一句话结论

候选镜像世界 = 三镜像差分机制（analyze_image_trace）照 load_b79 形状加两个 loader（sha+长度
+共享 bootloader+差异分类账四重前置断言）；公共前缀验证**现在就能跑**（两个场景的前沿都在
0x0600db3c 之前，不依赖 D1）——但首差异不是接驳窗口，是 fence 立即数与 config.dat index 两个
既有派生族（§1.4 修正任务卡假设），l1 候选表尾跨 4KiB 对齐桶是**新观测项**（EQ 的立即重合
机制不可移植）。联动用例 12 条按注入层级 L0-L3 分层（直调窗口→S19 post 手势→完整 ADC→
RCSP 帧），每条给出观察点/预期差异集；A/B 白名单=四源闭包（构造差/载荷写集/改道差/步数
派生）+预登记二级差，时间类噪音**如实报+机器分类不过滤**，归因不进四源且未预登记=回归。

## 1. 候选镜像世界的构造规格

### 1.1 现有双/三镜像接线的事实基线 [代码]

`tools/analyze_image_trace.py` 的镜像配置是「每镜像一个命名 loader + 钉死清单」模式：

| 机制 | 现状 | 锚点 |
|---|---|---|
| loader 注册表 | `LOADERS = {'official', 'tested_b79', 'eq_fence_repair'}` | :759-761 |
| 身份钉死 | 每 loader require UFW sha256（`B79_SHA`/`REPAIRED_SHA`）+ plain 长度（`PLAIN_LENGTHS` per label：official/b79=0xE3053、eq=0xE31C5）+ app 切片 sha（`B79_APP_SHA`/`EQ_APP_SHA`） | load_b79/load_eq_candidate :405-422 |
| 共享 handoff 断言 | `flash[:0x2000] == official_flash[:0x2000]`——bootloader/头区逐字节同，共享 handoff 假设不破 | load_b79 :410 |
| 场景单源 | `frontier_run` 直接 import `probe_irq_entry.irq_case` 的 SCENARIOS flag 集（frontier_registry 单源），支持 `crc_bankb`（289,849）与 `p11_heap_experimental`（条件前沿 3,747,585）两档 | :87-99/:366-396 |
| 新鲜度钉子 | official 必须先复现主前沿/条件前沿停点，配置漂移即拒 | :93-108 |
| 逐步签名 | TraceMachine（DualCoreMachine 纯观察子类）：核/before_pc/next_pc/flags/16 GPR 规范形/该步全部 guest RAM 写 | COMPARISON_BASIS :283-294 |
| 差分机制 | first signature divergence（classify_divergence 关联 image_diff_runs 根因）/first_control_divergence/reconvergence（步滞后搜索）/common_control_prefix/stop parity/final_report_diffs | compare_pair :549-600 |
| 已知差异语义表 | `KNOWN_SITE_LABELS`（fence 0x34E98/ANC 键表 0x9E510/config.dat 0xE2EE1/追加段等）——**只做展示标注，不参与判定** | :299-317 |
| self-check 钉子 | 2026-11-12 首轮 official_vs_tested_b79 结论锁定（`require(set(pairs)=={'official_vs_tested_b79'})`） | :780-800 |

boot 前缀的既有 parity 实测形状 [实测：HANDOFF 三镜像节]：official↔b79 零控制流分歧（资源
表头 CRC 字节经 rep/CRC 引擎显形 30 个签名段，分支全同）；b79↔EQ 在 169,439 控制流分歧
（config.dat JLFS index 1→0，多枚举追加文件一轮 **+458 步**）后**立即重合**（表尾 0xE3053/
0xE31C5 落同一 4KiB 对齐桶，对齐后地址相同），全签名残余分歧持续 63,290 步后重合到停点；
fence 立即数差在 debug_init ~116,148 显形为 0xffe218 寄存器值差（P1，已闭合）。

### 1.2 第三/四镜像 loader 设计 [设计]

照 load_b79 形状新增两个 loader（候选都是完整合法 UFW，四层 CRC 链自洽——不像 eq 候选需要
candidate_view 的 readonly 视图，直接 load_image 解密即可）：

```python
L1_SLOTRAM_SHA  = '906184b2e3a5f36669ba80e97d6716206a0b9b78923a34eba1f1df29036b5914'  # §12.3
R4_KEYHOOK_SHA  = '8e9a47d0ec89982bd6f35a616d0e1d310562c1509d761746822e787bf5dcbf5d'  # r4 §3.3
PLAIN_LENGTHS += {'l1_slotram': 0xE5200, 'r4_keyhook': 0xE524E}

def load_l1_slotram(path, official_path):        # 与 load_r4_keyhook 同形
    raw, flash, plain = load_image(path)
    require(sha256(raw) == L1_SLOTRAM_SHA, ...)   # ① UFW sha
    require(verify(path)['passed'], ...)           # ② 独立校验器 CRC 全过
    official_flash = load_image(official_path)[1]
    require(flash[:0x2000] == official_flash[:0x2000], ...)  # ③ 共享 bootloader/handoff
    require(len(plain) == PLAIN_LENGTHS['l1_slotram'], ...)  # ④ plain 长度
    # ⑤ 差异分类账前置检查（见 §1.3）：byte_diff_runs(official, candidate) 的区域分类
    #    == hearing-aid-l1-payload §12.3 / r4-command-hook §3.3 的逐区域字节账
    return app, plain, (), L1_SLOTRAM_SHA
```

- **app 切片 sha**：候选不改 app.bin 既有内容（追加文件在明文区尾部、窗口在原代码区、
  CRC 链字节在头区）——但 app 切片 [0x100, 0x100+842654) 内含 6 窗口 16B+CRC 字节，故
  l1/r4 的 app sha ≠ B79_APP_SHA，实施时从规范构建产物实测钉死（构建器要求输出目录
  不存在，sha 复跑即得；不做假设值）。
- **互斥与族关系**：r4_keyhook 隐含 slotram 全链（其组合段前 364B 与 slotram 候选段逐字节
  相同，构建时断言 [文档：r4 §3.2②]）——parity 上 official↔r4 = official↔slotram 的差异
  面 ∪ {注册表槽 4B+keyhook 78B+链式 CRC}。两 loader 独立注册，不做「叠加」开关。
- **EQ 候选不动**：三候选共用追加点互斥（AGENTS 不变量），analyze_image_trace 的
  eq_fence_repair 行为不变；新增 pair 键**不改** 2026-11-12 self-check 的
  `set(pairs)=={'official_vs_tested_b79'}` 钉子——新镜像对走独立 self-check 函数（钉
  official↔l1_slotram 的首轮结论，形状照旧：parity/首分歧步/差异段数三件套），避免「改
  钉子=改历史」[设计]。
- **freshness 跟随**：两 loader 跑在 SCENARIOS 既有场景上，official 侧新鲜度自检已内建；
  候选侧停点 parity 是输出断言（§1.4），不是前置。

### 1.3 已知差异语义表增补（KNOWN_SITE_LABELS 展示层）[设计]

把候选的字节差区（plain 偏移=运行地址−0x06000000）增补进展示表（只标注、不判定，判定走
§2.3 白名单闭包）：

| plain 游程 | 镜像对 | 语义 [文档：hearing-aid §11.2/§12.3、r4 §3.1/§3.3] |
|---|---|---|
| 0x34E98..0x34E9E（3B） | off↔l1/r4 | fence 上限立即数 0x060C3338→0x060E31E0（l1）/0x060E322E（r4） |
| 0xDB9A/0xDBC4/0xDC74/0xDC7E/0xDC9A/0xDCA4 各 8B | off↔l1(含 r4) | 接驳窗口 ON-1/ON-2/OFF-1/TRANS-A/OFF-2/TRANS-B 整窗替换 |
| 0xA9C7C..0xA9C80（4B） | slotram↔r4 | cmdid 0x30 项 setter 槽 00000000→LE32(0x060E31E0) |
| 0xE2ED3..0xE2ED5 + 0xE2EE1 | off↔l1(含 r4) | config.dat 头 CRC + index 1→0（追加 mic_effect.bin 后重算） |
| 0xE3053..0xE5200 / ..0xE524E | off↔l1 / off↔r4 | 追加文件 mic_effect.bin（头 32+0xff+代码 364[/442]） |
| 0x2000..0x2020 区、app 区头 CRC、UFW 外层 | 全部对 | boot-info app_size+三层 CRC / JLFS 双层 CRC / UFW 条目 CRC（构建器分类账，off↔l1 计 472B、off↔r4 计 554B） |

注意：l1/r4 候选**不改**资源表头 CRC（0x0..4/0x20..24）与 ANC 键表/stream 音量区——那两族是
official↔b79/EQ 的差异 [文档：d1-acceptance §3.1]，候选对里应零差异（§2.3 白名单外的多余
差异在 loader ⑤ 分类账检查即拒）。

### 1.4 公共前缀验证：预期形状与任务卡假设修正

**任务卡假设**：「boot 前缀应逐字节同轨迹——直到首个差异指令=接驳窗口首写」。**按证据修正**：
R2/R4 候选与官方的镜像差里有两类字节在 boot 期就会被消费，首差异**不是**接驳窗口：

1. **fence 立即数（plain 0x34E98）**：debug_init ~116,148 装载 LIM_PC0 上限 → 0xffe218 寄存器
   值差（P1 先例：指令编码差→寄存器差，控制流不变；b79 0x060c3338 vs EQ 0x060e31c5 已闭合，
   l1/r4 同一消费点）。窗口行为差只在 PC>0x060c3338 取指时出现——boot/D1/D2 前缀零执行
   （追加段只被载荷接驳触达）。
2. **config.dat JLFS index（plain 0xE2EE1）**：VM init 0x6011132 枚举 @169,439 控制流分歧——
   候选多枚举 mic_effect.bin 一轮（+458 步族，EQ 同款机制）→reconverge。

**新观测项（本设计点名，不可照抄 EQ 结论）**：EQ↔b79 的立即重合靠「两镜像表尾落同一 4KiB
对齐桶」（0xE3053/0xE31C5→均对齐 0xE3000，0x600ae3e 换算后地址相同）；l1 候选表尾
0xE5200 对齐 0xE5000 ≠ official 0xE3053→0xE3000——**对齐后地址差 0x2000**，末项指针/block
经 0x600ae3e 换算后持续存活为寄存器/栈帧派生差（EQ 案的分歧族①），且**不能预设重合步数**。
运行观测项：official↔l1 在 169,439 之后的签名残余段集合与重合点实测归档（预期仍是派生族
——指针值存活到被覆写；但若对齐后地址差流入任何基址计算产生控制流差=新形状，如实报告）。

**公共前缀的判定口径 [设计]**：

- **窗口/载荷零消费**：候选独有取指（窗口替换后的跳转指令、载荷区 [0x060E3074, 段末)）在
  前缀内执行次数为 0——`common_control_prefix` 段内 watch 断言；这是「前缀未把镜像差异
  消费进控制流」的动态证据（COMPARISON_BASIS 既有口径：轨迹一致≠镜像等价，只证明本前缀
  未消费）。
- **控制流分歧段全部∈已知族**：{fence（无控制流差，仅寄存器）、config.dat 枚举 +458 段}
  且每段 reconverge；已知族之外的控制流分歧=FAIL。
- **停点 parity**：两镜像停在同 kind 停点（official↔l1 预期同停点；EQ 先例 +458 步差下
  b79/EQ 仍同停点@前沿）。
- **可立即执行**：crc_bankb（289,849，~17s/镜像）与 p11_heap_experimental（3,747,585，
  ~3.5 分钟/镜像）两档的前沿**都在 0x0600db3c 键循环相位之前**（主前沿停在 power_init
  0x60679ba、条件前沿停在 S19 后 core-tick CNT 读 0x12b0e8）——公共前缀验证不依赖 D1
  过线，是候选世界构造路的第一条可跑用例（§3）。

## 2. 联动用例集（按功能线分组）

### 2.0 注入层级矩阵与世界起点 [设计]

按键/App 命令到 R2/R4 接驳点的路径分四层，逐层解锁（每层都是真实固件代码，越往上覆盖
越完整；层级即用例的前置链，不是等价类——上层用例不重复下层的断言面）：

| 层 | 注入方式 | 覆盖到 | 先例/前置 |
|---|---|---|---|
| L0 | post_stop 直调窗口宿主/分发器（0x0600db3c / 0x0600f85e 写路径） | 桩→载荷→API 全链 + 固件原生改道路径 | S 路键链直调先例 [文档：peripheral-input-simulation §7]；前置=前沿过 0x0600db3c / 分发器可调性观测 |
| L1 | post 手势事件注入（S19 分发面，0x10000c handler 消费） | cmd 动作分支全链（cmd 11→0x060189aa→0x0600dcee→db3c） | BW 双击注入先例；前置=D1 事件循环过线（D2-a B 组口径：只算分发/动作层测试，不冒充识别覆盖） |
| L2 | 完整 ADC 采样序列注入（AJ 三键矩阵 fixture+拍驱动） | 识别+动作串联闭环 | AJ/S 实测 [文档：peripheral-input-simulation §7/§8]；前置=cmd 11 的物理键绑定钉死（§2.1-R2-C3 待钉项） |
| L3 | RCSP 命令帧注入（App 0x17 模式直设 / FF-0x30 key30） | App 命令层闭环（BLE→RCSP→分发器） | 前置=RCSP 相位（R4-VM-05 同一 blocked_missing） |

**世界起点**：每用例从「官方镜像 D2 用例通过的同一观察窗起点」出发（候选世界与官方世界
同 budget 全前缀重放到注入点——§2.3 说明为何不用快照）。**注入在候选镜像与官方镜像各跑
一遍**（A/B 双世界），不是只跑候选：官方侧提供「未改道」的对照轨迹与改道差源（§2.3 源 D）。

### 2.1 R2 线：辅听 L1 接驳联动（镜像=l1_slotram，906184b2）

通用观察点集合（每用例引用，不重复罗列）：

- **W-窗口**：6 窗口 PC（0x0600db9a/0x0600dbc4/0x0600dc74/0x0600dc7e/0x0600dc9a/0x0600dca4）
  的执行记录（watch_pcs/milestones 既有机制）——候选侧命中集合==注入序列的预期窗口集，
  官方侧同 PC 执行的是**原指令**（call ctx_get+槽读取），两侧都非空。
- **W-载荷**：载荷/桩入口执行——open 0x060E3074、close 0x060E30FA、四 leave 桩/两 TRANS 桩
  （0x060E313A/0x060E315A/0x060E317A/0x060E3192/0x060E31AA/0x060E31C8）与 7 个 jlstream
  API 锚点调用记录（路 O 已实证 open 链各环节真实行为 [文档：hearing-aid §8]）。
- **W-槽**：**0x125574** 槽状态机（boot BSS 清零=0 → open 写 stream → close 清 0）——RAM 槽
  是 close 半链前提 [文档：hearing-aid §12.1/§12.4]；watch_ram 既有机制。
- **W-簿记**：0x124880 时钟链表（clock_alloc "mic_effect" 24MHz 节点 open 时入链 / close 时
  notify(11)→clock_free 0x0600572C 全部同名摘链 [文档：hearing-aid §10.1]）、播放器对象簇
  [QCY+592]=0x126810（音量门读点）、pipeline 节点堆区（11 节点，路 O 实测形状）。
- **W-ANC 侧**：b[[QCY+500]+33] 模式字节（[文档：key-action-map §4.2]）、set_config(16)
  持久化镜像、BLE 0x17 回执帧构造（0x06025652(23,…)）。

**用例表**（预期差异集=该用例注入后 A/B diff 中允许非零的白名单源，引用 §2.3 的源编号；
「非预期差异=回归」的机器判定统一走 §2.3 三层报告，表内不重复）：

| id | 用例 | 注入序列（层） | 观察点 | 预期差异集 |
|---|---|---|---|---|
| R2-COT-01 | 公共前缀 parity（boot 段） | 无注入（§1.4 全前缀重放，crc_bankb+条件前沿两档） | §1.4 四条口径（窗口/载荷零消费、分歧段∈已知族、reconverge、停点 parity）+表尾跨桶新观测项归档 | 源 A（fence 寄存器差+config.dat 枚举段+表尾派生族）；**本用例无注入，A 之外任何差=回归** |
| R2-COT-02 | TRANS 窗口半程 | L0 直调 0x0600db3c(3,1)（TRANS，flag=1 走链 1 窗口 A；再直调 (3,0) 之外的链 2 变体按 §11.4 链选择观测补） | W-窗口（A 命中）、W-载荷（trans_branch_1→OFF apply 0x0600dae4→open 全链→尾声 0x0600dc8c）、W-槽（0→stream）、W-簿记（clock_alloc 节点入链、pipeline 11 节点） | 源 B（载荷+桩写集：槽、stream 对象、时钟节点、堆区）+源 D（官方走 TRANS apply fn vs 候选走 OFF apply fn+open——ANC 引擎配置路径差）+源 A/C |
| R2-COT-03 | 生命周期 open→close→再 open（codex 二次输入） | L0 序列：db3c(3,1) → db3c(1,1)（OFF，leave_off1/off2 窗）→ db3c(3,1) | W-槽状态机 0→stream→0→stream′（三个状态转移逐个断言）；close 链 stop 0x060741ee→release 0x06074204→槽清 0→notify(11)→clock_free（W-簿记节点数平衡：alloc 与 free 恰好配对）；第二次 open 无早退（槽已清）且重跑全链；**stop/release 的同步等待边界**=停点若落在 cmd-post 等待（0x06074192 族）如实报 BLOCKED 归流服务线程模型件（§5 边界 4） | 源 B+D+A/C；二级差预登记：第二次 open 的 stream 地址与第一次不同（堆不复位）——两侧候选内自洽即可 |
| R2-COT-04 | cmd 7 等价负例（双击不改道） | L1 post 0x10a04（多功能键双击，AJ 实测载荷字 [文档：d1-acceptance §6.1 表]） | cmd 7 分支全链（0x06025404→get_config(22)→0x060251fc(3 或 1,1)→0x0600dab6 [文档：key-action-map §4.1]）；0x124a04 模式字节翻转+set_config(22)+BLE 0x2E 回执；**0x0600db3c 族 6 窗口零命中** | **零行为差**（cmd 7 不触 ANC/mic_effect dispatch [文档：key-action-map §4.4：调用边分开]，A/B 两世界此用例应全同）——任何差=回归。这是「候选不碰无关功能线」的最强负例 |
| R2-COT-05 | 音量键交汇（辅听活动门） | L0 先 db3c(3,1) 激活辅听 → L1 post 0x10b00（音量-单击） | 候选侧：[QCY+592]≠0 门命中→音量动作早退（QCY+4434 不变、0x10000/0x20000 事件族零 post）[文档：key-action-map §4.4 第二交汇点]；官方侧：门读 0→正常媒体音量链 | 源 D（门读值差→**行为差**：官方 post 事件族/候选不 post——这是候选的功能预期，不是回归）；二级差预登记=QCY+4434 级别变化仅官方侧发生 |
| R2-COT-06 | App 0x17 直设通透（与按键不分叉） | L3：RCSP 0x17 帧 data[0]=3（wire TRANS） | wire 3→0x0600dd62 call db3c(3,1)→窗口 A/B [文档：hearing-aid §11.4]；观察同 R2-COT-02 + 0x1265EB..ED 三个直设字节 | 同 R2-COT-02（源 B+D+A/C）；断言「App 语义与按键语义在同一窗口汇合」=窗口命中集合与 R2-COT-02 一致 |

**R2-COT-03 与 R2-HEA-06 的关系**：r234 档案的 R2-HEA-06（BLOCKED：接驳后完整路径执行）由
R2-COT-02/03 解锁——联动用例是其执行半边；R2-HEA-06 记录在过线后补 PASS 并引用 COT 记录
（预填时不动它）。

**待钉死项（如实标注）**：①cmd 11（ANC 键切换）的物理键→映射表绑定：默认表 15 槽无 cmd 11
绑定 [文档：key-action-map §3.2]，「物理 ANC 键」（b79 用户实测两态的那个键）到 cmd 11 的
映射来源未定位（key id 13/逻辑键 9 的槽不在默认表）——**L2 完整 ADC 闭环用例因此暂不预填**
，钉死后按 §6.4 准入规则补（它新增的只是识别半边，动作半边与 R2-COT-06 同链）；②键循环
宿主 0x0600db3c 到达的模型件依赖（anc_task/流服务线程/停-重配-启链上的未建模边界）属前沿
推进路的既有账，不在本设计展开。

### 2.2 R4 线：key30 命令接驳联动（镜像=r4_keyhook，8e9a47d0）

| id | 用例 | 注入序列（层） | 观察点 | 预期差异集 |
|---|---|---|---|---|
| R4-COT-01 | 公共前缀 parity（boot 段） | 同 R2-COT-01（official↔r4_keyhook；差异面=l1 差异 ∪ 注册表槽 4B+keyhook 78B+链式 CRC） | §1.4 四条口径；注册表槽/keyhook 区在前缀零取指（keyhook 仅 0x30 写路径触达；槽是数据零执行） | 源 A（同 R2-COT-01 族）；A 之外任何差=回归 |
| R4-COT-02 | 分发器写路径半程 | L0：构造命令体 data=[30, v0..v7]（9B），直调分发器 0x0600f85e 的 0xFF 写路径（body[0]=0x30 匹配 +24→取 +8 setter **非零**→同步 call 0x0600f932-93e [文档：r4 §1.1]） | setter 载荷 0x060E31E0 执行：len==9 且 data[0]==30 → set_config(30, data+1, 8)@0x06001f12 → VM key 30 记录写入（路 AB 记录形状 `57 30 <8B>` [文档：vm-key-roundtrip]）→回执 resp[0..2]=`30 01 00`（status=0）经 0x0600f946 包装；官方侧对照：**0x30 槽=0 → 0x0600f8b6 静默退出，零回执零写**（这就是空槽的官方行为 [文档：r4 §1.2]） | 源 B（载荷写集：VM 记录区、resp 缓冲）+源 D（官方静默退出 vs 候选执行——行为差是功能预期）+源 A/C |
| R4-COT-03 | 读回闭环 | L0：同世界续注入 data=[30]（1B 读形状） | get_config(30, resp+2, 8)@0x06000f88 → **读回 8 字节逐字节==R4-COT-02 写入值**（路 AB 直调层已实证同机往返；本用例增量=经分发器+载荷到达 get_config）→回执 `30 08 v0..v7`；负例形状：len∉{1,9} 或 data[0]≠30 → n=0 静默（分发器 0x0600f942 ifs(n<1) 跳过） | 同 R4-COT-02（读路径写面=resp 缓冲）；读回值断言在候选世界内部（A/B 两侧候选侧才有效——官方无此路径） |
| R4-COT-04 | 其他 cmdid 回归负例 | L0：同世界直调分发器走 cmdid 0x17/0x22/0x1d 的既有 setter | 三条既有命令路径行为与官方世界全同（注册表其余 14 项未动：除 0x30 槽 4B 外注册表区零差异 [文档：r4 §3.3 字节账]）；EQ 0x22 帧（R3-APP-01 组帧资产可复用）round-trip 不变 | **零行为差**（0x17/0x22/0x1d 的 handler 入口/写集与官方逐项同）——任何差=回归 |
| R4-COT-05 | RCSP 帧全程闭环 | L3：完整 FF/0x30 命令帧经 BLE→RCSP 相位注入（异步 trampoline 0x060256aa 或同步路径按运行观测） | R4-COT-02/03 断言面 + 命令帧到达分发器的完整前置链（RCSP 对象/[QCY+1004]） | 同 R4-COT-02/03；**BLOCKED 归 RCSP 相位**（R4-VM-05 同一缺口，预填时挂 blocked_missing） |

**R4-COT-02 的世界前置（运行观测项）**：直调 0x0600f85e 的最小可调世界=命令体缓冲可合成 +
分发器读的上下文对象（异步投递路径 0x0600f8b8..f92e 需要命名任务 0x060A1D11 存在；**同步
分支 0x0600f932 不依赖任务投递**，首选同步路径起跑）。若分发器入口前置读到未建模对象，
按 S 路先例在 world_prep 补齐并记录（d2b 蓝图 §2.5.1 同款），不放宽停点。

### 2.3 A/B 双世界接口与差异白名单规则（§6.5「多一字节=回归」的机器化）

**接口形态 [设计]**：

```
for pair in [('official','l1_slotram'), ('official','r4_keyhook')]:
    world_a = full_replay(official,  scenario, budget=注入点)   # 全前缀重放
    world_b = full_replay(candidate, scenario, budget=注入点)   # 同 budget 同 flag 集
    prefix_parity(world_a, world_b)                             # ① 前缀逐步 parity（§1.4 口径）
    inject(world_a, seq); inject(world_b, seq)                  # ② 同注入序列（各自世界）
    run_to(world_a, 终点); run_to(world_b, 终点)                # 终点=用例观察窗结束
    report = ab_diff(world_a, world_b)                          # ③ 三层 diff 报告（下）
```

- **为何不用快照**：快照的静态配置层按文件+digest 重建（M8 [文档：snapshot-design §1/§8]），
  恢复闸校验镜像 digest——候选世界的 flash 字节不同，直接共享官方快照会被 digest 闸拒绝；
  放行=拆 image_fingerprint，属快照机制的镜像维度扩展，而 snapshot-design §2.1 已挂账
  「镜像变体快照首期不做——L2 parity 本身就是全前缀重放纪律，快照化必须先过等价对照」。
  **裁定：首期 A/B = 双世界全前缀重放**（一次性成本 2×3.5 分钟/对，可接受）；快照化在
  镜像内 artifact-tier 等价绿后另立（本设计不预设）。
- **注入后不按 step zip 比对**：候选执行载荷额外步数，注入点后两侧步轴必然错位（EQ +458
  先例）——终态比对按**事件对齐**：注入序号/窗口命中记录/观察点事件各自对齐（时间轴只作
  参考不作索引）。逐 step 签名比对只在①前缀段使用。

**差异白名单=四源闭包 + 预登记二级差 [设计]**。任何 diff 项必须归入下列四源之一，否则
unexplained（=回归）：

| 源 | 定义 | 机器归因方式 |
|---|---|---|
| **A 构造差** | 镜像字节差集 D（byte_diff_runs(official, candidate)，=构建器分类账逐区域断言对象 [文档：hearing-aid §12.3/r4 §3.3]）被读取/执行产生的显形：fence 寄存器值差、config.dat 枚举 +458 段、表尾派生族、CRC 引擎结果差 | 取指 PC 的指令区间∩D → A；寄存器差的来源指令区间∩D → A（classify_divergence 既有 image_diff_run 根因关联 [代码 :485-507]） |
| **B 载荷写集** | 候选载荷与桩的全部 guest RAM 写：静态可枚举（open 41 条/close 20 条/桩 8 条的源码写点 + 其调用的 7+2 个固件 API 的已知写面——open 链写面已被路 O 仿真实测 [文档：hearing-aid §8]） | 写地址∈载荷写集地址表（实施时从载荷源+§8 实测形状生成，钉进单测） |
| **C 步数派生** | 载荷额外步数 Δ 造成的恒差族：步数计数、jiffies [0x125004]、core-tick CNT/PRD 读数、Timer0 分数余数、事件/交付计数 | 地址∈时间类地址表 ∧ 差值可归因 Δ 的常数闭包（P10 先例：CNT 恒差=+458×4 [文档：d1-acceptance §3.5 P10]） |
| **D 改道差** | 桩语义=「原分支改走另一条固件原生路径」：官方走 TRANS apply fn vs 候选走 OFF apply fn+open；官方 0x30 槽静默退出 vs 候选执行 setter；辅听激活→音量门 [QCY+592] 读值差 | 候选侧执行的函数集合==预期改道集（用例表预声明）∧ 其写面⊆固件原生写面∪源 B |

**二级差预登记规则**（防白名单无限扩散）：源 B/D 的写被后续固件逻辑读取产生的二级行为差
（例：R2-COT-05 音量门差→事件族 post 与否），必须在**用例预声明清单**内（本设计逐用例已给
「二级差预登记」栏）；未预登记的二级差=unexplained。依据=d1-acceptance §7 时窗比较面的既有
原则：误差范围/白名单须先声明，不能事后拟合；§6.4 范围表预冻结同款。

**时间类噪音的裁定（任务点名的规则问题）——如实报+机器分类，不过滤不归一化**：

1. jiffies/CNT/PRD/Timer0 remainder/步数派生计数：**如实出现在层 1 严格 diff**，分类器归
   源 C（须满足「差值=Δ 的可归因常数」——P10 的「CNT 恒差 +1832=458×4」同款闭包）；
2. 源 C 差若**流入控制流**（期限判断/IRQ 资格/调度决策/事件顺序分叉）——**升级 unexplained**
   （=回归）。依据=d1-acceptance §3.4 预测 2 的既有规则原文：「一旦差值影响期限判断、IRQ
   资格、调度决策或事件顺序，即升级为行为差异（预期不发生，但按证据说话）」；
3. 不采用过滤/normalize 的理由：过滤=丢证据（真回归可能藏进时间噪音）；「多一字节=回归」
   的用户定调落在**层 3 判定**而非层 1 报告——三层报告把「如实」与「判定」分开：
   **层 1** 严格 diff（全部差如实列出，不吞）→ **层 2** 分类账（每差标注 A/B/C/D/unexplained
   +归因链）→ **层 3** 判定（unexplained==0 ∧ 预登记二级差全部命中 ∧ 改道集==预声明 →
   PASS；否则 FAIL=回归）。

**判定器反例自检（AZ 模式，防空真）**：①故意破坏窗口字节（如桩地址改 1B 重建镜像）→
A/B 判定器必须 FAIL（差异落入 unexplained——证明判定器有分辨力）；②故意注入 RAM 偏差
（篡改候选世界一个白名单外字节）→ 必须 FAIL。两条进 fast tier 单测（实施路落码）。

**与既有 COMPARISON_BASIS 的边界承接**：外设寄存器写不在逐步签名内（无钩子）[代码
:286-288]——A/B 终态比对补一条外设投影 report 的整体 diff（final_report_diffs 已有），差异
按同四源分类（例：候选侧 JL_AUDIO/JL_ANC 写记录因改道路径不同而差→源 D）。

## 3. 前置依赖与执行顺序

```
（现在即可）R2-COT-01 / R4-COT-01 公共前缀 parity ──┐
                                                    ├─ 只依赖：候选 loader 构造（§1.2）
（进行中）D1 过线（BJ 梯队）──────────┐             │
                                      ├─ 官方镜像 D2 用例通过（§6.1/§6.2）
（前沿推进）过 0x0600db3c 键循环相位 ──┘             │
                                                      ├─ R2-COT-02..05（L0/L1 用例逐层解锁）
（前沿推进）RCSP/任务派发相位 ────────────────────────┼─ R4-COT-02..04（分发器半程）
                                                      └─ R2-COT-06 / R4-COT-05（L3 全程）
```

- **可提前项**：①候选 loader+KNOWN_SITES+self-check 扩展（纯 tools/ 实施路，不依赖任何
  前沿）；②R2/R4-COT-01 公共前缀 parity——两个场景的前沿都在 0x0600db3c 之前，**D1 未过线
  即可跑**（§1.4）；③A/B 三层报告与判定器骨架（用 crc_bankb 前缀即可开发+反例自检，注入
  段空跑）；④R2-COT-04 等价负例的断言面设计复核（静态，已完成于本表）。
- **不可提前项**：R2-COT-02/03（窗口执行）依赖前沿过 0x0600db3c（R2-HEA-06 同一缺口）；
  L1 注入依赖 D1 事件循环；R2-COT-05/06、R4-COT-02..05 按层依赖如上。
- **与 D2 官方用例的关系（用户定调的顺序）**：同注入序列先在官方镜像过 D2-a/D2-b 闭环
  （§6.1/§6.2），再进候选世界——官方侧 D2 记录（d2a/d2b 档案）是 A/B 双世界里「官方世界」
  行为断言的既有依据，候选用例不重复验收官方侧行为，只做 A/B diff。
- **真机授权不被替代**（§6.5 原文承接）：全绿后剩刷机授权（R2/R4 候选 UNTESTED 不刷机）
  与 BLE 联调授权（R3），「条件仿真通过」是上真机前的最强证据。

## 4. 验收记录预填：id 规则 + 底稿

**id 规则裁定 [设计]**：任务卡口径「COT-XX-NN」与 acceptance_record.py 的
`CASE_ID_RE = ^[A-Z]\d{1,2}(-[a-z])?(-[A-Z][A-Z0-9]{1,5})?-\d{2}$` [代码 :43] 不兼容
（COT 三字母不匹配路线段 [A-Z]\d{1,2}）。裁定：**COT 作为覆盖面缩写进第三段**——
`R2-COT-NN` / `R4-COT-NN`（匹配现有正则、零改 tools/），挂在既有 R2/R4 线下与 r234 面板
天然同表；不扩正则（扩=改 tools/，且四段语义「路线-分组-覆盖面-序号」里 COT 本就是覆盖面
属性）。档案目录 `docs/acceptance-samples/cot/`（照 d2b/r234 先例进 git，运行态副本在
analysis/local-checks/acceptance/，git 忽略）。

预填记录九栏（§8 格式）本路已落盘 `docs/acceptance-samples/cot/` 共 11 条
（R2-COT-01..06、R4-COT-01..05）：结果全部 **NOT_RUN**（R2-COT-02/03/06、R4-COT-05 四条
带显式 blocked_missing 语义但按校验器口径 NOT_RUN+未覆盖项挂依赖——BLOCKED 留给「已开跑
被模型缺口拦住」的用例，与 d2b INJ 先例一致）；断言引用本文档节号+源文档锚点；模型假设
逐条挂（候选 UNTESTED/直调层语义/DMA 投影不涉及——R2 音频链不注入数据，只走模式链）。
`README.md` 面板由 `acceptance_record.py --summary` 再生成。

## 5. 诚实边界（必须随设计一起读）

1. **纯设计，未实施**：loader/KNOWN_SITES/判定器/注入场景全部是规格，未改 tools/、未跑
   任何场景；§1.4 的表尾跨桶行为、§2.2 的分发器可调性、§2.1 的 cmd 11 键绑定都是运行
   观测项，实施路复核后如不符先改本设计再动手。
2. **候选镜像 UNTESTED 不变**：本设计不构成任何候选的运行验证声明；全部候选保持
   UNTESTED、EQ 候选 67ba97ef 状态不变、互斥关系不动（AGENTS 不变量）。
3. **A/B 等价的语义边界**：unexplained==0 只证明「差异全部归因到四源+预登记二级差」，
   是**回归检测**语义（候选没改不该改的东西），不是候选功能正确性的证明——功能正确性
   由各用例的行为断言（观察点逐条）承担，两者都在层 3 判定内但分列输出。
4. **R2-COT-03 的 stop/release 同步等待**：close 链 post cmd4/cmd9 给流服务线程并同步等
   完成（0x06074192 族 [文档：hearing-aid §10.1]）——若世界无流服务任务上下文（D1 后
   事件循环内的 jlstream 任务是否已建是运行观测项），停在这里=诚实停（BLOCKED 归流服务
   线程模型件），不合成完成。
5. **L1/L3 注入的既有边界承接**：post 手势事件只算分发/动作层测试（d1-acceptance §6.1
   codex 反例）；RCSP 相位的注入面（BLE 帧/异步 trampoline 的缓冲生命期 [文档：r4 §5.3]）
   归 R4-COT-05 且依赖前沿，本设计不预估到达步数。
6. **白名单地址表是实施产物**：源 B 的载荷写集地址表、源 C 的时间类地址表在实施时从
   载荷源+运行实测生成并钉进单测（本设计只定义闭包规则与归因方式）；表漂移=单测红。
7. **任务卡两处假设的修正已如实标注**（§1.4 首差异≠窗口；§2.1 cmd 7 不达 R2 窗口——
   key-action-map §4.4 钉死），修正依据全部为已合并静态证据。

## 6. 红线核对（本路自查）

- 未改 tools/ 任何文件（本文件与 acceptance-samples/cot/ 为 docs/ 新增，git 状态可查）；
- 规格全部出自已合并实证（analyze_image_trace 源码/hearing-aid-l1-payload/r4-command-hook/
  snapshot-design/d1-acceptance/key-action-map/d2b-injection-blueprint/peripheral-input-
  simulation/HANDOFF 三镜像节），出处随行；未钉死值标「运行观测项」；
- 候选 sha/差异字节数全部引构建文档钉死值，无私有路径/私有工件名进本文；
- 提交不加 Co-Authored-By；`check_local.py --units` 绿（含 acceptance_record 对预填档案
  的 --validate）。
