# H3S project guide for coding agents

## Purpose and current result

This repository documents and reproduces a QCY H3S / JL AC701N firmware modification. The tested image makes the physical ANC mode key alternate ANC ON and transparency, excluding normal/OFF from the key cycle. The user confirmed the result on 2026-11-09. The image remains version 0.1.1.8 (280). Its tone configuration is reduced by 10 dB, but the user could not clearly hear a reduction; do not report a measured or confirmed 10 dB acoustic improvement.

Read README.md, docs/validation.md, docs/anc-cycle.md and docs/reproduction.md first. `firmware/manifest.json` is the authoritative artifact manifest. Firmware binaries are GitHub Release assets under tag `firmware`, not Git blobs. Historical `offline` / `UNTESTED` fields in archived build reports describe their creation stage; the subsequent device result is separate evidence.

An EQ-toggle experiment was flashed once on 2026-11-10 after explicit user authorization. **DO NOT FLASH r2 AGAIN:** SHA256 `c8dae5233678102aac152f0ff94952edea83c6eeca6afaf216db43226d0b6696` passed device E6 verification but failed post-update operation: no power-off prompt, manual power-on followed by blinking LED, no normal pairing, BLE connection timeout147, and volume-button reset did not show the expected response. The user reports normal power-off also fails. Seeing the original BLE advertisement does not prove a healthy application. See docs/eq-toggle.md; the offline evidence file describes the BUILD stage only. No EQ behavior has been validated.

**Current device state:** on 2026-11-10 USB restoration rewrote only six bank-B sectors and verified a full 2MiB readback. Both applications equal the tested b79 image, with boot CRCs valid. Bank A, current VM, pairing/resources/calibration were retained. Bluetooth and playback work again. After briefly reporting an unresponsive ANC key, the user explicitly confirmed that the key switches normally and the QCY App controls modes again. Recovery is accepted; no further device writes are needed. The user clarified that normal key/App operation returned after manually restarting the headset once. Include one manual headset restart after unplugging USB in the documented closeout procedure; the underlying mechanism was not determined. Do not retry the failed EQ image or claim EQ was implemented. See docs/usb-recovery.md and evidence/usb-recovery.json. The phone still uses the previously tested0.4/code6 client; public source version is separate.

The user explicitly requested recording the USB rescue workflow and tools in this repository. The public Linux usbfs tool is profile-limited to this exact rejected r2 → b79 restoration. Never use the 942080B UFW flash.bin as a full-chip image, alter bank A/VM/calibration, relax profile guards for another failure, or silently reuse an attempted write journal. New writes require session authorization; the current restoration was already authorized and performed. USB readback success is separate from button behavior.

## Exact artifact identities

On 2026-11-10 the CPU ROM address window `0x000000..0x020000` (end exclusive, 128KiB) was acquired twice, byte-identical, SHA256 `7cae0a02e3ab3755c1e392f03c89222dd701065ec9bd3ec8ac109a7b3f40f793`. All162 named SDK export stubs matched, including `mask_init`0x1fde8→0x4590 and `_pll_init`0x1fe14→0xc2ce. This is an acquired address window, not proven full silicon ROM extent. Raw ROM stays private; do not publish it or alter the firmware Release. No Flash erase/program/OTP commands were used; no full Flash comparison was made this session. User reported exactly “耳机启动后正常”; do not inflate that into separate playback/key tests. See docs/rom-read.md and evidence/rom-read.json. Historical simulation reports predate this acquisition; ROM is not yet mapped into the simulator and hardware/IRQ models remain missing. No further hardware action is needed to close out the successful read.

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
- `tools/usb-recovery/read_rom.py`: separate fixed-window FD07 RAM-loader reader, max128B, twice-read equality, no Flash/OTP/reset commands. Requires fresh normal QCY USB, refuses existing ambiguous ROM/loader identity. Public refactor has only offline tests, not another live run. Do not reconnect hardware merely to test it. `validate_rom.py` checks two private images and pinned SDK export jumps offline; validated against actual captured images.
- `tools/probe_pi32v2.py`: pinned community pi32v2 instruction simulator plus explicit H3S-derived execution fence. Historical r2 branch regression runs without a private ELF; optional exact archived ELF adds 15 actual compiled function cases with explicit API stubs. See docs/preflash-emulation.md. This is not full H3S boot or a general candidate approval tool; diagnostic fence override never permits flashing r2.
- `tools/probe_boot.py`: exact OFFICIAL UFW application-entry exploration, five independent bounded scenarios. Known prefixes stop at upstream context gaps, PLL0 read, ROM `_pll_init`, or uninitialized handoff fields; no BSS/RTOS boot achieved. See docs/boot-exploration.md. SP/SSP and one full-width store have explicit local adapters; PLL/R0 assumptions are not measured device state. Unknown RAM does not read as zero.
- `tools/probe_boot_handoff.py`: typed BOOT_DEVICE_INFO replay plus optional bounded nonzero startup loops; under explicit PLL/device-sentinel/full-count assumptions, 16077 model steps initialize BSS/data/RAM-code with exact digests, stopping at zero-count REP. No RTOS/ROM boot. `boot_arg_list` is a DIFFERENT PLL object, not the real R0 handoff. Large REP counts are extrapolated from upstream AC791N counts1..7, not validated BR28 semantics. See docs/boot-handoff.md. Never publish full RAM/event mirrors.
- `tools/probe_boot_boundaries.py`: 13 strict/counterfactual/isolated profiles characterize zero-REP/ICFG/retained-RAM/CRC/control-write boundaries. Under explicit hypotheses, actual update-result code finishes and stops BEFORE writing0 to0xf2c11c; static next ROM call is mask_init0x1fde8, not executed. Isolated setup_arch stops BEFORE IPMASK0xffe1a8 write6 and is not contiguous boot. No ROM/API stub or unknown control-write latch. See docs/simulation-boundaries.md. Exploration has established current limits, not complete headset emulation.
- `tools/fetch_boot_reference.py`: optional pinned SDK header/archive download and local LLVM IR extraction to ignored directories. The SDK is reference material, not exact QCY source or a runnable hardware emulator.
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

**User preference: run checks locally, not on GitHub Actions.** Do not add/enable Actions workflows, dispatch runs or use GitHub-hosted CI. `python3 tools/check_local.py` runs the default local host/simulation checks; use `--fetch-inputs` for first-time pinned downloads, `--reproduce` for firmware reproduction, or `--android-build` for a local SDK35 build when appropriate. Publishing repository changes does not require a GitHub CI run.

For firmware changes, run the independent integrity verifier and `tools/check_reproduction.py`. Zero attenuation without ANC modification must reproduce the official image byte-for-byte. The documented -10 dB + ANC settings must reproduce the tested digest. Validate the exact diff, not only the CRCs. Keep new candidates distinct from the validated release; never silently replace an existing tag or release asset with different bytes.

For USB recovery code, run `python3 tools/usb-recovery/test_recovery.py`; compare offline planning to preserved private session data when available, without publishing those dumps.

For ROM-reader changes also run `python3 tools/usb-recovery/test_rom.py` (included in check_local.py). Keep FD07 separate from Flash FD05, fixed window and pinned-loader phase gates. Actual 512B memory reads shut down USB at both0 and0x4000;128B then read the whole window successfully, including address0. Do not call address0 unmapped. 256B was not live tested and exact maximum is unknown. Loader FB06 upload still uses512B. Avoid raw UBOOT1 RAM/SFR reads because some implementations encrypt the source in place. No automatic retries or speculative Flash writes.

For offline instruction-probe changes, use Python3.11+: `python3 tools/fetch_firmware.py --variant current`, then `python3 tools/probe_pi32v2.py --fetch-simulator`. Retain rejection of all five archived r2 branches and unknown memory/instructions; never replace unsupported semantics with a NOP. The pinned upstream targets AC791N, not a certified BR28 board. Keep its source clean, document the missing-ID-file adapter, and distinguish stubbed API results from real DSP/RTOS behaviour. Optional archived ELF diagnosis must preserve `archived_r2_verdict=REJECTED` and `suitable_for_flash_approval_alone=false`.

For boot-probe changes: download `--variant original`, then run `python3 tools/probe_boot.py --fetch-simulator --check-known-blockers`. Keep the first unmodified-upstream result, distinguish store-model bookkeeping reads from architectural loads, track RAM initialization, and stop on unknown MMIO/ROM/runtime instructions. Exit0 for a known blocked regression is not boot success. Do not infer real R0/PLL values from the diagnostic profiles or publish private Flash/RAM dumps.

Also run `python3 tools/probe_boot_handoff.py`: known-only fields must stop at unknown chip_id, sentinel A/B must preserve uninitialized neighbours and the same observed prefix PCs, and startup-memory digests must match before stopping at zero-count REP. Preserve old round1 report reproduction. Full-count REP is a documented research assumption; do not silently treat zero-count, privileged modes, IRQs or ROM as implemented.

Run `python3 tools/probe_boot_boundaries.py` for boundary changes. Preserve strict zero-REP and unknown-ICFG stops; skip-at-zero, partial REP chunks and RTS-at-end-of-IRQ-helper-REP are explicitly named hypotheses, not BR28 measurements. Actual QCY CRC must match stdlib; valid/invalid retained parameters must select different result codes and clear only80B, retaining unknown neighbours. Unknown control writes still fail. Never continue failed boot by silently entering setup_arch; isolated calls must be labelled. Optional pinned startup ELF matching proves only non-relocated byte agreement and reference labels, not exact SDK provenance or hardware semantics.

For Android changes, run `tools/android-query/test.sh`; build with Android35/build-tools35.0.0 and an explicit `H3S_BLE_ADDRESS`. The generated DeviceConfig and signing key stay under ignored build/. Do not commit a real device address. Maintain strict target/file/version/nonce/fresh-read gates and do not add arbitrary packet write entry points.

## Device actions and publication

Offline analysis, documentation, building or publishing does not authorize a new flash. Only perform device writes when the user's task includes them. Preserve authorization already given in the active conversation; do not invent repeated approval steps. No automatic retries, recovery, version bump or firmware fallback. Existing PROBE queries are separate from private per-session flash confirmation.

Never publish raw JSONL/HCI logs, target-info auth keys, account tokens, device addresses, private signing keys, host SSH details, personal application data, full QCY APK/decompiled trees or cached proprietary toolchains. Firmware-derived SFC key0x3496 is part of the public image format and is not the private per-device auth key. Summarize evidence with its limits: bytes sent are not flash readback, unchanged version is not proof of the new behavior, and user feedback is not an automatic cycle measurement.
