# 启动探索第二轮：交接结构和内存初始化

后续已[取得CPU ROM窗口](rom-read.md)，包括真实`mask_init`／`_pll_init`；尚未接入模拟器。下文缺失ROM的描述和停止位置记录当时模型状态。

后续已完成[边界探索](simulation-boundaries.md)：保留本轮严格停止，新增显式假设路径继续执行升级结果和CRC处理，并定位控制写／ROM和IPMASK边界。下文的零计数停止结果不变。

2026-11-10，已从参考 SDK 的头文件、库中的 LLVM bitcode，以及 QCY 的真实指令交叉确定启动交接结构。在明确的 PLL／测试参数／非零循环模型下，执行 **16077 次 step**，完成三段启动内存初始化并核对摘要。现在停止在 `0x06000182` 的零计数 `rep`，尚未进入 RTOS、蓝牙或音频硬件初始化，也没有执行内部 ROM。

工具：[tools/probe_boot_handoff.py](../tools/probe_boot_handoff.py)，结果：[evidence/boot-handoff.json](../evidence/boot-handoff.json)。[第一轮](boot-exploration.md)的五个结果保留，并继续逐字节复现原报告。全过程离线，没有修改耳机或固件。

## 一个必须修正的假设

第一轮试探的 `R0=0x0019fc28` 只是假设。新证据区分了两个对象：

- `boot_arg_list` 是 PLL 使用的 `_boot_arg`。参考 `clock.c.o` 的类型是三个字节字段、对齐、两个32位字段；`boot_clk_init` 在 PLL0 bit0清零时写入24000000和48000000，再调用 `_pll_init`。
- **入口 R0 指向 `BOOT_DEVICE_INFO`**，它包含文件系统头指针、SFC映射、chip ID、trim和蓝牙地址。QCY `0x060664d8` 的复制路径与参考 `boot_info_init` 的字段布局相符。

不能因为前者有一个导出地址，就把它用作后者。当前仍未取得真实入口 R0 数值，第二轮使用独立标注的模拟器地址来承载正确类型；没有声称还原了真实 bootloader 的完整交接状态。

| `BOOT_DEVICE_INFO` 偏移 | 大小 | 内容与当前证据 |
| --- | ---: | --- |
| 0 | 4 | `fs_info` 指针；由模拟器安排目标地址 |
| 4 | 4 | `sfc_base_addr`；本轮选择 bank A 的物理0x2020作为模型 |
| 8 | 4 | `app_addr`；选择与已知SFC映射一致的0x06000000 |
| 12 | 2 | chip ID，真实值未知 |
| 14 | 2 | trim，真实值未知 |
| 16 | 8 | 蓝牙地址和CRC，真实交接值未知；不使用私有设备数据 |

`fs_info` 的数据从精确官方包头解码得到，同时核对头CRC。VID／PID按固定拆包工具保留明文。实际用到 `FlashSize=0x1ff000` 和 `align=16`；**前者是文件头字段，不等于重新测量了物理 Flash 容量**。

## 四组新回归

```bash
python3 tools/fetch_firmware.py --variant original
python3 tools/probe_boot_handoff.py --fetch-simulator \
  --report analysis/boot-handoff/round2.json
```

| 场景 | step | 停止位置／验证 |
| --- | ---: | --- |
| 只提供可从文件和映射构造的字段 | 19 | `0x060664f0` 读未知chip ID即停，不补0 |
| 提供测试值A | 30 | 交接复制完成，停在第一条未适配`rep` |
| 提供不同测试值B | 30 | 同样完成复制；此前PC轨迹与A相同 |
| A + 有界非零启动循环适配 | 16077 | 三段内存摘要通过，停在零计数`rep` |

测试值A/B给chip ID、trim、MAC/CRC填入两组明确的哨兵值，只检查复制和相邻字段保留；不表示这些值能用于设备或通过其校准／地址校验。交接目标 `0x00122800` 中未被该例程写入的15B仍保留为未知；没有把整个结构预先填0。两个哨兵场景目前仅检查到首个`rep`的相同控制流，不泛化为后续功能不依赖这些字段。

复制结果如下，长度、初始化覆盖和摘要全部检查：

| 区域 | RAM范围，不含末地址 | 内容来源 |
| --- | --- | --- |
| BSS | `0x00125320..0x0012a62c`，21260B | 真正执行逐字清零 |
| data | `0x00124840..0x00125320`，2784B | 从官方应用运行地址0x060c3338复制 |
| RAM code | `0x0012a640..0x0012ef14`，18644B | 从官方应用运行地址0x060c3e18复制 |

JSON保存三段SHA256、覆盖字节数和最后64条指令。10684次guest写入只保留首32和末32项及计数，不发布完整RAM镜像或长篇厂商机器码。

## 新增的CPU适配及限制

普通byte／halfword／word／doubleword存储必须按实际宽度写入，不能为上游存储模型的“合并旧字”记账操作强行补齐未知相邻字节。适配器仅处理精确官方应用中已观察的交接写入和启动循环写入，保留其他地址和指令的拒绝行为。

非零`rep`仅适配三个明确的启动循环，执行真实循环体，逐次递减R2并检查范围、预算和最终摘要。**大计数使用上游通用模型的外推：其AC791N硬件观测只测试过1..7，尚未验证BR28的大计数分块／中断／时序细节。16077是该功能模型的step计数，不是实机指令计数或耗时测量。**

来到第四个复制循环时，原固件把长度设为0，随后执行 `rep`。上游[未决问题](https://github.com/jvitkauskas/pi32v2-isa/blob/3608ed19bc21324f8ecfe79cc0018d05e43d9563/docs/GAPS.md)明确保留零计数行为未验证，因此本轮拒绝继续，没有默认跳过、跑一次或循环溢出。PLL0 bit0=1依旧是假设，PLL未初始化路径的内部ROM函数依旧缺失。没有模拟运行模式切换、异常、中断或双核。

## 参考分析如何复现

参考 SDK 固定 `67328f4741c69993c88e504c67377b196c724f97`，实际目录从 `SDK/` 开始。新增文件和对象摘要记在 [data/dependencies.json](../data/dependencies.json)。可选择下载分析材料：

```bash
python3 tools/fetch_boot_reference.py --decode-ir \
  --llvm-dis llvm-dis-16 --output-dir analysis/boot-reference
```

脚本只下载摘要固定的 `boot.h`、`cpu.a`、`system.a` 到忽略的缓存，用本机`ar`提取固定对象，再用本机LLVM16读取bitcode；不执行厂商库、不重新编译或刷机。`startup.S.o` 是ELF，`clock.c.o`和`boot.c.o`是LLVM bitcode。后两者可以直接恢复IR和类型信息，避免只从反汇编猜结构。

LLVM16报告`clock.c.o`中部分debug挂接无效并忽略这些debug信息；本轮使用其IR指令与独立头文件/QCY指令核对，不依赖被丢弃的debug定位。参考库不是QCY确切源码，未把整库、IR或专有工具重新发布；它与QCY相符的是已核对的类型和少量控制／复制行为，不能推断所有函数逐字节一致。

## 本机检查和下一步

按用户要求，GitHub Actions已手动停用且工作流从仓库移除。以后使用 `python3 tools/check_local.py`，默认包含原函数探针、第一轮阻塞回归和第二轮交接／内存回归。首次输入下载加`--fetch-inputs`；固件完整复现加`--reproduce`；Android本机构建按需加`--android-build`。不触发GitHub托管CI。

下一步先核对零计数和大计数`rep`的架构行为，再处理后面的ICFG读改写及早期初始化；同时保留“真实PLL/ROM/交接初态未知”的独立缺口。当前结果可验证文件到RAM的功能性初始化，仍不适合单独批准任何候选刷写。
