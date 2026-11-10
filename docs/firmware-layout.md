# 固件容器、文件布局与校验层

本页的偏移均针对官方 SHA256 `65cafc7f...`。必须区分 **uFW 文件偏移、flash.bin 内偏移、app.bin 内偏移、CPU 运行地址**。

2026-11-10 已取得设备整片备份，并验证物理 bank A/B、VM与保留区的位置；新增实测布局见 [USB救砖](usb-recovery.md)。以下主表仍采用原UFW容器坐标，不能直接当整片烧录地址。

## 容器和平台判断

- uFW 长度1326848字节，头部声明 `AC701N`，官方文件名包含 `7018F`。
- 配置声明 `FLASH_SIZE=2097152`、`SDK_VERSION=AC701N_V1.0.0`。这是容器与构建标记，不是取得了 QCY 完整原工程。
- 主程序入口 `0x06000100`；实际指令以杰理 pi32v2 / r3 工具正确解码。
- 设备返回的 `jl_sdk_ac697_publish` project字符串不能单独用来推翻容器、指令和文件布局的证据，也不能据此按 ARM／RISC-V 解析。

## UFW 外层

前64字节为文件头，随后是13个80字节条目。文件头和每个条目分别用 JL ENC 初始key `0xffff` 解扰。头的结构为 `<HHIHHI48s>`；条目为 `<HHHHIII44s16s>`。

| 条目 | type | uFW偏移 | 有效长度 | padded长度 |
| --- | ---: | ---: | ---: | ---: |
| flash.bin | 0 | 1536 | 942080 | 942080 |
| info.log | 2 | 943616 | 0 | 0 |
| uboot.version | 55 | 943616 | 32 | 32 |
| TON | 50 | 943648 | 47024 | 47040 |
| TON1 | 50 | 990688 | 66368 | 66368 |
| ANCIF | 50 | 1057056 | 256 | 256 |
| ANCIF1 | 50 | 1057312 | 2760 | 2784 |
| isd_config.ini | 52 | 1060096 | 1526 | 1536 |
| v_ota.bin | 56 | 1061632 | 64 | 64 |
| ota.bin | 100 | 1061696 | 264880 | 264896 |
| farg.cfg | 251 | 1326592 | 5 | 32 |
| blimit.bin | 161 | 1326624 | 144 | 160 |
| tail.bin | 255 | 1326784 | 64 | 64 |

type50/52/55/56/161/251 的内容另做 SFC 解扰；外层资源的key相位使用**原始uFW绝对偏移**。不能把资源切下来后从0偏移按相同参数处理。其他支持的类型按原字节处理。数据 CRC 覆盖有效长度，目录寻址还要检查padded长度和文件边界。

原包的每项摘要、长度和CRC可由 `extract_ufw_resources.py` 重算，生成 `analysis/v1.1.8/outer/manifest.json`。

## flash.bin 和 JLFS

flash.bin在uFW中从 `0x600` 开始。以下表使用**flash内偏移**：

| 位置 | 内容 |
| --- | --- |
| 顶层JLFS目录 | 引导程序、isd_config.ini、app_dir_head、备用app目录、key_mac保留区等 |
| `0x2000..0x2020` | 独立编码的32字节双备份boot-info |
| `0x2020` | app_area_head，运行入口0x06000100，整个块长842912 |
| `0x2040` | 嵌套app.bin文件目录项 |
| `0x2120` | app.bin数据，842654字节 |
| `0xCFCC0` | EXT_RESERVED顺序资源块 |
| `0xCFD20` | ALIGN_DIR，内含anc_ext.bin |
| `0xDF030` | 3mic_coeff.bin资源块 |
| `0xDF454` | cfg_tool.bin资源块 |
| `0xDF8DC` | p11_code.bin资源块 |
| `0xE28D4` | scene.bin资源块 |
| `0xE2A4C` | stream.bin资源块头 |
| `0xE2A6C` | stream.bin数据，9351字节 |
| `0xE4EF3` | config.dat资源块 |
| `0xE5073` | 总应用区结束（不包含此字节） |

32字节JLFS项使用 `<HHIIBBH16s>`，包含头CRC、数据CRC、地址／偏移、长度／块长、flags、保留字节、结束标记及名称。名称字段可能带NUL和 `0xff` 填充；生成器原样保留不相关元数据。

顺序资源块的长度包含头，数据紧跟32字节头。app_area_head中的嵌套目录项则使用父区相对偏移；app.bin地址字段 `0x100` 指向 `0x2020+0x100=0x2120`，不能当绝对flash地址。

`VM/BTIF/ANCIF/ANCIF1` 的目录描述不等于已经获取了耳机这些区域的运行时内容。原始OTA资源不是完整设备flash备份。

## 编码和CRC

本包从配置恢复的SFC key是 `0x3496`。应用区从flash+0x2020起按32字节块处理；块key会结合位置变化。生成器不重写所有密文，而是在相同位置使用：

```text
new_cipher_byte = old_cipher_byte XOR old_plain_byte XOR new_plain_byte
```

这是利用该位置的XOR密钥流相同，避免改变无关区域。双备份头不属于这个SFC区，必须另用初始key `0xffff` 编解码。

CRC16参数为多项式0x1021、初始0、非反射；`crcmod.mkCrcFun(0x11021, initCrc=0, rev=False)` 与标准库 `binascii.crc_hqx(data, 0)` 对照验证。关键CRC层：

1. app.bin子项：数据CRC，再更新32字节目录头的头CRC。
2. app_area_head父项：覆盖其数据区，包括更新后的子项头与app数据，再更新父头CRC。
3. 各修改资源自身的JLFS数据与头CRC，例如stream.bin。
4. 双备份应用区整体的**明文CRC、密文CRC、boot-info头CRC**。
5. 外层flash.bin条目的数据CRC。
6. UFW条目目录CRC，最后UFW文件头CRC。

这些是不同层，不能用“外层CRC都过了”替代整体校验。

## 双备份boot-info：首刷遗漏的位置

原始解码内容：

```text
0cb0 3cc8 6abf 0000 53300e00 ffffffff ffffffff ffffffff ffffffff ffffffff
```

| 偏移 | 含义 | 官方原值 |
| --- | --- | --- |
| 0 | CRC16(header[2:32]) | 0xB00C |
| 2 | 应用区明文CRC16 | 0xC83C |
| 4 | 应用区密文CRC16 | 0xBF6A |
| 6 | 原包为0；实机两bank观测为序号2/3，恢复时保留目标bank原值 | 0 |
| 8 | 应用区长度 | 0xE3053 /929875 |

覆盖范围是flash `[0x2020, 0xE5073)`。三个原始数值全部能精确复算。第一次候选只更新了stream和外层CRC，仍保留原来的两个总体数据CRC，设备最终拒绝。仅提示音修复版的明文／密文／头CRC分别是 `0x8111 /0xF647 /0xC80A`。当前ANC版本另有app变化，实际值见 [完整性报告](../evidence/integrity.json)，不能继续沿用提示音版值。

`check_reproduction.py` 会重造那个只有总体CRC错误的历史包，确保独立校验器仍能检出。

## 按键表的四种地址

| 坐标 | 地址 |
| --- | --- |
| app.bin文件内 | 0x9E410 |
| CPU运行地址 | 0x06000100 +0x9E410 =0x0609E510 |
| flash.bin内 | 0x2120 +0x9E410 =0xA0530 |
| uFW文件内 | 0x600 +0xA0530 =0xA0B30 /658224 |

只能修改已验证源包的这一处，随后更新全部相关CRC。不要对所有 `01 03 02` 做全局替换。
