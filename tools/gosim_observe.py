"""Opt-in observation of the explore hot loop for the "whole-loop-to-Go"
feasibility measurement (codex plan tools/gosim/whole-loop).

纯只读测量件：默认关闭（模块级单例为 None，probe_boot 零开销零行为变化），
由环境变量 H3S_GOSIM_OBSERVE 显式开启。开启后只在 explore 主循环的尝试边界
做状态读取与计数，不写任何仿真状态、不改报告字节。

它回答的问题：真实 frontier 上「满足整段搬移安全条件的连续尝试段」有多长、
覆盖多少 CPU 时间、连续段为什么被打断。安全条件逐条照 codex 方案第 3 节的
硬条件标记（执行条件 + 时间条件），见 classify 逻辑。

退出原因（连续段被打断的第一条不满足条件，canonical 顺序）：
  pc_hook          活跃 PC 钩子（diagnostics 观察器截停点）
  multicore        双核运行（单核运行条件失败）
  abstract_value   抽象值（UnknownWord/IcfgValue 非具体整数）
  repeat_after_then repeat / after_then 在途
  ram_virtual_fetch RAM/虚拟取指（非 ROM/flash 不可变代码）
  parallel          .p 并行指令
  memory_access     访存（memory/stack 指令族）
  special_register  特殊寄存器（sr 字段 / cache 指令）
  stall             CoreStall（idle 停泊等）
  irq               IrqDeliveryRequest
  blocked           Blocked 诚实停
  upstream_failure  上游模拟器异常（RuntimeError/ValueError/KeyError/IndexError）
  budget            预算换手（instruction_budget 正常换手收尾）
"""
import os
import time
from collections import Counter

ENV = 'H3S_GOSIM_OBSERVE'

# canonical exit-reason constants (evaluation order for first-failure marking)
PC_HOOK = 'pc_hook'
MULTICORE = 'multicore'
ABSTRACT = 'abstract_value'
REPEAT = 'repeat_after_then'
RAM_FETCH = 'ram_virtual_fetch'
PARALLEL = 'parallel'
MEMORY = 'memory_access'
SPECIAL_REG = 'special_register'
STALL = 'stall'
IRQ = 'irq'
BLOCKED = 'blocked'
UPSTREAM = 'upstream_failure'
BUDGET = 'budget'

# instruction families judged "访存" and "特殊寄存器" (derived from
# pi32v2.toml group taxonomy: memory=ld/st/rmw 族, stack=push/pop/ldm/stm 族,
# cache=flush/iflush/pfetch, sr 字段=mov r_sr/mov sr=imm 等特殊寄存器访问)
_MEMORY_GROUPS = ('memory', 'stack')
_SPECIAL_REG_GROUPS = ('cache',)


def enabled():
    """Non-empty H3S_GOSIM_OBSERVE turns the observer on (default off)."""
    return bool(os.environ.get(ENV, ''))


def immutable_fetch_pc(pc, rom):
    """True when pc is in the immutable (ROM/flash) fetch region, mirroring
    CPU.decode's immutable branch exactly."""
    return (rom is not None and 0 <= pc < len(rom)) or 0x06000000 <= pc < 0x08000000


def concrete_int(value):
    """Strict int check: excludes bool, UnknownWord and IcfgValue (neither
    subclasses int)."""
    return type(value) is int


class GosimObserver:
    """Per-attempt run-length collector. All methods are pure bookkeeping over
    its own state plus read-only views of the CPU/instruction; nothing it reads
    is ever mutated, and no simulation object is touched."""

    def __init__(self):
        self.reset()

    def reset(self):
        self.runs = Counter()            # 连续段长 -> 段数
        self.exit_reasons = Counter()    # 退出原因 -> 段数(只在段长>0 时记)
        self.attempt_reasons = Counter() # 每次尝试的首失败条件 -> 次数(全量)
        self.total_attempts = 0
        self.safe_attempts = 0
        self.safe_thread_time = 0.0
        self.total_thread_time = 0.0
        self.current_run = 0
        self.max_run = 0
        self._t0 = None
        self._head_reason = None
        self._insn_reason = None

    # ---- per-attempt hooks (called from probe_boot.explore) ----

    def begin(self, *, single_core, immutable, concrete, repeat_active, pc_hook):
        """Loop head, after machine.select(): classify head-level execution
        conditions and start the thread_time window for this attempt."""
        self.total_attempts += 1
        self._t0 = time.thread_time()
        self._head_reason = None
        self._insn_reason = None
        if pc_hook:
            self._head_reason = PC_HOOK
        elif not single_core:
            self._head_reason = MULTICORE
        elif not concrete:
            self._head_reason = ABSTRACT
        elif repeat_active:
            self._head_reason = REPEAT
        elif not immutable:
            self._head_reason = RAM_FETCH

    def hook_stop(self):
        """A diagnostics stop-before-pc / stop-on-rom-return breaks the loop
        before decode; treat it as a pc_hook unsafe attempt and close the run."""
        self.total_thread_time += time.thread_time() - self._t0
        self.attempt_reasons[PC_HOOK] += 1
        self._settle(PC_HOOK)

    def note_insn(self, insn):
        """After decode: classify instruction-level execution conditions."""
        if self._head_reason is not None:
            return
        group = getattr(insn, 'group', '') or ''
        parallel = getattr(insn, 'id', '').endswith('.p')
        memory = group in _MEMORY_GROUPS
        special_reg = group in _SPECIAL_REG_GROUPS or any(
            f.kind == 'sr' for f in getattr(insn, 'fields', {}).values())
        if parallel:
            self._insn_reason = PARALLEL
        elif memory:
            self._insn_reason = MEMORY
        elif special_reg:
            self._insn_reason = SPECIAL_REG

    def finalize(self, clean, post_reason=None):
        """After the try/except chain: settle this attempt. clean=True means the
        attempt retired normally (step + advance, no exception, no stop)."""
        dt = time.thread_time() - self._t0
        self.total_thread_time += dt
        if self._head_reason is not None:
            reason = self._head_reason
        elif self._insn_reason is not None:
            reason = self._insn_reason
        elif not clean:
            reason = post_reason
        else:
            reason = None
        self.attempt_reasons[reason or 'safe'] += 1
        self._settle(reason, dt)

    def finish(self, stop_kind):
        """Post-loop: close any still-open run (only instruction_budget leaves
        one open; a stop already closed it via finalize/hook_stop)."""
        reason = BUDGET if stop_kind == 'instruction_budget' else stop_kind
        self._settle(reason, 0.0)

    def _settle(self, reason, dt):
        if reason is None:
            self.current_run += 1
            self.safe_attempts += 1
            self.safe_thread_time += dt
            return
        if self.current_run > 0:
            self.runs[self.current_run] += 1
            self.exit_reasons[reason] += 1
            if self.current_run > self.max_run:
                self.max_run = self.current_run
            self.current_run = 0

    # ---- reporting ----

    def summary(self):
        runs = dict(self.runs)
        buckets = {'N=1': 0, '2-8': 0, '9-32': 0, '33-128': 0, '129+': 0}
        for length, count in runs.items():
            if length == 1:
                buckets['N=1'] += count
            elif length <= 8:
                buckets['2-8'] += count
            elif length <= 32:
                buckets['9-32'] += count
            elif length <= 128:
                buckets['33-128'] += count
            else:
                buckets['129+'] += count
        total_runs = sum(runs.values()) or 0
        covered = sum(length * count for length, count in runs.items())
        total_time = self.total_thread_time
        safe_time = self.safe_thread_time
        return {
            'enabled': True,
            'total_attempts': self.total_attempts,
            'safe_attempts': self.safe_attempts,
            'safe_attempt_fraction': (self.safe_attempts / self.total_attempts
                                      if self.total_attempts else 0.0),
            'runs': runs,
            'run_count': total_runs,
            'max_run': self.max_run,
            'run_length_buckets': buckets,
            'run_length_bucket_fractions': {k: (v / total_runs if total_runs else 0.0)
                                            for k, v in buckets.items()},
            'covered_attempts': covered,
            'exit_reasons': dict(self.exit_reasons),
            'attempt_reasons': dict(self.attempt_reasons),
            'total_thread_time_s': total_time,
            'safe_thread_time_s': safe_time,
            'safe_thread_time_fraction': (safe_time / total_time if total_time else 0.0),
        }
