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
  外设时钟冻结、事件时钟 post_stop 冻结不变式(盲区 B-1 收口)、闭式
  换算常量(盲区 B-2 收口)。除 bus/post_stop 两组外不需要私有 ROM。
- 慢场景层(opt-in `--scenario-evidence --rom ROM`,不在 check_local 内):
  用被破坏的开关复跑大场景,证明前沿通过 genuinely 依赖该投影。
  见 docs/falsification-checks.md 的运行记录。
"""
import argparse
import json
import struct
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0,str(Path(__file__).resolve().parent))

from br28_model import BR28Model, Blocked, TIMER0, IRQ_CONFIG  # noqa: E402
from probe_boot import ENTRY, IrqDeliveryRequest, application, explore  # noqa: E402
from probe_boot_handoff import HANDOFF, SENTINEL_A, package_header, profile  # noqa: E402
from probe_pi32v2 import ROOT, upstream  # noqa: E402
from world_snapshot import SnapshotError, WorldSnapshot  # noqa: E402


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
        ('SRC_SYNC 容器撤掉 copex mode-22 放行(路 SRCSYNC 依赖边)',
         dict(jl_src_sync_config_words=True),
         'the JL_SRC_SYNC config container only layers on the copex mode-22 release'),
        ('JL_SYNC 容器撤掉 SRC_SYNC 放行(路 SYNCIMPL 依赖边)',
         dict(jl_sync_config_words=True),
         'the JL_SYNC config container only layers on the JL_SRC_SYNC release'),
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

    def test_src_sync_probe_value_guard_on_full_conditional_stack(self):
        # 路 SRCSYNC:probe 值域守卫(恰全一字)藏在 jl_src_sync_config_words
        # 块内,最小依赖组合够不着(copex 依赖链 vl0→daa_con1→…更深),改用
        # registry 全开条件栈——只错置 probe 值,守卫必须先于任何仿真炸出。
        import frontier_registry
        import probe_irq_entry
        from probe_irq_entry import irq_case
        flags = frontier_registry.scenario_flags('p11_heap_experimental',
                                                 probe_irq_entry)
        flags['jl_src_sync_probe_value'] = 0x1234
        with self.assertRaisesRegex(
                ValueError,
                'the SRC_SYNC CON1 sensitivity probe takes exactly the all-ones word'):
            irq_case(_SimulatorBomb(),b'',b'',b'','sabotaged_probe',**flags)


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

        def post_stop(machine,hw,cpu,stop=None):
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
# B-2 闭式换算常量(盲区 B-2 收口;路 BK §3.2 钉死,docs/timer2-eventclock-
# design.md §3.1/§3.2):「1 毫秒 = 恰 4000 模型步」有两条**独立**换算链,
# 必须在同一全局模型步轴上汇合——散落字面值改一处就漂,这里提为命名常量,
# AZ-7 用断言把推导链钉死(改任何原子,常量与模型行为一起红)。
#   LRC/TIMER2 链: 1ms = 200 LRC tick(固件 PRD 写值,TIMER2 0x12cd26 helper
#     的 1ms 调用形状)× 80 RC16M 参考 tick/LRC tick(LRC_HZ_NOMINAL=200000,
#     AL 恒等式 7.68e9/38400;16MHz 参考系)÷ 4 RC16M 参考 tick/模型步
#     (BR28Model.source_ticks_per_step 默认) = 200×80÷4 = 恰 4000 步。
#   STD12M/Timer0 链: prd=119999(固件写值 12*10000/1-1)→ PRD+1=120000
#     STD12M tick/10ms ÷ 3 STD12M tick/步(12/16×4,Timer0.
#     STD12M_TICKS_PER_REF_TICK=(3,4))= 40000 步/10ms = 4000 步/ms。
# 只提这两条已被闭式证明的普适换算;观测形状的瞬态推导值(如 horizon
# 36,780 步,依赖 cnt)不提常量。常量用**字面原子**推导而非 import 实现
# 侧常量:import 顺序不同时,「from br28_model import 常量」的快照会让本
# 常量与实现原子同步漂移(断言恒真、漂移漏检);原子与实现侧的绑定由
# AZ-7 的 atoms 测试单独钉死,链条=改实现原子 → atoms 红 + 模型互证红。
STEPS_PER_MS=200*80//4                               # 200 LRC tick × 80 ÷ 4 = 4000
TIMER0_STEPS_PER_10MS=120000//3                      # (PRD+1)@10ms ÷ 3 tick/步 = 40000


# ---------------------------------------------------------------------------
# AZ-6 事件时钟 post_stop 冻结不变式(盲区 B-1 收口;前置=CW 落地 event_clock
# /docs/event-clock.md 契约)。AZ-5 已钉「post_stop 直调段不推进外设时间」
# (Timer0/直调循环),AZ-2 已钉「模型 advance 停止非幂等」;本组把「stop 后
# 事件时钟不再前进」升级为机器层不变式,常态覆盖事件时钟的全部可观测计数
# 面:任何 stop 形状(交付请求逃逸/无事件诚实停/严格停传播/TIMER2 唤醒正常
# 返回)之后、下一次显式推进调用之前,反复观察(计数快照+完整 report() 生成)
# 必须逐项不变。合成双核(probe_event_clock 的 ParkedCpu 桩,如实标注非真实
# 固件上下文)不需要 ROM/模拟器,进 units;任何「观察/查询路径隐式走表」的
# 改动在这里红(反例自证钉分辨力)。
class EventClockPostStopFreezeTests(unittest.TestCase):
    @staticmethod
    def _reportable_parked_cpu(pc,core_id):
        """probe_event_clock.ParkedCpu + report() 需要的最小字段(R/rets)。

        probe_event_clock 的场景从不调 machine.report(),故 ParkedCpu 缺这
        两个字段;冻结观察轮要生成完整报告,这里补占位(R 全 0 与机器
        launch 的「placeholders, not measured」同措辞),不是新硬件状态。"""
        from probe_event_clock import ParkedCpu
        cpu=ParkedCpu(pc,core_id)
        cpu.R=[0]*16;cpu.rets=0
        return cpu

    @staticmethod
    def _clock_state(machine,hardware):
        """事件时钟全部可观测计数面的快照(纯读;任何一项推进都会显形)。"""
        timer=hardware.timer
        return {'event_clock_advances':machine.event_clock_advances,
                'completed_model_steps':hardware.counts['completed_model_steps'],
                'events_recorded':machine.event_count,
                'core_ticks':[t.report() for t in hardware.core_ticks or ()],
                'timer0':None if timer is None else
                    (timer.con,timer.cnt,timer.prd,timer.pending,timer.expirations,
                     timer.remainder,timer.source_remainder,timer.delivery_in_flight),
                'timer2':None if hardware.timer2 is None else hardware.timer2.report(),
                'software_pending':[(w.known,w.value) for w in
                    (hardware.software_irq.pending if hardware.software_irq is not None else ())]}

    def _assert_frozen(self,machine,hardware,rounds=3):
        """反复观察(快照+完整报告生成+事件计数)必须逐项等于停点快照。"""
        before=self._clock_state(machine,hardware)
        for attempt in range(rounds):
            report=machine.report()      # 完整 report() 生成也是观察路径
            self.assertEqual(self._clock_state(machine,hardware),before,
                             f'observation round {attempt} must not move the event clock')
            projection=report.get('event_clock_projection')
            if projection is not None:
                self.assertEqual(projection['virtual_model_steps_advanced'],
                                 before['event_clock_advances'])
            self.assertEqual(machine.event_count,before['events_recorded'])
        return before

    def _assert_resolution(self,machine,hardware,before):
        # 非空真证明:显式 advance 一步(运行循环同款参数)必须动计数——
        # 「怎么查都不动」若成立,冻结断言就没有分辨力。
        hardware.advance(0x300)
        self.assertNotEqual(self._clock_state(machine,hardware),before,
                            'an explicit advance must move the counters '
                            '(otherwise the freeze assertion is vacuous)')

    def _parked_machine(self,hardware):
        """probe_event_clock.parked_machine 的可报告变体(cpu 带占位 R/rets)。"""
        from br28_machine import DualCoreMachine
        m=DualCoreMachine(software_irq_entry=True,core_tick_entry=True,
                          idle_wait_projection=True,event_clock_projection=True)
        cpu0=self._reportable_parked_cpu(0x6066318,0)
        cpu1=self._reportable_parked_cpu(0x12c5ca,1)
        m.cores={0:cpu0,1:cpu1};m.hardware=hardware;m.idle_wait_cores={0}
        return m,cpu0,cpu1

    def test_delivery_request_stop_freezes_the_event_clock(self):
        # S1 交付请求逃逸:事件时钟推进到最近 core tick 溢出,IrqDeliveryRequest
        # 逃出 enter_idle_wait(控制权回到 harness,不交付)——停点后冻结。
        from probe_event_clock import hardware as event_hardware,arm_tick
        from probe_boot import IrqDeliveryRequest
        h=event_hardware(core0_irq3_nibble=3,core1_irq3_nibble=0)
        m,cpu0,cpu1=self._parked_machine(h)
        arm_tick(h,0,4,5)             # 2 步后溢出,合格
        with self.assertRaises(IrqDeliveryRequest) as caught:
            m.enter_idle_wait(cpu1)   # 全核 IDLE → 事件时钟 → 2 步 → 请求
        self.assertEqual((caught.exception.details['core'],caught.exception.details['irq']),(0,3))
        self.assertEqual(m.event_clock_advances,2)
        before=self._assert_frozen(m,h)
        self._assert_resolution(m,h,before)

    def test_honest_stop_freezes_the_event_clock(self):
        # S2 无事件诚实停:零推进 Blocked(all_cores_idle_waiting)——停点后冻结。
        from probe_event_clock import hardware as event_hardware
        from probe_boot import Blocked
        h=event_hardware(core0_irq3_nibble=3,core1_irq3_nibble=0)
        m,cpu0,cpu1=self._parked_machine(h)  # 不武装任何 tick
        with self.assertRaisesRegex(Blocked,'all_cores_idle_waiting'):
            m.enter_idle_wait(cpu1)
        self.assertEqual(m.event_clock_advances,0)
        before=self._assert_frozen(m,h)
        self._assert_resolution(m,h,before)

    def test_strict_blocked_stop_freezes_the_event_clock(self):
        # S3 严格停传播:Timer0 合格到期而交付投影关——事件时钟推进途中
        # Blocked(interrupt_delivery_not_implemented) 原样传播,停点后冻结。
        from probe_boot import Blocked
        from br28_machine import DualCoreMachine
        h=BR28Model(initial_irq_config=[0]*32,initial_ipmask=0,
                    timer0_std12m_source=True)
        h.write(IRQ_CONFIG,0)
        for address,value in ((TIMER0,0x4000),(TIMER0+4,0),(TIMER0+8,3),(TIMER0,0x1400)):
            h.write(address,value)
        h.write(IRQ_CONFIG,0x30000)    # IRQ4 使能+优先级1(真实运行态 nibble)
        h.write(TIMER0,0x1401)         # STD12M 启动;每步 3 计数,prd+1=4
        m=DualCoreMachine(software_irq_entry=True,idle_wait_projection=True,
                          event_clock_projection=True)
        cpu0=self._reportable_parked_cpu(0x6066318,0)
        m.cores={0:cpu0};m.hardware=h;m.idle_wait_cores=set()
        with self.assertRaisesRegex(Blocked,'interrupt_delivery_not_implemented'):
            m.enter_idle_wait(cpu0)    # 单核全 IDLE → 事件时钟 → 到期 → 严格停
        self.assertEqual(m.event_clock_advances,2)   # 闭式 horizon=ceil(16/12)=2
        before=self._assert_frozen(m,h)
        # 分辨力:停后再调 advance 会再次抛同一个严格停(AZ-2 的模型层
        # 非幂等在 Timer0 上的形状)且计数已动——两件事一起断言。
        with self.assertRaisesRegex(Blocked,'interrupt_delivery_not_implemented'):
            self._assert_resolution(m,h,before)

    def test_timer2_wakeup_return_freezes_and_matches_the_closed_form(self):
        # S4 TIMER2 完成唤醒正常返回:事件时钟按 BK 闭式推进恰 STEPS_PER_MS
        # 步,pending 置位、锚点停泊核被唤醒——返回后冻结。B-1(停后冻结)与
        # B-2(1ms=4000 步闭式)在这个形状上汇合:推进量就是命名常量本身。
        from br28_machine import DualCoreMachine,TIMER2_POLL_ANCHOR
        from br28_model import TIMER2

        class _IdleDecodeCpu:
            """TIMER2 锚点停泊桩:与 probe_event_clock.ParkedCpu 同级的
            合成件(非真实固件上下文),另在 0x12cd76 提供 idle 解码
            (wake_timer2_poll 的 require 需要)与 report() 占位字段。"""
            def __init__(self,pc,core_id):
                self.mem=None;self.pc=pc;self.reti=0;self.steps=0
                self.core_id=core_id;self.flags=0
                self.sp_known=True;self.sp=0x4013f0;self.usp=None;self.ssp=0x123840
                self.last_explicit_ssp=0x123840
                self.bootstrap_context=None;self.after_then=None;self.repeat=None
                self.active_context=None
                self.icfg=0x300;self.icfg_known=True
                self.R=[0]*16;self.rets=0   # 占位,not measured
            def decode(self,pc):
                class _Insn:id='idle'
                return _Insn(),b'\x00\x00',2

        h=BR28Model(initial_irq_config=[0]*32,initial_ipmask=0,
                    timer2_delay_projection=True)
        for address,value in ((TIMER2,0x4000),(TIMER2+4,1),(TIMER2+8,200),(TIMER2,0xC01)):
            h.write(address,value)     # 1ms 形状:CNT=1/PRD=200/LRC 源/div1
        m=DualCoreMachine(software_irq_entry=True,idle_wait_projection=True,
                          event_clock_projection=True,timer2_delay_projection=True)
        cpu0=_IdleDecodeCpu(TIMER2_POLL_ANCHOR,0)
        m.cores={0:cpu0};m.hardware=h;m.idle_wait_cores=set()
        m.enter_idle_wait(cpu0)        # 全核 IDLE → 事件时钟 → 4000 步 → 唤醒返回
        self.assertEqual(m.event_clock_advances,STEPS_PER_MS,
                         'the event clock must advance exactly the closed-form 1ms horizon')
        self.assertEqual((cpu0.pc,cpu0.steps),(TIMER2_POLL_ANCHOR+2,1))
        self.assertTrue(h.timer2.pending)
        self.assertEqual(h.timer2.transactions[-1]['expiry_model_step'],STEPS_PER_MS)
        before=self._assert_frozen(m,h)
        self._assert_resolution(m,h,before)

    def test_parked_entry_retry_after_stop_does_not_advance(self):
        # 机器层入口幂等:停后对每个泊着的核重试 enter_idle_wait——「已泊」
        # 短路立即 return,零推进。烧表只发生在直调 advance/事件时钟入口
        # (AZ-2 与下一测试分别钉死),不在泊核入口。
        from probe_event_clock import hardware as event_hardware,arm_tick
        from probe_boot import IrqDeliveryRequest,Blocked
        h=event_hardware(core0_irq3_nibble=3,core1_irq3_nibble=0)
        m,cpu0,cpu1=self._parked_machine(h)
        arm_tick(h,0,4,5)
        with self.assertRaises(IrqDeliveryRequest):
            m.enter_idle_wait(cpu1)
        before=self._clock_state(m,h)
        for cpu in (cpu0,cpu1):
            m.enter_idle_wait(cpu)     # 已泊核:立即返回
        self.assertEqual(self._clock_state(m,h),before)
        # 诚实停形状同样入口幂等。
        h2=event_hardware(core0_irq3_nibble=3,core1_irq3_nibble=0)
        m2,c0,c1=self._parked_machine(h2)
        with self.assertRaisesRegex(Blocked,'all_cores_idle_waiting'):
            m2.enter_idle_wait(c1)
        before2=self._clock_state(m2,h2)
        for cpu in (c0,c1):
            m2.enter_idle_wait(cpu)
        self.assertEqual(self._clock_state(m2,h2),before2)

    def test_event_clock_entry_retry_after_stop_does_not_burn_counters(self):
        # B-1 盲区在事件时钟入口的裁决(实测性质,钉死为正面不变式):
        # AZ-2 已钉「模型层直调 hardware.advance 停止非幂等——重试烧表」;
        # 机器层 advance_event_clock 入口的 horizon 家族则显式排除
        # unresolved_overflow 的 core tick(溢出即停在溢出态)与已在途的
        # Timer0 pending 片(handler ACK 前不会再抛),重入时无事件 →
        # 诚实停零推进。两个捕获形状都钉:core-tick 交付请求与 Timer0
        # in-flight 请求;唯一新增簿记是 event_clock_engaged 事件计数 +1。
        from probe_event_clock import hardware as event_hardware,arm_tick
        from probe_boot import IrqDeliveryRequest,Blocked

        def clock_domain(state):      # 事件簿记(event_count)不算时钟计数
            return {k:v for k,v in state.items() if k!='events_recorded'}

        # 形状一:core-tick 交付请求捕获(溢出即 unresolved_overflow,停表)。
        h=event_hardware(core0_irq3_nibble=3,core1_irq3_nibble=0)
        m,cpu0,cpu1=self._parked_machine(h)
        arm_tick(h,0,4,5)              # 只武装 core0(core1 保持停表,horizon 单源)
        with self.assertRaises(IrqDeliveryRequest):
            m.enter_idle_wait(cpu1)
        before=self._clock_state(m,h)
        with self.assertRaisesRegex(Blocked,'all_cores_idle_waiting'):
            m.advance_event_clock(cpu1)   # 重入:horizon 排除溢出态计数器
        after=self._clock_state(m,h)
        self.assertEqual(clock_domain(after),clock_domain(before),
                         'the event-clock entry retry must not burn any timer counter')
        self.assertEqual(after['events_recorded'],before['events_recorded']+1,
                         'the retry only adds the event_clock_engaged bookkeeping event')

        # 形状二:Timer0 in-flight 交付请求捕获(周期计数器,但 in-flight
        # pending 片被 horizon 排除——不推进就不会走表)。
        from br28_machine import DualCoreMachine
        h2=BR28Model(initial_irq_config=[0]*32,initial_ipmask=0,
                     timer0_std12m_source=True,timer0_irq_delivery_requests=True)
        h2.write(IRQ_CONFIG,0)
        for address,value in ((TIMER0,0x4000),(TIMER0+4,0),(TIMER0+8,3),(TIMER0,0x1400)):
            h2.write(address,value)
        h2.write(IRQ_CONFIG,0x30000)
        h2.write(TIMER0,0x1401)
        m2=DualCoreMachine(software_irq_entry=True,idle_wait_projection=True,
                           event_clock_projection=True,timer0_irq_entry=True)
        cpu0b=self._reportable_parked_cpu(0x6066318,0)
        m2.cores={0:cpu0b};m2.hardware=h2;m2.idle_wait_cores=set()
        with self.assertRaises(IrqDeliveryRequest) as caught:
            m2.enter_idle_wait(cpu0b)
        self.assertEqual((caught.exception.details['core'],caught.exception.details['irq']),(0,4))
        before2=self._clock_state(m2,h2)
        with self.assertRaisesRegex(Blocked,'all_cores_idle_waiting'):
            m2.advance_event_clock(cpu0b)  # 重入:in-flight pending 片不是事件
        after2=self._clock_state(m2,h2)
        self.assertEqual(clock_domain(after2),clock_domain(before2))
        self.assertEqual(after2['events_recorded'],before2['events_recorded']+1)

    def test_sabotaged_observation_path_is_caught(self):
        # 反例自证(破坏→红):若观察路径被改成隐式走表(比如 report() 生成时
        # 「顺手把时间推进到最新」),冻结断言必须检出。给实例 report 打补丁
        # 插入一次 advance,断言快照变化被抓住——证明冻结断言有分辨力,不是
        # 「怎么查都不动」的空真。
        from probe_event_clock import hardware as event_hardware
        from probe_boot import Blocked
        from br28_machine import DualCoreMachine
        h=event_hardware(core0_irq3_nibble=3,core1_irq3_nibble=0)
        m,cpu0,cpu1=self._parked_machine(h)
        with self.assertRaisesRegex(Blocked,'all_cores_idle_waiting'):
            m.enter_idle_wait(cpu1)
        before=self._clock_state(m,h)
        original=DualCoreMachine.report
        try:
            m.report=lambda:(h.advance(0x300),original(m))[1]   # 隐式推进的观察路径
            m.report()
            self.assertNotEqual(self._clock_state(m,h),before,
                                'a query path that implicitly advances must be caught')
        finally:
            del m.report               # 撤除实例遮蔽,恢复类实现


# ---------------------------------------------------------------------------
# AZ-7 闭式换算常量断言(盲区 B-2 收口):STEPS_PER_MS/TIMER0_STEPS_PER_10MS
# 的推导链逐原子钉死,并与模型行为互证——两条独立换算链在同一全局模型步轴
# 上必须汇合;改实现侧任何原子(LRC 频率、参考 tick 比、每步源 tick),常量
# 断言与模型互证一起红,漂移不会静默。
class ClosedFormStepConstantsTests(unittest.TestCase):
    def test_reference_step_axis_atoms_are_pinned(self):
        # 原子常量与实现侧绑定:这些是推导链的全部输入,改任何一处,
        # 下面的汇合/互证断言一起红。
        from br28_model import LRC_HZ_NOMINAL,LRC_REF_TICKS_PER_LRC_TICK,Timer0
        self.assertEqual((LRC_HZ_NOMINAL,LRC_REF_TICKS_PER_LRC_TICK),(200000,80),
                         'AL identity 7.68e9/38400=200kHz and its 16MHz-reference quotient')
        self.assertEqual(Timer0.STD12M_TICKS_PER_REF_TICK,(3,4),
                         '12MHz STD12M vs the 16MHz RC16M reference axis')
        h=BR28Model(initial_irq_config=[0]*32,initial_ipmask=0)
        self.assertEqual(h.source_ticks_per_step,4,
                         'RC16M reference ticks per model step (the shared run-loop axis)')
        self.assertEqual(h.source_ticks_per_step*Timer0.STD12M_TICKS_PER_REF_TICK[0]
                         //Timer0.STD12M_TICKS_PER_REF_TICK[1],3,
                         'STD12M ticks per model step')

    def test_two_independent_chains_meet_on_the_same_step_axis(self):
        # LRC/TIMER2 链与 STD12M/Timer0 链独立推导,同一全局模型步轴上
        # 必须给出同一个「每毫秒步数」——任一条链的原子漂移都打破汇合。
        self.assertEqual(STEPS_PER_MS,200*80//4)
        self.assertEqual(TIMER0_STEPS_PER_10MS,120000//3)
        self.assertEqual(TIMER0_STEPS_PER_10MS,10*STEPS_PER_MS,
                         'the Timer0 10ms period and the TIMER2 1ms delay must agree '
                         'on the shared model-step axis')

    def test_timer2_one_ms_delay_is_exactly_the_constant(self):
        # 模型互证(TIMER2 链):1ms 形状(CNT=1/PRD=200/LRC 源/div1)的闭式
        # horizon=常量,且逐 advance(4) 第 STEPS_PER_MS 步恰首次 pending。
        from br28_model import Timer2Delay,TIMER2
        t=Timer2Delay()
        for address,value in ((TIMER2,0x4000),(TIMER2+4,1),(TIMER2+8,200)):
            t.write(address,value)
        t.write(TIMER2,0xC01)
        self.assertEqual(t.steps_to_next_expiration(4),STEPS_PER_MS)
        self.assertEqual(t.transactions[-1]['closed_form_steps'],STEPS_PER_MS)
        for _ in range(STEPS_PER_MS-1):
            t.advance(4)
            self.assertFalse(t.pending)
        t.advance(4)
        self.assertTrue(t.pending)
        self.assertEqual(t.transactions[-1]['expiry_model_step'],STEPS_PER_MS)

    def test_timer0_ten_ms_period_is_exactly_ten_constants(self):
        # 模型互证(STD12M/Timer0 链):前沿形状 prd=119999 的整周期恰
        # TIMER0_STEPS_PER_10MS 步一次到期;回绕位置=整周期 3 tick/步
        # 与 120000 tick 的守恒(每步恰 3 计数的 AZ-2 纪律在此形状特化)。
        from br28_model import Timer0
        t=Timer0((4,5))
        t.write(0,0x4000);t.write(4,0);t.write(8,119999);t.write(0,0x1401)
        for _ in range(TIMER0_STEPS_PER_10MS-1):
            t.advance(4)
            self.assertEqual(t.expirations,0)
        t.advance(4)
        self.assertEqual(t.expirations,1)
        self.assertEqual(t.cnt,3*TIMER0_STEPS_PER_10MS-120000)

    def test_sabotaged_conversion_atom_is_caught(self):
        # 反例自证(破坏→红):改换算链一个原子(LRC 参考 tick 比 80→40,
        # 即「LRC 频率漂一倍」),闭式与常量立刻不再一致——漂移被检出,
        # 不会静默通过。
        import br28_model
        from br28_model import Timer2Delay,TIMER2
        t=Timer2Delay()
        for address,value in ((TIMER2,0x4000),(TIMER2+4,1),(TIMER2+8,200)):
            t.write(address,value)
        t.write(TIMER2,0xC01)
        original=br28_model.LRC_REF_TICKS_PER_LRC_TICK
        try:
            br28_model.LRC_REF_TICKS_PER_LRC_TICK=40
            self.assertNotEqual(t.steps_to_next_expiration(4),STEPS_PER_MS,
                                'a doubled LRC frequency must drift the closed form '
                                'off the named constant')
        finally:
            br28_model.LRC_REF_TICKS_PER_LRC_TICK=original
        self.assertEqual(t.steps_to_next_expiration(4),STEPS_PER_MS,'atom restored')


# ---------------------------------------------------------------------------
# AZ-8 场景级快照反例(盲区 B-3 收口,路 B3R/sim-b3r,2026-11-15)。
#
# B-3 挂账原文:场景级反例单次世界构建 ~3 分钟,进不了 --units;SNAP 落地
# probe_boot.explore(snapshot=) 捕获/恢复后,「停点后破坏」类反例降到秒级
# (docs/snapshot-design.md §4.1 消费者①;「前缀分歧」类仍走慢层 R1/R2/R3)。
#
# 世界=公开资产 typed_memory_replay 16,077 步(SNAP fast tier 同款)挂一个
# 带 Timer0 走表+软件 IRQ pending 的外设模型:外设域访问只有 PLL0.CON0 读,
# initial_registers={PLL0:1} 与 explore(pll=1) 假设同值,前缀与原世界同停点
# (unverified_zero_count_rep@16,077);Timer0 con=0x1001(source4/div1)每模型步
# 恰 4 参考计数、prd=0xFFFFFFF 全程不回绕(pending 恒 False,不触交付路径)。
# 中点 8,000 捕获后对快照做**值级**篡改——形状闸(manifest/白名单)全部自洽
# 重 derive 通过,测的是恢复链路携带的值;SNAP 已证的 RAM 字节篡改之外,
# 本路把破坏类别扩到状态位/计数簿记/容器记录值/pending 位/账本/CPU 状态位
# 与静态 fixture 八类。每类断言面=①等价对照(纯 json.dumps,插入序可见,
# 无 diff 白名单)②报告键精确红点(含 check_d1_acceptance 的 _timer0_state
# 数据源)③恢复侧静态守卫。红=破坏被抓住;「仍绿」=快照恢复路径把破坏
# 洗掉了(报告从静态重建而非恢复世界读)=假阳性,正是 B-3 要堵的洞。
#
# 边界(如实记录):typed_memory_replay 是单核无 machine 世界(explore 的 CPU
# 形状没有 machine.select() 需要的 icfg_known 属性),M5 机器交付簿记
# (timer0_irq_deliveries)在该世界不可承载——其破坏反例在慢层 R4(ROM 门控,
# irq_case 世界,300k+ 步捕获)。M3.timer.delivery_in_flight 不在报告键里,
# 值级篡改对该字段不可观测,同在 R4 用 M5 形态覆盖。
def _snapshot_assets_ready():
    firmware=ROOT/'firmware/QCY_H3S_7018F_1T2_release_note_V1.1.8_011.ufw'
    simulator=ROOT/'tools/reference-cache/pi32v2-isa/tools/llvm/sim.py'
    return firmware.exists() and simulator.exists()


@unittest.skipUnless(_snapshot_assets_ready(),
                     'official firmware / pinned pi32v2-isa not present in this checkout')
class SnapshotScenarioFalsificationTests(unittest.TestCase):
    """B-3 场景级反例:恢复世界→注入一类破坏→短程 resume→既有断言面红。"""
    MIDPOINT=8000

    @classmethod
    def make_hardware(cls):
        from br28_model import BR28Model,PLL0
        hardware=BR28Model(initial_registers={PLL0:1},          # =explore(pll=1) 假设值
                           software_irq_command_projection=True,
                           initial_software_irq_pending=(0x8,0))  # pending word0 bit3
        hardware.timer.con,hardware.timer.cnt,hardware.timer.prd=0x1001,1000,0xFFFFFFF
        return hardware

    @classmethod
    def run_world(cls,budget=None,**snapshot):
        return explore(cls.module,cls.app,name='typed_memory_replay',adapt_stack=True,
                       pll=1,r0=HANDOFF,handoff=cls.handoff,
                       startup_loops=True,budget=budget,hardware=cls.make_hardware(),
                       snapshot=snapshot or None)

    @classmethod
    def setUpClass(cls):
        cls.temp=tempfile.TemporaryDirectory(prefix='h3s-b3r-')
        cls.addClassCleanup(cls.temp.cleanup)
        firmware=ROOT/'firmware/QCY_H3S_7018F_1T2_release_note_V1.1.8_011.ufw'
        cls.module=upstream();cls.app=application(firmware)
        cls.handoff=profile(package_header(firmware),SENTINEL_A)
        cls.full=cls.run_world(budget=100_000)
        assert cls.full['stop']['kind']=='unverified_zero_count_rep'   # 前缀与原世界同停点
        cls.full_dump=json.dumps(cls.full)
        cls.resume_budget=cls.full['completed_steps']-cls.MIDPOINT+1   # +1=终态 Blocked 重试
        cls.snapshot=Path(cls.temp.name)/'midpoint.bin'
        captured=cls.run_world(budget=cls.MIDPOINT,capture=cls.snapshot)
        assert captured['stop']=={'kind':'instruction_budget'}

    def resume_tampered(self,name,mutate):
        """载入共享捕获快照→值级篡改→重存(manifest 对 body 形状重 derive,
        形状闸保持绿——测的是值,不是格式)→恢复续跑到原终点。"""
        loaded=WorldSnapshot.load(self.snapshot)
        mutate(loaded)
        path=Path(self.temp.name)/f'{name}.bin'
        loaded.save(path)
        resumed=self.run_world(budget=self.resume_budget,restore=path,
                               expected_context={'scenario':'typed_memory_replay',
                                                 'completed_steps':self.MIDPOINT})
        report=dict(resumed);report.pop('snapshot_restore')
        return resumed,report

    def assert_caught(self,resumed,report):
        # 红点一:等价对照(无 diff 白名单)必须红;诚实停形状不变(破坏如实
        # 呈现在报告键里,既不被恢复洗掉、也没有把世界改成另一种崩溃)。
        self.assertNotEqual(json.dumps(report),self.full_dump,
                            'tampered resume still matches the full replay: '
                            'the restore path washed the corruption out (false positive)')
        self.assertEqual(resumed['stop']['kind'],self.full['stop']['kind'])
        self.assertEqual(resumed['completed_steps'],self.full['completed_steps'])

    def test_control_untampered_resume_matches_full_replay(self):
        # 对照组(AZ-1 先例):未破坏的恢复续跑与完整直跑逐字节相等——
        # 证明反例组的「抓住」不是「任何恢复都红」的空真。
        resumed,report=self.resume_tampered('control',lambda loaded:None)
        self.assertEqual(json.dumps(report),self.full_dump)

    def test_timer0_pending_bit_flip_is_caught(self):
        # 破坏:翻转 Timer0 pending 状态位(False→True)。icfg gates8/9 未开
        # (eligible 立即 False,无交付路径),resume 照常到终点——破坏必须
        # 存活到报告键 peripherals.timer0.pending(check_d1_acceptance 的
        # _timer0_state 数据源,D1-2 读的就是它)。
        resumed,report=self.resume_tampered(
            'timer_pending',lambda loaded:
            loaded.body['M3']['objects']['timer']['fields'].update(pending=True))
        self.assert_caught(resumed,report)
        from check_d1_acceptance import _timer0_state
        self.assertEqual(_timer0_state(resumed)['pending'],True)
        self.assertEqual(_timer0_state(self.full)['pending'],False)

    def test_timer0_counter_bias_is_caught(self):
        # 破坏:Timer0 cnt +4(一个模型步的参考计数量)。恢复世界继续
        # 走表,篡改按步传播——终点 cnt 恰偏 4。BQ 闭式换算/事件时钟的
        # horizon 全压在 cnt/两级分数余数上,这类时钟核心状态被恢复洗掉
        # =所有时序断言失真。
        resumed,report=self.resume_tampered(
            'timer_cnt',lambda loaded:
            loaded.body['M3']['objects']['timer']['fields'].update(cnt=33004))
        self.assert_caught(resumed,report)
        from check_d1_acceptance import _timer0_state
        self.assertEqual(_timer0_state(resumed)['cnt'],
                         _timer0_state(self.full)['cnt']+4)

    def test_completed_step_counter_bias_is_caught(self):
        # 破坏:counts 簿记一条(completed_model_steps +1,虚拟时间轴)。
        # 报告键 peripherals.counts 必须从恢复世界读出偏差。
        def bias(loaded):
            counter=loaded.body['M3']['scalars']['counts']['$counter']
            counter[0][1]+=1
        resumed,report=self.resume_tampered('counter_bias',bias)
        self.assert_caught(resumed,report)
        self.assertEqual(report['peripherals']['counts']['completed_model_steps'],
                         self.full['peripherals']['counts']['completed_model_steps']+1)

    def test_register_projection_value_bias_is_caught(self):
        # 破坏:通用寄存器投影容器(写记录值)一条——PLL0 读回记录 1→2。
        def bias(loaded):
            loaded.body['M3']['scalars']['registers']['$pairs'][0][1]=2
        resumed,report=self.resume_tampered('register_bias',bias)
        self.assert_caught(resumed,report)
        self.assertEqual(report['peripherals']['registers']['0xfd4600'],'0x2')
        self.assertEqual(self.full['peripherals']['registers']['0xfd4600'],'0x1')

    def test_software_irq_pending_bit_drop_is_caught(self):
        # 破坏:移除软件 IRQ pending word0 的 pending 位(value 0x8→0x0,
        # known 掩码不动)。报告键 peripherals.software_irq_commands.pending
        # 的 known_value 必须跟着变——「移除一个 pending 位」不能被恢复
        # 路径静默补回。
        def drop(loaded):
            pending=loaded.body['M3']['objects']['software_irq']['fields']['pending']
            pending[0]['$partial_word']['value']=0
        resumed,report=self.resume_tampered('sw_irq_drop',drop)
        self.assert_caught(resumed,report)
        self.assertEqual(report['peripherals']['software_irq_commands']['pending']['0']['known_value'],'0x0')
        self.assertEqual(self.full['peripherals']['software_irq_commands']['pending']['0']['known_value'],'0x8')

    def test_memory_event_ledger_bias_is_caught(self):
        # 破坏:M7 循环账本一条记录值(memory events 首条 bytes 翻一位)。
        # 账本跨恢复点连续是报告等价的前提(docs/snapshot-design.md §M7)。
        def bias(loaded):
            loaded.body['M7']['scalars']['events'][0]['bytes']='01fd1900'
        resumed,report=self.resume_tampered('ledger_bias',bias)
        self.assert_caught(resumed,report)
        self.assertEqual(report['memory_events'][0]['bytes'],'01fd1900')
        self.assertEqual(self.full['memory_events'][0]['bytes'],'00fd1900')

    def test_cpu_state_bit_flip_is_caught(self):
        # 破坏:M1 CPU 状态位 sp_known True→False。报告键 last_registers.sp
        # 由它分派(hex/unknown)——恢复世界必须如实呈现「栈指针未知」。
        def flip(loaded):
            loaded.body['M1']['objects']['0']['fields']['sp_known']=False
        resumed,report=self.resume_tampered('sp_known',flip)
        self.assert_caught(resumed,report)
        self.assertEqual(report['last_registers']['sp'],'unknown')
        self.assertNotEqual(self.full['last_registers']['sp'],'unknown')

    def test_static_fixture_bias_refused_by_restore_guard(self):
        # 破坏(守卫面):静态 fixture 值篡改(initial_registers 的 PLL 假设
        # 1→2,形状自洽)。恢复侧 check_static 对照构造链重建的静态视图,
        # 值不一致必须拒载——「重建世界」不能用另一套静态配置偷换快照世界。
        loaded=WorldSnapshot.load(self.snapshot)
        loaded.body['M3']['static']['initial_registers']['$pairs'][0][1]=2
        path=Path(self.temp.name)/'static_bias.bin'
        loaded.save(path)
        with self.assertRaisesRegex(SnapshotError,'static configuration mismatch'):
            self.run_world(budget=10,restore=path,
                           expected_context={'scenario':'typed_memory_replay',
                                             'completed_steps':self.MIDPOINT})


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
    # R4:机器交付簿记值级破坏(B-3 收口的 M5 形态,路 B3R;units 层不可承载
    # ——typed_memory_replay 单核无 machine,见 AZ-8 注释的边界说明)。条件
    # 前沿 flag 全栈 N=400,000 预算停点捕获,跨过首次 Timer0 STD12M 到期
    # (343,252)故 timer0_irq_deliveries 非空;对照组=未篡改恢复续跑 vs 完整
    # 直跑逐字节相等;破坏=首条交付簿记的 reti_from 改一字符(形状闸自洽
    # 重 derive)——machine 报告键 timer0_irq_entry_projection.deliveries[0]
    # 必须偏离。交付簿记是 one-shot 投影的「已交付」判定源,恢复路径若
    # 洗掉/重建它=假账本。预算口径(实测):instruction_budget 的预算计
    # **尝试步数**,completed 与预算的差=泊核/未完成尝试(本世界 397,845
    # completed@budget 400,000);对照的 full 预算必须=捕获预算+X(SNAP
    # artifact tier 同款),capture_steps+X 会在捕获点前耗尽预算。
    flags4=dict(scenario_flags('p11_heap_experimental',pie))
    # 四次调用共享一个场景名:capture_context 的 scenario 记录场景名参数,
    # expected_context 必须与捕获时一致(名字不同=上下文闸拒载)。
    with tempfile.TemporaryDirectory(prefix='h3s-b3r-slow-') as work4:
        snap4=Path(work4)/'m5-deliveries.snapshot'
        pie.irq_case(module,app,header,rom,'az_falsify_m5_delivery_bias',**flags4,
                     flash_image_bytes=image,
                     snapshot={'budget':400_000,'capture':str(snap4)})
        loaded4=WorldSnapshot.load(snap4)
        capture4=loaded4.header['capture_context']['completed_steps']
        context4={'scenario':'az_falsify_m5_delivery_bias','completed_steps':capture4}
        deliveries4=loaded4.body['M5']['scalars']['timer0_irq_deliveries']
        full4=pie.irq_case(module,app,header,rom,'az_falsify_m5_delivery_bias',**flags4,
                           flash_image_bytes=image,snapshot={'budget':400_000+200})
        ctrl4=pie.irq_case(module,app,header,rom,'az_falsify_m5_delivery_bias',**flags4,
                           flash_image_bytes=image,
                           snapshot={'budget':200,'restore':str(snap4),
                                     'expected_context':context4})
        ctrl4=dict(ctrl4);ctrl4.pop('snapshot_restore')
        deliveries4[0]['reti_from']=deliveries4[0]['reti_from'][:-1]+'0'
        loaded4.save(snap4)
        r4=pie.irq_case(module,app,header,rom,'az_falsify_m5_delivery_bias',**flags4,
                        flash_image_bytes=image,
                        snapshot={'budget':200,'restore':str(snap4),
                                  'expected_context':context4})
    r4_report=dict(r4);r4_report.pop('snapshot_restore')
    full_d=full4['machine']['timer0_irq_entry_projection']['deliveries']
    r4_d=r4_report['machine']['timer0_irq_entry_projection']['deliveries']
    findings['R4_m5_delivery_bookkeeping_bias']={
        'capture_steps':capture4,'deliveries_at_capture':len(deliveries4),
        'control_resume_equals_full':json.dumps(ctrl4)==json.dumps(full4),
        'full_first_delivery_reti_from':full_d[0]['reti_from'],
        'biased_first_delivery_reti_from':r4_d[0]['reti_from'] if r4_d else None,
        'caught':(json.dumps(r4_report)!=json.dumps(full4) and bool(r4_d)
                  and r4_d[0]['reti_from']!=full_d[0]['reti_from'])}
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
