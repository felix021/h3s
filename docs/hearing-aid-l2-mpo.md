# 助听 L2「每频率限幅（MPO）」落地：全带水平尾段曲线（路 HAL2 / sim-hal2）

2026-11-17。任务：把 App 助听 tab 留桩的「每频输出上限」接实，实现「每频增益（如
1kHz +39dB）+ 整体输出不超 ~100dB（数字域）」。本路产出**离线镜像 + 参数层验证**，
不刷机、不构成「助听器可用」或「瞬时峰值保护已验证」的声明。前置：main 2712dcb
（L1KEY 已 merge，双击多功能键 toggle 辅听）。

## 0. 一句话结论

codex 参谋（gpt-6-astra，`~/tmp/codex/202611172044-hal2/final.md`）三选一判：**采纳修正版
(a)——单段 Drc1Eff 写「水平尾段」三点曲线做全带 MPO**，并揪出既有 L0 演示的语义错误：
DRC 核心对末段**线性外推、无末点钳位**，只改 `threshold[2].out` 是压缩比不是上限。本路：

1. **固件参数侧**：`build_quiet_candidate.py` 新增 `--hearing-aid-mpo <dB>`（可与
   `--hearing-aid-demo` 组合，默认演示 M=100），把 Drc1Eff 三点写成
   (0.3,0.3)/**(M,M)/(X,M)**——末段斜率 0、截距 M，任意过驱动输入都映射到 M。
2. **App 侧**：`index.html` 助听 tab 每频上限滑杆聚合为 `min(band)` 全带生效值，
   payload 如实携带聚合规则 + 三点曲线；BLE 参数写入通道本路不做（留桩，§5）。
3. **验证**：独立校验器 `verify_hearing_aid_l0.py` 参数化（mode/mpo）+ 新增曲线语义
   断言（末段斜率 0、外推不过 M、拐点严格递增）+ 旧形状负例；单测
   `test_hearing_aid_mpo_image.py`（逻辑层 5 + 工件层 4 断言）。

## 1. codex 三选一结论（参谋记录）

| 方案 | 判定 | 依据 |
|---|---|---|
| **(a) 单段 Drc1Eff 全带 MPO** | **采纳（修正版）** | Drc1Eff 是辅听链唯一 WDRC（§5 记录 4 个 wdrc-uuid 节点：DRC7/8/9 属媒体链）；参数区现成、CRC 打包链路现成 |
| (b) EQ 增益上限近似 | 否 | EQ 在 DRC 前，gain cap 只限增益量不限绝对输出；且 App 滑杆范围 gain≤40 < limit≥60，`gain[i] ≤ MPO[i]` 恒真、纯空转 |
| (c) 真 multiband DRC | 参数层不可行 | ops 表 28 条无 multiband_drc（0xdefe 单带 wdrc；虽有 3band_merge 0x1b9d/2band_merge 0x665c，但组多带需改 pipeline 拓扑+新参数结构，超出本路参数层边界） |
| (d) a+b 组合 | 不取 | 限幅能力全来自 a，b 无增量保证（见 (b)） |

**关键修正（codex 揪出、本路 IR 复核确认）**：路 E L0 演示「只改 threshold[2].out
110.3→100 即上限 100」不成立。证据（参考 SDK drc.a/drc.o LLVM IR，钉死 archive）：

- `DrcInit`（drc.o.ll:1017/1097）：内部边界 = th[1..N-2].in（N=threshold_num=3 时即
  th[1].in 一个边界），逐段斜率 k[i]=(out[i+1]-out[i])/(in[i+1]-in[i])；
- `DrcGainProcess`（drc.o.ll:128）：按检测电平选段后算 `k×电平+b`，**没有任何
  「超末点就钳到末点 out」的分支**——末段直线被外推；
- `TWOPOINT`（mode=1，drc.o.ll:504）：是**立体声双采样处理**（第二个采样复用第一个
  采样的增益），不是「两组阈值」。

于是旧形状 (0.3,0.3)/(90.3,90.3)/(110.3,**100**) 的末段斜率 = (100-90.3)/(110.3-90.3)
≈ **0.485**，120dB 检测电平映射 ≈ **104.7dB**——超过 100，不是上限。修正形状
(0.3,0.3)/**(100,100)/(110.3,100)**：段0 单位增益到 100，段1 斜率 0、截距 100，
**任意**过驱动电平都映射 100。通用规则 `(x0,x0)、(M,M)、(X,M)`（x0<M<X）；M≥110.3
时 X 抬到 M+10.3 保证 in 严格递增（分母为零会让斜率成 NaN）。M>0.3 由脚本校验。

## 2. 固件参数侧（tools/build_quiet_candidate.py）

- `apply_hearing_aid_mpo_curve(stream, mpo)`：定位 Drc1Eff（name_hash 0x63e0/uuid
  0xdefe/subid 6/@0x1038/76B），校验官方初值（bypass=0/attack=100/release=200/
  tnum=3/拐点三点/inputgain=outputgain=0），写 4 个 float：th[1].in、th[1].out= M、
  th[2].in=X、th[2].out=M；写完即跑 `verify_mpo_curve`。
- `verify_mpo_curve(knees, tnum, mpo)`：按 IR 语义独立断言曲线确是 M 上限——in 严格
  递增、末段斜率 0、探测点（含末点+30/+300dB 过驱动外推）全部 ≤M。
- `--hearing-aid-mpo <dB>`：单用只动 Drc1Eff（不动 EQ/VolEff）；与
  `--hearing-aid-demo` 同用覆盖演示默认 100。`--hearing-aid-demo` 本身改为多点写入
  （对路 E 的语义修正），字节差异 23→**29**（13 参数 + 16 CRC）。纯 MPO 候选差异
  = 9 参数字节 + 16 CRC = 25。MPO≥110.3 时多 1 字节（th[2].in 变化）。
- 非法输入（≤0.3、NaN/inf）在写入前被拒；X 规则保证任何合法 M 都满足严格递增。
- 报告（build-report.json `hearing_aid_demo` 键）新增 `mode`/`mpo_db`/`mpo_semantics`
  （全带数字域稳态上限；非每频独立、非 SPL、attack=100/TWOPOINT 下不保证瞬时峰值），
  `roundtrip` 改为 `mpo_curve` 三点 + `threshold_num`。

## 3. 独立校验（tools/verify_hearing_aid_l0.py）

- 不 import builder，独立 CRC + 逐字节 diff + 参数往返（路 E 口径不变）。
- 新增 `--mode {demo,mpo}` + `--mpo <dB>`：demo 校验 1kHz/+39dB+曲线；mpo 校验 EQ
  保持官方值（1070/0dB）+ 曲线。
- 新增 `check_mpo_curve`：与 builder 同规则独立实现的曲线语义断言（末段斜率 0、外推
  不过 M、拐点严格递增）。
- 读回字段从单一 `threshold[2].out` 扩为三点拐点 + attack/release/bypass 保持校验。

## 4. App 侧（tools/android-query/assets/index.html）

- `buildHearingAidPayload()`：每频上限聚合 `M=min(band.out_threshold)`，payload 携带
  `mpo: {effective, aggregation, curve: 三点, threshold_num: 3, semantics}`；`band_out_threshold`
  数组保留为「每频独立限幅（L3 multiband）」的用户输入存档。不再伪装原生 wdrc_struct
  （旧桩 `threshold_num: 6` 与固件实际 3 不符，已改 3）。
- UI：新增「全带 MPO（聚合生效值）」汇总行（取十频最小值，任一频段调低压低全带；
  单带 WDRC 说明），每次滑杆变动/复位即时刷新。X 规则与固件一致（M<110.3 用 110.3，
  否则 M+10.3），node 冒烟验证 min 聚合与尾点抬高行为正确。
- 命名诚实：交付名是「全带数字压缩上限参数候选」，不写「每频 MPO 已实现」。

## 5. BLE 绑定留桩与下一步

App→固件参数写入通道今日不存在：`haChanged()` 仍提示「BLE 命令留桩，未发送」；
r4-keyhook 候选（`--r4-keyhook`）只接了 8B VM config key 30 往返（cmdid 0x30），**配置
存进去与音频参数生效之间缺消费链**（固件里没有「读 key→写 stream 参数→重启 pipeline」
的路径）。下一步（L3 候选）：版本化参数格式 + 固件侧消费载荷（读 key 30 → 解包 → 写
Eq1Eff/Drc1Eff 参数 → 触发 pipeline 重载），或等待厂商 OTA。每频独立限幅（multiband：
分频+多 WDRC+merge 节点拓扑）也在 L3 之后。

## 6. 诚实边界

- 本路 = 离线镜像 + 参数层验证（CRC/字节差异/往返/曲线语义断言），**不刷机**。
- M 是调音工具数字域 dB 刻度（原值 110.3 的同一标尺），**不是 dB SPL**；真机声学标定
  需声压计，留待授权（CJ §4 / CN §3 口径不变）。
- attack=100/release=200 + TWOPOINT 双采样平滑下，**稳态**上限成立≠瞬时峰值不越限
  （PEAK 检测器的建立时间内的瞬态仍可能超出）；「防瞬间峰值伤听力」的完整结论需整链
  脉冲验证，不在本路。
- 参考 SDK IR 证据对 QCY 镜像的适用性沿用 CN 红线：无 patch 迹象，但未逐函数证明一致。

## 7. 复跑命令

```sh
python3 tools/build_quiet_candidate.py --attenuation-db 0 --hearing-aid-mpo 95 \
    --output-dir <目录>          # 纯全带 MPO 候选（EQ 不动）
python3 tools/build_quiet_candidate.py --attenuation-db 0 --hearing-aid-demo \
    --output-dir <目录>          # 1kHz+39dB + MPO=100 曲线（修正版演示）
python3 tools/verify_hearing_aid_l0.py --mode mpo --mpo 95 \
    --candidate <目录>/H3S_0.1.1.8_tones_minus0dB_HEARING_AID_MPO95_UNTESTED.ufw
python3 tools/test_hearing_aid_mpo_image.py   # 逻辑层 + 工件层（有固件输入时）
```

## 8. 证据锚点

- codex 参谋全文：`~/tmp/codex/202611172044-hal2/final.md`（不入库）；prompt 同目录
  prompt.txt，log.md。
- IR：drc.o.ll DrcInit:1017/1097（边界/斜率）、DrcGainProcess:128（选段外推无钳）、
  :504（TWOPOINT 双采样）；audio_wdrc.c.ll:609（threshold_num 透传）。IR 本地缓存
  /tmp/sdk-libs/drc.o.ll、/home/felix021/tmp/hearaid-cj/ir/（SDK archive SHA 钉死，
  bootstrap 67328f4）。76B blob 结构对 audio_wdrc.h wdrc_struct_tool_set（含独立
  float rms_time）吻合，sw_drc.h 分支无独立 rms_time——CJ §2 的结构出处描述按此修正，
  字节布局本身不变。
- stream.bin §5：4 个 wdrc-uuid(0xdefe) 节点 = DRC7/8/9（媒体，@0xf54/0xfa0/0xfec）+
  Drc1Eff（辅听，@0x1038）；ops 表 [0xa8dd0,0xa9070) 28 条含 wdrc/3band_merge(0x1b9d)/
  2band_merge(0x665c)/data_saturation(0x6b29)，无 multiband_drc。
- 单测：tools/test_hearing_aid_mpo_image.py（负例=旧单点形状在 120dB 处 ~104.7dB 越
  限被拒；M≥110.3 尾点抬高；篡改 1 字节独立校验必红）。
