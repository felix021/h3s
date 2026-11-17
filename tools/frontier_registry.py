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
    # ---- conditional 栈(p11_heap_experimental_config 场景增量,59 个开关) ----
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
    Feature('fd31_adc_res_named_values', 'conditional', ('fd31_adc_pnd_completion',), True, 'FD31_ADC_RES_S19_VALUES',
            'JL_ADC RES 具名档位 fixture(derived-not-measured,按模拟复用通道;路 BM/S19 批 1 勘误'
            '——0x10005 trim 环采样缺行,场景改传 FD31_ADC_RES_S19_VALUES={旧表'
            '+0x10005:400+0x10001:4},BY/键链场景继续用旧表 FD31_ADC_RES_NAMED_VALUES;路 JLEQ 续三'
            '再扩两行 0x1000d TWVD=350(环恒走满端点)/0x1000e PVDD=550(良构数据路径),'
            'W4[4] trim 域第二函数 0x6068662 两处新通道)'),
    Feature('p33_analog_tail', 'conditional', (), False, None,
            'W1[0] 尾段 P3_ANA_CON0/7/8/9/10+P3_CHG_CON0/CON1 容器扩展'),
    Feature('p3_ana_read_ldoin_low', 'conditional', ('p33_analog_tail',), False, None,
            'P3_ANA_READ 比较器常量 0 fixture(电池启动无外部供电锚)'),
    Feature('gpcnt_con_readback', 'conditional', (), False, None,
            'GPCNT_CON 位粒度读回=固件已写位投影(bit0=0 走跳过分支)'),
    # ---- 路 JLEQ 续五:GPCNT 配置字放行。29,544,781 停点 0x60688b6(W4[4]
    # VLVD 域 GPCNT 重配置链消费未知配置位)——读回全字(未知位显式合成
    # 初始 0,AM synthetic-zero 先例)+整字写记录+|=0x1 使能形状;NUM
    # (0xfd1104 __RO)仍严格停;依赖边挂 gpcnt_con_readback=ld.w 拦截语义
    # 前置,执行序在 JL_EQ 族放行之后(推进顺序非硬件依赖)。 ----
    Feature('gpcnt_con_config', 'conditional', ('gpcnt_con_readback',), False, None,
            'GPCNT_CON 配置字放行(29,544,781 停点 0x60688b6;全字读回合成'
            '初始 0+整字写记录+|=0x1 使能形状;计数语义不建模;NUM 严格停)'),
    Feature('gpcnt_con_completion', 'conditional', ('gpcnt_con_config',), False, None,
            'GPCNT_CON bit7 完成位投影(50M 预算耗尽 49,996,010 @ 0x6069546 '
            '稳态测量任务轮询;读侧 OR 0x80=lrct_trim_done/SFC PLL-lock 同模式;'
            '延迟不建模;NUM 读保持严格停=下一前沿测量值决策)'),
    # ---- 路 GPCNUM:GPCNT NUM 具名值放行。29,601,125 停点 0x606955c
    # (任务 0x6069538 首次转换读 0xfd1104=JL_GPCNT->NUM,br28.h __RO)——
    # 三选一 (a) 具名 fixture(codex 参谋采纳+降档):491520000=32768×15000,
    # 商=32768→×1000=32768000 mHz 落在假设标称 32768 Hz 睡眠钟上
    # (assumed-not-measured:无固件侧标称常数可引,弱于 LRCT 闭环;
    # 候选 32000/15360、160000/3072 已记录);值面验证臂内纯数据
    # (20 槽滑动平均→[0x12A3D0]→低功耗漂移补偿)。 ----
    Feature('gpcnt_num_value', 'conditional', ('gpcnt_con_completion',), True,
            'GPCNT_NUM_PROBE',
            'GPCNT NUM 具名探路值(29,601,125 停点 0x606955c 读 0xfd1104;'
            'fixture=15000=491520000/32768,假设标称 assumed-not-measured,'
            'LRCT 38400 同款降档;每次读回同一常量,值下界 1;未提供时严格停)'),
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
    Feature('jl_audio_adc_reset_writes', 'conditional', ('wl_aud_con_writes',), False, None,
            'JL_AUDIO ADC_CON(0xfe0538) reset 写记录+读回=最后写值(域三,0x60719c0 并行对旧 r3 形状)'),
    Feature('jl_adda_daa_con1_initial_fixture', 'conditional', ('jl_audio_adc_reset_writes',), True,
            'JL_ADDA_DAA_CON1_INITIAL_PROBE',
            'JL_ADDA DAA_CON1(0xfe0a04)显式初始字探针 fixture(敏感性探针对 {0,0xfffffff8} 之一,'
            'sensitivity-probe-not-plausible-state,无隐式零默认)'),
    Feature('jl_adda_daa_con1_config_readback', 'conditional', ('jl_audio_adc_reset_writes',), False, None,
            'JL_ADDA DAA_CON1 配置位读回=最后写值(两开关分立;无初值未写入时仍停原边界)'),
    Feature('jl_audio_dac_vl0_readback', 'conditional', ('jl_adda_daa_con1_initial_fixture',), False, None,
            'JL_AUDIO DAC_VL0(0xfe051c)数字音量字写记录+读回=最后写值+无写时合成初始字 0'
            '(读值只进 insert 不进分支,双通道调用下写回恒 0x0;路 CW/S19 批 0b 配置类放行)'),
    Feature('event_clock', 'conditional', ('idle_wait',), False, None,
            '全核 IDLE 事件时钟推进(advance_event_clock;W2[0] os_time_dly 等 tick 等待的首个'
            '消费者,不开则停在首次全核 IDLE;TIMER2 投影本体属批 1)'),
    Feature('subword_stores', 'conditional', (), False, None,
            '普通子字存储直写(exact_subword_stores,绕过合并读)'),
    Feature('word_store_sp_l', 'conditional', (), False, None,
            '长位移 sp 相对整字存储直写(exact_word_store_sp_l)'),
    Feature('ext_pair', 'conditional', (), False, None,
            '并行对 ext.* 传送族前缀(exact_parallel_ext_transfers 子开关;SIMB1F 0x600f95a 假停的读路径解锁)'),
    Feature('postincrement_pair', 'conditional', ('ext_pair',), False, None,
            'ext 传送族∥pi/pin 后增量存储对(exact_parallel_postincrement_stores 子开关;'
            'PARSTOR 留待的 pi·pr 形 6 站点收口,st.w.pin.l 0x6077628/0x60776c2/st.w.pi'
            ' 0x609999a/st.b.pi 0x60b5e66/0x60b6572/st.b.pin 0x60b61ba;writeback 冲突'
            '外延 derived-not-measured)'),
    Feature('postincrement_store_word', 'conditional', (), False, None,
            '独立(非并行)pi/pin 整字存储直写(exact_postincrement_word_stores 子开关;'
            'st.{w}.{pi,pin}+.l 4 形全族,独立 word 形静态 1,045 站点;硬件捕获 53/53;'
            '证据形状 0x60649d0 st.w.pin.l=WDFEED 前沿 41,828,530 停点)'),
    Feature('mmu_cross_page_split', 'conditional', (), False, None,
            '跨 128B MMU 页界双字存储拆两半各自翻译(derived-not-measured)'),
    Feature('timer0_irq4_delivery', 'conditional', ('p11_power_init', 'timer0_std12m'), False, None,
            'Timer0 pending→IRQ4 交付机制(进入投影+一次性 RTI)'),
    Feature('core_tick_irq3_delivery', 'conditional', (), False, None,
            'CPU0 core tick 溢出→IRQ3 交付机制(unresolved_overflow 防重入)'),
    Feature('core_tick_prd_readback', 'conditional', ('core_tick_irq3_delivery',), False, None,
            'core-tick PRD 读回=最近真实写入投影(BQ 一案:unresolved 溢出窗内只放行 offset 8,'
            '回放 tick_timer_init 的 PRD store 1199999;缺失继续严格停;CON/CNT 读与 CNT/pending/'
            'ACK/计时相位全不动;不给默认值;第二案 held 重新仲裁仅在放行后复现漏仲裁才加)'),
    Feature('core_tick_cnt_held_snapshot', 'conditional', ('core_tick_prd_readback',), False, None,
            'core-tick CNT held 快照读(B3 续一案:unresolved 溢出窗内只放行 offset 4,返回模型'
            '实际存储的溢出后冻结快照——显式「溢出后 CNT 冻结」假设 assumed-not-measured,'
            '非 PRD-1/非周期末值/非合成默认;该场景快照=0;读无副作用,CON 读与 pending/ACK/'
            '计时相位全不动;硬件回绕行为无 BR28 证据不声明)'),
    # ---- S19 冲刺批 1(W4[4] 簇,路 BM 总装;四件同 PR) ----
    Feature('jl_audio_dac_cop_writes', 'conditional', ('jl_audio_dac_vl0_readback',), False, None,
            'JL_AUDIO DAC_COP(0xfe0534,+0x34 成员 13 __WO)写侧 write-only 整字记录'
            '(全 app 恰两处纯写 0x7F:W1[3] 尾 0x606ab2c+音频域 0x606e038,零读回;'
            'D2 场景的 RMW/读面属 D1 后另立证据包;路 BM §4.3 行 0b 勘误的写侧三选一)'),
    Feature('jl_audio_dac_dma_writes', 'conditional', ('jl_audio_dac_cop_writes',), False, None,
            'JL_AUDIO DAC DMA 四字(0xfe0504/08/10/14,ADR/LEN/HRP/PNS)写侧 write-only 整字记录'
            '(W1[3] 尾 DAC 启动 0x6071e92..ea8 四写恰 0x18e180 缓冲址+1+0+0,探路实证零读回'
            '零 RMW;D2 的 DMA 指针/计数读属 tone/PCM 推送域 D1 后;路 BM 行 0b 勘误之二)'),
    Feature('jl_pvdd_trim_writes', 'conditional', ('p11_power_init',), False, None,
            'W4[4] PVDD/trim 域容器:P3_ANA_CON13(0xf2c274)/P3_ANA_CON1(0xf2c244)/'
            'P3_PVDD0_AUTO(0xf2c10c) 合成初始字 0+读回=当前值(VOLTAGE_CONFIG/'
            'P33_ANALOG_CONFIG 同族,p33_sfr.h 纯地址宏无类型标注不臆造模拟态);'
            '路 BM 设计'),
    Feature('timer2_delay_projection', 'conditional', ('idle_wait', 'event_clock'), False, None,
            'JL_TIMER2 延时域投影(W4[4] 0x60685fe→0x12cd26 忙等):0xfd0600 族写记录'
            '+LRC 源名义 200kHz 计数器(PRD 跨越置 pending)+CON 读回 bit15+完成唤醒'
            '(kick 先例,锚 0x12cd76,无 IRQ6 交付——ICU 使能无证据)+IWKUP_NUM '
            '0xFFE1B0 写记录;1ms=200 LRC tick=4000 模型步闭式;路 BK 设计'),
    Feature('p33_canary', 'conditional', ('p11_power_init',), False, None,
            'P3_VLVD_CON(0xf2c044)/P3_PCNT_CON(0xf2c124) 金丝雀写记录+首读具名 fixture'
            '(BV 备而未接线;W4[4] 0x12c29a 量测分支首读 0xf2c044——BA §2.2 修正后健康'
            '路径必经;触发即停=未覆盖/写后/重复读/越形写全严格停;路 BM 接线)'),
    Feature('p33_canary_first_read_words', 'conditional', ('p33_canary',), True,
            'P33_CANARY_FIRST_READ_WORDS',
            '金丝雀首读具名字 fixture({0xf2c044:0}=VLVD 未使能 boot 初态,'
            'synthetic-not-measured)'),
    # ---- S19 冲刺批 2 续(路 B3/sim-s19b3:CNT held 快照+JL_ADDA 族+电池段两案) ----
    Feature('jl_adda_analog_family', 'conditional', ('jl_adda_daa_con1_config_readback',), False, None,
            'JL_ADDA 模拟族五字容器(DAA_CON0/DAA_CON2/DAA_CON3/ADDA_CON1/+0x4c 无名;4,521,083 '
            '停点 0x606ac42=模拟 init 函数 0x606abf0 静态全枚举白名单;合成初始 0+读回=当前值,'
            '读值只进写回组装零分支;+4 DAA_CON1 由路 CV 容器独占;DAA_CON2 bit1 状态位消费者'
            '0x606b352 在音频任务域,硅语义推迟未证)'),
    # ---- S19 冲刺批 2 续二(路 DT/sim-dac-tm0:DAC_TM0/TM1 半字 setter 容器) ----
    Feature('jl_audio_dac_tm_readback', 'conditional', ('jl_adda_analog_family',), False, None,
            'JL_AUDIO DAC_TM0/TM1(0xfe0524/0xfe0528)半字 field setter 0x606ab92 容器'
            '(19,139,239 停点 0x606abbe=selector 8/10 handler 的 RMW 内部读;12 路 tbb '
            '0/2→TM0低 1/3→TM1低 8/10→TM0高 9/11→TM1高 4..7→裸rts;读值只进写回组装零分支,'
            '全 app 无 setter 外读者→「getter/硬件时间标记被读」预判撤销;合成初始 0+读回='
            '当前值,初值来源 synthetic_zero 具名登记(codex:保留半字持久携带=CV 性质,无'
            '读者故不单设 fixture 开关;本轮不做 derived 不预埋);恰白名单 setter 静态地址集,'
            '只服务对齐 32 位字,新读者须重审;TM 语义不解释保留原名)'),
    Feature('jl_audio_dac_tm_probe_value', 'probe_only', ('jl_audio_dac_tm_readback',), True, None,
            'DAC_TM0 初值敏感性探针(TM0 初值一次性 0xffffffff 替换,TM1 不扰动保归因干净;'
            '两臂写回断言 0xFDA8_0000/0xFDA8_FFFF 异或 0xFFFF+轨迹全同,CV 双探针先例;'
            'sensitivity-probe-not-plausible-state)'),
    # ---- S19 冲刺批 2 续三(路 ADA/sim-adacon0:JL_ADDA ADA 子族九字容器) ----
    Feature('jl_adda_ada_family', 'conditional', ('jl_audio_dac_tm_readback',), False, None,
            'JL_ADDA ADA 子族九字容器(0xfe0a20..0xfe0a40 ADA_CON0..8;21,798,206 停点 '
            '0x606b5ee=ADA 配置函数 0x606b4d0..0x606c3a8 的 ADA_CON0 bit5 清位 RMW '
            '内部读;静态全图 7 个直接 li 基址站点(9 实例)全部配置 RMW/组装形状、'
            '读值零分支消费(309 RMW+51 ld 逐一核查);合成初始 0+读回=当前值;'
            '0xfe0a44 零触达不进白名单;新开关分立不扩 B3 五字 WORDS(legacy 逐字节'
            '不变红线);依赖边=当前执行路径先后关系(TM 放行后链才到达),非硬件'
            '依赖;codex 定调:九字精确归属、同址新读者须重审、close 族清位服务合成'
            '配置字非硅状态)'),
    Feature('jl_adda_ada_probe_value', 'probe_only', ('jl_adda_ada_family',), True, None,
            'ADA_CON0 初值敏感性探针(初值一次性 0xffffffff 替换只注 0xfe0a20,其余八字'
            '不扰动保归因干净;断言非零合成初值经停点 RMW 只清 bit5——codex 钉子:'
            '仅测初值零会漏掉 RMW 读写不同容器状态的实现错误;sensitivity-probe-'
            'not-plausible-state)'),
    # ---- 路 LPV/sim-lpvad:P11_SFR LPVAD VAD_ACON0 单字容器 ----
    Feature('p11_lpvad_config', 'conditional', ('jl_adda_ada_family',), False, None,
            'P11_SFR LPVAD VAD_ACON0(0xf2b404=p11_sfr_base+map_adr(0x14,0x01))单字容器'
            '(21,798,257 停点 0x606b68e=ADA 配置函数 0x606b4d0 顺序段尾 bit28 置位 RMW '
            '内部读;静态访问面:0xf2b400..0xf2b434 窗全 app 唯一 li 站点 0x606b688、'
            '唯一访问即停点本条,内部读零 GPR/零分支/零 call 参数,窗内无 ld.w 显式读;'
            '数据区无字面值、邻组 0xf2b21c 不触达;RAM 指针/跨函数传参未穷尽如实报);'
            '合成初始 0+读回=当前值(两项均为显式模型假设,非复位值;codex:零消费者'
            '只限已识别主核软件消费者,RMW 自身消费旧值);白名单恰一字,其余 12 字零'
            '触达留 unmapped,同址新读者(新 ld/新 PC/新掩码/其他核)须重审;新开关'
            '分立不扩 p11_window(契约+族冲突,codex 定调);跨域限制:不模拟 q32s 访问/'
            'LPVAD 检测/跨域可见性/副作用;依赖边=执行序前置非硬件依赖'),
    # ---- 路 JLA/sim-jla:JL_AUDIO ADC 通道缓冲配置五字 write-only 记录 ----
    Feature('jl_audio_adc_dma_writes', 'conditional', ('p11_lpvad_config',), False, None,
            'JL_AUDIO ADC DMA 五字(0xfe053c/40/44/48/4c,ADR/LEN/PNS/HWP/SRP)'
            '(21,800,325 停点 0x606df1e=set_buffer 0x606dee4 并行对 store 半写 ADR,'
            'D2-b 蓝图五写之首)写侧 write-only 整字记录(全 app li 枚举+数据流解析'
            '恰 9 条访问=set_buffer 五写+IRQ68 handler 三读一写,后者当前前缀不可达'
            '留严格停;数据区零独立字面值;codex 定调:恰五字、不收 SRN 0xfe0550、'
            '拒绝合成读回=不提前承诺消费语义、同址新写者须重审;依赖边=当前场景'
            '推进顺序非硬件依赖,模型类独立可用)'),
    # ---- 路 ASS/sim-ass:ADC 启动函数配置块四字合成读回容器 ----
    Feature('jl_adc_start_config', 'conditional', ('jl_audio_adc_dma_writes',), False, None,
            'ADC 启动函数 0x606df66 配置块四字(JL_ASS MIC_CTL0 0xfe0904/MIC_CTL1 '
            '0xfe0908+JL_AUDIO MBG_CON 0xfe0554/ADC_CON1 0xfe055c)(21,800,455 停点 '
            '0x606e044=[r0+0]|=0x2 内部读 0xfe0904,r0=r5+976 构造自 0xfe0534 基址'
            '——间接构造地址的首个停点,li 枚举法免疫,基址+偏移传播解析法〔356 li '
            '锚点全 GPR 常量跟踪+寄存器间传播〕扫描)合成配置读回容器:合成初始 0+'
            '读回=当前值(两项显式模型假设 synthetic-not-measured);四字访问面'
            '(按指令站点计 29 个:MIC_CTL0 2/MIC_CTL1 16/MBG_CON 5/ADC_CON1 6)'
            '全部配置 RMW/insert 写回组装形状、读值零分支消费(B3/DT/ADA/LPV 判据;'
            'MIC_CTL1 另有前缀不可达直接 li 族 0x606c8ca=D2-b C2 链 MIC_CTL1 重 '
            'mux,同形状 NOT_RUN,其入册依据=自身静态消费审查〔codex 另核两个直接'
            '调用点 0x606da8e/0x606ec26 无残值消费〕而非 JLA 先例外推);'
            '0xfe0900/0xfe0558 留 AK 容器、SRN 0xfe0550 留 unmapped(JLA 边界)、'
            '0xfe090c/0xfe0910/0xfe0560 零触达不进;数据区零独立字面值(不排除'
            '运行时算出存 RAM 的指针来源,如实报);同址新读者(新 ld/新 RMW 掩码/'
            '新 PC/其他核)须重审——地址白名单只拦地址非运行时阻断,属维护约束;'
            '依赖边=当前场景推进顺序(JLA 放行后链才到达停点)非硬件依赖,模型类'
            '独立可用;codex 定调 gpt-6-astra:选 (a) 恰四字,缩 (b) 不解决额外'
            '语义疑问只重复停,敏感性探针见 jl_adc_start_probe_value)'),
    Feature('ancif_official_resources', 'conditional', ('jl_adc_start_config',), True, 'ANCIF_OFFICIAL_RESOURCES',
            'flash 顶 sdfile ANCIF/ANCIF1 文件头官方内容 fixture(28,764,184 停点'
            '0x606c5ba 读 0x61fbfe0=app 分区表项 ANCIF virt 0x1FE000 经 0x6010c20'
            '换算对 [0x127290]=0x2020/[0x127294]=0x06000000 的头地址;表在 flash'
            '0x06000080 镜像内,内容在 UFW 外层 type50 资源 0x102120/0x102220,'
            '外层/内层 CRC+魔数 0x9881/0x98C1+头内名 ANCGAIN01/ANCCOEF01 与消费'
            '者 0x606c54e parse 期望逐项互证)readonly 映射 [0x61fbfe0,+0x100)+'
            '[0x61fc0e0,+0xac8);显式假设=目标分区携带官方 UFW 资源内容'
            '(official-resource-content,非设备实测——usb-recovery 实测布局标'
            '「保留校准」,真机可能含每机差异),场景结论条件通过;挂载自校验'
            '(长度/魔数/CRC 失败即拒);ANCIF1 只映射有效长不补 0xF00 声明容量;'
            '依赖边挂 jl_adc_start_config=场景推进顺序(ASS 放行后链才到达停点)'
            '非硬件依赖;同址新读者(头范围外/写者/其他核)须重审'),
    # ---- 路 B2G/sim-b2g:B2R 案 1 提层(probe_only→conditional)。B2R 落地时
    # 登记 probe_only 的理由:S15 邮箱首停 0x12CC94 在当时前沿(flash 顶窗口
    # 0x606c5ba,FLS 在途)之后,条件栈成员被 scenario_flags 自动置真会破坏
    # official 快照钉值;FLS 合并(前沿 28,890,702 @ 0x6011976,随机种子读)
    # 后提层——S15 消费者仍在停点之后,提层实测停点/步数逐值不变(容器在
    # 当前前缀零触达,条件场景报告新增 p2m_mailbox 状态键,零事务)。 ----
    Feature('p2m_mailbox_ack', 'conditional', ('p11_power_init',), False, None,
            'P11 邮箱 ack 完成投影(BM §3 规格:S15/S17 喂狗 0x12cc7e——M2P_INT_SET kick '
            '0x400→P2M_INT_PND bit12 置位+P2M+3 回声=M2P+7 值;P2M_INT_CLR 0x1000 清位;lpwr '
            '请求-响应同款,即时无延迟,synthetic-not-measured;三勘误后的正确地址 '
            '0xF27F43/0xF27F87/0xF2A618/0xF2A60C/0xF2A608;首版 0b875ed,B2R 重落地)'),
    # ---- 路 WDFEED/sim-wdfeed:三选一 (b) 喂狗副作用补全。32,737,356 @
    # 0x12c5ca 的 watchdog_expired_delivery_not_implemented(CON 0x2e/
    # time_code 14/elapsed 16384)根因=喂狗事务的请求/ack 面已建模、P11 侧
    # feed 副作用缺失:主核 init 后 WDT 接口恰一次直接 feed(command_values
    # 末项 0x1002E),此后全部喂狗走邮箱,P11(q32s 不执行)的 WDT KEY/CON
    # 写不可见,计数器自 init 起必然到期。放行=事务闭合(回声匹配 kick 时刻
    # 锁定的消息字节 8+固件 CLR)时计数器重装;证据=喂狗函数成功判据恰为
    # 回声匹配+CLR(0x12ccac/0x12ccb0,s19-final-specs §3.1)+p11_code.bin 三处
    # WDT KEY 字面量 0x048c159d(p11_local 0x2e2/0x8c4/0x20cc)+真机 park 长期
    # 存活;假设显式:P11 消息 8 处理器重装 WDT 未执行验证,提交点=事务闭合
    # 是记账时刻非物理喂狗时刻。(a) 到期交付不采纳:真硅到期=复位、无向量
    # 槽、稳态不发生;(c) 不需要:节拍无漂移、时钟标称值只定墙钟刻度。
    # codex 参谋全文 /tmp/wdfeed-codex.md(裁定 (b)+四点吸收)。----
    Feature('wdt_mailbox_feed', 'conditional', ('p2m_mailbox_ack',), False, None,
            '喂狗邮箱事务闭合→P11 侧 WDT feed 副作用投影(计数器重装;显式假设='
            'P11 成功处理消息 8 重装所建模 WDT,q32s 未执行;提交点=回声匹配+CLR'
            '与固件自身成功判据重合;已到期不补喂;watchdog 报告 mailbox_feed_'
            'side_effects 单列,feeds 保持总数)'),
    # ---- 路 B2G/sim-b2g:B2R 案 2 提层(0b875ed 原案顺序:案 1 之后)。B2R
    # 落地时登记 probe_only 的理由:解析器 0x60010b4(W1[4]/S9 域)在当时前沿
    # (flash 顶窗口 0x606c5ba,FLS 在途)之后,进条件栈会改共享场景报告形状;
    # FLS 合并(前沿 28,890,702 @ 0x6011976,随机种子读)后提层——解析器入口
    # 仍在停点之后,提层实测停点/步数逐值不变(PC 锚定安装钩子零触发,机器
    # 报告新增 cfg523_named_fixture_projection 状态键,installed_at=None)。 ----
    Feature('cfg523_named_fixture', 'conditional', (), False, None,
            'config-523 分档器产物对象具名 fixture(70B=CFG523_OBJECT_BYTES PC 锚定首装 '
            '0x1274F4,解析器 0x60010b4 首次执行前写入→幂等早退返回对象;S9 注册/S17 '
            '健康门/S19 后键扫三读者共用;通道字 0x10001 mux1;释放态 ≤9 修正的配套 '
            'RES 行 0x10001=4 已在 BM 批 1 S19 值表;synthetic-not-measured——真实 '
            'config 523 在 bank B 记录区,公开工件外;BH §4.2 设计;首版 0b875ed,'
            'B2R 重落地)'),
    # ---- 路 RAND/sim-rand:JL_RAND->R64L(0xfd3b00)熵源种子 fixture。熵源类
    # 停点(EFUSE 页「显式 synthetic fixtures,绝不臆造 allF/all0」先例):
    # 熵机制无文档、值不可推导(synthetic-not-measured),主臂 0x12345678
    # (Lehmer MINSTD 首步 state=0x2a18e99e,6 字节 MAC 输出非退化);退化种子
    # 0/0x7FFFFFFF/0xFFFFFFFE=LCG 不动点(全零 MAC)主臂避开;R64H(0xfd3b04)
    # 零触达不进容器;依赖边挂 ancif_official_resources=场景推进顺序(ANCIF
    # 放行后链才到达停点)非硬件依赖;固定种子对 BT 栈随机槽分配消费者
    # (0x6055f68 R64L%79 重试环)可能停滞,前沿外消费者到达时须重审。 ----
    Feature('jl_rand_seed', 'conditional', ('ancif_official_resources',), True,
            'JL_RAND_SEED_BYTES',
            'JL_RAND->R64L(0xfd3b00)熵源种子 fixture(readonly 单字,每次读同一冻结常量'
            '=constant_on_every_read;synthetic-not-measured,非设备实测;熵机制无文档、'
            '值不可推导;R64H 0xfd3b04 零触达不进容器)'),
    # ---- 路 JLEQ/sim-jleq:EQ 初始化两字 write-only 记录。codex gpt-6-astra
    # 三选一定调选 (c) 恰两字 write-only(拒绝全族八字读回容器:当前推进零读,
    # 读回+合成初值会提前承诺未建立的消费语义;六字 0xfe1504..0xfe1518 零
    # 访问证据不得凭 __RW 推成可读回);两写无条件整字 st.w(codex 勘误
    # 0x6072b68 bnez 目标=0x6072b90 只跳分配+首次 memset,PARKPRE 观察项的
    # 「CON0 写臂被跳」表述撤销);敏感性定调=无 ones 臂(纯 st.w 不读旧值),
    # 主臂断言 read_attempts=0+负例关停停 29,215,313;JL_EQ 是数据驱动地址族
    # (基址仅 .data 0x60c3748 一处、零 li 常量、经 [0x12727C]+16 间接装载,
    # S19MAP #7 漏判根因);依赖边挂 jl_rand_seed=场景推进顺序非硬件依赖。 ----
    Feature('jl_eq_init_writes', 'conditional', ('jl_rand_seed',), False, None,
            'EQ 初始化函数 0x6072b24 两字(JL_EQ->CON0 0xfe1500/FLT_ADR 0xfe151c)'
            'write-only 整字记录(29,215,313 停点 0x6072b92 写 CON0=0 放行;boot '
            '路径只写不读,两写无条件整字 st.w 无 RMW;读侧显式严格停,六字 '
            '0xfe1504..0xfe1518 与 0xfe1520 起零触达留 unmapped;JLA/ADC-DMA '
            'write-only 先例,codex 定调;数据驱动地址族,li 枚举法免疫)'),
    # ---- 路 JLEQ 续二:core tick freerun 边沿周期使能放行。29,229,183 停点
    # 0x6065834=tick 重启函数 0x6065806 尾(固件形状 CON=0→CNT=0→PRD=
    # 0xFFFFFFFF→CON=1,收割 µs 存 [0x1265C8] 后切自由运行 µs 计数器);
    # 恰放行 prd==0xFFFFFFFF 一格(prd==0 恒拒),计数公式 period=prd+1=2^32
    # 不变、任何受建模视界内零到期;读回/ACK/pending 语义全不动;硅上最大
    # 周期行为未测量 assumed-not-measured;依赖边挂 jl_eq_init_writes=场景
    # 推进顺序非硬件依赖。 ----
    Feature('core_tick_freerun_edge_period', 'conditional', ('jl_eq_init_writes',), False, None,
            'core tick PRD=0xFFFFFFFF 边沿周期使能放行(tick 重启函数 0x6065806 尾'
            '0x6065834 停点;恰放行 prd==0xffffffff 一格,prd==0 恒拒;模型公式 '
            'period=prd+1=2^32 不变零到期;assumed-not-measured;依赖边=推进顺序'
            '非硬件依赖)'),
    # ---- 路 D2AI/sim-d2ai:ADC DMA 交付半边(模型件+IRQ68 进入,预研
    # docs/d2-audio-input-pre-study.md §7 步 1;INJ-01/INJ-02 执行载体)。----
    Feature('audio_adc_dma_delivery', 'conditional', ('core_tick_freerun_edge_period',), False, None,
            'JL_AUDIO ADC DMA 五字交付半边扩类(读回投影:五字最后写值/HWP=模型'
            '写指针/SRN 0xfe0550 并入白名单;ADC_CON bit7 pending 读侧叠加+'
            '|=0x40 W1C 联动;produce_block=DMA 完成事件投影,唯一合成输入=样本'
            '字节,窗口/块长按固件 set_buffer 写下的 ADR/LEN/PNS 推导;advance() '
            'IRQ68 扫描逐 pending 片至多一次 IrqDeliveryRequest;机器侧最小进入/'
            'RTI 投影镜像 Timer0,嵌套仅限 timer0 在途且真实 ICFG 优先级严格更高'
            '(park 稳态 prio2>prio1);DMA 完成时序=投影;依赖边=场景推进顺序'
            '非硬件依赖,前置五字容器+ADC reset 写域)'),
    # ---- 路 D2ANC/sim-d2anc:JL_ANC 硬件滤波块 44 字容器(INJ-05 ANC 场景
    # 的寄存器写记录面)。22 字=全 app li 常量→寄存器传播扫描钉死;另 22 字=
    # 表驱动滤波配置族(0x606cfb2 域)的运行时变址触达(寄存器索引来自滤波
    # 模式表,静态不可枚举;D2ANC 重配链直调逐墙钉死并入,并入后 0x606ee26
    # (param,0) 5839 步完整走通),窗外零触达字留 unmapped;读值只进写回组装
    # (AM 判据);boot 前缀零触达(D2AUD §6.2)=开启不动前沿;滤波硬件语义/
    # IRQ67 交付不建模。 ----
    Feature('jl_anc_writes', 'conditional', ('audio_adc_dma_delivery',), False, None,
            'JL_ANC 硬件滤波块 44 字容器(0xfe0400..0xfe04f4,停-重配-启链'
            '0x606e9e8/0x606ebe0+滤波配置族 0x606cc36/0x606ce02/0x606d9c0/'
            '0x606cfb2/0x606da1e 的写记录+读回=当前值,合成初始 0;'
            'AM/ADA 先例;前缀零触达,只服务 INJ-05 场景 post_stop 直调段;'
            '依赖边=场景先后关系非硬件依赖)'),
    Feature('jl_clock_con3_readback', 'conditional', ('jl_anc_writes',), False, None,
            'JL_CLOCK CLK_CON3(0xfd0024)单字容器(JL_ASS 时钟函数 0x606c844 的'
            'ADC 时钟选择字,10 站点读值只进写回;合成初始 0+读回=当前值;'
            'AM 族;前缀零触达,只服务 ANC 场景;依赖边=场景先后关系非硬件'
            '依赖)'),
    Feature('jl_adc_start_probe_value', 'probe_only', ('jl_adc_start_config',), True, None,
            'MIC_CTL0(0xfe0904)初值敏感性探针(初值一次性 0xffffffff 替换,其余三字'
            '不扰动保归因干净;零臂写回 0x2/ones 臂写回 0xfffffffe 异或保留位;'
            'codex 钉子:「只进写回」≠初值无影响,未覆盖位保留进写事务,仅测初值'
            '零会漏掉 RMW 读写不同容器状态的实现错误;探针过只支持所测软件路径'
            '稳定,不证明硬件初值无关;CV/ADA 双探针先例;sensitivity-probe-not-'
            'plausible-state)'),
    # (p2m_mailbox_ack / cfg523_named_fixture 曾按 BQ 零消费者先例于 1103fa8
    # 从条件栈撤回(19,139,239 停点证实消费时序在音频数字域之后);音频域
    # 过界后由路 B2R 从 0b875ed 重落地到 probe_only 层,FLS 合并后由路
    # B2G 按到达顺序提层回 conditional(见条件栈尾两块)——提层实测当前
    # 前沿停点/步数不变,两消费者(S15 邮箱/S17 cfg523)都在停点之后。)
    # ---- probe_only(只在 probe_irq_entry 场景变体出现,10 个开关) ----
    Feature('timer0_irq4_probe_steps', 'probe_only', ('timer0_irq4_delivery',), True, None,
            'Timer0 首次到期拉近探路 fixture(计数 seed,场景传值)'),
    Feature('timer0_irq4_masked', 'probe_only', ('timer0_irq4_probe_steps',), False, None,
            'Timer0 IRQ4 反事实掩码对照(清 IRQ4 nibble 的惰性演示)'),
    Feature('core_tick_irq3_probe_steps', 'probe_only', ('core_tick_irq3_delivery',), True, None,
            'core tick 首溢出拉近探路 fixture(计数 seed,场景传值)'),
    Feature('core_tick_irq3_masked', 'probe_only', ('core_tick_irq3_probe_steps',), False, None,
            'core tick IRQ3 反事实掩码对照(清 IRQ3 nibble)'),
    Feature('core_tick_cnt_snapshot_probe_value', 'probe_only', ('core_tick_cnt_held_snapshot',), True, None,
            'core tick CNT held 快照敏感性探针值(unresolved 窗内 CNT 读改返探针值'
            '——0 vs 周期末值 PRD-1=1199998 两分叉对照,CV 双探针先例;sensitivity-'
            'probe-not-plausible-state,记录观察路径首次分叉)'),
    Feature('key_chain_scan', 'probe_only', ('p11_power_init', 'fd31_adc_pnd_completion',
                                             'fd31_adc_res_named_values'), True, None,
            '停点后直调键链场景(键分档查询→扫描回调→手势→post)'),
    Feature('key_chain_matrix', 'probe_only', ('p11_power_init', 'fd31_adc_pnd_completion',
                                               'fd31_adc_res_named_values'), True, None,
            '停点后直调三键矩阵多手势场景(路 AJ:3 键 4 档分档器+单击/三击/四击/长按档)'),
    Feature('d1_acceptance', 'probe_only', ('event_clock', 'timer0_irq4_delivery',
                                            'core_tick_irq3_delivery', 'repeat'), False, None,
            'D1 验收观察窗场景(park 后 N 模型步,断言 D1-1..5,含 H8 生产者自检'
            '与正式验收入口;d1-acceptance §1;d1-scene-blueprint;'
            'd1-window-producer-spec)'),
    Feature('d1_window_steps', 'probe_only', ('d1_acceptance',), True, None,
            'D1 观察窗模型步预算(默认=max(S_tick+160k,200k),H8 自检不足自动延长;'
            '显式值覆盖且不延长)'),
    Feature('d1_post_inject', 'probe_only', ('d1_acceptance',), True, None,
            "park 后事件注入变体('s18_18' 直调 0x6000cba,显式合成假设,默认关;"
            '行为实现归 PR-4)'),
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
        'completed_steps': 41847303,
        'stop_pc': '0x6019458',
        'stop': {'kind': 'unmapped_memory', 'access': 'read',
                 'address': '0xfd610c', 'size': 4, 'reference_sdk_register': None},
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
