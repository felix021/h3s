# H3S project guide for coding agents

## Purpose and current result

This repository documents and reproduces a QCY H3S / JL AC701N firmware modification. The tested image makes the physical ANC mode key alternate ANC ON and transparency, excluding normal/OFF from the key cycle. The user confirmed the result on 2026-11-09. The image remains version 0.1.1.8 (280). Its tone configuration is reduced by 10 dB, but the user could not clearly hear a reduction; do not report a measured or confirmed 10 dB acoustic improvement.

Read README.md, docs/validation.md, docs/anc-cycle.md and docs/reproduction.md first. `firmware/manifest.json` is the authoritative artifact manifest. Firmware binaries are GitHub Release assets under tag `firmware`, not Git blobs. Historical `offline` / `UNTESTED` fields in archived build reports describe their creation stage; the subsequent device result is separate evidence.

## Exact artifact identities

- Official source: 1326848 bytes; SHA256 `65cafc7f9a7545c5c30701d141c77db334e37065589e42c51306132c94c879af`.
- Tested ANC image: same length; SHA256 `b79cb3a6f8702eb9e1d12eaf181a968f6b5848a66c051b2c792c39aa8b42fa1b`.
- No original 0.1.1.9 image or full device flash dump was obtained. Do not call the 0.1.1.8 image a proven restore backup for 0.1.1.9. A double-backup flag is not a recovery procedure.

## Repository map

- `tools/build_quiet_candidate.py`: fixed-source patching, ANC table and tone parameters, all known CRC updates.
- `tools/verify_ufw_integrity.py`: independent stdlib decoding and CRC verification; never import the builder here.
- `tools/extract_ufw_resources.py`, `tools/unpack.py`: original firmware extraction and outer resource checks.
- `tools/disassemble_app.py`, `tools/analyze_anc_cycle.py`: pi32v2 ELF wrapper, vendor disassembly and state reconstruction.
- `tools/android-query/`: Android query/OTA client, bounded transport and fixed-candidate approval gate.
- `evidence/`: curated analysis excerpts and sanitized verification summaries; no raw device session logs.
- `tools/jl-misctools/`: upstream MIT submodule, pinned to `0a5b12db0ef38f3042acffbe2452730a37fd2405`.

## Technical invariants

1. QCY wire IDs are ON=1, OFF=2, TRANS=3. Internal ANC IDs are OFF=1, ON=2, TRANS=3. Never mix them with the generic SDK 0/1/2 enumeration.
2. The actual key routine at 0x06018b60 uses first-match semantics. The table at app.bin+0x9e410 / runtime0x0609e510 changes 01 03 02 to 02 03 02. Do not patch every occurrence of those bytes.
3. Preserve firmware length, unrelated resources, code instructions and source SHA guards. Update child app.bin JLFS CRCs, app_area_head CRCs, independent boot-info plaintext/ciphertext/header CRCs and outer UFW CRCs in order. The 32-byte boot-info at flash+0x2000 is encoded independently of the SFC application region.
4. Preserve stream section3 identifier0x6c94. The reference SDK reads it as an online tuning flow identifier; it is not evidence that the exact QCY runtime uses all modified volume parameters.
5. pi32v2 call/jump operands are relative to the END of the instruction. Use the vendor toolchain or a verified processor module; do not disassemble as ARM or generic RISC-V.
6. ATT MTU247 means GATT payload244. Wait for every write callback; the tested service also has a 10ms inter-chunk minimum. A 522-byte SDK frame uses three GATT chunks. Larger-MTU success does not establish the sole cause of historical0x8e.

## Workflow and validation

Analyze locally by default. Use a configured build host only for Android builds or tools requiring its architecture; no hostnames, accounts or absolute workstation paths belong in scripts. See docs/reproduction.md for exact commands.

For firmware changes, run the independent integrity verifier and `tools/check_reproduction.py`. Zero attenuation without ANC modification must reproduce the official image byte-for-byte. The documented -10 dB + ANC settings must reproduce the tested digest. Validate the exact diff, not only the CRCs. Keep new candidates distinct from the validated release; never silently replace an existing tag or release asset with different bytes.

For Android changes, run `tools/android-query/test.sh`; build with Android35/build-tools35.0.0 and an explicit `H3S_BLE_ADDRESS`. The generated DeviceConfig and signing key stay under ignored build/. Do not commit a real device address. Maintain strict target/file/version/nonce/fresh-read gates and do not add arbitrary packet write entry points.

## Device actions and publication

Offline analysis, documentation, building or publishing does not authorize a new flash. Only perform device writes when the user's task includes them. Preserve authorization already given in the active conversation; do not invent repeated approval steps. No automatic retries, recovery, version bump or firmware fallback. Existing PROBE queries are separate from private per-session flash confirmation.

Never publish raw JSONL/HCI logs, target-info auth keys, account tokens, device addresses, private signing keys, host SSH details, personal application data, full QCY APK/decompiled trees or cached proprietary toolchains. Firmware-derived SFC key0x3496 is part of the public image format and is not the private per-device auth key. Summarize evidence with its limits: bytes sent are not flash readback, unchanged version is not proof of the new behavior, and user feedback is not an automatic cycle measurement.
