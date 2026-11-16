# AVD 验收截图档案（路 SIMB1E / SIMB3A，2026-11-17 入库）

本目录=仿真联调验收的 UI 断言面截图精选（AVD h3s-app 模拟器，非真机）。对应验收摘要：
`evidence/sim-routes/m1-avd-e2e.json`、`evidence/sim-routes/m3-avd-e2e.json`；
总索引=[../../evidence-index.md](../../evidence-index.md)。

隐私核验（入库前逐张人工查看）：全部为 AVD 应用 UI/仿真世界状态——无真机数据、
无私有路径/主机名/IP（10.0.2.2 为 AVD 标准宿主回环别名）、无账号信息。原始截图目录
（gitignored）留各路 worktree，见摘要 JSON 的 raw_local 字段。

## m1-avd/（M1 AVD 端到端，路 SIMB1E）

| 文件 | 内容 |
|---|---|
| 00-initial.png | app 冷启动初始 UI（未连接，页面日志『启动：不自动连接』） |
| 01-connected.png | 触摸调试期（点击未达页面 JS，供过程记录；后改用 DRIVE 广播驱动口） |
| 10-connected-readloop.png | 读环：已连接·MTU247·电量 0%/10%/0% + 页面日志 [BLE mtu]/[readStatus]×3 |
| 11-anc-eq-cards.png | 读环：EQ 卡『设备回报：EQ 0 段』+ ANC 分段（fresh 世界模式 0，如实不标记） |
| 12-anc-write-readback.png | **（标注勘误，见下）**实为 EQ 编辑页：writeEq 发送 66 字节后的曲线编辑视图 |
| 13-worlddead-disconnect.png | world_dead：未连接+『与耳机的连接已断开（status=129）』+显示清空+[BLE disconnected] |
| 14-reconnect-recovered.png | 复役：重连后已连接·电量恢复显示·[BLE mtu]/[readStatus]×3 |
| theme-test.png | 调试期主题测试（深色变体；电量数值为静态 fixture 期状态，非本验收读环） |

### 12 号截图标注勘误（codex 对账审查指出，2026-11-17 核实采纳）

原始记录（worktree 内 sim-b1e-avd-e2e.json）将 `12-anc-write-readback.png` 标注为
「ANC 写环：『降噪』分段按下+『设备回报：降噪』」；入库前逐张复核确认**该图实际内容
是 EQ 编辑页**（十段增益滑杆+保存/取消，页面日志 [writeEq] 发送 66 字节）。
按「不删原图、勘误随档」原则：保留原文件名与原图，以本条说明修正标注。
ANC 写环的 UI 断言截图因此**缺失**——该场景的既有证据面=线级回执
（写后读 0x17 → `1703010000` 模式 1=降噪）+页面日志 [setAnc] ok，UI 截图留待下次
AVD 会话补拍（HANDOFF 待办）。入库摘要 `m1-avd-e2e.json` 已按勘误后口径书写。

## m3-avd/（M3 刷机流 AVD 全环，路 SIMB3A）

| 文件 | 内容 |
|---|---|
| simb3a_e2e_final_usb_tab.png | 终态：USB tab 恢复卡『状态：已验证』+设备 SHA256==S_target（绿）+操作日志（进入 ROM/恢复计划 3 扇区/全片复读==目标/设备已重启） |
