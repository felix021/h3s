#!/usr/bin/env python3
"""Generate fixed-source H3S tone/ANC images offline; never flashes a device.

The exact published ANC artifact was device-tested; newly generated settings
are offline candidates. See docs/validation.md for evidence and limitations.
Requires analysis/v1.1.8 from tools/unpack.py and the pinned jl-misctools submodule.
"""
import argparse
import hashlib
import json
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


def apply_hearing_aid_demo(stream):
    """离线把辅听链参数改成「1kHz +39dB + 输出限幅」演示值，原地改、尺寸不变。

    - Eq1Eff（eq_tool @0xeac，与 Eq4Eff/Eq5Eff 共享）：seg[5] 1070Hz/0dB → 1000Hz/+39dB。
    - Drc1Eff（wdrc_struct @0x1038）：threshold[2].out 110.3 → 100.0（数字域上限，非 SPL 标定）。
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
    # i32 freq, float gain, float q}}。段数字段为大端 u16（10 段=00 0a 00 00），
    # 其余字段小端；seg[i] 位于 +8+i*16。尺寸=8+16*seg_num 与记录 size 自洽。
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

    # wdrc_struct：{i32 bypass, u16 attack, u16 release,
    # threshold[6]{float in, float out}, float inputgain, float outputgain,
    # float rms_time, u8 threshold_num, ...}。threshold[2].out 在 +8+2*8+4。
    drc_off = s5_base + drc["offset"]
    bypass = struct.unpack_from("<i", stream, drc_off)[0]
    attack, release = struct.unpack_from("<HH", stream, drc_off + 4)
    tnum = stream[drc_off + 68]
    if (bypass, attack, release, tnum) != (0, 100, 200, 3):
        raise ValueError("Drc1Eff 初始参数与已核实版本不一致")
    out_off = drc_off + 28
    old_out = struct.unpack_from("<f", stream, out_off)[0]
    if old_out != f32(110.3):
        raise ValueError("Drc1Eff 初始限幅阈值与已核实版本不一致")
    struct.pack_into("<f", stream, out_off, 100.0)
    changes.append({"node": "Drc1Eff", "stream_off": out_off, "field": "threshold[2].out",
                    "before": old_out, "after": 100.0, "note": "数字域上限，非 SPL 标定"})
    changes.append({"node": "VolEff", "stream_off": s5_base + vol["offset"],
                    "field": "<BHiBihH>", "before": list(vol_values),
                    "after": list(vol_values), "unchanged": True,
                    "note": "输入侧主音量，本演示不改"})
    return changes, s5_base


def verify_hearing_aid_roundtrip(stream, s5_base):
    """把改后的参数区按 §1.2 结构重新解析，确认 1kHz/+39dB/out=100 如预期（往返一致）。"""
    _, records = stream_param_records(stream)
    by_key = {(r["name_hash"], r["cfg_index"]): r for r in records}
    eq = by_key[(HEARING_AID_NODES["Eq1Eff"]["name_hash"], 0)]
    drc = by_key[(HEARING_AID_NODES["Drc1Eff"]["name_hash"], 0)]
    eq_off = s5_base + eq["offset"]
    drc_off = s5_base + drc["offset"]
    freq = struct.unpack_from("<i", stream, eq_off + 8 + 5 * 16 + 4)[0]
    gain = struct.unpack_from("<f", stream, eq_off + 8 + 5 * 16 + 8)[0]
    out = struct.unpack_from("<f", stream, drc_off + 28)[0]
    if freq != 1000 or gain != f32(39.0) or out != f32(100.0):
        raise ValueError("改后参数区往返解析不一致："
                         f"freq={freq} gain={gain} out={out}")
    return {"freq": freq, "gain": gain, "out_threshold": out}


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
                   help="助听器弱化版 EQ L0 演示：辅听链 1kHz +39dB + 输出限幅；仅离线候选")
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
    if args.hearing_aid_demo:
        demo_changes, s5_base = apply_hearing_aid_demo(stream)
        hearing_aid = {"changes": demo_changes,
                       "roundtrip": verify_hearing_aid_roundtrip(stream, s5_base)}
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
                                        r4_keyhook=args.r4_keyhook)
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
            not args.hearing_aid_demo and not hearing_aid_l1 and result != source:
        raise ValueError("零改动重打包不一致")
    if args.hearing_aid_demo and not hearing_aid_l1:
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
    if args.hearing_aid_demo:
        suffix += "_HEARING_AID_DEMO"
    if hearing_aid_l1:
        suffix += ("_R4_KEYHOOK_OFFLINE" if args.r4_keyhook
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
