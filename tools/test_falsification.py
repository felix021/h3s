#!/usr/bin/env python3
"""Falsification counterexample suite (route AZ/sim-falsify).

方法(codex 全局复盘第 3 路「独立反例验证」):对高风险投影故意破坏一个
被依赖的输入——开关错置(依赖层叠撤掉)/fixture 撤掉或给错值/必要 IRQ
屏蔽/阈值档位破坏——然后断言机制**按预期失败**(显式拒绝、诚实停点、
观察结果改变)。反例「仍通过」= 原测试假阳性(盲区),记录进
docs/falsification-checks.md。每个反例测试的通过条件本身就是「破坏被抓住」。

两层:
- 快速单测层(本文件直接运行,已接进 check_local.py --units):接线守卫、
  Timer0 计数守恒/停止纪律、跨页拆分哨兵、键链常量前置守卫、post_stop
  外设时钟冻结。不需要私有 ROM;除 bus/post_stop 两组外也不需要模拟器。
- 慢场景层(opt-in `--scenario-evidence --rom ROM`,不在 check_local 内):
  用被破坏的开关复跑大场景,证明前沿通过 genuinely 依赖该投影。
  见 docs/falsification-checks.md 的运行记录。
"""
import argparse
import json
import struct
import sys
import unittest
from pathlib import Path

sys.path.insert(0,str(Path(__file__).resolve().parent))

from br28_model import BR28Model, Blocked, TIMER0, IRQ_CONFIG  # noqa: E402
from probe_boot import ENTRY, IrqDeliveryRequest, application, explore  # noqa: E402
from probe_boot_handoff import SENTINEL_A, package_header, profile  # noqa: E402
from probe_pi32v2 import ROOT, upstream  # noqa: E402


# ---------------------------------------------------------------------------
# AZ-1 接线守卫反例族:场景参数错置必须在任何仿真开始前被拒绝。
#
# 破坏方式=把某投影打开而撤掉它的层叠依赖(或撤掉值 fixture 的关键行)。
# 期望=irq_case 的 require 守卫立即 ValueError,**绝不进入 dual_case 仿真**
# (传一个任何属性访问都爆炸的假 module 作为证明:若守卫漏了,假 module 会
# 先炸出「unexpected simulator use」而不是接线错误)。对照组(banks 无
# entry)证明未破坏时执行确实能越过全部接线守卫、深入到机器构造——
# 否则整组「被抓住」是空真的。
class _SimulatorBomb:
    """任何属性访问都失败:证明被测路径没有触碰仿真器/镜像。"""
    def __getattr__(self,name):
        raise AssertionError(f'unexpected simulator use: {name}')


class WiringGuardFalsificationTests(unittest.TestCase):
    """irq_case 参数错置反例:每个撤掉依赖的开关组合必须被接线守卫拒绝。"""
    CASES = (
        # (说明, kwargs, 期望 ValueError 文案子串)
        ('fd31 CON 读回撤掉 CON 写记录投影',
         dict(p11_power_init=True,fd31_adc_con=False,fd31_adc_con_readback=True),
         'the fd31 ADC CON readback only layers on the fd31 ADC CON projection'),
        ('fd31 PND 完成撤掉 CON 读回',
         dict(p11_power_init=True,fd31_adc_con=True,fd31_adc_con_readback=False,
              fd31_adc_pnd_completion=True),
         'the fd31 ADC PND completion only layers on the CON readback projection'),
        ('fd31 RES 具名值撤掉 PND 完成',
         dict(p11_power_init=True,fd31_adc_con=True,fd31_adc_con_readback=True,
              fd31_adc_pnd_completion=False,fd31_adc_res_named_values={0x10000:400}),
         'the fd31 ADC RES named values only layer on the PND completion projection'),
        ('键链场景的 RES fixture 缺键通道行(参数错置)',
         dict(p11_power_init=True,fd31_adc_con=True,fd31_adc_con_readback=True,
              fd31_adc_pnd_completion=True,fd31_adc_res_named_values={0x10000:400},
              key_chain_scan=True),
         'the key-chain RES fixture must cover the key channel word'),
        ('键链与矩阵场景同时开(一个 post_stop)',
         dict(p11_power_init=True,fd31_adc_con=True,fd31_adc_con_readback=True,
              fd31_adc_pnd_completion=True,
              fd31_adc_res_named_values={0x10000|1:700,0x10000:400},
              key_chain_scan=True,key_chain_matrix=True),
         'key_chain_scan and key_chain_matrix are separate scenarios'),
        ('Timer0 交付撤掉 STD12M 计数前缀',
         dict(p11_power_init=True,timer0_std12m=False,timer0_irq4_delivery=True),
         'the Timer0 IRQ4 delivery only layers on the STD12M counting prefix'),
        ('Timer0 掩码探针撤掉到期探针',
         dict(p11_power_init=True,timer0_std12m=True,timer0_irq4_delivery=True,
              timer0_irq4_masked=True),
         'the Timer0 IRQ4 masked probe requires the expiry probe'),
        ('lp_timer CON 读回撤掉写记录',
         dict(p11_power_init=True,lp_timer_writes=False,lp_timer_con_readback=True),
         'the LP-timer CON readback only layers on the LP-timer write records'),
        ('UART CON1 读回撤掉 init 写白名单(路 AA 依赖边)',
         dict(jl_uart_init_writes=False,jl_uart_con1_readback=True),
         'the JL_UART CON1 readback projection requires the route-M init write records'),
        ('堆信息 fixture 撤掉 power_init 前缀',
         dict(p11_power_init=False,heap_info_bytes=b'\x00'*4),
         'heap-info fixture only layers on the p11_power_init scenario'),
    )

    def test_dependency_sabotage_is_refused_before_any_simulation(self):
        from probe_irq_entry import irq_case
        for label,kwargs,message in self.CASES:
            with self.subTest(sabotage=label):
                with self.assertRaisesRegex(ValueError,message):
                    irq_case(_SimulatorBomb(),b'',b'',b'','sabotaged_wiring',**kwargs)

    def test_control_unsabotaged_wiring_reaches_the_machine_constructor(self):
        # 对照组:最小无破坏组合(banks=True 而 entry=False 的错误属于
        # DualCoreMachine 构造器,深在 dual_case 内)必须越过全部接线守卫——
        # 证明上一组的「被抓住」不是「永远拒绝」的空真。
        from probe_irq_entry import irq_case
        with self.assertRaisesRegex(ValueError,
                                    'entry bank assumption requires the entry projection'):
            irq_case(_SimulatorBomb(),b'',b'',b'','control_wiring',
                     entry=False,banks=True)


# ---------------------------------------------------------------------------
# AZ-2 Timer0 计数守恒 + 停止纪律(codex 原型「停止后事件是否还在增长」)。
#
# 守恒律(两级分数换算 + 分频 + 计数三级 telescoping,与 advance() 逐步
# 互逆):3*Σ源tick + rem_src₀ = 4*T + rem_src(T=换算后 tick 数);
# T + rem_d₀ = div*U + rem_d(U=分频后 tick 数);
# cnt₀ + U = cnt + (prd+1)*expirations。任何一级泄漏/幻影增长都会破坏等式。
class Timer0ConservationTests(unittest.TestCase):
    @staticmethod
    def _armed_timer(prd,con=0x1401):
        from br28_model import Timer0
        t=Timer0((4,5))
        t.write(0,0x4000);t.write(4,0);t.write(8,prd);t.write(0,con)
        return t

    def _conservation(self,con,prd,advances):
        """三级独立账本与 Timer0.advance() 逐步互证;pending 片间做真实
        W1C 形状(0x4000)清 pending 进下一片。任何一级的幻影增长/泄漏
        (包括 W1C、pending、in-flight 期间)都会打破等式。"""
        from br28_model import DIVISORS
        divisor=DIVISORS[(con>>4)&15]
        std12m=(con>>10)&15==5
        t=self._armed_timer(prd,con)
        cnt0=t.cnt
        fed=0
        for i,source_ticks in enumerate(advances):
            t.advance(source_ticks)
            fed+=source_ticks
            if std12m:                       # 第一级:12/16 换算,分数余数
                converted,rem_src=divmod(3*fed,4)
            else:
                converted,rem_src=fed,0
            ticks,rem_div=divmod(converted,divisor)   # 第二级:分频
            with self.subTest(step=i,con=hex(con)):
                self.assertEqual(t.source_remainder,rem_src)
                self.assertEqual(t.remainder,rem_div)
                # 第三级:cnt + (prd+1)*到期数 == 初值 + 分频后 tick 数。
                self.assertEqual(cnt0+ticks-(prd+1)*t.expirations,t.cnt)
            if t.pending:                    # 真实 handler W1C 形状清 pending
                t.write(0,con|0x4000)
                self.assertFalse(t.pending)
        return t

    def test_std12m_ticks_are_conserved_across_pending_and_w1c_episodes(self):
        # 前沿形状(source5/div1/prd119999):一个完整周期 + 第二片途中,
        # 步长混入 768(测试里出现的另一档源 tick 批量)。
        t=self._conservation(0x1401,119999,[4]*36779+[4]*100+[768]*20+[4]*20000)
        self.assertGreaterEqual(t.expirations,1,'a full period must have expired')

    def test_source4_with_divisor_is_conserved(self):
        # legacy source4 + 真实分频(0x1021:div 字段 2)下同一守恒律,
        # 覆盖换算级关闭时的两级账本。
        t=self._conservation(0x1021,50,[4]*40+[768]*8)
        self.assertGreaterEqual(t.expirations,1)

    def test_stop_is_deterministic_and_consumption_is_explicit_not_phantom(self):
        # 交付请求边界:in-flight 期间 advance 不再抛但计数照走(周期定时器
        # 语义,显式投影;每模型步 3 个 STD12M 计数);W1C 解除 in-flight。
        # 反例视角:不存在「不调 advance 也增长」的幻影路径——增量全部
        # 可归因到显式 advance 调用(守恒测试),这里钉停止边界的确定性。
        h=BR28Model(initial_irq_config=[0]*32,initial_ipmask=0,
                    timer0_std12m_source=True,timer0_irq_delivery_requests=True)
        h.write(IRQ_CONFIG,0)
        for address,value in ((TIMER0,0x4000),(TIMER0+4,0),(TIMER0+8,3),(TIMER0,0x1400)):
            h.write(address,value)
        h.write(IRQ_CONFIG,0x30000)  # IRQ4 使能+优先级1(真实运行态 nibble)
        h.write(TIMER0,0x1401)       # STD12M 启动;每步 3 计数,prd+1=4
        h.advance(0x300)             # cnt=3,未到期
        self.assertFalse(h.timer.pending)
        with self.assertRaises(IrqDeliveryRequest) as caught:
            h.advance(0x300)         # cnt=6 → 首次到期,pending+合格 → 请求交付
        self.assertEqual((caught.exception.details['core'],caught.exception.details['irq']),(0,4))
        cnt_at_request=h.timer.cnt
        h.advance(0x300)             # in-flight:不重抛;显式消耗 3 计数
        consumed=(h.timer.cnt+4*h.timer.expirations)-(cnt_at_request+4*1)
        self.assertEqual(consumed,3,'one model step must consume exactly 3 STD12M ticks')
        self.assertTrue(h.timer.pending)
        h.write(TIMER0,0x1401|0x4000)  # 真实 handler W1C 形状
        self.assertFalse(h.timer.pending)
        self.assertFalse(h.timer.delivery_in_flight)

    def test_strict_stop_is_documented_non_idempotent(self):
        # 盲区钉死(非仓库 bug,纪律在运行循环):默认严格世界里 pending+
        # 合格 → Blocked(interrupt_delivery_not_implemented)。模型在抛出前
        # 已消费本步计数(定时器推进先于交付检查),所以「停后再调
        # advance」会在再次抛出前继续走表。诚实地把这一点作为已知边界
        # 记录:任何在停止后重试 advance 的 harness 都会静默烧掉定时器
        # 计数——docs/falsification-checks.md 盲区 B-1。
        h=BR28Model(initial_irq_config=[0]*32,initial_ipmask=0,
                    timer0_std12m_source=True)
        h.write(IRQ_CONFIG,0)
        for address,value in ((TIMER0,0x4000),(TIMER0+4,0),(TIMER0+8,3),(TIMER0,0x1400)):
            h.write(address,value)
        h.write(IRQ_CONFIG,0x30000)
        h.write(TIMER0,0x1401)
        h.advance(0x300)
        with self.assertRaisesRegex(Blocked,'interrupt_delivery_not_implemented'):
            h.advance(0x300)
        volume=lambda t:t.cnt+4*t.expirations
        volume_after_stop=volume(h.timer)
        with self.assertRaisesRegex(Blocked,'interrupt_delivery_not_implemented'):
            h.advance(0x300)
        self.assertEqual(volume(h.timer),volume_after_stop+3,
                         'documented non-idempotency: the retry consumed 3 timer ticks')


# ---------------------------------------------------------------------------
# AZ-3 跨页拆分(路 AN)哨兵负例增补:已有「两半各自翻译/默认关/第二页
# PTE 无效」三负例(test_boot_bus),这里补:(a) 页表字节零污染(PTE 及
# 其邻字哨兵);(b) 无效第二页时**全部**六个哨兵不污染;(c) 页内双字
# 不走拆分路径(无假拆分);(d) 跨页双字**读** ld.d 两半各取各自翻译
# (上游 m2 语义,读侧本就两独立事务——拆分世界读写对称,读侧无诚实停)。
class CrossPageFalsificationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.module=upstream()
        firmware=ROOT/'firmware/QCY_H3S_7018F_1T2_release_note_V1.1.8_011.ufw'
        cls.app=application(firmware);cls.header=package_header(firmware)

    def handoff(self):
        h=profile(self.header,SENTINEL_A)
        from probe_boot_handoff import RAM_CODE
        dst,src,length=RAM_CODE
        h['seeds'].append((dst,self.app[src-ENTRY:src-ENTRY+length]))
        h['description']['phase']='falsification cross-page sentinel test; isolated window'
        return h

    def cross_page_case(self,*,split=True,valid_second=True,in_page=False,load=False):
        """cross_page_case 的自包含变体(test_boot_bus 模式):MMU 编程窗 +
        一条 st.d/ld.d;页表哨兵(PTE 邻字)+ 目标页哨兵齐备。"""
        from pi32v2_asm import encode
        h=self.handoff()
        h['regions'] += [(0x120000,0x122800,'mmu_page_table'),
                         (0x130000,0x130088,'mmu_test_page_a'),
                         (0x12f000,0x12f020,'mmu_test_page_b')]
        tag_a=0x8000|(0x130000>>7)
        tag_b=(0x8000|(0x12f000>>7)) if valid_second else (0x12f000>>7)
        h['seeds'] += [(0x12002a,struct.pack('<H',tag_a)),
                       (0x12002c,struct.pack('<H',tag_b)),
                       # 页表哨兵:两枚 PTE 前后的邻接 2 字节表项(页表是
                       # 2 字节项;拆分翻译只许读 PTE,不许写任何页表字节)。
                       (0x120028,struct.pack('<H',0x2121)),
                       (0x120030,struct.pack('<H',0x3131)),
                       # 目标页哨兵(同 test_boot_bus 六位置)。
                       (0x130078,struct.pack('<I',0xaaaaaaaa)),
                       (0x13007c,struct.pack('<I',0xbbbbbbbb)),
                       (0x130080,struct.pack('<I',0xcccccccc)),
                       (0x130000,struct.pack('<I',0xdddddddd)),
                       (0x12f000,struct.pack('<I',0xeeeeeeee)),
                       (0x12f004,struct.pack('<I',0xf0f0f0f0))]
        for label,address,length in (('pte_below',0x120028,2),('pte_a',0x12002a,2),
                                     ('pte_b',0x12002c,2),('pte_above',0x120030,2),
                                     ('sentinel_below_low',0x130078,4),('low_half_target',0x13007c,4),
                                     ('contiguous_after_page',0x130080,4),('page_a_head',0x130000,4),
                                     ('high_half_target',0x12f000,4),('sentinel_above_high',0x12f004,4),
                                     ('inpage_low',0x12f00c,4),('inpage_high',0x12f010,4)):
            h['snapshots'].append((label,address,length))
        app=bytearray(self.app)
        base=ENTRY+len(app)-32
        tail=(encode('ld.d.sp.l',r=2,n=2) if load else encode('st.d.sp.l',r=0,n=2))
        window=b''.join((encode('st.w.l',r=4,n=1,s=5),
                         encode('st.w.l',r=4,n=2,s=6),
                         encode('st.w.l',r=4,n=0,s=7),
                         tail))
        app[base-ENTRY:base-ENTRY+len(window)]=window
        regs=[0]*16;regs[0]=0x55667788;regs[1]=0x11223344
        regs[2]=0x0bad0bad;regs[3]=0x0bad0bad
        regs[4]=0xff0400;regs[5]=0x120000;regs[6]=0x1227ff;regs[7]=1
        sp=0x400af4 if not in_page else 0x400b04   # 跨页(afc+8)/页内(b0c..b13)
        diagnostics={'entry_pc':base,'initial_sp':sp,'initial_gprs':regs,
                     'exact_parallel_stores':True,'exact_doubleword_stores':True}
        if split:diagnostics['mmu_cross_page_split']=True
        result=explore(self.module,bytes(app),name=f'az_cross_page_{"load" if load else "store"}'
                                              f'_{"inpage" if in_page else "split"}'
                                              f'_{"ok" if valid_second else "invalid"}',
                       handoff=h,hardware=BR28Model(mmu_model=True,initial_mmu_con=0),
                       diagnostics=diagnostics,budget=4)
        return result,base

    def test_split_store_leaves_the_page_table_bytes_untouched(self):
        result,base=self.cross_page_case()
        self.assertEqual(result['completed_steps'],4)
        snap=result['memory_snapshots']
        # 拆分两半正确落位(基线,与既有测试一致)……
        self.assertEqual(snap['low_half_target']['bytes_with_unknowns'],'88776655')
        self.assertEqual(snap['high_half_target']['bytes_with_unknowns'],'44332211')
        # ……且页表零污染:两枚 PTE 与前后邻接表项逐字节不变(翻译只读不写)。
        self.assertEqual(snap['pte_below']['bytes_with_unknowns'],'2121')
        self.assertEqual(snap['pte_above']['bytes_with_unknowns'],'3131')
        self.assertEqual(int.from_bytes(bytes.fromhex(snap['pte_a']['bytes_with_unknowns']),'little'),
                         0x8000|(0x130000>>7))
        self.assertEqual(int.from_bytes(bytes.fromhex(snap['pte_b']['bytes_with_unknowns']),'little'),
                         0x8000|(0x12f000>>7))

    def test_split_store_with_invalid_second_page_pollutes_no_sentinel(self):
        result,base=self.cross_page_case(valid_second=False)
        self.assertEqual(result['stop']['kind'],'mmu_invalid_page')
        self.assertEqual(result['stop']['address'],'0x400b00')
        snap=result['memory_snapshots']
        # 第一半已落盘(两次独立访问的字面语义),其余**全部**六个哨兵不动
        # (既有测试只断言其中三个,这里补满——第二半被拒时也不许有任何
        # 越界/回卷副作用)。
        self.assertEqual(snap['low_half_target']['bytes_with_unknowns'],'88776655')
        for label,expected in (('sentinel_below_low','aaaaaaaa'),
                               ('contiguous_after_page','cccccccc'),
                               ('page_a_head','dddddddd'),
                               ('high_half_target','eeeeeeee'),
                               ('sentinel_above_high','f0f0f0f0'),
                               ('pte_below','2121'),('pte_above','3131')):
            self.assertEqual(snap[label]['bytes_with_unknowns'],expected,label)

    def test_in_page_doubleword_store_is_not_split(self):
        # 反向反例:8 字节访问整段落在一页内时不得走拆分适配器(单次翻译、
        # 连续落盘)——「无假拆分」。
        result,base=self.cross_page_case(in_page=True)
        self.assertEqual(result['completed_steps'],4)
        self.assertEqual(result['instruction_adapters'],
                         {'exact_full_word_store':3,'exact_doubleword_store':1})
        snap=result['memory_snapshots']
        # 页 22(VA 0x400b00..)自己的翻译是 0x12f000:两半连续落在 0x12f00c/10
        # (单次翻译的连续落盘),不碰另一页的目标哨兵。
        self.assertEqual(snap['inpage_low']['bytes_with_unknowns'],'88776655')
        self.assertEqual(snap['inpage_high']['bytes_with_unknowns'],'44332211')
        self.assertEqual(snap['high_half_target']['bytes_with_unknowns'],'eeeeeeee',
                         'in-page store must not touch the other page target')

    def test_cross_page_doubleword_load_reads_both_translations(self):
        # 读侧对称性:ld.d 按上游 m2 语义=两次独立 4 字节读,两半各取各自
        # 翻译页的种子值;「按第一页连续读」会把高半读成 0xcccccccc——
        # 用寄存器结果区分。
        result,base=self.cross_page_case(load=True)
        self.assertEqual(result['completed_steps'],4)
        snap=result['memory_snapshots']
        r=result['last_registers']['r']
        # 编码字段→目标对以实测为准:encode('ld.d.sp.l',r=2,n=2)(d0e90840,
        # codec 往返一致)把 d[sp+8] 装进 (r4,r5)——低半来自页 21 自己的
        # 翻译,高半来自页 22 自己的翻译。
        self.assertEqual(r[4],'0xbbbbbbbb','low half must come from page 21 own translation')
        self.assertEqual(r[5],'0xeeeeeeee','high half must come from page 22 own translation, '
                                          'not the contiguous continuation')
        for label,expected in (('contiguous_after_page','cccccccc'),
                               ('pte_below','2121'),('pte_above','3131')):
            self.assertEqual(snap[label]['bytes_with_unknowns'],expected,label)


# ---------------------------------------------------------------------------
# AZ-4 键链常量前置守卫:场景的通过判据(三个手势 post)只有在「档位值
# 真的落在分档带里」时才可推导。破坏=任一常量改出带外(比如把按下档改到
# 释放带)会让手势全灭——本组把带结构不变式钉在单测层,让这类编辑在
# 3 分钟场景跑之前就失败。
class KeyChainGuardTests(unittest.TestCase):
    def test_res_levels_sit_in_their_own_bands(self):
        from probe_irq_entry import (KEY_CHAIN_THRESHOLD_RELEASE,KEY_CHAIN_RES_RELEASE,
                                     KEY_CHAIN_RES_PRESSED,KEY_CHAIN_KEY_ID)
        self.assertLessEqual(KEY_CHAIN_RES_RELEASE,KEY_CHAIN_THRESHOLD_RELEASE,
                             'release level must classify as no-key')
        self.assertLess(KEY_CHAIN_THRESHOLD_RELEASE,KEY_CHAIN_RES_PRESSED,
                        'press level must fall inside the press band')
        self.assertLessEqual(KEY_CHAIN_RES_PRESSED,1022,'press level must stay below the sentinel')
        self.assertEqual(KEY_CHAIN_KEY_ID,10,'route-BW cross-checked key id')

    def test_key_channel_fixture_maps_to_the_press_level(self):
        from probe_irq_entry import (KEY_CHAIN_RES_NAMED_VALUES,KEY_CHAIN_RES_PRESSED,
                                     FD31_ADC_RES_NAMED_VALUES,KEY_CHAIN_CHANNEL_WORD)
        self.assertEqual(KEY_CHAIN_RES_NAMED_VALUES[KEY_CHAIN_CHANNEL_WORD],KEY_CHAIN_RES_PRESSED)
        # 键链 dict 是 BY 三通道的**扩展**而非改写(世界前缀值不受场景影响)。
        for channel,value in FD31_ADC_RES_NAMED_VALUES.items():
            self.assertEqual(KEY_CHAIN_RES_NAMED_VALUES[channel],value)

    def test_sensor_object_threshold_table_is_monotone_with_sentinel_tail(self):
        from probe_irq_entry import key_chain_sensor_object_bytes,KEY_CHAIN_THRESHOLD_RELEASE
        obj=key_chain_sensor_object_bytes()
        thresholds=struct.unpack_from('<20H',obj,10)
        self.assertEqual(thresholds[0],KEY_CHAIN_THRESHOLD_RELEASE)
        self.assertEqual(list(thresholds[1:]),[1022]+[1023]*18)
        self.assertTrue(all(a<=b for a,b in zip(thresholds,thresholds[1:])),
                        'threshold table must stay ascending for first-match semantics')


# ---------------------------------------------------------------------------
# AZ-5 post_stop 外设时钟冻结(codex 原型「停止后事件是否还在增长」的
# 直调版):key_chain_direct_call 的受控步进循环显式不调 hardware.advance
# ——这里用真模拟器 + 已走表的 Timer0 实测:直调若干步后,定时器
# cnt/两级余数/到期数/模型步数**全部不变**,CPU 状态逐字段复原;随后
# 显式 advance 一次计数即动(证明冻结断言不是没有分辨力的空真)。
class PostStopFreezeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.module=upstream()
        firmware=ROOT/'firmware/QCY_H3S_7018F_1T2_release_note_V1.1.8_011.ufw'
        cls.app=application(firmware);cls.header=package_header(firmware)

    def test_direct_call_loop_freezes_peripheral_time(self):
        from pi32v2_asm import encode
        from probe_irq_entry import key_chain_direct_call
        h=profile(self.header,SENTINEL_A)
        from probe_boot_handoff import RAM_CODE
        dst,src,length=RAM_CODE
        h['seeds'].append((dst,self.app[src-ENTRY:src-ENTRY+length]))
        h['description']['phase']='falsification post-stop freeze test; isolated window'
        app=bytearray(self.app)
        base=ENTRY+len(app)-32
        window=encode('rts')
        app[base-ENTRY:base-ENTRY+len(window)]=window
        hardware=BR28Model(initial_irq_config=[0]*32,initial_ipmask=0,
                           timer0_std12m_source=True)
        hardware.write(IRQ_CONFIG,0)
        for address,value in ((TIMER0,0x4000),(TIMER0+4,0),(TIMER0+8,119999),(TIMER0,0x1401)):
            hardware.write(address,value)
        for _ in range(3):
            hardware.advance(4)   # 主循环等价:让表先走起来(cnt=9)
        self.assertGreater(hardware.timer.cnt,0)
        state_before=hardware.timer.cnt
        observations=[]

        class _Bookkeeping:
            after_steps=0
            def after_step(self,cpu=None,before_pc=None):self.after_steps+=1

        def post_stop(machine,hw,cpu):
            bookkeeping=_Bookkeeping()
            saved={'pc':cpu.pc,'sp':cpu.sp,'rets':cpu.rets,'flags':cpu.flags,'R':list(cpu.R)}
            snapshot=lambda:(hw.timer.cnt,hw.timer.remainder,hw.timer.source_remainder,
                             hw.timer.expirations,hw.counts['completed_model_steps'])
            before=snapshot()
            value,steps,hits=key_chain_direct_call(cpu,bookkeeping,base,[],
                                                   max_steps=10,watch=(base,))
            after=snapshot()
            observations.append({'frozen':before==after,'steps':steps,
                                 'after_steps':bookkeeping.after_steps,
                                 'restored':(cpu.pc,cpu.sp,cpu.rets,cpu.flags,list(cpu.R))
                                            ==(saved['pc'],saved['sp'],saved['rets'],
                                              saved['flags'],saved['R'])})
            # 非空真证明:显式 advance 一步(参数是 ICFG,同运行循环),计数必须动。
            hw.advance(0x300)
            observations.append({'explicit_advance_moves':hw.timer.cnt!=after[0]})
            return {'freeze_probe':observations}

        result=explore(self.module,bytes(app),name='az_post_stop_freeze',
                       handoff=h,hardware=hardware,
                       diagnostics={'entry_pc':base},budget=1,post_stop=post_stop)
        probe=result['freeze_probe']
        self.assertTrue(probe[0]['frozen'],
                        'direct-call loop must not advance peripheral time')
        self.assertEqual((probe[0]['steps'],probe[0]['after_steps']),(1,1))
        self.assertTrue(probe[0]['restored'],'direct-call loop must restore CPU state')
        self.assertTrue(probe[1]['explicit_advance_moves'],
                        'freeze assertion must have resolution (an explicit advance moves the counter)')
        self.assertEqual(hardware.timer.cnt,state_before+3+3,
                         'exactly two explicit advances may move the counter: the single explore '
                         'main-loop step (+3) and the resolution probe (+3); the direct call adds 0')


# ---------------------------------------------------------------------------
# 慢场景层(一次性取证,不在 check_local):
#   R1 条件前沿撤掉 RES fixture → 应停 689,402 fd31_adc_res_not_modeled;
#   R2 条件前沿撤掉 Timer0 IRQ4 交付 → 应在首次合格 pending 处诚实停
#      (interrupt_delivery_not_implemented),远早于 3,137,573;
#   R3 键链场景破坏分档阈值(按下档 700 改出分档带,阈值 500→750)→
#      三个手势 post 全灭(断言链对输入敏感,「改拍数」类破坏会被抓住)。
def scenario_evidence(rom_path,firmware_path,out_path):
    import probe_irq_entry as pie
    from probe_boot import flash_image
    from frontier_registry import scenario_flags, frontier
    rom=rom_path.read_bytes()
    from probe_boot import check_rom
    check_rom(rom)
    module=upstream();app=application(firmware_path);header=package_header(firmware_path)
    image=flash_image(firmware_path)
    findings={}
    # R1:撤掉 RES fixture。
    flags=dict(scenario_flags('p11_heap_experimental',pie));flags.pop('fd31_adc_res_named_values')
    r1=pie.irq_case(module,app,header,rom,'az_falsify_res_removed',**flags,flash_image_bytes=image)
    findings['R1_res_fixture_removed']={
        'completed_steps':r1['completed_steps'],'stop_pc':r1['stop_pc'],'stop':r1['stop'],
        'caught':(r1['stop']['kind']=='fd31_adc_res_not_modeled'
                  and r1['stop'].get('address')=='0xfd3104'
                  and r1['completed_steps']<frontier('p11_heap_experimental')['completed_steps'])}
    # R2:撤掉 Timer0 IRQ4 交付。
    flags=dict(scenario_flags('p11_heap_experimental',pie));flags.pop('timer0_irq4_delivery')
    r2=pie.irq_case(module,app,header,rom,'az_falsify_timer0_delivery_removed',**flags,
                    flash_image_bytes=image)
    findings['R2_timer0_irq4_delivery_removed']={
        'completed_steps':r2['completed_steps'],'stop_pc':r2['stop_pc'],'stop':r2['stop'],
        'caught':(r2['stop']['kind']=='interrupt_delivery_not_implemented'
                  and r2['stop'].get('irq')==4
                  and r2['completed_steps']<frontier('p11_heap_experimental')['completed_steps'])}
    # R3:键链分档阈值破坏(按下档改出带)。
    saved=pie.KEY_CHAIN_THRESHOLD_RELEASE
    try:
        pie.KEY_CHAIN_THRESHOLD_RELEASE=750
        r3=pie.irq_case(module,app,header,rom,'az_falsify_keychain_threshold',
                        entry=True,banks=True,idle_wait=True,rti=True,unmaskable=True,
                        sfc_suspend=True,gpio_out_readback=True,kick_wake=True,hardware_crc=True,
                        rep_block=True,rep_unknown_transport=True,repeat=True,icu_region=True,
                        dcu_cmo=True,zero_rep_skip=True,erased_bankb=True,insert_load_pair=True,
                        halfword_pair=True,p11_power_init=True,
                        heap_info_bytes=pie.P11_HEAP_INFO_DERIVED,byte_pair=True,lp_timer_writes=True,
                        lrct_trim_done=True,lrct_trim_result=pie.LRCT_TRIM_RESULT_PROBE,
                        timer0_std12m=True,lp_timer_con_readback=True,
                        p33_analog_config=True,power_init_rstcon_masks=True,
                        power_init_wkup_dly=True,
                        mem_pwr_con=True,p33_window=True,p33_port_sel=True,
                        p33_awkup=True,p3_ls=True,p11_syscon1_masks=True,
                        fd31_adc_con=True,fd31_adc_con_readback=True,
                        fd31_adc_pnd_completion=True,
                        fd31_adc_res_named_values=pie.KEY_CHAIN_RES_NAMED_VALUES,
                        p33_analog_tail=True,
                        p3_ana_read_ldoin_low=True,
                        gpcnt_con_readback=True,
                        jl_clock_dcck=True,
                        jl_uart_con=True,
                        jl_uart_init_writes=True,
                        subword_stores=True,word_store_sp_l=True,
                        timer0_irq4_delivery=True,
                        core_tick_irq3_delivery=True,
                        key_chain_scan=True,
                        flash_image_bytes=image)
    finally:
        pie.KEY_CHAIN_THRESHOLD_RELEASE=saved
    kc=r3['key_chain_direct_scan']
    expected={hex(0x10000|(10<<8)|4),hex(0x10000|(10<<8)|1),hex(0x10000|(10<<8)|0)}
    observed={p['payload'] for sequence in ('double_click','long_press','single_click')
              for p in kc[sequence]['post_calls']}
    findings['R3_keychain_threshold_sabotaged']={
        'world_stop':(r3['stop_pc'],r3['stop']['kind']),
        'sampling_transport_intact':(len(kc['res_fixture_switches'])==7 and
                                     all(s['sampled_value']==s['res_fixture']
                                         for s in kc['res_fixture_switches'])),
        'observed_posts':sorted(observed),'expected_posts':sorted(expected),
        'caught':observed!=expected}
    out_path.parent.mkdir(parents=True,exist_ok=True)
    out_path.write_text(json.dumps(findings,indent=1,sort_keys=True,default=str))
    print(json.dumps(findings,indent=1,sort_keys=True,default=str))
    return 0 if all(v['caught'] for v in findings.values()) else 1


def main(argv=None):
    args=sys.argv[1:] if argv is None else argv
    if '--scenario-evidence' not in args:
        # 默认=快速单测层(接进 check_local --units):直接交给 unittest,
        # 保留 -v/类名过滤等标准用法。
        unittest.main(argv=[sys.argv[0],*args])
        return 0
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--scenario-evidence',action='store_true',
                        help='run the SLOW scenario-tier falsifications (minutes; needs --rom)')
    parser.add_argument('--rom',type=Path,help='private ROM path for the scenario tier')
    parser.add_argument('--firmware',type=Path,
                        default=ROOT/'firmware/QCY_H3S_7018F_1T2_release_note_V1.1.8_011.ufw')
    parser.add_argument('--out',type=Path,default=ROOT/'analysis/local-checks/falsification-scenarios.json')
    parsed=parser.parse_args(args)
    if parsed.rom is None:
        parser.error('--scenario-evidence requires --rom')
    return scenario_evidence(parsed.rom,parsed.firmware,parsed.out)


if __name__=='__main__':
    sys.exit(main())
