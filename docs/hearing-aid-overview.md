# 助听功能线总览（一页导航）

2026-11-18，基于 main e014530。这页回答三个问题：助听这件事做到了哪、每条线的证据和边界在哪份文档、离真机验证还差什么。只做导航，细节以各线文档为准。**所有固件候选 UNTESTED、从未刷机。**

先交代三个词：**辅听（mic_effect）**是固件里 SDK 自带的环境声放大通路（麦克风进、均衡、压缩、限幅、喇叭出）；**MPO**是最大输出上限（输出限幅）；**WDRC**是宽动态范围压缩——助听器典型的「小声多放大、大声少放大」机制。**VM key**是耳机里掉电不丢的配置槽，App 写进去、固件开机后读。

## 全景表

| 线 | 一句话结论 | 文档 | 候选（全部 UNTESTED，未刷机） | 关键诚实边界 |
|---|---|---|---|---|
| L0 镜像参数 | 离线把辅听链参数改成「1kHz +39dB + 输出限幅」演示值，重算 CRC 打包，字节级往返闭环（`--hearing-aid-demo`，默认 M=100） | [hearing-aid-l0-demo.md](hearing-aid-l0-demo.md) | 演示镜像，与官方差异 29 字节 | 旧「只改末点 out=100」当上限的语义有误（DRC 末段线性外推），已勘误：L2 改成三点水平尾段 |
| L1 双击键 | 双击多功能键从「音频模式循环」改成开/关辅听；open/close 载荷与双向接驳窗口全部离线落地 | [hearing-aid-l1-key7.md](hearing-aid-l1-key7.md)、[hearing-aid-l1-payload.md](hearing-aid-l1-payload.md) | `7465cd43…`（key7）/ `5a5f2ef7…`（含 close）/ `906184b2…`（slotram 加固） | 接驳点在 ANC 键循环，主前沿没推进到那——完整路径只有静态证据；与 L3/L4/L5 占同一个接驳窗，互斥不能同刷 |
| L2 MPO 曲线 | 把 Drc1Eff（辅听链唯一 WDRC）写成三点水平尾段 (0.3,0.3)/(M,M)/(X,M)，过驱动输入都映射到 M——全带输出上限（`--hearing-aid-mpo M`） | [hearing-aid-l2-mpo.md](hearing-aid-l2-mpo.md) | 参数开关，随组合候选携带（L5 里 M=95） | M 是调音工具的数字域 dB 刻度，不是 SPL 声压；attack=100 稳态上限成立不等于瞬时峰值不越限；单带 WDRC 做不了每频独立限幅（App 每频上限取 min 后全带生效） |
| L3 key30 MPO 热更 | App 下发 → BLE cmdid 0x30 → VM key 30 → 双击进辅听 open 成功后 apply_mpo 读 key、套官方 76B 模板、`jlstream_set_node_param` 热更 WDRC；顺手修了 keyhook 假成功回执 bug | [hearing-aid-l3-mpo.md](hearing-aid-l3-mpo.md) | `88483a67ce4351bfe15aeeef4a34bcfd13a11d2cba7ef2ed07a742c69cdbe182`（630B） | 消费链零执行证据——只有静态+字节层验证；「保存回执成功 ≠ 参数生效 ≠ 出声」三个层级分清 |
| L4 key31 EQ 十段热更 | key 31 携带 88B 十段 float32 EQ → open 成功后 apply_eq 逐段发 EQ_SEG 44B 消息热更 Eq1Eff 输入均衡（`--hearing-aid-l4-eq`，隐含 L3 全链） | [hearing-aid-l4-eq.md](hearing-aid-l4-eq.md) | `2b31e0203bfde1478cb11676334e6eba49e7a5d8af23932505a183fd9bfb7323`（943B） | 十段非事务——中途失败留下部分曲线（fade 0.1f 平滑掩盖）；消费链零执行证据同 L3 |
| L5 合一候选 | `--hearing-aid-full`（要求显式 `--hearing-aid-mpo M`）字节上就是 L4+L2 组合，零新增载荷；956B 文件账逐字节归因到六个桶 | [hearing-aid-l5-full.md](hearing-aid-l5-full.md) | `9ad0f0881a7996cb4dea81477d8731aa9b4db69a6f352603118663d68eda5465`（M=95） | key30 运行时合法写入直接覆盖 95（App 各频段默认上限 110，直接点保存会抬到 110），95 不是硬上限；未带 ANC 两态/提示音衰减，行为和 b79 有别 |
| lfix 修复 F1 | HAL5 基座 + open/close 修复载荷：A1 open 前 ANC 退出等待（os_time_dly 1 tick×60 轮询≈590ms，超时取消）+ B1 场景值 10→16（调用保留）+ B2 start 后 node_ioctl(0x2000C,1)，可选 A3 128M 时钟压舱（BSS 持有标志门控释放）；双产物 F1a/F1b 仅差 A3（plan §4-C 裁决；codex 实施审五条 P1 已吸收，见 impl §9） | [hearing-aid-lfix-impl.md](hearing-aid-lfix-impl.md)、方案 [hearing-aid-lfix-plan.md](hearing-aid-lfix-plan.md) | F1a `73b45e659d127452697b5580b2474ef481ce9b8118f3f5dfa8b9c731bb4fef48` / F1b `f16576687dae42a583149aa9f8a2f1b89dc777228245a27c289f0faeec00f2c1`（M=95，UNTESTED 未刷） | 触发背景=HAL5 真机左耳刺耳噪声+open/close 循环蓝牙断联；静态补丁无法验证运行行为——A1 依赖的 +29 清零站点已定位（0x0600db36-38/0x060276e4-e6，但清零≠硬件重配结束），A3 只判「高频保持相关」且续航/温升未测；F1 不在既有回滚 profile（验收前需扩） |
| App tab+样本 | R3 WebView 壳第三 tab：十频段横排、上限弹窗（60–120）、±80 增益条、全带 MPO 聚合行；h3sha 桥播测试单频（125–16k 预设+任意频率）和 12 段二级样本（语音/音乐/自然，全 CC0/Public Domain） | [../tools/android-query/README.md](../tools/android-query/README.md)「助听 tab」节 | —（App 非固件；0.6.0-r3-webview，已装机 ace，2026-11-18 00:10） | 助听 tab 的两个保存按钮（MPO/EQ）尚未与真机联调，对着 b79 固件也消费不了（要 L3/L4 候选）；扬声器强制路由 `setPreferredDevice` 真机蓝牙场景未验证；语音样本是 edge-tts 合成，不是真人录音 |
| 验证计划 | 6 阶段 go/no-go（基线→刷写→健康→参数→听感→收尾）+ 两段式授权话术 + 22 张 NOT_RUN 预填卡（[acceptance-samples/ha/](acceptance-samples/ha/)） | [hearing-aid-acceptance-plan.md](hearing-aid-acceptance-plan.md) | —（计划无候选；验证对象=L5 `9ad0f088…`，恢复目标=b79） | 计划制定时未连接设备、未刷写、未取得新授权；此后装机已完成（见 App 节），刷写与参数授权仍缺；三档授权每档都要用户明示 |

各线怎么串起来：L5 候选 = L1 双击开关 + L2 兜底限幅 + L3/L4 的 App 热更通道；App 助听 tab 是遥控器和试音台；验证计划把这一切拉到真机。L0 是最早的参数层可行性演示，结论已被 L2 修正收编。

## App 现在能干什么（已装机 ace 的当前形态）

ace（一加 Ace / CPH2423）已于 **2026-11-18 00:10** 装入本仓 tools/android-query 构建的最新版 `cn.felix021.h3squery`（0.6.0-r3-webview），launcher 就是 WebView 壳三 tab，构建里已固定目标耳机的 BLE 地址：

- **耳机 tab**：连接/电量只读、写入门控、ANC 三态、音频模式、EQ 七预设+自定义 ±12、按键自定义 3 键×5 手势、提示音音量、升级入口（只认固定候选摘要）。
- **助听 tab**：横排十频段、上限弹窗（60–120）、±80 增益条、全带 MPO 聚合行；测试单频（125–16k 预设+任意频率）与 12 段二级样本（TTS 语音 / CC0 音乐 / 自然场景）经 h3sha 桥播放；**MPO 与十段 EQ 两个保存按钮**（bridge.setHearingCfg → key30 8B / setHearingEq → key31 88B）。
- **USB tab**：M2 只读/M3 武装刷写的骨架，链路未验证。

要分清「界面在」和「链路通」：两个保存按钮对着当前 b79 固件发不出去也不被消费——key30/key31 的固件侧消费链在 L3/L4 候选里（未刷），App 侧命令通道（白名单/读入口/回执判据/软回退）已就绪（见下节 §0b）；样本播放走手机扬声器，与耳机固件无关，现在就能用。

历史：ace 此前长期装 0.4/code6 旧客户端（USB 恢复期间装回的已验证版本，原生查询+b79 固定候选升级、无助听 tab），2026-11-18 00:10 起被上述新版替换。版本身份按计划 §0d 钉死（APK SHA256、签名证书摘要、构建来源 commit），进会话冻结清单。

## 真机验证前还缺什么

1. **用户授权门（硬前置）**：USB 刷写与参数测试两档授权还没给（候选一个都没刷）。装机动作已完成（见上节）；基线复测等只读设备动作仍需明示授权。话术要素（候选 SHA 前 8 位复述防拿错包、写入范围 bank B、未测风险、回滚预授权）见计划 §0.1。
2. **刷前缺口（计划 §0b，已修复，2026-11-21）**：App 侧——key31 写帧白名单（EqWritePolicy 放行 key31 89B 写/1B 读）、key31 读入口（getHearingEq）、保存按钮四态回执判据（expectAck 精确 `30 01 00`，`{ok:false}` 显示失败）、key31 坏魔数软回退工具（invalidateHearingEq）均已落地（PREGAP 8fc0307）；App/固件参数门边界已对齐（MPO 0.3<M<120.0 独占、EQ |gain|≤40dB 含边界——本轮把此前 119.9/±40 独占的偏窄门改齐）。默认上限 110 仍会把 L5 候选 bake-in 的 M=95 抬掉——这是「App 是遥控器、按用户所设写 key30」的设计取舍而非白名单缺口（保存前反馈行已显示将写入的 M 值），保留如实标注。USB 侧——b79↔HAL5 双向 profile 已落地（tools/usb-recovery/accept_usb.py：hal5 flash/rollback、f1a/f1b 同层回退，不放宽 r2→b79 旧保护）。这些解决后，剩授权门（下 1）与 App 身份钉死（下 3）仍未做。
3. **App 身份钉死（§0d）**：装机已完成，但 APK SHA256、签名证书摘要与构建来源 commit 还没进会话冻结清单；版本号不足以区分同版本重建 APK。
4. **预期管理**：HAL5 是相对 b79 的增量包——ANC 键回官方三态、提示音音量不衰减（约响 10dB），验收不能按 b79 行为预期（计划 §2.4）。

## 延伸阅读

- 预研：[hearing-aid-eq-feasibility.md](hearing-aid-eq-feasibility.md)（辅听链结构与参数区逐字节钉死）、[eq-gain-limit.md](eq-gain-limit.md)（EQ 增益无 clamp，+39dB 算法侧全放行）
- L1 证据底座：[hearing-aid-l1-wiring.md](hearing-aid-l1-wiring.md)（激活链调用面穷举=固件完全未接线）、[hearing-aid-l1-payload.md](hearing-aid-l1-payload.md)（载荷设计与六窗口接驳）
- 恢复路径：[usb-recovery.md](usb-recovery.md)（b79 恢复先例、工具边界与授权模型）
- 前沿与基建：[HANDOFF.md](../HANDOFF.md)、[snapshot-resume.md](snapshot-resume.md)（前沿推进臂位 25.6 分钟→0.5 秒）
