# D3 验收面板（真机一致性·案头就绪预填，路 BS / sim-d3prep 2026-11-14）

本目录=D3（模型↔真机行为一致性，d1-acceptance §7）的记录底稿（进 git，照 d2a/d2b/r234
模式）：**BS 预填 6 条 NOT_RUN + SESS 增 D3-SFR-03（C3 补录）与 D3-PRB-02（打包
dry-run PASS）共 8 条**——D3 的执行半边全部等真机授权或观测手段，预填只固化
「身份/证据/失败判据」三要素与诚实边界。id 规则=`D3-XX-NN`（XX=比较面缩写：PRB=观测
手段会话/EVT=事件流/SFR=寄存器配置/TIM=时窗/AUD=音频输出；设计文档
[docs/d3-preparation.md](../../d3-preparation.md) §4）。运行态副本在
`analysis/local-checks/acceptance/`（git 忽略）。

## NOT_RUN 的分野（一句话）

D3 与 D1/D2 的记录不同：执行半边在**仓库外**（真机会话/联调/观测手段），本面板固化的
是请求时的证据结构与失败判据——「案头就绪」=工具/计划/判据齐，授权后按记录执行、
按记录收口（inconclusive 也是合法结果，不是重试理由）。D3-PRB-02 是唯一离线可收口
的记录：打包工具的 dry-run 全绿（PASS）不授权任何真机会话。

## 等待项地图（7 条 NOT_RUN 的依赖，逐条挂到具体记录）

| 依赖 | 性质 | 挂在哪些条目上 |
|---|---|---|
| 只读观测会话授权（FD07/FD05，AGENTS「Device actions」口径：文档不授权设备动作） | 四条 SFR/PRB 记录的共同前提；工具就绪=**会话打包就绪**（read_rom.py `--session-pack [--dry-run]`，65 命令/零写断言/29 单测，docs/d3-session-pack.md；C1 五读点计划=PRB-01+SFR-01/02/03 一次会话） | D3-PRB-01/ D3-SFR-01/ D3-SFR-02/ D3-SFR-03 |
| BLE 联调授权（与 FD07 是两类授权，§3 模板分型） | R3/R4 三线挂账的同一授权（r234 README 全局待授权项清单第 2 行） | D3-EVT-01 |
| 真机事件日志面（观测手段本身，现不存在） | §7 时窗行「真机侧观测手段先落实」缺口——手段具备前 NOT_RUN 整体等待 | D3-TIM-01 |
| 模型侧 D2-b 场景执行+真机三明确（输入/采集/容差） | D3-AUD-01 双重前置（模型半边=D2-b-INJ 面板两依赖；真机半边=校准手段） | D3-AUD-01 |
| 观测会话的先对先销原则 | 已有真机证据（ROM 窗/整片备份/fm1 ISA 捕获）对照面已在 d3-preparation §2.1 盘点——不重复立项 | （面板外，机制层） |

语义说明：NOT_RUN=未跑（计划/判据已固化）；BLOCKED（必选用例因手段缺失未过=§6.4
「没过」）保留给授权后撞上无法解除缺件的场合，预填阶段不占用。

- 复核合法性：`python3 tools/acceptance_record.py --validate docs/acceptance-samples/d3`
- 再生成本页表格（会整页覆写本文件，头部需手工补回）：
  `python3 tools/acceptance_record.py --summary --dir docs/acceptance-samples/d3 --output docs/acceptance-samples/d3/README.md`
- 记录格式与校验规则：docs/d1-acceptance.md §8/§8.1。
# 验收记录汇总(§8 九栏格式)

目录: docs/acceptance-samples/d3

| 用例 id | 结果 | 证据等级 | 断言引用 | 模型假设 | 未覆盖项 | 记录时间 |
|---|---|---|---|---|---|---|
| D3-AUD-01 | NOT_RUN | none | d1-acceptance §7 音频输出比较面：仅在输入、采集方法、容差三者明确时比较——三缺一即本记录维持 NOT_RUN（诚实收口，不降级凑数）;<br>「有声音」与「音质/ANC 效果」分开（§6.4 游戏低延迟同口径）：本记录只验收前者；后者列为永不覆盖项除非获得校准手段;<br>若硬件段（JL_ANC 滤波块/模拟前端）由硬件处理软件只配寄存器——该段未覆盖须明确记录，不借软件 handler 被调用宣称音频闭环（§6.2 codex 原话口径） | 3 | 4 | 2026-11-14T21:18:45 |
| D3-EVT-01 | NOT_RUN | none | d1-acceptance §7 事件流比较面（限定输入下事件与最终模式一致）：真机回执形状与模型/R3 组帧族逐字节对照（app-protocol §4.4 两处 [未钉] 定案）;<br>R4-VM-04/05 的真机半边：0x30 回执 30 01 status/30 08 v0..v7 形状与 Loopback 桩对照；R4 候选刷入时（另行刷机授权）0x30 冲突面监控;<br>「设备已应用」边界钉死（app-protocol §4.4：App 侧有时间过滤，响应≠应用）——以重连后读回为准，不以回执时序为准 | 3 | 3 | 2026-11-14T21:18:45 |
| D3-PRB-01 | NOT_RUN | none | d3-preparation §1.3 C1：控制组三读全匹配且 0xf27f4a..4d == 0c 3d f4 39（LE u16 begin=0x3d0c/size=0x39f4，路 X 三方推导）→四字节升 measured，p11_heap_info_bytes 由 derived 转实测 fixture;<br>控制组匹配但描述符不符→如实记录字节、推导链复审（不拟合）；P11 RAM 控制组（0xf20080）全 0/全 FF→保持性未证=inconclusive 挂账;<br>安全边界：只读命令；FD07 仅经 loader 路径（ROM 原生 FD07 原地变换严禁指向 P11 窗）；128B 实测分块；零擦写/零 OTP/零跳转 | 3 | 3 | 2026-11-14T21:18:45 |
| D3-PRB-02 | PASS | none | 命令计划=65 条（INQUIRY 1+FB06 46+FB08 1+FC0A 1+FD07 16=8 读点×双读），步骤连续、读地址与 SESSION_PACK_PLAN 逐项一致（§1 命令总表）;<br>零写断言：进入序列后全部命令为 FD07 读；FB00/FB01/FB02/FB04/FC0C/FC12/FD05 等写/擦/OTP/跳转/复位类命令计数=0；注入 FD05/FB01 的污染计划被断言抓住;<br>双读不一致终止纪律：probe_read_two 两读逐字节比对，不一致抛错结束会话（无重试）；transport 白名单拒绝计划外地址与非 128B 长度;<br>三分支判读占位：每检查带推导链 source 与 branch_pending_session 占位；控制组失败仅门控 C1 目标（C2/C3/C5 判读独立）；verdict 全部 enforced=false（报告不强制） | 1 | 3 | 2026-11-14T23:27:10 |
| D3-SFR-01 | NOT_RUN | none | d3-preparation §1.3 C2：RES 读值即 WBG04 码值→升 measured 并反解 Vref（K=801±3d 除法链，BU §2）;<br>读值须使电池门两条 mode 路径（float 直读与 divider 兜底，BU §5 疑点）都过 3200mV 门——若任一不过=模型世界与真机矛盾，重审 fixture 而非记录通过;<br>门方向核验（BU §6）：读值 R 应满足 4×(R×K/400)≥3200（K∈[756,846] 全域）——4004mV≈标称 4.0V 锂电池物理锚 | 3 | 4 | 2026-11-14T21:18:45 |
| D3-SFR-02 | NOT_RUN | none | d3-preparation §1.3 C5：读值作复位值锚（模型初始 con/cnt/prd=None→严格停的复位面从此有真值参照）;<br>能力边界如实声明：unresolved overflow 期间 CNT 是否冻结（模型通用 overflow guard 严格停的根据）是运行时动态语义，loader 会话复位态不可答——本记录不以任何复位值推断动态语义;<br>PRD 复位值≠1199999 不构成矛盾（1199999 是固件启动后写的运行值；复位值与运行值分账记录） | 3 | 3 | 2026-11-14T21:18:45 |
| D3-SFR-03 | NOT_RUN | none | d3-session-pack §2.3：掩码位=0b110（bit1=1 且 bit2=1）→USB 在位场景下充电检测位语义获真机对照，与 CB 电池场景 fixture 0b000 互补成对——两个场景锚一起约束 charge_hw.h 位语义+charge.c 阈值推导链;<br>掩码位≠0b110→位语义/阈值/复位态检测器行为推导链复审（如实记录字节，不拟合；域内其他形状=新输入）;<br>双读不一致或 FD07 失败→会话即停记录（通用纪律，无重试）；0xffe/0xfd3 窗首读挂起同样即停（无窗探测重试） | 3 | 4 | 2026-11-14T23:27:38 |
| D3-TIM-01 | NOT_RUN | none | d1-acceptance §7 时窗比较面：识别时窗与响应时间落在事先定义的误差范围内（范围声明=本记录未覆盖项，手段具备后先声明再测）;<br>模型侧拍轴时序（D2-a-REC-01 实测：档手势码 10+档序、档计时起点=按下确认拍、释放=显式手势 3）与真机行为的结构一致性（档位切换点/无双发单击负例）;<br>真机侧证据等级要求：以日志面时刻为准；「手感/录屏/用户反馈」不构成时窗断言证据（AGENTS：用户反馈不是自动循环测量） | 3 | 3 | 2026-11-14T21:18:45 |

结果统计: PASS=1 FAIL=0 BLOCKED=0 NOT_RUN=7(共 8 条;BLOCKED=必选用例因模型缺失未过,不是弱化 PASS,§6.4)
