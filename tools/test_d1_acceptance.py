#!/usr/bin/env python3
"""Unit tests for tools/check_d1_acceptance.py (route CH / sim-d1assert;
D1-5 health by route PR-1/sim-pr1).

三态契约:pass(全条款有数据且满足)/pending(前沿或观测未到,pending != pass)/
fail(有数据但违反条款)。覆盖:
  - 合成「当前前沿」形状(601,880 停点世界:31 次运行中 Timer0 交付、无 park)
    → 窗口断言 pending,any-context 机制子项真实 pass;
  - 合成 park 世界(BQ 闭式数值:首段 36,780、周期 40,000、core tick 143,173)
    → D1-1..D1-4/BI-A1/A2/A5 pass,A3/A4 pending(不适用非 fail);
  - 合成 D1 健康世界(d1_window.health 键契约 PASS 形状,批 3 任务书 §1.0b)
    → D1-5 H1-H7 pass + run_all d1_gate=pass;逐规则 FAIL 变体(到期不足/间隔漂移/
    交付风暴/软件 IRQ 交付/同事件重发/重复消费/零唤醒/卡运行态/断言面饿死/
    积压超限/序号回退/取模不一致/步号倒流/转换流断链)+ pending 语义(键缺≠零事件
    ≠PASS、core tick 假设缺=形状不完整、右边界未闭合)+ 否定式总闸红 +
    FAIL 证据契约形状(H2 三分计数/H4 未闭合唤醒区间);
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
                                 bi_a2_period_conservation,bi_a5_c2_exit_two_levels,
                                 d1_5_health)

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


def d1_health_scenario():
    """合成 D1 窗口健康样本(d1_window.health 键契约的 PASS 形状;PR-1 冻结契约,
    生产者=PR-2 probe 侧;合成样本只证明 checker 有分辨力,不计入 D1 首次过线证据
    ——批 3 任务书 §1.0b 第 2 条)。数值贴 BQ/B3 实测形状:park=100、Timer0 period
    40,000(prd=119999/STD12M/div1 推导)、core tick period 300,000(prd=1199999/
    progress=4,PRD 读回=恒等链不承载冻结假设)、窗口 N=200,000(蓝图 §1.3 下限)。
    窗口内 3 次 Timer0 到期/交付/回 park + 1 次 core tick 交付;CPU1 全窗静默。"""
    park=100
    scenario=copy.deepcopy(parked_scenario())
    scenario['name']='synthetic_d1_health'
    expiry=[park+36780,park+76780,park+116780]      # 与 parked_scenario 相同步号
    timeline0=[];step=park+36781
    for irq,handler in ((4,'0x12ece8'),(4,'0x12ece8'),(4,'0x12ece8'),(3,'0x60661a4')):
        timeline0+=[{'model_step':step,'kind':'woken','pc':'0x12c5ca','irq':irq,
                     'nesting':0},
                    {'model_step':step+50,'kind':'running','pc':handler,'irq':irq,
                     'nesting':0},
                    {'model_step':step+200,'kind':'entered_idle_wait','pc':'0x12c5ca',
                     'irq':None,'nesting':0}]
        step+=40000
    scenario['d1_window']={
      'park':{'model_step':park,'instruction_step':park*4,
              'cores':{'0':{'pc':'0x12c5ca','via':'scheduler_idle_switch'},
                       '1':{'pc':'0x12c5ca'}},
              'event_clock_engaged':True,
              'timer0_state':{'con':'0x1401','cnt':9660,'prd':119999,
                              'period_closed_form':40000},
              'core_tick_state':{'cnt':627308,'prd':1199999,
                                 'deadline_closed_form':park+143173,
                                 'assumption':'core_tick_cnt_held_snapshot:'
                                 'assumed-not-measured (NOT PRD-1)'}},
      'timer0_cycles':[{'index':i,'expiry_model_step':s,'gap_from_previous':40000,
                        'delivered':True,'acked':True,'w1c_count':1,'re_parked':True}
                       for i,s in enumerate(expiry)],
      'health':{
        'window':{'model_step_start':park,'model_step_end':park+200000,'steps':200000,
                  'complete':True},
        'timer0_expiries':3,
        'timer0_expiry_steps':expiry,
        'per_source':[
          {'source':'timer0','core':0,'period':40000,'expirations':3,'deliveries':3,
           'completions':3,'first_deadline':expiry[0],'pending_at_window_start':False},
          {'source':'core_tick','core':0,'period':300000,'expirations':1,'deliveries':1,
           'completions':1,'first_deadline':park+143173,'pending_at_window_start':False},
          {'source':'irq124','core':0,'period':None,'expirations':0,'deliveries':0,
           'completions':0,'first_deadline':None,'pending_at_window_start':False}],
        'event_reposts':[
          {'event_id':'0x10000c','subcode':18,'origin':'firmware-natural','posts':1,
           'consumed':1,'producer_pc':'0x60123aa','first_post_step':park+50000,
           'pending_at_window_end':False}],
        'queue_balance':{'enqueued':1,'dequeued':1},
        'per_core':{
          '0':{'wake_count':4,'rewait_count':4,
               'wake_reasons':{'timer0':3,'core_tick':1},
               'timeline':timeline0,'last_open_wake':None},
          '1':{'wake_count':0,'rewait_count':0,'wake_reasons':{},
               'timeline':[],'last_open_wake':None}},
        'ready_tasks':[],
        'queue':{'count_final':0,'qlimit':16,'capacity':8,
                 'cursor_trace':[{'step':park+50000,'op':'enqueue','seq':0,'physical':0},
                                 {'step':park+50100,'op':'dequeue','seq':0,'physical':0}],
                 'cursor_monotonic':True}},
      'window_blocked_events':[],
      'd1_model_steps_total':park+200000,
      'd1_window_instruction_steps':60000}
    return scenario


def _d15(scenario):
    return d1_5_health(scenario)


def _failed_checks(result,prefix):
    return [c for c in result['checks'] if c['status']=='fail' and c['name'].startswith(prefix)]


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


class D1_5PendingTests(unittest.TestCase):
    """pending 语义:键缺≠零事件≠PASS;core tick 假设缺=形状不完整;右边界未闭合。"""

    def test_frontier_scenario_stays_pending(self):
        self.assertEqual(_d15(frontier_scenario())['status'],'pending')

    def test_parked_world_without_health_keys_pending(self):
        # park 窗口已开但 d1_window.health 未产(PR-2 未落地/前沿未到)→ pending 非 pass
        result=_d15(parked_scenario())
        self.assertEqual(result['status'],'pending')
        self.assertTrue(any('d1_window.health' in (c.get('missing') or '')
                            for c in result['missing']),result['missing'])

    def test_missing_single_key_is_pending_not_pass(self):
        scenario=d1_health_scenario()
        del scenario['d1_window']['health']['timer0_expiries']
        result=_d15(scenario)
        self.assertEqual(result['status'],'pending')
        self.assertTrue(any('timer0_expiries' in (c.get('missing') or '')
                            for c in result['missing']))

    def test_missing_repost_key_is_not_vacuous_pass(self):
        # 边界样本(任务书 §1.0b 第 5 条):键缺不得当成「零事件」vacuous pass
        scenario=d1_health_scenario()
        del scenario['d1_window']['health']['event_reposts']
        result=_d15(scenario)
        self.assertEqual(result['status'],'pending')
        self.assertFalse(any(c['status']=='pass' and c['name'].startswith('h3_')
                             for c in result['checks']))

    def test_core_tick_period_missing_is_pending(self):
        # period 实读缺=无法算上界(不预锁 300,000 兜底)→ pending
        scenario=d1_health_scenario()
        scenario['d1_window']['health']['per_source'][1]['period']=None
        result=_d15(scenario)
        self.assertEqual(result['status'],'pending')
        self.assertTrue(any('period' in (c.get('missing') or '') for c in result['missing']))

    def test_core_tick_cnt_assumption_missing_is_pending(self):
        # §1.4:D1-3 时间断言链(含 H2 的 core tick 周期上界)必须带 CNT 冻结假设,
        # 缺失=断言形状不完整 → pending 非 pass
        scenario=d1_health_scenario()
        del scenario['d1_window']['park']['core_tick_state']['assumption']
        result=_d15(scenario)
        self.assertEqual(result['status'],'pending')
        self.assertTrue(any('assumption' in (c.get('missing') or '')
                            for c in result['missing']))

    def test_truncated_window_open_wake_is_pending_not_fail(self):
        # 右边界条款(任务书 §1.0b 第 8 条):窗口截断+末态 running=未闭合区间,
        # 标「尚未到期」不误 FAIL;last_open_wake 的未闭合证据字段保留
        scenario=d1_health_scenario()
        health=scenario['d1_window']['health']
        health['window']['complete']=False
        core0=health['per_core']['0']
        core0['timeline']=core0['timeline'][:-1]      # 掐掉末条 entered_idle_wait
        core0['rewait_count']=3                        # 4 唤醒只闭合 3
        core0['last_open_wake']={'last_pc':'0x60661a4','in_flight_irq':3,
                                 'nesting_level':1}
        result=_d15(scenario)
        self.assertEqual(result['status'],'pending')
        self.assertIn('in_flight_irq',json.dumps(result['missing']))

    def test_unconsumed_post_is_pending(self):
        # H3:consumed<posts 且 pending_at_window_end → 未闭合样本(pending),
        # 不 fail(非重复分发)也不 pass(需要完整样本)
        scenario=d1_health_scenario()
        health=scenario['d1_window']['health']
        entry=health['event_reposts'][0]
        entry['consumed']=0;entry['pending_at_window_end']=True
        health['queue_balance']={'enqueued':1,'dequeued':0}
        health['queue']['count_final']=1
        health['queue']['cursor_trace']=[{'step':50000,'op':'enqueue','seq':0,
                                          'physical':0}]
        result=_d15(scenario)
        self.assertEqual(result['status'],'pending')
        self.assertFalse(_failed_checks(result,'h3_'))


class D1_5PassTests(unittest.TestCase):
    def test_healthy_world_all_rules_pass(self):
        result=_d15(d1_health_scenario())
        self.assertEqual(result['status'],'pass',
                         json.dumps(result['failures']+result['missing']))
        prefixes={c['name'][:2] for c in result['checks'] if c['status']=='pass'}
        self.assertTrue({'h1','h2','h3','h4','h5','h6','h7'} <= prefixes,
                        sorted(c['name'] for c in result['checks']))

    def test_h6_physical_wraparound_is_legal(self):
        # H6 勘误口径(任务书 §1.0b 第 6 条):物理指针按容量取模回绕合法——
        # seq 6..9 → physical 6,7,0,1 不算异常回卷
        scenario=d1_health_scenario()
        health=scenario['d1_window']['health']
        health['queue']['cursor_trace']=[
            {'step':40000+i*100,'op':'enqueue','seq':seq,'physical':seq%8}
            for i,seq in enumerate((6,7,8,9))]
        result=_d15(scenario)
        self.assertEqual(result['status'],'pass')

    def test_h5_vacuous_empty_ready_set_passes(self):
        # 蓝图 §3 H5:R=∅ 时 vacuous PASS 并记录(不是 pending)
        scenario=d1_health_scenario()
        result=_d15(scenario)
        h5=[c for c in result['checks'] if c['name']=='h5_no_starvation']
        self.assertEqual(h5[0]['status'],'pass')
        self.assertIn('vacuous',h5[0]['detail'])

    def test_h5_non_assertion_face_starved_is_observation_not_fail(self):
        # H5 两级受检(任务书 §1.0b 第 7 条):固定优先级无公平性保证,非断言面
        # 就绪任务未调度=如实记录(观察级)不 FAIL;等待时长字段保留
        scenario=d1_health_scenario()
        scenario['d1_window']['health']['ready_tasks']=[
            {'tcb':'0x12a000','entry':'0x6035840','assertion_face':False,
             'schedulings':0,'ready_since_step':2000,'longest_wait':190000,
             'first_scheduled_step':None}]
        result=_d15(scenario)
        self.assertEqual(result['status'],'pass',
                         json.dumps(result['failures']))
        self.assertTrue(any('observation-level' in (c.get('detail') or '')
                            and 'longest_wait' in (c.get('detail') or '')
                            for c in result['checks']))

    def test_h4_nested_wakes_share_one_rewait(self):
        # 嵌套唤醒共享一次回等待:rewait<wake 且末态闭合=pass(任务书 §1.0b
        # 第 8 条嵌套层级——不误判为卡运行态)
        scenario=d1_health_scenario()
        core0=scenario['d1_window']['health']['per_core']['0']
        core0['wake_count']=5;core0['rewait_count']=4
        core0['wake_reasons']={'timer0':3,'core_tick':2}
        tail=core0['timeline'][-1]['model_step']
        core0['timeline'][-1:]=[
            {'model_step':tail+10,'kind':'woken','pc':'0x12c5ca','irq':3,'nesting':1},
            {'model_step':tail+20,'kind':'running','pc':'0x60661a4','irq':3,'nesting':1},
            {'model_step':tail+30,'kind':'entered_idle_wait','pc':'0x12c5ca','irq':None,
             'nesting':0}]
        result=_d15(scenario)
        self.assertEqual(result['status'],'pass',
                         json.dumps(result['failures']+result['missing']))


class D1_5FailTests(unittest.TestCase):
    """反例自证:故意构造 H 键违约输入 → 机检必须红(AZ 假阳性框架精神)。"""

    def test_h1_too_few_expiries_fails(self):
        scenario=d1_health_scenario()
        health=scenario['d1_window']['health']
        health['timer0_expiries']=2
        health['timer0_expiry_steps']=[36880,76880]
        scenario['d1_window']['timer0_cycles']=scenario['d1_window']['timer0_cycles'][:2]
        result=_d15(scenario)
        self.assertEqual(result['status'],'fail')
        self.assertTrue(_failed_checks(result,'h1_timer0_expiries'))

    def test_h1_gap_drift_fails(self):
        scenario=d1_health_scenario()
        scenario['d1_window']['health']['timer0_expiry_steps']=[36880,76880,117880]
        result=_d15(scenario)
        self.assertEqual(result['status'],'fail')
        self.assertTrue(_failed_checks(result,'h1_expiry_interval'))

    def test_h1_d1_2_cross_check_contradiction_fails(self):
        scenario=d1_health_scenario()
        scenario['d1_window']['timer0_cycles']=scenario['d1_window']['timer0_cycles'][:2]
        result=_d15(scenario)
        self.assertEqual(result['status'],'fail')
        self.assertTrue(_failed_checks(result,'h1_d1_2_cross'))

    def test_h2_timer0_storm_fails(self):
        scenario=d1_health_scenario()
        source=scenario['d1_window']['health']['per_source'][0]
        source['deliveries']=12;source['expirations']=12;source['completions']=12
        result=_d15(scenario)
        self.assertEqual(result['status'],'fail')
        self.assertTrue(_failed_checks(result,'h2_timer0_'))

    def test_h2_software_irq_delivery_fails(self):
        # 软件 IRQ 预期 0(D1 世界事件源封闭):出现交付=风暴形状
        scenario=d1_health_scenario()
        scenario['d1_window']['health']['per_source'][2]['deliveries']=1
        result=_d15(scenario)
        self.assertEqual(result['status'],'fail')
        self.assertTrue(_failed_checks(result,'h2_irq124'))

    def test_h3_unbounded_repost_fails(self):
        scenario=d1_health_scenario()
        health=scenario['d1_window']['health']
        entry=health['event_reposts'][0]
        entry['posts']=5;entry['consumed']=5
        health['queue_balance']={'enqueued':5,'dequeued':5}
        health['queue']['cursor_trace']=(
            [{'step':40000+i*100,'op':'enqueue','seq':i,'physical':i%8}
             for i in range(5)]
            +[{'step':46000+i*100,'op':'dequeue','seq':i,'physical':i%8}
              for i in range(5)])
        result=_d15(scenario)
        self.assertEqual(result['status'],'fail')
        self.assertTrue(_failed_checks(result,'h3_repost'))

    def test_h3_duplicate_consumption_fails(self):
        scenario=d1_health_scenario()
        health=scenario['d1_window']['health']
        health['event_reposts'][0]['consumed']=2
        health['queue_balance']={'enqueued':1,'dequeued':2}
        result=_d15(scenario)
        self.assertEqual(result['status'],'fail')
        self.assertTrue(_failed_checks(result,'h3_repost'))

    def test_h4_zero_wakes_fails(self):
        scenario=d1_health_scenario()
        core0=scenario['d1_window']['health']['per_core']['0']
        core0['wake_count']=0;core0['rewait_count']=0;core0['wake_reasons']={}
        core0['timeline']=[]
        result=_d15(scenario)
        self.assertEqual(result['status'],'fail')
        self.assertTrue(_failed_checks(result,'h4_core0'))

    def test_h4_stuck_running_fails_with_open_wake_evidence(self):
        # 窗口完成却末态 running=卡运行态(FAIL);未闭合唤醒区间证据含
        # 最后 PC/在途 IRQ/嵌套层级(任务书 §1.0b 第 8 条 H4 专条)
        scenario=d1_health_scenario()
        core0=scenario['d1_window']['health']['per_core']['0']
        core0['timeline']=core0['timeline'][:-1];core0['rewait_count']=3
        core0['last_open_wake']={'last_pc':'0x60661a4','in_flight_irq':3,
                                 'nesting_level':1}
        result=_d15(scenario)
        self.assertEqual(result['status'],'fail')
        failed=_failed_checks(result,'h4_core0')
        self.assertEqual(failed[0]['evidence']['open_wake']['in_flight_irq'],3)
        self.assertEqual(failed[0]['evidence']['open_wake']['nesting_level'],1)
        self.assertEqual(failed[0]['evidence']['first_violation']['pc'],'0x60661a4')

    def test_h5_assertion_face_task_starved_fails(self):
        # 两级受检的 FAIL 级:断言面任务(app_core 消费链)就绪却窗口内零调度
        scenario=d1_health_scenario()
        scenario['d1_window']['health']['ready_tasks']=[
            {'tcb':'0x127e10','entry':'0x6012028','assertion_face':True,
             'schedulings':0,'ready_since_step':1000,'longest_wait':198000,
             'first_scheduled_step':None}]
        result=_d15(scenario)
        self.assertEqual(result['status'],'fail')
        self.assertTrue(_failed_checks(result,'h5_'))

    def test_h5_app_core_entry_matched_without_flag_fails(self):
        # assertion_face 字段缺时按 entry==app_core 常量分级(0x6012028)
        scenario=d1_health_scenario()
        scenario['d1_window']['health']['ready_tasks']=[
            {'tcb':'0x127e10','entry':'0x6012028',
             'schedulings':0,'ready_since_step':1000,'longest_wait':198000}]
        result=_d15(scenario)
        self.assertEqual(result['status'],'fail')

    def test_h6_backlog_over_limit_fails(self):
        scenario=d1_health_scenario()
        scenario['d1_window']['health']['queue']['count_final']=99
        result=_d15(scenario)
        self.assertEqual(result['status'],'fail')
        self.assertTrue(_failed_checks(result,'h6_backlog'))

    def test_h6_seq_regression_fails(self):
        scenario=d1_health_scenario()
        scenario['d1_window']['health']['queue']['cursor_trace']=[
            {'step':50000,'op':'enqueue','seq':0,'physical':0},
            {'step':50100,'op':'enqueue','seq':2,'physical':2},
            {'step':50200,'op':'enqueue','seq':1,'physical':1},   # 逻辑序号回退
            {'step':50300,'op':'dequeue','seq':0,'physical':0}]
        result=_d15(scenario)
        self.assertEqual(result['status'],'fail')
        self.assertTrue(_failed_checks(result,'h6_cursor'))

    def test_h6_modulo_mismatch_fails(self):
        scenario=d1_health_scenario()
        scenario['d1_window']['health']['queue']['cursor_trace']=[
            {'step':50000,'op':'enqueue','seq':8,'physical':9},    # 8%8=0 != 9
            {'step':50100,'op':'dequeue','seq':8,'physical':0}]
        result=_d15(scenario)
        self.assertEqual(result['status'],'fail')
        self.assertTrue(_failed_checks(result,'h6_cursor'))

    def test_h7_step_regression_fails(self):
        scenario=d1_health_scenario()
        core0=scenario['d1_window']['health']['per_core']['0']
        core0['timeline'][1]['model_step']=core0['timeline'][0]['model_step']-1
        result=_d15(scenario)
        self.assertEqual(result['status'],'fail')
        self.assertTrue(_failed_checks(result,'h7_core0'))

    def test_h7_double_park_transition_fails(self):
        # 连续 entered_idle_wait=转换流断裂(重复 park 无唤醒)
        scenario=d1_health_scenario()
        core0=scenario['d1_window']['health']['per_core']['0']
        core0['timeline']=[{'model_step':100,'kind':'entered_idle_wait',
                            'pc':'0x12c5ca'},
                           {'model_step':200,'kind':'entered_idle_wait',
                            'pc':'0x12c5ca'}]
        result=_d15(scenario)
        self.assertEqual(result['status'],'fail')
        self.assertTrue(_failed_checks(result,'h7_core0'))

    def test_h7_empty_cpu0_timeline_with_wakes_fails(self):
        # wake_count>0 而时间线空=断链(计数说有唤醒,转换流没记录)
        scenario=d1_health_scenario()
        scenario['d1_window']['health']['per_core']['0']['timeline']=[]
        result=_d15(scenario)
        self.assertEqual(result['status'],'fail')
        self.assertTrue(_failed_checks(result,'h7_core0'))


class D1_5GateTests(unittest.TestCase):
    """否定式总闸(蓝图 §3/任务书 §1.0b 第 4 条):D1-1..4 全 PASS 而 H1-H7 任一
    FAIL → D1 仍 FAIL(fail 态不抵消);BI-A* 不进 gate。"""

    def test_negation_gate_h_fail_keeps_d1_failed(self):
        scenario=d1_health_scenario()
        health=scenario['d1_window']['health']
        health['timer0_expiries']=2
        health['timer0_expiry_steps']=[36880,76880]
        scenario['d1_window']['timer0_cycles']=scenario['d1_window']['timer0_cycles'][:2]
        result=run_all(scenario)
        by_id={a['id']:a for a in result['assertions']}
        for aid in ('D1-1','D1-2','D1-3','D1-4'):
            self.assertEqual(by_id[aid]['status'],'pass',
                             f'{aid}: {json.dumps(by_id[aid]["failures"])}')
        self.assertEqual(by_id['D1-5']['status'],'fail')
        self.assertEqual(result['d1_gate'],'fail')
        self.assertEqual(result['summary']['fail'],1)

    def test_healthy_world_d1_gate_pass(self):
        result=run_all(d1_health_scenario())
        by_id={a['id']:a for a in result['assertions']}
        for aid in ('D1-1','D1-2','D1-3','D1-4','D1-5'):
            msg=f'{aid}: {json.dumps(by_id[aid]["failures"]+by_id[aid]["missing"])}'
            self.assertEqual(by_id[aid]['status'],'pass',msg)
        self.assertEqual(result['d1_gate'],'pass')

    def test_pending_health_keeps_gate_pending(self):
        result=run_all(parked_scenario())
        self.assertEqual(result['d1_gate'],'pending')


class D1_5EvidenceTests(unittest.TestCase):
    """FAIL 证据契约(任务书 §1.0b 第 8 条):{rule,expected,observed,first_violation
    {模型步,core,PC},相关 source/task,违反前后片段,窗口末尾状态+证据完整性};
    H2 风暴证据含 per source+core 到期/交付/完成三分计数。"""

    def _evidences(self,mutation):
        scenario=d1_health_scenario()
        mutation(scenario)
        result=_d15(scenario)
        evidences=[c.get('evidence') for c in result['checks']
                   if c['status']=='fail' and c.get('evidence')]
        self.assertTrue(evidences)
        return evidences

    def test_contract_shape_on_h1(self):
        evidences=self._evidences(lambda s:s['d1_window']['health']
                                  .__setitem__('timer0_expiries',2))
        for evidence in evidences:
            self.assertEqual(evidence['rule'],'H1')
            self.assertIn('expected',evidence);self.assertIn('observed',evidence)
            self.assertTrue({'model_step','core','pc'} <= set(evidence['first_violation']))
            self.assertIn('source_or_task',evidence)
            self.assertIn('window_end',evidence)
            self.assertIn('evidence_complete',evidence['window_end'])

    def test_h2_storm_evidence_carries_three_way_counts(self):
        def mutate(scenario):
            scenario['d1_window']['health']['per_source'][0]['deliveries']=12
        evidence=self._evidences(mutate)[0]
        self.assertEqual(evidence['rule'],'H2')
        self.assertEqual(evidence['counts'],
                         {'expirations':3,'deliveries':12,'completions':3})
        self.assertEqual(evidence['derived_upper_bound'],
                         -(-200000//40000)+1)          # ⌈200000/40000⌉+1 = 6
        self.assertIn('first_deadline',evidence)
        self.assertIn('pending_at_window_start',evidence)
        self.assertEqual(evidence['source_or_task'],'timer0@core0')

    def test_h7_evidence_carries_around_snippet(self):
        def mutate(scenario):
            core0=scenario['d1_window']['health']['per_core']['0']
            core0['timeline'][2]['model_step']=core0['timeline'][1]['model_step']-1
        evidence=[e for e in self._evidences(mutate) if e['rule']=='H7'][0]
        # 违反前后状态转换片段(仅存首违,不存逐指令 timeline):前一条+违规条+后一条
        self.assertIn('around',evidence)
        self.assertEqual(len(evidence['around']),3)
        self.assertEqual(evidence['first_violation']['model_step'],
                         evidence['around'][1]['model_step'])


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
