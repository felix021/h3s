# 助听器弱化版 EQ L0 工程闭环演示（路 E / sim-eql0）

2026-11-13。本页记录 R2「助听器弱化版 EQ」的 **L0 主方案可行性演示**：离线把 stream.bin
§5 的**辅听链**（pipeline-mic_effect）参数改成「1kHz 高增益 + 输出限幅」一组演示值，重算
JLFS 资源 CRC 打包，产出字节级可复现的候选镜像，**不刷机**。参数定位/结构全部沿用路 CJ
[docs/hearing-aid-eq-feasibility.md](hearing-aid-eq-feasibility.md) 与路 CN
[docs/eq-gain-limit.md](eq-gain-limit.md) 钉死的证据；官方镜像 stream.bin sha
`19c9deebc2ad26d8bda47f979bfff759ec1203c50e5b6980c4f3d528581e72d2` 与 CJ 记录一致。

## 0. 一句话结论

**L0 工程闭环成立**：`tools/build_quiet_candidate.py` 新增 opt-in `--hearing-aid-demo`
开关，能按 §5 node_cfg_format 记录（name_hash 引用节点名）定位辅听链三个节点、原地改
float 参数、重算 JLFS CRC 打包；候选镜像与官方镜像的差异**仅**落在目标参数字节 + 相关
CRC 字段（共 23 个 .ufw 字节），往返解析确认 gain=39/freq=1000/out=100，`jl_crc16` 复核
一致。这是「参数区改动 + CRC 更新」的字节级复现闭环，不涉及新代码、不改任何名字/哈希。

## 1. 改动的参数表（改前 → 改后）

节点按 stream.bin §5 的 `node_cfg_format` 记录定位（`{flag,uuid16,subid,mode,name_hash
(djb2 低16位),f5,cfg_index,offset32,size16,resv[16]}`，32 字节/条；`offset32` 相对 §5 数据
起点，指向参数 blob）。名字经哈希引用，本演示不改名字，故 §5 记录 offset/size 与哈希全部
保持不变（参数尺寸也不变：eq_tool 168B / wdrc 76B / volume 16B）。

| 节点 | name_hash | uuid/subid | §5 offset | 字段 | 改前 | 改后 |
|---|---|---|---|---|---|---|
| Eq1Eff | 0xdabd | 0x737b/10 | 0xeac | `seg[5].freq` | 1070 Hz | **1000 Hz** |
| Eq1Eff | 0xdabd | 0x737b/10 | 0xeac | `seg[5].gain` | 0.0 dB | **+39.0 dB** |
| Drc1Eff | 0x63e0 | 0xdefe/6 | 0x1038 | `threshold[2].out` | 110.3 | **100.0** |
| VolEff | 0xb6c7 | 0x74e3/21 | 0x14d7 | `<BHiBihH>` | (0,16,-45,1,0,5,0) | 不变（定位校验） |

说明：

- **Eq1Eff** 与 Eq4Eff/Eq5Eff 共享同一参数区 @0xeac（三段共用，CJ §3.2）。eq_tool 布局
  `{float global_gain, 4B 段数字段, seg[10]{u16 index, u16 iir_type, i32 freq, float gain,
  float q}}`；段数字段是**大端 u16**（10 段=`00 0a 00 00`，与 6 段 eq 记录的 `00 06 00 00`
  对照自洽），其余字段小端。演示改 seg[5]（原 1070 Hz 最接近 1kHz）的频点与增益；其余 9
  段保持平坦 0dB 不变。
- **Drc1Eff** 的 `threshold[2].out` 是压缩拐点对的输出映射电平——原拐点 (0.3→0.3)/
  (90.3→90.3)/(110.3→110.3) 是「线性到 110.3 后拐平」的现成限幅形状（CJ §2），把第三个
  `out` 从 110.3 改成 100.0 即把输出天花板压到 100（数字域）。
- **VolEff** 是辅听链输入侧主音量（链：ADC→…→VolEff→EQ→DRC→LLNS→DAC），与本次「增益/限幅」
  演示正交，只做定位+往返校验、不改。

## 2. 字节级差异范围

改后的参数是浮点，逐字节变化只落在真正翻转的字节（freq 1070=0x42E→1000=0x3E8 变低 2 字节；
gain 0→39.0 变高 2 字节；out 110.3=0x42DC999A→100.0=0x42C80000 变低 3 字节），共 **7 个
stream 参数字节**。整条镜像（.ufw）差异**恰好 23 个字节**，分四类：

| 区域 | 字节数 | 内容 |
|---|---|---|
| stream 参数区（flash 0xe2a6c+§5 offset） | 7 | seg[5].freq/gain + threshold[2].out |
| stream JLFS 头 CRC（flash 0xe2a4c） | 4 | 数据 CRC(2) + 头 CRC(2) |
| 双备份 boot-info CRC（flash 0x2000） | 6 | 明文 CRC(2) + 密文 CRC(2) + 头 CRC(2) |
| UFW 外层 CRC 字段 | 6 | 外层头 CRC(2)+条目 CRC(2) + flash 条目 CRC(2) |

其余 1,326,825 字节与官方镜像**逐字节相同**（`verify_hearing_aid_diff` 逐字节精确比对，
diff == expected 才通过）。flash 层改动共 17 字节（不含 UFW 外层 6 字节）。

## 3. CRC 验证结果

- 官方 stream.bin `jl_crc16` = **0xc966**（与 unpack 输出 `C966` 一致）。
- 改后 stream.bin `jl_crc16` = **0x03be**。
- 候选镜像里 stream 的 JLFS 数据 CRC 字段 = **0x03be**（== `jl_crc16(改后 stream)`），
  头 CRC = **0xa4dd**（== `jl_crc16(头[2:32])`）——独立用 `jl_crc16` 重算一致。
- 双备份 boot-info 的明文/密文总体 CRC、UFW 外层 CRC 均按既有流程重算并通过
  `check_reproduction` 同款校验路径。

## 4. 诚实标注：数字域上限 ≠ SPL 标定

「整机最大输出限 ~100dB SPL」在固件里**没有声压标定**（CJ §4/CN §3 结论不变）：固件内无任何
DAC 满幅→dB SPL 的常量/表，`threshold[2].out=100.0` 的 100 只是**调音工具数字域 dB 刻度上的
绝对上限**（该刻度下原值即 110.3，明显不是 dBFS——数字音频不能超过 0dBFS，说明这是工具自有
标尺），**不是 100 dB SPL**。真机一次声压测量才能把数字 dB 校准到 SPL，属测量授权域、不在本
演示范围。此外 39dB 单段 peaking 会把整段频带放大约 90 倍，输出端必须靠这条限幅链压住，与
CN §3 结论一致。

## 5. 复跑命令与产物

```sh
# 前置：在仓库根目录、analysis/v1.1.8 已由 tools/unpack.py 生成
python3 tools/build_quiet_candidate.py --hearing-aid-demo --attenuation-db 0 \
    --output-dir <候选输出目录>
```

产物（文件名 `H3S_0.1.1.8_tones_minus0dB_HEARING_AID_DEMO_UNTESTED.ufw`）：

- 输出 sha256 `fbad438b74ccf7a3318279e1ba2cd2f77c6192d023d5f5900152c5f163545873`。
- `build-report.json` 内含 `hearing_aid_demo` 子键：三节点 change 记录（改前/改后值）、
  往返解析结果（freq=1000/gain=39.0/out_threshold=100.0）、字节级 diff 位置清单与计数。
- 断言（`apply_hearing_aid_demo` 的定位/初值校验 + `verify_hearing_aid_roundtrip` 往返 +
  `verify_hearing_aid_diff` 逐字节 diff）全部内联在脚本里，每次跑开关都会重跑，失败即抛错。
- 开关默认关：不带 `--hearing-aid-demo` 时 legacy 行为不变——`--attenuation-db 10
  --anc-cycle-two-state` 仍复现已测镜像 sha `b79cb3a6…`（已对照）。

红线：候选 UNTESTED 状态不变、不刷机、不提刷机建议。
