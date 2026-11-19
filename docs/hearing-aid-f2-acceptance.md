# 辅听修复候选 F2 与对照 F1a+C1 刷写验收链（路 F2-PREP / sim-f2prep）

2026-11-19；基于 main 5c5e05a（已含 LFIXIMPL2）。本路把 F2 / F1a+C1 双产物接入
accept_profile / accept_usb 刷写工具链并同步 ace，**只准备、不刷写**：真机刷写待
用户明示确认后由主协调执行。两候选仍 UNTESTED（静态补丁无法验证运行行为，见
docs/hearing-aid-lfix2-impl.md §7）。

## 1. 产物与三层钉值

三重身份不能互换：UFW 文件摘要 ≠ 应用密文摘要 ≠ 整片 2MiB 摘要。app 层钉值用
accept_profile.parse_package 同款逻辑从产物解出，与构建报告 dual_bank_boot_info
app_bytes 交叉核对一致（F2 931034 = F1a 930968 + 66 = C2 close 补刀；F1a+C1
930968 = F1a 等长——C1 两态表是等长表字节替换，只有内容不同，摘要门必须能分开
两者，测试已钉）。

| 候选 | 内容 | UFW SHA256 | app len | app SHA256 |
|---|---|---|---|---|
| **F2**（修复候选） | F1a 全链 + C1 两态表 + C2 close 恢复 ANC | `7e972bf00484920bce0a41b1653d94f5c151455947b08fe3b0fb67e3d3c68348` | 931034 | `36f103a88e77fab3bc5d4793b330250759a56154e39fa6751b58207b8e1b5b4a` |
| **F1a+C1**（对照候选） | F1a 全链 + 仅 C1 两态表（无 C2） | `503f427fcefd2cd796b8e992a50275f3257cfb8012419df281a827bd83338351` | 930968 | `5b787afb81dcf441de12f03e2330aef11c7d900e25474772388e285abd5dc926` |
| F1a（两者的基座，当前在机） | HAL5 全链 + A1+B1+B2+A3 | `73b45e659d127452697b5580b2474ef481ce9b8118f3f5dfa8b9c731bb4fef48` | 930968 | `d2499f8f1d8adcb9ae1fc17c49e14531ae07569b0b00806de86b067d81b0a535` |
| HAL5 | full M=95 | `9ad0f0881a7996cb4dea81477d8731aa9b4db69a6f352603118663d68eda5465` | 930782 | `b8f10fd5c4f88d8e642577aee35077cb1d28088ccdcc33ef20545df0b513de73` |
| b79（测试过的基线） | ANC 两态 | `b79cb3a6f8702eb9e1d12eaf181a968f6b5848a66c051b2c792c39aa8b42fa1b` | 929875 | `1505f01398c3d32c0854fdf98e8b84aaae5269f7d5f278fd8a27459d7d19e6bf` |

版本号同为 `V_0.1.1.8 / 280`，版本查询区分不了；镜像身份只能靠字节层证据。
F2 vs F1a 全文件 diff = 1 表字节 + 20 CRC 元数据字节 + C2 段（lfix2-impl §4）。

## 2. 方向清单（profile 全表）

tools/usb-recovery 三件套按候选参数化（`--candidate hal5|f1a|f1b|f2|f1ac1`，默认
hal5）。每方向钉 UFW sha + app len + app sha；来源态不符、产物 sha 不符（单 bit
翻转）、跨候选拿错包（含 F1a 与 F1aC1 等长内容差异）均在**首个擦除前**明确拒绝。
nonce/意图日志/读回校验流程照旧。

| profile | 方向 | bank B 来源态 → 目标态 | 刷入包 |
|---|---|---|---|
| accept-hal5-flash-v1 | flash, hal5 | b79 → HAL5 | HAL5 UFW |
| accept-b79-rollback-v1 | rollback, hal5 | HAL5 → b79 | b79 UFW |
| accept-f1a-flash-v1 | flash, f1a | HAL5 → F1a | F1a UFW |
| accept-f1a-rollback-v1 | rollback, f1a | F1a → HAL5（同层一键） | HAL5 UFW |
| accept-f1b-flash-v1 | flash, f1b | HAL5 → F1b | F1b UFW |
| accept-f1b-rollback-v1 | rollback, f1b | F1b → HAL5（同层一键） | HAL5 UFW |
| **accept-f2-flash-v1** | flash, f2 | **F1a → F2** | F2 UFW |
| **accept-f2-rollback-v1** | rollback, f2 | **F2 → F1a（同层一键）** | F1a UFW |
| **accept-f1ac1-flash-v1** | flash, f1ac1 | **F1a → F1aC1** | F1aC1 UFW |
| **accept-f1ac1-rollback-v1** | rollback, f1ac1 | **F1aC1 → F1a（同层一键）** | F1a UFW |

- **既有 b79↔HAL5、HAL5↔F1x 六方向行为不变**（回归钉死：既有测试零改动全绿，
  ALREADY_B79/ALREADY_HAL5 报告文案逐字不变）。
- f2/f1ac1 无直连跳层 profile；退到底 = F2/F1aC1→F1a 后再走既有
  accept-f1a-rollback-v1 与 accept-b79-rollback-v1（F2→F1a→HAL5→b79 三跳，按需
  可选）。
- rollback 遇 bank B 已是回退目标时给零写入报告：f2/f1ac1 方向 `ALREADY_F1A`，
  不产生任何擦除。
- F2 回退到 F1a 时 app 缩短 66B，回退会把 [新末尾, 旧末尾) 0xff 清尾；F1aC1 与
  F1a 等长，回退无清尾区间。
- 其余规则（仅改 bank B 精确变化扇区、boot-info 最后提交、VM 保留最新、中断
  分析器）全部沿用 docs/hearing-aid-acceptance-plan.md §1a，本路未放宽任何门。

## 3. 设备现状与操作顺序（关键）

设备当前 **bank B=F1a**（2026-11-19 16:53 从 ace 刷入；ace 侧 f1a-flash-plan-
20261119.json 与 f1a-workdir 存档；bank A 仍 b79）。这正好是 F2 与 F1a+C1 共同
的 flash 来源态，**两候选都是当前状态的单步下一步**：

```
b79 ──hal5-flash──▶ HAL5 ──f1a-flash──▶ F1a(当前) ──f2-flash──▶ F2
                                             │                    │
                                             │              (rollback 一键回 F1a)
                                             └──f1ac1-flash──▶ F1a+C1（对照）
                                                    │
                                                    └──(rollback 一键回 F1a)
再往下退：F1a ──f1a-rollback──▶ HAL5 ──b79-rollback──▶ b79（既有链，按需）
```

默认刷 **F2**；F1a+C1 只在 F2 验收异常时按 §4 判据 7 启用作单变量对照（拆 C1/C2
变量），不作常规刷写对象。若刷写前设备状态有变（不确定时先 USB 双读核验 bank B），
以工具的来源态门为准。

## 4. F2 验收标准

修复目标对应的原症状与归因见 docs/hearing-aid-lfix2-impl.md §1 归因表。判据全文
以 lfix2-impl §10 为准，摘要如下：

**判据 0（codex 裁决新增，先于一切）**：逐次记录每次按 ANC 键时**助听的开关状态**
与本次是否经过 close；验收分两条序列——(a) 保留用户原始按键顺序、(b) 明确经过
close 的顺序（开助听→听几秒→关助听→等 2s→按 ANC 键）。序列 (a) 回答「助听开着
按 ANC」的并发场景（C2 不覆盖）是否仍在；序列 (b) 才回答 C2 的 200ms/ON 提示音/
完成等待起了什么作用。

| # | 目标 | 验收判据 |
|---|---|---|
| 1 | 症状 5 通透消失 | **ANC 键两态循环恢复**：降噪↔通透，无「关闭降噪」态；若「按了没反应/听感不变」记频率（卡死链/滤波跳过候选，lfix2-impl §9） |
| 2 | C2 判别信号 | **关助听后 ~0.3-0.9s 内听到「开启降噪」提示音**（C2b db3c(2,1) 直接可闻信号：播出=close 后窗口 tone 链可用；不播但降噪生效=形态 A 静默跳过；不播且按键无反应=形态 B 卡死）；关助听全程不断联 |
| 3 | 症状 3/4 无提示音+断联 | **关助听后按 ANC 键**：ON↔TRANS 切换正常+每步有提示音+不断联（两序列 (a)(b) 都记） |
| 4 | C2 自愈边界 | 快速「关助听→立即双击开」若第一次被取消（无声无提示音），再按一次应正常；记录出现频率；多次无反应=形态 B 开放项 |
| 5 | 症状 6 EQ 无效定案 | **EQ 三步判别**（codex 方案）：同一 F2+同一 App+同一首持续音乐：(i) 助听关交替发平直/高区分度曲线；(ii) 音乐保持播放开助听重复发送，分别记录音乐声与 mic 回送声；(iii) 关助听回纯音乐对照。音乐响应+mic 不响应=0x22 只作用音乐图坐实 |
| 6 | F1a 已修行为不回退 | **助听听感不回退**：两耳无刺耳、有放大声（B1=16+B2 保持的回归确认；左耳无刺耳是 F1a 真机已验证行为） |
| 7 | 对照路径 | F2 验收异常时：先刷 **F1a+C1** 拆 C1/C2 变量（C1 两态表生效+无 C2 恢复补刀），再谈 hold 常驻判别档（lfix2-impl §9） |
| 8 | 健康底线 | 全程正常关机可关、无复位循环、无异常发热；断联观察贯穿所有序列（蓝牙图标/播放连续性）；否则立即停测转回滚 |

主观听感只记「此人此窗口」观察，不外推声压/临床结论。

## 5. 回滚路径（F2/F1aC1→F1a 同层一键）

触发条件（任一）：判据 0-6 任一 FAIL、健康底线破、用户叫停。已获回滚预授权时
直接执行：

1. USB 进 ROM → 新会话双读全片（`h3s_usb.py backup`）。
2. `accept_usb.py plan-rollback --candidate f2|f1ac1 --package <F1a.ufw>`（设备在
   F2/F1aC1 状态；若刷写中断，先走 `analyze`/`recover` 中断处置）。
3. 审查 plan.json → `rollback --apply --confirm-nonce <nonce>`（同层回退到 F1a；
   F2 回退会 0xff 清尾 66B 到旧 F2 app 末尾，F1aC1 回退等长无清尾；若 bank B 已是
   F1a 则报 `ALREADY_F1A` 零写入）。
4. 整片读回一致 → 拔 USB、**手动正常重启一次** → F1a 健康清单（F1a 是真机已
   验证过行为的状态：连接正常+左耳无刺耳，见 lfix2-impl §1 症状 1/2 行）。
5. 需要继续退：F1a→HAL5 走 accept-f1a-rollback-v1，HAL5→b79 走
   accept-b79-rollback-v1（既有链第二、三跳）。

读回通过但健康未过 = 回滚未完成；未知混合态不接受，保留现场与 journal 另报方案。

## 6. 给用户的操作步骤（刷写时）

1. 耳机保持可正常开机状态，USB 线接 ace（一加 Ace），手机/ace 弹窗选 **OTG**。
2. 告知主协调「可以刷 F2」即视为刷写启动信号；后续 USB 备份/计划/刷写/读回由
   主协调在 ace 上执行，用户不用操作。
3. ace 侧已就绪的文件（`~/h3s-accept/`，sha 与 §1 表逐字一致）：
   - `lfiximpl2-f2.ufw`（7e972bf0…）/ `f1a-c1.ufw`（503f427f…）
   - 既有 HAL5/F1 工具与包不动、不覆盖（accept_*.py 已随本路更新到五候选版并
     验证 import）。
4. 刷写完成后按提示**拔 USB 并手动正常关/开机一次**（r2 恢复教训的固定收尾），
   然后按 §4 判据 0 起步佩戴验收（先双序列逐次记录，再走判据 1-8）。

刷写授权话术沿用 acceptance-plan §1b 要素：候选完整 SHA + 简称前 8 位（用户复述
`7e972bf0`）、写入范围（bank B，保留 A/序号/设备参数/VM/校准）、未测风险、回滚
预授权。本路不构成任何授权。

## 7. 本路边界

1. 未连接任何设备、未刷写、未取得新授权；F2/F1a+C1 保持 UNTESTED。
2. 离线证据（测试 42 项全绿：F2/F1aC1 正负例含等长 F1aC1 vs F1a 摘要互斥 +
   既有 33 项零改动回归 + 真包 F2/F1aC1 假设备全链往返）只证明计划/校验/回滚
   字节层正确，不构成运行行为证据。
3. ace 同步：双产物 scp + sha256sum 逐字核验 + accept_profile/accept_verify/
   accept_usb 三模块更新并验证 import；未在 ace 上跑刷写、未动 USB 节点权限。
4. 判据 0/4/5 的运行时观察（助听开关状态记录、C2 提示音、EQ 三步）属真机验收
   范畴，离线不可代答。
