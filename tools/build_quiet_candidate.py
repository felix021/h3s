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
from jltech.cipher import jl_enc_cipher
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


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--attenuation-db", type=int, default=10, choices=range(0, 31))
    p.add_argument("--anc-cycle-two-state", action="store_true",
                   help="将已逆向的按键轮转表由 OFF/TRANS/ON 改为 ON/TRANS/ON；App 直接选模式不变")
    p.add_argument("--output-dir", type=Path, required=True)
    p.add_argument("--source", type=Path, default=ROOT / "firmware/QCY_H3S_7018F_1T2_release_note_V1.1.8_011.ufw")
    p.add_argument("--analysis-dir", type=Path, default=ROOT / "analysis/v1.1.8")
    args = p.parse_args()
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
    if args.attenuation_db == 0 and not args.anc_cycle_two_state and result != source:
        raise ValueError("零改动重打包不一致")
    args.output_dir.mkdir(parents=True)
    suffix = "_anc_on_trans" if args.anc_cycle_two_state else ""
    path = args.output_dir / f"H3S_0.1.1.8_tones_minus{args.attenuation_db}dB{suffix}_UNTESTED.ufw"
    path.write_bytes(result)
    (args.output_dir / "stream.bin").write_bytes(stream)
    report = {"status": "offline_candidate_not_device_verified", "source_sha256": SOURCE_SHA,
              "output_sha256": hashlib.sha256(result).hexdigest(), "bytes": len(result),
              "attenuation_db": args.attenuation_db, "nodes": changes,
              "changed_flash_offsets": changed_offsets,
              "anc_cycle": anc_cycle,
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
