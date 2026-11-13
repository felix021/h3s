#!/usr/bin/env python3
"""Single-source registry for the frontier scenario switches and stop pins.

为什么存在:前沿推进时同一个事实此前要在四处人工同步——probe_irq_entry
的场景开关表与前沿 require 字面值、analyze_image_trace 的 flag 字典与
freshness 钉子、test_image_trace_diff 的期望字面值、HANDOFF 前沿表——
漏改已多次发生。本模块把前三处收敛为单一数据源:每个 opt-in 开关一条
记录(开关名/所属场景栈/依赖/一句话描述),两个前沿契约(步数/停点)单点
存放;三个工具全部从这里派生。HANDOFF 前沿表保持人工(叙事,不进
registry)。

红线(codex 参谋定调,勿破):
- registry 只管「开关/依赖/freshness 契约」,不给寄存器值——寄存器建模
  留在 br28_model;数据 fixture 值(P11_HEAP_INFO_DERIVED 等)只按
  probe_irq_entry 模块常量名引用、由调用方传入命名空间解析,绝不复制
  数值到本文件;
- 测试不全生成——行为断言留在各自工具,这里只单点化前沿字面值;
- 纯数据+最小访问函数,不 import 任何工具模块(避免依赖环);
- 运行时开关校验(require 层叠守卫)留在 probe_irq_entry.irq_case 内,
  本文件的依赖表用于场景集闭包校验与文档,不代替运行时校验。

加新 opt-in 开关时只改这几处:
1. probe_irq_entry.irq_case 加参数(默认 False/None)+ wiring 注释;
2. 本文件 FEATURES 加一条(stack 选 base/conditional/probe_only,
   requires 按层叠关系填,value_switch=True 若非布尔);
3. 若开关推进条件前沿:FRONTIERS['p11_heap_experimental'] 按新停点重钉
   (probe 的前沿 require、analyze freshness、test 期望全部自动跟随);
4. HANDOFF 前沿表人工更新(保持人工,不进 registry)。
场景集闭包/参数一致性由 test_image_trace_diff 调 validate() 钉死:
漏登记(irq_case 有参数而 registry 无记录)或依赖不在栈内都会单测失败。
"""
from collections import namedtuple

# feature id 即 probe_irq_entry.irq_case 的场景参数名(开关名)。
# stack: 'base'=crc_bankb 共享栈成员; 'conditional'=p11_heap 条件栈增量;
#        'probe_only'=只在 probe 自己的场景变体出现(不进两个共享栈)。
# requires: 层叠依赖的开关名元组(与 irq_case 内 require 守卫一致)。
# value_switch: 非布尔开关(显式 fixture/探路值,值由场景调用点传入)。
# value_attr: 条件栈成员的值取自 probe_irq_entry 的该模块常量(只存名字)。
# summary: 一句话描述(证据全文见 probe_irq_entry 对应注释与 HANDOFF)。
Feature = namedtuple('Feature', 'switch stack requires value_switch value_attr summary')

FEATURES = (
    # ---- base 栈(crc_bankb_erased_fixture 场景,19 个开关) ----
    Feature('entry', 'base', (), False, None,
            '软件中断进入投影:RETI 载入被中断 PC,入口银行为显式假设'),
    Feature('banks', 'base', (), False, None,
            '进入投影的寄存器银行切换假设'),
    Feature('idle_wait', 'base', (), False, None,
            'CPU1 idle 等待投影(首次 idle 唤醒路径)'),
    Feature('rti', 'base', (), False, None,
            '对称一次性 RTI 返回投影'),
    Feature('unmaskable', 'base', (), False, None,
            '不可屏蔽中断投影'),
    Feature('sfc_suspend', 'base', (), False, None,
            'SFC suspend 尾部投影(0xfd1c00|=1 与第二 SFC 复位值 0 假设)'),
    Feature('gpio_out_readback', 'base', (), False, None,
            'GPIO 输出寄存器读回投影'),
    Feature('kick_wake', 'base', (), False, None,
            'cpu1_control kick 唤醒投影'),
    Feature('repeat', 'base', (), False, None,
            'repeat_irq_delivery 重复交付投影(handler 返回后重新使能交付)'),
    Feature('hardware_crc', 'base', (), False, None,
            '硬件 CRC 引擎投影'),
    Feature('rep_block', 'base', (), False, None,
            '多指令 rep.i 体执行(immediate_rep_multi_block)'),
    Feature('rep_unknown_transport', 'base', (), False, None,
            'rep 拷贝体未知字节按字节来源运输(rep_copy_unknown_transport)'),
    Feature('icu_region', 'base', (), False, None,
            '两核 ICU region 寄存器组(0xffe400/0xffec00)write-only 投影'),
    Feature('dcu_cmo', 'base', (), False, None,
            'DCU CMO 逐地址 cache 维护口(0xff05c8/0xff05cc)write-only 记录'),
    Feature('zero_rep_skip', 'base', (), False, None,
            '零计数 rep 跳过假设(count=0 体一次不执行)'),
    Feature('erased_bankb', 'base', (), False, None,
            '未验证擦除态 bank B 显式 fixture(四个证据窗 0xFF)'),
    Feature('insert_load_pair', 'base', (), False, None,
            '「装载+组装」并行对执行(exact_parallel_insert_load,旧值对称假设)'),
    Feature('halfword_pair', 'base', (), False, None,
            '并行对半字存储第二半(exact_parallel_halfword_stores 子开关)'),
    Feature('p11_power_init', 'base', (), False, None,
            'power_init 的 P11 依赖链(lpwr 响应/窗口/公共段拷贝/配置 RMW)'),
    # ---- conditional 栈(p11_heap_experimental_config 场景增量,33 个开关) ----
    Feature('heap_info_bytes', 'conditional', ('p11_power_init',), True, 'P11_HEAP_INFO_DERIVED',
            'P11 堆信息 4 字节显式 fixture(静态推导候选,derived-not-measured)'),
    Feature('byte_pair', 'conditional', (), False, None,
            '并行对字节存储第二半(exact_parallel_byte_stores 子开关)'),
    Feature('lp_timer_writes', 'conditional', ('p11_power_init',), False, None,
            'LP 定时器初始化写 write-only 记录(lp_timer_init_projection)'),
    Feature('lrct_trim_done', 'conditional', ('lp_timer_writes',), False, None,
            'JL_LRCT CON 读回 trim 完成投影(bit7 置 1 先例同 SFC PLL-lock)'),
    Feature('lrct_trim_result', 'conditional', ('lrct_trim_done',), True, 'LRCT_TRIM_RESULT_PROBE',
            'JL_LRCT NUM trim 结果探路值 fixture(38400,名义 200kHz LRC 恒等计数)'),
    Feature('timer0_std12m', 'conditional', (), False, None,
            'TIMER0 时钟源字段 5=STD12M 命名+12MHz 速率放行'),
    Feature('lp_timer_con_readback', 'conditional', ('lp_timer_writes',), False, None,
            'P11_LPTMR0 CON 读回=最后写值投影(SDK __RW 先例)'),
    Feature('p33_analog_config', 'conditional', ('p11_power_init',), False, None,
            'P3_ANA_CON2/CON5 合成初始字 0 读回投影(VOLTAGE_CONFIG 先例)'),
    Feature('power_init_rstcon_masks', 'conditional', ('p11_power_init',), False, None,
            'P3_RST_CON0 接受掩码集扩展(0x3fdf/0xFF7F/0x7FBF 三掩码)'),
    Feature('power_init_wkup_dly', 'conditional', ('p11_power_init',), False, None,
            'P3_WKUP_DLY 配置 RMW(&=0x7F8)不经读回放行'),
    Feature('mem_pwr_con', 'conditional', ('p11_power_init',), False, None,
            'MEM_PWR_CON 位 RMW write-only 记录+opaque 死读放行'),
    Feature('p33_window', 'conditional', ('p11_power_init',), False, None,
            'P33 窗无名寄存器族字节写 write-only 记录'),
    Feature('p33_port_sel', 'conditional', ('p11_power_init',), False, None,
            'P3_PORT_SEL0..11 整字写 write-only 记录'),
    Feature('p33_awkup', 'conditional', ('p11_power_init',), False, None,
            'P3_AWKUP 族仅记录写访问+P_IE 首读具名 fixture(整字 0)'),
    Feature('p3_ls', 'conditional', ('p11_power_init',), False, None,
            'P3_LS_P11+P3_LS_IO_DLY..PLL 整字写 write-only 记录'),
    Feature('p11_syscon1_masks', 'conditional', ('p11_power_init',), False, None,
            'P11_SYS_CON1 接受掩码集扩展(0xFFFFFFEF/0xFFFFFF7F)'),
    Feature('fd31_adc_con', 'conditional', ('p11_power_init',), False, None,
            'JL_ADC CON(0xfd3100)整字写 write-only 记录'),
    Feature('fd31_adc_con_readback', 'conditional', ('fd31_adc_con',), False, None,
            'JL_ADC CON 读回=最后写值投影(br28.h __RW)'),
    Feature('fd31_adc_pnd_completion', 'conditional', ('fd31_adc_con_readback',), False, None,
            'JL_ADC CON bit7 PND 无状态读时完成投影(EN=1 后 guest 读见 bit7)'),
    Feature('fd31_adc_res_named_values', 'conditional', ('fd31_adc_pnd_completion',), True, 'FD31_ADC_RES_NAMED_VALUES',
            'JL_ADC RES 具名档位 fixture(derived-not-measured,按模拟复用通道)'),
    Feature('p33_analog_tail', 'conditional', (), False, None,
            'W1[0] 尾段 P3_ANA_CON0/7/8/9/10+P3_CHG_CON0/CON1 容器扩展'),
    Feature('p3_ana_read_ldoin_low', 'conditional', ('p33_analog_tail',), False, None,
            'P3_ANA_READ 比较器常量 0 fixture(电池启动无外部供电锚)'),
    Feature('gpcnt_con_readback', 'conditional', (), False, None,
            'GPCNT_CON 位粒度读回=固件已写位投影(bit0=0 走跳过分支)'),
    Feature('jl_clock_dcck', 'conditional', (), False, None,
            'JL_CLOCK DCCK_CON0/1/2 容器投影(合成初始字 0+读回=当前值)'),
    Feature('jl_uart_con', 'conditional', (), False, None,
            'JL_UART0/1/2 CON 合成 0=空闲读回投影(uart_is_idle bit0)'),
    Feature('jl_uart_init_writes', 'conditional', (), False, None,
            'UART 初始化配置整字写白名单 write-only 记录(CON1/BAUD/...)'),
    Feature('jl_uart_con1_readback', 'conditional', ('jl_uart_init_writes',), False, None,
            'JL_UART CON1 读回=最后写值投影(uart_fifo_reset RMW 放行)'),
    Feature('jl_omap_writes', 'conditional', ('jl_uart_init_writes',), False, None,
            'JL_OMAP 引脚输出复用 49 字白名单 write-only 记录'),
    Feature('jl_imap_writes', 'conditional', ('jl_omap_writes',), False, None,
            'JL_IMAP 外设输入复用 63 字白名单 write-only 记录'),
    Feature('wl_aud_con_writes', 'conditional', ('jl_uart_con1_readback',), False, None,
            'JL_WL_AUD CON0(0xfe2200)整字写记录+读回=最后写值(音频 init 域一)'),
    Feature('audio_dac_init_writes', 'conditional', ('wl_aud_con_writes',), False, None,
            '音频 DAC init 三字(DAC_CON/AUD_CON/JL_ASS CLK_CON)写记录+读回(域二)'),
    Feature('subword_stores', 'conditional', (), False, None,
            '普通子字存储直写(exact_subword_stores,绕过合并读)'),
    Feature('word_store_sp_l', 'conditional', (), False, None,
            '长位移 sp 相对整字存储直写(exact_word_store_sp_l)'),
    Feature('mmu_cross_page_split', 'conditional', (), False, None,
            '跨 128B MMU 页界双字存储拆两半各自翻译(derived-not-measured)'),
    Feature('timer0_irq4_delivery', 'conditional', ('p11_power_init', 'timer0_std12m'), False, None,
            'Timer0 pending→IRQ4 交付机制(进入投影+一次性 RTI)'),
    Feature('core_tick_irq3_delivery', 'conditional', (), False, None,
            'CPU0 core tick 溢出→IRQ3 交付机制(unresolved_overflow 防重入)'),
    # ---- probe_only(只在 probe_irq_entry 场景变体出现,6 个开关) ----
    Feature('timer0_irq4_probe_steps', 'probe_only', ('timer0_irq4_delivery',), True, None,
            'Timer0 首次到期拉近探路 fixture(计数 seed,场景传值)'),
    Feature('timer0_irq4_masked', 'probe_only', ('timer0_irq4_probe_steps',), False, None,
            'Timer0 IRQ4 反事实掩码对照(清 IRQ4 nibble 的惰性演示)'),
    Feature('core_tick_irq3_probe_steps', 'probe_only', ('core_tick_irq3_delivery',), True, None,
            'core tick 首溢出拉近探路 fixture(计数 seed,场景传值)'),
    Feature('core_tick_irq3_masked', 'probe_only', ('core_tick_irq3_probe_steps',), False, None,
            'core tick IRQ3 反事实掩码对照(清 IRQ3 nibble)'),
    Feature('key_chain_scan', 'probe_only', ('p11_power_init', 'fd31_adc_pnd_completion',
                                             'fd31_adc_res_named_values'), True, None,
            '停点后直调键链场景(键分档查询→扫描回调→手势→post)'),
    Feature('key_chain_matrix', 'probe_only', ('p11_power_init', 'fd31_adc_pnd_completion',
                                               'fd31_adc_res_named_values'), True, None,
            '停点后直调三键矩阵多手势场景(路 AJ:3 键 4 档分档器+单击/三击/四击/长按档)'),
)

# 两个共享栈场景(analyze_image_trace.SCENARIOS 同名;probe 场景名在
# probe_scenario 字段)。SCENARIO_LAYERS 声明每层包含的 stack 成员。
PROBE_SCENARIO_NAMES = {'crc_bankb': 'crc_bankb_erased_fixture',
                        'p11_heap_experimental': 'p11_heap_experimental_config'}
SCENARIO_LAYERS = {'crc_bankb': ('base',),
                   'p11_heap_experimental': ('base', 'conditional')}

# 前沿契约单点:各场景 official 复现停点(probe require 断言与 analyze
# freshness 钉子全部派生自这里;前沿推进后只改本处)。
# stop=该场景停点的完整 stop 记录;freshness_fields=analyze freshness
# 钉子检查的 stop 子集字段。
FRONTIERS = {
    'crc_bankb': {
        'completed_steps': 289849,
        'stop_pc': '0x60679ba',
        'stop': {'kind': 'unmapped_memory', 'access': 'read',
                 'address': '0xf27f4b', 'size': 1, 'reference_sdk_register': None},
        'freshness_fields': ('kind', 'address'),
    },
    'p11_heap_experimental': {
        'completed_steps': 3137573,
        'stop_pc': '0x60719c0',
        'stop': {'kind': 'unmapped_or_readonly_write', 'access': 'write',
                 'address': '0xfe0538', 'size': 4, 'value_bytes': '00000000'},
        'freshness_fields': ('kind', 'address'),
    },
}

_BY_SWITCH = {f.switch: f for f in FEATURES}


def switches(layers):
    """有序开关名元组:stack 属于 layers 的全部 feature(注册顺序)。"""
    return tuple(f.switch for f in FEATURES if f.stack in layers)


def _flag_value(feature, values):
    if feature.stack == 'conditional' and feature.value_attr is not None:
        require_attr = getattr(values, feature.value_attr, None)
        if require_attr is None:
            raise AssertionError(
                f'switch {feature.switch!r} needs probe_irq_entry constant '
                f'{feature.value_attr!r}; pass the module as values')
        return require_attr
    return True


def base_flags():
    """base 栈开关 dict(全布尔 True,crc_bankb 场景完整 flag 集)。"""
    return {f.switch: True for f in FEATURES if f.stack == 'base'}


def extra_flags(values):
    """conditional 栈增量开关 dict(value_attr 常量按 values 命名空间解析)。"""
    return {f.switch: _flag_value(f, values) for f in FEATURES if f.stack == 'conditional'}


def scenario_flags(scenario, values=None):
    """一个共享栈场景传给 irq_case 的完整 flag dict(不含传输参数)。
    values=probe_irq_entry 模块对象(或含同名常量的命名空间),用于解析
    非布尔 fixture 开关的常量值;base-only 场景不需要 values。"""
    layers = SCENARIO_LAYERS[scenario]
    flags = {}
    for f in FEATURES:
        if f.stack not in layers:
            continue
        flags[f.switch] = _flag_value(f, values) if f.stack == 'conditional' else True
    return flags


def frontier(scenario):
    """前沿契约记录(拷贝,防调用方误改单点数据)。"""
    record = FRONTIERS[scenario]
    return {'completed_steps': record['completed_steps'],
            'stop_pc': record['stop_pc'],
            'stop': dict(record['stop'])}


def freshness(scenario):
    """analyze_image_trace 的 freshness 钉子 dict(由前沿契约派生)。"""
    record = FRONTIERS[scenario]
    return {'completed_steps': record['completed_steps'],
            'stop_pc': record['stop_pc'],
            'stop_fields': {key: record['stop'][key] for key in record['freshness_fields']}}


def validate():
    """registry 内部一致性(单测调用;违反即 AssertionError):
    开关名唯一、依赖存在且不依赖 probe_only 层、共享栈依赖闭包、
    场景/前沿/字段键对齐、value_attr 只出现在条件栈。"""
    names = [f.switch for f in FEATURES]
    assert len(names) == len(set(names)), 'duplicate switch names in FEATURES'
    stacks = {f.stack for f in FEATURES}
    assert stacks <= {'base', 'conditional', 'probe_only'}, f'unknown stack tier: {stacks}'
    for f in FEATURES:
        for dep in f.requires:
            assert dep in _BY_SWITCH, f'{f.switch} requires unknown switch {dep!r}'
            if f.stack != 'probe_only':
                assert _BY_SWITCH[dep].stack != 'probe_only', \
                    f'shared-stack switch {f.switch} depends on probe_only switch {dep!r}'
        if f.value_attr is not None:
            assert f.stack == 'conditional', f'{f.switch} has value_attr outside the conditional tier'
        if f.value_switch and f.stack != 'probe_only':
            assert f.value_attr is not None, f'{f.switch} is value-taking but has no value_attr'
    # 共享栈依赖闭包:任一场景开启的开关,其依赖也必须在该场景内
    # (probe 侧 irq_case 的 require 守卫保持运行时校验,这里钉场景集本身)。
    for scenario, layers in SCENARIO_LAYERS.items():
        member = set(switches(layers))
        for switch in member:
            missing = [dep for dep in _BY_SWITCH[switch].requires if dep not in member]
            assert not missing, f'{scenario} stack is not dependency-closed: {switch} needs {missing}'
    assert set(SCENARIO_LAYERS) == set(FRONTIERS) == set(PROBE_SCENARIO_NAMES), \
        'scenario names diverge between SCENARIO_LAYERS/FRONTIERS/PROBE_SCENARIO_NAMES'
    for scenario, record in FRONTIERS.items():
        assert record['freshness_fields'], f'{scenario} has no freshness_fields'
        for key in record['freshness_fields']:
            assert key in record['stop'], f'{scenario} freshness field {key!r} not in stop record'
        assert record['stop_pc'] and record['completed_steps'] > 0
    # base 层无依赖(p11_power_init 是条件链根,自身不依赖别的开关)
    for f in FEATURES:
        if f.stack == 'base':
            assert not f.requires, f'base switch {f.switch} declares dependencies'
    return True


validate()
