"""Opt-in Go segment-batch engine for probe_boot.explore's inner loop.

Wraps tools/gosim/gosim.py (cgo glue) + tools/gosim/gosim_batch.py (byte
protocol) so explore can run a pre-qualified stretch of safe instructions in
one cgo call and hand the per-step observations (pc / next_pc / flags / 16
registers) back to the Python loop, which materializes the trace rows exactly
as the legacy step-by-step path does.

Default off: probe_boot.gosim_batch_engine stays None and the loop is byte-for-
byte the legacy path (zero behavior change). The equivalence is enforced by the
measure harness (gosim_measure --self-check-batch): the same crc_bankb run with
the engine on must serialize to the identical report and trace digest.

The engine is a thin adapter over the stage-1 segment contract (segment.go /
gosim_batch.py). It never fabricates a value: Go stops at the first step outside
the contract (unsafe instruction, no model, no decode, fetch outside the
supplied immutable window), and the returned prefix is all that gets used.
"""
import sys
from pathlib import Path

_GOSIM = Path(__file__).resolve().parent / 'gosim'
if str(_GOSIM) not in sys.path:
    sys.path.insert(0, str(_GOSIM))

import gosim as _glue        # noqa: E402
import gosim_batch as _batch  # noqa: E402

# Cap on the number of steps one Go call may execute. Segments in the real
# frontier are short (max ~40 sequential-ALU steps, longer once branches land),
# so a modest cap bounds the wasted Go work when a hardware.advance IRQ/Blocked
# boundary truncates the materialized prefix.
BATCH_CAP = 256


class BatchEngine:
    """One Go CPU handle reused across a single explore run; the immutable
    ROM/flash fetch bytes are loaded once and never change."""

    def __init__(self, rom, app, entry):
        self.go = _glue.Gosim(arch=True)
        self.rom = rom
        self.app = app
        self.entry = entry
        self._loaded = False

    def _load(self):
        if self._loaded:
            return
        if self.rom is not None:
            self.go.write(0, self.rom)
        self.go.write(self.entry, self.app)
        self._loaded = True

    def _window(self, pc):
        # The immutable fetch window is the ROM window or the flash app slice,
        # whichever contains the segment head. A sequential-ALU segment never
        # leaves its window; a (phase-2) branch that does stops the batch with
        # BatchFetch and the boundary is handed back to Python.
        if self.rom is not None and 0 <= pc < len(self.rom):
            return 0, len(self.rom)
        return self.entry, self.entry + len(self.app)

    def run_segment(self, c, n):
        """Run up to n safe steps from c's current state (c.R/c.flags/c.sp/
        c.rets/c.pc must all be concrete ints). Returns (stop_code, stop_pc,
        steps) where steps is a list of (pc, next_pc, flags, R_after) tuples and
        R_after is a 16-tuple of ints."""
        self._load()
        lo, hi = self._window(c.pc)
        self.go.set_regs(c.R)
        self.go.set_state(c.flags, c.sp, c.rets, c.pc)
        outcap = _batch.HDR_SIZE + n * (_batch.STEP_FIXED + _batch.MEM_SIZE)
        buf = self.go.run_segment(n, lo, hi, outcap)
        (_executed, stop_code, stop_pc), steps = _batch.parse_buffer(buf)
        return stop_code, stop_pc, [
            (pc, next_pc, flags, R) for pc, next_pc, flags, R, _mem in steps
        ]
