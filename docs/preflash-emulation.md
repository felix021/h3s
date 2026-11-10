# 刷机前的 pi32v2 离线执行验证

2026-11-10 已在本机跑通第一层原型：用社区 CPU 模拟器执行实际 QCY 指令和历史 r2 补丁 ELF，再加入从 H3S 固件提取的取指范围检查。它能在刷机前拒绝上次失败的五个补丁入口；尚不能启动完整 H3S、建立蓝牙连接或验证声音。

工具：[tools/probe_pi32v2.py](../tools/probe_pi32v2.py)。实跑脱敏结果：[evidence/emulation-probe.json](../evidence/emulation-probe.json)。本轮没有连接设备、刷写或生成新固件。当前耳机继续使用已恢复、已验收的 b79 ANC 两态版本。

后续已完成 [第一轮应用入口启动探索](boot-exploration.md)：五个独立场景分别定位指令模型适配、PLL0 寄存器、ROM `_pll_init` 和未知启动参数；尚未进入 BSS 初始化或 RTOS。

[第二轮](boot-handoff.md)在明确的交接／PLL／循环假设下完成BSS、data和RAM代码初始化摘要验证，停在零计数`rep`；尚未进入RTOS或模拟真实ROM。

## 杰理资料和现成模拟器

本机使用的固件和参考 SDK 指向 AC701N / BR28。杰理官方 [Bootloader 芯片映射](https://doc.zh-jieli.com/SDKWiki/master/Jieli-Tech/gitee-repos-Other/2-fw-bootloader/2-1-bootloader-architecture-chip-support.html) 明确区分 AC701N→br28、AC791N→wl82。两者的 Flash、时钟、引脚和加密适配不同，不能直接把其他板子的模拟器当成 H3S。

截至本次公开资料检索，找到了 SDK、寄存器头文件、启动和接口说明，但未找到能覆盖此 QCY 方案的完整公开 TRM、官方完整 CPU 指令手册和可直接运行 H3S 的官方 CPU／整机模拟器。官方 [701N UI 编辑工具手册](https://doc.zh-jieli.com/KDocs/master/屏融合应用方案入口汇总/701N_UI编辑工具用户手册.html) 提到的 Lua 模拟器用于界面开发，不能据此认为能执行耳机固件。

社区已有可用基础，不必从零写 CPU：

| 项目 | 已有能力 | 对 H3S 的边界 |
| --- | --- | --- |
| [jvitkauskas/pi32v2-isa](https://github.com/jvitkauskas/pi32v2-isa) | 指令表、汇编／反汇编／链接器、硬件指令观测和 Python 指令执行器 | 首个目标是 AC791N / FM-1 的 r3 核心；本项目已用它执行 H3S 的少量指令，但未验证全部 BR28 指令语义 |
| [simonjohansson/fm1-emulator](https://github.com/simonjohansson/fm1-emulator) | 基于 QEMU 的 pi32v2 CPU 和 FM-1 板级模型；上游报告可运行原厂 FM-1 等固件 | 板级设备面向 FM-1；没有现成的 H3S 蓝牙、ANC、codec、启动和寄存器模型 |
| [joakimeriksson/fm1-emu](https://github.com/joakimeriksson/fm1-emu) | Rust CPU／SoC 模型，可原生或浏览器运行 FM-1／SMK-37 固件 | 同样需要 BR28 / H3S 适配；上游的固件兼容性不是本项目对耳机的验证结果 |

若向杰理或方案商索取资料，最有用的是：准确芯片料号及修订版、TRM／内存与寄存器图、pi32v2 ISA 和 ABI、异常与 PC 保护说明、ROM／SFC／启动流程，以及可用调试器或模拟器。QCY 定制程序的符号／链接 map、RTOS 配置和音频节点接口也比跨型号参数表更直接。目前没有因此向第三方发送消息。

## 复现第一层验证

需要 Python **3.11+**、Git、网络下载；没有额外 Python 依赖，也不需要 GPU 或厂商编译器。先取得固定摘要的已验证固件：

```bash
python3 tools/fetch_firmware.py --variant current
python3 tools/probe_pi32v2.py --fetch-simulator \
  --report analysis/emulation/baseline.json
```

模拟器固定为提交 `3608ed19bc21324f8ecfe79cc0018d05e43d9563`，下载到忽略的 `tools/reference-cache/pi32v2-isa/`，检查 HEAD 和已跟踪文件差异。不会自动升级上游。后续已有缓存时可省略 `--fetch-simulator`。检查使用 `python3 tools/check_local.py` 在本机执行，GitHub Actions 已停用。

基础运行不需要归档 ELF：它执行原固件的限界参数加载、原始 source-read 指令，并执行五组冻结的 r2 入口机器码，确认目标均被取指策略拒绝。这是历史失败回归，不是任意新候选的通用准入器。

持有当时本地归档 ELF 时，可加参数运行更多诊断：

```bash
python3 tools/probe_pi32v2.py \
  --patch-elf analysis/eq-toggle-build-20261110-r2/patch.elf \
  --report analysis/emulation/archived-r2.json
```

该可选路径不会由仓库下载 ELF；它检查五个入口的精确字节、节布局和 337B payload 摘要，只接受历史 r2 的这组代码。当前构建器已拒绝该越界布局，不能为了取得 ELF 而放开构建器保护或重建可刷包。公开结果的原 ELF 摘要是 `f8fdcf50f12c2d421b7c5ad4487dd21b372d3ef89e3dffdd4096fc52f5486c08`；完整 ELF 不随本次提交发布。

固定上游有一处打包缺陷：`semantics/m2.py` 读取一个未提交的开发机 ID 文件。本项目的加载适配器仅在该文件读取期间，从上游已提交的硬件观测 JSONL 中恢复指令 ID 集合，并排除其他模块已经提供的模型；不修改上游源码、不补造指令效果。重复模型会报错。适配器和固定提交一起审计，不能据此声称所有收集到的指令都已在 BR28 上验证。

## 实际验证结果

执行 `debug_init` 中 `0x06034e92`、`0x06034e98` 的两条真实立即数加载，得到 Flash 取指范围参数 `0x06000100..0x060c3338`，同时核对 `0x06034eac` 的保护启用指令。其 RAM helper／寄存器依据见 [EQ 实验复盘](eq-toggle.md)。本工具把该范围作为明确的离线拒绝策略，**没有模拟 PC 保护寄存器、中断控制器和真实异常分发，也没有设备异常日志来证明唯一根因**。上界精确包含关系仍需芯片文档确认；r2 的目标远超上界，不受这一细节影响。

| 历史补丁入口 | 实际执行分支得到的目标 | 结果 |
| --- | --- | --- |
| `0x060059ce` | `0x060e3092` | BLOCKED |
| `0x06025030` | `0x060e30b0` | BLOCKED |
| `0x0602510e` | `0x060e30c2` | BLOCKED |
| `0x06025364` | `0x060e3074` | BLOCKED |
| `0x06078846` | `0x060e30a0` | BLOCKED |

此外，本次实跑：

- 原 QCY source-read 的两条指令得到预期寄存器值和续执行地址。
- 五项负向检查确认未知 MMIO 读取、Flash 写入、奇数取指、范围外取指、跨执行边界指令都会报错。每次解码都检查，覆盖模拟器内部的并行指令第二槽和控制流预读。
- 可选 ELF 路径运行真实编译产物的 8 个 source 更新用例，检查来源值／高位保留、callee-saved 寄存器和栈恢复。
- 在明确绕过取指范围的独立诊断中，运行 3 个实际 `eq_toggle` 用例：关闭、重新开启、空间模式排除；检查两个音频节点名、UUID、44B 参数、调用顺序、返回值、标志位和寄存器／栈。
- 同样运行 4 个实际 `eq_open_config` 用例，检查目标节点的 bypass 和其他节点／原配置保留。

后两组共 7 例与 source 的 8 例合计 15 例。音频发送和恢复 API 是显式桩函数，故意覆盖 caller-saved 寄存器以检查 ABI；没有执行 DSP、RTOS 或真实音频 API。**诊断可以通过，但 r2 结论始终是 `REJECTED`。** 运行返回 0 表示回归符合预期（包括拒绝 r2），不是允许刷机。

## 下一步需要补什么

最有收益的顺序是：

1. 将当前只认历史 r2 的探针扩展成接受新候选精确 ELF／链接 map 的执行框架，定义每个入口的 RAM、参数和期望 API 轨迹；继续与原程序做差分。
2. 补齐双击事件分发、source 读取的高位标志处理、节点开关和重建，以及播放暂停／重连／通话／切模式等路径。当前测试没有覆盖按键到音频结果的完整链路。
3. 对接更多真实原固件函数，给调用上下文、错误返回和节点生命周期建模，减少桩函数覆盖的范围。
4. 若继续追求整机启动，再从已有 FM-1 模拟器移植 CPU 基础，逐步补 BR28 ROM、RAM 复制、SFC、异常／中断／双核、定时器、看门狗、Flash 保护、GPIO、蓝牙和音频设备。具体 CPU 版本差异也要用证据核对。

已有完整性验证器继续检查封装／CRC／精确差分；CPU 执行测试补充检查控制流、ABI、栈和访问范围。即使这些检查都通过，也无法仅凭当前模拟器确认蓝牙配对、ANC 听感、实时调度和功耗。构建器／刷写客户端的固定摘要保护保持独立。
