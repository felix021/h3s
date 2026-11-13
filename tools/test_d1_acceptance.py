#!/usr/bin/env python3
"""Unit tests for tools/check_d1_acceptance.py (route CH / sim-d1assert).

三态契约:pass(全条款有数据且满足)/pending(前沿或观测未到,pending != pass)/
fail(有数据但违反条款)。覆盖:
  - 合成「当前前沿」形状(601,880 停点世界:31 次运行中 Timer0 交付、无 park)
    → 窗口断言 pending,any-context 机制子项真实 pass;
  - 合成 park 世界(BQ 闭式数值:首段 36,780、周期 40,000、core tick 143,173)
    → D1-1..D1-4/BI-A1/A2/A5 pass,A3/A4 pending(不适用非 fail);
  - 逐条款 fail 变体(金丝雀执行/间隔偏离/ACK 不符/jiffies 非 +1/消息链缺环节/
    窗口内未知停点/窗口外交付锚错);
  - 真实 analysis/local-checks/irq-entry.json(存在时):三态合法+窗口未开必 pending
    ——前沿推进后 park 世界数据出现时本测试不挡路(只验结构合法);
  - 周期推导与模型闭式(steps_to_next_expiration,BQ)互证。
"""
import copy
import json
import sys
import unittest
from pathlib import Path

sys.path.insert(0,str(Path(__file__).resolve().parent))
from br28_model import Timer0
from check_d1_acceptance import (ASSERTIONS,run_all,park_window,
                                 _timer0_expected_period_steps,
                                 d1_1_park,d1_2_timer0_cycles,d1_3_core_tick,
                                 d1_4_message_chain,bi_a1_first_timer0_wake,
                                 bi_a2_period_conservation,bi_a5_c2_exit_two_levels)

REPORT=Path(__file__).resolve().parents[1]/'analysis'/'local-checks'/'irq-entry.json'


def frontier_scenario():
    """合成「当前前沿」形状(条件主场景 601,880 停点世界的报告键子集)。"""
    delivery={'kind':'timer0_irq_entry','core':0,'irq':4,'vector_address':'0x19fe10',
              'handler':'0x12ece8','reti_from':'0x12e286','delivery_clears_pending':False,
              'pending_cleared_by_handler_ack':True}
    return {'name':'synthetic_frontier','completed_steps':601880,'stop_pc':'0x6069540',
            'stop':{'kind':'unknown_gpcnt_configuration_bits'},
            'machine':{'events':[{'kind':'cpu1_released'},{'kind':'shared_lock_wait'}],
                       'events_truncated':True,
                       'timer0_irq_entry_projection':{'enabled':True,'deliveries':[delivery]*31},
                       'core_tick_entry_projection':{'enabled':True,'deliveries':[
                           {'kind':'core_tick_entry','core':0,'irq':3,
                            'vector_address':'0x19fe0c','handler':'0x60661a4',
                            'reti_from':'0x12b7b6'}]},
                       'irq_delivery_deferral_projection':{'enabled':True,'count':14,'events':[]}},
            'peripherals':{'timer0':{'con':5121,'cnt':2799,'prd':11999,'pending':False,
                                     'expirations':41},
                           'core_tick_configuration':[{'con':1,'cnt':617452,'prd':1199999,
                                                       'acknowledgements':1,'expirations':1}],
                           'ipmask':0}}


def parked_scenario():
    """合成 park 世界(全部路 CH 观测键就位;数值与 BQ 闭式一致:
    park 边界=model_step 100,Timer0 cnt=9660/prd=119999 → 首段 S=36,780、周期 40,000;
    core tick cnt=627308/prd=1199999/progress=4 → 首溢出=100+143,173=143,273)。"""
    park=100
    expiry=[park+36780,park+76780,park+116780]
    deliveries=[{'kind':'timer0_irq_entry','core':0,'irq':4,'vector_address':'0x19fe10',
                 'handler':'0x12ece8','reti_from':'0x12c5ca','delivery_clears_pending':False,
                 'pending_cleared_by_handler_ack':True,'model_step':step}
                for step in (park+36781,park+76781,park+116781)]
    timer0_snap={'con':0x1401,'cnt':9660,'prd':119999,'remainder':0,'source_remainder':0}
    segments=[]
    for i in range(3):
        segments.append({'model_step':park,'timer0':dict(timer0_snap),
                         'first_segment_advances':36780 if i==0 else 36780,
                         'inter_segment_execution':3220,
                         'core_ticks':{'0':{'con':1,'cnt':627308,'prd':1199999,'progress':4}}})
    # 段守恒锚定:段1=36,780+3,220(半周期+执行),段2/3=36,780+3,220=40,000=周期 ✓
    events=[{'kind':'core_idle_wait_entered','core':1,'pc':'0x12c5ca'},
            {'kind':'core_idle_wait_entered','core':0,'pc':'0x12c5ca'},
            {'kind':'event_clock_engaged','idle_cores':[0,1]}]
    events+= [{'kind':'timer0_irq_rti','core':0,'reti_from_frame':'0x12c5ca'}]*3
    log=([{'kind':'core_idle_wait_entered','core':1,'pc':'0x12c5ca','model_step':park},
          {'kind':'core_idle_wait_entered','core':0,'pc':'0x12c5ca','model_step':park}]
         +[{'kind':'core_idle_wait_entered','core':0,'pc':'0x12c5ca','model_step':park+s}
           for s in (36790,76790,116790)])
    sample={'event':'(18,0)','origin':'firmware-natural','posted':True,'enqueued':'0x10000C03',
            'get_event_returned_13':True,'id_read':True,'id_read_value':'0x10000c',
            'bit20_checked':True,'special_path':True,'table_record_hit':'table1[39]',
            'handler_executed':True,'handler_returned':True,'pend_again':True}
    return {'name':'synthetic_parked','completed_steps':1000000,
            'stop_pc':'0x12c5ca','stop':{'kind':'instruction_budget'},
            'machine':{'events':events,'events_truncated':False,
                       'idle_wait_park_log':log,
                       'watchpoint_hits':{'0x60123e6':2,'0x6064c8c':2,
                                          '0x601249c':0,'0x60124ae':0,'0x60653ae':1},
                       'event_clock_projection':{'enabled':True,'virtual_model_steps_advanced':36780*3},
                       'event_clock_engagements':segments,
                       'timer0_irq_entry_projection':{'enabled':True,'deliveries':deliveries},
                       'core_tick_entry_projection':{'enabled':True,'deliveries':[
                           {'kind':'core_tick_entry','core':0,'irq':3,
                            'vector_address':'0x19fe0c','handler':'0x60661a4',
                            'reti_from':'0x12c5ca','model_step':park+143173,
                            'eligibility_note':{'ipmask':0,'priority':1,'eligible':True}}]},
                       'jiffies_observations':[{'before':115,'after':116}],
                       'task_switch_observations':[{'from':'idle','to':'pmu_task'}],
                       'message_chain_observations':{
                           'samples':[sample],'s18_preset_samples':[['(18,0)','short-circuit'],
                                                                    ['(20,2)','sys_timer path']],
                           'window_covered':True,'last_dispatch_step':park+50000,
                           'verdict':'samples'}},
            'peripherals':{'timer0':{'con':5121,'cnt':0,'prd':119999,'pending':False,
                                     'expirations':44,'w1c_acknowledgements':3,
                                     'expiration_model_steps':expiry},
                           'core_tick_configuration':[{'con':1,'cnt':627308,'prd':1199999,
                                                       'acknowledgements':1,'expirations':1,
                                                       'expiration_model_steps':[park+143173]}],
                           'ipmask':0}}


def no_event_scenario():
    """A3 适用世界:无事件诚实停(零推进)。"""
    return {'name':'synthetic_no_event','completed_steps':10,'stop_pc':'0x12c5ca',
            'stop':{'kind':'all_cores_idle_waiting',
                    'note':'no modeled event can fire: no counting core tick, no counting/'
                           'eligible Timer0 and no set software pending bit (masked pending or '
                           'IRQ never wakes)'},
            'machine':{'events':[{'kind':'core_idle_wait_entered','core':1,'pc':'0x12c5ca'},
                                 {'kind':'core_idle_wait_entered','core':0,'pc':'0x12c5ca'}],
                       'events_truncated':False,
                       'event_clock_projection':{'enabled':True,'virtual_model_steps_advanced':0}},
            'peripherals':{'timer0':{'con':0,'cnt':0,'prd':119999,'pending':False,'expirations':0},
                           'core_tick_configuration':[{'con':0,'cnt':0,'prd':0,
                                                       'acknowledgements':0,'expirations':0}],
                           'ipmask':0}}


class WindowTests(unittest.TestCase):
    def test_frontier_window_closed(self):
        opened,why=park_window(frontier_scenario())
        self.assertFalse(opened);self.assertIn('park window not reached',why)

    def test_parked_window_open(self):
        opened,why=park_window(parked_scenario())
        self.assertTrue(opened)

    def test_honest_stop_opens_window(self):
        opened,_=park_window(no_event_scenario())
        self.assertTrue(opened)


class PendingTests(unittest.TestCase):
    """前沿未到 park:窗口断言 pending(pending != pass),机制子项真实 pass。"""

    def test_all_assertions_pending_on_frontier(self):
        for assertion_id,_ in ASSERTIONS:
            fn=dict((aid,f) for aid,f in ASSERTIONS)[assertion_id]
            result=fn(frontier_scenario())
            self.assertIn(result['status'],('pending',),f'{assertion_id} must be pending '
                         f'on a pre-park frontier, got {result["status"]}')

    def test_frontier_mechanism_subchecks_really_pass(self):
        result=d1_2_timer0_cycles(frontier_scenario())
        by={c['name']:c for c in result['checks']}
        self.assertEqual(by['delivery_mechanism_shape']['status'],'pass')
        self.assertEqual(by['expiry_delivery_consistency']['status'],'pass')
        self.assertEqual(by['park_window']['status'],'pending')
        result=d1_3_core_tick(frontier_scenario())
        by={c['name']:c for c in result['checks']}
        self.assertEqual(by['ack_exactly_once_per_delivery']['status'],'pass')

    def test_pending_reports_missing_counters(self):
        result=d1_1_park(frontier_scenario())
        self.assertEqual(result['status'],'pending')
        # 窗口未开时 early-return,观测缺口从静态清单 required_observations 可见
        # (HANDOFF 路 CH 计数器需求的机器可读来源)
        result=d1_2_timer0_cycles(frontier_scenario())
        self.assertIn('expiration_model_steps',
                      json.dumps(result['required_observations']))


class PassTests(unittest.TestCase):
    def test_parked_world_d1_pass(self):
        for fn in (d1_1_park,d1_2_timer0_cycles,d1_3_core_tick,d1_4_message_chain):
            result=fn(parked_scenario())
            self.assertEqual(result['status'],'pass',
                             f'{result["id"]}: {json.dumps(result["failures"]+result["missing"])}')

    def test_parked_world_bi_a1_a2_a5_pass(self):
        for fn in (bi_a1_first_timer0_wake,bi_a2_period_conservation,bi_a5_c2_exit_two_levels):
            result=fn(parked_scenario())
            self.assertEqual(result['status'],'pass',
                             f'{result["id"]}: {json.dumps(result["failures"]+result["missing"])}')

    def test_no_event_world_a3_pass(self):
        from check_d1_acceptance import bi_a3_no_event_honest_stop
        self.assertEqual(bi_a3_no_event_honest_stop(no_event_scenario())['status'],'pass')

    def test_a4_pending_when_core_tick_counts(self):
        from check_d1_acceptance import bi_a4_timer0_only_horizon
        result=bi_a4_timer0_only_horizon(parked_scenario())
        self.assertEqual(result['status'],'pending')  # 非 Timer0-only 世界:不适用=pending


class FailTests(unittest.TestCase):
    def _mutated(self):
        return copy.deepcopy(parked_scenario())

    def test_canary_execution_fails_d1_1(self):
        scenario=self._mutated()
        scenario['machine']['watchpoint_hits']['0x601249c']=1
        result=d1_1_park(scenario)
        self.assertEqual(result['status'],'fail')

    def test_wrong_park_pc_fails_d1_1(self):
        scenario=self._mutated()
        for entry in scenario['machine']['idle_wait_park_log']:
            if entry.get('core')==1:entry['pc']='0x6066318'
        self.assertEqual(d1_1_park(scenario)['status'],'fail')

    def test_interval_off_by_one_fails_d1_2(self):
        scenario=self._mutated()
        scenario['peripherals']['timer0']['expiration_model_steps']=[36780,76781,116780]
        result=d1_2_timer0_cycles(scenario)
        self.assertEqual(result['status'],'fail')

    def test_w1c_mismatch_fails_d1_2(self):
        scenario=self._mutated()
        scenario['peripherals']['timer0']['w1c_acknowledgements']=2
        self.assertEqual(d1_2_timer0_cycles(scenario)['status'],'fail')

    def test_unknown_stop_in_window_fails_d1_2(self):
        scenario=self._mutated()
        scenario['stop']={'kind':'unmapped_memory','address':'0xf2c064'}
        self.assertEqual(d1_2_timer0_cycles(scenario)['status'],'fail')

    def test_delivery_anchor_mismatch_fails_even_pre_park(self):
        # any-context 机制子项:窗口未开也照判 fail(机制锚错与窗口无关)
        scenario=frontier_scenario()
        scenario['machine']['timer0_irq_entry_projection']['deliveries'][0]['handler']='0xdeadbee'
        self.assertEqual(d1_2_timer0_cycles(scenario)['status'],'fail')

    def test_jiffies_delta_two_fails_d1_3(self):
        scenario=self._mutated()
        scenario['machine']['jiffies_observations']=[{'before':115,'after':117}]
        self.assertEqual(d1_3_core_tick(scenario)['status'],'fail')

    def test_core_tick_ack_mismatch_fails_d1_3(self):
        scenario=self._mutated()
        scenario['peripherals']['core_tick_configuration'][0]['acknowledgements']=2
        self.assertEqual(d1_3_core_tick(scenario)['status'],'fail')

    def test_core_tick_closed_form_mismatch_fails_d1_3(self):
        # 闭式对照的是溢出步号(expiration_model_steps),交付步号允许延迟(codex 口径)
        scenario=self._mutated()
        scenario['peripherals']['core_tick_configuration'][0]['expiration_model_steps']=[140000]
        self.assertEqual(d1_3_core_tick(scenario)['status'],'fail')

    def test_core_tick_delivery_delay_is_not_a_closed_form_failure(self):
        # 交付晚于溢出(延迟交付)不算闭式失败——溢出步号仍精确
        scenario=self._mutated()
        scenario['machine']['core_tick_entry_projection']['deliveries'][0]['model_step']=143273+500
        self.assertEqual(d1_3_core_tick(scenario)['status'],'pass')

    def test_message_chain_observed_broken_stage_fails_d1_4(self):
        # 环节 False(观测到没发生)=链路断 → fail(codex 复核口径:观察到失败才算 fail)
        scenario=self._mutated()
        scenario['machine']['message_chain_observations']['samples'][0]['handler_returned']=False
        result=d1_4_message_chain(scenario)
        self.assertEqual(result['status'],'fail')

    def test_message_chain_unobserved_stage_is_pending_not_fail(self):
        # 环节键缺(未观测)=观测缺口 → pending,不算观察到失败
        scenario=self._mutated()
        del scenario['machine']['message_chain_observations']['samples'][0]['handler_returned']
        result=d1_4_message_chain(scenario)
        self.assertEqual(result['status'],'pending')

    def test_message_chain_missing_origin_is_pending(self):
        scenario=self._mutated()
        del scenario['machine']['message_chain_observations']['samples'][0]['origin']
        self.assertEqual(d1_4_message_chain(scenario)['status'],'pending')

    def test_broken_chain_is_fail_not_no_sample(self):
        # post 成功却在链上丢失 = 链路不通 → fail,不能算无样本(d1-acceptance §1 D1-4)
        scenario=self._mutated()
        sample=scenario['machine']['message_chain_observations']['samples'][0]
        for stage in ('enqueued','get_event_returned_13','id_read','bit20_checked',
                      'special_path','table_record_hit','handler_executed','handler_returned',
                      'pend_again'):
            sample[stage]=False
        result=d1_4_message_chain(scenario)
        self.assertEqual(result['status'],'fail')

    def test_external_injected_sample_is_not_accepted(self):
        # 外部注入样本不算 D1-4(固件自然产生约束,d1-acceptance §1 D1-4)——
        # 只有注入样本时无完成样本 → pending(未覆盖),绝不能 pass
        scenario=self._mutated()
        scenario['machine']['message_chain_observations']['samples'][0]['origin']='injected'
        result=d1_4_message_chain(scenario)
        self.assertIn(result['status'],('pending',))

    def test_timeslice_never_executed_fails_d1_3(self):
        scenario=self._mutated()
        scenario['machine']['watchpoint_hits']['0x60653ae']=0
        self.assertEqual(d1_3_core_tick(scenario)['status'],'fail')


class D1_4VerdictTests(unittest.TestCase):
    def _mutated(self):
        return copy.deepcopy(parked_scenario())

    def test_queue_empty_verdict_is_pending_not_fail(self):
        # 回调表空/队列空 = 链路通无事件源:D1-4 未覆盖(pending),不是 fail
        scenario=self._mutated()
        chain=scenario['machine']['message_chain_observations']
        chain['samples']=[];chain['verdict']='queue_empty_no_source';chain['window_covered']=True
        self.assertEqual(d1_4_message_chain(scenario)['status'],'pending')

    def test_no_sample_without_verdict_is_pending_missing(self):
        scenario=self._mutated()
        chain=scenario['machine']['message_chain_observations']
        chain['samples']=[];chain['verdict']=None
        result=d1_4_message_chain(scenario)
        self.assertEqual(result['status'],'pending')
        self.assertTrue(any('broken chain' in json.dumps(c) for c in result['missing']),
                        result['missing'])


class ClosedFormTests(unittest.TestCase):
    """断言推导与模型闭式(BQ steps_to_next_expiration)互证,不预锁常数。"""

    def test_period_40000_from_real_shape(self):
        # con=0x1401(STD12M,div=1)/prd=119999 → (120000)/3 = 40,000 模型步
        self.assertEqual(_timer0_expected_period_steps({'con':0x1401,'prd':119999}),40000)

    def test_first_expiry_36780_matches_model_closed_form(self):
        timer=Timer0(allowed_sources=(4,5))
        timer.con=0x1401;timer.cnt=9660;timer.prd=119999
        # BQ 钉死:真实前缀形状 S=(120000-9660)/3=36,780,此后每周期 40,000
        self.assertEqual(timer.steps_to_next_expiration(4),36780)
        self.assertEqual(timer.steps_to_next_expiration(4),(120000-9660+2)//3)

    def test_non_integral_period_degrades_to_missing(self):
        # STD12M div1 prd=1:每周期 2 计数/每步 3 计数 → 非整步周期 → None(不猜值)
        self.assertIsNone(_timer0_expected_period_steps({'con':0x1401,'prd':1}))
        self.assertIsNone(_timer0_expected_period_steps({'con':0,'prd':119999}))
        self.assertIsNone(_timer0_expected_period_steps({'con':0x1401}))

    def test_divisor_table_used_not_formula(self):
        # divisor 查 DIVISORS 表(非等比):div 字段 1 → DIVISORS[1]=4;字段 4 → DIVISORS[4]=2
        self.assertEqual(_timer0_expected_period_steps({'con':0x1411,'prd':29999}),40000)
        self.assertEqual(_timer0_expected_period_steps({'con':0x1441,'prd':59999}),40000)


class RealReportTests(unittest.TestCase):
    """真实 analysis/local-checks/irq-entry.json(存在时;check_local 生成物,git 忽略)。
    只验三态合法与结构;前沿推进出现 park 世界数据时本测试不挡路。"""

    @unittest.skipIf(not REPORT.exists(),'local irq-entry.json not generated in this checkout')
    def test_real_report_assertions_are_wellformed(self):
        report=json.loads(REPORT.read_text())
        names={s.get('name') for s in report.get('scenarios',())}
        self.assertIn('p11_heap_experimental_config',names)
        scenario={s['name']:s for s in report['scenarios']}['p11_heap_experimental_config']
        result=run_all(scenario)
        for assertion in result['assertions']:
            self.assertIn(assertion['status'],('pass','pending','fail'))
            self.assertTrue(assertion['checks'])
            for check in assertion['checks']:
                self.assertIn(check['status'],('pass','pending','fail'))
        opened,_=park_window(scenario)
        if not opened:
            for assertion in result['assertions']:
                self.assertEqual(assertion['status'],'pending',
                                 f'{assertion["id"]} must be pending pre-park')

    @unittest.skipIf(not REPORT.exists(),'local irq-entry.json not generated in this checkout')
    def test_real_frontier_mechanism_shape_passes(self):
        report=json.loads(REPORT.read_text())
        scenario={s['name']:s for s in report['scenarios']}['p11_heap_experimental_config']
        deliveries=scenario['machine'].get('timer0_irq_entry_projection',{}).get('deliveries')
        if not deliveries:return  # 未来报告重构时跳过,不猜
        result=d1_2_timer0_cycles(scenario)
        by={c['name']:c for c in result['checks']}
        self.assertEqual(by['delivery_mechanism_shape']['status'],'pass')


if __name__=='__main__':
    unittest.main()
