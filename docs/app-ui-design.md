已完整读取旧页面及两个原生桥，全程没有修改文件。

先纠正两处接口描述：**现有 Java 桥的 ANC 模式是 `1/2/3`；EQ 参数是对象数组，增益单位为百分之一 dB。** 以下代码按实际接口编写。

另外，有两项无法仅靠替换 HTML 完成：

- `WebShellActivity` 没有开启 DOM Storage，需增加 `settings.setDomStorageEnabled(true)`，才能持久化主题选择。[Android 文档](https://developer.android.com/reference/android/webkit/WebSettings#setDomStorageEnabled(boolean))
- Activity 固定使用暗色主题，需让原生主题随系统切换，WebView 的 `prefers-color-scheme` 才能反映系统主题。[Android 文档](https://developer.android.com/develop/ui/views/layout/webapps/dark-theme)

下面实现完整的前端能力；存储不可用时会明确提示。

**1. 设计规范**

采用蓝灰色卡片界面，不使用渐变、外部字体或图片。

| Token | 亮色 | 暗色 | 用途 |
|---|---|---|---|
| `--bg` | `#F4F6F8` | `#0E141D` | 页面 |
| `--surface` | `#FFFFFF` | `#17212F` | 卡片 |
| `--soft` | `#E8EDF3` | `#223044` | 次级背景、禁用按钮 |
| `--text` | `#18212F` | `#F1F5F9` | 正文 |
| `--muted` | `#475569` | `#BDC9D8` | 辅助文字 |
| `--line` | `#64748B` | `#94A3B8` | 控件边界、刻度 |
| `--accent` | `#164E9B` | `#93C5FD` | 主按钮、选中项、曲线 |
| `--hover` | `#123F80` | `#BFDBFE` | 主按钮悬停 |
| `--active` | `#0F3366` | `#60A5FA` | 主按钮按下 |
| `--on-accent` | `#FFFFFF` | `#102238` | 主按钮文字 |
| `--ok` | `#166534` | `#86EFAC` | 成功 |
| `--warn` | `#854D0E` | `#FDE047` | 待确认 |
| `--danger` | `#991B1B` | `#FCA5A5` | 错误 |

对比度按 sRGB 相对亮度公式计算。表中“背景最低值”取页面、卡片、次级背景三种配对中的最小值；不通过降低透明度表示禁用。正文门槛为 4.5:1，必要图形和控件边界为 3:1。[文字标准](https://www.w3.org/WAI/WCAG22/Understanding/contrast-minimum.html)、[图形标准](https://www.w3.org/WAI/WCAG22/Understanding/non-text-contrast.html)

| 配对 | 亮色 | 暗色 |
|---|---:|---:|
| 正文／背景最低值 | 13.75:1 | 12.18:1 |
| 辅助及禁用文字／背景最低值 | 6.44:1 | 7.95:1 |
| 控件边界／背景最低值 | 4.04:1 | 5.20:1 |
| 强调文字、曲线／背景最低值 | 6.87:1 | 7.40:1 |
| 成功文字／背景最低值 | 6.06:1 | 9.50:1 |
| 警示文字／背景最低值 | 5.82:1 | 10.12:1 |
| 错误文字／背景最低值 | 7.06:1 | 7.03:1 |
| 主按钮文字／normal | 8.09:1 | 8.90:1 |
| 主按钮文字／hover | 10.25:1 | 11.30:1 |
| 主按钮文字／active | 12.46:1 | 6.32:1 |

布局与状态：

- 字号：页面标题 20px、卡片标题 17px、正文及控件 15px、说明 13px、频段标签 12px。
- 间距：4／8／12／16／24px；卡片圆角 18px，按钮 12px，标签 8px。
- 点击控件至少 44px 高。360dp 下 EQ 每行五段，每段约 56px 宽。
- normal 使用明确边框；hover 改背景；active 使用实色；focus 使用 3px 外轮廓；disabled 保留可读文字并使用虚线边框。
- 状态同时用文字表达，不能只靠颜色。
- 写命令区分“已排队”“设备已应答”“失败／超时”，不会点击即冒充已生效。

兼容性核对：

| 接口 | 实际形状 |
|---|---|
| `armWrite(boolean)` | 返回 `void` |
| `isArmed()`、`isConnected()` | 返回布尔值 |
| `connect()` | JSON 字符串，成功含 `{ok:true,connecting:true}` |
| `disconnect()` | JSON 字符串，成功 `{ok:true}` |
| `writeEq(type,master,bandsJson)` | 每段 `{freq,q,gain,iirType}`；`q=Q×100`，`gain/master=dB×100` |
| `setAnc(...)` | 降噪 `(1,1,2)`；通透 `(3,1,4)`；关闭 `(2,0,0)` |
| `setPromptVolume(level)` | `3 / 6 / 8` |
| `setAudioMode(mode)` | 音乐 `1`；游戏 `3`；电影 `4` |
| 按键写入 | `setKeyRemap(eventId,action)`，另保留 `setKeyRemapCustom(...)` |
| 按键读取 | `getKeyRemap()` → `{ok:true,map:{"769":6,...}}`，仅壳侧工作副本 |
| 状态查询 | `readStatus(47)` 查询电量 |
| 异步事件 | `window.onH3sEvent(object)`；包含 `response/device_message/mtu/disconnected/send_failed` |
| 写入返回 | 成功可能为 `{ok:true,sent:true,seq,sn,bytesHex}`；仅组帧时没有 `sent`；失败 `{ok:false,error}` |

七个原始预设包含不同频率、9 段、−17dB、小数增益及空间音效 `type=10`。以下采用已说明的处理：**七个预设完整保留并原样发送；自定义档使用要求的固定十段、整数 ±12dB。** 不做未经定义的预设换算。

**2. 完整 `assets/index.html`**

