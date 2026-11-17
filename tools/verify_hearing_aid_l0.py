#!/usr/bin/env python3
"""Independent byte-level + CRC audit for the hearing-aid L0/L2 candidates.

不 import builder（build_quiet_candidate.py），只复用 verify_ufw_integrity.py 的
stdlib CRC/解码助手，照 verify_eq_candidate.py 的「独立校验」口径。三步：

1. CRC 合法性：对官方镜像与候选镜像各自跑 verify()（UFW 外层 / flash 条目 /
   双备份 boot-info / JLFS 头与数据 CRC 全查）。
2. 字节级差异：候选 .ufw 与官方镜像逐字节比对，差异必须恰好落在
   「改动的 stream 参数字节 + 6 个 UFW 外层 CRC + 4 个 stream JLFS 头 CRC +
   6 个双备份 boot-info CRC」上（期望集由参数往返先确认、再逐字节精确比对，
   任何多余/缺失字节都直接抛错）。
3. 参数往返：把候选 stream.bin §5 按 node_cfg_format 记录（name_hash 引用）重新
   定位 Eq1Eff/Drc1Eff/VolEff 并读回。demo 模式（--mode demo）确认 1000Hz /
   +39.0dB；mpo 模式（--mode mpo）确认 EQ 未动。两种模式都确认 Drc1Eff 是
   水平尾段 MPO 曲线 (0.3,0.3)/(M,M)/(X,M)——末段斜率 0、外推不过 M（路 L2 修正：
   DRC 核心按末段直线外推、无末点钳位，只改 threshold[2].out 不构成上限），
   同时用官方镜像确认改前值 1070Hz / 0dB / 拐点 (0.3)/(90.3)/(110.3) / VolEff 不变。

诚实边界：本脚本验证的是「参数区改动 + CRC 更新」的字节级复现闭环，NOT 助听器可用，
也 NOT 瞬时峰值保护（attack=100/TWOPOINT 平滑下稳态上限成立，瞬态仍可能越限）。
辅听模式进入方式（L1 接驳，docs/hearing-aid-l1-payload.md）与声学标定（真机声压）均
不在本路范围；M 的 dB 是调音工具数字域刻度，不是 dB SPL（docs/hearing-aid-eq-
feasibility.md §4 / docs/eq-gain-limit.md §3）。
"""
import argparse
import hashlib
import json
from pathlib import Path
import struct

from verify_ufw_integrity import crc, decode, sfc, verify

ROOT = Path(__file__).resolve().parents[1]
SOURCE_SHA = "65cafc7f9a7545c5c30701d141c77db334e37065589e42c51306132c94c879af"

# 辅听链（pipeline-mic_effect）三个节点在 stream.bin §5 记录里的 name_hash（djb2 低
# 16 位）/uuid/subid，以及已核实的参数区 offset/size。证据：
# docs/hearing-aid-eq-feasibility.md §2/§7。本演示只改参数 blob、不改名字，故哈希与
# §5 记录 offset/size 应保持不变（eq_tool 168B / wdrc_struct 76B / volume_cfg 16B）。
NODES = {
    "Eq1Eff":  {"name_hash": 0xdabd, "uuid": 0x737b, "subid": 10,
                "offset": 0xeac, "size": 168},
    "Drc1Eff": {"name_hash": 0x63e0, "uuid": 0xdefe, "subid": 6,
                "offset": 0x1038, "size": 76},
    "VolEff":  {"name_hash": 0xb6c7, "uuid": 0x74e3, "subid": 21,
                "offset": 0x14d7, "size": 16},
}


def f32(v):
    """把 Python float 归到固件实际存储的 IEEE754 single 精确值。"""
    return struct.unpack("<f", struct.pack("<f", v))[0]


def load_image(path):
    """解出 .ufw 里的 flash 原始区、明文 app 区、stream.bin 及其 JLFS 条目绝对偏移。"""
    data = Path(path).read_bytes()
    header = decode(data[:64])
    hc, dc, size, count = struct.unpack_from("<HHIH", header)
    if size != len(data) or crc(header[2:]) != hc:
        raise ValueError("UFW 外层头无效: " + str(path))
    if crc(data[64:64 + count * 80]) != dc:
        raise ValueError("UFW 目录 CRC 无效: " + str(path))
    flash_pos = flash_len = None
    for i in range(count):
        row = decode(data[64 + i * 80:144 + i * 80])
        kind, _, check, _, offset, length, _, _, name = struct.unpack("<HHHHIII44s16s", row)
        name = name.split(b"\0")[0].decode("ascii")
        if name == "flash.bin":
            flash_pos, flash_len = offset, length
            break
    if flash_pos is None:
        raise ValueError("缺少 flash.bin 条目")
    flash_raw = data[flash_pos:flash_pos + flash_len]
    boot = decode(flash_raw[0x2000:0x2020])           # 双备份 boot-info，0xffff 头编码
    app_len = struct.unpack_from("<I", boot, 8)[0]
    app = sfc(flash_raw[0x2020:0x2020 + app_len])     # app 区，SFC origin=0
    ent_pos = data_pos = None
    stream = None
    p = 0
    while p + 32 <= len(app):
        h, d, _, block, flags, _, index, name = struct.unpack_from("<HHIIBBH16s", app, p)
        name = name.split(b"\0")[0].decode("ascii")
        if name == "stream.bin":
            ent_pos = 0x2020 + p
            data_pos = 0x2020 + p + 32
            stream = app[p + 32:p + block]
            break
        p += block
        if index:
            break
    if stream is None:
        raise ValueError("app 区缺 stream.bin")
    return data, flash_raw, app, stream, ent_pos, data_pos, flash_pos


def stream_s5(stream):
    """独立解析 stream.bin 第 5 节，返回 (s5_base, {(name_hash,cfg_index): record})。"""
    pos = 0
    s5_base = s5_size = None
    while pos + 4 <= len(stream):
        kind = stream[pos]
        size = ((stream[pos + 1] & 3) << 16) | int.from_bytes(stream[pos + 2:pos + 4], "little")
        if kind == 5:
            s5_base, s5_size = pos + 4, size
            break
        pos += 4 + size
    if s5_base is None:
        raise ValueError("stream 缺少第 5 节参数区")
    records = {}
    for rec_off in range(s5_base + 16, s5_base + s5_size - 16, 32):
        rec = stream[rec_off:rec_off + 32]
        if rec[0] not in (1, 2, 3):
            break
        records[(struct.unpack_from("<H", rec, 5)[0], rec[9])] = {
            "type": rec[0],
            "uuid": struct.unpack_from("<H", rec, 1)[0],
            "subid": rec[3],
            "name_hash": struct.unpack_from("<H", rec, 5)[0],
            "cfg_index": rec[9],
            "offset": struct.unpack_from("<I", rec, 10)[0],
            "size": struct.unpack_from("<H", rec, 14)[0],
        }
    return s5_base, records


def read_params(stream, s5_base, records, stage):
    """按 §5 记录定位三节点并读回关键字段（stage 仅用于报错语境）。"""
    out = {}
    for name, spec in NODES.items():
        rec = records.get((spec["name_hash"], 0))
        if rec is None or rec["uuid"] != spec["uuid"] or rec["subid"] != spec["subid"]:
            raise ValueError(f"§5 未定位到 {name}")
        if (rec["offset"], rec["size"]) != (spec["offset"], spec["size"]):
            raise ValueError(f"{name} 参数区 offset/size 与已核实版本不一致")
        base = s5_base + rec["offset"]
        if name == "Eq1Eff":
            seg_num = struct.unpack_from(">H", stream, base + 4)[0]
            if seg_num != 10 or rec["size"] != 8 + 16 * seg_num:
                raise ValueError("Eq1Eff 段数/尺寸不一致")
            freq = struct.unpack_from("<i", stream, base + 8 + 5 * 16 + 4)[0]
            gain = struct.unpack_from("<f", stream, base + 8 + 5 * 16 + 8)[0]
            out[name] = {"seg_num": seg_num, "seg[5].freq": freq, "seg[5].gain": gain}
        elif name == "Drc1Eff":
            bypass = struct.unpack_from("<i", stream, base)[0]
            attack, release = struct.unpack_from("<HH", stream, base + 4)
            tnum = stream[base + 68]
            knees = [struct.unpack_from("<ff", stream, base + 8 + i * 8) for i in range(3)]
            out[name] = {"bypass": bypass, "attack": attack, "release": release,
                         "threshold_num": tnum, "knees": knees}
        else:  # VolEff
            out[name] = list(struct.unpack_from("<BHiBihH", stream, base))
    return out


def check_mpo_curve(knees, tnum, mpo):
    """独立复核水平尾段 MPO 语义（与 builder 的 verify_mpo_curve 同规则、独立实现口径）。

    斜率 k[i]=(out[i+1]-out[i])/(in[i+1]-in[i])；内部边界=pts[1..tnum-2].in；检测电平
    超末拐点沿用末段直线外推（drc.o DrcInit/DrcGainProcess，路 L2/codex 复核钉死）。
    要稳态上限=M：in 严格递增、末段斜率=0、截距=M、全曲线（含过驱动外推）≤M。
    """
    pts = knees[:tnum]
    ins = [p[0] for p in pts]
    if any(b <= a for a, b in zip(ins, ins[1:])):
        raise ValueError(f"拐点 in 电平非严格递增：{ins}")
    slopes = [(pts[i + 1][1] - pts[i][1]) / (pts[i + 1][0] - pts[i][0])
              for i in range(len(pts) - 1)]
    if slopes[-1] != 0.0:
        raise ValueError(f"末段斜率非 0（{slopes[-1]}）：过驱动输入会突破 MPO {mpo}")
    for probe in (ins[0], mpo / 2, mpo, ins[-1], ins[-1] + 30.0, ins[-1] + 300.0):
        seg = sum(1 for i in range(1, len(pts) - 1) if probe > pts[i][0])
        b = pts[seg + 1][1] - slopes[seg] * pts[seg + 1][0]
        if slopes[seg] * probe + b > mpo + 1e-4:
            raise ValueError(f"曲线在 {probe} dB 处突破 MPO {mpo}")
    return slopes


def audit(source_path, candidate_path, mpo=100.0, mode="demo"):
    for path in (source_path, candidate_path):
        if not verify(path)["passed"]:
            raise ValueError("独立 CRC 校验失败: " + str(path))
    src = load_image(source_path)
    cand = load_image(candidate_path)
    if hashlib.sha256(src[0]).hexdigest() != SOURCE_SHA:
        raise ValueError("官方镜像 SHA 不匹配")

    # 参数往返：官方镜像改前值 + 候选镜像改后值。
    src_s5, src_records = stream_s5(src[3])
    cand_s5, cand_records = stream_s5(cand[3])
    before = read_params(src[3], src_s5, src_records, "before")
    after = read_params(cand[3], cand_s5, cand_records, "after")
    if (before["Eq1Eff"]["seg[5].freq"], before["Eq1Eff"]["seg[5].gain"]) != (1070, 0.0):
        raise ValueError("官方 Eq1Eff 改前值不符")
    if before["Drc1Eff"]["knees"] != [(f32(0.3), f32(0.3)), (f32(90.3), f32(90.3)),
                                      (f32(110.3), f32(110.3))]:
        raise ValueError("官方 Drc1Eff 改前拐点不符")
    if mode == "demo":
        if (after["Eq1Eff"]["seg[5].freq"], after["Eq1Eff"]["seg[5].gain"]) != (1000, f32(39.0)):
            raise ValueError("候选 Eq1Eff 改后值不符")
    else:  # mpo：纯限幅候选，EQ 必须保持官方值
        if (after["Eq1Eff"]["seg[5].freq"], after["Eq1Eff"]["seg[5].gain"]) != (1070, 0.0):
            raise ValueError("纯 MPO 候选不应改动 Eq1Eff")
    mpo = f32(mpo)
    x_tail = f32(110.3) if mpo < f32(110.3) else f32(mpo + 10.3)
    expected_knees = [(f32(0.3), f32(0.3)), (mpo, mpo), (x_tail, mpo)]
    drc_after = after["Drc1Eff"]
    if drc_after["knees"] != expected_knees or drc_after["threshold_num"] != 3:
        raise ValueError(f"候选 Drc1Eff 改后拐点不符：{drc_after['knees']} != {expected_knees}")
    if (drc_after["bypass"], drc_after["attack"], drc_after["release"]) != (0, 100, 200):
        raise ValueError("候选 Drc1Eff attack/release/bypass 应保持原值")
    slopes = check_mpo_curve(drc_after["knees"], drc_after["threshold_num"], mpo)
    if before["VolEff"] != after["VolEff"] or tuple(after["VolEff"]) != (0, 16, -45, 1, 0, 5, 0):
        raise ValueError("VolEff 应保持不变")

    # 字节级差异：只允许「参数字节 + 三层 CRC」。
    ent_pos = cand[4]
    data_pos = cand[5]
    flash_pos = cand[6]
    stream_changed = [i for i in range(len(src[3]))
                      if src[3][i] != cand[3][i]]
    expected = {0, 1, 2, 3, 68, 69}                        # UFW 外层头 CRC + flash 条目 CRC
    expected |= {flash_pos + ent_pos + k for k in range(4)}   # stream JLFS 头 CRC（2+2）
    expected |= {flash_pos + 0x2000 + k for k in range(6)}    # 双备份 boot-info 明文/密文/头 CRC
    expected |= {flash_pos + data_pos + i for i in stream_changed}
    diff = {i for i in range(len(src[0])) if src[0][i] != cand[0][i]}
    if diff != expected:
        missing = sorted(expected - diff)
        unexpected = sorted(diff - expected)
        raise ValueError(f"字节差异与预期不符：missing={missing[:24]} unexpected={unexpected[:24]}")

    return {
        "status": "offline audit passed; NOT hardware validation",
        "mode": mode,
        "mpo_db": mpo,
        "source_sha256": hashlib.sha256(src[0]).hexdigest(),
        "candidate_sha256": hashlib.sha256(cand[0]).hexdigest(),
        "bytes": len(cand[0]),
        "ufw_diff_count": len(diff),
        "flash_diff_count": len(diff) - 6,
        "stream_param_diff_count": len(stream_changed),
        "stream_jlfs_entry_offset": hex(ent_pos),
        "stream_jlfs_data_offset": hex(data_pos),
        "before": before,
        "after": after,
        "mpo_curve_slopes": slopes,
        "crc_valid": True,
        "device_verified": False,
        "honest_boundary": (
            "全带数字域稳态压缩上限（水平尾段曲线，非每频独立限幅、非 dB SPL、"
            "attack/TWOPOINT 平滑下不保证瞬时峰值不越限）；"
            "辅听模式进入方式（L1 接驳）与真机声压标定不在本路范围"),
    }


if __name__ == "__main__":
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--source", type=Path,
                   default=ROOT / "firmware/QCY_H3S_7018F_1T2_release_note_V1.1.8_011.ufw")
    p.add_argument("--candidate", type=Path, required=True)
    p.add_argument("--mode", choices=["demo", "mpo"], default="demo",
                   help="demo=1kHz+39dB+MPO 曲线；mpo=纯限幅（EQ 不动）")
    p.add_argument("--mpo", type=float, default=100.0,
                   help="期望的 MPO 数字域上限 dB（默认 100）")
    p.add_argument("--report", type=Path)
    args = p.parse_args()
    result = audit(args.source, args.candidate, mpo=args.mpo, mode=args.mode)
    text = json.dumps(result, ensure_ascii=False, indent=2) + "\n"
    if args.report:
        args.report.write_text(text)
    print(text, end="")
