#!/usr/bin/env python3
"""copex.ppi mode-22 指数近似模型(路 COPEXIMPL,codex 裁 (a) 收窄版)。

背景与证据(docs/frontier-scout-copex.md,侦察双源钉死):
- mode 22 = e^x。SDK DSP 库 math_fast_funtion.c 的 exp_float 就是
  「fmin(31)/fmax(-32) 钳位 → zext 上字清零 → copex(22) → trunc 下字」;
  固件 0x606a8fc exp 叶子(0x606a900 停点)与 IR 逐指令同形。AC791N 硬件
  捕获 6 条中 4 条数值吻合(e^0→1.0、极小输入→1.0×3),e^1.0→0x402df853
  一条有限互证。
- AC791N ≠ BR28:捕获只证 ISA 层;BR28 数值细节(舍入/全域误差)无真机
  数据。本模型是 **conditional mathematical approximation /
  derived-not-measured**,不是硅位精确复刻:下字=host double exp 的
  float32 就近舍入(IEEE round-to-nearest);AC791N 在 e^1.0 上比正确
  舍入低 1 ulp(0x402df853 vs 0x402df854)——如实标注、不刻意复刻,
  ±1 ulp 敏感性由验证臂单独覆盖。
- dst 上字:硬件捕获非常数(5 条 0x1ffffff5、1 条 0x2b7e14f2),isa
  notes 的 mode-15 历史依赖前科未对 mode 22 排除——模型不写假常数,
  表示为 CopexUpperWord「不透明结果」:允许搬运/栈保存恢复(字节来源=
  copex 指令 PC),任何数值消费即停。
- flags:364 条捕获共识只写 dst pair、psr 低 4 位保持——finish_exact
  不动 flags,契约一致。

契约(fail-closed,任何不满足=专用停点,不部分提交):
- n == 22 精确匹配(不用 n&31 推广——硬件等价证据未覆盖控制高位组合;
  bit4=浮点族选择的推断不作为放行依据)。其余 7 个 app 模式(24/16/17/
  23/0/6/7)另案审查,本模型一律 dsp_mode_not_modeled 诚实停。
- 源上字已知 0(SDK zext 形状;非零或抽象值=dsp_operand_shape_not_modeled)。
- 源下字有限 float32,且 x 在声明域内:float32 正常态可表出的 e^x,
  x∈[ln(2^-126), ln((2-2^-23)·2^127)]≈[-87.3365, +88.7228](闭区间;
  次正规/上溢/NaN/Inf 的硬件行为无证据,dsp_domain_boundary 停)。
  app 输入域 x∈[-10.4,+4.5](eq 系数 db2mag 的 dB 换算)远在域内。
- .p 并行孪生显式拒绝(基形注册会被上游 pair() 自动派发到基形模型——
  「不注册即诚实停」不成立,见侦察 §6 勘误 2):copex.ppi.p 作前半、
  或任何 .p 指令的并行后半是 copex 家族,均在两半副作用提交前
  dsp_operand_shape_not_modeled 停。app 真码 .p/copex-as-B 零站点,
  此为完备性守卫。

codex 六坑对照:
①上字非常数 → CopexUpperWord 不透明表示(见上);
②.p 孪生 → step_body 指令级拦截在 upstream pair() 之前,两位置全拒;
③接入点 → 安装检查挂在 probe_boot 整机前沿 CPU 的构造点
  (architectural_k2 同级;probe_pi32v2 的四个函数级探针 harness 不含
  copex 站点,函数级场景不经过本门),拦截门经 hardware.copex_exp22
  槽接进 step_body——EVIMPL/DMAIMPL 同款接线;
④异常路由 → 契约拒绝直接抛 probe_boot.Blocked(上游 effect() 只捕
  KeyError/ValueError;Blocked 穿透为主循环的独立 stop kind,绝不落入
  upstream_simulator_failure);
⑤冲突检查 → assert_copex_exp22_installable(models):上游若将来提供
  copex 模型即报冲突要求重审(不静默让位/遮蔽——静默改变模型含义比
  报错危险);
⑥快照契约 → 新文件进 FINGERPRINT_FILES;M4 族 copex_exp22 计数账本;
  CopexUpperWord 进 world_snapshot 抽象值编解码(寄存器面)。
"""
import math
import struct

WORD_MASK = 0xFFFFFFFF
EXP22 = 22

# 声明域(闭区间):e^x 在 float32 正常态可表出的精确 x 边界。
X_MIN = math.log(2.0 ** -126)                    # ≈ -87.3365 (0xc2aeac50)
X_MAX = math.log((2.0 - 2.0 ** -23) * 2.0 ** 127)  # ≈ +88.7228 (0x42b17218)


class CopexUpperWord:
    """copex dst 上字的不透明结果:数值消费即停,搬运按 unknown 字节对待。

    与 br28_unknown.UnknownWord 的关系:接口同形(bytes/origins),语义
    不同——未知字节的来源不是未初始化物理内存,而是 copex 协处理器在
    pc 处的输出(硬件非常数、历史依赖未排除)。栈搬运走 probe_boot 的
    write_abstract_word 字节路径,落内存后与其它 unknown 字节同表示
    (origins=copex PC);读回构造的是普通 UnknownWord,来源字段如实
    指向该 PC。任何算术/比较/序列化(快照编解码除外)触发
    copex_dst_upper_word_consumed 诚实停。
    """

    __slots__ = ('pc', 'bytes', 'origins')

    def __init__(self, pc):
        self.pc = pc
        self.bytes = (None, None, None, None)
        self.origins = (pc & WORD_MASK,) * 4

    def consumed(self, *_args, **_kwargs):
        from probe_boot import Blocked
        raise Blocked('copex_dst_upper_word_consumed',
                      sources=[hex(a) for a in self.origins],
                      note='the copex destination upper word is hardware-nonconstant '
                           '(captures 0x1ffffff5/0x2b7e14f2, history dependence not '
                           'excluded); numeric consumption is an honest stop')

    __int__ = __index__ = __float__ = __bool__ = __hash__ = consumed
    __eq__ = __ne__ = __lt__ = __le__ = __gt__ = __ge__ = consumed
    __and__ = __rand__ = __or__ = __ror__ = __xor__ = __rxor__ = consumed
    __add__ = __radd__ = __sub__ = __rsub__ = __mul__ = __rmul__ = consumed
    __lshift__ = __rlshift__ = __rshift__ = __rrshift__ = consumed
    __truediv__ = __rtruediv__ = __floordiv__ = __rfloordiv__ = consumed
    __mod__ = __rmod__ = __pow__ = __rpow__ = __neg__ = __pos__ = __invert__ = consumed

    def __repr__(self):
        return 'CopexUpperWord(pc=%#x)' % self.pc


def copex_exp22_lower(x):
    """mode-22 下字:host double exp → float32 就近舍入(近似语义,非硅复刻)。"""
    return struct.unpack('<I', struct.pack('<f', math.exp(x)))[0]


def assert_copex_exp22_installable(models):
    """坑⑤:上游模型面冲突检查(装机点=probe_boot CPU 构造,k2 同级)。

    上游 sim.py 对无模型指令诚实停;本模型的拦截门在 step_body,先于
    upstream models 派发。若上游将来真的提供 copex 模型,本门会静默遮蔽
    它——那比报错危险,所以这里显式报冲突要求重审本开关的取舍。
    """
    conflict = [name for name in ('copex.ppi', 'copex.ppi.p') if models.get(name) is not None]
    if conflict:
        raise RuntimeError(
            'upstream simulator now provides %s — the local mode-22 exponential '
            'approximation (route COPEXIMPL) would shadow it at the step_body gate; '
            're-review the release instead of silently preferring either model'
            % ', '.join(conflict))


class CopexExp22Model:
    """mode-22 指数近似模型的拦截门+账本(经 hardware.copex_exp22 槽接线)。

    intercept(cpu, insn, raw, size, operands) 从 probe_boot CPU.step_body 调用
    (EVIMPL/DMAIMPL 同款);返回 True=指令已完成(finish_exact 已调),调用方
    计 adapter;False=非本门指令,落回常规路径。契约不满足抛 Blocked(坑④:
    独立 stop kind,不经上游 effect() 的 ValueError 路由)。
    """

    STOP_NOTE_OTHER_MODES = ('the other seven app copex modes (24/16/17/23/0/6/7) are '
                             'SDK-named but single-source; each needs its own parameter-'
                             'order/unit/word-shape review before modelling')

    def __init__(self, probe_ul_bias=None):
        # 账本(M4 族 copex_exp22 快照状态):executions/逐次样本(有界,首
        # MAX_SAMPLES 条)/首末样本/模式直方图。停点回合的拒绝也入账(报告
        # 面如实),计数随快照恢复。samples 让「N 次执行是否同一输入形状/
        # 其余站点零触达」可从报告面直接判定(codex 20261118:首末样本
        # 措辞不足以支撑三次同形的声明)。
        # probe_ul_bias(仅 probe_only 敏感性臂,主臂恒 None):下字结果
        # 一次性整体 +bias ulp(CV/ADA 双探针先例,sensitivity-probe-
        # not-plausible-state)——三镜像 parity 只证相对行为不证共同模型
        # 误差,±1 ulp 扰动臂单测模型误差对下游路径的影响;不改变域检查。
        self.probe_ul_bias = probe_ul_bias
        self.executions = 0
        self.samples = []
        self.first = None
        self.last = None
        self.modes = {}

    # ------------------------------------------------------------------ 拦截
    MAX_SAMPLES = 32

    def _note_mode(self, mode):
        self.modes[mode] = self.modes.get(mode, 0) + 1

    def _sample(self, cpu, x_bits, result_bits):
        row = {'pc': hex(cpu.pc), 'core': cpu.core_id,
               'x_bits': hex(x_bits), 'result_bits': hex(result_bits)}
        if self.first is None:
            self.first = row
        self.last = row
        if len(self.samples) < self.MAX_SAMPLES:
            self.samples.append(row)

    def intercept(self, cpu, insn, raw, size, operands):
        """step_body 指令级拦截门;True=已执行完,False=非本门指令。"""
        if insn.id == 'copex.ppi.p':
            self._note_mode(operands(insn, raw)['n'])
            from probe_boot import Blocked
            raise Blocked('dsp_operand_shape_not_modeled', pc=hex(cpu.pc),
                          instruction_id=insn.id,
                          position='parallel_twin_first_half',
                          note='the copex.ppi.p parallel twin dispatches to the base '
                               'form in the upstream pair() path; no hardware capture '
                               'covers copex inside a parallel slot, so both halves are '
                               'rejected before any effect commits')
        if insn.id.endswith('.p'):
            pair_insn, _pair_raw, _pair_size = cpu.decode(cpu.pc + size)
            if pair_insn.id in ('copex.ppi', 'copex.ppi.p'):
                self._note_mode(operands(pair_insn, _pair_raw)['n'])
                from probe_boot import Blocked
                raise Blocked('dsp_operand_shape_not_modeled', pc=hex(cpu.pc),
                              instruction_id=pair_insn.id,
                              position='parallel_second_half',
                              note='copex as the second half of a parallel pair has no '
                                   'hardware capture; rejected before either half commits')
            return False
        if insn.id != 'copex.ppi':
            return False
        from probe_boot import Blocked
        op = operands(insn, raw)
        dst, src, mode = op['r'], op['s'], op['n']
        if mode != EXP22:
            self._note_mode(mode)
            raise Blocked('dsp_mode_not_modeled', pc=hex(cpu.pc), mode=mode,
                          instruction_id=insn.id, note=self.STOP_NOTE_OTHER_MODES)
        src_hi, src_lo = cpu.R[src + 1], cpu.R[src]
        if not (isinstance(src_hi, int) and src_hi == 0):
            raise Blocked('dsp_operand_shape_not_modeled', pc=hex(cpu.pc),
                          instruction_id=insn.id,
                          source_register=src + 1,
                          reason='source upper word is not the SDK zero-extended shape',
                          value=(hex(src_hi) if isinstance(src_hi, int)
                                 else 'unknown:%s' % type(src_hi).__name__))
        if not isinstance(src_lo, int):
            raise Blocked('dsp_operand_shape_not_modeled', pc=hex(cpu.pc),
                          instruction_id=insn.id,
                          source_register=src,
                          reason='source lower word is not a known integer',
                          value='unknown:%s' % type(src_lo).__name__)
        x = struct.unpack('<f', struct.pack('<I', src_lo))[0]
        if not (X_MIN <= x <= X_MAX):      # NaN 比较为 False,一并落域外
            raise Blocked('dsp_domain_boundary', pc=hex(cpu.pc), instruction_id=insn.id,
                          x_bits=hex(src_lo), x=repr(x),
                          domain=[repr(X_MIN), repr(X_MAX)],
                          domain_meaning='e^x representable as a NORMAL float32; '
                                         'subnormal/overflow/NaN hardware behaviour '
                                         'has no capture evidence',
                          note='the app input domain x in [-10.4, +4.5] (eq db2mag) '
                               'sits well inside; an out-of-domain input is a new '
                               'execution shape, not a value to clamp or saturate')
        result_bits = copex_exp22_lower(x)
        if self.probe_ul_bias is not None:
            result_bits = (result_bits + self.probe_ul_bias) & WORD_MASK
        self.executions += 1
        self._note_mode(EXP22)
        self._sample(cpu, src_lo, result_bits)
        # 提交:下字=float32(e^x),上字=不透明结果;flags 保持(捕获共识)。
        cpu.R[dst] = result_bits
        cpu.R[dst + 1] = CopexUpperWord(cpu.pc)
        cpu.finish_exact(cpu.pc + size)
        return True

    # ------------------------------------------------------------------ 报告
    def report(self):
        return {'executions': self.executions,
                'samples': [dict(row) for row in self.samples],
                'first': dict(self.first) if self.first else None,
                'last': dict(self.last) if self.last else None,
                'modes': {str(key): value for key, value in sorted(self.modes.items())}}
