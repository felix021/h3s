"""Exact memoization of the pinned upstream decode/execute pipeline.

Route PERF (docs/perf-budget-analysis.md) measured the conditional frontier
stepping segment: instruction semantics ~1.1% of CPU, the rest is per-step
scaffolding. Three pure recomputations dominate the upstream share:

- ``check.takes_state(fn)`` runs ``inspect.signature(fn)`` on every
  ``effect()`` call (0.881/step; ~12-14% of stepping CPU inside inspect
  itself) although the result is constant per model function.
- ``check.operands(insn, raw)`` rebuilds each field's bit positions
  (``insn.field_bits`` re-joins the encoding string and rescans it per field,
  per call) although the operand dict is a pure function of (insn, raw)
  (2.129/step).
- ``codec.format(insn, values)`` renders full disassembly text every step for
  a diagnostic buffer that keeps only the last 64 rows (1.013/step), plus the
  ``half_mask`` material inside pair execution.

The upstream tree is pinned read-only (``probe_pi32v2.upstream()`` verifies
its git state), so the wrappers are installed from the repo-side execution
layer instead: ``DualCoreMachine.attach()`` — the single assembly point every
stepping session passes through — calls :func:`install`, which locates the
loaded simulator's namespace through the CPU class and replaces the three
module attributes with exact memoizing versions:

- takes_state: first-call cache keyed by the callable itself. The first
  reflection still runs at its original execution site (no load-time
  precomputation, no signature errors triggered for functions that never
  run); a reflection error is not cached and repeats unchanged.
- operands: per-insn dict keyed by the sorted raw field values. Keying by
  decoded field values is finer than (PC, instruction word): the same
  instruction word always decodes to the same field values, self-modified
  code decodes to different values and simply misses. The returned op dict
  is shared between callers; neither repository callers nor upstream models
  mutate it (grep-verified, pinned by tools/test_exec_memo.py).
- format: per-insn dict keyed by (sorted values, syntax). ``codec.format``
  is a pure function of its arguments; the returned text is shared
  (Python strings are immutable).
- ``Insn.mask``/``Insn.value`` (route O3, docs/o3-memory-design.md §5.2):
  the upstream properties rebuild the encoding join + per-char scan + int
  parse on EVERY access; ``codec.decode``'s scan loop touches them twice per
  candidate (~89 candidates per real decode miss). They are pure functions
  of ``insn.words``, the table is pinned and ``words`` is never mutated at
  runtime (grep-verified upstream-wide; tools/test_exec_memo.py re-pins it),
  so the class attributes are replaced by first-call-cached descriptors that
  memo the original property's result into each instance's ``__dict__`` under
  PRIVATE new keys (never shadowing anything: the property is replaced at the
  class level; instance dicts could not shadow the old data descriptor
  anyway). The upstream decode loop itself is untouched.

Behavior contract: same inputs return the exact object the original call
would have produced (the first call always runs the original function), so
reports, counts and stop points are unchanged. No PC-keyed caching of
mutable-memory decodes is added here; the existing decode cache and its byte
revalidation in the probe layer are untouched.
"""

_OPERANDS_MEMO = '_h3s_operands_memo'
_FORMAT_MEMO = '_h3s_format_memo'
_MASK_MEMO = '_h3s_mask_cached'
_VALUE_MEMO = '_h3s_value_cached'
_WRAPPER_MARK = '_h3s_exec_memo'
_CACHED_MARK = '_h3s_first_call_cached'

_installed = {}  # (module object, attribute name) -> original function


class _FirstCallCachedAttr:
    """Non-data descriptor: first access runs the original property getter
    and stores the result in the instance ``__dict__`` under a private key;
    later accesses are a dict lookup inside ``__get__`` (non-data descriptors
    let instance attributes win, and the private key never collides with a
    real attribute). ``uninstall`` restores the original property (a data
    descriptor, which takes precedence over any leftover private keys)."""

    def __init__(self, fget, key):
        self.fget = fget
        self.key = key

    def __get__(self, obj, objtype=None):
        if obj is None:
            return self
        d = obj.__dict__
        v = d.get(self.key)
        if v is None:
            v = d[self.key] = self.fget(obj)
        return v


def _sim_namespace(cpu):
    """The loaded simulator's module namespace, reached through the CPU class.

    The pinning loader (probe_pi32v2.upstream) execs sim.py under a private
    name, so the namespace is identified structurally: the upstream CPU base
    class is the MRO entry whose own ``effect`` closes over ``check`` and
    ``codec`` module objects. Probe-layer subclasses override ``effect`` with
    a different module namespace and never match.
    """
    for cls in type(cpu).__mro__:
        effect = cls.__dict__.get('effect')
        if effect is None:
            continue
        namespace = effect.__globals__
        check = namespace.get('check')
        codec = namespace.get('codec')
        if (hasattr(check, 'takes_state') and hasattr(check, 'operands')
                and hasattr(codec, 'format')):
            return namespace
    return None


def install(cpu):
    """Install the memoizing wrappers (idempotent, process-wide).

    Returns True when the wrappers are active after the call. The underlying
    module objects are imported once per process by the import system, so
    repeated machine assemblies (and multiple machines in one process) reuse
    the same installation.
    """
    namespace = _sim_namespace(cpu)
    if namespace is None:
        return False
    check = namespace['check']
    codec = namespace['codec']
    if not getattr(check.takes_state, _WRAPPER_MARK, False):
        original = check.takes_state
        cache = {}

        def takes_state(fn, _original=original, _cache=cache):
            try:
                return _cache[fn]
            except KeyError:
                pass
            result = _original(fn)  # errors are not cached and repeat unchanged
            _cache[fn] = result
            return result

        takes_state.__wrapped__ = original
        setattr(takes_state, _WRAPPER_MARK, True)
        _installed[(id(check), 'takes_state')] = (check, 'takes_state', original)
        check.takes_state = takes_state
    if not getattr(check.operands, _WRAPPER_MARK, False):
        original = check.operands

        def operands(insn, raw, _original=original):
            memo = insn.__dict__.get(_OPERANDS_MEMO)
            key = tuple(sorted(raw.items()))
            if memo is not None:
                got = memo.get(key)
                if got is not None:
                    return got
            got = _original(insn, raw)
            if memo is None:
                memo = insn.__dict__[_OPERANDS_MEMO] = {}
            memo[key] = got
            return got

        operands.__wrapped__ = original
        setattr(operands, _WRAPPER_MARK, True)
        _installed[(id(check), 'operands')] = (check, 'operands', original)
        check.operands = operands
    if not getattr(codec.format, _WRAPPER_MARK, False):
        original = codec.format

        def format(insn, values, syntax=None, _original=original):
            memo = insn.__dict__.get(_FORMAT_MEMO)
            key = (tuple(sorted(values.items())), syntax)
            if memo is not None:
                got = memo.get(key)
                if got is not None:
                    return got
            got = _original(insn, values, syntax)
            if memo is None:
                memo = insn.__dict__[_FORMAT_MEMO] = {}
            memo[key] = got
            return got

        format.__wrapped__ = original
        setattr(format, _WRAPPER_MARK, True)
        _installed[(id(codec), 'format')] = (codec, 'format', original)
        codec.format = format
    # Route O3 (docs/o3-memory-design.md §5.2): first-call-cached
    # Insn.mask/Insn.value. Shape-guarded: only plain upstream properties are
    # replaced (the tree itself is pinned by probe_pi32v2.upstream's revision
    # check, which refuses a changed tree before anything here runs).
    insn_cls = getattr(namespace.get('table'), 'Insn', None)
    if (insn_cls is not None
            and isinstance(getattr(insn_cls, 'mask', None), property)
            and isinstance(getattr(insn_cls, 'value', None), property)
            and not any(getattr(getattr(insn_cls, name), _CACHED_MARK, False)
                        for name in ('mask', 'value'))):
        for name, key in (('mask', _MASK_MEMO), ('value', _VALUE_MEMO)):
            original_property = getattr(insn_cls, name)
            cached = _FirstCallCachedAttr(original_property.fget, key)
            setattr(cached, _CACHED_MARK, True)
            _installed[(id(insn_cls), name)] = (insn_cls, name, original_property)
            setattr(insn_cls, name, cached)
    return True


def uninstall():
    """Restore every original function (test isolation only)."""
    while _installed:
        _, (module, name, original) = _installed.popitem()
        setattr(module, name, original)
