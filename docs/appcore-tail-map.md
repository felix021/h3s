# app_core tail: static execution-path map and boundary inventory (route BH / sim-tailmap)

2026-11-13. Read-only research route. This page replaces the "B6 boundary count
unknown" entry of [docs/d1-gap-map.md](d1-gap-map.md) with a quantified
inventory: a segment-by-segment static path map from the P3_LS clear (the
in-flight route-BD stop at `0x6067d48`) through the app_core tail to the
message loop, the boundary list the model will actually hit, the two power-off
dead-wait canaries, and a concrete stop-point sequence for routes BG/BH.

**Method.** Static disassembly only (pinned `pi32v2-isa` decoder at the same
commit the probes use), driven by a purpose-built recursive walker with
constant propagation over the public firmware assets: app.bin (SHA
`18f3d573…`, load base `0x06000100`), the decrypted SFC plain image, and the
local private ROM window (local analysis only; no path or bytes reproduced
here). No `tools/` code was modified and no probe was executed. RAM-code
callees in `0x12a640..0x12ef14` are disassembled from their flash copy source
`0x060c3e18` (the pinned load table: RAM_CODE `0x12a640←0x060c3e18` len
`0x48d4`, DATA `0x124840←0x060c3338` len `0xae0`, BSS `0x125320..0x12a62c`
zero-filled), cross-checked against the pinned trampoline anchor
(`0x12caf4 = usp/sp 0x124840, reti 0x12cad4` decodes at flash `0x060c62cc`).

**Walker limitations (documented, not hidden).** Per-PC first-env policy on
loop back-edges; calls conservatively clobber r0–r3; unhandled operand shapes
are recorded but not interpreted; implausible constant bases (mask arithmetic)
are dropped as artifacts. The walker was validated against known anchors: the
frontier P3_LS clear shape at `0x6067d48..0x6067d64`, the cleanup walker's
`0xfd6000`/P33 accesses, and the B1/W-table contents below.

## 1. Pinned corrections and facts

- **B3 correction (d1-gap-map §2.2 B3 wrote `0x6064d0c`/`0x6064d24`).** The
  installs are `[0x129d7c]=0x6034d0c` (`0x6067d6a`) and `[0x129d80]=0x6034d24`
  (`0x6067d7a`) — plain RAM-data writes, both in the mapped BSS; no stop
  expected (d1-gap-map's judgement stands, only the addresses change).
- app_core is the never-returning task `0x6012028..0x60124b4`. Its pre-tail
  part (`0x6012028..0x60120dc`, including the A1/B1 table walkers and
  `0x6067876` power_init) has already executed in the current main-line
  prefix; the frontier sits inside the P33 window walker called at
  `0x60120e2`.
- Function-table layout around the tail (flash, all statically dumped):
  A1 `0x60c32c8..0x60c3338` 4×28B (pre-tail, executed);
  B1 `0x60c327c..0x60c328c` 4 ptrs (`0x60001b6,0x6004954,0x60124ba,0x60759ae`, pre-tail);
  W1 `0x60c32a4..0x60c32b8` 5 ptrs (`0x6012e3e,0x601321e,0x6013246,0x602ea68,0x60614a2`);
  W2 `0x60c3250..0x60c327c` 11 ptrs (`0x6004360,0x60074e6,0x601043a,0x6027658,0x602f5a8,0x603346e,0x6033678,0x603371e,0x607bf2e,0x60759ee,0x607f8be`);
  W3 `0x60c32b8..0x60c32bc` 1 ptr (`0x60009e8`);
  W4 `0x60c328c..0x60c32a4` 6 ptrs (`0x600ec2e,0x600ecc6,0x600ed30,0x600edf8,0x6034c80,0x6066f90`);
  struct table `0x60c0e20..0x60c0e30` one 16B entry
  `{flag=1, pdata=0x124888, fn=0x6000e94, init=0x6001866}`;
  cleanup table `0x60c32bc..0x60c32c8` 3 ptrs (`0x6022598,0x602ea60,0x6034c92`).
- Message-dispatch tables (read by the loop at `0x601240a`/`0x6012436`):
  flash `0x60a89e0..0x60a8c68` (81 slots) and `0x60a8c68..0x60a8cb0` (9
  slots), 8-byte `{event-id, handler}` pairs; roughly 30–45 live handler
  pointers into `0x600xxx..0x602xxx` (slot alignment ±4 needs one runtime
  observation to pin exactly — medium confidence). Loop breaks on event id 13
  or `0x10000c`.

## 2. Execution path map (post-walker, in order)

| Seg | Anchor | Content | Boundary surface |
|---|---|---|---|
| S1 | `0x6067c6e→0x6067d68` | P3_AWKUP subpath + P3_LS/`0xf2a638`/`0xf2a628` clear tail, `pop.pc_rl` returns | route BB+BD (in flight); nothing new after |
| S2 | `0x60120e8/0x60120ee` | B3 callback installs | RAM-data writes only |
| S3 | `0x60120f4..0x601210a` | `0x60115b4`×2 (three word writes `0xfd3100` each: `0x20000`, `0x20040`, `0x40`), ROM memset stub `0x1fd80` (176B `0xff`), `0x60115cc` (polls `0xfd3100` bit0-sign/bit4 with `csync`) | **N1 first new boundary** |
| S4 | `0x6012112..0x601213a` | resource getters `0x6001230`/`0x601160e` pairs for ids `0x10000`,`0x10006`,`0x10004` | reads inside `0x601163a` are the N1 poll |
| S5 | `0x601213e..0x601215e` | 32×`0x60116d0` free-memory loop (reads `0xfd3100`+`0xfd3104`), result `(sum−min−max)/30` → `h[0x126160+5968]` | N1 readback |
| S6 | `0x6012176/0x6012186` | `0x601174c` ×2 → float stores | N1 transitive |
| S7 | `0x601219c` | vector install `0x6065d86` (irq 39, arg `0x60348e8`) → RAM pend/post `0x12b55c`/`0x12b5b0` | RAM/OS only; both RAM fns touch BSS only |
| S8 | `0x60121ae` | task create `0x6066c26` (name `0x60349d2`) → `0x6066a7e`→`0x606689a` | RAM only; scheduler IPI round-trip (existing mechanism) |
| S9 | `0x60121ce..0x6012212` | struct walker: `0x6001866` init + task create fn `0x6000e94` pdata `0x124888` prio `[0x124898]=10` | RAM only |
| S10 | `0x60121b4..0x6012254` | pointer-table walkers W1(5)+W2(11)+W3(1)+W4(6) = 23 functions, all flash code, called unconditionally | W1[0] `0x6012e3e`→`0x6068152` P33/clock RMWs = **N3** |
| S11 | `0x6012258..0x6012294` | buffer init `0x6064162`, `0x6064f18`, task create `0x6067242` (fn `0x6000d6c`, name `0x60a1aad`); if ret==0 → RAM `0x12cc40` (reads `0xf2c044`, `0xf2c128`, `0xf2c280`) | `0xf2c044` = **N4** (conditional-main) |
| S12 | `0x601229c..0x60122a2` | `0x6064d9e`, `0x6011d32` (VM read) | RAM/flash only |
| S13 | `0x60122aa..0x60122f6` | boot-flag branch `b[0x1265c8]` (writer pinned: event machinery `0x606487c`): ==2 → `0x6067dda(12/16)` charger/power queries → `0x6000f88(90)` VM read, maybe `0x6001f12(90)` VM write | `0x6067dda`→`0x6067f84` cluster = **N2** (conditional) |
| S14 | `0x60122f6..0x601232c` | `0x6001cb0`; ret!=0 → `0x6011eae`+`0x6064f18`+task create `0x6067242`(fn `0x600741c`)+`b[0x1249f6]=1`+goto S18 | RAM only |
| S15 | `0x6012330..0x6012356` | (==0 path) 11×11 loop `0x600a5b4`→`0x600a590`→`0x606749e`+RAM `0x12cc7e`; 11 consecutive low → **K1**; normal exit r6>10 → S16 | N2 transitive (0x606749e reads P33/M2P) |
| S16 | `0x601235a..0x60123a4` | `0x6011eae`, `0x606859c`→`0x6067dda` branch; r0==0 → `0x6064efc(10)` wait-loop on `b[r8+47]`; `b[0x1249fe]` read (static value 0 → skips `0x602255c`) | N2 transitive |
| S17 | `0x6012458..0x601248c` | ≤150-retry loop `0x600a590`/`0x6064efc(1)`/`0x6000e74`; success → `b[r8+4456]=1` back to S16; exhaust → **K2** | N2 transitive |
| S18 | `0x60123a6..0x60123bc` | event posts `0x6000cba(18,0)`, `0x6000cba(20,2)`; `b[r8+632]=1` | RAM only |
| S19 | `0x60123e6..0x6012456` | **message loop**: `0x6064c8c(sp,16)` get-event until id 13/`0x10000c`; `0x60108fe`; dispatch over the two flash tables; loop forever | RAM/OS + dispatch; pends on empty queue = B9 idle horizon, not a stop |
| K1 | `0x601249c` | `0x6064efc(100)`, `0x6011fd8(4)`, cleanup `0x60682a4`, self-loop `0x60124a2` | canary (see §4) |
| K2 | `0x60124ae` | `b[r8+4457]=1`, `0x60682a4`, self-loop `0x60124b4` | canary (see §4) |

The audio/OS "domain" functions named in d1-gap-map B6
(`0x606dcc6/0x606dc14/0x606dd16/0x606dcd6`, `0x6066a0a/0x60669a6/0x60669f0/0x6066a1a`)
are **not on this sequential path**. `0x606dcc6` is a 4-instruction getter
(returns `b[0x126160+7684]`); `0x606dcd6`/`0x606dd16` wrap RAM pend/post and
app event machinery; the `0x6066a0a` family are OS flag primitives with dozens
of callers across the app. They are reached from **task bodies and event
handlers** (S8/S9/S11/S14 create tasks `0x6000e94`, `0x6000d6c`,
`0x600741c`; the §5 dispatch tables hold the handler set) — i.e. from
scheduler contexts that only run after app_core blocks in S19. That is the
post-D1 domain, inventoried in §5.

## 3. Boundary inventory (the core deliverable)

Closure: 335 functions reachable from the S2..S19 direct/indirect calls
(depth ≤8, ROM utility stubs not descended). Access classification against
the address sets already handled by `br28_model.py` + the main-line scenario
stack (`probe_irq_entry.py` `p11_heap_experimental_config`), with the SDK
cached headers for names.

**Totals on the sequential S1→S19 path: 71 distinct SFR addresses touched.**

- 26 already modeled exactly (P33 voltage `0xf2c110`/`0xf2c258`/`0xf2c26c`/
  `0xf2c270`/`0xff0000`, analog `0xf2c254`, WKUP family `0xf2c120`/`0xf2c128`/
  `0xf2c160/164/19c/1a0`, PORT_SEL tail `0xf2c300`, P3_LS `0xf2c340`,
  `0xf2c940/944`, M2P window `0xf27f80/8c`, LRCT `0xfd1924`, CRC engine
  `0xfd3500/04`, USB/io `0xfd6000/0xfd6014`, SFC `0xfe0200`, `0xffe8c0`,
  `0xf2c054`, `0xf2c114`, debug/cache …).
- 26 family-adjacent (same projection family, minor register-set extension):
  M2P bytes `0xf27f85..0xf27f9b`, P3_LS word set `0xf2c344..354` (route BD),
  `0xf2c0e0`, `0xf2c064`, `0xf2c124/12c`, `0xf2c280`, SYS-div family
  `0xfd0008/000c/0010/001c`, `0xfe0204`, and the **four P3_ANA_CON
  extensions `0xf2c25c/260/264/268`** (route-AM projection currently covers
  only CON2/CON5; `0x6068152` RMWs them on the main path via W1[0]).
- **19 genuinely new addresses in 4 clusters** — the real B6 decision list:

| # | Address(es) | Kind & sites | Owner function(s) | Path | Three-way first judgement | Dependency |
|---|---|---|---|---|---|---|
| N1a | `0xfd3100` | w×3 `0x60115ba/c4/c6` (`0x20000`,`0x20040`,`0x40`); poll-read `0x601164a/52` (bit0-sign, bit4) | `0x60115b4`, `0x601163a`, `0x6011666` | **main, first** (`0x60120f4`) | new decision point: write-only records (lp_timer CON precedent) + poll-completion readback (`lrct_trim_done` precedent shape: read→bit-test→loop) | HANDOFF item 4 already anticipated "补电源 0xfd3100" |
| N1b | `0xfd3104` | r `0x6011704` | `0x60116d0` | main (S5 loop) | same family as N1a (status word of the supply block) | N1a |
| N2 | `0xfd0200` r `0x6068076`; `0xfd1400/04`,`0xfd1d00`,`0xfd1e00`,`0xfd2408`,`0xfd5f00`,`0xfd6100`,`0xfd7504` w | power-framework on/off | `0x6067f84` (also `0x6067e16` for `0xf2c240`) | **conditional** (S13 charger branch `b[0x1265c8]==2`, S15/S16/S17 battery paths, K-trees) | new decision point: mostly write-only records + one `0xfd0200` read needing semantics | N1 (queries gated behind power up) |
| N3 | `0xf2c240` r/w RMW `0x606827e/8a`, `0x6067e28/32` | P33 unnamed window register | `0x6068152`, `0x6067e16` | **main via W1[0] `0x6012e3e`** (S10) | new decision point inside an existing family container (p33_window): readback semantics must not be invented silently | route-AM/AZ family precedents |
| N4 | `0xf2c044` r `0x12cc56`,`0x60682d0`, r/w `0x12ced4/da`; `0xf2c0e4/e8/ec` w `0x60682e8..f4`; `0xfd0600/04/08` `0x12cd26` | P33/power + unnamed peripheral block | `0x12cc40` (RAM), `0x12ceb8`, `0x60682a4`, `0x12cd26` | conditional-main (`0x12cc40` at S11 if task-create ret==0) + **K1/K2 only** | pin (watch-only): unreachable on the healthy path; model only if a canary is actually reached | K1/K2 (§4) |

Predicted **first stop** after route BD lands: PC `0x60115ba`,
`st.w [0xfd3100]=0x20000` → kind `unsupported_peripheral_write`
(`BR28Model.write` → `handles()` gate), i.e. N1a is the next frontier by
construction, ~a few dozen steps after the walker returns.

Non-SFR boundary surface on the path: **0 unmapped addresses, 0
mapped-but-uninitialized RAM reads** (heap untouched; all BSS/Data accesses
are to boot-initialized or firmware-written words), 31 unresolved indirect
call sites — all dispatch-through-RAM-struct patterns (message loop ×3,
event machinery `0x6064c0e` ×3, `0x60108fe` ×5, plus callback tables in
walker entries) that the machine resolves dynamically at runtime — and ROM
utility stubs `0x1fd80/0x1fd84/0x1fd8c` (memset-family; the machine already
executes ROM directly). **No p11_init re-entry exists anywhere in the
335-function closure** (the only ROM calls are the three stubs) — this closes
the d1-gap-map §6 open question, medium-high confidence (const-prop may miss
an indirect p11_init pointer, but no table slot in §1 resolves to it).

**Answer to "B6 real boundary count": 4 new-decision clusters (19 addresses)
+ 1 same-family extension (4 P3_ANA_CON registers) on the sequential path; 3
more new addresses off-path (§5).** The "large unknown segment" is now a
bounded, mostly-precedented list.

## 4. Canary and pruning analysis

- **K1 `0x601249c`** (call `0x60682a4` then `goto -2` self-loop at
  `0x60124a2`). Reachable only from S15 when `0x600a5b4` (→`0x600a590` →
  `0x606749e` power/charger query + RAM `0x12cc7e`) returns **below
  `h[0x1272d4]` on 11 consecutive iterations**. The threshold halfword has
  **no static writer** anywhere in either sweep closure (BSS → 0 at boot), so
  with a zero threshold the trigger reduces to 11 consecutive *negative*
  query returns (query failure). Inputs: N2-family models decide the value.
- **K2 `0x60124ae`** (`0x60682a4` then self-loop `0x60124b4`). Reached when
  the S17 ≤150-retry loop exhausts (`0x6000e74` ret > 9) or the first
  post-`0x6064efc(1)` flag read at `0x6012472` is zero — i.e. persistent
  ADC/power-read failure.
- **Third exit `0x6012486`** (recoverable-low battery): sets `b[r8+4456]=1`
  and rejoins S16 — not a dead wait; normal-path compatible.
- The shared subtree `0x60682a4` (cleanup) walks the 3-ptr table
  `{0x6022598,0x602ea60,0x6034c92}`, reads M2P `0xf27f85..8f`, writes P33
  `0xf2c0e0..ec`, `0xf2c044`, `0xf2c160/164/19c/1a0` and the P3_LS set
  again, reads `0xfd6000/0xfd6014`, and drives the `0x6084eac..0x60850bc`
  port functions — all N4/canary-surface.
- **Pruning judgement: pin K1/K2 as watch-only.** Under any
  healthy-input projection family (named-zero, last-write readback,
  poll-completion) the queries return non-negative values and the retries
  succeed, so neither canary should be reached; if the simulation ever
  reaches `0x601249c`/`0x60124ae`, treat it as a wrong-direction signal (as
  d1-gap-map already suggested), not as a modeling task. No subgraph needs
  eager pruning beyond this: everything else on the path is reachable.

## 5. Off-path inventory (post-D1 domains, for later routes)

- **Audio hardware registers `0xfe051c` and `0xfe0a04`** — the only genuinely
  new SFR domains found in the whole B6 closure. Owners: `0x606daf0`,
  `0x606db36`, `0x606dd16` (r/w `0xfe051c`), `0x606b40e`, `0x606db74`
  (`0xfe0a04`). These sit behind the audio task/handler layer
  (`0x60125f4`/`0x60125fe`/`0x601267a` and siblings in `0x6012xxx..0x6017xxx`
  call them), i.e. after D1. No SDK name in the cached headers; unnamed.
- `0xf2c10c` (r/w) in `0x60685fe`/`0x6068662` — P33-family, off the
  sequential path (idle/trim-domain).
- Event-handler set: the two dispatch tables of §1 (~30–45 flash handlers in
  `0x600xxx..0x602xxx`, ids `0x100002..0x100013` + `0x10000c`).
- Task bodies created by the tail: `0x6000e94` (pdata `0x124888`),
  `0x6000d6c`, conditional `0x600741c`; plus `0x6067242`'s internal
  table-driven creates over the 12-byte-entry table at `0x609d640`.

## 6. Route-ladder refinement (BG/BH split; suggestions, not decisions)

- **BG = main-path completion (S2→S19), three stops deep:**
  1. BG-a: N1 power supply `0xfd3100/04` — write-only records + poll
     readback decision (first stop `0x60115ba`); carries S3–S6.
  2. BG-b: N3 `0xf2c240` RMW readback + P3_ANA_CON extension
     `0xf2c25c/260/264/268` (route-AM container); carries S10/W1.
  3. BG-c: S8/S11 task-create round-trips (existing IPI mechanism) +
     conditional `0x12cc40` reading N4's `0xf2c044` — decide then.
- **BH = conditional power framework + canary watch:**
  1. BH-a: N2 cluster (`0x6067f84`: `0xfd0200` read + 8 write-only) — only
     needed if the S13 boot flag or battery paths route there; evidence
     first (which branches actually taken at runtime).
  2. BH-b: keep K1/K2 as watch-PCs; N4 stays pinned unless reached.
- **BI/BJ unchanged** (Timer0 ≈341k / core-tick ≈447k deadlines and the
  event-clock first-verification are exactly d1-gap-map B7–B10; S19's
  pend-on-empty is the BI entry condition).
- Off-path §5 inventory (audio regs, dispatch handlers) belongs after D1;
  it is listed here so later routes inherit a known map instead of a blank.

## 7. Uncertainties (stated)

- Const-prop precision: per-PC first-env and r0–r3 clobber mean a few
  indirect sites stay unresolved that runtime will resolve; all §1 table
  dumps are instruction-anchored, so the walker gaps do not affect the
  boundary list.
- Dispatch-table slot alignment (±4) and the exact break-id semantics of
  `0x10000c` need one runtime observation (BI-era) to pin; the table bounds
  and handler range are static-fact.
- The "no p11_init re-entry" claim is closure-scoped, not app-global.
- Step-count deadlines (341,097/447,490) remain estimates that later
  CON/PRD writes could move (d1-gap-map §6 caveat unchanged).
- `0x1fd80/0x1fd84/0x1fd8c` identification as memset-family stubs is by
  usage shape (176-byte `0xff` fill observed at `0x6012100`), medium
  confidence; the machine executes them as ROM regardless.
