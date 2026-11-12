# P11 heap descriptor measurement via FD07: offline assessment

2026-11-12, route BA (offline research route). This page answers one question: **can the USB
debug-mode `FD07` memory-read command read the four heap-descriptor bytes at
`0xf27f4a..0xf27f4d` (P2M message area +0xa in the P11 RAM window)?** The whole route was
offline: ROM-window disassembly plus disassembly of the pinned public RAM-loader blob. No
device was connected, no USB command was run, and no `tools/usb-recovery/` code was changed.
The device session in this page is a **contingency plan, not an authorized or executed action**.

All ROM claims below are anchored to instruction addresses inside the digest-pinned 128 KiB
ROM window (the published `firmware` Release asset). Loader claims are anchored to addresses
in the pinned public loader blob (SHA-256 pinned in `tools/usb-recovery/h3s_usb.py`,
`LOADER_SHA`), which FB06 uploads at CPU address `0x120000`; loader addresses below are CPU
addresses `0x120000 + blob offset`.

## Measurement target

The main-scenario strict stop 289,849 is the first model read (`0x60679ba`) of the four bytes
`0xf27f4a..0xf27f4d`: `{u16 begin; u16 size}` of the P11 heap descriptor, in the P2M message
area at P11 RAM offset `+0x7f40+0xa`. The bytes are written by P11's own init code during
normal application boot; the boot simulation cannot produce them (P11 is a separate CPU that
the simulator does not execute).

A static derivation exists (route X, see HANDOFF): **begin=`0x3d0c`, size=`0x39f4`**, from
p11_code.bin link-time immediates, the SDK consumer macros and the
VAD_DVAD_CONFIG_BEGIN layout, cross-checked three ways. Two structural sanity facts support
the pair: the app protect-area registration `(3, 0xf20000, 0xf23d0b)` ends exactly one byte
before `0xf20000+0x3d0c`, and `0x3d0c+0x39f4 = 0x7700` ends below the message area at
`0x7f40`, both inside the SDK-declared 0x8000-byte P11 RAM.

Derived is not measured. Under the main-scenario evidence standard, these four bytes are the
only values whose upgrade from "derived" to "measured" requires reading them back from a real
device; every other stop in the 289,849 dependency chain has been closed by execution
evidence. The only candidate channel is the USB debug mode's `FD07` memory read, evaluated
below.

## Offline assessment

There are **two different FD07 implementations** on this chip, and the distinction matters:

1. the mask ROM's own handler, and
2. the RAM loader's handler. After FB06 uploads the loader and FB08 jumps to it, the loader
   owns USB itself — its dispatcher at `0x121cfe` is registered into its own USB driver
   structure (`0x120658`), and the blob contains **zero** references to the ROM hook slot
   `0x181428`. The historical reads (whole ROM window, 128B blocks) and both 512B failures
   all went through the **loader** implementation.

### ROM FD07 handler (0x499a..0x49f4)

Command dispatch: `0x4938` reads the hook pointer from `[0x181428]`; a nonzero hook is called
first (`0x4942`) and a nonzero result bypasses ROM handling (`0x4944`). Without a hook, the
opcode byte at `[0x1813f8+15]` selects the family (`0x4948`): `0xfd` → FD07 path (`0x494a`),
re-checked against low byte 7 at `0x499e`.

The handler is a straight parse-and-forward:

- `0x49a2..0x49b4` assemble CDB bytes 2..5 into a big-endian 32-bit word, `0x49b8` `rev8`
  yields the native address in `r6`. **There is no address mask, whitelist, range or window
  check anywhere in the path.**
- `0x49bc` calls `0x470c`, which returns the big-endian 16-bit length from
  `[0x1813f8+21..22]`. `0x49c6` extracts the low 16 bits for the transform calls; the full
  value goes to the send call. **There is no length check either.**
- `0x49ce` calls `0xaa1e` (transform), `0x49e0` calls `0x4738` (send), `0x49ec` calls
  `0xaa1e` again on the normal return path.

The transform `0xaa1e` XOR-encodes the **source in place** (write-back at `0xaa4e`): it seeds
the CRC engine at `0xfd3500` with `0xffff`, feeds two `0xff` bytes, then per source byte feeds
`table[0x1f619 + (i mod 16)]` and XORs the byte with the current CRC low byte. The send
function `0x4738` chunks internally at 64 bytes (`0x4776..0x4788`) with no total-length cap.

Consequence: the ROM FD07 is a **read-modify-write** on the target region. On the normal path
the second transform restores the source, but an interrupted or failed transfer leaves the
source XOR-corrupted, and MMIO/peripheral windows do not behave like writable RAM under this
access pattern at all. This is the concrete mechanism behind the standing rule "avoid raw
UBOOT1 RAM/SFR reads" — the ROM FD07 must never be aimed at the P11 window.

### Loader FD07 handler (0x122230..0x122272)

The loader blob is stored encrypted on the upstream side; FB06's ROM completion path calls
`0xaa1e` once per 512-byte block after reception, so each block decrypts with a fresh seed.
The offline analysis decrypted the pinned blob with exactly the transform pinned above
(per-block re-seed) and disassembled it with the same pinned pi32v2 decoder used by
`tools/analyze_usb_rom.py`. The entry at `0x120000` then decodes as clean code (push.rmask,
clear the loader bss `0x127000..0x127470`, jump to main), which is also a self-check of the
transform parameters.

The loader's FD07 handler:

- `0x122230..0x122254` parse CDB bytes 2..5 (command buffer base `r14`, CDB byte 0 at
  `[r14+7]`, set up at `0x121d28..0x121d2c`) into the native big-endian address `r3`.
  **No address check of any kind.**
- `0x122258` calls `0x121236`, assembling the big-endian 16-bit length from CDB bytes 6..7
  into `r4`. **No length check in the handler.**
- `0x12225c..0x122264`: `memcpy(dest=0x127000, src=address, n=length)` via the ROM export
  stub `0x1fd74`. This is a **plain copy: the source is only read, never written** — no
  in-place transform, so no MengLi write-back hazard.
- `0x122268..0x122270`: send the copied bytes through the function pointer at
  `[0x127100+0x38]` (the loader's USB device object lives at `0x127100`, set at `0x121d1c`;
  the IO buffer register `r11 = 0x127000` is set at `0x121d30`).

The length boundary is structural, not checked: the IO buffer is `0x127000..0x1270ff` and the
USB device object starts **immediately** at `0x127100`. Any FD07 with length > 0x100
overwrites the loader's own device object (including the send pointer at `0x127138`) with a
copy of the source bytes; the subsequent indirect send call then jumps through a smashed
pointer and USB dies. The loader's FD05 flash-read handler, by contrast, chunks internally at
0x100 (`0x1221fe..0x122202`) and never overflows, which is why 512-byte FD05 reads worked in
the same session while 512-byte FD07 reads did not.

### Cross-validation against device observations

| Device observation (2026-11-10 session) | Static prediction from this disassembly | Match |
| --- | --- | --- |
| FD07 512B at address 0x0 and 0x4000 both shut down USB | memcpy of 512B from any address smashes `0x127100..0x1271ff` (device object) — address-independent failure | yes |
| 32B and 160B probes and 128B continuous reads all succeeded (whole 0x0..0x20000 window, including address 0) | ≤0x100 keeps the copy inside the IO buffer; no state corruption | yes |
| FD05 512B reads succeeded in the same loader session | FD05 loops in ≤0x100 chunks through the same buffer; no overflow | yes |
| FB06 512B upload blocks succeeded | different command; FB06 receive path writes the upload target, not the IO buffer | yes |
| Upstream tool caps memory IO at 256B | matches the 0x100-byte IO buffer ending exactly at the device object | yes |

The exact 0x100 boundary is statically safe (last copied byte `0x1270ff`) but was never
device-tested; the historical and recommended block size stays at **128 bytes**, the tested
value.

### Can FD07 read 0xf27f4a?

**Command layer: yes.** Neither FD07 implementation performs any address legality check; the
session-relevant loader implementation is a plain read-only memcpy. `0xf27f4a` with length
≤0x100 would be accepted and copied.

**Bus layer: probably, with direct same-context evidence for the window family.** The loader
itself accesses the `0xf2xxxx` peripheral window during normal operation: it writes
`[0xf2c120]=0` during init (`0x1205c0..0x1205c6`), reads `[0xf2c2c8]` in a config loop
(`0x121316`), and read-modify-writes `[0xf2c254]` (`0x122772..0x122784`). So the `0xf2xxxx`
window is mapped and accessible in exactly the execution context a future session would use.
The P11 RAM subwindow (`0xf20000..0xf2a000`) is not exercised by the loader itself; the
application accesses it from the same core, but no offline artifact proves the subwindow is
readable while P11 is in its post-reset state during a USB session. This stays a flagged
uncertainty.

**Content layer: unknown offline — the real risk.** The heap bytes are written by P11 during
normal boot. Entering USB debug mode resets the chip out of the application (re-enumeration
from `3654:4a55` to `4c4a:3442`), and whether P11's RAM content (and P11 itself) survives
that transition — reset, retention, or still-running — cannot be determined from the ROM or
loader bytes. A mismatched readback would therefore be **ambiguous**: it could disprove the
derivation or merely show that the window did not retain the boot-time bytes. The session
plan below resolves this with positive controls whose expected bytes are independently known.

### Candidate address assessment

| Address | Region | Command layer | Offline verdict / caveat |
| --- | --- | --- | --- |
| `0xf27f40..0xf27fbf` | P11 RAM: P2M message area (heap descriptor at +0xa) and M2P area | accepted, read-only copy | one 128B read at `0xf27f40` covers descriptor + both message areas; retention across the mode-switch reset is the flagged unknown |
| `0xfd3604` | JL_LRCT NUM (SFR) | accepted | plain MMIO read; value is the app's trim result counter — in a loader session the app trim never ran, so expect reset-state, not boot-state, data; low diagnostic value |
| `0x1265f4/0x1265f8` | main RAM, CPU1 ready snapshot (`0x1265f5`) | accepted | plain SRAM read; loader writes nothing here (its bss is `0x127000..0x127470`); stale post-boot value (1) expected if RAM retains — useful retention control |
| `0x19fef4/0x19fef8` | main RAM, CPU1 entry snapshot (`0x19fef8`, known `0x12caf4`) | accepted | strong positive control: expected word is known from three-way simulation evidence; caveat: the loader calls ROM `mask_init`, whose small (61/63-byte-class) inits near `0x19fd68` are distinct from but not provably bounded away from `0x19fef8` — treat a surprise here as "loader init touched it", not as a failed read |
| `0xf20080..0xf2009f` | P11 RAM, p11_code.bin head | accepted | **key positive control**: expected bytes are the public UFW resource payload (BANKCB entry0, CRC-verified offline); matching them proves P11 RAM retention and validates the whole read path before the target read |

## Device session plan (contingency — NOT authorized, NOT executed)

Preconditions, all three required:

1. This offline assessment concludes "readable at the command layer" (it does), and the
   positive-control strategy is accepted as the interpretation guard.
2. `read_rom.py` is extended with a separate, offline-tested P11-heap profile (sketch below);
   the extension must pass its offline unit tests before any session.
3. The user explicitly authorizes the session in the active conversation at session time.
   Nothing on this page authorizes a device action.

Steps (single session, no retries):

1. Let the headset boot normally and finish startup (the normal state in which P11 has
   written the heap descriptor).
2. Plug USB, verify the fresh normal QCY identity, switch to ROM mode, verify BR28/UBOOT1.00,
   upload the pinned loader (FB06), run it (FB08), confirm SPI NOR (FC0A) — the existing
   session-entry sequence, unchanged.
3. Positive controls first, each read twice with byte-identical comparison:
   `0x19fef4` (8B), `0x1265f4` (8B), `0xf20080` (32B).
4. One 128-byte FD07 read at `0xf27f40`, read twice, byte-identical comparison required.
5. Unplug USB, manually restart the headset, confirm normal operation (the documented
   closeout).

Safety boundaries: read-only commands only; no Flash/OTP/erase/program/jump/reset commands;
FD07 only after loader verification (never through the ROM's in-place-transform path); block
size 128B (tested value); no automatic retries — any failure or ambiguity ends the session
and is recorded as-is; UBOOT1 RAM/SFR raw reads remain out of scope.

Expected outputs and decision criteria:

- All controls match AND block bytes at offset `+0xa` equal `0c 3d f4 39` (LE u16 pair
  begin/size as derived) → the four bytes upgrade to **measured**; record in evidence with
  the session log; the derived fixture becomes a measured-value fixture.
- Controls match but the descriptor differs → record the bytes honestly; do **not**
  force-fit; the derived fixture remains an explicit experimental assumption and the mismatch
  becomes new input to the derivation review.
- P11 RAM control (`0xf20080`) returns an all-zero/all-FF pattern → P11 RAM did not retain
  boot-time content; the target read is **inconclusive** (cannot distinguish wrong derivation
  from cleared window); record the blocker and stop.
- Any FD07 failure or USB shutdown → record and stop; no retry.

## If FD07 cannot reach it

If a future authorized session shows the P11 RAM window unreadable or non-retaining, the
measurement path is closed for this channel. The fallback is already in place and remains a
legitimate choice: keep the derived pair (begin=`0x3d0c`/size=`0x39f4`) as an **explicit
experimental configuration** (`p11_heap_experimental_config`), exactly as the conditional
frontier 302,620+ already does. The cost is stated plainly: the main scenario's heap evidence
stays "derived, not measured", and that evidence level does not upgrade. No simulated or
invented byte value may ever be recorded as measured.

## Future tool extension sketch (not implemented on this route)

`read_rom.py` keeps its fixed ROM-window profile untouched. A minimal separate profile, only
if the session is later authorized:

- A new explicit subcommand (for example `--p11-heap-probe`) with its own fixed read plan:
  the four control reads and the one target read above, each 128B, each twice-read.
- The phase gate stays: FD07 allowed only in `loader-ready`, after pinned-loader FB06/FB08
  and FC0A, exactly as now; every other opcode remains rejected.
- The window guard changes from "address within 0..0x20000" to an explicit per-read allowlist
  (the five planned addresses), not a general window; block size pinned at 128B; no retries.
- Output goes to a fresh 0700 workdir as today, with the control-vs-expected comparison
  reported rather than enforced (a mismatch is data, not an error to retry away).

This route does not implement any of it; the sketch exists so the extension, if ever
requested, stays inside the existing guard philosophy.

2026-11-13, route BM (offline tool route): the sketch above is now implemented in
`tools/usb-recovery/read_rom.py` as the optional `--p11-heap-probe` mode. The fixed plan
is the four control reads (`0x19fef4`, `0x1265f4`, `0xf20080`, `0xfd3604`) then the target
`0xf27f40`, each exactly one 128B FD07 read, each read twice with byte comparison and no
retry; the FD07 phase gate (loader-ready after pinned FB06/FB08/FC0A) and the fresh normal
QCY USB entry are the existing session sequence, unchanged. The default ROM-window mode of
`read_rom.py` is untouched. Control-vs-expected comparisons are reported, never enforced;
the `0xf20080` head comparison needs the optional pinned `--p11-code` reference (public UFW
resource, SHA1-pinned). Offline tests extend `tools/usb-recovery/test_rom.py` (allowlist
rejection, 128B pinning, phase gate, two-read consistency, report-only expectations); no
device session was connected or authorized by this implementation. The session preconditions
above (including explicit user authorization at session time) are unchanged.

## Limits of this assessment

- The loader disassembly covers the pinned public blob only; it proves command semantics, not
  silicon bus behavior for the P11 RAM subwindow, and not what the mode-switch reset does to
  P11 or its RAM. Those are exactly the uncertainties the positive controls are designed to
  surface empirically.
- The transform reconstruction was validated by the loader entry decoding cleanly and by all
  five device observations matching; the CRC engine's silicon behavior is otherwise pinned
  only as far as the existing repository contracts state.
- Nothing here certifies a USB session, ownership of RAM ranges, or restores machine state
  after any command; the standing rule that a completed response is not a restoration proof
  still applies. No device action is authorized by this page.
