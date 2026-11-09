#!/usr/bin/env python3
"""提取已解包 H3S UFW 的外层资源及提示音，逐项校验 CRC；不生成刷机包。

先用 jl-misctools 的 fwunpack_newfw.py 解出 jlfw.yaml，再传 --unpacked。
依赖：crcmod、PyYAML，以及本目录的 jl-misctools。
"""
import argparse
import hashlib
import json
from pathlib import Path
import struct
import sys

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parent / "jl-misctools/firmware"))
from jltech.cipher import jl_enc_cipher, jl_sfc_cipher
from jltech.crc import jl_crc16


def safe_name(raw):
    name = raw.split(b"\0")[0].rstrip(b"\xff").decode("ascii")
    if not name or name in (".", "..") or "/" in name or "\\" in name:
        raise ValueError("无效资源文件名")
    return name


def extract_tones(data, output):
    output.mkdir()
    entries = []
    offset = 32
    while True:
        if offset + 32 > len(data):
            raise ValueError("提示音目录缺少结束项")
        hcrc, dcrc, start, size, flags, _, last, raw = struct.unpack_from("<HHIIBBH16s", data, offset)
        if jl_crc16(data[offset + 2:offset + 32]) != hcrc or flags != 2:
            raise ValueError("提示音目录头校验失败")
        name = safe_name(raw)
        content = data[start:start + size]
        if len(content) != size or jl_crc16(content) != dcrc:
            raise ValueError(f"提示音数据校验失败：{name}")
        (output / name).write_bytes(content)
        entries.append({"name": name, "offset": start, "size": size, "crc_ok": True})
        offset += 32
        if last:
            return entries


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("firmware", type=Path)
    parser.add_argument("--unpacked", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True, help="必须是不存在的新目录")
    args = parser.parse_args()
    info = yaml.safe_load((args.unpacked / "jlfw.yaml").read_text())
    key = info["chip-key"]
    blob = args.firmware.read_bytes()
    header = bytearray(blob[:64])
    jl_enc_cipher(header, 0, 64, 0xffff)
    hcrc, lcrc, size, count, _, _, chip = struct.unpack("<HHIHHI48s", header)
    if size != len(blob) or jl_crc16(header[2:]) != hcrc:
        raise ValueError("UFW 头校验失败")
    if jl_crc16(blob[64:64 + count * 80]) != lcrc:
        raise ValueError("UFW 目录校验失败")
    args.output.mkdir(parents=True)
    outer = args.output / "outer"
    tones = args.output / "tones"
    outer.mkdir()
    tones.mkdir()
    entries, tone_entries = [], []
    for index in range(count):
        raw = bytearray(blob[64 + index * 80:64 + (index + 1) * 80])
        jl_enc_cipher(raw, 0, 80, 0xffff)
        kind, _, crc, _, start, size, padded, _, raw_name = struct.unpack("<HHHHIII44s16s", raw)
        name = safe_name(raw_name)
        if size > padded or start + padded > len(blob):
            raise ValueError(f"UFW 数据越界：{name}")
        content = bytearray(blob[start:start + padded])
        encrypted = kind in (50, 52, 55, 56, 161, 251)
        if encrypted:
            # SFC 每 32 字节重置；密钥变化取决于原始 UFW 绝对偏移。
            jl_sfc_cipher(content, 0, len(content), -start, key)
        elif kind not in (0, 2, 100, 255):
            raise ValueError(f"尚未验证的 UFW 条目类型：{kind}")
        content = bytes(content[:size])
        if jl_crc16(content) != crc:
            raise ValueError(f"UFW 数据校验失败：{name}")
        (outer / name).write_bytes(content)
        entries.append({"index": index, "type": kind, "name": name, "offset": start,
                        "size": size, "padded_size": padded, "sfc_decrypted": encrypted,
                        "crc_ok": True, "sha256": hashlib.sha256(content).hexdigest()})
        if name in ("TON", "TON1"):
            files = extract_tones(content, tones / name)
            tone_entries.append({"partition": name, "files": files})
    (outer / "manifest.json").write_text(json.dumps(entries, indent=2) + "\n")
    (tones / "manifest.json").write_text(json.dumps(tone_entries, indent=2) + "\n")
    print(f"{safe_name(chip)}：{len(entries)} 个 UFW 条目、"
          f"{sum(len(e['files']) for e in tone_entries)} 个提示音分区文件，全部 CRC 通过")


if __name__ == "__main__":
    main()
