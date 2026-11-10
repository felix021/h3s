# 第三方材料与归属

本项目的研究工具、人工分析记录与厂商固件、SDK、App是不同材料；不将一个软件许可泛化覆盖所有附件和第三方内容。

## jl-misctools

- 上游：https://github.com/kagaimiq/jl-misctools
- 固定提交：0a5b12db0ef38f3042acffbe2452730a37fd2405
- 作者：Andrey Grigoryev /kagaimiq。
- 上游采用MIT许可，原许可保留在Git子模块的LICENSE中。该子模块负责基础拆包与编码算法，不能把这些工作归为本项目原创。

## 杰理Android SDK与AndroidX

JL OTA AAR来自Jieli-Tech/Android-JL_Bluetooth的固定提交；该仓库包含Apache-2.0许可，SDK二进制及其使用还应查看上游随附说明。本仓库通过脚本下载AAR，未把其二进制重新作为本项目自编代码提交。

AndroidX Core1.7.0来自Google Maven，遵循其上游许可与通知。构建下载地址与摘要均可检查。

## 杰理芯片参考SDK、工具链与处理器模块

芯片参考源码来自公开JL701N SDK的固定提交，用来对照结构和行为；它不是QCY的确切源码。缓存不纳入Git，保留来源链接、版本和摘要。Linux工具链通过厂商入口取得，不随仓库重新打包。

启动研究另从同一固定SDK的`SDK/interface/system/boot.h`、`cpu.a`、`system.a`核对交接布局及IR。完整库、提取对象和IR只进入忽略的本机缓存／analysis，未重新发布，也不适用本仓库自编工具的软件许可。下载和本机LLVM分析流程见[第二轮启动记录](docs/boot-handoff.md)。

quarkslab/ghidra-jieli派生于kagaimiq的处理器定义，本项目仅引用固定版本来核对分支语义，未复制整个模块；其权利和许可按各上游说明。

## pi32v2 指令模拟器

作者 Julius Vitkauskas 的 https://github.com/jvitkauskas/pi32v2-isa ，固定提交 `3608ed19bc21324f8ecfe79cc0018d05e43d9563`。上游代码使用 MIT-0，文档和指令表使用 CC0-1.0；许可保留在下载缓存的 `LICENSES/`。本项目通过脚本获取固定提交，使用其解码器、CPU 执行器和指令模型，不将这些成果归为本项目原创。H3S 地址／内存限制、API 桩、用例与报告是本项目的适配层；它没有把上游 AC791N 验证泛化为 BR28 整机认证。缺失 ID 文件的加载适配见 [离线执行说明](docs/preflash-emulation.md)。

FM-1 的 QEMU／Rust 模拟器目前仅作为研究来源链接，没有复制或链接其实现；实际使用时应分别检查各自的许可证和通知。

## QCY固件、App与型号配置

原始固件、App和型号控制面板来源于QCY相关产品。本仓库提供用户要求保存的原始／修改固件附件、分析结果和复现代码，不声称拥有厂商原程序版权，也不对这些厂商材料另行授予软件许可。

完整QCY APK和整棵反编译源码未纳入仓库。分析中的方法名、协议格式、地址与小型机器码片段用于说明实际研究结论；人工伪代码不是厂商原始源码。

## jl-uboot-tool 与 USB RAM loader

USB MSC/BR28协议参考Andrey Grigoryev (kagaimiq) 的 https://github.com/kagaimiq/jl-uboot-tool ，固定提交 `adb3f18889e88ac512ce0a3c4d8cc3d3cb30696a`，MIT许可。对应许可全文保存在 `tools/usb-recovery/UPSTREAM_LICENSE`，相关协议实现保留来源说明。

BR28 RAM loader是该提交的 `data/loaderblobs/usb/br28loader.bin`，23552B、SHA256 `ab1b77eb73385aaa78ab769253b2af7ef63d0ade46f391bd7424cc598a40267c`。本仓库提供固定URL和摘要下载，不重新打包第三方二进制，也不把仓库级MIT声明泛化为拥有厂商二进制的版权。参考芯片SDK的USB入口来自固定JL701N提交；实际QCY机器码和设备响应另行核对。
