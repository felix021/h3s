# 辅听修复候选 F1 双产物刷写验收链（路 F1-PREP / sim-f1prep）

2026-11-19；基于 main 9c77ce7（已含 LFIXIMPL）。本路把 F1a/F1b 双产物接入
accept_profile / accept_usb 刷写工具链并同步 ace，**只准备、不刷写**：真机刷写待
用户明示确认后由主协调执行。F1 候选本身仍 UNTESTED（静态补丁无法验证运行行为，
见 docs/hearing-aid-lfix-impl.md §7/§8）。

## 1. 产物与三层钉值

三重身份不能互换：UFW 文件摘要 ≠ 应用密文摘要 ≠ 整片 2MiB 摘要。app 层钉值用
accept_profile.parse_package 同款逻辑从产物解出（构建报告 hearing_aid_l1 应用长度
930968/930854 交叉核对一致；相对 HAL5 基座实耗 +186B/+72B）。

| 候选 | 内容 | UFW SHA256 | app len | app SHA256 |
|---|---|---|---|---|
| **F1a**（默认，诊断档） | HAL5 全链 + A1+B1+B2 + A3 时钟压舱 | `73b45e659d127452697b5580b2474ef481ce9b8118f3f5dfa8b9c731bb4fef48` | 930968 | `d2499f8f1d8adcb9ae1fc17c49e14531ae07569b0b00806de86b067d81b0a535` |
| **F1b**（对照镜像） | HAL5 全链 + A1+B1+B2（无 A3） | `f16576687dae42a583149aa9f8a2f1b89dc777228245a27c289f0faeec00f2c1` | 930854 | `a700de5da57c83f9300a30d602591ea96583d52da7d3f845154f3861c2f6418d` |
| HAL5（F1 的基座） | full M=95 | `9ad0f0881a7996cb4dea81477d8731aa9b4db69a6f352603118663d68eda5465` | 930782 | `b8f10fd5c4f88d8e642577aee35077cb1d28088ccdcc33ef20545df0b513de73` |
| b79（测试过的基线） | ANC 两态 | `b79cb3a6f8702eb9e1d12eaf181a968f6b5848a66c051b2c792c39aa8b42fa1b` | 929875 | `1505f01398c3d32c0854fdf98e8b84aaae5269f7d5f278fd8a27459d7d19e6bf` |

版本号四者同为 `V_0.1.1.8 / 280`，版本查询区分不了；镜像身份只能靠字节层证据。

## 2. 方向清单（profile 全表）

tools/usb-recovery 三件套按候选参数化（`--candidate hal5|f1a|f1b`，默认 hal5）。
每方向钉 UFW sha + app len + app sha；来源态不符、产物 sha 不符（单 bit 翻转）、
跨候选拿错包均在**首个擦除前**明确拒绝。nonce/意图日志/读回校验流程照旧。

| profile | 方向 | bank B 来源态 → 目标态 | 刷入包 |
|---|---|---|---|
| accept-hal5-flash-v1 | flash, hal5 | b79 → HAL5 | HAL5 UFW |
| accept-b79-rollback-v1 | rollback, hal5 | HAL5 → b79 | b79 UFW |
| accept-f1a-flash-v1 | flash, f1a | HAL5 → F1a | F1a UFW |
| accept-f1a-rollback-v1 | rollback, f1a | F1a → HAL5（同层一键） | HAL5 UFW |
| accept-f1b-flash-v1 | flash, f1b | HAL5 → F1b | F1b UFW |
| accept-f1b-rollback-v1 | rollback, f1b | F1b → HAL5（同层一键） | HAL5 UFW |

- **既有 b79↔HAL5 双方向行为不变**（回归钉死：既有测试零改动全绿）。
- F1 方向无直连 F1x→b79；退到 b79 = F1x→HAL5 后再走既有 accept-b79-rollback-v1
  （两跳，按需可选）。
- rollback 遇 bank B 已是回退目标时给零写入报告：hal5 方向 `ALREADY_B79`、
  f1a/f1b 方向 `ALREADY_HAL5`，不产生任何擦除。
- 其余规则（仅改 bank B 精确变化扇区、boot-info 最后提交、VM 保留最新、
  中断分析器）全部沿用 docs/hearing-aid-acceptance-plan.md §1a，本路未放宽任何门。

## 3. 设备现状与操作顺序（关键）

设备当前 **bank B=HAL5**（2026-11-18 从 ace 刷入，整片 2MiB 读回验证
FLASH_ACCEPT_VERIFIED，flash.log 存档；bank A 仍 b79）。这正好是 F1 的 flash
来源态，**F1x 刷写是当前状态的单步下一步**：

```
b79 ──accept-hal5-flash-v1──▶ HAL5(当前) ──accept-f1x-flash-v1──▶ F1x
                                   ◀─accept-f1x-rollback-v1── (同层一键回退)
b79 ◀─accept-b79-rollback-v1───────┘（按需，第二跳）
```

若刷 F1 前设备状态有变（不确定时先 USB 双读核验 bank B），以工具的来源态门为准：
b79 状态需先走 accept-hal5-flash-v1；HAL5 刷写与健康检查按
docs/hearing-aid-acceptance-plan.md 阶段 1 执行（此处不重复）。

## 4. F1 验收标准

修复目标对应的原症状（docs/hearing-aid-lfix-plan.md §0）与判据：

| # | 原症状 | 修复点 | 验收判据（全过才算 F1x 过线） |
|---|---|---|---|
| 1 | open 状态左耳刺耳噪声 | B1 场景值 10→16 | **左耳无刺耳**：开辅听后左耳与右耳同为可闻放大环境声，无固定垃圾噪声 |
| 2 | 右耳轻噪+音量随时间爬升 | B2 SET_CHANNEL(1) | **右耳正常**：放大声可闻、无异常爬升/失控 |
| 3 | open→close→open 第二次 open 起蓝牙断联重连 2-3s | A3 时钟压舱（F1a）/A1 退出等待 | **open→运行→close→再 open 全程稳定**：第二次 open 蓝牙不断联、不重连，双击开关每步有响应（A1 防 ≈5.9s 挂键） |
| 4 | 健康底线 | — | 全程正常关机可关、无复位循环、无异常发热；否则立即停测转回滚 |

F1a=默认刷写对象；F1b 仅在 F1a 出现 A3 相关新异常（如时钟压舱副作用）时作对照
诊断用，不作常规刷写目标。主观听感只记「此人此窗口」观察，不外推声压/临床结论。

## 5. 回滚路径（F1→HAL5 同层一键）

触发条件（任一）：验收判据 1/2/3 任一 FAIL、健康底线破、用户叫停。已获回滚
预授权时直接执行：

1. USB 进 ROM → 新会话双读全片（`h3s_usb.py backup`）。
2. `accept_usb.py plan-rollback --candidate f1x --package <HAL5.ufw>`（设备在
   F1x 状态；若刷写中断，先走 `analyze`/`recover` 中断处置）。
3. 审查 plan.json → `rollback --apply --confirm-nonce <nonce>`（同层回退到 HAL5，
   F1 app 比 HAL5 长 186/72B，回退会 0xff 清尾到旧 F1 app 末尾）。
4. 整片读回一致 → 拔 USB、**手动正常重启一次** → HAL5 健康清单。
5. 需要继续退到 b79：再走既有 accept-b79-rollback-v1（第二跳）。

读回通过但健康未过 = 回滚未完成；未知混合态不接受，保留现场与 journal 另报方案。

## 6. 给用户的操作步骤（刷写时）

1. 耳机保持可正常开机状态，USB 线接 ace（一加 Ace），手机/ace 弹窗选 **OTG**。
2. 告知主协调「可以刷 F1a」即视为刷写启动信号；后续 USB 备份/计划/刷写/读回由
   主协调在 ace 上执行，用户不用操作。
3. ace 侧已就绪的文件（`~/h3s-accept/`，sha 与 §1 表逐字一致）：
   - `lfiximpl-f1a.ufw`（73b45e65…）/ `lfiximpl-f1b.ufw`（f1657668…）
   - 既有 HAL5 工具与包不动、不覆盖。
4. 刷写完成后按提示**拔 USB 并手动正常关/开机一次**（r2 恢复教训的固定收尾），
   然后按 §4 判据佩戴验收。

刷写授权话术沿用 acceptance-plan §1b 要素：候选完整 SHA + 简称前 8 位（用户复述
`73b45e65`）、写入范围（bank B，保留 A/序号/设备参数/VM/校准）、未测风险、回滚
预授权。本路不构成任何授权。

## 7. 本路边界

1. 未连接任何设备、未刷写、未取得新授权；F1a/F1b 保持 UNTESTED。
2. 离线证据（测试 33 项全绿：F1 正负例 + b79/HAL5 回归 + 真包 F1 假设备全链
   往返）只证明计划/校验/回滚字节层正确，不构成运行行为证据。
3. ace 同步只 scp 双产物 + sha256sum 核验，未在 ace 上跑刷写、未动 USB 节点权限。
4. B2 通道数运行值反例核实（cmd 0x2000C 直存 b[priv+28]）等三项运行时核实
   （lfix-impl §9）属真机验收范畴，并入 §4 判据观察，不在离线范围。
