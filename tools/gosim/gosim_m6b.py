"""ctypes glue for the Go M6 Stage B after_step no-op judgment
(tools/gosim/after_step.go).

The differential test test_m6_after_step_consistency.py drives the static
after_step judgment through this module and compares it against the real
br28_machine.DualCoreMachine.after_step over the four hook anchors. Build the
shared library first:

    cd tools/gosim && go build -buildmode=c-shared -o libgosim.so ./cshared
"""
import ctypes
import json
import os
from ctypes import c_char_p, c_int, c_uint32

_HERE = os.path.dirname(os.path.abspath(__file__))
_LIB = os.path.join(_HERE, "libgosim.so")

_LIBRARY = ctypes.CDLL(_LIB)
_LIBRARY.gosim_afterstep_can_prove_noop.argtypes = [c_char_p, c_int, c_uint32, c_uint32]
_LIBRARY.gosim_afterstep_can_prove_noop.restype = c_int

NOOP = 1
FIRES = 0
_CONFIG_ERR = -1


def can_prove_noop(config, core, before_pc, next_pc):
    """Return True when the Go judgment proves the after_step phase is a no-op
    for this (core, before_pc, next_pc) under the given hook configuration."""
    raw = json.dumps(config).encode("utf-8")
    rc = _LIBRARY.gosim_afterstep_can_prove_noop(raw, core, before_pc, next_pc)
    if rc == _CONFIG_ERR:
        raise ValueError("gosim after_step config load failed")
    return rc == NOOP
