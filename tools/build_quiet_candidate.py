#!/usr/bin/env python3
"""Generate fixed-source H3S tone/ANC images offline; never flashes a device.

The exact published ANC artifact was device-tested; newly generated settings
are offline candidates. See docs/validation.md for evidence and limitations.
Requires analysis/v1.1.8 from tools/unpack.py and the pinned jl-misctools submodule.
"""
import argparse
import hashlib
import json
import math
from pathlib import Path
import struct
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools/jl-misctools/firmware"))
from jltech.cipher import jl_enc_cipher, jl_sfc_cipher
from jltech.crc import jl_crc16

SOURCE_SHA = "65cafc7f9a7545c5c30701d141c77db334e37065589e42c51306132c94c879af"


def stream_flow_identifier(data):
    """Mirror the section traversal in reference SDK jlstream_read_stream_crc()."""
    pos = 0
    identifiers = []
    while pos < len(data):
        if pos + 4 > len(data):
            raise ValueError("stream 节头越界")
        kind = data[pos]
        size = ((data[pos + 1] & 3) << 16) | int.from_bytes(data[pos + 2:pos + 4], "little")
        end = pos + 4 + size
        if end > len(data):
            raise ValueError("stream 节数据越界")
        if kind == 3:
            if size != 2:
                raise ValueError("stream 流程标识节长度不匹配")
            identifiers.append(int.from_bytes(data[pos + 4:end], "little"))
        pos = end
    if identifiers != [0x6c94]:
        raise ValueError("stream 流程标识与已核实版本不一致")
    return identifiers[0]


def encode_header(data):
    data = bytearray(data)
    jl_enc_cipher(data, 0, len(data), 0xffff)
    return data


def f32(v):
    """Round a Python float to the exact IEEE754 single the firmware stores."""
    return struct.unpack("<f", struct.pack("<f", v))[0]


# 辅听链（pipeline-mic_effect）三个节点在 stream.bin §5 记录里的 name_hash（djb2 低 16
# 位）。名字经哈希引用，本路只改参数 blob、不改名字，故哈希/§5 记录 offset/size 全部
# 保持不变。证据：docs/hearing-aid-eq-feasibility.md §1.2/§2/§7。
HEARING_AID_NODES = {
    "Eq1Eff": {"name_hash": 0xdabd, "uuid": 0x737b, "subid": 10,
               "offset": 0xeac, "size": 168},  # eq_tool，与 Eq4Eff(0x7fe0)/Eq5Eff(0x0c41) 共享
    "Drc1Eff": {"name_hash": 0x63e0, "uuid": 0xdefe, "subid": 6,
                "offset": 0x1038, "size": 76},  # wdrc_struct
    "VolEff": {"name_hash": 0xb6c7, "uuid": 0x74e3, "subid": 21,
               "offset": 0x14d7, "size": 16},  # volume_cfg <BHiBihH>
}


def stream_param_records(data):
    """定位 stream.bin 第 5 节参数区并解析 node_cfg_format 记录数组。

    节头 4 字节 = 1 字节 kind + 18 位长度（第 2 字节低 2 位是长度高 2 位 + 2 字节 LE）。
    §5 = 16 字节头 + 32 字节记录数组 + 参数 blob。记录里 u32 `offset` 是相对 §5 数据
    起点的绝对偏移，指向该节点的参数 blob。布局参考 docs/hearing-aid-eq-feasibility.md
    §1.2（node_cfg_format 经参考 SDK IR 复核）。
    """
    pos = 0
    s5_base = None
    while pos + 4 <= len(data):
        kind = data[pos]
        size = ((data[pos + 1] & 3) << 16) | int.from_bytes(data[pos + 2:pos + 4], "little")
        if kind == 5:
            s5_base = pos + 4
            s5_size = size
            break
        pos += 4 + size
    if s5_base is None:
        raise ValueError("stream 缺少第 5 节参数区")
    records = []
    for rec_off in range(s5_base + 16, s5_base + s5_size - 16, 32):
        rec = data[rec_off:rec_off + 32]
        if rec[0] not in (1, 2, 3):
            break  # 记录数组尾（type=4 终结记录）
        records.append({
            "type": rec[0],
            "uuid": struct.unpack_from("<H", rec, 1)[0],
            "subid": rec[3],
            "mode": rec[4],
            "name_hash": struct.unpack_from("<H", rec, 5)[0],
            "cfg_index": rec[9],
            "offset": struct.unpack_from("<I", rec, 10)[0],
            "size": struct.unpack_from("<H", rec, 14)[0],
        })
    return s5_base, records


def apply_hearing_aid_mpo_curve(stream, mpo):
    """把 Drc1Eff 写成「水平尾段」MPO 限幅曲线（原地改、尺寸不变）。

    路 L2/codex 复核修正：DRC 核心按相邻拐点对算分段斜率、对末段**线性外推**，
    没有超末点钳位（drc.o DrcInit/DrcGainProcess）——只改 threshold[2].out 得到的是
    压缩比 ~2:1 而非上限（120dB 输入仍映出 ~104.7dB）。真正的稳态上限需要把三点写成
    (0.3,0.3)/(M,M)/(X,M)：末段斜率 0、截距 M，任意过驱动输入都映射到 M。
    X 取 110.3（M 低于原末点时保持），M≥110.3 时抬高到 M+10.3 保证 in 严格递增
    （分母为零会让 DrcInit 算出 NaN 斜率）。M 需 > 0.3。
    返回 (changes, s5_base)。M 是调音工具数字域 dB 刻度，非 SPL 标定。
    """
    if not (mpo > 0.3):
        raise ValueError(f"MPO 必须 > 0.3（数字域 dB），收到 {mpo}")
    mpo = f32(mpo)
    s5_base, records = stream_param_records(stream)
    spec = HEARING_AID_NODES["Drc1Eff"]
    rec = {(r["name_hash"], r["cfg_index"]): r for r in records}[(spec["name_hash"], 0)]
    drc_off = s5_base + rec["offset"]
    bypass = struct.unpack_from("<i", stream, drc_off)[0]
    attack, release = struct.unpack_from("<HH", stream, drc_off + 4)
    tnum = stream[drc_off + 68]
    if (bypass, attack, release, tnum) != (0, 100, 200, 3):
        raise ValueError("Drc1Eff 初始参数与已核实版本不一致")
    knees = [struct.unpack_from("<ff", stream, drc_off + 8 + i * 8) for i in range(3)]
    expected = [(f32(0.3), f32(0.3)), (f32(90.3), f32(90.3)), (f32(110.3), f32(110.3))]
    if knees != expected:
        raise ValueError("Drc1Eff 初始拐点与已核实版本不一致")
    x_tail = f32(110.3) if mpo < f32(110.3) else f32(mpo + 10.3)
    if not (f32(0.3) < mpo < x_tail):
        raise ValueError("拐点 in 电平必须严格递增")
    changes = []
    for field, offset, after in (("threshold[1].in", 8 + 8, mpo),
                                 ("threshold[1].out", 8 + 12, mpo),
                                 ("threshold[2].in", 8 + 16, x_tail),
                                 ("threshold[2].out", 8 + 20, mpo)):
        off = drc_off + offset
        old = struct.unpack_from("<f", stream, off)[0]
        struct.pack_into("<f", stream, off, after)
        changes.append({"node": "Drc1Eff", "stream_off": off, "field": field,
                        "before": old, "after": after,
                        "note": "水平尾段 MPO（数字域稳态上限，非 SPL 标定）"})
    verify_mpo_curve([(f32(0.3), f32(0.3)), (mpo, mpo), (x_tail, mpo)], 3, mpo)
    return changes, s5_base


def verify_mpo_curve(knees, tnum, mpo):
    """按 DrcInit/DrcGainProcess 的分段线性语义断言曲线确实是 M 上限。

    斜率 k[i]=(out[i+1]-out[i])/(in[i+1]-in[i])；检测电平超过末拐点后沿用末段直线
    （无钳位）。要稳态上限=M：in 严格递增（防 NaN 斜率）、末段斜率=0、截距=M、
    全曲线（含 +30dB 过驱动外推）不超过 M。
    """
    pts = knees[:tnum]
    ins = [p[0] for p in pts]
    if any(b <= a for a, b in zip(ins, ins[1:])):
        raise ValueError(f"拐点 in 电平非严格递增：{ins}")
    slopes = [(pts[i + 1][1] - pts[i][1]) / (pts[i + 1][0] - pts[i][0])
              for i in range(len(pts) - 1)]
    if slopes[-1] != 0.0:
        raise ValueError(f"末段斜率非 0（{slopes[-1]}），过驱动输入会突破 {mpo}")
    for probe in (ins[0], mpo / 2, mpo, ins[-1], ins[-1] + 30.0, ins[-1] + 300.0):
        # 分段选择照 DrcGainProcess：内部边界=pts[1..tnum-2].in，段号=严格高于
        # 检测电平的边界数；超出末拐点沿用末段直线外推（无钳位）。
        seg = sum(1 for i in range(1, len(pts) - 1) if probe > pts[i][0])
        b = pts[seg + 1][1] - slopes[seg] * pts[seg + 1][0]
        if slopes[seg] * probe + b > mpo + 1e-4:
            raise ValueError(f"曲线在 {probe} dB 处突破 MPO {mpo}")
    return slopes


def apply_hearing_aid_demo(stream, mpo=100.0):
    """离线把辅听链参数改成「1kHz +39dB + MPO 限幅」演示值，原地改、尺寸不变。

    - Eq1Eff（eq_tool @0xeac，与 Eq4Eff/Eq5Eff 共享）：seg[5] 1070Hz/0dB → 1000Hz/+39dB。
    - Drc1Eff（wdrc_struct @0x1038）：水平尾段 MPO 曲线（默认 100.0，见
      apply_hearing_aid_mpo_curve；多点写入是对路 E「只改 threshold[2].out 不成上限」
      的修正）。
    - VolEff（volume_cfg @0x14d7）：定位并校验，不改（输入侧主音量，与本次增益/限幅演示正交）。
    返回 (changes, s5_base)。
    """
    s5_base, records = stream_param_records(stream)
    by_key = {(r["name_hash"], r["cfg_index"]): r for r in records}

    def locate(spec):
        rec = by_key.get((spec["name_hash"], 0))
        if rec is None or rec["uuid"] != spec["uuid"] or rec["subid"] != spec["subid"]:
            raise ValueError(f"第 5 节未定位到节点 name_hash=0x{spec['name_hash']:04x}")
        if (rec["offset"], rec["size"]) != (spec["offset"], spec["size"]):
            raise ValueError(f"节点 {spec} 参数区偏移/尺寸与已核实版本不一致")
        return rec

    eq = locate(HEARING_AID_NODES["Eq1Eff"])
    drc = locate(HEARING_AID_NODES["Drc1Eff"])
    vol = locate(HEARING_AID_NODES["VolEff"])

    # VolEff 只读校验，不改。
    vol_values = struct.unpack_from("<BHiBihH", stream, s5_base + vol["offset"])
    if vol_values != (0, 16, -45, 1, 0, 5, 0):
        raise ValueError("VolEff 初始参数与已核实版本不一致")

    changes = []
    # eq_tool：{float global_gain, 4B 段数字段, seg[10]{u16 index, u16 iir_type,
    # i32 freq, float gain, float q}}。+4..7 为位域 {u8 bypass, u8 seg_num,
    # u16 pad}（10 段=00 0a 00 00；HAL4 勘误：旧「大端 u16 段数」表述作废，
    # 消费端 eq_open_config 读 +4 为 bypass、eq ioctl 读 bits8..15 为段数，
    # 见 docs/hearing-aid-l4-eq.md §1.5）；其余字段小端；seg[i] 位于 +8+i*16。
    # 尺寸=8+16*seg_num 与记录 size 自洽。
    eq_off = s5_base + eq["offset"]
    seg_num = struct.unpack_from(">H", stream, eq_off + 4)[0]
    if seg_num != 10 or eq["size"] != 8 + 16 * seg_num:
        raise ValueError("Eq1Eff 段数/尺寸与已核实版本不一致")
    target = 5
    freq_off = eq_off + 8 + target * 16 + 4
    gain_off = eq_off + 8 + target * 16 + 8
    old_freq = struct.unpack_from("<i", stream, freq_off)[0]
    old_gain = struct.unpack_from("<f", stream, gain_off)[0]
    if old_freq != 1070 or old_gain != 0.0:
        raise ValueError("Eq1Eff 目标段初始参数与已核实版本不一致")
    struct.pack_into("<i", stream, freq_off, 1000)
    struct.pack_into("<f", stream, gain_off, 39.0)
    changes.append({"node": "Eq1Eff", "stream_off": freq_off, "field": "seg[5].freq",
                    "before": old_freq, "after": 1000})
    changes.append({"node": "Eq1Eff", "stream_off": gain_off, "field": "seg[5].gain",
                    "before": old_gain, "after": 39.0})

    # Drc1Eff：水平尾段 MPO 曲线（多点写入，见 apply_hearing_aid_mpo_curve）。
    mpo_changes, _ = apply_hearing_aid_mpo_curve(stream, mpo)
    changes.extend(mpo_changes)
    changes.append({"node": "VolEff", "stream_off": s5_base + vol["offset"],
                    "field": "<BHiBihH>", "before": list(vol_values),
                    "after": list(vol_values), "unchanged": True,
                    "note": "输入侧主音量，本演示不改"})
    return changes, s5_base


def verify_hearing_aid_roundtrip(stream, s5_base, mpo=100.0, eq_demo=True):
    """把改后的参数区按 §1.2 结构重新解析，确认 EQ/MPO 曲线如预期（往返一致）。"""
    _, records = stream_param_records(stream)
    by_key = {(r["name_hash"], r["cfg_index"]): r for r in records}
    eq = by_key[(HEARING_AID_NODES["Eq1Eff"]["name_hash"], 0)]
    drc = by_key[(HEARING_AID_NODES["Drc1Eff"]["name_hash"], 0)]
    eq_off = s5_base + eq["offset"]
    drc_off = s5_base + drc["offset"]
    freq = struct.unpack_from("<i", stream, eq_off + 8 + 5 * 16 + 4)[0]
    gain = struct.unpack_from("<f", stream, eq_off + 8 + 5 * 16 + 8)[0]
    if eq_demo and (freq, gain) != (1000, f32(39.0)):
        raise ValueError(f"改后 Eq1Eff 往返解析不一致：freq={freq} gain={gain}")
    if not eq_demo and (freq, gain) != (1070, 0.0):
        raise ValueError(f"纯 MPO 模式不应动 EQ：freq={freq} gain={gain}")
    mpo = f32(mpo)
    knees = [struct.unpack_from("<ff", stream, drc_off + 8 + i * 8) for i in range(3)]
    tnum = stream[drc_off + 68]
    x_tail = f32(110.3) if mpo < f32(110.3) else f32(mpo + 10.3)
    if knees != [(f32(0.3), f32(0.3)), (mpo, mpo), (x_tail, mpo)] or tnum != 3:
        raise ValueError(f"改后 Drc1Eff 往返解析不一致：knees={knees} tnum={tnum}")
    verify_mpo_curve(knees, tnum, mpo)
    return {"freq": freq, "gain": gain,
            "mpo_curve": [[k[0], k[1]] for k in knees], "threshold_num": tnum}


def verify_hearing_aid_diff(result, source, flash_pos, data_pos, ent_pos, boot_pos,
                            stream_changed_offsets):
    """断言候选 .ufw 与官方镜像的差异只落在目标参数区 + CRC 字段，其余逐字节不变。

    期望差异 = 改动的 stream 参数字节（相对 stream 起点）映射到 flash，加上 stream 的
    JLFS 头 CRC、双备份 boot-info 的明文/密文/头 CRC、以及 UFW 外层三处 CRC 字段。
    逐字节精确比对；任何多余/缺失字节都直接抛错。返回 (diff, expected) 供报告。
    """
    expected = {flash_pos + data_pos + i for i in stream_changed_offsets}
    expected |= {flash_pos + ent_pos + k for k in range(4)}   # stream JLFS 头 CRC（2+2）
    expected |= {flash_pos + boot_pos + k for k in range(6)}  # boot-info 明文/密文/头 CRC
    expected |= {0, 1, 2, 3, 68, 69}                          # UFW 外层头 CRC + flash 条目 CRC
    diff = {i for i in range(len(source)) if result[i] != source[i]}
    if diff != expected:
        missing = sorted(expected - diff)
        unexpected = sorted(diff - expected)
        raise ValueError("字节差异与预期不符："
                         f"missing={missing[:24]} unexpected={unexpected[:24]}")
    return sorted(diff), sorted(expected)


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--attenuation-db", type=int, default=10, choices=range(0, 31))
    p.add_argument("--anc-cycle-two-state", action="store_true",
                   help="将已逆向的按键轮转表由 OFF/TRANS/ON 改为 ON/TRANS/ON；App 直接选模式不变")
    p.add_argument("--eq-toggle-elf", type=Path,
                   help="实验性 EQ 双击开关补丁 ELF；必须同时启用 ANC 两态选项")
    p.add_argument('--eq-code-fence-repair',action='store_true',
                   help='离线修复候选：扩展实际Flash PC上界到EQ载荷末尾；不代表启动验证通过')
    p.add_argument("--hearing-aid-demo", action="store_true",
                   help="助听器弱化版 EQ L0 演示：辅听链 1kHz +39dB + MPO 限幅曲线（默认 100dB）；仅离线候选")
    p.add_argument("--hearing-aid-mpo", type=float, metavar="DB",
                   help="全带 MPO 数字域上限（dB，>0.3）：把 Drc1Eff 写成水平尾段曲线 (0.3,0.3)/"
                        "(M,M)/(X,M)——多点写入才是真稳态上限（只改末点 out 只是压缩比，路 L2 修正）；"
                        "单用时只动 Drc1Eff 不动 EQ；与 --hearing-aid-demo 同用则覆盖其默认 100；"
                        "数字域刻度非 SPL 标定，仅离线候选")
    p.add_argument("--hearing-aid-l1-patch", action="store_true",
                   help="辅听 L1 离线候选：追加 mic_effect 激活载荷 + ANC 通透档接驳 + PC fence 扩界；"
                        "不可与 --eq-toggle-elf 同用（追加区冲突）；候选 UNTESTED 不刷机")
    p.add_argument("--hearing-aid-l1-close", action="store_true",
                   help="辅听 L1 双向离线候选：隐含 --hearing-aid-l1-patch，追加 close 载荷并把两条链的"
                        " ON/OFF 落点窗口接驳为先幂等关辅听再走原 apply（离开通透即收口）；候选 UNTESTED 不刷机")
    p.add_argument("--hearing-aid-l1-slotram", action="store_true",
                   help="辅听 L1 数据槽 RAM 化离线候选：隐含 --hearing-aid-l1-close，数据槽从追加段尾"
                        "（flash 映射区，普通 store 写不进）挪到 boot 清零 BSS 0x125574——close 链依赖槽里的"
                        " stream 句柄，这是 close 在真机能真正关断的前提；候选 UNTESTED 不刷机")
    p.add_argument("--r4-keyhook", action="store_true",
                   help="R4 App 命令接驳离线候选：隐含 --hearing-aid-l1-slotram，追加 keystore_hook 载荷"
                        "（App 自定义命令 → VM get/set_config key 30）并把命令注册表 cmdid 0x30 项的空"
                        " setter 字（0x060a9c7c，原值 0）填为载荷地址——写路径今日为 no-op，属纯增量；"
                        "候选 UNTESTED 不刷机")
    p.add_argument("--hearing-aid-l1-key7", action="store_true",
                   help="辅听 L1 双击多功能键 toggle 离线候选：隐含 --hearing-aid-l1-slotram，"
                        "把 cmd 7（双击）分支体入口 0x06025404 接驳为 HA toggle（读槽→开/关），"
                        "开辅听前先请求关 ANC（db3c(1,1)）；ANC 键保持原 OFF/ON/TRANS 语义"
                        "（不接驳 TRANS=HA，见 docs/hearing-aid-l1-key7.md）；候选 UNTESTED 不刷机")
    p.add_argument("--hearing-aid-l3-mpo", action="store_true",
                   help="助听 L3 MPO 消费链离线候选：隐含 --r4-keyhook（BLE cmdid 0x30 写 VM key 30"
                        " + --hearing-aid-l1-slotram 全链），追加 apply_mpo 载荷并把双击 toggle 桩扩展为"
                        " open 成功后读 key 30（版本化 8B：魔数 HA+版本+float MPO）→ L2 水平尾段曲线 →"
                        " jlstream_set_node_param(0xdefe,\"Drc1Eff\",76B) 热更（wdrc ioctl 0x06078d0a"
                        " 0x2000F 分支整包复制+updata，下一音频块生效）；与 --hearing-aid-l1-key7"
                        " 互斥（同一接驳窗）；见 docs/hearing-aid-l3-mpo.md；候选 UNTESTED 不刷机")
    p.add_argument("--hearing-aid-l4-eq", action="store_true",
                   help="助听 L4 EQ 十段热更离线候选：隐含 --hearing-aid-l3-mpo 全链（keyhook 扩展"
                        " key 31 分支），追加 apply_eq 载荷——open 成功后读 key 31（版本化 88B：魔数 HA+版本"
                        "+段数+10 段 {u16 freq, float32 gain}）→ 整包先验（freq 1..21599、|gain|≤40dB）→"
                        " 10 条 EQ_SEG 44B 段包 jlstream_set_node_param(0x737b,\"Eq1Eff\",包,44) 热更"
                        "（eq ioctl 0x0607898e subcmd 7 分支，每条要求返回 1 否则停止）；只发 Eq1Eff"
                        "（mic_effect 主链 MPO 上游输入 EQ；Eq4Eff/Eq5Eff 在输出支路不动，避免限幅后"
                        "重复整形）；候选 UNTESTED 不刷机，见 docs/hearing-aid-l4-eq.md")
    p.add_argument("--hearing-aid-full", action="store_true",
                   help="助听 L1+L2+L3+L4 全功能合一离线候选（路 HAL5）：隐含 --hearing-aid-l4-eq 全链"
                        "（代码字节与该候选完全一致——L4 的双击桩已按 open成功→apply_mpo→apply_eq 链成），"
                        "并要求显式 --hearing-aid-mpo M（0.3<M<120，与 key30 消费范围一致）把 L2 水平尾段"
                        " MPO 曲线烘进 stream.bin 作为无 key 时的兜底；固件字节 == --hearing-aid-l4-eq"
                        " --hearing-aid-mpo M 组合构建（零新增载荷）；与 --hearing-aid-demo /"
                        " --hearing-aid-l1-key7 互斥；候选 UNTESTED 不刷机，见 docs/hearing-aid-l5-full.md")
    p.add_argument("--hearing-aid-lfix", action="store_true",
                   help="辅听 lfix 修复候选 F1（路 LFIX-IMPL，基座=HAL5 合一全链）：open/close 载荷替换为"
                        "修复版——A1 open 前 ANC 退出等待（b[[QCY+500]+29]==0 且 [+33]==1 放行，"
                        "0x6064efc(10ms) 轮询，上限 ~600ms，超时取消本次 open 槽保持 0）+ B1 删除"
                        " set_scene(stream,10)（实为 set_scene(PC_SPK)，官方 DHA 从不调用）+ B2 start 返回后"
                        "显式 SET_CHANNEL(1)（node_ioctl(stream,0x1100,0x20007,1)，arg=1 通道数量语义）；"
                        "要求显式 --hearing-aid-mpo M（同 full 契约）；默认叠加 A3 时钟压舱"
                        " clock_alloc(\\\"lfix_hold\\\",128MHz)（F1a 诊断档），--lfix-no-hold 出 F1b"
                        "（仅差 A3，双镜像 A/B 对照）；候选 UNTESTED 不刷机，见 docs/hearing-aid-lfix-impl.md")
    p.add_argument("--lfix-no-hold", action="store_true",
                   help="F1b 变体：去掉 A3 时钟压舱（alloc/配对 free/名串全不出现在镜像中），"
                        "与默认 F1a 仅差 A3——plan §4-C 双产物裁决（运行时开关需 key32 参数通道，"
                        "改造面大不划算）；两个产物一次刷机各验一轮即可做 A/B 对照")
    p.add_argument("--output-dir", type=Path, required=True)
    p.add_argument("--source", type=Path, default=ROOT / "firmware/QCY_H3S_7018F_1T2_release_note_V1.1.8_011.ufw")
    p.add_argument("--analysis-dir", type=Path, default=ROOT / "analysis/v1.1.8")
    args = p.parse_args()
    if args.eq_toggle_elf and not args.anc_cycle_two_state:
        p.error('--eq-toggle-elf requires --anc-cycle-two-state')
    if args.eq_code_fence_repair and not args.eq_toggle_elf:
        p.error('--eq-code-fence-repair requires --eq-toggle-elf')
    if args.hearing_aid_l1_close:
        args.hearing_aid_l1_patch = True      # close 是同一候选族的超集（路 X 建议）
    if args.hearing_aid_l1_slotram:
        args.hearing_aid_l1_close = True      # slotram 是 close 的进一步超集（路 CT）
        args.hearing_aid_l1_patch = True
    if args.r4_keyhook:
        args.hearing_aid_l1_slotram = True    # R4 接驳是 slotram 候选族的超集（路 AQ）
        args.hearing_aid_l1_close = True
        args.hearing_aid_l1_patch = True
    if args.hearing_aid_l1_key7:
        args.hearing_aid_l1_slotram = True    # L1KEY toggle 是 slotram 候选族的超集（路 L1KEY）
        args.hearing_aid_l1_close = True
        args.hearing_aid_l1_patch = True
    if args.hearing_aid_l3_mpo:
        if args.hearing_aid_l1_key7:
            p.error('--hearing-aid-l3-mpo and --hearing-aid-l1-key7 share the KEY7 graft window')
        args.r4_keyhook = True                # L3 MPO 消费链 = keyhook 写入 + toggle 消费（路 HAL3）
        args.hearing_aid_l1_slotram = True
        args.hearing_aid_l1_close = True
        args.hearing_aid_l1_patch = True
    if args.hearing_aid_l4_eq:
        if args.hearing_aid_l1_key7:
            p.error('--hearing-aid-l4-eq and --hearing-aid-l1-key7 share the KEY7 graft window')
        args.hearing_aid_l3_mpo = True        # L4 EQ = L3 MPO 全链 + EQ 段热更（路 HAL4）
        args.r4_keyhook = True
        args.hearing_aid_l1_slotram = True
        args.hearing_aid_l1_close = True
        args.hearing_aid_l1_patch = True
    if args.hearing_aid_full:
        # 路 HAL5（codex ~/tmp/codex/202611180021-hal5 + 复核 202611180041-hal5rev）：
        # full 只是组合/命名/审计层，固件字节必须等于 --hearing-aid-l4-eq
        # --hearing-aid-mpo M（零新增载荷）。校验顺序 demo→缺M→范围→key7；范围门
        # 先挡非有限/越界 double（f32 转换 1e100 会 OverflowError），再按 float32
        # 化后的值判 0.3f < M < 120.0f（0.300000001 会舍入到 0.3f，须在此拒绝）。
        if args.hearing_aid_demo:
            p.error('--hearing-aid-full and --hearing-aid-demo are mutually exclusive '
                    '(demo also edits the Eq1Eff/Eq4Eff/Eq5Eff shared blob on the '
                    'post-MPO output branches)')
        if args.hearing_aid_mpo is None:
            p.error('--hearing-aid-full requires an explicit --hearing-aid-mpo <dB> '
                    '(the baked fallback curve is part of the candidate contract)')
        _m = args.hearing_aid_mpo
        if not math.isfinite(_m) or not (0.3 < _m < 120.0):
            p.error('--hearing-aid-full requires 0.3 < --hearing-aid-mpo < 120 '
                    '(same consumption range as VM key 30)')
        if not (f32(0.3) < f32(_m) < f32(120.0)):
            p.error('--hearing-aid-full requires 0.3 < --hearing-aid-mpo < 120 after '
                    'float32 rounding (same consumption range as VM key 30)')
        if args.hearing_aid_l1_key7:
            p.error('--hearing-aid-full and --hearing-aid-l1-key7 share the KEY7 graft window')
        args.hearing_aid_l4_eq = True         # full = L4 全链 + L2 烘焙兜底（路 HAL5）
        args.hearing_aid_l3_mpo = True
        args.r4_keyhook = True
        args.hearing_aid_l1_slotram = True
        args.hearing_aid_l1_close = True
        args.hearing_aid_l1_patch = True
    if args.lfix_no_hold and not args.hearing_aid_lfix:
        p.error('--lfix-no-hold requires --hearing-aid-lfix')
    if args.hearing_aid_lfix:
        # 路 LFIX-IMPL：F1 = HAL5 合一基座 + open/close 载荷修复版（A1+B1+B2[+A3]）。
        # 校验契约与 full 相同（显式 M、范围门、demo/key7 互斥）；隐含全链同 full。
        if args.hearing_aid_demo:
            p.error('--hearing-aid-lfix and --hearing-aid-demo are mutually exclusive')
        if args.hearing_aid_mpo is None:
            p.error('--hearing-aid-lfix requires an explicit --hearing-aid-mpo <dB> '
                    '(same baked-fallback contract as --hearing-aid-full)')
        _m = args.hearing_aid_mpo
        if not math.isfinite(_m) or not (0.3 < _m < 120.0):
            p.error('--hearing-aid-lfix requires 0.3 < --hearing-aid-mpo < 120 '
                    '(same consumption range as VM key 30)')
        if not (f32(0.3) < f32(_m) < f32(120.0)):
            p.error('--hearing-aid-lfix requires 0.3 < --hearing-aid-mpo < 120 after '
                    'float32 rounding (same consumption range as VM key 30)')
        if args.hearing_aid_l1_key7:
            p.error('--hearing-aid-lfix and --hearing-aid-l1-key7 share the KEY7 graft window')
        args.hearing_aid_l4_eq = True         # F1 基座 = HAL5 全链 + 修复载荷
        args.hearing_aid_l3_mpo = True
        args.r4_keyhook = True
        args.hearing_aid_l1_slotram = True
        args.hearing_aid_l1_close = True
        args.hearing_aid_l1_patch = True
    if args.hearing_aid_l1_patch and args.eq_toggle_elf:
        p.error('--hearing-aid-l1-patch and --eq-toggle-elf append at the same flash offset')
    source = args.source.read_bytes()
    if hashlib.sha256(source).hexdigest() != SOURCE_SHA:
        raise ValueError("原包摘要不匹配")
    analysis = args.analysis_dir
    stream = bytearray((analysis / "unpacked/files/stream.bin").read_bytes())
    original_stream = bytes(stream)
    flow_identifier = stream_flow_identifier(original_stream)
    flash_plain = bytearray((analysis / "unpacked/decrypted.bin").read_bytes())
    original_flash_plain = bytes(flash_plain)
    # 已从本版 stream.bin 的节目录及节点记录定位，结构参考官方 volume_cfg。
    nodes = [("Vol_SysTone", 0x18f5, (0, 16, -45, 1, 0, 6, 0)),
             ("Vol_SysKTone", 0x1905, (0, 16, -60, 2, 0, 6, 64))]
    changes = []
    for name, pos, expected in nodes:
        values = struct.unpack_from("<BHiBihH", stream, pos)
        if values != expected or name.encode() not in stream:
            raise ValueError(f"节点布局与已核实版本不一致：{name}")
        before = list(values)
        after = before.copy()
        after[2] -= args.attenuation_db
        after[4] -= args.attenuation_db
        struct.pack_into("<BHiBihH", stream, pos, *after)
        table_before, table_after = [], []
        if values[3] == 2:
            table_before = list(struct.unpack_from("<16f", stream, pos + 16))
            if table_before[0] != -60.0 or table_before[-1] != 0.0:
                raise ValueError("自定义音量表不匹配")
            table_after = [v - args.attenuation_db for v in table_before]
            struct.pack_into("<16f", stream, pos + 16, *table_after)
        changes.append({"node": name, "stream_offset": pos, "before": before,
                        "after": after, "table_before": table_before, "table_after": table_after})
    hearing_aid = None
    if args.hearing_aid_demo or args.hearing_aid_mpo is not None:
        mpo = args.hearing_aid_mpo if args.hearing_aid_mpo is not None else 100.0
        if args.hearing_aid_demo:
            demo_changes, s5_base = apply_hearing_aid_demo(stream, mpo=mpo)
        else:
            demo_changes, s5_base = apply_hearing_aid_mpo_curve(stream, mpo)
        hearing_aid = {"mode": "demo+mpo" if args.hearing_aid_demo else "mpo",
                       "mpo_db": f32(mpo),
                       "mpo_semantics": "全带数字域稳态压缩上限（水平尾段多点曲线）；"
                                        "非每频独立限幅（单带 WDRC）、非 SPL 标定、"
                                        "attack=100/TWOPOINT 下不保证瞬时峰值不越限",
                       "changes": demo_changes,
                       "roundtrip": verify_hearing_aid_roundtrip(
                           stream, s5_base, mpo=mpo, eq_demo=args.hearing_aid_demo)}
    hearing_aid_full = None
    if args.hearing_aid_full:
        # 路 HAL5 合一语义报告（codex ~/tmp/codex/202611180021-hal5 §4）：由最终有效
        # 配置生成，不改任何载荷字节——full 候选的固件字节 == --hearing-aid-l4-eq
        # --hearing-aid-mpo M 组合构建。
        baked = f32(mpo)
        # 与 apply_hearing_aid_mpo_curve 同式：mpo 先 float32 化再加 10.3（双精度
        # 直加会与镜像实况漂移 ~7e-6，codex 复核 202611180041-hal5rev #3）
        x_tail = f32(110.3) if baked < f32(110.3) else f32(baked + 10.3)
        hearing_aid_full = {
            "mode": "l1_key7_toggle + l2_baked_mpo + l3_key30_mpo + l4_key31_eq",
            "features": {
                "l1": "双击多功能键 toggle（读槽 0x125574 → open 先 db3c(1,1) 请求关 ANC / close）",
                "l2": "stream.bin Drc1Eff 水平尾段 MPO 曲线（构建期烘焙，pipeline open 按文件配置装载）",
                "l3": "key 30（8B {HA,01,00,float32 M1}）→ open 成功后 apply_mpo 运行时覆盖 Drc1Eff",
                "l4": "key 31（88B 十段）→ open 成功后 apply_mpo 再 apply_eq 热更 Eq1Eff 十段",
            },
            "baked_mpo_db": baked,
            "baked_curve": [[f32(0.3), f32(0.3)], [baked, baked], [x_tail, baked]],
            "runtime_priority":
                "key30 运行时值 > L2 烘焙值：open 成功后 apply_mpo 读 key 30，值合法则"
                "以 (0.3,0.3)/(M1,M1)/(120,M1) 覆盖运行参数；key 30 缺失/格式拒/范围拒"
                "则跳过（返回 1），保持本次 open 从 stream.bin 装载的 L2 曲线。close→open"
                " 每次重读。M1 无 min(M, M1) 钳位——L2 的 M 是默认值不是硬上限，合法"
                " M1 可以大于 M。",
            "semantics_by_key30_state": [
                {"key30": "缺失/长度/格式/范围拒", "curve": "保持本次从文件装载的 L2 曲线 M"},
                {"key30": "合法 M1，运行时更新成功", "curve": "先装载 M，随后覆盖为 M1"},
                {"key30": "已开启时修改 key 30", "curve": "不立即重应用；下次 close→open 重读"},
                {"key30": "前次成功应用 M1，本次 key 30 已失效",
                 "curve": "本次重建先装载 M，跳过覆盖；不承诺保留前次 M1"},
                {"key30": "参数提交返回错误/等待不返回",
                 "curve": "不能仅凭 key 合法推断最终音频状态"},
            ],
            "semantics_note":
                "上表是设计语义（静态链路证据），完整消费链未在任何仿真/真机执行验证。",
            "non_atomic_caveats": [
                "open 成功 ≠ 首个音频块已用新参数：start 返回与第一块消费之间的窗口内"
                "用 L2 烘焙曲线（这正是 L2 兜底的价值）",
                "十段 EQ 非事务：段间可能跨音频块逐步生效（fade 0.1f 平滑），中途失败"
                "留下部分应用的曲线；key 31 值仍在，下次 open 重试全部十条",
                "MPO 失败不阻止 EQ：apply_mpo 返回后无条件 apply_eq；若 apply_mpo 在"
                "同步等待上阻塞则 EQ 也到不了",
                "ANC 互斥只有请求动作：db3c(1,1) 返回 ≠ anc_task 硬件退出完成；HA 开启"
                "期间外部再开 ANC 无『先关 HA』处理",
                "保存回执 ≠ 消费 ≠ 出声三个层级；0x0607538a 的同步等待在冻结单上下文"
                "会撞 EQPIPE 同族墙，真机标准调度下未验证",
            ],
            "tail_point_note":
                "L2 烘焙 th[2].in = 110.3（M≥110.3 时 M+10.3）；运行时模板固定 120.0f。"
                "末段斜率 0 下两者静态传递曲线等价，76B 参数包不逐字节相同。",
            "equivalent_build":
                f"--hearing-aid-l4-eq --hearing-aid-mpo {baked:.9g}",
            "status": "OFFLINE_UNTESTED",
        }
    hearing_aid_lfix = None
    if args.hearing_aid_lfix:
        # 路 LFIX-IMPL：F1 候选语义报告（docs/hearing-aid-lfix-impl.md；
        # 方案 docs/hearing-aid-lfix-plan.md §4，codex 裁决修订版已吸收）。
        hearing_aid_lfix = {
            "candidate": "F1b (A1+B1+B2, no clock hold)" if args.lfix_no_hold
                         else "F1a (A1+B1+B2+A3 clock hold, diagnostic tier)",
            "base": "HAL5 full chain (l1 toggle + l2 baked MPO + l3 key30 "
                    "+ l4 key31 + r4 keyhook); only the open/close payload "
                    "sources change vs the HAL5 candidate",
            "a1_anc_wait":
                "open 前轮询 b[[0x1265C0+500]+29]==0 且 [+33]==1（内部 OFF=1）；"
                "0x6064efc(10ms) 任务睡眠轮询，上限 60 次 ~600ms；超时取消本次"
                " open（返回 -22，槽保持 0，再按一次即重试）；ctx==0 跳过等待",
            "b1_set_scene10_removed":
                "删除 0x06074e64(stream,10)（set_scene PC_SPK，路 L1 误标 "
                "set_notify）；场景保持 set_scene(stream,16,0) 的 HEARING_AID",
            "b2_set_channel_after_start":
                "start 返回 0 后 node_ioctl(stream,0x1100,0x20007,1)——对协商"
                "结果最后覆盖；arg=1 是通道数量语义（非 17/18/20 模式枚举）；"
                "返回值不检查（协商默认 mono 时为无害重申）",
            "a3_clock_hold": None if args.lfix_no_hold else
                "open uuid 命中后 clock_alloc('lfix_hold',128MHz)，close 尾/"
                "open 失败尾 clock_free('lfix_hold')；Σ=base24+mic24+hold128 →"
                " DVFS 恒 160M 封顶，抑制运行期振荡；首次升频切换不可避免；"
                "长期续航/温升未测——短期诊断档，非最终产品方案",
            "a3_switch": ("双产物 A/B（plan §4-C）：运行时开关需 key32 参数通道"
                          "（keystore_hook+App 通道+测试面改造），判定双镜像更划算；"
                          "F1a(默认,含压舱) 与 F1b(--lfix-no-hold) 仅差 A3"),
            "honest_boundary": [
                "静态补丁：全部锚点反汇编复核，但 ANC 等待放行、SET_CHANNEL 覆盖、"
                "压舱抑制振荡均为设计语义，运行行为留给真机刷写后用户实测",
                "验收标准=open→运行→close→再 open 全程稳定 + 左耳无刺耳噪声；"
                "按三时间窗分开记录（open 当场/持续运行/close 后再 open）",
                "A3 判读边界：F1a 不掉线只判「高频保持相关」（C1 时钟扰动 vs C5 "
                "算力不足不分），归因需跨周期保持实验（plan §5⑦）",
            ],
            "plan": "docs/hearing-aid-lfix-plan.md",
            "impl_report": "docs/hearing-aid-lfix-impl.md",
            "status": "OFFLINE_UNTESTED",
        }
    if stream_flow_identifier(stream) != flow_identifier:
        raise ValueError("不应改变在线调音流程标识")
    # 内层 stream.bin 的 JLFS 目录头与原始数据先做交叉校验。
    ent_pos, data_pos = 0xe2a4c, 0xe2a6c
    header = flash_plain[ent_pos:ent_pos + 32]
    if jl_crc16(header[2:]) != struct.unpack_from("<H", header)[0]:
        raise ValueError("原始 JLFS 头校验失败")
    if flash_plain[data_pos:data_pos + len(stream)] != original_stream:
        raise ValueError("原始 flash 与 stream 不匹配")
    if jl_crc16(original_stream) != struct.unpack_from("<H", header, 2)[0]:
        raise ValueError("原始 JLFS 数据校验失败")
    flash_plain[data_pos:data_pos + len(stream)] = stream
    struct.pack_into("<H", flash_plain, ent_pos + 2, jl_crc16(stream))
    struct.pack_into("<H", flash_plain, ent_pos, jl_crc16(flash_plain[ent_pos + 2:ent_pos + 32]))
    anc_cycle = None
    if args.anc_cycle_two_state:
        # H3S's actual pi32v2 anc_next handler at 0x06018cc0 calls 0x06018b60.
        # That routine reads this three-byte table and stops at the FIRST matching
        # current mode. Replacing OFF with ON yields ON -> TRANS -> ON; a current
        # OFF state is absent and takes the default index 0 (ON) on the next press.
        # These are internal ANC modes: OFF=1, ON=2, TRANS=3, NOT QCY wire IDs.
        app_entry, app_data, app_size = 0x2040, 0x2120, 842654
        table_offset = 0x9e410
        app = bytes(flash_plain[app_data:app_data + app_size])
        if hashlib.sha256(app).hexdigest() != "18f3d573a5b1a15f488a6803ce4f828634e602015c85e92d205bbe62d7060041":
            raise ValueError("ANC 分析基于的 app.bin 摘要不匹配")
        if app[table_offset:table_offset + 3] != bytes([1, 3, 2]):
            raise ValueError("原始 ANC 循环表不匹配")
        if app[0x18a86:0x18a8c] != bytes.fromhex("c4ff10e50906"):
            raise ValueError("已逆向的 ANC 循环表加载指令不匹配")
        for pos in (app_entry, 0x2020):
            if jl_crc16(flash_plain[pos + 2:pos + 32]) != struct.unpack_from("<H", flash_plain, pos)[0]:
                raise ValueError("原始应用区 JLFS 头校验失败")
        if jl_crc16(app) != struct.unpack_from("<H", flash_plain, app_entry + 2)[0]:
            raise ValueError("原始 app.bin JLFS 数据校验失败")
        area_size = struct.unpack_from("<I", flash_plain, 0x2020 + 8)[0]
        if area_size != 842912 or jl_crc16(flash_plain[0x2040:0x2020 + area_size]) != struct.unpack_from("<H", flash_plain, 0x2022)[0]:
            raise ValueError("原始 app_area_head 数据校验失败")
        flash_plain[app_data + table_offset] = 2
        # Nested app.bin, enclosing app_area_head, then the overall boot-info below.
        struct.pack_into("<H", flash_plain, app_entry + 2, jl_crc16(flash_plain[app_data:app_data + app_size]))
        struct.pack_into("<H", flash_plain, app_entry, jl_crc16(flash_plain[app_entry + 2:app_entry + 32]))
        struct.pack_into("<H", flash_plain, 0x2022, jl_crc16(flash_plain[0x2040:0x2020 + area_size]))
        struct.pack_into("<H", flash_plain, 0x2020, jl_crc16(flash_plain[0x2022:0x2040]))
        anc_cycle = {"app_offset": table_offset, "runtime_address": "0x0609e510",
                     "flash_offset": app_data + table_offset,
                     "before": [1, 3, 2], "after": [2, 3, 2],
                     "internal_modes": {"off": 1, "on": 2, "transparent": 3},
                     "code_bytes_changed": 0, "table_bytes_changed": 1,
                     "direct_app_selection_modified": False,
                     "evidence": "analysis/anc-cycle-20261109/", "device_verified": False}
    eq_toggle = None
    if args.eq_toggle_elf:
        from eq_toggle_image import apply_eq_patch
        eq_toggle = apply_eq_patch(flash_plain, args.eq_toggle_elf,repair_code_fence=args.eq_code_fence_repair)
    hearing_aid_l1 = None
    if args.hearing_aid_l1_patch:
        from hearing_aid_l1_image import apply_l1_patch
        hearing_aid_l1 = apply_l1_patch(flash_plain,
                                        close_hooks=args.hearing_aid_l1_close,
                                        slot_ram=args.hearing_aid_l1_slotram,
                                        r4_keyhook=args.r4_keyhook,
                                        key7_toggle=args.hearing_aid_l1_key7,
                                        l3_mpo=args.hearing_aid_l3_mpo,
                                        l4_eq=args.hearing_aid_l4_eq,
                                        lfix=args.hearing_aid_lfix,
                                        lfix_hold=(args.hearing_aid_lfix
                                                   and not args.lfix_no_hold))
    result = bytearray(source)
    ufw_header = encode_header(source[:64])
    count = struct.unpack_from("<H", ufw_header, 8)[0]
    first = encode_header(source[64:144])
    kind, _, _, _, flash_pos, flash_size, _, _, name = struct.unpack("<HHHHIII44s16s", first)
    if kind != 0 or flash_size != len(flash_plain) or name.split(b"\0")[0] != b"flash.bin":
        raise ValueError("UFW flash 条目不匹配")
    # SFC 是位置相关 XOR：同一位置以原始密文字节叠加明文差分，无需重写其他区域。
    changed_offsets = []
    for offset, (old, new) in enumerate(zip(original_flash_plain, flash_plain)):
        if old != new:
            result[flash_pos + offset] ^= old ^ new
            changed_offsets.append(offset)
    extended = eq_toggle or hearing_aid_l1
    if extended:
        # Unpacked bytes beyond the old JLFS end are raw padding, not decoded
        # SFC plaintext. Encode the extended region explicitly, including its
        # partial original last cipher block, using the original app-area base.
        extended_end = extended['new_app_end']
        encrypted_app = bytearray(flash_plain[0x2020:extended_end])
        jl_sfc_cipher(encrypted_app, 0, len(encrypted_app), 0, 0x3496)
        result[flash_pos + 0x2020:flash_pos + extended_end] = encrypted_app
    # 双备份 boot-info 在 app JLFS 前另有一层总校验，使用 0xffff 独立编码，
    # 不属于上面从 0x2020 开始的 SFC 区。首次候选遗漏了这一层。
    boot_pos, app_pos = 0x2000, 0x2020
    boot = encode_header(source[flash_pos + boot_pos:flash_pos + app_pos])
    boot_hcrc, plain_crc, cipher_crc, reserved, app_size = struct.unpack_from("<HHHHI", boot)
    app_end = app_pos + app_size
    if (boot_hcrc, plain_crc, cipher_crc, reserved, app_size) != (0xb00c, 0xc83c, 0xbf6a, 0, 0xe3053):
        raise ValueError("原始双备份 boot-info 布局不匹配")
    if app_end > flash_size or jl_crc16(boot[2:]) != boot_hcrc:
        raise ValueError("原始双备份 boot-info 头 CRC 或边界无效")
    if jl_crc16(original_flash_plain[app_pos:app_end]) != plain_crc:
        raise ValueError("原始双备份明文总体 CRC 不匹配")
    if jl_crc16(source[flash_pos + app_pos:flash_pos + app_end]) != cipher_crc:
        raise ValueError("原始双备份密文总体 CRC 不匹配")
    if extended:
        app_end = extended['new_app_end']
        app_size = app_end - app_pos
        struct.pack_into('<I', boot, 8, app_size)
    new_plain_crc = jl_crc16(flash_plain[app_pos:app_end])
    new_cipher_crc = jl_crc16(result[flash_pos + app_pos:flash_pos + app_end])
    struct.pack_into("<HH", boot, 2, new_plain_crc, new_cipher_crc)
    struct.pack_into("<H", boot, 0, jl_crc16(boot[2:]))
    result[flash_pos + boot_pos:flash_pos + app_pos] = encode_header(boot)
    changed_offsets = [i for i in range(flash_size) if result[flash_pos + i] != source[flash_pos + i]]
    struct.pack_into("<H", first, 4, jl_crc16(result[flash_pos:flash_pos + flash_size]))
    result[64:144] = encode_header(first)
    struct.pack_into("<H", ufw_header, 2, jl_crc16(result[64:64 + count * 80]))
    struct.pack_into("<H", ufw_header, 0, jl_crc16(ufw_header[2:]))
    result[:64] = encode_header(ufw_header)
    if args.attenuation_db == 0 and not args.anc_cycle_two_state and not eq_toggle and \
            args.hearing_aid_mpo is None and not args.hearing_aid_demo \
            and not hearing_aid_l1 and result != source:
        raise ValueError("零改动重打包不一致")
    if hearing_aid is not None and not hearing_aid_l1:
        # L1 启用时由 verify_l1_image 做覆盖更广的全镜像精确差异断言（含本演示的
        # stream 明文差异），旧断言的期望集不包含 L1 的字节，此处让位。
        stream_changed_offsets = [i for i in range(len(original_stream))
                                  if stream[i] != original_stream[i]]
        diff_positions, allowed_positions = verify_hearing_aid_diff(
            result, source, flash_pos, data_pos, ent_pos, boot_pos, stream_changed_offsets)
        hearing_aid["byte_diff"] = {"diff_positions": diff_positions,
                                    "allowed_positions": allowed_positions,
                                    "diff_count": len(diff_positions)}
    if hearing_aid_l1:
        from hearing_aid_l1_image import verify_l1_image
        l1_diff, l1_expected = verify_l1_image(
            result, source, flash_pos, original_flash_plain, flash_plain, hearing_aid_l1)
        hearing_aid_l1["byte_diff"] = {"diff_positions": l1_diff,
                                       "expected_positions": l1_expected,
                                       "diff_count": len(l1_diff)}
    args.output_dir.mkdir(parents=True)
    suffix = "_anc_on_trans" if args.anc_cycle_two_state else ""
    if eq_toggle:
        suffix += "_eq_toggle_EXPERIMENTAL"
    if args.hearing_aid_lfix:
        # 路 LFIX-IMPL：F1a/F1b 单独命名（F1a=含 A3 压舱诊断档，F1b=仅差 A3 的
        # 对照镜像）。M 文本同 full 用 float32 化后的 .9g（可往返）。
        suffix += (f"_LFIX_F1{'B_NOHOLD' if args.lfix_no_hold else 'A_HOLD'}"
                   f"_MPO{f32(mpo):.9g}_OFFLINE")
    elif args.hearing_aid_full:
        # 路 HAL5：full 单独命名（隐含链的 L4 后缀不再叠加）。M 文本用 float32
        # 化后的 .9g（可往返；:g 的 6 位有效数字会把 119.99999 显示成 120，
        # 而该命令烘焙的是不同字节且 120 不满足 full 契约——codex 复核 #4）
        suffix += f"_HEARING_AID_FULL_MPO{f32(mpo):.9g}_OFFLINE"
    elif args.hearing_aid_demo:
        suffix += ("_HEARING_AID_DEMO" if args.hearing_aid_mpo is None
                   else f"_HEARING_AID_DEMO_MPO{mpo:g}")
    elif args.hearing_aid_mpo is not None:
        suffix += f"_HEARING_AID_MPO{mpo:g}"
    if hearing_aid_l1 and not args.hearing_aid_full and not args.hearing_aid_lfix:
        suffix += ("_HEARING_AID_L4_EQ_OFFLINE" if args.hearing_aid_l4_eq
                   else "_HEARING_AID_L3_MPO_OFFLINE" if args.hearing_aid_l3_mpo
                   else "_R4_KEYHOOK_OFFLINE" if args.r4_keyhook
                   else "_HEARING_AID_L1_KEY7_OFFLINE" if args.hearing_aid_l1_key7
                   else "_HEARING_AID_L1_SLOTRAM_OFFLINE" if args.hearing_aid_l1_slotram
                   else "_HEARING_AID_L1_CLOSE_OFFLINE" if args.hearing_aid_l1_close
                   else "_HEARING_AID_L1_OFFLINE")
    filename = f"H3S_0.1.1.8_tones_minus{args.attenuation_db}dB{suffix}_UNTESTED.ufw"
    if eq_toggle:
        filename = f"H3S_0.1.1.8_tones_minus{args.attenuation_db}dB_ANC_EQ_TOGGLE_EXPERIMENTAL.ufw"
        if args.eq_code_fence_repair:
            filename=filename.replace('_EXPERIMENTAL.ufw','_FENCE_REPAIR_OFFLINE_UNTESTED.ufw')
    path = args.output_dir / filename
    path.write_bytes(result)
    (args.output_dir / "stream.bin").write_bytes(stream)
    report = {"status": "offline_candidate_not_device_verified", "source_sha256": SOURCE_SHA,
              "output_sha256": hashlib.sha256(result).hexdigest(), "bytes": len(result),
              "attenuation_db": args.attenuation_db, "nodes": changes,
              "changed_flash_offsets": changed_offsets,
              "anc_cycle": anc_cycle,
              **({'eq_toggle': eq_toggle} if eq_toggle else {}),
              **({'hearing_aid_demo': hearing_aid} if hearing_aid else {}),
              **({'hearing_aid_l1': hearing_aid_l1} if hearing_aid_l1 else {}),
              **({'hearing_aid_full': hearing_aid_full} if hearing_aid_full else {}),
              **({'hearing_aid_lfix': hearing_aid_lfix} if hearing_aid_lfix else {}),
              "zero_change_identical": bytes(result) == source,
              "dual_bank_boot_info": {"flash_offset": boot_pos, "app_offset": app_pos,
                                      "app_bytes": app_size, "plain_crc_before": plain_crc,
                                      "plain_crc_after": new_plain_crc, "cipher_crc_before": cipher_crc,
                                      "cipher_crc_after": new_cipher_crc,
                                      "header_crc_after": struct.unpack_from("<H", boot)[0]},
              "stream_flow_identifier": {"value": flow_identifier, "hex": f"0x{flow_identifier:04X}",
                                         "preserved": True,
                                         "reference_sdk_role": "在线调音时比较工具流程图与 stream.bin 的一致性",
                                         "evidence": "docs/stream-volume.md",
                                         "qcy_exact_implementation_verified": False},
              "limitations": ["输出版本仍为 0.1.1.8；其他输入设备版本未获普遍兼容验证",
                              "新生成的配置不自动继承已发布固定摘要包的设备验证结论", "参数含义依据同芯片公开 SDK",
                              "参考 SDK 的第 3 节标识用于在线调音流程匹配；QCY 确切实现仍未验证"]}
    (args.output_dir / "build-report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
    print(path)
    print(report["output_sha256"])
    print(f"修改 {len(changed_offsets)} 个 flash 字节；候选包尚未刷写验证")


if __name__ == "__main__":
    main()
