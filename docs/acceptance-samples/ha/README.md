# HAL5 真机验证面板（路 ACCEPT-HA / sim-accept-ha 预填 2026-11-18）

本目录=助听 HAL5 合一候选真机分阶段验证计划的预填记录卡（22 张，全部 NOT_RUN/none）。
计划正文=[docs/hearing-aid-acceptance-plan.md](../../hearing-aid-acceptance-plan.md)；本路只出
计划与预填卡，未连接手机/耳机、未刷写、未取得任何新的设备授权。候选仍 UNTESTED。

取材：候选与协议=docs/hearing-aid-l5-full.md / l3-mpo.md / l4-eq.md；USB 基建与红线=
docs/usb-recovery.md + AGENTS.md；回执字节=tools/r4cmd/keystore_hook.S；App 侧门=tools/
android-query/（EqWritePolicy/KeyStoreFrame/H3SBridge/assets/index.html 源码复核）。
case id 前缀 H5=本路（H3S HAL5 真机验证），与既有 R2/R3/R4 线互不影响。

## 等待项（两类缺口，逐条挂卡）

| 等待项 | 性质 | 挂在哪些条目上 |
|---|---|---|
| 阶段0a 离线就绪（工具/器材，先于授权） | USB 双向 profile（b79→HAL5 刷写 + HAL5→b79 回滚，现工具只有 r2→b79 单向硬编码）；App key31 白名单/读回入口/保存按钮假成功修复；客观声学测量器材 | H5-PRE-01/02（工具缺口）、H5-MET-01（器材缺口）、H5-BLE-03（静默负例前提） |
| 用户明示授权（两段式，计划 §阶段1b 话术） | 基线+ROM 双备份一段；实际扇区计划出来后再确认刷写（复述 sha 前 8 位 9ad0f088）；回滚预授权在同一段话术内明示 | H5-AUT-01（授权门本身）及其后全部真机卡（BAS/APP/USB/HLT/BLE×4/KEY×3/ANC/AUD/EQ/MPO/DEF/RBK/END） |

注意：HAL5 未带 b79 的 ANC 两态补丁与 -10dB 提示音衰减——刷后 ANC 键回官方三态、提示音
更响，这是预期行为差异（计划阶段 1 §1d 表），不是刷写故障。

- 复核合法性：`python3 tools/acceptance_record.py --validate docs/acceptance-samples/ha`
- 全局面板：[../acceptance-dashboard.md](../acceptance-dashboard.md)（记录改动后
  `python3 tools/acceptance_dashboard.py` 再生成；`check_local --units` 校验一致性）
- 再生成本页表格（会整页覆写本文件，头部需手工补回）：
  `python3 tools/acceptance_record.py --summary --dir docs/acceptance-samples/ha --output docs/acceptance-samples/ha/README.md`
- 记录格式与校验规则：docs/d1-acceptance.md §8/§8.1。

# 验收记录汇总(§8 九栏格式)

目录: docs/acceptance-samples/ha

| 用例 id | 结果 | 证据等级 | 断言引用 | 模型假设 | 未覆盖项 | 记录时间 |
|---|---|---|---|---|---|---|
| H5-ANC-01 | NOT_RUN | none | 完整记录外部再开ANC时现象，不预设自动关闭HA;<br>异常立即结束；若产品要求强互斥，当前实现不满足，不能给该需求PASS | 2 | 2 | 2026-11-18T01:12:10 |
| H5-APP-01 | NOT_RUN | none | 已装全部APK SHA与冻结构建相符，不能只用versionCode=10识别;<br>实际App具备PRE-02所需路径；无simulation/loopback；样本manifest与SHA可追溯;<br>不安装、不清数据、不改权限来完成只读检查 | 2 | 2 | 2026-11-18T01:12:10 |
| H5-AUD-01 | NOT_RUN | none | 3对均可区分HA附加输出与手机直达/普通通透；不走A2DP/HFP声源;<br>setPreferredDevice返回或speaker徽标不足以证明路由；缺观测或不可区分即BLOCKED | 2 | 2 | 2026-11-18T01:12:10 |
| H5-AUT-01 | NOT_RUN | none | 授权绑定完整9ad0f088…SHA、B写区、保留A/VM/设备参数、未测风险及恢复耗时估计;<br>回退是否预授权明确；输入清单包含BLE写/开关/音频，ANC并存和坏魔数另列;<br>只认可明确确认；撤销即停止新事务；候选或profile变化重新审查 | 2 | 2 | 2026-11-18T01:12:10 |
| H5-BAS-01 | NOT_RUN | none | 每轮开机60s内可查询，播放60s无异常，正常关机手势后10s内关闭;<br>ANC键ON/TRANS两态；双击按当前映射循环音乐/游戏/电影，记录实际顺序;<br>无基线异常才允许计划进入HAL5写入门 | 2 | 2 | 2026-11-18T01:12:10 |
| H5-BLE-01 | NOT_RUN | none | 写key30/31精确回执30 01 00；读分别30 08+8B/30 58+88B，逐字节与发送值相等;<br>M95值48 41 01 00 00 00 be 42；EQ头48 41 02 00 0a 00 00 00加十段LE值;<br>每次一个应用请求在途；记录实际MTU及片数；不把GATT回调或UI成功当VM证据;<br>写超时先读回消歧、不自动补写；首次失败保留 | 2 | 3 | 2026-11-18T01:12:10 |
| H5-BLE-02 | NOT_RUN | none | 各受测拒发例返回错误且TX增量0；明确拒绝所在层;<br>App M(0.3,119.9)/EQ绝对值<40与固件M(0.3,120)/EQ绝对值<=40分列;<br>setHearingCfg的8B坏魔数可过shape门，不虚称bridge拒绝一切协议非法值 | 2 | 2 | 2026-11-18T01:12:10 |
| H5-BLE-03 | NOT_RUN | none | 确有缺失/长度不符证据才可把5s无响应计为预期静默;<br>无独立前提证据时BLOCKED；超时本身不证明key不存在 | 2 | 2 | 2026-11-18T01:12:10 |
| H5-BLE-04 | NOT_RUN | none | 3/3轮两key精确等于重启前值，回执长度/状态/SN正确;<br>只声明持久保存，不以全片SHA跨运行不变为判据 | 2 | 2 | 2026-11-18T01:12:10 |
| H5-DEF-01 | NOT_RUN | none | 仍回30 01 00且读回精确8B/88B坏魔数；不能把这当set_config失败;<br>默认回退预期key30为L2 M95、key31文件默认；两key各自验证，不互相代替;<br>保存失效值可客观验；默认实际曲线缺观测则BLOCKED，不凭相似听感证明;<br>低M回退95可能增大输出；非静音/删key/固件回滚 | 2 | 2 | 2026-11-18T01:12:10 |
| H5-END-01 | NOT_RUN | none | 首轮建议回b79；用户保留HAL5须明确确认，RBK-01仍NOT_RUN不能PASS;<br>保留所有FAIL与BLOCKED，听不出如实记录；不发布整片/身份/原始私人日志;<br>10min无异常只支持有限窗口，不报告长期稳定或医疗助听有效 | 2 | 2 | 2026-11-18T01:12:10 |
| H5-EQ-01 | NOT_RUN | none | 比较流程完成8对且记录听不出/无法判断，不强迫选赢家;<br>本卡PASS仅指比较协议完成，不表示EQ生效；十段生效另需MET-01;<br>每4对休息至少1min；异常/不适/疲劳停止，不追求升音量获得差异 | 2 | 3 | 2026-11-18T01:12:10 |
| H5-HLT-01 | NOT_RUN | none | 手动正常关开机完成，启动60s内可查询；播放60s、空闲120s无复位失联;<br>正常关机10s内完成；BLE无法连接/无法关机停止功能试验并转已授权恢复;<br>HAL5没有b79两态补丁；按候选三态预期检查，不把广播/280版本视作候选身份 | 2 | 2 | 2026-11-18T01:12:10 |
| H5-KEY-01 | NOT_RUN | none | 5s内可辨一次开、一次关；状态不明不继续盲按;<br>anc_off提示只记请求现象，不代替ANC硬件退出/HA输出证明;<br>啸叫/无法关闭/关机失败立即停止功能试验 | 2 | 2 | 2026-11-18T01:12:10 |
| H5-KEY-02 | NOT_RUN | none | 20/20有效双击各一次预期变化，10开10关，无多翻/漏翻/意外原音频模式循环;<br>人工失误单记不改为固件成功；每周期收回可确认关闭;<br>双击无反应停HA；仅健康且已授权时继续只读BLE诊断 | 2 | 2 | 2026-11-18T01:12:10 |
| H5-KEY-03 | NOT_RUN | none | 3/3轮开机可观察HA关闭，首次双击开、再次关；key值仍一致;<br>未观察RAM槽，不宣称直接证明0x125574为0 | 2 | 2 | 2026-11-18T01:12:10 |
| H5-MET-01 | NOT_RUN | none | 先冻结检测门max(3dB,3×基线标准差)，不事后放宽；不稳定夹具先修再测;<br>十段逐段覆盖并与邻频比较；一段可闻不代表十段通过;<br>MPO判据排除声源压缩/NoiseGate等混淆；缺器材或前提即BLOCKED;<br>只有观测支持的相对变化可声明，无校准不报告绝对SPL | 2 | 3 | 2026-11-18T01:12:10 |
| H5-MPO-01 | NOT_RUN | none | 比较流程完成并接受听不出；本卡PASS不证明MPO精确限幅;<br>不升音源强迫触发阈值；M为数字域dB，M95非不可突破硬上限;<br>写后未重开无差异只记该条件未检出，不断言所有音频块参数状态 | 2 | 3 | 2026-11-18T01:12:10 |
| H5-PRE-01 | NOT_RUN | none | 双向plan独立重算一致；变化仅在B精确允许扇区，0xee000最后提交，扇区数从差分推导;<br>A/bootloader/VM/资源/校准不变；B头仅CRC与长度更新，序号3与设备参数保留;<br>错包/坏CRC/错相位/越界/非VM新变化/旧journal/双读不同全部在首擦前拒绝;<br>完整HAL5回滚与中途中断混合态分开；所有注入中断点有journal绑定处置，未知态拒绝 | 2 | 3 | 2026-11-18T01:12:10 |
| H5-PRE-02 | NOT_RUN | none | key31的89B写data及1B读data可走精确白名单；其它key/坏形状仍零发送;<br>错误JSON及仅排队成功不显示设备保存成功，只有关联回执与读回确认存储;<br>key31坏魔数仅受限固定形状操作；无任意帧写总开关;<br>MTU23/103/247分片重组、SN关联、超时不补写的离线检查通过 | 2 | 2 | 2026-11-18T01:12:10 |
| H5-RBK-01 | NOT_RUN | none | 全片逐字节等本次target；两bank应用为b79、A2/B3，A/资源/校准及最新VM保留;<br>不套用r2六扇区；不要求等首次备份全片SHA；HA key残留如实归档;<br>手动正常重启后连接/播放/关机、ANC六次两态、双击六次音频模式循环与App三态均过;<br>无法进入USB或未知混合态停止并另报方案，不能宣称已恢复 | 2 | 3 | 2026-11-18T01:12:10 |
| H5-USB-01 | NOT_RUN | none | 两遍备份完全相等；执行前非VM变化拒绝，最新VM保留;<br>擦后全FF、块写状态/CRC正确、每扇区及整片逐字节等于本次target;<br>任何擦写/读回错误停止后续步骤，不删journal重跑；不套旧六扇区ORDER | 2 | 3 | 2026-11-18T01:12:10 |

结果统计: PASS=0 FAIL=0 BLOCKED=0 NOT_RUN=22(共 22 条;BLOCKED=必选用例因模型缺失未过,不是弱化 PASS,§6.4)
