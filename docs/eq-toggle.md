# 多功能键双击开关 EQ：实验镜像

**2026-11-10 实机结果：停止使用 r2。** 用户授权后进行了一次 OTA，设备 E6 返回0、E7有确认、SDK成功；1834个非空数据块和1个EOF独立核对通过，全部466个相对原包的修改字节均被请求／发送。但升级后耳机未正常回连，用户报告无关机语音、手动开机后蓝／白灯闪烁、无法正常配对，后来正常关机和音量双键复位也无预期反应。能扫描到原地址广播，BLE连接返回147超时。**传输／完整性通过不等于新代码能正常运行。**

随后通过 [USB 救砖](usb-recovery.md) 将 bank B 恢复为之前的 b79 ANC 两态应用，6扇区与整片读回通过，两bank的应用均匹配旧版。蓝牙／播放已恢复；短暂的模式键无反应反馈后，用户手动重启耳机一次，进一步明确确认按键正常切换、App也能控制，恢复通过验收。没有再次通过 BLE OTA 重刷。下文是历史构建记录，不能继续当作可刷推荐。

已确认一个足以导致异常的设计遗漏：原固件 `0x06034e92..0x06034eac` 初始化 Flash PC 范围为 `0x06000100..0x060c3338` 并启用保护；新增代码 `0x060e3074` 超出这个范围。对应 RAM 设置函数 `0x0012b7b8`（线性 app 视图为 `0x060c4f90`）写入 `LIM_PC0_L/H`，参考芯片 `csfr.h` 可核对寄存器。原离线检查只检查文件布局、CRC 和分支目标字节，没有检查硬件执行许可范围。现 builder 和独立 EQ 验证器显式拒绝此布局，即使历史字节复现仍可重现，也不能再通过当前审计。没有设备异常日志，尚不能声称已证实唯一实际故障点。

后续已生成独立的[执行范围修复候选](eq-fence-repair.md)，仅离线审计与函数回放通过；完整启动仍在推进，未再次刷机。原r2和本页历史试验结论不变。

构建阶段：用户希望多功能键双击在“启用 App 配置的 EQ”和“关闭 EQ”之间切换，并要求构建镜像。完成主机逻辑测试、独立完整性／差分／跳转校验和干净原包复现，随后进行上述实机试验。已验证的公开 `firmware` Release 仍是原 ANC 两态版本，没有替换。

实验包：`H3S_0.1.1.8_tones_minus10dB_ANC_EQ_TOGGLE_EXPERIMENTAL.ufw`，1,326,848 字节：

```text
SHA256 c8dae5233678102aac152f0ff94952edea83c6eeca6afaf216db43226d0b6696
```

源码在 [tools/eq-toggle](../tools/eq-toggle/)，打包使用 [build_quiet_candidate.py](../tools/build_quiet_candidate.py)，独立检查使用 [verify_eq_candidate.py](../tools/verify_eq_candidate.py)。本地历史失败镜像为 r2，输出位于忽略目录 `analysis/eq-toggle-candidate-20261110-r2/`。离线检查摘要见 [evidence/eq-toggle-offline.json](../evidence/eq-toggle-offline.json)。

构建期间的 r1 草稿曾使用其他上下文的 gesture0x82。复核原默认表后改为普通双击编号2；r1 未刷入，已作废，独立检查现会拒绝它。

## App 的选项为什么不够

H3S 控制面板中，多功能键双击的 `eventID=1794 / 0x0702`，默认动作是 `7`（模式切换）；菜单只有 `0..7`，没有 EQ。该模式切换对应音乐／游戏／电影等音频模式，与 ANC 模式键的两态修改分开。

本地反编译的 `QCYConnectManager.setKeyFunction()` 将 eventID 拆成 key、gesture，再调用 `JLDeviceImpl.setBtnClickFunction()`。App 内有两种传输实现：标准 ADV 类型 2 的三字节 `[key, gesture, action]`，以及新设备自定义命令 `0x2b` 的 gesture/action 组合。本轮没有确认 H3S 实际选择哪个分支，不能把 App 中存在 `0x2b` 实现当成设备已支持该命令。

固件 `0x060252c6` 处理按键映射，`0x06025382` 根据动作编号跳转：

| 动作 | 入口 | 解析结果 |
| --- | --- | --- |
| 1～6 | `0x06025396` 等 | 播放暂停、上下曲、语音助手、音量加减 |
| 7 | `0x06025404` | 当前音频模式切换，调用 `0x060251fc` |
| 8、10 | `0x06025392` | 返回未处理 |
| 9 | `0x06025390` | 返回已处理，没有 EQ 调用 |
| 11 | `0x0602544e` | ANC 相关处理，调用 `0x06018b60` |
| 127 | `0x060253a6` 检查 | 无动作 |

因此，在这张分发表里没有可直接绑定的隐藏 EQ 开关编号。需要新增固件行为；只给 App 菜单增加选项不足以完成目标。

## EQ 参数和 bypass 是两个接口

App 的 H3S 参数均衡器路径为 `setEQWtihQFG2()`，使用自定义命令 `0x22`。固件在 `app.bin+0xa9a78` 注册它：getter `0x0602580a`，setter `0x0602583c`，应用当前参数的例程是 `0x06025118`。

参数格式是 3 字节头加每段 7 字节，至少包含 EQ 类型、master gain，以及各段的频率、增益、Q、IIR 类型。配置保存使用 key 42。App 的预设选择也可以下发整组参数，不能把某个“预设编号”直接当作布尔开关。尤其 `0x06024fe6("MusicEqBt", index)` 读取并加载配置项，**不是** `enable(name, bool)`。

实际 EQ 节点描述符位于 `app.bin+0xa8e88`，UUID 为 `0x737b`，控制处理函数 `0x0607898e`。节点参数发送入口为 `0x0607538a`。在节点参数控制消息中，subcommand **2** 的跳转目标是 `0x06078a2e`：

- 从参数包 `+4` 读取一个字节，存入节点的 bypass 状态。
- 值非零时调用 `0x0607895c`，释放滤波运行资源并清空运行句柄。
- 值为零且运行句柄为空时，经 `0x06078b60` 调用 `0x060787ac` 重新打开。

这是实际 H3S 机器码中的分支，不只是参考 SDK 的能力列表。参考 SDK 的 `eq_update_parm()` 也有单独的 `EQ_IS_BYPASS_CMD`，但参考版本不能代替本固件证据：

- [参考 SDK 的 EQ 参数更新代码](https://gitlab.zh-jieli.com/ai_test/701n/-/blob/67328f4741c69993c88e504c67377b196c724f97/SDK/audio/effect/node_param_update.c)
- [参考 SDK 的 EQ 参数更新示例](https://gitlab.zh-jieli.com/ai_test/701n/-/blob/67328f4741c69993c88e504c67377b196c724f97/SDK/audio/effect/demo/eq_demo.c)

## 实验包的行为与边界

本次按“关闭＝旁路参数 EQ”构建。切回原厂默认音效与旁路不同：原厂曲线不一定平直，bypass 目标节点也不等于跳过整个 DSP、ANC 或所有扬声器校正。

实现的预期行为如下，均待耳机实测：

- 多功能键双击固定切换参数 EQ；该手势的 App 动作绑定不再生效。单击、三击、其他按键及独立 ANC 模式键沿用原处理。
- 第一次双击关闭，第二次双击恢复。曲线参数本身保持原值；关闭期间在 App 修改曲线后，重新启用应使用最新参数。
- 重建音频节点时重新检查关闭状态；没有正在播放的音频时切换也会保存运行期间的状态。
- 启用时调用原有音频模式恢复回调 `0x0602619a(0,10)`：音乐模式应用当前 App 曲线，游戏／电影模式保留其原来的预设路径。
- 开关状态只在 RAM 中，重新开机默认启用 EQ，不新增 VM 写入。EQ 类型 10 是独立空间音效路径，双击在此类型下不做切换。
- 没有新增 App 的“EQ 已关闭”显示或提示音。App 仍能显示／编辑原曲线，不能以 UI 曲线未变化判定开关无效。
- 保留已验证的 ANC ON↔通透循环表及原来的提示音参数调整。提示音实际声压降低仍未验证。

本轮没有设备设置写入或刷写，版本仍是 0.1.1.8 / 280。实际音频路由、听感、时序和并发行为不能由离线测试保证；刷入后需验证双击交替、暂停／恢复、关闭期间修改曲线以及 ANC 两态功能。

## 五处钩子与存储布局

| 原地址 | 修改 |
| --- | --- |
| `0x06025364` | 映射匹配后检查 key=7、gesture=2，与原默认表 `[7,2,7]` 一致；其他事件重放原动作读取及上下文查询 |
| `0x060059ce` | 读取 EQ 参数来源时屏蔽新增的高位，重放原来的 r0=0 |
| `0x06025030` | 加载预设后记录 source=2，同时保留开关位 |
| `0x0602510e` | 应用自定义参数后记录 source=1，同时保留开关位 |
| `0x06078846` | EQ 打开时，对目标节点强制旁路；返回原代码前重放 `r0=[r6+4]`，从 `0x0607884c` 继续 |

开关使用 QCY 全局状态 `0x001265c0+42` 的 bit7；原 source=1/2 占低位。原程序对应的一个读取位置及两个写入位置均被处理。该字节位于启动时清零的 BSS 范围 `0x00125320..0x0012a62c` 内。没有猜测空闲 RAM、扩大 BSS 或分配新的全局指针。

目标节点为 `MusicEqBt`、`Eq0Media`，对应原自定义 EQ 更新循环的两个名字。启动代码将 `0x060c3338` 的初始化数据复制到 RAM `0x00124840`，由此可核对名字缓冲区 `0x00124e48` 和 `0x00124e58`。通话 EQ、ANC 和其他节点不匹配此名字检查。

原 `app.bin` 的长度不变，改五个各 6 字节的指令窗口。337 字节代码／常量放在原 flash 尾部空白区，并追加成有独立 CRC 的 JLFS 文件 `eq_toggle.bin`：原最后一个 `config.dat` 的结束标志改为非末项，其内容和位置保持原样。追加头位于 flash+`0xe5073`，代码四字节对齐于 flash+`0xe5094` / 运行地址 `0x060e3074`。

双备份 boot-info 的覆盖范围增加 370 字节，以包括新文件头、对齐字节和代码。原 flash.bin、完整 UFW 的长度保持不变。新增区域不能沿用“原明文差分 XOR”：拆包工具没有解密旧末项之后的原始填充字节，因此打包器对扩展后的整个 SFC 区显式重编码，再更新所有层次 CRC。

## 构建及独立验证

只需将 `tools/eq-toggle/` 复制到能运行厂商工具链的构建机，编译后将 ELF 和反汇编拉回本机；分析和打包不依赖远程执行。

```sh
bash tools/eq-toggle/test.sh
bash tools/eq-toggle/build.sh /path/to/pi32v2/bin /fresh/patch-output
python3 tools/build_quiet_candidate.py \
  --anc-cycle-two-state --attenuation-db 10 \
  --eq-toggle-elf /fresh/patch-output/patch.elf \
  --output-dir analysis/eq-candidate
python3 tools/verify_ufw_integrity.py \
  analysis/eq-candidate/H3S_0.1.1.8_tones_minus10dB_ANC_EQ_TOGGLE_EXPERIMENTAL.ufw
python3 tools/verify_eq_candidate.py \
  firmware/H3S_0.1.1.8_ANC_ON_TRANSPARENT.ufw \
  analysis/eq-candidate/H3S_0.1.1.8_tones_minus10dB_ANC_EQ_TOGGLE_EXPERIMENTAL.ufw \
  --patch-asm /fresh/patch-output/patch.asm
python3 tools/check_reproduction.py
```

本次使用与原分析相同的 `jieli-linux-toolchains-20260730.1`，clang 4.0.1、pi32v2/r3。链接器强制每个钩子恰为 6 字节，payload 不超过既有空白区，不允许新 `.data` / `.bss`。C 逻辑另在本机通过 AddressSanitizer／UndefinedBehaviorSanitizer 检查。主机测试使用模拟节点，不能替代 pi32v2 实机行为。

独立验证器不导入打包代码：检查原资源内容不变、五处修改范围、ANC 表、原 bootloader、扩展区边界、实际反汇编字节和跳转／返回地址。另从干净原始 uFW 解包重构，候选 SHA256 一致；原包／提示音版／已验证 ANC 版的摘要及历史错误 CRC 拒绝检查也全部通过。

## 复核定位

先按现有流程取得原始 `app.bin` 和 vendor objdump 的完整反汇编，然后在本机执行：

```sh
python3 tools/analyze_eq_toggle.py
```

脚本固定检查原始 app SHA256、逐条核对反汇编对应的机器码、重算跳转表与关键分支，并核对仓库内的 App 控制面板。结果写入忽略目录 `analysis/eq-toggle/analysis.json`；脚本不修改固件，也不连接设备。通过表示这些静态定位与输入一致，**不表示双击开关功能已完成**。
