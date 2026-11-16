#!/usr/bin/env python3
"""D1 窗口观察器单测(路 PR2OBS,d1-window-producer-spec §2-§3/§7)。

合成样本世界照 test_d1_acceptance.py(PR2CHK)风格:fake machine/hardware 只带
观察器消费的属性面;真 DualCoreMachine 只测 model_step_total 公式与注册点。
键面契约用对照断言钉死(观察器不 import checker,本测试同时 import 两者比对):
- CHAIN_STAGES 与 check_d1_acceptance.CHAIN_STAGES 同名同序;
- timer0_period_closed_form 与 checker._timer0_expected_period_steps 同输出;
- finalize 树/合成样本(d1_health_scenario)的键集逐键对齐;
- 观察器产出(树+machine_face+合成 peripherals)拼成 scenario 后
  check_d1_acceptance.run_all 可消费不崩(键面兼容冒烟,pending 合法)。
"""
import copy
import struct
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0,str(Path(__file__).resolve().parent))

import br28_machine
import d1_window_observer as observer_module
from d1_window_observer import (D1WindowObserver,CHAIN_STAGES,PEND_WRITE_FACE,
    S19_LOOP_HEAD,GET_EVENT_CALL,CONSUME_PC,BIT20_PC,SPECIAL_PATH_PC,
    S18_HANDLER_PC,POST_API,ENQUEUE_SITES,KEYSCAN_CALLBACK,ADC_SCAN_CALLBACK,
    SYS_TIMER_FIRE_SITES,TIMER0_HANDLER_PC,
    timer0_period_closed_form,core_tick_deadline_closed_form)

import check_d1_acceptance as checker
from br28_machine import DualCoreMachine
from br28_model import BR28Model,CPU1_CONTROL,TIMER0
from probe_boot import Blocked,IrqDeliveryRequest


# ── 合成世界(观察器消费面;数值贴 BQ/B3 实测形状,同 test_d1_acceptance) ────────
class FakeMachine:
    def __init__(self,*,con=0x1401,cnt=9660,prd=119999,pending=False,
                 tick_cnt=627308,tick_prd=1199999,tick_progress=4,tick_pending=False,
                 cfg523=None,timer0_horizon=36780,queue_count=0,
                 callback_table=0x60a8c68):
        self.timer=SimpleNamespace(con=con,cnt=cnt,prd=prd,pending=pending,
                                   expirations=0,remainder=0,source_remainder=0,
                                   delivery_in_flight=False)
        self.core_ticks=[SimpleNamespace(con=1,cnt=tick_cnt,prd=tick_prd,
                                         progress=tick_progress,pending=tick_pending,
                                         expirations=0,acknowledgements=0,
                                         unresolved_overflow=False)]
        self.timer0_wake_horizon=lambda icfg:timer0_horizon
        self.stalls=0;self.event_clock_advances=0;self.idle_wait_cores=set()
        self.cfg523_installed_at=cfg523
        ram={}
        tcb=0x127db8
        for i,byte in enumerate(tcb.to_bytes(4,'little')):ram[0x126c5c+i]=byte
        for i,byte in enumerate(queue_count.to_bytes(2,'little')):
            ram[tcb+6+i]=byte
        if callback_table is not None:
            for i,byte in enumerate(callback_table.to_bytes(4,'little')):
                ram[0x126F6C+i]=byte
        mem=SimpleNamespace(ram=ram)
        self.cores={0:SimpleNamespace(core_id=0,pc=0x12c5ca,R=[0]*16,mem=mem,icfg=0,
                                      active_context=None,reti=None),
                    1:SimpleNamespace(core_id=1,pc=0x12c5ca,R=[0]*16,mem=mem,icfg=0,
                                      active_context=None,reti=None)}
        self.hardware=self

    def model_step_total(self,completed):
        return completed+self.stalls+self.event_clock_advances


def make_observer(machine,**kwargs):
    return D1WindowObserver(machine,machine,d1_window_steps=kwargs.pop(
        'd1_window_steps',None),**kwargs)


def reach_park(machine,observer,*,pre_steps=()):
    """驱动到 W_park:若干前置步→pend 写面→停泊(enter_idle_wait 钩子)→engage。
    返回 (完成步数,W_park)。"""
    completed=0
    for pc in pre_steps:
        observer.note_step(machine.cores[0],pc,completed);completed+=1
    observer.note_step(machine.cores[0],PEND_WRITE_FACE,completed);completed+=1
    machine.stalls+=1  # pend 写面后的停泊圈(stall 分支)
    observer.note_step(machine.cores[0],0x12c5ca,completed,stalled=True)
    machine.idle_wait_cores.update((0,1))
    observer.note_idle_wait_entered(machine,machine.cores[0])
    observer.note_event_clock_engaged(machine,machine.cores[0])
    return completed,observer._w_park


class RegistrationTests(unittest.TestCase):
    def tearDown(self):
        br28_machine.d1_window_observer=None

    def test_module_registration_and_detach(self):
        machine=FakeMachine()
        observer=make_observer(machine)
        self.assertIs(br28_machine.d1_window_observer,observer)
        observer.detach()
        self.assertIsNone(br28_machine.d1_window_observer)

    def test_default_registration_point_is_none(self):
        self.assertIsNone(br28_machine.d1_window_observer)

    def test_invalid_explicit_steps_rejected(self):
        machine=FakeMachine()
        for bad in (0,-5,1.5,True,'x'):
            with self.assertRaises(ValueError):
                D1WindowObserver(machine,machine,d1_window_steps=bad)


class ModelStepTotalTests(unittest.TestCase):
    """双轴计数器模型步轴(真 DualCoreMachine;=completed+stalls+eca 与
    hardware.advance 调用数一一对应,br28_machine.model_step_total 注记口径)。"""

    def test_formula_tracks_all_three_components(self):
        machine=br28_machine.DualCoreMachine()
        self.assertEqual(machine.model_step_total(0),0)
        machine.stalls=7;machine.event_clock_advances=13
        self.assertEqual(machine.model_step_total(100),120)
        machine.stalls+=1;machine.event_clock_advances+=40_000
        self.assertEqual(machine.model_step_total(101),101+8+40_013)


class WParkAnchoringTests(unittest.TestCase):
    def tearDown(self):
        br28_machine.d1_window_observer=None

    def test_boot_park_does_not_open_window(self):
        """负例:boot 期 os_time_dly 途中停泊(engage 早于 pend 写面)不触发锚。"""
        machine=FakeMachine()
        observer=make_observer(machine)
        machine.idle_wait_cores.update((0,1))
        observer.note_event_clock_engaged(machine,machine.cores[0])
        self.assertIsNone(observer._w_park)
        tree=observer.finalize(stop={'kind':'instruction_budget'})
        self.assertNotIn('park',tree)
        self.assertEqual(tree['window_blocked_events'],[])

    def test_pend_write_then_engage_opens_window(self):
        machine=FakeMachine()
        observer=make_observer(machine)
        completed,w=reach_park(machine,observer)
        self.assertIsInstance(w,int)
        tree=observer.finalize(stop={'kind':'window_complete'})
        park=tree['park']
        self.assertEqual(park['model_step'],w)
        self.assertEqual(park['instruction_step'],completed)
        self.assertTrue(park['event_clock_engaged'])
        self.assertEqual(park['cores']['0']['pc'],'0x12c5ca')
        self.assertEqual(park['cores']['0']['via'],'scheduler_idle_switch')
        self.assertEqual(park['queue_count'],0)   # h[TCB+6] 互证(RAM 直读)
        self.assertEqual(park['timer0_state']['period_closed_form'],40000)
        self.assertEqual(park['core_tick_state']['deadline_closed_form'],
                         w+143173)
        self.assertIn('assumed-not-measured',park['core_tick_state']['assumption'])

    def test_top_level_core_tick_state_mirror(self):
        machine=FakeMachine()
        observer=make_observer(machine)
        reach_park(machine,observer)
        tree=observer.finalize(stop={'kind':'window_complete'})
        self.assertEqual(tree['core_tick_state'],tree['park']['core_tick_state'])


class WindowStepsTests(unittest.TestCase):
    def tearDown(self):
        br28_machine.d1_window_observer=None

    def _n_for(self,*,s_tick,p_park,explicit=None):
        # con=0x1401 → per_step=3、divisor=1:prd=P_park*3-1 反推真实闭式周期
        machine=FakeMachine(tick_cnt=(1200000-s_tick*4) if s_tick is not None else 627308,
                            tick_progress=4,
                            prd=(p_park*3-1) if p_park is not None else 119999,
                            con=0x1401 if p_park is not None else None)
        if s_tick is None:
            machine.core_ticks[0].cnt=None;machine.core_ticks[0].prd=None
            machine.core_ticks[0].progress=None
        if p_park is None:
            machine.timer.con=None;machine.timer.prd=None
        observer=make_observer(machine,d1_window_steps=explicit)
        reach_park(machine,observer)
        return observer

    def test_formula_default_shape(self):
        # S_tick=143173 → N=max(303173,200000)=303173;303173//40000=7≥3 不延长
        observer=self._n_for(s_tick=143173,p_park=40000)
        self.assertEqual(observer._n_effective,303173)
        self.assertFalse(observer._n_extended)

    def test_h8_automatic_extension(self):
        # P=70000,S_tick=10000 → 公式 N=200000;200000//70000=2<3 → 延长 3*P
        observer=self._n_for(s_tick=10000,p_park=70000)
        self.assertEqual(observer._n_effective,3*70000)
        self.assertTrue(observer._n_extended)

    def test_explicit_steps_never_extended(self):
        observer=self._n_for(s_tick=10000,p_park=70000,explicit=100000)
        self.assertEqual(observer._n_effective,100000)
        self.assertFalse(observer._n_extended)

    def test_missing_s_tick_falls_back_to_floor(self):
        observer=self._n_for(s_tick=None,p_park=40000)
        self.assertEqual(observer._n_effective,200000)

    def test_missing_period_no_extension_no_guess(self):
        observer=self._n_for(s_tick=143173,p_park=None)
        self.assertEqual(observer._n_effective,303173)
        tree=observer.finalize(stop={'kind':'window_complete'})
        self.assertIsNone(tree['park']['timer0_state']['period_closed_form'])
        sources={e['source']:e for e in tree['health']['per_source']}
        self.assertIsNone(sources['timer0']['period'])


class PerSourceEmitTests(unittest.TestCase):
    def tearDown(self):
        br28_machine.d1_window_observer=None

    def _sources(self,*,cfg523):
        machine=FakeMachine(cfg523=cfg523)
        observer=make_observer(machine)
        reach_park(machine,observer)
        tree=observer.finalize(stop={'kind':'window_complete'})
        return {entry['source']:entry for entry in tree['health']['per_source']}

    def test_keyscan_requires_runtime_evidence(self):
        """emit 纪律(§3.1):cfg523_installed_at=None→keyscan 无条目;非 None→必在
        (period=10×P_timer0 实读)。"""
        missing=self._sources(cfg523=None)
        self.assertNotIn('keyscan',missing)
        present=self._sources(cfg523={'pc':'0x60010b2','object_bytes':70})
        self.assertIn('keyscan',present)
        self.assertEqual(present['keyscan']['kind'],'sys_timer')
        self.assertEqual(present['keyscan']['period'],10*40000)

    def test_adc_scan_unconditional(self):
        """emit 纪律(§3.2):adc_scan 无条件必在(period=5×P_timer0)。"""
        for cfg523 in (None,{'pc':'0x60010b2'}):
            sources=self._sources(cfg523=cfg523)
            self.assertIn('adc_scan',sources)
            self.assertEqual(sources['adc_scan']['period'],5*40000)

    def test_timer0_core_tick_period_faces(self):
        sources=self._sources(cfg523=None)
        self.assertEqual(sources['timer0']['period'],40000)       # park 态闭式
        self.assertEqual(sources['core_tick']['period'],300000)   # (PRD+1)/progress
        self.assertEqual(sources['timer0']['first_deadline'],
                         sources['timer0']['first_deadline'])     # int|None 如实
        self.assertTrue(callable(getattr(sources['core_tick']['first_deadline'],'__eq__',None)))

    def test_aperiodic_source_only_on_touch(self):
        """非周期源触达才入表(§2.4);窗口内 irq124 交付→条目+计数。"""
        machine=FakeMachine()
        observer=make_observer(machine)
        completed,w=reach_park(machine,observer)
        machine.stalls+=1
        observer.note_irq_delivery(machine,{'kind':'software_irq_entry','core':0,
                                            'irq':124,'reti_from':'0x12c5ca'},True)
        tree=observer.finalize(stop={'kind':'window_complete'})
        sources={e['source']:e for e in tree['health']['per_source']}
        self.assertIn('irq124',sources)
        self.assertEqual(sources['irq124']['deliveries'],1)
        self.assertIsNone(sources['irq124']['period'])

    def test_boot_delivery_not_counted_in_window(self):
        """窗口差分:W_park 前的 boot 交付不进 per_source.deliveries。"""
        machine=FakeMachine()
        observer=make_observer(machine)
        observer.note_irq_delivery(machine,{'kind':'timer0_irq_entry','core':0,
                                            'irq':4,'reti_from':'0x6066318'},True)
        reach_park(machine,observer)
        machine.stalls+=1
        observer.note_irq_delivery(machine,{'kind':'timer0_irq_entry','core':0,
                                            'irq':4,'reti_from':'0x12c5ca'},True)
        tree=observer.finalize(stop={'kind':'window_complete'})
        sources={e['source']:e for e in tree['health']['per_source']}
        self.assertEqual(sources['timer0']['deliveries'],1)   # 仅窗口内那次


class DeliveryAccountingTests(unittest.TestCase):
    def tearDown(self):
        br28_machine.d1_window_observer=None

    def test_delivery_model_step_wake_correction(self):
        """正常圈交付(woke=False)绑 completed+1;停泊唤醒(woke=True)不加。"""
        machine=FakeMachine()
        observer=make_observer(machine)
        reach_park(machine,observer)
        machine.stalls=10;machine.event_clock_advances=5
        observer._completed=100
        observer.note_irq_delivery(machine,{'kind':'timer0_irq_entry','core':0,
                                            'irq':4,'reti_from':'0x12c5ca'},True)
        self.assertEqual(observer._deliveries[-1]['model_step'],115)
        observer.note_irq_delivery(machine,{'kind':'timer0_irq_entry','core':0,
                                            'irq':4,'reti_from':'0x6010000'},False)
        self.assertEqual(observer._deliveries[-1]['model_step'],116)

    def test_rti_counts_completions(self):
        machine=FakeMachine()
        observer=make_observer(machine)
        reach_park(machine,observer)
        machine.stalls+=1
        observer.note_irq_delivery(machine,{'kind':'timer0_irq_entry','core':0,
                                            'irq':4,'reti_from':'0x12c5ca'},True)
        cpu=machine.cores[0];cpu.reti=0x12c5ca
        observer.note_irq_rti(machine,'timer0_irq_rti',cpu)
        tree=observer.finalize(stop={'kind':'window_complete'})
        sources={e['source']:e for e in tree['health']['per_source']}
        self.assertEqual(sources['timer0']['completions'],1)
        self.assertEqual(tree['timer0_cycles'][0]['rti_pc'],'0x12ed0c')


class DualAxisTests(unittest.TestCase):
    def tearDown(self):
        br28_machine.d1_window_observer=None

    def test_event_clock_segment_advances_model_axis_only(self):
        """双轴纪律(RBKREV P0):快进段只耗模型步轴(eca),指令步轴不变。"""
        machine=FakeMachine()
        observer=make_observer(machine)
        completed,w=reach_park(machine,observer)
        before_total=machine.model_step_total(observer._completed)
        machine.event_clock_advances+=36780   # 快进段(不执行指令)
        observer.note_step(machine.cores[0],0x12ece8,completed+1)  # 唤醒后一指令步
        tree=observer.finalize(stop={'kind':'window_complete'})
        self.assertEqual(tree['d1_model_steps_total'],before_total+36780+1)
        self.assertEqual(tree['d1_window_instruction_steps'],(completed+1)-completed)

    def test_totals_present_and_ordered(self):
        machine=FakeMachine()
        observer=make_observer(machine)
        reach_park(machine,observer)
        tree=observer.finalize(stop={'kind':'window_complete'})
        self.assertIsInstance(tree['d1_model_steps_total'],int)
        self.assertGreaterEqual(tree['d1_model_steps_total'],tree['park']['model_step'])
        self.assertIsInstance(tree['d1_window_instruction_steps'],int)


class WindowTruncationTests(unittest.TestCase):
    def tearDown(self):
        br28_machine.d1_window_observer=None

    def _open(self,**kwargs):
        machine=FakeMachine(**kwargs)
        observer=make_observer(machine)
        reach_park(machine,observer)
        return machine,observer

    def test_horizon_limit_is_remaining_budget(self):
        machine,observer=self._open()
        machine.event_clock_advances+=observer._n_effective-5
        self.assertEqual(observer.event_clock_horizon_limit(machine),5)

    def test_horizon_limit_zero_at_exhaustion(self):
        machine,observer=self._open()
        machine.event_clock_advances+=observer._n_effective
        self.assertEqual(observer.event_clock_horizon_limit(machine),0)
        self.assertEqual(observer.window_stop_request['kind'],'window_complete')

    def test_horizon_limit_none_before_window(self):
        machine,observer=self._open()
        observer2=D1WindowObserver(machine,machine)
        observer.detach();observer2.detach()
        fresh=D1WindowObserver(FakeMachine(),FakeMachine.__new__(FakeMachine))
        # 窗口未开:零干预(None)
        fake=FakeMachine()
        fresh2=D1WindowObserver(fake,fake)
        self.assertIsNone(fresh2.event_clock_horizon_limit(fake))

    def test_mainloop_side_stop_request(self):
        machine,observer=self._open()
        completed=observer._completed
        for i in range(observer._n_effective+1):
            observer.note_step(machine.cores[0],0x6010000,completed+i)
        stop=observer.window_stop_request
        self.assertEqual(stop['kind'],'window_complete')
        self.assertEqual(stop['window']['model_step_start'],observer._w_park)
        self.assertEqual(stop['window']['steps'],observer._n_effective)

    def test_finalize_marks_complete_and_blocked(self):
        machine,observer=self._open()
        reach=observer._w_park
        tree=observer.finalize(stop={'kind':'window_complete'})
        self.assertTrue(tree['health']['window']['complete'])
        machine2,observer2=self._open()
        tree2=observer2.finalize(stop={'kind':'unmapped_memory','address':'0x1'})
        self.assertFalse(tree2['health']['window']['complete'])
        self.assertEqual(len(tree2['window_blocked_events']),1)
        self.assertEqual(tree2['window_blocked_events'][0]['kind'],'unmapped_memory')


class WatchpointFaceTests(unittest.TestCase):
    def tearDown(self):
        br28_machine.d1_window_observer=None

    def test_counts_executions_and_skips_stalled_idle(self):
        machine=FakeMachine()
        observer=make_observer(machine)
        observer.note_step(machine.cores[0],S19_LOOP_HEAD,0)
        observer.note_step(machine.cores[0],S19_LOOP_HEAD,1)
        observer.note_step(machine.cores[0],0x12c5ca,2,stalled=True)  # idle 未执行
        face=observer.machine_face()
        self.assertEqual(face['watchpoint_hits']['0x60123e6'],2)
        self.assertNotIn('0x12c5ca',face['watchpoint_hits'])

    def test_park_log_entries_carry_model_step(self):
        machine=FakeMachine()
        observer=make_observer(machine)
        reach_park(machine,observer)
        machine.idle_wait_cores.add(0)
        observer.note_irq_delivery(machine,{'kind':'timer0_irq_entry','core':0,
                                            'irq':4,'reti_from':'0x12c5ca'},True)
        face=observer.machine_face()
        kinds=[e['kind'] for e in face['idle_wait_park_log']]
        self.assertEqual(kinds,['core_idle_wait_entered','core_woken_by_delivery'])
        for entry in face['idle_wait_park_log']:
            self.assertIsInstance(entry['model_step'],int)


class ChainTrackerTests(unittest.TestCase):
    def tearDown(self):
        br28_machine.d1_window_observer=None

    def _chain_observer(self):
        machine=FakeMachine()
        return make_observer(machine)

    def _full_chain_steps(self,cpu):
        cpu.R=[0x60a1d11,0x10000C,18,0]+[0]*12
        yield POST_API
        for pc in (*ENQUEUE_SITES,GET_EVENT_CALL,CONSUME_PC,BIT20_PC,
                   SPECIAL_PATH_PC,S18_HANDLER_PC,PEND_WRITE_FACE):
            yield pc

    def test_full_chain_marks_pinned_stages_true(self):
        observer=self._chain_observer()
        machine=observer.machine;cpu=machine.cores[0]
        completed=0
        for pc in self._full_chain_steps(cpu):
            observer.note_step(cpu,pc,completed);completed+=1
        face=observer.machine_face()
        chain=face['message_chain_observations']
        samples=chain['samples']
        self.assertEqual(len(samples),1)
        sample=samples[0]
        self.assertEqual(sample['event'],'(18,0)')
        self.assertEqual(sample['origin'],'firmware-natural')
        self.assertEqual(sample['id_read_value'],None)   # 锚 [待 PR-2 实测]
        for stage in ('posted','enqueued','get_event_returned_13','bit20_checked',
                      'special_path','handler_executed','pend_again'):
            self.assertIs(sample[stage],True,(stage))
        for stage in ('id_read','table_record_hit','handler_returned'):
            self.assertIsNone(sample[stage],(stage))      # 未钉锚=未观测
        self.assertEqual(chain['verdict'],'samples')

    def test_broken_chain_flags_skipped_stage_false(self):
        observer=self._chain_observer()
        cpu=observer.machine.cores[0]
        cpu.R=[0x60a1d11,0x10000C,20,2]+[0]*12
        completed=0
        for pc in (POST_API,GET_EVENT_CALL,CONSUME_PC,S18_HANDLER_PC):
            # 缺 enqueued/bit20/special_path 站:后续站命中→中间站 False
            observer.note_step(cpu,pc,completed);completed+=1
        chain=observer.machine_face()['message_chain_observations']
        sample=chain['samples'][0]
        self.assertEqual(sample['event'],'(20,2)')
        self.assertIs(sample['enqueued'],False)
        self.assertIs(sample['bit20_checked'],False)
        self.assertIs(sample['special_path'],False)
        self.assertEqual(chain['verdict'],'chain_broken')
        self.assertIn('broken_evidence',chain)

    def test_s18_preset_samples_are_prewindow(self):
        machine=FakeMachine()
        observer=make_observer(machine)
        cpu=machine.cores[0]
        cpu.R=[0x60a1d11,0x10000C,18,0]+[0]*12
        observer.note_step(cpu,POST_API,0)
        reach_park(machine,observer)
        chain=observer.machine_face()['message_chain_observations']
        self.assertEqual(len(chain['s18_preset_samples']),1)
        self.assertEqual(chain['s18_preset_samples'][0][0],'(18,0)')


class KeyContractTests(unittest.TestCase):
    """键面与冻结契约逐键对齐(§7 单测表;观察器不 import checker,此处对照)。"""

    def test_chain_stages_match_checker(self):
        self.assertEqual(list(CHAIN_STAGES),checker.CHAIN_STAGES)

    def test_timer0_period_matches_checker_derivation(self):
        for con,prd in ((0x1401,119999),(0x1401,59999),(0,119999),(0x1401,None),
                        (None,None),(0x1c01,47999)):
            self.assertEqual(timer0_period_closed_form(con,prd),
                             checker._timer0_expected_period_steps({'con':con,'prd':prd}),
                             (con,prd))

    def test_core_tick_closed_forms(self):
        self.assertEqual(core_tick_deadline_closed_form(627308,1199999,4),143173)
        self.assertEqual(core_tick_deadline_closed_form(1199999,1199999,4),1)
        self.assertIsNone(core_tick_deadline_closed_form(1199999,1199999,0))
        self.assertIsNone(core_tick_deadline_closed_form(None,5,4))

    def test_scenario_tree_keys_match_frozen_contract(self):
        """finalize 树键集=d1_health_scenario(契约可执行实例)∪蓝图 §4.3 镜像子树,
        不发明契约外的 health 键。"""
        machine=FakeMachine()
        observer=make_observer(machine)
        reach_park(machine,observer)
        tree=observer.finalize(stop={'kind':'window_complete'})
        # 顶层(合成样本键 ⊂ 观察器键;观察器多出的是蓝图镜像子树)
        for key in ('park','timer0_cycles','health','d1_model_steps_total',
                    'd1_window_instruction_steps','window_blocked_events','projection'):
            self.assertIn(key,tree)
        # health 子树逐键==冻结契约(d1-health-checks §2)
        reference=checker.__dict__  # 契约键集由消费面(_h1.._h8)钉:
        health=tree['health']
        for key in ('window','timer0_expiries','timer0_expiry_steps','per_source',
                    'event_reposts','queue_balance','per_core','queue'):
            self.assertIn(key,health)
        # park 子树逐键
        for key in ('model_step','instruction_step','cores','event_clock_engaged',
                    'timer0_state','core_tick_state','queue_count'):
            self.assertIn(key,tree['park'])
        # per_source 条目逐键(契约条目形态;sys_timer 源另带 kind)
        for entry in health['per_source']:
            base={'source','core','period','expirations','deliveries','completions',
                  'first_deadline','pending_at_window_start'}
            expected=base|({'kind'} if entry.get('kind')=='sys_timer' else set())
            self.assertEqual(set(entry),expected,entry.get('source'))
        # timer0_cycles 条目逐键(规格 §2.6)
        for cycle in tree['timer0_cycles']:
            self.assertEqual(set(cycle),
                             {'index','expiry_model_step','gap_from_previous','delivered',
                              'handler_entry_pc','acked','w1c_count','rti_pc','reti_value',
                              're_parked','tick_count_delta','tick_ms_delta',
                              'sys_timer_polled','delivery_delay_steps'})
        self.assertIsNone(reference.get('_unused',None))

    def test_assumption_wording_frozen(self):
        machine=FakeMachine()
        observer=make_observer(machine)
        reach_park(machine,observer)
        tree=observer.finalize(stop={'kind':'window_complete'})
        self.assertEqual(tree['core_tick_state']['assumption'],
                         'core_tick_cnt_held_snapshot:assumed-not-measured (NOT PRD-1)')

    def test_pending_places_stay_none_not_guesses(self):
        """[待 PR-2 实测] 的取数点保持 None/缺键,不硬编码。"""
        machine=FakeMachine(cfg523={'pc':'0x60010b2'})
        observer=make_observer(machine)
        reach_park(machine,observer)
        tree=observer.finalize(stop={'kind':'window_complete'})
        self.assertIsNone(tree['empty_queue_pend']['queue_head'])
        self.assertIsNone(tree['dispatch']['table1_records_walked'])
        self.assertIsNone(tree['dispatch']['dynamic_filter_ptr'])
        self.assertIsNone(tree['core_tick']['jiffies_before'])
        self.assertIsNone(tree['health']['queue']['qlimit'])
        self.assertNotIn('ready_tasks',tree['health'])   # 未扫描=键缺→pending
        for source in ('keyscan','adc_scan'):
            entry=next(e for e in tree['health']['per_source'] if e['source']==source)
            self.assertIsNone(entry['first_deadline'])
            self.assertIsNone(entry['completions'])


class CheckerConsumptionTests(unittest.TestCase):
    """观察器产出能被 checker 判定(§7:键面兼容冒烟;pending 合法、不崩)。"""

    def tearDown(self):
        br28_machine.d1_window_observer=None

    def test_run_all_consumes_observer_output(self):
        machine=FakeMachine(cfg523={'pc':'0x60010b2','object_bytes':70})
        observer=make_observer(machine)
        completed,w=reach_park(machine,observer)
        cpu=machine.cores[0]
        # 窗口内一周期:engage 已过,补一次 timer0 交付+RTI+repark
        machine.event_clock_advances+=36780
        machine.stalls+=1
        observer.note_irq_delivery(machine,{'kind':'timer0_irq_entry','core':0,'irq':4,
            'handler':TIMER0_HANDLER_PC,'reti_from':'0x12c5ca'},True)
        cpu.R=[0x60a1d11,0x10000C,18,0]+[0]*12
        for pc in (POST_API,*ENQUEUE_SITES,GET_EVENT_CALL,CONSUME_PC,BIT20_PC,
                   SPECIAL_PATH_PC,S18_HANDLER_PC,PEND_WRITE_FACE):
            observer.note_step(cpu,pc,completed);completed+=1
        cpu.reti=0x12c5ca
        observer.note_irq_rti(machine,'timer0_irq_rti',cpu)
        machine.timer.expirations=3401+1
        machine.core_ticks[0].expirations=1;machine.core_ticks[0].acknowledgements=1
        tree=observer.finalize(stop={'kind':'window_complete'})
        face=observer.machine_face()
        peripherals={'timer0':{'con':0x1401,'cnt':0,'prd':119999,'pending':False,
                               'expirations':machine.timer.expirations},
                     'core_tick_configuration':[{'con':1,'cnt':0,'prd':1199999,
                                                 'acknowledgements':1,'expirations':1}],
                     'ipmask':0}
        scenario={'name':'observer_smoke','completed_steps':completed,
                  'stop_pc':'0x12c5ca','stop':{'kind':'window_complete'},
                  'machine':{**face,'events':[],'events_truncated':True,
                             'event_clock_projection':{'enabled':True,
                                                       'virtual_model_steps_advanced':
                                                       machine.event_clock_advances}},
                  'peripherals':peripherals,'d1_window':tree}
        for assertion_id,fn in checker.ASSERTIONS:
            result=fn(copy.deepcopy(scenario))
            self.assertIsInstance(result,dict)
            self.assertIn(result['status'],('pass','pending','fail'),assertion_id)
        # d1_5 的 H2 面至少吃到四源条目(不再是整表 missing)
        d15=checker.d1_5_health(copy.deepcopy(scenario))
        names={c['name'] for c in d15['checks']}
        self.assertTrue(any(name.startswith('h2_') and 'bounded' in name
                            for name in names),sorted(names))


class _HookMem:
    def __init__(self,slots):self.slots=dict(slots)
    def read(self,address,length):
        assert (address,length) in ((a,4) for a in self.slots)
        return struct.pack('<I',self.slots[address])


class _HookCpu:
    """最小 CPU 桩(test_br28_machine.RepeatIrqDeliveryCpu 同款面)。"""
    def __init__(self,slots,pc,core_id):
        self.mem=_HookMem(slots);self.pc=pc;self.reti=0;self.steps=7
        self.core_id=core_id;self.flags=0;self.R=[0]*16   # 通用寄存器桩(note_step 链跟踪消费)
        self.sp_known=True;self.sp=0x4013f0;self.usp=None;self.ssp=0x123840
        self.last_explicit_ssp=0x123840
        self.bootstrap_context=None;self.after_then=None;self.repeat=None
        self.active_context=None
        self.icfg=0x300;self.icfg_known=True


class MachineHookIntegrationTests(unittest.TestCase):
    """真 DualCoreMachine+真 BR28Model 路径(test_br28_machine.EventClockTests
    同款世界):钉 br28_machine 侧钩子调用点真实触发——engage/交付/park 钩子+
    事件时钟快进段 horizon 截断(唯一行为件,规格 §5)。合成世界测试只覆盖
    观察器方法本身,这里覆盖机器侧调用面。"""

    def machine(self):
        return DualCoreMachine(software_irq_entry=True,core_tick_entry=True,
                               idle_wait_projection=True,event_clock_projection=True,
                               timer0_irq_entry=True)

    def hardware(self):
        h=BR28Model(cpu1_held_projection=True,initial_cpu1_control=0,
                    initial_irq_config=[(3<<16)|(3<<12)]+[0]*31,initial_ipmask=0,
                    timer0_std12m_source=True,timer0_irq_delivery_requests=True,
                    core_tick_configuration=True,initial_core_tick_controls=(0,0),
                    core_tick_progress=(1,1),core_tick_delivery_requests=True,
                    software_irq_command_projection=True,
                    initial_software_irq_pending=(0,0),software_irq_delivery_requests=True)
        h.enable_multicore([0]*32,0,lambda value:None)
        h.write(CPU1_CONTROL,1)
        h.core_icfg={0:0x300,1:0x300}
        return h

    def parked(self,h):
        m=self.machine()
        cpu0=_HookCpu({0x19fe10:0x12ece8},pc=0x6066318,core_id=0)
        cpu1=_HookCpu({0x19fe10:0x12ece8},pc=0x12c5ca,core_id=1)
        m.cores={0:cpu0,1:cpu1};m.hardware=h;m.idle_wait_cores={0}
        return m,cpu0,cpu1

    def arm_timer0(self,h,cnt,prd,con=0x1401):
        h.write(TIMER0,0x4000);h.write(TIMER0+8,prd)
        h.write(TIMER0+4,cnt);h.write(TIMER0,con)

    def test_hooks_fire_on_the_real_advance_event_clock_path(self):
        """engage/交付/park 钩子经真 advance_event_clock/deliver_timer0_irq 触发;
        窗口未开(pend 写面未达)时 horizon 零干预——行为与无观察器逐点一致。"""
        h=self.hardware()
        m,cpu0,cpu1=self.parked(h)
        observer=D1WindowObserver(m,h)
        self.addCleanup(observer.detach)
        self.arm_timer0(h,9660,119999)   # 真实前缀形状:S=36,780
        with self.assertRaises(IrqDeliveryRequest):
            m.enter_idle_wait(cpu1)
        self.assertEqual(len(observer._engagements),1)
        self.assertEqual(observer._engagements[0]['timer0']['prd'],119999)
        self.assertIsNone(observer._w_park)   # boot 停泊:pend 写面未达,不开窗
        self.assertEqual(m.event_clock_advances,36780)  # 零干预:与基线相同
        self.assertIn('core_idle_wait_entered',
                      [e['kind'] for e in observer._park_log])
        m.deliver_timer0_irq(0)
        self.assertEqual(len(observer._deliveries),1)
        self.assertEqual(observer._deliveries[0]['irq'],4)
        self.assertIn('core_woken_by_delivery',
                      [e['kind'] for e in observer._park_log])

    def test_event_clock_segment_truncated_to_window_boundary(self):
        """快进段 horizon 截断(§5):窗口剩余 5 步 < 闭式 36,780 → 段走 5 步,
        下一轮剩余 0 → Blocked('window_complete')(explore 的既有通道转停点)。"""
        h=self.hardware()
        m,cpu0,cpu1=self.parked(h)
        observer=D1WindowObserver(m,h)
        self.addCleanup(observer.detach)
        self.arm_timer0(h,9660,119999)
        observer._w_park=0;observer._n_effective=5   # 直接置开窗态(判据单测已钉)
        with self.assertRaises(Blocked) as ctx:
            m.enter_idle_wait(cpu1)
        self.assertEqual(ctx.exception.details['kind'],'window_complete')
        self.assertEqual(ctx.exception.details['window'],
                         {'model_step_start':0,'steps':5})
        self.assertEqual(m.event_clock_advances,5)   # 恰在窗口边界,不多走一步
        self.assertTrue(observer._stopped)


class D1WindowHookupTests(unittest.TestCase):
    """路 D1RUN 挂接对表用例(PR2SPEC §7⑧):DualCoreMachine d1_window 参数
    校验/attach 构造注册/after_step 正常圈喂步(自累计)+窗口耗尽 raise
    Blocked/note_stall 停泊圈喂步(不 raise)。挂接位=after_step(c.step 已过
    completed+=1 已做、advance 未跑)与 note_stall(stalls 已 +1)。"""

    def test_d1_window_option_validation(self):
        with self.assertRaises(ValueError):
            DualCoreMachine(d1_window='yes')            # 非布尔
        with self.assertRaises(ValueError):
            DualCoreMachine(d1_window_steps=100)        # steps 需 d1_window
        with self.assertRaises(ValueError):
            DualCoreMachine(d1_window=True,d1_window_steps=0)   # 非正整数
        with self.assertRaises(ValueError):
            DualCoreMachine(d1_window=True,d1_window_steps=True)  # bool 不算 int
        DualCoreMachine(d1_window=True)                 # None=公式 N,合法

    def test_attach_constructs_and_registers_the_observer(self):
        h=self.hardware()
        m=DualCoreMachine(cpu1_reset_pc=0,software_irq_entry=True,idle_wait_projection=True,
                          event_clock_projection=True,d1_window=True,d1_window_steps=12345)
        cpu0=_HookCpu({0x19fe10:0x12ece8},pc=0x6066318,core_id=0)
        self.assertIsNone(br28_machine.d1_window_observer)
        m.attach(cpu0,lambda:_HookCpu({0x19fe10:0x12ece8},pc=0x12c5ca,core_id=1),h)
        observer=br28_machine.d1_window_observer
        self.assertIsInstance(observer,D1WindowObserver)
        self.assertIs(observer.machine,m)
        self.assertEqual(observer.explicit_steps,12345)
        observer.detach()
        self.assertIsNone(br28_machine.d1_window_observer)
        # 防御兜底:遗留注册被下一次 attach 清掉(d1_window=False 的会话)。
        br28_machine.d1_window_observer=observer
        m2=DualCoreMachine(cpu1_reset_pc=0,software_irq_entry=True,idle_wait_projection=True,
                           event_clock_projection=True,d1_window=True)
        m2.attach(cpu0,lambda:_HookCpu({0x19fe10:0x12ece8},pc=0x12c5ca,core_id=1),
                  self.hardware())
        self.assertIsNot(br28_machine.d1_window_observer,observer)
        br28_machine.d1_window_observer=None

    def hardware(self):
        # 不预调 enable_multicore——attach 会自己调(fresh held-CPU1 守卫)。
        return BR28Model(cpu1_held_projection=True,initial_cpu1_control=0,
                         initial_irq_config=[0]*32,initial_ipmask=0,
                         software_irq_command_projection=True)

    def test_after_step_feeds_instructions_and_raises_window_complete(self):
        h=self.hardware()
        m=DualCoreMachine(cpu1_reset_pc=0,software_irq_entry=True,idle_wait_projection=True,
                          event_clock_projection=True,d1_window=True)
        cpu0=_HookCpu({0x19fe10:0x12ece8},pc=0x6066318,core_id=0)
        m.attach(cpu0,lambda:_HookCpu({0x19fe10:0x12ece8},pc=0x12c5ca,core_id=1),h)
        observer=br28_machine.d1_window_observer
        self.addCleanup(observer.detach)
        observer._w_park=0;observer._n_effective=3   # 直接置开窗态(判据单测已钉)
        for i in range(3):
            try:
                m.after_step(cpu0,0x60123e6+i)       # 正常圈:喂步(自累计)
            except Blocked as e:                     # 第 3 圈:窗口耗尽
                self.assertEqual(e.details['kind'],'window_complete')
                self.assertEqual(e.details['window'],{'model_step_start':0,'steps':3})
                break
            self.assertIsNone(observer.window_stop_request)
        else:
            self.fail('window exhaustion never raised Blocked')
        self.assertEqual(observer._completed,3)      # 自累计指令步=绝对轴

    def test_note_stall_feeds_stalled_steps_without_raising(self):
        h=self.hardware()
        m=DualCoreMachine(cpu1_reset_pc=0,software_irq_entry=True,idle_wait_projection=True,
                          event_clock_projection=True,d1_window=True)
        cpu0=_HookCpu({0x19fe10:0x12ece8},pc=0x6066318,core_id=0)
        m.attach(cpu0,lambda:_HookCpu({0x19fe10:0x12ece8},pc=0x12c5ca,core_id=1),h)
        observer=br28_machine.d1_window_observer
        self.addCleanup(observer.detach)
        observer._w_park=0;observer._n_effective=1
        m.note_stall(cpu0,{'kind':'core_idle_waiting','core':0})
        # 停泊圈:模型步已过窗口边界(stalls+1)但指令步不累计、不 raise——
        # 窗口停请求置位,延到下一正常圈(after_step 兜底)。
        self.assertEqual(observer._completed,0)
        self.assertIsNotNone(observer.window_stop_request)
        with self.assertRaises(Blocked) as ctx:
            m.after_step(cpu0,0x60123e6)
        self.assertEqual(ctx.exception.details['kind'],'window_complete')


if __name__=='__main__':
    unittest.main()
