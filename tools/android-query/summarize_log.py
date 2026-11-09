#!/usr/bin/env python3
"""Summarize a probe session, reconstructing raw RCSP frames independently of the SDK."""
import argparse
from collections import Counter
import json
from pathlib import Path


def frames(blob):
    while blob:
        if len(blob) < 8 or blob[:3] != b"\xfe\xdc\xba":
            raise ValueError("Incomplete or invalid RCSP header")
        size = int.from_bytes(blob[5:7], "big")
        end = size + 8
        if size < 1 or len(blob) < end or blob[end - 1] != 0xef:
            raise ValueError("Incomplete or invalid RCSP frame")
        yield {"opcode": blob[4], "command": bool(blob[3] & 0x80), "body": blob[7:end-1]}
        blob = blob[end:]


def summarize(path):
    rows = [json.loads(line) for line in path.read_text().splitlines()]
    tx = [f for row in rows if row["event"] == "tx_frame" for f in frames(bytes.fromhex(row["data"]))]
    rx = list(frames(b"".join(bytes.fromhex(row["data"]) for row in rows if row["event"] == "rx")))
    attrs = {}
    for frame in rx:
        if frame["opcode"] == 3 and not frame["command"]:
            body = frame["body"]
            if body[0] != 0:
                continue
            body = body[2:]  # status and SN
            while body:
                size = body[0]
                if size < 1 or size + 1 > len(body):
                    raise ValueError("Invalid target-info TLV")
                attrs[body[1]] = body[2:size+1]
                body = body[size+1:]
    info = next((row["data"] for row in reversed(rows) if row["event"] == "device_info"), {})
    safe_keys = ["getVersionCode", "getVersionName", "getPid", "getVid", "getUid", "getUbootVersionName", "getSdkType", "getProjectCode", "getLowPowerLimit", "isSupportDoubleBackup", "isNeedBootLoader", "isMandatoryUpgrade", "isSupportMD5"]
    return {
        "session": path.name,
        "device": {key: info[key] for key in safe_keys if key in info},
        "raw_target_attribute_types": [f"0x{key:02x}" for key in attrs],
        "raw_version_code": int.from_bytes(attrs[5], "big") if 5 in attrs else None,
        "raw_pid": int.from_bytes(attrs[10][2:4], "big") if 10 in attrs else None,
        "generic_quantity_attribute_present": 1 in attrs,
        "generic_quantity_raw": attrs[1][0] if 1 in attrs and attrs[1] else None,
        "generic_low_power_limit_present": 1 in attrs and len(attrs[1]) > 4,
        "raw_double_backup": attrs[8][0] == 1 if 8 in attrs and attrs[8] else None,
        "raw_need_bootloader": attrs[8][1] == 1 if 8 in attrs and len(attrs[8]) > 1 else None,
        "tx_opcode_counts": {f"0x{key:02x}": value for key, value in Counter(f["opcode"] for f in tx).items()},
        "upgrade_or_reboot_frames_sent": any(f["opcode"] in range(227, 233) for f in tx),
        "observations": [{"event": row["event"], "data": row["data"]} for row in rows if row["event"] in ("battery_response", "battery_slots", "file_flag", "eligibility", "file_check", "compatibility", "blocked_tx", "sdk_error", "file_error")],
        "limitations": ["E2 eligibility with offset=0/len=0 sends no firmware bytes and does not validate downgrade or a particular file", "Double-backup capability is not a tested recovery procedure"],
    }


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("log", type=Path)
    args = parser.parse_args()
    print(json.dumps(summarize(args.log), ensure_ascii=False, indent=2))
