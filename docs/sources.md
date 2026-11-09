# 来源、依赖和分析方法

## 工具与固定版本

| 材料 | 固定版本／来源 | 用途 |
| --- | --- | --- |
| [jl-misctools](https://github.com/kagaimiq/jl-misctools) | 0a5b12db0ef38f3042acffbe2452730a37fd2405，Git子模块 | UFW/JLFS拆包、JL ENC/SFC和CRC参考 |
| [Android-JL_Bluetooth](https://github.com/Jieli-Tech/Android-JL_Bluetooth) | 6293873b641ffd8d461773873ecedef1a9d9038d | 官方OTA AAR与Android传输参考 |
| [JL701N参考SDK](https://gitlab.zh-jieli.com/ai_test/701n) | 67328f4741c69993c88e504c67377b196c724f97 | volume_cfg、stream标识、ANC及更新结构参考 |
| [Linux工具链](https://pkgman.jieliapp.com/s/linux-toolchain) | 本次20260730.1，LLVM4.0.1 | 厂商pi32v2反汇编 |
| [ghidra-jieli](https://github.com/quarkslab/ghidra-jieli) | e1bd0707874b77b759401555d24839ad43af1267 | 核对pi32v2的PC相对分支语义 |
| AndroidX Core | 1.7.0，Google Maven | JL SDK权限检查依赖 |
| Python依赖 | crcmod1.7、PyYAML6.0.2 | 生成／上游拆包；独立校验器不依赖它们 |

完整固定SHA在 [data/dependencies.json](../data/dependencies.json) 和 `tools/bootstrap.py`。厂商工具链下载别名可能更新，应保留版本记录；本仓库不重新发布整套专有工具链。

## 官方文档

- [Android OTA文档](https://doc.zh-jieli.com/Apps/Android/ota/en-us/master/index.html)。
- [OTA常见问题](https://doc.zh-jieli.com/Apps/Android/ota/en-us/master/other/qa.html)，涉及重复请求、分片和传输问题的调查方向；不是0x8E的唯一归因证据。
- [ANC控制](https://doc.zh-jieli.com/Apps/Android/jielihome/zh-cn/master/development/function/anc_ctrl.html)。
- [ADV设备设置和type10格式](https://doc.zh-jieli.com/Apps/Android/jielihome/zh-cn/master/development/function/tws_func.html)。

标准协议文档说明SDK能力，不等于H3S实现情况。比如通用type10查询没有返回目标字段，本项目未尝试写入，而转向实际固件分析。

## 固件与QCY云端查询

原包是QCY H3S官方0.1.1.8文件；精确摘要、长度和当前包身份固定在 [发布清单](../firmware/manifest.json)。没有取得官方0.1.1.9原包。

`tools/query_firmware.py` 保留早期云端调查代码：获取游客token，向 `https://appcontent.qcy.com/earphoneoOta/findEarphoneoOta` 提交modelId16764和版本，再可选下载响应文件。它不连接耳机。

云端响应会随时间变化，不能用“今天查询仍返回某记录”证明原包永远可下载。可复现构建应使用固定Release附件与摘要，查询工具只作后续调查入口。不要将临时token和带账号信息的响应提交仓库。

## App反编译与实际固件还原的区别

App部分来自QCY4.2.1/701的修复DEX和jadx分析，追踪Java调用与命令参数。H3S主程序则是raw pi32v2机器码，通过厂商objdump和手工控制流还原进行研究。

没有恢复QCY完整原始C源码、链接符号或工程流程图。`evidence/anc-cycle/key-cycle-reconstructed.c` 是人为命名的说明伪代码；地址和机器字节有直接依据，外部函数名称解释带有行为推断。

参考SDK中的源码／LLVM bitcode提供结构旁证。比如原包三个总体CRC精确复算和原始结构相互支持，比仅在SDK找到同名结构更强；相同错误码却时序不同则不能直接套解释。

## 未纳入Git的内容

为了让仓库可读、可复现且不携带会话隐私，以下仅由工具重新生成或由使用者自行提供：

- 原始／修改uFW作为Release附件；拆出的大二进制、完整汇编和ELF放ignored analysis/。
- SDK AAR、工具链及完整参考SDK缓存通过固定来源下载。
- QCY APK与完整反编译树由使用者自行取得，不复制到仓库。
- 原始JSONL/HCI、authKey、真实设备地址、nonce计划、调试签名及远程主机配置不公开。

固件格式的通用SFC key0x3496是输入包自带信息，不是运行时设备authKey。Git忽略规则不能代替人工检查，尤其不应直接 `git add` 原研究目录。
