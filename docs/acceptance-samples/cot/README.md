# 候选镜像联动用例验收面板（路 BT / sim-coimg 预填 2026-11-14）

本目录=d1-acceptance §6.5（候选镜像 D2 用例集）的预填验收记录（进 git，照 d2b/r234 模式）：
R2 辅听 L1 接驳联动 6 条（R2-COT-01..06）+ R4 key30 命令接驳联动 5 条（R4-COT-01..05）。规格
出处=docs/candidate-image-cotest.md（路 BT 设计文档）；镜像=l1_slotram（906184b2）/r4-keyhook
（8e9a47d0）两离线候选，均 UNTESTED 不刷机。id 规则：COT 为覆盖面缩写进第三段（R2-COT-NN，
匹配 acceptance_record 的 CASE_ID_RE；任务卡「COT-XX-NN」前缀形式与校验器正则不兼容的裁定
见设计文档 §4）。

结果全部 NOT_RUN（预填）：可立即执行项=R2/R4-COT-01 公共前缀 parity（两场景前沿均在
0x0600db3c 之前，不依赖 D1；条件前沿档 19,139,239 已跨 C0≈447k，判定按两窗口径——
2026-11-15 路 L2AC 修订：[0, C0) 四条口径+[C0, 停点] 等 own-step 三层清单，
可执行判定式见 docs/l2a-differential-criteria.md，A1/EQDIV/E1-PAR-01 证据链）；其余按注入层级 L0-L3 挂前沿/D1/RCSP 相位依赖（设计文档 §3
前置链）。R2-COT-02/03 过线后解锁 r234 档案 R2-HEA-06，R4-COT-02/03/05 解锁 R4-VM-05。

- 复核合法性：`python3 tools/acceptance_record.py --validate docs/acceptance-samples/cot`
- 再生成本页表格（会整页覆写本文件，头部需手工补回）：
  `python3 tools/acceptance_record.py --summary --dir docs/acceptance-samples/cot --output docs/acceptance-samples/cot/README.md`
- 记录格式与校验规则：docs/d1-acceptance.md §8/§8.1；用例规格：docs/candidate-image-cotest.md。

# 验收记录汇总(§8 九栏格式)

目录: docs/acceptance-samples/cot

| 用例 id | 结果 | 证据等级 | 断言引用 | 模型假设 | 未覆盖项 | 记录时间 |
|---|---|---|---|---|---|---|
| R2-COT-01 | NOT_RUN | none | candidate-image-cotest §1.4：首差异指令≠接驳窗口（任务卡假设修正）——fence 立即数与 config.dat index 两族在 boot 期即被消费（P1/P3 先例，HANDOFF 三镜像节）；公共前缀判定口径=两窗（§1.4 修订）：[0, C0) 窗口/载荷零消费+分歧段∈已知族+reconverge+停点 parity；[C0, 停点] 等 own-step 三层清单+跨停点闭式（l2a-differential-criteria；A1 证明 C0 后零控制流分歧机制性必破=交付相位彩票，EQDIV 证明事件计数等价判据可用，E1-PAR-01）;<br>§1.2：loader 前置断言五件（UFW sha 906184b2/独立校验器 CRC 全过/bootloader[:0x2000]==official/PLAIN_LENGTHS 0xE31E0（BU 实测修正：plain 长度=flash 末端−0x2020，cotest §1.2 工具就绪段）/差异分类账==hearing-aid-l1-payload §12.3 逐区域 472 字节账）——分类账不等即拒载;<br>§2.3 层 3 判定：本用例无注入，白名单源=A（构造差）+C0 后三层清单基线（相位彩票/等待域差集在本用例归档，供 COT-02..06 作基线）；两者之外任何差=unexplained=回归;<br>COMPARISON_BASIS 承接（analyze_image_trace）：逐步签名=核/前后 PC/flags/16 GPR/该步全部 guest RAM 写；外设寄存器写不在逐步签名内；轨迹一致≠镜像等价，只证明本前缀未消费差异 | 5 | 4 | 2026-11-15T00:58:00 |
| R2-COT-02 | NOT_RUN | none | candidate-image-cotest §2.1 R2-COT-02：窗口半程=桩→载荷→API 全链+固件原生改道路径（L0 层，S 路键链直调先例）；观察点逐条=W-窗口/W-载荷/W-槽/W-簿记;<br>§2.3 白名单四源闭包：预期差异集=源 B（载荷+桩写集：槽 0x125574、stream 对象/11 节点堆区、时钟链表节点）+源 D（官方走 TRANS apply fn vs 候选走 OFF apply fn+open 的 ANC 引擎配置路径差——桩语义=原分支改走另一条固件原生路径）+源 A/C；unexplained==0 才 PASS（多一字节=回归）;<br>hearing-aid-l1-payload §11.2 桩形状：进入状态 r4=apply flag/r7=QCY 基址；重放 ctx getter→改取 OFF 槽 [ctx+120]→apply→open→goto 尾声；§9.4.2 的「接驳后完整路径未在仿真跑」欠账由本用例解锁;<br>§2.3 判定器反例自检（实施路）：故意破坏窗口桩地址 1B 重建镜像→A/B 必须 FAIL（分辨力证明） | 5 | 3 | 2026-11-15T00:58:00 |
| R2-COT-03 | NOT_RUN | none | candidate-image-cotest §2.1 R2-COT-03：生命周期 open→close→再 open——close 半链以槽值为唯一 stream 句柄（hearing-aid §12.1：RAM 槽是 close 真机可用的前提），close 早退路径（槽==0）在 boot 后首次离开通透时被真实走过;<br>hearing-aid §10.2 close 载荷语义：槽==0 幂等早退；三段式 stop→release→notify 与固件 7 个 close 调用点惯用法逐 call 同形；§10.1 event_notify(11) 的 "mic_effect" 分支=clock_free（与激活分支 clock_alloc 配对）;<br>§2.3：二级差预登记=二次 open 的 stream 地址与第一次不同（堆不复位）——候选世界内自洽即可；白名单源 B+D+A/C，unexplained==0 才 PASS;<br>本用例解锁 r234 R2-HEA-06（BLOCKED：接驳后完整路径执行）的执行半边——过线后该记录补 PASS 并引用本条 | 5 | 4 | 2026-11-15T00:58:00 |
| R2-COT-04 | NOT_RUN | none | candidate-image-cotest §2.1 R2-COT-04：cmd 7 等价负例=「候选不碰无关功能线」的最强负例——双击链路（任务卡原文「多功能键双击=模式切换 cmd 7→ANC 链」）经 key-action-map §4.4 钉死**不达 R2 窗口**（cmd 7 走另一台 dispatch 0x0600dab6）；A/B 两世界此用例应零增量差（观察窗终点事件面差集 ⊆ 注入起点已归档的 C0 后三层清单基线——+458 偏移对在 C0 后本就存在基线差，逐字节全同不可达；新增任何差类=回归）;<br>key-action-map §4.1 cmd 7 全链（0x124a04 取值族 {1,3,4}、1↔3 往复、失配只发 BLE 0x2E）+§5 对照表 cmd 7 行观察点;<br>BW 双击注入先例（0x60252c6 入口消费链实测，等价类证据）；L1 注入只算分发/动作层测试（d1-acceptance §6.1 codex 反例承接——每类代表动作的完整 ADC 闭环归 D2-a A 组，不在本用例）;<br>§2.3：本用例预期增量差异集=空（源 A 与 C0 后三层清单基线已归档于 R2-COT-01；注入段零增量差） | 4 | 3 | 2026-11-15T00:58:00 |
| R2-COT-05 | NOT_RUN | none | candidate-image-cotest §2.1 R2-COT-05：音量键交汇=key-action-map §4.4 第二交汇点——0x06017a58/0x06017b22 的 0x0603370c 门读 [QCY+592]（pc_mic/mic_effect 播放器对象槽），辅听活动时音量键在本路径被吞（返回不执行媒体音量）；这是动作覆盖×辅听接驳的可测交汇;<br>§2.3：预期差异集=源 D（门读值差→行为差：官方 post 事件族/候选不 post——候选的功能预期差，不是回归）+源 B（辅听激活写集）+源 A/C；二级差预登记=QCY+4434 与事件族;<br>辅听激活后的音量接管路径未定位（key-action-map §4.1 标注）——本用例只断「媒体音量链不走」，不断言接管路径正确性 | 4 | 3 | 2026-11-15T00:58:00 |
| R2-COT-06 | NOT_RUN | none | candidate-image-cotest §2.1 R2-COT-06：App 0x17 直设与按键不分叉——hearing-aid §11.4（路 AC）钉死 mode apply 全部经 0x0600db3c（键循环同一函数），既有 6 窗口候选已拦截 App 直设的进/离通透，无需新窗口;<br>§11.4 wire→内部模式映射：wire 1→db3c(2)、wire 2→db3c(1)、wire 3→db3c(3)；flag 恒 1（回调传入）；同模式重入被 0x0600db66 b[r2+5]==r5 早退+App 查态早退兜在窗口之前;<br>预期差异集同 R2-COT-02（源 B+D+A/C）;<br>BLOCKED 依赖=RCSP 相位（R4-VM-05/R3-APP-05 同一缺口族；r234 README 全局待授权项第三行的前沿推进类） | 3 | 3 | 2026-11-15T00:58:00 |
| R4-COT-01 | NOT_RUN | none | candidate-image-cotest §2.2 R4-COT-01：公共前缀 parity 的 r4 对——fence 0x060E322E/config.dat index（slotram 同族）+注册表槽与 keyhook 零消费；白名单源=A+C0 后三层清单基线（同 R2-COT-01 修订），两者之外任何差=回归;<br>§1.2：loader 前置断言（sha 8e9a47d0/PLAIN_LENGTHS 0xE322E（BU 实测修正：plain 长度=flash 末端−0x2020）/差异分类账==r4 §3.3 逐区域账：vs 官方 554=slotram 472+注册表槽 4+keyhook 密文 78）;<br>r4-command-hook §1.2 等价安全检查承接：槽原字节 00000000+全 app 引用扫描零命中+消费面钉死（本用例动态面=前缀零消费的旁证） | 4 | 3 | 2026-11-15T00:58:00 |
| R4-COT-02 | NOT_RUN | none | candidate-image-cotest §2.2 R4-COT-02：分发器写路径半程——预期差异集=源 B（载荷写集：VM 记录区+resp 缓冲）+源 D（官方静默退出 vs 候选执行=功能预期差）+源 A/C;<br>r4-command-hook §2 载荷依据表：set_config 入口 0x06001f12 由路 AB 函数级仿真直调全链实证（写→同机读回→重启重扫仍在）；key 30 无需注册（VM match key-1<256 全命中）；静默不回 n=0=分发器 0x0600f942 ifs(n<1) 既有行为;<br>§2.3 判定器反例自检：故意改坏载荷字节/回执形状→必须 FAIL;<br>本用例解锁 r234 R4-VM-05（BLOCKED：命令分发链执行）的半程半边——RCSP 相位到达后 R4-COT-05 补全程 | 4 | 3 | 2026-11-15T00:58:00 |
| R4-COT-03 | NOT_RUN | none | candidate-image-cotest §2.2 R4-COT-03：读回闭环——路 AB 已实证「直调 set/get_config 分派器的读写往返」，本用例增量=经分发器+载荷到达 get_config（R4-VM-05 缺口的「handler 到达」半边）;<br>r4-command-hook §2：读形状（len 1）与写形状（len 9）同帧族按长度分路；FE 01 30 原路径保留断言=候选只写 +8 setter 槽、读路径零改动（§1.2 消费面钉死）;<br>预期差异集=源 B（resp 缓冲写）+源 D（官方无此回执）+源 A/C | 3 | 3 | 2026-11-14T21:40:00 |
| R4-COT-04 | NOT_RUN | none | candidate-image-cotest §2.2 R4-COT-04：其他 cmdid 回归负例——候选只动 0x30 槽，既有命令族行为必须与官方零增量差（差集 ⊆ C0 后三层清单基线，R4-COT-01 归档）；新增任何差类=回归（「候选不碰无关功能线」的 R4 侧负例，与 R2-COT-04 对偶）;<br>r4-command-hook §1.1 表 A 十五项占用：0x17/0x22/0x1d 的 setter 槽未被改动（字节账无注册表区差异除 0x30 项）；分发器走表逻辑（body[0]=cmdid 匹配 +24）对全部 cmdid 同形;<br>§2.3：本用例白名单=源 A+C0 后三层清单基线（R4-COT-01 归档），注入段零增量差 | 3 | 3 | 2026-11-15T00:58:00 |
| R4-COT-05 | NOT_RUN | none | candidate-image-cotest §2.2 R4-COT-05：RCSP 帧全程闭环——解锁 r234 R4-VM-05 的全程半边；预期差异集同 R4-COT-02/03;<br>r4-command-hook §5.2：载荷到分发器的完整路径（BLE→RCSP→0x0600f85e→异步/同步→set/get_config→VM 后端）未在任何仿真/真机跑过——本用例是该缺口的收口用例;<br>R3-APP-01/04 组帧与 bridge 资产复用（KeyStoreFrame/EqWritePolicy 0x30 两形状白名单——形状层已 JVM/Loopback 全过，本用例补固件侧执行） | 4 | 3 | 2026-11-15T00:58:00 |

结果统计: PASS=0 FAIL=0 BLOCKED=0 NOT_RUN=11(共 11 条;BLOCKED=必选用例因模型缺失未过,不是弱化 PASS,§6.4)
