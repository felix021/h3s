# 提示音参数和stream标识：已知与未确认

当前发布包保留了两个提示音音量节点的-10dB配置调整。但用户在首次成功刷入后表示听感变化不明显，因此本项目只确认**参数被修改并随成功包发送**，没有确认实际声压降低10dB。

## 修改位置和结构

stream.bin共9351字节，资源数据从flash+0xE2A6C开始。音量节点来自第5节的记录；节内记录的偏移相对于该节数据起点0x871，不能直接当整个stream的文件偏移。

| 节点 | stream内结构起点 | 原min/max | 当前min/max |
| --- | --- | --- | --- |
| Vol_SysTone | 0x18F5 | -45 /0dB | -55 /-10dB |
| Vol_SysKTone | 0x1905 | -60 /0dB | -70 /-10dB |

参考同芯片 `volume_cfg` 的packed布局，生成器使用 `<BHiBihH>`：bypass、最大档位、最小dB、自定义表模式、最大dB、当前档位、表字节数。两个节点的最大档位16、当前档位6不变；Vol_SysKTone有16个float的自定义表，各项也减10dB。Vol_SysRing不修改。

本次未改WTS/AAC音频内容，没有进行解码后归一化或重新编码。ANC滤波器、音乐EQ和麦克风增益也未改。

## 参考代码提供了什么依据

固定SDK的 `volume_node.c` 从stream读取节点参数，把min/max或自定义dB表交给数字音量计算，支持这一结构解释。原包数据与节点名、目录记录和预期值对应，生成器还检查这些原值。

同时参考实现显示，若启用在线调音配置，`volume_ioc_start()`可在读stream之后，用 `jlstream_read_effects_online_param()` 覆盖min/max、bypass和自定义表。实际音量档位来自 `app_audio_get_volume()`，不一定使用配置里的cur_vol字段。

这说明存在“参数被运行时覆盖”等值得追踪的路径，**还不能证明QCY启用了该路径或这就是听感不明显的原因**。其他可能需要验证的点包括具体提示音是否走这两个节点、启动早期的播放路径及设备保存设置。没有做校准声压或波形对照，不以猜测替代测量。

## 第3节0x6C94是什么

原stream的节目录：

| 节头偏移 | 类型 | 数据长度 |
| --- | ---: | ---: |
| 0x0000 | 0 | 928 |
| 0x03A4 | 2 | 1113 |
| 0x0801 | 3 | 2，内容94 6C |
| 0x0807 | 4 | 98 |
| 0x086D | 5 | 6481 |
| 0x21C2 | 6 | 52 |
| 0x21FA | 7 | 8 |
| 0x2206 | 8 | 30 |
| 0x2228 | 10 | 603 |

最后一节正好结束于9351。生成器检查边界和唯一标识，始终保留0x6C94。

参考SDK的media.a中 `jlstream_file_parse.c.o` 是LLVM bitcode。`jlstream_read_stream_crc()`循环读取4字节节头，按18位长度跳过其他节，找到type3后直接读两个字节返回；该函数不遍历参数重算CRC。`effects_adj.c` 的 `eff_stream_crc_check()` 将其与在线工具发来的流程标识比较，不匹配返回流程错误。

因此保留这个值有参考实现依据；不是遗漏了已确认的整份参数CRC。但没有取得QCY工程生成器，也没有证明所有QCY运行路径与参考SDK相同。

## 来源和复跑

SDK固定提交 `67328f4741c69993c88e504c67377b196c724f97`：

- `SDK/audio/framework/nodes/volume_node.c`。
- `SDK/audio/framework/include/volume_node.h`。
- `SDK/audio/effect/effects_adj.c`，SHA256 `300d6483aca3e50af3b7c34b7555b6e06c196951cc2bcfec26f289d68d3b34fe`。
- `SDK/cpu/br28/liba/media.a`，SHA256 `6d1c5ed5e03594f3f508933ab06ec6a125f4c3cba75aad7be31da318120af233`。

通过 `tools/bootstrap.py --reference-sdk --reference-binaries` 获取并校验；不把整个厂商SDK复制进Git。具体命令见 [复现流程](reproduction.md)。
