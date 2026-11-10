# QCY H3S 固件定制与逆向记录

让 H3S 的物理降噪模式键只在 **ANC 开启 ↔ 通透（环境音）** 之间轮换，跳过正常／ANC 关闭模式。

**2026-11-09 已完成实际 OTA，用户确认“符合预期，达到效果了”。** 当前固件仍报告 `V_0.1.1.8` / `280`。仓库包含修改代码、拆包和校验工具、Android 查询／刷写工具，以及从 QCY App 设置命令追到固件按键逻辑的分析。

**2026-11-10 更新：** EQ 实验导致连接异常后，已通过 [USB 救砖流程](docs/usb-recovery.md) 恢复原 ANC 两态镜像，整片读回通过，蓝牙和播放恢复；用户随后明确确认独立降噪键能正常切换、App 也能控制。

## 下载固件

原始固件和当前版本均在 **[firmware Release](https://github.com/felix021/h3s/releases/tag/firmware)**，不放进 Git 历史。

| 文件 | 含义 |
| --- | --- |
| [QCY_H3S_7018F_1T2_release_note_V1.1.8_011.ufw](https://github.com/felix021/h3s/releases/download/firmware/QCY_H3S_7018F_1T2_release_note_V1.1.8_011.ufw) | 未修改的官方 0.1.1.8 OTA 包 |
| [H3S_0.1.1.8_ANC_ON_TRANSPARENT.ufw](https://github.com/felix021/h3s/releases/download/firmware/H3S_0.1.1.8_ANC_ON_TRANSPARENT.ufw) | 当前已验证的按键两态版本，保留提示音参数调整 |
| [SHA256SUMS](https://github.com/felix021/h3s/releases/download/firmware/SHA256SUMS) | 两个固件的校验值 |

两个包均为 **1,326,848 字节**：

```text
官方原包
65cafc7f9a7545c5c30701d141c77db334e37065589e42c51306132c94c879af

当前修改版
b79cb3a6f8702eb9e1d12eaf181a968f6b5848a66c051b2c792c39aa8b42fa1b
```

发布名只是原构建文件 `H3S_0.1.1.8_tones_minus10dB_anc_on_trans_UNTESTED.ufw` 的易读别名，**字节和摘要相同**。历史文件名里的 `UNTESTED` 是生成时的状态，随后实际刷写与用户验收另有记录。

## 已实现和已验证到哪一步

| 项目 | 结果 |
| --- | --- |
| 模式键跳过正常／关闭，只切降噪和通透 | 11-09 用户实测确认；11-10 USB 恢复后用户再次确认按键正常切换 |
| QCY App「声音」页直接选择降噪／通透／正常 | 对应固件路径未修改；本轮没有另做该入口的设备回归测试 |
| 提示音与按键音参数降低 10 dB | 参数已修改并随包刷入；用户听感变化不明显，**未确认实际声压降低 10 dB** |
| 原始 0.1.1.9 → 修改版 0.1.1.8 | 在这台 H3S 上完成过；当前 ANC 版本是在 0.1.1.8 上同版本更新 |
| 多功能键双击开关 EQ | r2 实机出现启动后连接异常，停止使用；已用 USB 恢复旧镜像 |
| 开机默认 ANC、通透 EQ／增益／限幅／反馈消除 | 尚未实现 |
| 完整 Flash 备份、USB 故障恢复 | 已取得两份一致的2MiB备份，6扇区恢复和整片读回通过；蓝牙播放、模式键与App控制均获用户确认 |
| 0.1.1.9 原包 | 未取得 |

这是**一台 H3S 的验证结果**，不是所有硬件批次的兼容性保证。刷写前先读设备能力并核对包摘要；双备份能力报告不等于已有救砖方案。

## 修改有多小

实际固件的内部模式编号为 `OFF=1, ON=2, TRANS=3`。`anc_next` 例程使用三项循环表，并在找到**第一个**当前模式后停止搜索：

```text
原表：01 03 02    OFF → TRANS → ON → OFF
新表：02 03 02    ON  → TRANS → ON → TRANS …
```

只把表的首字节 `01` 改成 `02`，不改机器指令。若当前通过 App 进入正常模式，表中没有 OFF，下一次按模式键取默认索引 0，进入 ON。App 的直接选模式命令走独立路径。

- 循环表：`app.bin + 0x9E410`，运行地址 `0x0609E510`。
- 相对前一个已刷入的提示音版本：`app.bin` 仅 **1 字节**变化；整个 uFW 变化 **20 字节**，其余为必需的校验元数据。
- 相对官方原包：另有提示音参数调整及关联校验值；不能把整个修改包描述成只有一个字节变化。

完整证据见 [按键逆向](docs/anc-cycle.md)、[App 与固件配合](docs/app-analysis.md)、[验证记录](docs/validation.md)。

多功能键双击开关 EQ 的 [实验记录](docs/eq-toggle.md) 包含一次失败的实机试验：镜像完整性和传输通过，但升级后连接／按键异常，**r2 停止使用**。它不包含在上述已验证 Release 中。

## 从原始包复现

需要 Python 3.10+、Git。以下流程只做离线文件处理，不连接耳机：

```bash
git clone --recurse-submodules https://github.com/felix021/h3s.git
cd h3s
python3 -m venv .venv
. .venv/bin/activate
python -m pip install -r requirements.txt

python tools/fetch_firmware.py --variant original
python tools/unpack.py
python tools/build_quiet_candidate.py \
  --attenuation-db 10 --anc-cycle-two-state \
  --output-dir analysis/rebuild-current

python tools/verify_ufw_integrity.py \
  analysis/rebuild-current/H3S_0.1.1.8_tones_minus10dB_anc_on_trans_UNTESTED.ufw
python tools/check_reproduction.py
```

最后一个命令会在新的临时目录重新拆包，检查官方零修改、提示音中间包、当前 ANC 包的三个精确摘要，同时重现旧 CRC 缺陷并确认校验器拒绝它。**不会调用 adb 或开始 OTA。**

如果已下载 Release 附件，也可以把官方原包直接放到 `firmware/`；下载工具会核对已有文件。各构建命令使用新的输出目录，避免覆盖历史候选。

## Android 查询与 OTA 工具

源码在 [tools/android-query](tools/android-query/README.md)。使用官方 JL OTA SDK 1.10.0 / 10932，通过 QCY 的 `A002/1/2` BLE 服务工作。

```bash
python tools/bootstrap.py --android
export ANDROID_SDK_ROOT=/path/to/android-sdk
export H3S_BLE_ADDRESS=AA:BB:CC:DD:EE:FF  # 换成自己的 H3S BLE 地址
bash tools/android-query/build.sh
```

要求 JDK、Android platform 35、build-tools 35.0.0。实际目标地址只写入被 Git 忽略的生成文件；仓库没有测试耳机的真实地址和签名私钥。公开源 App 当前为 `0.5.1-restore-anc`，固定接受已验证 b79 镜像并拒绝 EQ 实验包；其源码／主机验证与原实测 `0.4` 客户端分开记录。USB 救砖使用独立工具，不经 Android OTA 客户端。

工具把查询与刷写分开：查询层只允许限定的只读命令；刷写只接受固定包摘要、目标身份和版本，使用单次、短时效确认并在开始前重新读取设备。当前只接受版本 280 和上述当前固件。不要为绕过门槛而随意放开版本、摘要或任意写包。

安装、权限、候选文件复制、确认入口、传输监控、验收与日志保密见 [Android 使用说明](tools/android-query/README.md) 和 [OTA 协议与实现](docs/ota.md)。

## 文档导航

| 文档 | 内容 |
| --- | --- |
| [docs/reproduction.md](docs/reproduction.md) | 下载、依赖、拆包、构建、独立校验和重新反汇编的完整命令 |
| [docs/firmware-layout.md](docs/firmware-layout.md) | UFW 13 个条目、JLFS、SFC、双备份 boot-info、全部 CRC 层和地址坐标 |
| [docs/anc-cycle.md](docs/anc-cycle.md) | 实际机器码、状态转移、最小补丁和 App 模式编号差异 |
| [docs/app-analysis.md](docs/app-analysis.md) | QCY「声音」页调用链、命令注册记录、保存与应用路径 |
| [docs/protocol.md](docs/protocol.md) | BLE 服务、RCSP 帧、端序、QCY 自定义命令与通用接口实测边界 |
| [docs/usb-recovery.md](docs/usb-recovery.md) | 实测 USB 救砖、ROM入口、RAM loader、整片双备份、双分区与6扇区恢复工具 |
| [docs/ota.md](docs/ota.md) | E1～E8、前台服务、MTU 分包、队列、回连、确认与成功条件 |
| [docs/stream-volume.md](docs/stream-volume.md) | 两个音量节点、表结构、stream 流程标识和听感未确认的原因范围 |
| [docs/validation.md](docs/validation.md) | 四次实际传输、两类失败、修复、成功与用户反馈，哪些结论不能推出 |
| [docs/sources.md](docs/sources.md) | 固定依赖版本、原始来源、工具链、反编译方法和第三方说明 |
| [AGENTS.md](AGENTS.md) | 后续编码代理需要遵守的项目事实、技术不变量和工作边界 |

## 仓库结构

```text
firmware/manifest.json, SHA256SUMS   发布附件的精确身份
data/                              型号控制面板、依赖来源和固定摘要
docs/                              人可读的协议、分析和操作说明
evidence/                          小型反汇编片段与脱敏校验结果
tools/                             自编分析、打包、复现和下载脚本
tools/android-query/               Android 查询、OTA 和日志核对工具
tools/jl-misctools/                 固定提交的上游 MIT 子模块
analysis/                          运行时生成，Git 忽略
```

原始设备日志可能包含 authKey、蓝牙地址和附近设备广播，未上传；完整 QCY APK、整棵反编译源码、调试签名和专有工具链缓存也未上传。`evidence/` 保存足以核对结论的脱敏摘要，完整文件级分析可从发布原包重跑生成。固件格式里的 SFC key `0x3496` 来自原包，和未公开的设备 authKey 不是同一项。

第三方来源与各自许可范围见 [THIRD_PARTY.md](THIRD_PARTY.md)。
