# 完整复现流程

## 1. 输入和环境

仓库检查在本机执行，GitHub Actions已停用。依赖和输入就绪后运行`python3 tools/check_local.py`；首次可加`--fetch-inputs`下载固定输入，固件完整复现加`--reproduce`，本机Android SDK35构建加`--android-build`。启动模拟的独立复现见[交接与内存初始化](boot-handoff.md)。

唯一支持的输入原包是 `QCY_H3S_7018F_1T2_release_note_V1.1.8_011.ufw`，SHA256：

```text
65cafc7f9a7545c5c30701d141c77db334e37065589e42c51306132c94c879af
```

所有地址、结构和原值检查均针对这一包。不要把新版本、同平台其他型号或任意 flash dump 改名后交给生成器。`--source` 只改变输入路径，不取消摘要检查。

分析与文件重打包可在普通 Linux／aarch64 环境执行。本次就是在手机上的 Linux 环境完成静态分析和候选验证；Android 构建以及 x86_64 厂商反汇编器在构建机执行。任何脚本都不要求指定的远程主机。

```bash
git clone --recurse-submodules https://github.com/felix021/h3s.git
cd h3s
git submodule status
python3 -m venv .venv
. .venv/bin/activate
python -m pip install -r requirements.txt
python tools/fetch_firmware.py --variant both
```

如果先普通 clone，再执行 `git submodule update --init --recursive`。GitHub 的源码 ZIP 不包含子模块内容，推荐使用 Git 克隆。`crcmod==1.7` 用于上游 CRC；`PyYAML==6.0.2` 用于拆包描述。独立校验器只需 Python 标准库。

`fetch_firmware.py` 从固定的 `firmware` Release 下载，按 `firmware/manifest.json` 核对长度与 SHA256；已有同名不同内容文件会被拒绝，不会静默覆盖。它没有蓝牙代码。

## 2. 拆包和资源提取

```bash
python tools/unpack.py
```

默认输出 `analysis/v1.1.8/`，目录必须原先不存在。输出分三部分：

```text
unpacked/
  jlfw.yaml                 入口地址、chip-key、文件分类
  decrypted.bin             原包的解扰 flash 视图
  top/                      引导和顶层配置
  files/app.bin             原始主程序，842654字节
  files/stream.bin          音频流程，9351字节
  files/scene.bin            场景配置
  files/config.dat           其他配置
  files/...                 ANC扩展、麦克风和其他资源
outer/                      13个UFW外层条目及manifest
tones/TON, tones/TON1        两套提示音及manifest
```

底层先执行固定提交的 `fwunpack_newfw.py`，再执行本仓库的 `extract_ufw_resources.py`。后者是必需的：上游的 flash 解包不会自动把本包外层的 `TON/TON1/ANCIF/ANCIF1` 全部展开。

自定义位置的等价流程：

```bash
python tools/unpack.py --source /path/to/official.ufw --output-dir analysis/fresh-source
python tools/build_quiet_candidate.py \
  --source /path/to/official.ufw --analysis-dir analysis/fresh-source \
  --attenuation-db 10 --anc-cycle-two-state --output-dir analysis/fresh-build
```

## 3. 构建当前版本

```bash
python tools/build_quiet_candidate.py \
  --attenuation-db 10 --anc-cycle-two-state \
  --output-dir analysis/rebuild-current
```

生成 uFW、修改后的 stream、结构化 `build-report.json`。固件摘要必须为：

```text
b79cb3a6f8702eb9e1d12eaf181a968f6b5848a66c051b2c792c39aa8b42fa1b
```

生成器的 `--attenuation-db` 范围为0～30；这只是离线参数能力，不表示所有值做过设备测试。只有发布清单中的固定摘要具有本次按键行为验收。历史输出文件名有 `UNTESTED`，生成脚本不负责继承或改写设备验证状态。

两个重要基线：

```bash
python tools/build_quiet_candidate.py --attenuation-db 0 --output-dir analysis/noop
python tools/build_quiet_candidate.py --attenuation-db 10 --output-dir analysis/tone-only
```

零修改必须精确等于官方包；仅提示音版本必须得到 `d1f7a067b169285d23457f8c182a8626bc75c01d679f2846a1f7c5e84b9b752d`。当前版本相对仅提示音版本的 app.bin 只改变表中一个字节。

## 4. 独立校验和回归

```bash
python tools/verify_ufw_integrity.py firmware/H3S_0.1.1.8_ANC_ON_TRANSPARENT.ufw
python tools/check_reproduction.py --output analysis/reproduction-check.json
```

`verify_ufw_integrity.py` 自己实现解码，使用 `binascii.crc_hqx`，不导入生成器或 jl-misctools；检查外层、双备份总头／明文／密文、顺序及嵌套 JLFS，并列出实际音量参数。

`check_reproduction.py` 每次都在新的临时目录拆原包，检查：

1. 零修改、仅提示音、ANC当前版的长度和三个固定摘要。
2. 每个生成包的独立完整 CRC。
3. app.bin 精确单字节差分 `0x9e410: 1→2`；外层其他资源未变。
4. 相对提示音包的整个 uFW 只有20字节不同。
5. 重造历史错误：恢复过时的 boot-info 后补齐外层 CRC，得到已失败包 `5651bff3...`；独立校验器仍必须检出两个总体 CRC 错误。
6. 错误源文件被拒绝，且未产生输出目录。

本次公开整理的实跑结果在 [evidence/publication-reproduction.json](../evidence/publication-reproduction.json)。这些检查不访问硬件，也不替代设备侧验收。

## 5. 重新反汇编主程序

使用 [杰理官方 Linux 工具链](https://pkgman.jieliapp.com/s/linux-toolchain)。本次版本为 `jieli-linux-toolchains-20260730.1`，`pi32v2/bin/objdump` 报 LLVM4.0.1，支持 pi32/pi32v2/q32s。官方下载入口可能更新版本；有差异时应重新检查解码，不能按名称认为二进制完全相同。

```bash
python tools/disassemble_app.py \
  --objdump /path/to/pi32v2/bin/objdump
python tools/analyze_anc_cycle.py
```

脚本先验证原始 app.bin 摘要，按 `jlfw.yaml` 入口地址 `0x06000100` 生成 ELF32-pi32v2 包装，再执行：

```text
objdump -d -arch-name=pi32v2 -mcpu=r3 app-linear.elf
```

完整汇编、ELF、精确片段和状态报告生成在 `analysis/anc-cycle-20261109/`。分析脚本逐条核对反汇编中显示的机器字节与原 app.bin，按**指令结束地址**解析相对调用，查找表地址引用并模拟状态转移。

在不能直接运行厂商x86工具的aarch64主机，也可先用 `python tools/disassemble_app.py --elf-only` 生成ELF，再把该ELF交给构建机的objdump；最后将生成的汇编拉回本机分析。公开整理时生成的新ELF已与实际原调查使用的ELF逐字节比较一致。

这是线性反汇编，不会自动区分整份程序的数据／代码、跳转表和动态搬移段。某些数据被打印成无效指令不代表固件损坏。当前按键函数的有效路径已经人工核对；人工还原的 C 伪代码不等于取得 QCY 原源码。小型证据片段直接保存在 [evidence/anc-cycle](../evidence/anc-cycle/)。

## 6. 参考 SDK 核对

```bash
python tools/bootstrap.py --reference-sdk
python tools/bootstrap.py --reference-binaries
```

参考均固定为 SDK 提交 `67328f4741c69993c88e504c67377b196c724f97`，下载后逐个检查摘要。它不是 QCY 的确切源码。小型源文件和 `media.a` / `update.a` 缓存在被忽略的 `tools/reference-cache/`。

需要重新检查 stream 标识时，可以在新的临时目录用 `ar` 提取 `media.a` 中的 `jlstream_file_parse.c.o`，再用兼容 LLVM bitcode 的 `llvm-dis` 查看。原调查使用 `llvm-dis-18`；旧调试元数据可能产生警告，不能把调试信息警告当作函数 IR 无法解析。

## 7. Android 和设备流程

参见 [Android 工具说明](../tools/android-query/README.md)。这一部分会实际访问设备，与上面纯离线流程分开。仓库整理和校验命令不会自动安装 App 或刷固件。

完整 QCY APK 不在仓库；若要重跑 App 分析，需要自行取得相应版本的 APK，并按 [App 分析](app-analysis.md) 处理加固与 DEX。不要把未脱壳／漏方法的 jadx 输出当作完整实现。
