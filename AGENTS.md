# H3S project guide for coding agents

## Purpose and current result

This repository documents and reproduces a QCY H3S / JL AC701N firmware modification. The tested image makes the physical ANC mode key alternate ANC ON and transparency, excluding normal/OFF from the key cycle. The user confirmed the result on 2026-11-09. The image remains version 0.1.1.8 (280). Its tone configuration is reduced by 10 dB, but the user could not clearly hear a reduction; do not report a measured or confirmed 10 dB acoustic improvement.

Read README.md, docs/validation.md, docs/anc-cycle.md and docs/reproduction.md first. `firmware/manifest.json` is the authoritative artifact manifest. Firmware binaries are GitHub Release assets under tag `firmware`, not Git blobs. Historical `offline` / `UNTESTED` fields in archived build reports describe their creation stage; the subsequent device result is separate evidence.

An EQ-toggle experiment was flashed once on 2026-11-10 after explicit user authorization. **DO NOT FLASH r2 AGAIN:** SHA256 `c8dae5233678102aac152f0ff94952edea83c6eeca6afaf216db43226d0b6696` passed device E6 verification but failed post-update operation: no power-off prompt, manual power-on followed by blinking LED, no normal pairing, BLE connection timeout147, and volume-button reset did not show the expected response. The user reports normal power-off also fails. Seeing the original BLE advertisement does not prove a healthy application. See docs/eq-toggle.md; the offline evidence file describes the BUILD stage only. No EQ behavior has been validated.

**Current device state:** on 2026-11-10 USB restoration rewrote only six bank-B sectors and verified a full 2MiB readback. Both applications equal the tested b79 image, with boot CRCs valid. Bank A, current VM, pairing/resources/calibration were retained. Bluetooth and user-confirmed playback work again, but the user reports the mode key has no response; this regression remains unresolved. Do not claim all functions restored. Continue diagnosis without retrying the EQ image. See docs/usb-recovery.md and evidence/usb-recovery.json. The phone still uses the previously tested0.4/code6 client; public source version is separate.

The user explicitly requested recording the USB rescue workflow and tools in this repository. The public Linux usbfs tool is profile-limited to this exact rejected r2 → b79 restoration. Never use the 942080B UFW flash.bin as a full-chip image, alter bank A/VM/calibration, relax profile guards for another failure, or silently reuse an attempted write journal. New writes require session authorization; the current restoration was already authorized and performed. USB readback success is separate from button behavior.

## Exact artifact identities

- Official source: 1326848 bytes; SHA256 `65cafc7f9a7545c5c30701d141c77db334e37065589e42c51306132c94c879af`.
- Tested ANC image: same length; SHA256 `b79cb3a6f8702eb9e1d12eaf181a968f6b5848a66c051b2c792c39aa8b42fa1b`.
- No original 0.1.1.9 image was obtained. A full device dump was obtained during USB recovery on 2026-11-10 and is kept private; it is a later 0.1.1.8 device state. Do not call the 0.1.1.8 image a proven restore backup for 0.1.1.9. A double-backup flag is not a recovery procedure.

## Repository map

- `tools/build_quiet_candidate.py`: fixed-source patching, ANC table and tone parameters, all known CRC updates.
- `tools/verify_ufw_integrity.py`: independent stdlib decoding and CRC verification; never import the builder here.
- `tools/extract_ufw_resources.py`, `tools/unpack.py`: original firmware extraction and outer resource checks.
- `tools/disassemble_app.py`, `tools/analyze_anc_cycle.py`: pi32v2 ELF wrapper, vendor disassembly and state reconstruction.
- `tools/android-query/`: Android query/OTA client, bounded transport and fixed-candidate approval gate.
- `tools/usb-recovery/`: Linux/Android USB ROM entry, pinned RAM loader, double full backups, offline fixed-profile planning, six-sector restore and full readback. `plan` is offline; only `restore --apply` erases/programs Flash.
- `evidence/`: curated analysis excerpts and sanitized verification summaries; no raw device session logs.
- `tools/jl-misctools/`: upstream MIT submodule, pinned to `0a5b12db0ef38f3042acffbe2452730a37fd2405`.

## Technical invariants

1. QCY wire IDs are ON=1, OFF=2, TRANS=3. Internal ANC IDs are OFF=1, ON=2, TRANS=3. Never mix them with the generic SDK 0/1/2 enumeration.
2. The actual key routine at 0x06018b60 uses first-match semantics. The table at app.bin+0x9e410 / runtime0x0609e510 changes 01 03 02 to 02 03 02. Do not patch every occurrence of those bytes.
3. Preserve firmware length, unrelated resources, unrelated code instructions and source SHA guards. The ANC-only variant changes no instructions; the separate EQ experiment has five explicitly guarded six-byte sites. Update child app.bin JLFS CRCs, app_area_head CRCs, independent boot-info plaintext/ciphertext/header CRCs and outer UFW CRCs in order. The 32-byte boot-info at flash+0x2000 is encoded independently of the SFC application region.
4. Preserve stream section3 identifier0x6c94. The reference SDK reads it as an online tuning flow identifier; it is not evidence that the exact QCY runtime uses all modified volume parameters.
5. pi32v2 call/jump operands are relative to the END of the instruction. Use the vendor toolchain or a verified processor module; do not disassemble as ARM or generic RISC-V.
6. ATT MTU247 means GATT payload244. Wait for every write callback; the tested service also has a 10ms inter-chunk minimum. A 522-byte SDK frame uses three GATT chunks. Larger-MTU success does not establish the sole cause of historical0x8e.

## Workflow and validation

Analyze locally by default. Use a configured build host only for Android builds or tools requiring its architecture; no hostnames, accounts or absolute workstation paths belong in scripts. See docs/reproduction.md for exact commands.

For firmware changes, run the independent integrity verifier and `tools/check_reproduction.py`. Zero attenuation without ANC modification must reproduce the official image byte-for-byte. The documented -10 dB + ANC settings must reproduce the tested digest. Validate the exact diff, not only the CRCs. Keep new candidates distinct from the validated release; never silently replace an existing tag or release asset with different bytes.

For USB recovery code, run `python3 tools/usb-recovery/test_recovery.py`; compare offline planning to preserved private session data when available, without publishing those dumps.

For Android changes, run `tools/android-query/test.sh`; build with Android35/build-tools35.0.0 and an explicit `H3S_BLE_ADDRESS`. The generated DeviceConfig and signing key stay under ignored build/. Do not commit a real device address. Maintain strict target/file/version/nonce/fresh-read gates and do not add arbitrary packet write entry points.

## Device actions and publication

Offline analysis, documentation, building or publishing does not authorize a new flash. Only perform device writes when the user's task includes them. Preserve authorization already given in the active conversation; do not invent repeated approval steps. No automatic retries, recovery, version bump or firmware fallback. Existing PROBE queries are separate from private per-session flash confirmation.

Never publish raw JSONL/HCI logs, target-info auth keys, account tokens, device addresses, private signing keys, host SSH details, personal application data, full QCY APK/decompiled trees or cached proprietary toolchains. Firmware-derived SFC key0x3496 is part of the public image format and is not the private per-device auth key. Summarize evidence with its limits: bytes sent are not flash readback, unchanged version is not proof of the new behavior, and user feedback is not an automatic cycle measurement.
