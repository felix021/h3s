#!/usr/bin/env python3
"""Machine-readable D1 acceptance (D1-1..D1-5) + BI first-verification (A1-A5)
assertion skeleton (route CH / sim-d1assert; D1-5 health by route PR-1/sim-pr1;
H8 window-coverage self-check by route PR-2/sim-pr2).

每条断言忠实转录断言文档的自然语言语义,逐条注明 docs 出处行:

  D1-1..D1-4  docs/d1-acceptance.md §1(路 BR)+ docs/d1-s18-consumption.md §3(路 BZ 修正口径)
  D1-5        docs/d1-acceptance.md §1 D1-5 + docs/d1-scene-blueprint.md §3(H1-H7 表)
              + docs/s19-batch3-taskbook.md §1.0b(H5 两级受检/H6 逻辑序号勘误/
              FAIL 证据契约)+ docs/phase-review-d1-readiness.md §3.1 盲区#5(H8
              观察窗相容性自检,路 PR-2);数据面=d1_window.health.* 键形状(契约冻结于
              docs/d1-health-checks.md,生产者=PR-2 probe 侧,本文件只用合成样本自证)
  A1..A5      docs/d1-idle-event-clock.md §4(路 BP)

输入 = probe_irq_entry 场景报告 dict(scenario 行,含 'machine'/'peripherals' 键),
与 analysis/local-checks/irq-entry.json 的 scenarios[i] 同构。输出 = 结构化三态:

  pass    断言全部条款都有观测数据且全部满足
  pending 前沿/观测未到(park 窗口未开、或报告缺所需观测计数器)——pending 不是 pass
  fail    有观测数据但违反条款(金丝雀执行/间隔偏离/ACK 次数不符/jiffies 非 +1 等)

子项(checks)分两类:
  window='park-window'   只在两核 park 之后才有意义(D1 验收窗口语义)
  window='any-context'   机制形状子项,当前前沿的运行中交付即可核验
聚合:任一子项 fail → fail;无 fail 且有 pending/missing → pending;否则 pass。
这样骨架在当前前沿(未到 park)照常运行:窗口子项 pending、机制子项(交付锚/
计数一致性)在既有报告键上即可给出真实结论;前沿滑到 S19 park、计数器接入后
同一批函数直接给出 pass/fail,不需要改断言语义。

观测缺口:断言需要而当前报告没有的观测量,在结果的 missing 里逐项列出键名与
语义。这些 opt-in 计数器的实施(与 CG 的 br28_model/probe_irq_entry 场景推进
改动协调)记录在 HANDOFF 路 CH 条目;本文件只做「键在则核验、键缺则 pending」。

CLI:python3 tools/check_d1_acceptance.py --report analysis/local-checks/irq-entry.json
    [--scenario p11_heap_experimental_config] [--json]
无 --report 时打印断言清单(本工具是离线分析器,不跑任何 probe,不写任何报告)。
"""
import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0,str(Path(__file__).resolve().parent))
from br28_model import DIVISORS  # 与模型同源的 divisor 表(非等比),不自推导

# ── 文档锚点常量(docs 出处:docs/d1-acceptance.md §1、docs/d1-idle-event-clock.md
#    §1.1/§4、docs/d1-s18-consumption.md §1/§2、HANDOFF 路 BE/BF) ─────────────
PARK_IDLE_PC='0x12c5ca'            # 两核 idle 任务体 idle 指令(BP §1.1,trace 钉死)
K1_PC='0x601249c'                  # 金丝雀 K1(d1-acceptance §1 D1-1,tail-map §4)
K2_PC='0x60124ae'                  # 金丝雀 K2(同上)
S19_LOOP_HEAD='0x60123e6'          # S19 消息循环头(d1-acceptance §2.1)
GET_EVENT='0x6064c8c'              # get-event(buf,16),空队列 pend(同上)
POST_API='0x6000cba'               # S18 post API(BZ §1.1,BR §2.3 地址笔误已修正)
SPECIAL_PATH='0x60108fe'           # id==0x10000c 特路(d1-acceptance §2.1)
S18_HANDLER='0x6023962'            # (18,0) 认领 handler,表 1[39](BZ §1.5)
TIMER0_VECTOR='0x19fe10'           # 0x19fe00+4*4(路 BE)
TIMER0_HANDLER='0x12ece8'          # QCY Timer ISR 表 0x60a2508 槽 0(路 BE)
TIMER0_W1C_BIT=0x4000              # handler W1C bit14(路 BE)
CORE_TICK_HANDLER='0x60661a4'      # RTOS tick handler 帧(路 BF)
CORE_TICK_ACK_BIT=0x40             # |=0x40 ACK(路 BF)
JIFFIES_ADDR='0x125004'            # 软件 jiffies(D1-3,mod 2^32)
TIMESLICE_CALL='0x60653ae'         # 时间片调度 call(D1-3,路 BF 反汇编钉死)
TIMER0_TICK_PRD=119999             # tick_timer_init 的 10ms PRD(BZ §2.1)
# Timer0 闭式(BQ G1):STD12M 每模型步 3 计数(num=3*4,den=4),div=1、prd=119999
# 时周期=(120000)/3=40,000 模型步——40,000 是推导值不是魔数(D1-2/A2)。

# ── D1-5 H1-H8 常量(蓝图 §3 表+任务书 §1.0b 修正+REVIEW 盲区#5 裁决;
#    契约全文 docs/d1-health-checks.md)
APP_CORE_ENTRY='0x6012028'          # H5 断言面任务=app_core 消费链(路 BE RTI 钉死)
PERIODIC_SOURCES=('timer0','core_tick')  # H2 周期源(period 实读取上界);其余源=预期 0
HEALTH_POST_BOUND_NATURAL=2         # H3 上界:S18 静态每子码恰 post 一次,2 含一次容差
HEALTH_POST_BOUND_INJECTED=3        # H3 注入变体显式 +1(蓝图 §3 H3 行括号注)
HEALTH_TIMELINE_KINDS=('entered_idle_wait','woken','running')
# H7 转移合法性(状态转换流,非逐指令):park 后只能被唤醒;唤醒→处理或直接回等待;
# running→woken=嵌套 IRQ(任务书 §1.0b 第 8 条嵌套层级)。连续同 kind=转换流断裂。
HEALTH_TIMELINE_NEXT={'entered_idle_wait':('woken',),
                      'woken':('running','entered_idle_wait'),
                      'running':('entered_idle_wait','woken')}
MIN_HEALTH_CYCLES=3                 # H1/D1-2 的三周期样本下限;H8 覆盖折算同值引用
                                     # (REVIEW 盲区#5:窗口公式 160k 余量按 P=40,000
                                     #  形状值恰预留 3 周期,P_park 实读后须重验)

SOURCE_D1='docs/d1-acceptance.md'
SOURCE_BP='docs/d1-idle-event-clock.md'
SOURCE_BZ='docs/d1-s18-consumption.md'
SOURCE_BG='docs/d1-scene-blueprint.md'      # D1-5 规则表出处(路 BE 蓝图 §3)
SOURCE_TB='docs/s19-batch3-taskbook.md'     # D1-5 修正口径出处(批 3 任务书 §1.0b)
SOURCE_HL='docs/d1-health-checks.md'        # d1_window.health.* 冻结契约(本路 PR-1)
SOURCE_RV='docs/phase-review-d1-readiness.md'  # REVIEW 盲区#5=H8 裁决出处(路 PR-2)


def _hex(value):
    return hex(value) if isinstance(value,int) else value


def _machine(scenario):
    machine=scenario.get('machine') or {}
    return machine if isinstance(machine,dict) else {}


def _peripherals(scenario):
    peripherals=scenario.get('peripherals') or {}
    return peripherals if isinstance(peripherals,dict) else {}


def _events(scenario):
    events=_machine(scenario).get('events')
    return events if isinstance(events,list) else []


def _watch(scenario,pc):
    """watchpoint_hits 键(路 CH 观测需求):{pc_hex: 执行次数}。键缺返回 None。"""
    watch=_machine(scenario).get('watchpoint_hits')
    if not isinstance(watch,dict):return None
    return watch.get(pc)


def _timer0_state(scenario):
    state=_peripherals(scenario).get('timer0')
    return state if isinstance(state,dict) else {}


def _core_tick0(scenario):
    ticks=_peripherals(scenario).get('core_tick_configuration')
    if isinstance(ticks,list) and ticks and isinstance(ticks[0],dict):return ticks[0]
    return {}


def _deliveries(scenario,projection):
    value=_machine(scenario).get(projection)
    if isinstance(value,dict) and isinstance(value.get('deliveries'),list):
        return value['deliveries']
    return []


def _deferral(scenario):
    value=_machine(scenario).get('irq_delivery_deferral_projection')
    return value if isinstance(value,dict) else {}


def _park_log(scenario):
    """idle_wait_park_log(路 CH 观测需求):不截断的 park/唤醒序列。"""
    log=_machine(scenario).get('idle_wait_park_log')
    return log if isinstance(log,list) else None


def _engagements(scenario):
    """event_clock_engagements(路 CH 观测需求):每次 engage 时的计数器快照+段步数。"""
    value=_machine(scenario).get('event_clock_engagements')
    return value if isinstance(value,list) else None


def _message_chain(scenario):
    value=_machine(scenario).get('message_chain_observations')
    if isinstance(value,dict):return value
    return None


def park_window(scenario):
    """D1 验收窗口是否已开(两核 park)。

    判据(任一):machine.events 出现 event_clock_engaged;stop 是
    all_cores_idle_waiting 诚实停;events 出现两核各自的 core_idle_wait_entered。
    events 有截断(>32 条滑窗)时前两个判据仍然可靠(engaged/stop 不受截断影响)。
    """
    events=_events(scenario)
    if any(e.get('kind')=='event_clock_engaged' for e in events):
        return True,'event_clock_engaged present'
    stop=scenario.get('stop') or {}
    if stop.get('kind')=='all_cores_idle_waiting':
        return True,'honest stop all_cores_idle_waiting'
    parked={e.get('core') for e in events if e.get('kind')=='core_idle_wait_entered'}
    if 0 in parked and 1 in parked:
        return True,'both cores recorded core_idle_wait_entered'
    log=_park_log(scenario)
    if log is not None:
        cores={entry.get('core') for entry in log if entry.get('kind')=='core_idle_wait_entered'}
        if 0 in cores and 1 in cores:
            return True,'both cores parked per idle_wait_park_log'
    return False,('park window not reached: no event_clock_engaged, no '
                  'all_cores_idle_waiting stop and no both-core core_idle_wait_entered')


def _timer0_expected_period_steps(timer0):
    """从实际配置推导的 Timer0 到期间隔(模型步):STD12M 每步 3 计数(12/16×4 参考
    tick)、其余源每步 4 参考计数,divisor 查表分频。推导与 BQ 的闭式
    (steps_to_next_expiration)同源,不预锁常数(d1-idle-event-clock.md §4 A1
    「不预锁 341,097」同旨;40,000 = (119999+1)×1/3 的推导结果)。
    周期非整步数(park 后配置漂移)返回 None,断言降级为 missing 而非猜值。"""
    con,prd=(timer0.get('con'),timer0.get('prd'))
    if not isinstance(con,int) or not isinstance(prd,int) or not con&3:return None
    per_step=3 if (con>>10)&15==5 else 4      # STD12M 换算 vs RC16M 参考
    counts=DIVISORS[(con>>4)&15]*(prd+1)
    return counts//per_step if counts%per_step==0 else None


def _check(name,status='pass',window='park-window',detail='',missing=None,fail=None,
           evidence=None):
    """子项三态构造器:fail= 显式判 fail;missing 非空 → pending(观测未到≠通过);
    其余按显式 status(默认 pass)。evidence(可选)=FAIL 证据契约结构(D1-5,任务书
    §1.0b 第 8 条):{rule,expected,observed,first_violation,source_or_task,window_end}。"""
    if fail is not None:
        status='fail' if fail else 'pass'
    elif missing is not None:
        status='pending'
    return {'name':name,'status':status,'window':window,'detail':detail,
            **({'missing':missing} if missing else {}),
            **({'evidence':evidence} if evidence is not None else {})}


def _finalize(result,checks):
    result['checks']=checks
    result['failures']=[c for c in checks if c['status']=='fail']
    result['missing']=[c for c in checks if c['status']=='pending' and c.get('missing')]
    result['evidence']=[c['detail'] for c in checks if c['status']=='pass' and c['detail']]
    if result['failures']:result['status']='fail'
    elif any(c['status']=='pending' for c in checks):result['status']='pending'
    else:result['status']='pass'
    return result


def _base(assertion_id,title,source):
    return {'id':assertion_id,'title':title,'source':source,'status':'pending',
            'checks':[],'failures':[],'missing':[],'evidence':[],
            'required_observations':REQUIRED_OBSERVATIONS[assertion_id]}


# 每条断言需要的观测量清单(机器可读;「既有键」=当前报告已有,「路 CH 需求」=
# 尚缺的 opt-in 计数器,实施与 CG 的场景推进改动协调,见 HANDOFF 路 CH 条目)。
# 窗口未开时断言 early-return,观测缺口仍从这里可见,不必等窗口到达。
REQUIRED_OBSERVATIONS={
  'D1-1':['machine.events[core_idle_wait_entered/event_clock_engaged](既有键,32 条滑窗截断需配合)',
          'machine.idle_wait_park_log(路 CH 需求:不截断的完整 park/唤醒序列,条目含 model_step)',
          'machine.watchpoint_hits[0x60123e6/0x6064c8c](路 CH 需求:S19 循环头/get-event 调用计数)',
          'machine.watchpoint_hits[0x601249c/0x60124ae](路 CH 需求:K1/K2 金丝雀执行计数,>0 即 fail)',
          'machine.message_chain_observations.s18_preset_samples(路 CH 需求:S18 预置事件消费记录)',
          'machine.event_clock_projection(既有键,event_clock_projection 开启时)'],
  'D1-2':['machine.timer0_irq_entry_projection.deliveries(既有键:core/irq/vector/handler/'
          'delivery_clears_pending/pending_cleared_by_handler_ack)',
          'peripherals.timer0.expirations(既有键)+ machine.irq_delivery_deferral_projection(既有键)',
          'peripherals.timer0.expiration_model_steps(路 CH 需求:每次到期的 completed_model_steps,'
          '间隔恰推导周期步的前提观测)',
          'peripherals.timer0.w1c_acknowledgements(路 CH 需求:W1C bit14 计数,逐周期四查之 ACK)',
          'timer0_irq_entry_projection.deliveries[].model_step(路 CH 需求:交付时刻全局步号,'
          '窗口切分/首唤醒判定)',
          'machine.idle_wait_park_log(路 CH 需求:重新 core_idle_wait_entered 证据+首条 park 的 '
          'model_step 作窗口边界)'],
  'D1-3':['peripherals.core_tick_configuration[0].acknowledgements(既有键,ACK |=0x40 计数)',
          'machine.core_tick_entry_projection.deliveries(既有键)',
          'machine.event_clock_engagements[](model_step + core_ticks["0"] 快照)(路 CH 需求:park 时'
          ' core tick 快照+快照全局步号——相对闭式须加快照步号后与溢出时刻比对,不预锁常数)',
          'peripherals.core_tick_configuration[0].expiration_model_steps + core_tick deliveries[]'
          '.model_step(路 CH 需求:首次溢出的全局步号+交付步号——溢出须精确等于闭式,交付允许延迟)',
          'core_tick deliveries[].eligibility_note(路 CH 需求:结构化 {ipmask,priority,eligible:'
          'true},自由文本不算资格证明)',
          'machine.jiffies_observations(路 CH 需求:0x125004 写序 before/after,mod 2^32 恰 +1;'
          '多条时须带 delivery_index 绑定 handler 边界)',
          'machine.watchpoint_hits[0x60653ae](路 CH 需求:时间片调度 call 执行计数;返回由'
          '执行继续过该站点佐证)',
          'machine.task_switch_observations(路 CH 需求:观察项,实际恢复上下文记录)'],
  'D1-4':['machine.message_chain_observations(路 CH 需求:post→入队(0x10000C03)→get-event 13→'
          '[sp]=0x10000C→bit20→特路 0x60108fe→表命中(先表 2 后表 1)→handler 执行→返回→回 pend '
          '全环节,每环节三值 True/False/None=发生/观察到未发生/未观测;S18 (18,0) 单事件即满足'
          '断言面,BZ §3;≥1 自然样本全链即过,其余样本只记录)',
          'samples[].origin(路 CH 需求:firmware-natural 标注,外部注入样本不算)',
          'message_chain_observations.verdict(路 CH 需求:queue_empty_no_source/chain_broken '
          '区分「回调表空」与「链路不通」)'],
  'BI-A1':['machine.events[event_clock_engaged](既有键,events 截断时需 engagements 佐证)+ '
           'event_clock_projection(既有键)',
           'machine.event_clock_engagements[].timer0{con,cnt,prd,remainder,source_remainder}'
           '+first_segment_advances(路 CH 需求:首段 advances 与闭式 S=ceil 公式互证,余量必须'
           '显式不给默认值)',
           'timer0_irq_entry_projection.deliveries[].model_step + core_tick deliveries[].'
           'model_step(路 CH 需求:首个窗口内交付跨 IRQ 类比较——首个唤醒不得被更早的其它'
           'IRQ 抢先)',
           'machine.idle_wait_park_log + timer0_irq_rti 事件(首对首:首个 RTI 回首个交付保存的 '
           'RETI(idle 地址)+重新 park)'],
  'BI-A2':['peripherals.timer0.expiration_model_steps + idle_wait_park_log[].model_step'
           '(路 CH 需求:窗口内相邻到期恰推导周期步,park 边界切分)',
           'machine.event_clock_engagements[](model_step+first_segment_advances+'
           'inter_segment_execution)(路 CH 需求:段守恒 A_1=首差、A_(i+1)+X_i=到期差,'
           '锚定实际 horizon 不强制完整周期)'],
  'BI-A3':['stop.kind/note=all_cores_idle_waiting(既有键,反例世界)',
           'peripherals.timer0/core_tick_configuration con+pending(既有键,停表时全部不在计数'
           '且无已置位 pending——误停反证)',
           'machine.event_clock_projection.virtual_model_steps_advanced(既有键)+ 段级推进观测'
           '(路 CH 需求:停表前有过合法事件推进时累计非零,需段级区分)'],
  'BI-A4':['模型级形状已由 test_br28_machine EventClockTests(路 BQ)钉死;场景级样本需 '
           'event_clock_projection + Timer0-only 世界报告键(既有键判定适用性)'],
  'BI-A5':['复用 BI-A1/BI-A2 的观测;machine.task_switch_observations(路 CH 需求:观察项)'],
  'D1-5':['d1_window.health.timer0_expiries + timer0_expiry_steps(PR-2 需求:H1 到期次数'
          '与步号序列,间隔漂移判据)',
          'd1_window.health.per_source[]{source,core,period,expirations,deliveries,'
          'completions,first_deadline,pending_at_window_start}(PR-2 需求:H2 上界'
          '⌈N/P⌉+1 的实读 period 与到期/交付/完成三分计数;软件 IRQ 预期 0;'
          'period 同时是 H8 的实读 P_park 输入,复用无新增)',
          'd1_window.health.event_reposts[]{event_id,subcode,origin,posts,consumed,'
          'producer_pc,first_post_step,pending_at_window_end} + queue_balance{enqueued,'
          'dequeued}(PR-2 需求:H3 同事件重发与队列平衡,两键同窗口口径)',
          "d1_window.health.per_core['0'/'1'].{wake_count,rewait_count,wake_reasons,"
          'timeline[{model_step,kind,pc,irq,nesting}],last_open_wake{last_pc,'
          'in_flight_irq,nesting_level}}(PR-2 需求:H4 判据面=CPU0;H7 两核状态转换流)',
          'd1_window.health.ready_tasks[]{tcb,entry,assertion_face,schedulings,'
          'ready_since_step,longest_wait,first_scheduled_step}(PR-2 需求:H5 两级受检;'
          '[]=扫描过且空=vacuous pass;scheduled 布尔由 schedulings 推导不单独存)',
          'd1_window.health.queue.{count_final,qlimit,capacity,cursor_trace[{step,op,'
          'seq,physical}],cursor_monotonic}(PR-2 需求:H6;勘误口径=逻辑序号单调+'
          '物理指针=序号 mod 容量,物理回绕合法;cursor_monotonic 可选互证键)',
          'd1_window.health.window.{model_step_start,model_step_end,steps,complete}'
          '(PR-2 需求:窗口长度 N(上界公式)与右边界条款(末尾刚唤醒/ready 标'
          "「尚未到期」,不默认 PASS);steps 同时是 H8 覆盖折算的 N 输入,复用无新增)"],
}


# ════════════════════════════════════════════════════════════════════════════
# D1-1 真实进入消息循环并阻塞(docs/d1-acceptance.md §1 D1-1,L23-32)
# ════════════════════════════════════════════════════════════════════════════
def d1_1_park(scenario):
    result=_base('D1-1','真实进入消息循环并阻塞:两核 park+金丝雀零执行+S19 进入',
                 f'{SOURCE_D1} §1 D1-1(L23-32);{SOURCE_BP} §1.4/§1.1')
    opened,why=park_window(scenario)
    checks=[]

    # 金丝雀负例(any-context):K1/K2 执行计数>0 在任何前沿都是方向错误信号,
    # 窗口未开也照判 fail——不因 early-return 漏查(codex 复核修正)。
    for name,pc in (('canary_k1_zero_exec',K1_PC),('canary_k2_zero_exec',K2_PC)):
        hits=_watch(scenario,pc)
        if hits is None:
            checks.append(_check(name,window='any-context',
                missing=f"machine.watchpoint_hits['{pc}'](金丝雀执行计数)"))
        else:
            checks.append(_check(name,window='any-context',fail=hits>0,
                detail=f'{pc} executed {hits}x (must be 0; threshold/gate direction signal)'))

    if not opened:
        checks.append(_check('park_window',status='pending',detail=why))
        return _finalize(result,checks)
    checks.append(_check('park_window',detail=why))

    def window_check(name,missing=None,detail='',fail=False):
        if fail:return _check(name,status='fail',detail=detail)
        if missing:return _check(name,missing=missing)
        return _check(name,detail=detail)

    # S19 循环头真实执行+实际调用 0x6064c8c(sp,16)(d1-acceptance §1 D1-1/§2.1)
    head=_watch(scenario,S19_LOOP_HEAD);call=_watch(scenario,GET_EVENT)
    if head is None or call is None:
        checks.append(window_check('s19_loop_entered',
            missing=f"machine.watchpoint_hits['{S19_LOOP_HEAD}']/['{GET_EVENT}'] "
                    '(S19 循环头执行次数/get-event 实际调用计数)'))
    else:
        checks.append(window_check('s19_loop_entered',
            fail=(head<1 or call<1),
            detail=f'S19 head {S19_LOOP_HEAD} executed {head}x; get-event {GET_EVENT} called {call}x'
                   if head>=1 and call>=1 else
                   f'S19 head executed {head}x / get-event called {call}x (loop not really entered)'))

    # 循环首批对 S18 预置事件 ((18,0)/(20,2)) 的消费结果如实记录(允许先消费,记录非失败项;
    # 键缺=观测缺口 pending;窗口已覆盖却观测明确无样本=违反 S18 必 post 的静态结构→fail)
    chain=_message_chain(scenario)
    if chain is None:
        checks.append(window_check('s18_first_batch_recorded',
            missing='machine.message_chain_observations(S18 预置事件消费结果记录;D1-1 允许先消费,只要求如实记录)'))
    else:
        s18=chain.get('s18_preset_samples')
        if s18 is None:
            checks.append(window_check('s18_first_batch_recorded',
                missing='message_chain_observations.s18_preset_samples(S18 预置事件消费记录键缺)'))
        elif not s18 and chain.get('window_covered'):
            checks.append(window_check('s18_first_batch_recorded',fail=True,
                detail='window covered but zero S18 preset samples recorded: S18 posts both events '
                       'before the loop (static structure), an empty record contradicts it'))
        else:
            checks.append(window_check('s18_first_batch_recorded',
                detail=f'S18 preset consumption recorded: {json.dumps(s18)[:200]}'))

    # 空队列阻塞(pend 不返回)→ 调度器切 idle 任务 0x12c57e→0x12c5ca。
    # park pc 只核窗口边界的 park(有 model_step 时过滤);窗口开却零 park 记录=fail
    # (codex 复核修正:log 存在但空/无记录不得静默通过)。
    log=_park_log(scenario)
    park_boundary=None
    if log:
        for entry in log:
            if entry.get('kind')=='core_idle_wait_entered' and isinstance(entry.get('model_step'),int):
                park_boundary=entry['model_step'];break
    def window_parks(core):
        parks=[e for e in (log or []) if e.get('kind')=='core_idle_wait_entered' and e.get('core')==core]
        if park_boundary is not None:
            filtered=[e for e in parks if isinstance(e.get('model_step'),int) and e['model_step']>=park_boundary]
            # 边界过滤后一条不剩而原始记录里有步号→全部 park 都在边界前(矛盾);无步号记录按窗口内处理
            return filtered or [e for e in parks if not isinstance(e.get('model_step'),int)] or parks[-1:]
        return parks
    if log is None:
        checks.append(window_check('empty_queue_pend_blocks',
            missing='machine.idle_wait_park_log(完整 park/唤醒序列,events 滑窗截断不可替代)'))
    else:
        cpu0_parks=window_parks(0)
        bad=[e for e in cpu0_parks if e.get('pc')!=PARK_IDLE_PC]
        checks.append(window_check('empty_queue_pend_blocks',
            fail=(not cpu0_parks) or bool(bad),
            detail=(f'CPU0 parked at {PARK_IDLE_PC} (idle task body) {len(cpu0_parks)}x in-window via '
                    'scheduler switch; final pend did not return' if cpu0_parks and not bad
                    else (f'CPU0 park pcs: {[e.get("pc") for e in cpu0_parks]} (expected {PARK_IDLE_PC})'
                          if cpu0_parks else 'park window open but zero CPU0 park records in the log'))))

    # 两核 park:CPU1 已停泊 0x12c5ca;CPU0 经调度器切 idle 任务(同窗口过滤口径)
    if log is None:
        checks.append(window_check('both_cores_parked',
            missing='machine.idle_wait_park_log(两核 park 序列)'))
    else:
        for core in (0,1):
            parks=window_parks(core)
            bad=[e for e in parks if e.get('pc')!=PARK_IDLE_PC]
            checks.append(window_check(f'core{core}_parked_at_idle',
                fail=(not parks) or bool(bad),
                detail=(f'core{core} parked {len(parks)}x at {PARK_IDLE_PC} in-window' if parks and not bad
                        else (f'core{core} park pcs {[e.get("pc") for e in parks]} (expected {PARK_IDLE_PC})'
                              if parks else f'core{core} never parked in-window'))))

    # event_clock_engaged(D1-1 收口:全核 park 后事件时钟 engage)。
    # events 滑窗截断可能丢掉 engaged 事件——截断时降级 missing 而非 fail(codex 复核修正)。
    clock=_machine(scenario).get('event_clock_projection')
    engaged=any(e.get('kind')=='event_clock_engaged' for e in _events(scenario))
    truncated=bool(_machine(scenario).get('events_truncated'))
    if not isinstance(clock,dict) or not clock:
        checks.append(window_check('event_clock_engaged',
            missing='machine.event_clock_projection(事件时钟投影未启用或未接入该场景;BI/G3 场景接线项)'))
    elif not engaged and truncated:
        checks.append(window_check('event_clock_engaged',
            missing='event_clock_projection 开启但 events 截断,engaged 事件可能已被滑窗丢弃'
                    '(需 event_clock_engagements/idle_wait_park_log 佐证)'))
    else:
        checks.append(window_check('event_clock_engaged',fail=not engaged,
            detail='event_clock_engaged recorded' if engaged
                   else 'event clock projection enabled but never engaged (events not truncated)'))
    return _finalize(result,checks)


# ════════════════════════════════════════════════════════════════════════════
# D1-2 Timer0 连续运转与交错稳定(docs/d1-acceptance.md §1 D1-2,L33-43)
# ════════════════════════════════════════════════════════════════════════════
def d1_2_timer0_cycles(scenario):
    result=_base('D1-2','Timer0 连续运转:≥3 完整周期+间隔恰推导周期步+逐周期四查+无未知停点',
                 f'{SOURCE_D1} §1 D1-2(L33-43);{SOURCE_BP} §4 A2(周期推广)')
    timer0=_timer0_state(scenario)
    deliveries=_deliveries(scenario,'timer0_irq_entry_projection')
    checks=[]

    # 机制形状子项(any-context,当前前沿运行中交付即可核验;锚错即 fail 不论窗口)
    anchored=all(d.get('core')==0 and d.get('irq')==4 and d.get('vector_address')==TIMER0_VECTOR
                 and _hex(d.get('handler'))==TIMER0_HANDLER for d in deliveries)
    checks.append(_check('delivery_mechanism_shape',
        status=('pass' if anchored and deliveries else 'fail' if deliveries else 'pending'),
        window='any-context',
        detail=(f'{len(deliveries)} running deliveries all (core=0, irq=4, vector={TIMER0_VECTOR}, '
                f'handler={TIMER0_HANDLER}), real-W1C ack shape on every record'
                if anchored and deliveries else
                'delivery anchor mismatch (core/irq/vector/handler)' if deliveries else
                'no Timer0 delivery recorded yet')))
    no_delivery_clear=all(d.get('delivery_clears_pending') is False and
                          d.get('pending_cleared_by_handler_ack') is True for d in deliveries)
    if deliveries:
        checks.append(_check('delivery_does_not_clear_pending',status='pass' if no_delivery_clear else 'fail',
            window='any-context',
            detail='every delivery keeps pending; the real handler W1C (rmw |=0x4000) clears it'
                   if no_delivery_clear else 'some delivery record clears pending at entry (wrong shape)'))

    # 到期/交付/挂起计数一致性(any-context)
    expirations=timer0.get('expirations');deferral=_deferral(scenario)
    if isinstance(expirations,int) and deliveries:
        consistent=expirations>=len(deliveries)
        detail=(f'expirations {expirations} >= deliveries {len(deliveries)}'
                +(f'; {deferral.get("count")} deferred-in-flight holds re-requested, not dropped'
                  if isinstance(deferral.get('count'),int) else ''))
        checks.append(_check('expiry_delivery_consistency',status='pass' if consistent else 'fail',
            window='any-context',detail=detail if consistent else
            f'expirations {expirations} < deliveries {len(deliveries)}: deliveries exceed expirations'))

    opened,why=park_window(scenario)
    if not opened:
        checks.append(_check('park_window',status='pending',detail=
            why+'; window assertions (>=3 cycles, interval, four checks, re-park, '
            'post-dispatch cycle, no unknown stop) stay pending'))
        return _finalize(result,checks)
    checks.append(_check('park_window',detail=why))

    # ≥3 次连续完整周期 + RTI 回实际 RETI + 重新 park(需要交付步号/park 日志)。
    # 窗口切分:验收窗口=两核首次 park 之后——expiry/delivery 步号必须 > park 边界
    # (idle_wait_park_log 首条 park 的 model_step);无法确定边界时不猜,missing。
    expiry_steps=timer0.get('expiration_model_steps')
    log0=_park_log(scenario)
    park_boundary=None
    if log0:
        for entry in log0:
            if entry.get('kind')=='core_idle_wait_entered' and isinstance(entry.get('model_step'),int):
                park_boundary=entry['model_step'];break
    if all(isinstance(d.get('model_step'),int) for d in deliveries) and deliveries:
        window_deliveries=([d for d in deliveries if park_boundary is None or d['model_step']>park_boundary]
                           if park_boundary is not None else list(deliveries))
    else:
        window_deliveries=[]
    if isinstance(expiry_steps,list) and park_boundary is not None:
        window_expiries=[s for s in expiry_steps if isinstance(s,int) and s>park_boundary]
    else:
        window_expiries=[]
    steps=window_deliveries or window_expiries
    if (expiry_steps is None and not window_deliveries) or \
            (isinstance(expiry_steps,list) and park_boundary is None):
        checks.append(_check('three_complete_cycles',
            missing='peripherals.timer0.expiration_model_steps + deliveries[].model_step + '
                    'idle_wait_park_log[].model_step(每次到期/交付的全局模型步号与 park 边界;'
                    '间隔与窗口切分的前提观测,无法切分窗口时不猜)'))
    else:
        checks.append(_check('three_complete_cycles',fail=len(steps)<3,
            detail=f'{len(steps)} in-window Timer0 expiries/deliveries (>=3 required)'
                   if len(steps)>=3 else f'only {len(steps)} in-window expiries/deliveries'))

    # 相邻到期间隔恰推导周期步(配置不变;40,000 是 prd=119999/STD12M/div1 的推导值)。
    # 期望周期从 park 时刻快照推导(engage 时的 con/prd——窗口开头配置),终态只在
    # 无快照时使用并显式标注「假设窗口内配置不变」(codex 复核修正:不用窗口外的
    # 历史配置套窗口内间隔)。
    engagements=_engagements(scenario)
    expected=None;expected_src=None
    if engagements and isinstance(engagements[0].get('timer0'),dict):
        expected=_timer0_expected_period_steps(engagements[0]['timer0'])
        expected_src='park-time engage snapshot'
    if expected is None:
        expected=_timer0_expected_period_steps(timer0)
        expected_src=('final timer0 state (assumes the config is stable inside the window; '
                      'a mid-window CON/PRD rewrite invalidates this derivation)')
    if not window_expiries:
        checks.append(_check('expiry_interval_matches_config',
            missing='窗口内到期步号序列(expiration_model_steps + park 边界切分;≥2 个才可验间隔)'))
    elif expected is None:
        checks.append(_check('expiry_interval_matches_config',
            missing=f'cannot derive the expected period from timer0 state {json.dumps(timer0)} '
                    '(con/cnt/prd incomplete or non-integral period)'))
    else:
        gaps=[b-a for a,b in zip(window_expiries,window_expiries[1:])]
        bad=[g for g in gaps if g!=expected]
        checks.append(_check('expiry_interval_matches_config',fail=bool(bad),
            detail=f'adjacent in-window expiry gaps {gaps} all == derived period {expected} steps '
                   f'({expected_src})'
                   if not bad else f'expiry gaps {gaps} deviate from derived period {expected} '
                                   f'({expected_src})'))

    # 逐周期四查之 ACK:每交付恰一次 W1C(计数器需求)
    w1c=timer0.get('w1c_acknowledgements')
    if w1c is None:
        checks.append(_check('w1c_ack_per_delivery',
            missing='peripherals.timer0.w1c_acknowledgements(W1C bit14 清 pending 次数;'
                    '逐周期四查之 ACK 项)'))
    else:
        # 无交付步号观测时退化为全量对照(运行中世界的一致性检查,措辞如实)
        n=len(window_deliveries) if window_deliveries else len(deliveries)
        scope='in-window deliveries' if window_deliveries else 'all deliveries (no step numbers to split the window)'
        checks.append(_check('w1c_ack_per_delivery',fail=w1c!=n,
            detail=f'w1c acknowledgements {w1c} == {n} {scope}' if w1c==n
                   else f'w1c acknowledgements {w1c} != {n} {scope} (lost or duplicate ack)'))

    # 逐周期四查之 返回:RTI 回实际保存的 RETI + 重新 core_idle_wait_entered
    # (RTI 是周期的一环:证据缺(events 截断)=pending;未截断却零 RTI=周期不完整=fail;
    # RTI 数 >= 交付数 + 重新 park 数 >= 交付数——数量级配对,防某周期重复另一周期漏失)
    log=_park_log(scenario)
    truncated=bool(_machine(scenario).get('events_truncated'))
    rti_events=[e for e in _events(scenario) if e.get('kind')=='timer0_irq_rti']
    if truncated or not window_deliveries or log is None:
        checks.append(_check('rti_to_actual_reti_and_re_park',
            missing='machine.idle_wait_park_log + timer0_irq_rti 记录(RTI reti_from_frame 与交付 '
                    'reti_from 一致 + 重新 core_idle_wait_entered;events 滑窗截断时不可靠)'))
    elif not rti_events:
        checks.append(_check('rti_to_actual_reti_and_re_park',fail=True,
            detail=f'{len(window_deliveries)} in-window deliveries but zero timer0_irq_rti events '
                   '(events not truncated): the cycle never returned'))
    else:
        rti_ok=all(any(d.get('reti_from')==e.get('reti_from_frame') for d in window_deliveries)
                   for e in rti_events)
        repark_candidates=[e for e in log if e.get('kind')=='core_idle_wait_entered'
                           and e.get('core')==0]
        if park_boundary is not None:
            reparks=sum(1 for e in repark_candidates
                        if isinstance(e.get('model_step'),int) and e['model_step']>park_boundary)
        else:
            reparks=len(repark_candidates)
        ok=rti_ok and len(rti_events)>=len(window_deliveries) and reparks>=len(window_deliveries)
        checks.append(_check('rti_to_actual_reti_and_re_park',fail=not ok,
            detail=(f'{len(rti_events)} RTIs (>= {len(window_deliveries)} deliveries) returned to the '
                    f'actual saved RETI; {reparks} in-window re-parks' if ok else
                    'RTI/re-park mismatch (lost return, duplicate RTI or missing re-park)')))

    # 分发之后再覆盖一完整周期(若窗口内发生消息分发;完整周期=分发之后既有到期又有交付)
    chain=_message_chain(scenario)
    if chain is None:
        checks.append(_check('post_dispatch_full_cycle',
            missing='machine.message_chain_observations(判定窗口内是否发生消息分发)'))
    elif 'window_covered' not in chain:
        checks.append(_check('post_dispatch_full_cycle',
            missing='message_chain_observations.window_covered(窗口内是否发生分发的判定键;'
                    '键缺不得默认「无分发」)'))
    elif not chain['window_covered']:
        checks.append(_check('post_dispatch_full_cycle',
            detail='no message dispatch inside the window (clause not triggered)'))
    elif window_expiries and window_deliveries and isinstance(chain.get('last_dispatch_step'),int):
        last=chain['last_dispatch_step']
        covered=any(s>last for s in window_expiries) and \
                any(d['model_step']>last for d in window_deliveries)
        checks.append(_check('post_dispatch_full_cycle',fail=not covered,
            detail='a full cycle (expiry + delivery) follows the last in-window dispatch' if covered
                   else 'no full Timer0 cycle (expiry+delivery) after the in-window message dispatch'))
    else:
        checks.append(_check('post_dispatch_full_cycle',
            missing='message_chain_observations.last_dispatch_step + 到期/交付步号 '
                    '(分发后再覆盖一完整周期的步号对照)'))

    # 「无未知停点」:窗口内没有未处理的未知边界/跳过或补值(旧 kind 再次阻塞也不通过)。
    # probe 单次运行:终态 stop 即第一个未被处理的停——窗口开后任何未知停 kind 都算窗口内
    # 停;唯一豁免=全核停表(all_cores_idle_waiting)发生在 ≥3 周期完成之后(自然收尾,
    # codex 复核修正:预算停/窗口后停表不算未知停)。
    stop=scenario.get('stop') or {}
    kind=stop.get('kind')
    if kind in (None,'instruction_budget'):
        checks.append(_check('no_unknown_stops_in_window',
            detail='run ended at the instruction budget, no unknown stop inside the window'))
    elif kind=='all_cores_idle_waiting' and len(steps)>=3:
        checks.append(_check('no_unknown_stops_in_window',
            detail='honest all-cores stop after >=3 complete in-window cycles (natural end)'))
    else:
        checks.append(_check('no_unknown_stops_in_window',fail=True,
            detail=f'stop {kind} @ {scenario.get("stop_pc")} is an unhandled unknown boundary '
                   f'({len(steps)} in-window cycles; no unknown stops allowed inside the '
                   'acceptance window)'))
    return _finalize(result,checks)


# ════════════════════════════════════════════════════════════════════════════
# D1-3 core tick 的实际效果(docs/d1-acceptance.md §1 D1-3,L44-51)
# ════════════════════════════════════════════════════════════════════════════
def d1_3_core_tick(scenario):
    result=_base('D1-3','core tick 实际效果:闭式期限+资格交付+ACK 恰一次+jiffies+1+时间片调度',
                 f'{SOURCE_D1} §1 D1-3(L44-51);{SOURCE_BP} §4 A5(b)')
    tick=_core_tick0(scenario)
    deliveries=_deliveries(scenario,'core_tick_entry_projection')
    checks=[]

    # ACK |=0x40 恰一次/交付(any-context;acknowledgements 键已存在)
    ack=tick.get('acknowledgements')
    if isinstance(ack,int) and deliveries:
        checks.append(_check('ack_exactly_once_per_delivery',status='pass' if ack==len(deliveries) else 'fail',
            window='any-context',
            detail=f'core-tick acknowledgements {ack} == deliveries {len(deliveries)} (real |=0x40 once)'
                   if ack==len(deliveries) else
                   f'acknowledgements {ack} != deliveries {len(deliveries)}'))
    elif deliveries:
        checks.append(_check('ack_exactly_once_per_delivery',window='any-context',
            missing='peripherals.core_tick_configuration[0].acknowledgements'))

    opened,why=park_window(scenario)
    if not opened:
        checks.append(_check('park_window',status='pending',detail=
            why+'; window assertions (closed-form deadline, eligibility recheck, jiffies +1, '
            'timeslice call) stay pending'))
        return _finalize(result,checks)
    checks.append(_check('park_window',detail=why))

    # 首次溢出按从实际 park 状态推出的闭式期限到达(engage 快照互证,不预锁常数)。
    # 时间口径(codex 复核修正):ceil((prd+1-cnt)/progress) 是相对步数,必须加
    # engagement 的全局步号(model_step)后与**溢出时刻**步号
    # (core_tick_configuration[0].expiration_model_steps[0])比对;交付步号允许更晚
    # (交付延迟另记),溢出步号必须精确等于闭式。
    engagements=_engagements(scenario)
    expiry_steps0=_core_tick0(scenario).get('expiration_model_steps')
    first_delivery=deliveries[0] if deliveries and isinstance(deliveries[0].get('model_step'),int) else None
    snap_entry=None
    for entry in (engagements or []):
        core_snap=entry.get('core_ticks',{}) if isinstance(entry.get('core_ticks'),dict) else {}
        if isinstance(core_snap.get('0'),dict) and isinstance(entry.get('model_step'),int):
            snap_entry=entry;break
    if snap_entry is None or first_delivery is None:
        checks.append(_check('closed_form_deadline',
            missing='machine.event_clock_engagements[](model_step + core_ticks["0"] 快照'
                    '{con,cnt,prd,progress})+ core_tick deliveries[].model_step(交付步号)'
                    '+ core_tick_configuration[0].expiration_model_steps(溢出步号)'
                    '(相对闭式须加快照全局步号后与溢出时刻比对,交付允许延迟)'))
    elif not (isinstance(expiry_steps0,list) and expiry_steps0 and isinstance(expiry_steps0[0],int)):
        checks.append(_check('closed_form_deadline',
            missing='core_tick_configuration[0].expiration_model_steps(首次溢出的全局步号,'
                    '区分溢出时刻与允许延迟的交付时刻)'))
    else:
        snap=snap_entry['core_ticks']['0']
        cnt,prd,progress=(snap.get('cnt'),snap.get('prd'),snap.get('progress'))
        if not all(isinstance(v,int) for v in (cnt,prd,progress)) or not prd or progress<=0:
            checks.append(_check('closed_form_deadline',
                missing=f'engage snapshot incomplete: {json.dumps(snap)}'))
        else:
            expected=snap_entry['model_step']-(-((prd+1-cnt))//progress)  # 全局步号+相对闭式
            actual=expiry_steps0[0]
            checks.append(_check('closed_form_deadline',fail=actual!=expected,
                detail=(f'first core-tick expiry at model step {actual} == parked step '
                        f'{snap_entry["model_step"]} + closed form '
                        f'ceil(({prd}+1-{cnt})/{progress})={expected-snap_entry["model_step"]} '
                        f'(delivery at {first_delivery["model_step"]}, delay '
                        f'{first_delivery["model_step"]-actual} recorded separately)'
                        if actual==expected else
                        f'first expiry {actual} != closed-form {expected} from the parked snapshot')))

    # 交付符合当时的 IRQ 资格与优先级(pending 期间逐步重检;IPMASK 临界区延后属既有语义)。
    # 结构化契约:eligibility_note 为 dict 且 eligible==True 才算过(codex 复核修正:
    # 任意非空文本不算资格证明);字符串/缺键=观测格式不符→missing。
    eligibility=deliveries[0].get('eligibility_note') if deliveries else None
    if isinstance(eligibility,dict) and eligibility.get('eligible') is True:
        checks.append(_check('delivery_eligibility_and_recheck',detail=json.dumps(eligibility)[:200]))
    else:
        checks.append(_check('delivery_eligibility_and_recheck',
            missing='core_tick deliveries[].eligibility_note(结构化 {ipmask, priority, eligible:'
                    'true},交付时刻资格面;BF 的 pending 逐步重检语义佐证;自由文本不算证明)'))

    # jiffies [0x125004] mod 2^32 恰 +1(绑定首次 handler 入口/出口边界:
    # 单条观测=首 handler 专属;多条须带 delivery_index 绑定,否则无法排除同 handler 两次写)
    jiffies=_machine(scenario).get('jiffies_observations')
    if not isinstance(jiffies,list) or not jiffies:
        checks.append(_check('jiffies_plus_one',
            missing='machine.jiffies_observations(0x125004 写序观测:每次写前/写后值,'
                    'mod 2^32 恰 +1)'))
    elif len(jiffies)>1 and not all(isinstance(e.get('delivery_index'),int) for e in jiffies
                                    if isinstance(e,dict)):
        checks.append(_check('jiffies_plus_one',
            missing='jiffies_observations 多条时须带 delivery_index 绑定 handler 边界'
                    '(否则无法验证「该次 handler 入口/出口恰 +1」)'))
    else:
        deltas=[(entry.get('after',0)-entry.get('before',0))&0xffffffff
                for entry in jiffies if isinstance(entry,dict)]
        bad=[d for d in deltas if d!=1]
        checks.append(_check('jiffies_plus_one',fail=bool(bad),
            detail=f'jiffies {JIFFIES_ADDR} deltas {deltas} all == +1 (mod 2^32), bound to the '
                   'first handler entry/exit' if not bad
                   else f'jiffies deltas {deltas} violate exactly +1'))

    # 时间片调度 call 0x60653AE 真实执行并返回(返回的最小证据=probe 继续跑到别处:
    # 停在 call 站点=没返回)
    hits=_watch(scenario,TIMESLICE_CALL)
    if hits is None:
        checks.append(_check('timeslice_call_executed',
            missing=f"machine.watchpoint_hits['{TIMESLICE_CALL}'](时间片调度 call 执行计数)"))
    else:
        stopped_at_call=(scenario.get('stop_pc') or '').lower()==TIMESLICE_CALL
        checks.append(_check('timeslice_call_executed',fail=hits<1 or stopped_at_call,
            detail=f'timeslice scheduler call {TIMESLICE_CALL} executed {hits}x and execution '
                   'continued past it (returned)' if hits>=1 and not stopped_at_call
                   else f'timeslice call executed {hits}x, stop_pc={scenario.get("stop_pc")}'))

    # 若触发任务切换,按实际恢复上下文验收(观察项,不强求返回原任务;BP A5(b) 全证升级为观察)
    switches=_machine(scenario).get('task_switch_observations')
    if isinstance(switches,list):
        checks.append(_check('task_switch_accepted_as_is',detail=
            f'{len(switches)} task switch(es) observed; context restored as actually executed '
            '(no requirement to return to the original task)'))
    else:
        checks.append(_check('task_switch_accepted_as_is',
            missing='machine.task_switch_observations(观察项:实际恢复上下文记录)'))
    return _finalize(result,checks)


# ════════════════════════════════════════════════════════════════════════════
# D1-4 消息消费与分发闭环(docs/d1-acceptance.md §1 D1-4,L52-66;
#                          docs/d1-s18-consumption.md §1/§3 路BZ 修正口径)
# ════════════════════════════════════════════════════════════════════════════
CHAIN_STAGES=['posted','enqueued','get_event_returned_13','id_read','bit20_checked',
              'special_path','table_record_hit','handler_executed','handler_returned',
              'pend_again']
CHAIN_STAGE_SOURCE={
    'posted':f'post 实参({POST_API},S18 预置 (18,0)/(20,2))',
    'enqueued':'入队表示(0x606487c 环 slot 头 0x10000C03,BZ §1.1)',
    'get_event_returned_13':f'get-event {GET_EVENT} 返回 13(成功码)',
    'id_read':'[sp] 实际 id(=0x10000C,无 raw→0x1000xx 变换,BZ §1.2)',
    'bit20_checked':'bit20 检查(0x100000 位)',
    'special_path':f'id==0x10000c → {SPECIAL_PATH} 特路(按子码分派,BZ §1.3)',
    'table_record_hit':'12 字节分发表记录命中(先表 2 后表 1,BZ §1.4;(18,0) 命中表 1[39])',
    'handler_executed':f'handler 真实字节执行((18,0)→{S18_HANDLER},BZ §1.5)',
    'handler_returned':'handler 返回(handler PC 出现过不算通过,d1-acceptance §1 D1-4)',
    'pend_again':'回环再次 pend',
}


def d1_4_message_chain(scenario):
    result=_base('D1-4','消息消费与分发闭环:固件自然产生事件走完全链(不能只以 handler PC 出现过算通过)',
                 f'{SOURCE_D1} §1 D1-4(L52-66);{SOURCE_BZ} §1/§3')
    opened,why=park_window(scenario)
    checks=[_check('park_window',status='pass' if opened else 'pending',
                   detail=why if opened else
                   why+'; the S19 consumption window is behind the current frontier')]
    if not opened:return _finalize(result,checks)

    chain=_message_chain(scenario)
    if chain is None:
        checks.append(_check('sample_full_chain',
            missing='machine.message_chain_observations(post/入队/get-event/id/bit20/特路/表命中/'
                    'handler 执行/返回/回 pend 全环节观测;S18 (18,0) 单事件即满足断言面(BZ §3.1))'))
        return _finalize(result,checks)

    samples=chain.get('samples')
    if not isinstance(samples,list) or not samples:
        # 无样本:区分「回调表空」(链路通但无事件源→未覆盖,D1 未完成)与
        # 「链路不通」(post 成功却丢失→须定位,fail,不算无样本)——两者都不是 pass
        state=chain.get('verdict')
        if state=='chain_broken':
            checks.append(_check('sample_full_chain',fail=True,
                detail=f'broken chain observed: {json.dumps(chain.get("broken_evidence"))[:200]} '
                       '(post succeeded but the event was lost — locate it, this is not "no sample")'))
        elif state=='queue_empty_no_source':
            checks.append(_check('sample_full_chain',status='pending',detail=
                'no sample: queue empty with the callback table populated (link up, no firmware-'
                'natural event source in window) — D1-4 stays uncovered, D1 not complete'))
        else:
            checks.append(_check('sample_full_chain',missing=
                'no message sample recorded and no queue-empty/broken-chain verdict '
                '(cannot distinguish "empty table" from "broken chain"; d1-acceptance §1 D1-4)'))
        return _finalize(result,checks)

    # 样本来源约束:固件自然产生(d1-acceptance §1 D1-4「外部输入不在 D1 范围」)。
    # origin 缺=观测缺口;非 firmware-natural 的样本不计入完成样本且不算通过来源。
    natural=[s for s in samples if isinstance(s,dict) and s.get('origin')=='firmware-natural']
    no_origin=[s for s in samples if isinstance(s,dict) and 'origin' not in s]
    if no_origin:
        checks.append(_check('sample_full_chain',
            missing="samples[].origin(事件来源标注:'firmware-natural';外部注入样本不算 D1-4)"))
        return _finalize(result,checks)

    # 全链判定:≥1 个自然样本走完全链即满足断言面(BZ §3.1);其余样本只记录。
    # 环节三态(codex 复核修正):False(观测到没发生)=链路断→fail;
    # None/缺键(未观测)=观测缺口→pending,不算观察到失败。
    complete=[];broken=[];unobserved=[];details=[]
    for sample in natural:
        stages={k:sample.get(k) for k in CHAIN_STAGES}
        false_stages=[k for k,v in stages.items() if v is False]
        none_stages=[k for k,v in stages.items() if v is None]
        if false_stages:
            broken.append((sample.get('event','?'),false_stages))
            details.append(f"sample {sample.get('event','?')} BROKEN at {false_stages} "
                           '(observed not happening)')
        elif none_stages:
            unobserved.append((sample.get('event','?'),none_stages))
            details.append(f"sample {sample.get('event','?')} unobserved stages {none_stages}")
        else:
            complete.append(sample.get('event','?'))
            details.append(f"sample {sample.get('event','?')} completed all {len(CHAIN_STAGES)} stages")
    if broken:
        checks.append(_check('sample_full_chain',fail=True,detail='; '.join(details)))
    elif complete:
        checks.append(_check('sample_full_chain',
            detail=f'{len(complete)} firmware-natural sample(s) completed the full chain: '
                   f'{complete}; other samples recorded as-is ({len(natural)-len(complete)} total)'))
    elif unobserved:
        checks.append(_check('sample_full_chain',missing=
            f'sample stages unobserved (None), not proven broken: {details} '
            '(补齐环节观测后才能判 pass/fail)'))
    else:
        checks.append(_check('sample_full_chain',missing='no firmware-natural sample with observations'))

    # id 无变换的口径核验(BZ 闭合:post 硬编码 0x10000C,18/20=载荷子码)。
    # 只对走特路的样本强校验(特路进入条件就是 id==0x10000c);非特路样本走通用
    # 分发,id 空间开放(codex 复核修正:不把全部样本强制 0x10000c)。
    special_ids={s.get('id_read_value') for s in natural
                 if s.get('special_path') and s.get('id_read_value')}
    if special_ids:
        checks.append(_check('id_is_hardcoded_0x10000c',fail=bool(special_ids-{'0x10000c'}),
            detail=f"special-path sample ids {sorted(special_ids)} (the post API hardcodes 0x10000C; "
                   'raw 18/20 are payload subcodes; non-special samples use the open id space)'
                   if special_ids<= {'0x10000c'} else
                   f'special-path entered with ids {sorted(special_ids)} but the special path '
                   'requires id==0x10000c'))
    else:
        checks.append(_check('id_is_hardcoded_0x10000c',
            missing="samples[].id_read_value(特路样本的 [sp] 实际 id 观测)"))
    return _finalize(result,checks)


# ════════════════════════════════════════════════════════════════════════════
# BI A1 首个事件=按期限公式精确到期的 Timer0 IRQ4(docs/d1-idle-event-clock.md §4 A1)
# ════════════════════════════════════════════════════════════════════════════
def bi_a1_first_timer0_wake(scenario):
    result=_base('BI-A1','首个事件=按期限公式精确到期的 Timer0 IRQ4(advances 与闭式 S 互证)',
                 f'{SOURCE_BP} §4 A1(L153-157)')
    opened,why=park_window(scenario)
    checks=[_check('park_window',status='pass' if opened else 'pending',
                   detail=why if opened else why+'; first-wake assertions stay pending')]
    if not opened:return _finalize(result,checks)

    engaged=any(e.get('kind')=='event_clock_engaged' for e in _events(scenario))
    checks.append(_check('engaged_after_both_parked',fail=not engaged,
        detail='event_clock_engaged recorded after both cores parked' if engaged
               else 'no event_clock_engaged although the park window opened'))

    deliveries=_deliveries(scenario,'timer0_irq_entry_projection')
    # 与 d1_2 同口径:窗口内=步号>park 边界(idle_wait_park_log 首条 park 的 model_step)
    log=_park_log(scenario);park_boundary=None
    for entry in (log or []):
        if entry.get('kind')=='core_idle_wait_entered' and isinstance(entry.get('model_step'),int):
            park_boundary=entry['model_step'];break
    window_deliveries=([d for d in deliveries if isinstance(d.get('model_step'),int)
                        and (park_boundary is None or d['model_step']>park_boundary)]
                       if deliveries and all(isinstance(d.get('model_step'),int) for d in deliveries)
                       else [])
    if window_deliveries:
        first=window_deliveries[0]
        ok=first.get('core')==0 and first.get('irq')==4
        # 「首个唤醒」跨 IRQ 类核对(codex 复核修正):窗口内 core tick 交付若带步号,
        # 不得早于首个 Timer0 交付;软件 IRQ 交付无步号观测时如实标注。
        tick_deliveries=_deliveries(scenario,'core_tick_entry_projection')
        earlier_tick=[d for d in tick_deliveries if isinstance(d.get('model_step'),int)
                      and d['model_step']<first['model_step']]
        ok=ok and not earlier_tick
        checks.append(_check('first_wake_is_timer0_irq4',fail=not ok,
            detail=(f'first in-window wake = IrqDeliveryRequest(core=0, irq=4) at model step '
                    f'{first.get("model_step")}; no earlier in-window core-tick delivery'
                    if ok else
                    f'first in-window Timer0 wake at {first.get("model_step")} is preceded by '
                    f'{len(earlier_tick)} core-tick delivery(ies)' if ok is False and earlier_tick
                    else f'first in-window wake is {first.get("core")}/{first.get("irq")} (expected 0/4)')))
    else:
        checks.append(_check('first_wake_is_timer0_irq4',
            missing='timer0_irq_entry_projection.deliveries[].model_step(首个窗口内交付步号)'))

    engagements=_engagements(scenario)
    if engagements and isinstance(engagements[0].get('timer0'),dict) and \
            isinstance(engagements[0].get('first_segment_advances'),int):
        snap=engagements[0]['timer0'];advances=engagements[0]['first_segment_advances']
        con,prd,cnt=snap.get('con'),snap.get('prd'),snap.get('cnt')
        # codex 复核修正:余量不默认 0——快照缺 remainder/source_remainder 键=快照不完整,
        # pending 而非用补零猜值过互证。
        rem,srem=snap.get('remainder'),snap.get('source_remainder')
        if not all(isinstance(v,int) for v in (con,prd,cnt,rem,srem)):
            checks.append(_check('advances_match_closed_form',
                missing=f'engage timer0 snapshot incomplete (remainder/source_remainder must be '
                        f'explicit, not defaulted): {json.dumps(snap)[:160]}'))
        else:
            needed=DIVISORS[(con>>4)&15]*(prd+1-cnt)-rem
            num,den=(3*4,4) if (con>>10)&15==5 else (4,1)
            expected=-(-(den*needed-srem)//num)
            checks.append(_check('advances_match_closed_form',fail=advances!=expected,
                detail=f'first idle segment advanced {advances} == S=ceil((den*M-srem)/num)={expected} '
                       f'from the parked state (cnt={cnt}, prd={prd}, rem={rem}, srem={srem})'
                       if advances==expected
                       else f'first segment advances {advances} != closed form {expected}'))
    else:
        checks.append(_check('advances_match_closed_form',
            missing='machine.event_clock_engagements[](engage 时 timer0 {con,cnt,prd,remainder,'
                    'source_remainder} 快照 + first_segment_advances;A1 要求与实际 park 状态互证,'
                    '不预锁常数,余量必须显式)'))

    # W1C 清 pending 且 delivery_in_flight 复位(间接判据:后续到期仍能交付——后续交付
    # 只能发生在 W1C 清 pending+in_flight 复位之后,方向性证据)
    if window_deliveries and len(window_deliveries)>=2:
        checks.append(_check('w1c_and_in_flight_reset',
            detail='a second delivery followed: W1C cleared pending and the in-flight bookkeeping '
                   're-armed (indirect but directional evidence)'))
    elif window_deliveries:
        checks.append(_check('w1c_and_in_flight_reset',
            missing='第二周期交付(间接证明 delivery_in_flight 复位)或 in_flight 复位直接观测'))
    else:
        checks.append(_check('w1c_and_in_flight_reset',missing='窗口内交付序列'))

    # RTI 回实际保存的 RETI(=首个交付的 reti_from,idle 指令地址)并重新 park——
    # 首对首绑定(codex 复核修正:首个 RTI 必须对应首个交付,不拿任意一次 RTI 充数)
    log=_park_log(scenario)
    rti=[e for e in _events(scenario) if e.get('kind')=='timer0_irq_rti']
    if log is not None and rti and window_deliveries:
        first_rti=rti[0];first_delivery=window_deliveries[0]
        ok=(first_rti.get('reti_from_frame')==first_delivery.get('reti_from')
            ==PARK_IDLE_PC)
        repark=sum(1 for e in log if e.get('kind')=='core_idle_wait_entered'
                   and e.get('core')==0 and e is not (log and log[0]))
        ok=ok and repark>=1
        checks.append(_check('rti_to_idle_and_re_park',fail=not ok,
            detail=(f'first RTI returned to the saved RETI {PARK_IDLE_PC} (matches the first '
                    f'delivery); core0 re-parked afterwards' if ok else
                    f'first RTI reti_from_frame={first_rti.get("reti_from_frame")} vs first '
                    f'delivery reti_from={first_delivery.get("reti_from")} (expected {PARK_IDLE_PC})')))
    else:
        checks.append(_check('rti_to_idle_and_re_park',
            missing='machine.idle_wait_park_log + timer0_irq_rti 事件(首个 RTI 回首个交付保存的 '
                    'RETI(idle 地址)+重新 park)'))
    return _finalize(result,checks)


# ════════════════════════════════════════════════════════════════════════════
# BI A2 周期与推进量守恒(docs/d1-idle-event-clock.md §4 A2,L158-161)
# ════════════════════════════════════════════════════════════════════════════
def bi_a2_period_conservation(scenario):
    result=_base('BI-A2','周期与推进量守恒:相邻到期恰推导周期步;每段 advances=horizon−执行消耗',
                 f'{SOURCE_BP} §4 A2(L158-161)')
    opened,why=park_window(scenario)
    checks=[_check('park_window',status='pass' if opened else 'pending',
                   detail=why if opened else why+'; conservation stays pending')]
    if not opened:return _finalize(result,checks)

    timer0=_timer0_state(scenario);expiry_steps=timer0.get('expiration_model_steps')
    # 与 d1_2 同口径:窗口内到期(park 边界之后)+park 时刻快照推导周期
    engagements0=_engagements(scenario) or []
    log0=_park_log(scenario)
    park_boundary=None
    for entry in (log0 or []):
        if entry.get('kind')=='core_idle_wait_entered' and isinstance(entry.get('model_step'),int):
            park_boundary=entry['model_step'];break
    window_expiries=([s for s in expiry_steps if isinstance(s,int) and s>park_boundary]
                     if isinstance(expiry_steps,list) and park_boundary is not None else [])
    expected=None
    if engagements0 and isinstance(engagements0[0].get('timer0'),dict):
        expected=_timer0_expected_period_steps(engagements0[0]['timer0'])
    if expected is None:expected=_timer0_expected_period_steps(timer0)
    if len(window_expiries)<2:
        checks.append(_check('interval_exactly_period',
            missing='窗口内到期步号序列(expiration_model_steps + park 边界切分;≥2 个才可验间隔)'))
    elif expected is None:
        checks.append(_check('interval_exactly_period',
            missing=f'cannot derive the period from timer0 state {json.dumps(timer0)}'))
    else:
        gaps=[b-a for a,b in zip(window_expiries,window_expiries[1:])]
        bad=[g for g in gaps if g!=expected]
        checks.append(_check('interval_exactly_period',fail=bool(bad),
            detail=f'adjacent in-window expiry gaps {gaps} == derived period {expected} model steps '
                   '(global model-step axis, config assumed stable inside the window)'
                   if not bad else f'expiry gaps {gaps} != derived period {expected}'))

    engagements=_engagements(scenario)
    # 段守恒(codex 复核修正口径):每段 advances+段间执行 == 该段实际的 horizon——
    # 首段=首次到期−park 步号,后续段=相邻到期差,不强制等于完整周期
    # (首段可能是半周期,后续段也可能先撞上其它可交付事件)。
    if engagements and all(isinstance(e.get('first_segment_advances'),int) and
                           isinstance(e.get('inter_segment_execution'),int)
                           and isinstance(e.get('model_step'),int) for e in engagements):
        expiry=(timer0.get('expiration_model_steps')
                if isinstance(timer0.get('expiration_model_steps'),list) else [])
        # 时间线:park →A_1→ 到期1 →X_1→ park →A_2→ 到期2 →X_2→ ……
        # 守恒等式:A_1 == expiry[0]−park;A_{i+1}+X_i == expiry[i+1]−expiry[i]
        advances=[e['first_segment_advances'] for e in engagements]
        inters=[e['inter_segment_execution'] for e in engagements]
        n=len(engagements)
        if len(expiry)>=n and n>=1:
            park_step=engagements[0]['model_step']
            totals=[advances[0]]+[advances[i+1]+inters[i] for i in range(n-1)]
            anchors=[expiry[0]-park_step]+[b-a for a,b in zip(expiry,expiry[1:])][:n-1]
            pairs=list(zip(totals,anchors))
            bad=[(t,a) for t,a in pairs if t!=a]
            checks.append(_check('segment_advances_conservation',fail=bool(bad),
                detail=(f'segment advances/execution totals {totals} == actual per-segment horizons '
                        f'{anchors} (A_1 vs park→expiry[0]; A_(i+1)+X_i vs expiry gaps): no double '
                        'counting, advancement stops at the next deliverable event' if not bad
                        else f'segment totals {totals} != anchored horizons {anchors} '
                             '(double counting or overrun)')))
        else:
            checks.append(_check('segment_advances_conservation',
                missing='expiration_model_steps 与 engagements 段数不匹配(段 horizon 锚定需要 '
                        '每段对应的到期步号+engagement 的 model_step)'))
    else:
        checks.append(_check('segment_advances_conservation',
            missing='machine.event_clock_engagements[](每段 model_step + first_segment_advances + '
                    'inter_segment_execution;无重复计时/到可交付事件立即停推进的核验)'))
    return _finalize(result,checks)


# ════════════════════════════════════════════════════════════════════════════
# BI A3 无事件零推进诚实停(docs/d1-idle-event-clock.md §4 A3,L162-164)
# ════════════════════════════════════════════════════════════════════════════
def bi_a3_no_event_honest_stop(scenario):
    result=_base('BI-A3','无事件零推进诚实停:零推进+all_cores_idle_waiting;masked 原因不混同',
                 f'{SOURCE_BP} §4 A3(L162-164)')
    stop=scenario.get('stop') or {}
    checks=[]
    if stop.get('kind')!='all_cores_idle_waiting':
        checks.append(_check('no_event_world_applicability',status='pending',detail=
            f'scenario stop is {stop.get("kind")!r}, not the no-event honest stop; A3 only applies '
            'to a no-event counterfactual world (see probe_event_clock synthetic scenarios) — '
            'not applicable here is pending, not pass'))
        return _finalize(result,checks)
    note=stop.get('note','')
    checks.append(_check('zero_advance_honest_stop',
        status='pass' if 'no modeled event' in note else 'fail',
        detail=f'all_cores_idle_waiting with note {note!r}' if 'no modeled event' in note
               else f'note {note!r} does not state "no modeled event can fire"'))
    # 适用性反证(codex 复核修正):停表时若还有计数器在计数或 pending 已置位,这个停
    # 不是「无事件」而是误停——判 fail,不当 pass 收。
    timer0=_timer0_state(scenario)
    ticks=_peripherals(scenario).get('core_tick_configuration')
    counting=[]
    if isinstance(timer0.get('con'),int) and timer0['con']&3:counting.append('timer0')
    if isinstance(ticks,list):
        counting+= [f'core_tick[{i}]' for i,t in enumerate(ticks)
                    if isinstance(t,dict) and isinstance(t.get('con'),int) and t['con']&3]
    if timer0.get('pending'):counting.append('timer0.pending')
    checks.append(_check('no_event_world_counters_stopped',fail=bool(counting),
        detail='no counting counter and no set pending at the honest stop' if not counting
               else f'counters/pending still live at the stop: {counting} (this stop is premature, '
                    'not a no-event stop)'))
    # masked overflow unresolved 保留独立 Blocked 原因,不与停表混同;
    # 累计推进>0 时无法从累计值分离停表段(codex 复核修正:此前有合法事件推进的世界
    # 不要求累计为零,需段级观测区分)
    clock=_machine(scenario).get('event_clock_projection') or {}
    advances=clock.get('virtual_model_steps_advanced')
    if advances==0:
        checks.append(_check('zero_advances_in_no_event_world',
            detail='virtual_model_steps_advanced == 0 in the no-event world'))
    elif isinstance(advances,int):
        checks.append(_check('zero_advances_in_no_event_world',
            missing=f'累计 virtual_model_steps_advanced={advances}>0:需段级推进观测区分停表段'
                    '(此世界此前有过合法事件推进时累计非零是正常的,路 CH 段级计数需求)'))
    else:
        checks.append(_check('zero_advances_in_no_event_world',
            missing='machine.event_clock_projection.virtual_model_steps_advanced'))
    return _finalize(result,checks)


# ════════════════════════════════════════════════════════════════════════════
# BI A4 期限缺口反例(docs/d1-idle-event-clock.md §4 A4,L165-166;路 BQ 已实施)
# ════════════════════════════════════════════════════════════════════════════
def bi_a4_timer0_only_horizon(scenario):
    result=_base('BI-A4','期限缺口反例(G1 直接验收):Timer0-only 世界仍按闭式期限唤醒',
                 f'{SOURCE_BP} §4 A4(L165-166);G1 修法=HANDOFF 路 BQ')
    clock=_machine(scenario).get('event_clock_projection')
    ticks=_peripherals(scenario).get('core_tick_configuration')
    checks=[]
    timer0=_timer0_state(scenario)
    deliveries=_deliveries(scenario,'timer0_irq_entry_projection')
    ticks_counting=isinstance(ticks,list) and any(isinstance(t.get('con'),int) and t.get('con',0)&3
                                                  for t in ticks if isinstance(t,dict))
    if not isinstance(clock,dict):
        checks.append(_check('timer0_only_world_sample',
            missing='event_clock_projection 未启用:模型级形状已由 test_br28_machine '
                    'EventClockTests(路 BQ,4 例)钉死,场景级 Timer0-only 样本需 BI 场景接线'))
    elif ticks_counting:
        checks.append(_check('timer0_only_world_sample',status='pending',detail=
            'this scenario counts at least one core tick, so it is not a Timer0-only world; '
            'A4 stays a model-level (synthetic) assertion pinned by EventClockTests — '
            'not applicable here is pending, not pass'))
    elif deliveries:
        # Timer0-only 前提反证(codex 复核修正):Timer0 必须在计数,否则不是 A4 世界
        timer0_counting=isinstance(timer0.get('con'),int) and bool(timer0['con']&3)
        if not timer0_counting:
            checks.append(_check('timer0_only_world_sample',status='pending',detail=
                'event clock enabled, no core tick counting, but Timer0 is not counting either — '
                'not a Timer0-only wake world'))
            return _finalize(result,checks)
        engagements=_engagements(scenario)
        if engagements and isinstance(engagements[0].get('timer0'),dict) and \
                isinstance(engagements[0].get('first_segment_advances'),int):
            snap=engagements[0]['timer0'];advances=engagements[0]['first_segment_advances']
            con,prd,cnt=snap.get('con'),snap.get('prd'),snap.get('cnt')
            rem,srem=snap.get('remainder'),snap.get('source_remainder')
            if not all(isinstance(v,int) for v in (con,prd,cnt,rem,srem)):
                checks.append(_check('timer0_only_world_sample',
                    missing=f'engage 快照不完整(余量须显式): {json.dumps(snap)[:140]}'))
            else:
                needed=DIVISORS[(con>>4)&15]*(prd+1-cnt)-rem
                num,den=(3*4,4) if (con>>10)&15==5 else (4,1)
                expected=-(-(den*needed-srem)//num)
                checks.append(_check('timer0_only_world_sample',fail=advances!=expected,
                    detail=f'Timer0-only wake at {advances} == closed form {expected}' if advances==expected
                           else f'Timer0-only wake {advances} != closed form {expected}'))
        else:
            checks.append(_check('timer0_only_world_sample',
                missing='event_clock_engagements 快照(Timer0-only 唤醒与闭式互证)'))
    else:
        checks.append(_check('timer0_only_world_sample',status='pending',detail=
            'event clock enabled and no core tick counting, but no Timer0 delivery recorded: '
            'the closed-form wake has no scenario-level sample here (pending, not pass)'))
    return _finalize(result,checks)


# ════════════════════════════════════════════════════════════════════════════
# BI A5 C2 退出条件分两级(docs/d1-idle-event-clock.md §4 A5,L167-171)
# ════════════════════════════════════════════════════════════════════════════
def bi_a5_c2_exit_two_levels(scenario):
    result=_base('BI-A5','C2 退出条件分两级:(a) E1 往返即 BI 过;(b) 任务恢复全证=观察项',
                 f'{SOURCE_BP} §4 A5(L167-171)')
    checks=[]
    a1=bi_a1_first_timer0_wake(scenario);a2=bi_a2_period_conservation(scenario)
    # 文档口径:「A1+A2 通过即算 BI 过」——level(a) 是 A5 的判定面:A1/A2 fail 时
    # fail 传导(codex 复核修正),未达时 pending;level(b) 只是观察记录不 gate。
    if a1['status']=='pass' and a2['status']=='pass':
        checks.append(_check('level_a_e1_roundtrip',detail=
            'A1+A2 both pass: real tick wake → handler/ACK → restored context → back to IDLE '
            '(BI first verification satisfied; Timer0 round trip alone does not prove RTOS '
            'tick/scheduling)'))
    elif 'fail' in (a1['status'],a2['status']):
        checks.append(_check('level_a_e1_roundtrip',fail=True,detail=
            f'A1={a1["status"]}, A2={a2["status"]}: level (a) FAILED (failure propagates)'))
    else:
        checks.append(_check('level_a_e1_roundtrip',status='pending',detail=
            f'A1={a1["status"]}, A2={a2["status"]}: level (a) not yet satisfied — pending, not pass'))
    deliveries=_deliveries(scenario,'core_tick_entry_projection')
    if deliveries:
        ok=_hex(deliveries[0].get('handler'))==CORE_TICK_HANDLER
        switches=_machine(scenario).get('task_switch_observations')
        checks.append(_check('level_b_task_resume_full_proof',fail=not ok,
            detail=(f'core tick delivered to the real RTOS tick handler {CORE_TICK_HANDLER}'
                    +(f'; {len(switches)} task context restore(s) observed'
                      if isinstance(switches,list) else '; task context restore not yet observed '
                      '(observation item, not a hard assertion)')) if ok else
                    f'core tick handler {_hex(deliveries[0].get("handler"))} != {CORE_TICK_HANDLER}'))
    else:
        checks.append(_check('level_b_task_resume_full_proof',status='pending',detail=
            'no core tick delivery yet: level (b) full proof (E2-driven task resume) not reached'))
    return _finalize(result,checks)


# ════════════════════════════════════════════════════════════════════════════
# D1-5 观察窗口健康度 H1-H8(docs/d1-acceptance.md §1 D1-5;蓝图 §3 表;
# 任务书 §1.0b 修正:H5 固定优先级两级受检/H6 环形游标=逻辑序号单调/FAIL 证据契约;
# REVIEW 盲区#5 裁决:H8 观察窗相容性自检,路 PR-2)
# 数据面=d1_window.health.* 报告键形状(蓝图 §4.3;PR-1 冻结契约,
# docs/d1-health-checks.md;生产者=PR-2 probe 侧,键缺=pending 不猜)。
# ════════════════════════════════════════════════════════════════════════════
def _pc_eq(reported,anchor):
    """PC 等值比较(报告可能 '0x…' 字符串或 int;按 16 进制数值比较)。"""
    if isinstance(reported,int):return reported==int(anchor,16)
    if isinstance(reported,str):
        try:return int(reported,16)==int(anchor,16)
        except ValueError:return False
    return False


def _d1_window(scenario):
    window=scenario.get('d1_window')
    return window if isinstance(window,dict) else None


def _health_window(health):
    w=health.get('window')
    return w if isinstance(w,dict) else {}


def _health_window_steps(health):
    """窗口长度 N(上界公式 ⌈N/P⌉+1):优先 window.steps,缺则 end−start,都缺 None。"""
    w=_health_window(health)
    if isinstance(w.get('steps'),int):return w['steps']
    if isinstance(w.get('model_step_end'),int) and isinstance(w.get('model_step_start'),int):
        return w['model_step_end']-w['model_step_start']
    return None


def _window_end_evidence(health):
    """FAIL 证据契约的「窗口末尾状态+证据完整性」共用片段(任务书 §1.0b 第 8 条)。"""
    w=_health_window(health)
    return {'complete':w.get('complete'),
            'model_step_start':w.get('model_step_start'),
            'model_step_end':w.get('model_step_end'),
            'evidence_complete':isinstance(w.get('complete'),bool)}


def _window_cycle_coverage(health):
    """H8 折算:窗口 N 按实读 P_park 可覆盖的完整 Timer0 周期数(最坏相位口径)。

    P_park=per_source[timer0].period(park 态实读;BY 实测 delay 函数会按场景重编
    PRD 为 59999/47999/11999 三态,故不预锁 40,000 形状值兜底);N=health.window.steps
    (缺则 model_step_end−model_step_start,与 H2 上界公式同源)。折算=向下取整
    N//P_park:与窗口公式「160k 余量≥3×40,000」的预留口径一致,相位运气不折算在内。
    任一输入缺 → 覆盖 None(不猜;REVIEW 盲区#5:覆盖可证 <3 时正确动作=拒绝给结论
    而非误判)。返回 (coverage, n, period)。
    """
    n=_health_window_steps(health)
    period=None
    per_source=health.get('per_source')
    if isinstance(per_source,list):
        for entry in per_source:
            if isinstance(entry,dict) and entry.get('source')=='timer0':
                p=entry.get('period')
                if isinstance(p,int) and p>0:period=p
                break
    if period is None or not isinstance(n,int) or n<0:
        return None,n,period
    return n//period,n,period


def _violation(rule,expected,observed,first_violation=None,subject=None,
               around=None,worst=None,extra=None,window_end=None):
    """FAIL 证据契约七规则共用结构:{rule,expected,observed,first_violation{模型步,
    core,PC},相关 source/task,违反前后状态转换片段(around=首次违规,worst=最严重),
    窗口末尾状态+证据完整性}。计数/峰值在线累计在原始观测键里,此处只存片段。"""
    ev={'rule':rule,'expected':expected,'observed':observed,
        'first_violation':first_violation or
            {'model_step':None,'core':None,'pc':None,
             'note':'no step-tagged record available for the first violating entry'},
        'source_or_task':subject}
    if around is not None:ev['around']=around
    if worst is not None:ev['worst']=worst
    if extra:ev.update(extra)
    ev['window_end']=window_end if window_end is not None else {}
    return ev


def _timeline_violation(timeline,index):
    """timeline[index] 的 first_violation + 前后片段(仅存首违,任务书 §1.0b 第 9 条)。"""
    entry=timeline[index]
    first={'model_step':entry.get('model_step'),'core':entry.get('core'),
           'pc':entry.get('pc')}
    around=timeline[max(0,index-1):index+2]
    return first,around


# ── H1 定时事件确实发生(蓝图 §3 H1:窗口内 Timer0 到期 ≥3 次,与 D1-2 同数互证;
#     FAIL=到期 <3 或周期漂移) ────────────────────────────────────────────────
def _h1_timer0_events(health,window):
    name='h1_timer0_expiries_min3'
    exp=health.get('timer0_expiries')
    if not isinstance(exp,int):
        return [_check(name,missing='d1_window.health.timer0_expiries(窗口内 Timer0 '
                                '到期次数;H1 与 D1-2 同数互证)')]
    checks=[]
    if exp<3:
        # H8 相容性拦截(REVIEW 盲区#5 裁决):窗口按实读 P_park 折算覆盖可证 <3 时,
        # 「到期 <3」是窗口伪证而非定时器行为违约——拒绝给结论(pending)不误判 FAIL;
        # 覆盖足额(≥3)或无法折算(键缺不猜)时维持 PR1 冻结语义:观测缺口=真实缺口 FAIL。
        coverage,n,period=_window_cycle_coverage(health)
        if coverage is not None and coverage<MIN_HEALTH_CYCLES:
            checks.append(_check(name,
                missing=f'insufficient_window_coverage: {exp} expiries observed inside a '
                        f'window of N={n} steps that covers only {coverage} full cycles at '
                        f'the measured P_park={period} (<{MIN_HEALTH_CYCLES}) — the sample '
                        'floor is unreachable inside this window; refusing to judge the '
                        'cycle-count face (window config, not timer behavior; H8 face)'))
            return checks
        checks.append(_check(name,fail=True,
            detail=f'{exp} in-window Timer0 expiries (<3): the scheduled timer events '
                   'did not really happen',
            evidence=_violation('H1','>=3 in-window Timer0 expiries',
                f'{exp} expiries',subject='timer0@core0',
                window_end=_window_end_evidence(health))))
        return checks
    checks.append(_check(name,detail=f'{exp} in-window Timer0 expiries (>=3)'))
    # 周期漂移(间隔互不一致/倒流,或 !=per_source 实读 period;蓝图 §3 H1 FAIL 信号)
    period=None
    ps=health.get('per_source')
    if isinstance(ps,list):
        for entry in ps:
            if isinstance(entry,dict) and entry.get('source')=='timer0' \
                    and isinstance(entry.get('period'),int):
                period=entry['period'];break
    steps=health.get('timer0_expiry_steps')
    if not isinstance(steps,list) or len([s for s in steps if isinstance(s,int)])<2:
        checks.append(_check('h1_expiry_interval_stable',
            missing='d1_window.health.timer0_expiry_steps(≥2 个到期步号才可验间隔漂移;'
                    'D1-2 的 expiry_interval 子项以 peripherals.expiration_model_steps '
                    '为数据面,两处互补)'))
    else:
        intsteps=[s for s in steps if isinstance(s,int)]
        gaps=[b-a for a,b in zip(intsteps,intsteps[1:])]
        drift=[(i,g) for i,g in enumerate(gaps)
               if g<=0 or (period is not None and g!=period)]
        if drift:
            i,g=drift[0]
            checks.append(_check('h1_expiry_interval_stable',fail=True,
                detail=f'expiry gaps {gaps} drift (period={period} from per_source; '
                       f'first drift gap[{i}]={g})',
                evidence=_violation('H1',
                    'adjacent expiry gaps all equal (and == per_source timer0 period '
                    f'{period})' if period is not None
                    else 'adjacent expiry gaps all equal (no per_source period to anchor)',
                    f'gaps {gaps}',
                    first_violation={'model_step':intsteps[i+1],'core':0,'pc':None},
                    subject='timer0@core0',
                    window_end=_window_end_evidence(health))))
        else:
            checks.append(_check('h1_expiry_interval_stable',
                detail=f'adjacent expiry gaps {gaps} stable'
                       +(f' == per_source period {period}' if period is not None
                         else ' (no per_source period to anchor; stability only)')))
    # 与 D1-2 同数互证(timer0_cycles 是 D1-2 数据面;两者都在而不等=证据矛盾)
    cycles=window.get('timer0_cycles') if window else None
    if isinstance(cycles,list):
        if len(cycles)!=exp:
            checks.append(_check('h1_d1_2_cross_check',fail=True,
                detail=f'health.timer0_expiries={exp} != len(d1_window.timer0_cycles)='
                       f'{len(cycles)} (H1/D1-2 cross-check contradiction)',
                evidence=_violation('H1','health.timer0_expiries == len(timer0_cycles)',
                    f'{exp} vs {len(cycles)}',subject='timer0@core0',
                    window_end=_window_end_evidence(health))))
        else:
            checks.append(_check('h1_d1_2_cross_check',
                detail=f'health.timer0_expiries == len(timer0_cycles) == {exp} (H1/D1-2 '
                       'cross-check)'))
    return checks


# ── H2 同源重发有界(蓝图 §3 H2:每源交付 ≤⌈N/P_i⌉+1,P 从 per_source.period 实读,
#     不预锁 40,000/300,000;软件 IRQ124-127 预期 0;FAIL=超上界=风暴/无限重发) ─────
def _h2_source_bounds(health,window):
    per_source=health.get('per_source')
    if not isinstance(per_source,list) or not per_source:
        return [_check('h2_sources_recorded',
            missing='d1_window.health.per_source[](每源 {source,core,period,expirations,'
                    'deliveries,completions,first_deadline,pending_at_window_start};'
                    f'契约 {SOURCE_HL})')]
    n=_health_window_steps(health)
    window_end=_window_end_evidence(health)
    checks=[]
    for entry in per_source:
        if not isinstance(entry,dict) or not isinstance(entry.get('source'),str):
            checks.append(_check('h2_source_bounded',
                missing='per_source[].source(源名;条目形状不符)'));continue
        source=entry['source']
        name=f'h2_{source}_bounded'
        deliveries=entry.get('deliveries')
        if not isinstance(deliveries,int) or deliveries<0:
            checks.append(_check(name,missing=f'per_source[{source!r}].deliveries'
                    '(非负整数交付计数)'));continue
        period=entry.get('period')
        if source in PERIODIC_SOURCES:
            if not isinstance(period,int) or period<=0:
                checks.append(_check(name,missing=f'per_source[{source!r}].period'
                        '(park 态实读周期;蓝图 §3「不预锁 40,000/300,000」,缺=无法算上界)'))
                continue
            if not isinstance(n,int) or n<0:
                checks.append(_check(name,missing='health.window.steps(或 model_step_end−'
                        'model_step_start;上界公式 ⌈N/P⌉+1 的窗口长度)'));continue
            bound=-(-n//period)+1
            expected=f'deliveries <= ceil({n}/{period})+1 = {bound}'
        else:
            # 非周期源(软件 IRQ124-127 等):D1 世界事件源封闭(S18 预置+Timer0 回调+
            # 时间片唤醒,蓝图 §2.4),窗口内预期 0 交付(蓝图 §3 H2 原文)
            bound=0
            expected='deliveries == 0 (no periodic source in the closed D1 world)'
        # 到期/交付/完成三分计数一致性(任务书 §1.0b 第 8 条:风暴证据含三分计数;
        # 交付不得多于到期+窗口起点 pending 相位容差,完成不得多于交付)
        expirations=entry.get('expirations');completions=entry.get('completions')
        phase=1 if entry.get('pending_at_window_start') is True else 0
        inconsistent=((isinstance(expirations,int) and deliveries>expirations+phase)
                      or (isinstance(completions,int) and completions>deliveries))
        reported=entry.get('upper_bound')
        reported_bad=isinstance(reported,int) and reported!=bound
        if deliveries>bound or inconsistent or reported_bad:
            checks.append(_check(name,fail=True,
                detail=f'{source}@core{entry.get("core")}: expirations={expirations} '
                       f'deliveries={deliveries} completions={completions} '
                       +(f'exceeds bound {bound} (interrupt-storm shape)'
                         if deliveries>bound else '')
                       +(f'; counts inconsistent' if inconsistent else '')
                       +(f'; reported upper_bound={reported} != derived {bound}'
                         if reported_bad else ''),
                evidence=_violation('H2',expected,
                    f'expirations={expirations} deliveries={deliveries} '
                    f'completions={completions}',
                    subject=f'{source}@core{entry.get("core")}',
                    extra={'counts':{'expirations':expirations,'deliveries':deliveries,
                                     'completions':completions},
                           'derived_upper_bound':bound,
                           'first_deadline':entry.get('first_deadline'),
                           'pending_at_window_start':entry.get('pending_at_window_start')},
                    window_end=window_end)))
            continue
        # core tick 周期上界承载 CNT 冻结假设(§1.4 第 1 条末项/第 4 条:PRD 读回=
        # 恒等证据链不承载,快照承载;assumption 缺=断言形状不完整 → pending 非 pass)。
        # 键位:任务书 §1.4 写 d1_window.core_tick_state.assumption(顶层),蓝图 §4.3
        # 示例树放 park.core_tick_state 下——PR-2 落地前两处兼容(顶层优先)。
        if source=='core_tick':
            state=window.get('core_tick_state') if window else None
            if not isinstance(state,dict):
                park=window.get('park') if window else None
                state=park.get('core_tick_state') if isinstance(park,dict) else None
            assumption=state.get('assumption') if isinstance(state,dict) else None
            if not isinstance(assumption,str) or 'assumed-not-measured' not in assumption:
                checks.append(_check(name,missing='d1_window.core_tick_state.assumption'
                        '(core tick 周期上界的 CNT 冻结假设标注;§1.4:缺失=断言形状'
                        '不完整,pending 非 pass)'))
                continue
            checks.append(_check(name,
                detail=f'{source}@core{entry.get("core")}: {deliveries} deliveries <= '
                       f'bound {bound} (period {period} read back; assumption: {assumption})'))
        else:
            checks.append(_check(name,
                detail=f'{source}@core{entry.get("core")}: {deliveries} deliveries '
                       +(f'<= bound {bound} (period {period} from per_source)'
                         if source in PERIODIC_SOURCES
                         else '== 0 expected in the closed D1 world')))
    return checks


# ── H3 无同事件无限重发(蓝图 §3 H3:同 (id,子码) 入队 ≤上界(S18 预置=2,注入=+1);
#     消费=入队(无重复消费);FAIL=消费>入队(重复分发)或入队无界增长) ─────────────
def _h3_reposts(health):
    reposts=health.get('event_reposts')
    if not isinstance(reposts,list):
        return [_check('h3_repost_bounded',
            missing='d1_window.health.event_reposts[](每 (event_id,subcode) 一条;'
                    f'契约 {SOURCE_HL})')]
    window_end=_window_end_evidence(health)
    checks=[]
    if not reposts:
        checks.append(_check('h3_repost_bounded',
            detail='no (event_id,subcode) repost recorded in-window (vacuous pass)'))
    for entry in reposts:
        if not isinstance(entry,dict):
            checks.append(_check('h3_repost_bounded',
                missing='event_reposts[] 条目形状(需要 event_id/subcode/posts/consumed)'))
            continue
        key=f"({entry.get('event_id')},{entry.get('subcode')})"
        name=f"h3_repost_{entry.get('event_id')}_{entry.get('subcode')}"
        posts,consumed=entry.get('posts'),entry.get('consumed')
        if not isinstance(posts,int) or not isinstance(consumed,int):
            checks.append(_check(name,missing=f'event_reposts[{key}].posts/consumed'
                    '(入队/消费计数)'));continue
        bound=(HEALTH_POST_BOUND_INJECTED if entry.get('origin')=='injected'
               else HEALTH_POST_BOUND_NATURAL)
        first={'model_step':entry.get('first_post_step'),'core':None,
               'pc':entry.get('producer_pc')}
        if posts>bound:
            checks.append(_check(name,fail=True,
                detail=f'{key}: posts={posts} > bound {bound} (unbounded repost shape; '
                       f'origin={entry.get("origin")})',
                evidence=_violation('H3',f'posts <= {bound} (origin {entry.get("origin")})',
                    f'posts={posts} consumed={consumed}',first_violation=first,
                    subject=key,extra={'origin':entry.get('origin'),'posts':posts,
                                       'consumed':consumed},
                    window_end=window_end)))
        elif consumed>posts:
            checks.append(_check(name,fail=True,
                detail=f'{key}: consumed={consumed} > posts={posts} (duplicate dispatch)',
                evidence=_violation('H3','consumed == posts (no duplicate consumption)',
                    f'posts={posts} consumed={consumed}',first_violation=first,
                    subject=key,extra={'posts':posts,'consumed':consumed},
                    window_end=window_end)))
        elif consumed<posts:
            # 右边界条款(任务书 §1.0b 第 8 条):窗口末刚 post 未消费标「尚未到期」,
            # 未闭合样本 → pending(需要完整样本的验收项不默认 PASS)
            note=('post not yet consumed at the window edge (pending_at_window_end set)'
                  if entry.get('pending_at_window_end') is True
                  else 'post not consumed and not marked pending_at_window_end')
            checks.append(_check(name,missing=f'{key}: consumed={consumed} < posts={posts} '
                    f'({note}; H3 needs complete samples)'))
        else:
            checks.append(_check(name,
                detail=f'{key}: posts={posts} consumed={consumed} <= bound {bound} '
                       f'(origin={entry.get("origin")}, producer {entry.get("producer_pc")})'))
    # queue_balance(同窗口口径):总入队/出队与 reposts 汇总互证;出队不得超入队
    qb=health.get('queue_balance')
    if isinstance(qb,dict) and isinstance(qb.get('enqueued'),int) \
            and isinstance(qb.get('dequeued'),int):
        totals=[e for e in reposts if isinstance(e,dict)]
        posts_sum=sum(e['posts'] for e in totals if isinstance(e.get('posts'),int))
        consumed_sum=sum(e['consumed'] for e in totals
                         if isinstance(e.get('consumed'),int))
        if qb['dequeued']>qb['enqueued'] or qb['enqueued']!=posts_sum \
                or qb['dequeued']!=consumed_sum:
            checks.append(_check('h3_queue_balance_consistent',fail=True,
                detail=f'queue_balance enqueued={qb["enqueued"]} dequeued={qb["dequeued"]} '
                       f'vs repost sums posts={posts_sum} consumed={consumed_sum}',
                evidence=_violation('H3',
                    'dequeued <= enqueued and both == the repost sums (same window scope)',
                    f'enqueued={qb["enqueued"]} dequeued={qb["dequeued"]} '
                    f'posts_sum={posts_sum} consumed_sum={consumed_sum}',
                    subject='queue_balance',window_end=window_end)))
        else:
            checks.append(_check('h3_queue_balance_consistent',
                detail=f'queue_balance enqueued={qb["enqueued"]} dequeued={qb["dequeued"]} '
                       'match the repost sums'))
    elif qb is not None:
        checks.append(_check('h3_queue_balance_consistent',
            missing='queue_balance.{enqueued,dequeued}(整数总入队/出队计数)'))
    return checks


# ── H4 必要任务可唤醒(蓝图 §3 H4:窗口内 CPU0 被 Timer0/core tick 唤醒 ≥1 次且
#     每次唤醒处理后重新 core_idle_wait_entered;FAIL=唤醒后不回等待/零唤醒) ──────
def _h4_wake_rewait(health):
    per_core=health.get('per_core')
    if not isinstance(per_core,dict):
        return [_check('h4_core0_wake_and_rewait',
            missing="d1_window.health.per_core{}(每核 wake/rewait/wake_reasons/timeline;"
                    f"判据面=CPU0;契约 {SOURCE_HL})")]
    core0=per_core.get('0')
    if not isinstance(core0,dict):
        return [_check('h4_core0_wake_and_rewait',
            missing="per_core['0'](CPU0 判据面:蓝图 §3 H4 只对 CPU0 判定,CPU1 为记录面)")]
    name='h4_core0_wake_and_rewait'
    wake,rewait=core0.get('wake_count'),core0.get('rewait_count')
    if not isinstance(wake,int) or not isinstance(rewait,int):
        return [_check(name,missing='per_core[\'0\'].{wake_count,rewait_count}'
                '(唤醒/回等待计数)')]
    window_end=_window_end_evidence(health)
    reasons=core0.get('wake_reasons')
    if wake==0:
        return [_check(name,fail=True,
            detail='zero in-window wakes of CPU0: the necessary task was never wakeable',
            evidence=_violation('H4','wake_count >= 1 (Timer0/core tick must wake CPU0)',
                'wake_count=0',subject='core0',
                extra={'rewait_count':rewait,'wake_reasons':reasons},
                window_end=window_end))]
    if isinstance(reasons,dict):
        reason_sum=sum(v for v in reasons.values() if isinstance(v,int))
        if reason_sum!=wake:
            return [_check(name,fail=True,
                detail=f'wake_reasons sum {reason_sum} != wake_count {wake} '
                       '(counting contradiction)',
                evidence=_violation('H4','sum(wake_reasons) == wake_count',
                    f'{reason_sum} vs {wake}',subject='core0',extra={'wake_reasons':reasons},
                    window_end=window_end))]
    if rewait>wake:
        return [_check(name,fail=True,
            detail=f'rewait_count {rewait} > wake_count {wake} (counting contradiction)',
            evidence=_violation('H4','rewait_count <= wake_count',
                f'{rewait} vs {wake}',subject='core0',window_end=window_end))]
    timeline=core0.get('timeline')
    last=(timeline[-1].get('kind') if isinstance(timeline,list) and timeline
          and isinstance(timeline[-1],dict) else None)
    complete=_health_window(health).get('complete')
    if last=='entered_idle_wait':
        # 末次唤醒已闭合(rewait<wake 合法:嵌套唤醒共享一次回等待,任务书 §1.0b
        # 第 8 条嵌套层级;计数如实记录)
        return [_check(name,
            detail=f'CPU0 woken {wake}x, re-waited {rewait}x, final state re-parked '
                   f'(nested wakes may share one re-wait); reasons {reasons}')]
    if last in ('woken','running'):
        open_wake=core0.get('last_open_wake')
        # 未闭合唤醒区间证据:最后 PC/在途 IRQ/嵌套层级(任务书 §1.0b 第 8 条 H4 专条)
        if complete is True:
            idx=len(timeline)-1
            first,around=_timeline_violation(timeline,idx)
            return [_check(name,fail=True,
                detail=f'CPU0 woken {wake}x but only {rewait} re-waits with the window '
                       f'complete and final state {last!r}: stuck running, not re-parked',
                evidence=_violation('H4',
                    'every wake is followed by core_idle_wait_entered before the window ends',
                    f'wake_count={wake} rewait_count={rewait} final={last!r}',
                    first_violation=first,subject='core0',around=around,
                    extra={'open_wake':open_wake},window_end=window_end))]
        return [_check(name,
            missing=f'CPU0 woken {wake}x, re-waited {rewait}x, final state {last!r} with '
                    'the window truncated: the open wake interval is not closed yet '
                    '(right-edge clause: not yet due, not a pass); open wake '
                    f'{json.dumps(open_wake) if isinstance(open_wake,dict) else open_wake}')]
    # timeline 缺/空:计数面兜底(rewait==wake 互证;rewait<wake 无时间线分不清嵌套与未闭合)
    if rewait==wake:
        return [_check(name,
            detail=f'CPU0 woken {wake}x and re-waited {rewait}x (counts agree; no '
                   'timeline to anchor the final state)')]
    return [_check(name,
        missing=f'CPU0 woken {wake}x but re-waited {rewait}x with no timeline: cannot '
                'distinguish nested wakes sharing one re-wait from an unclosed interval')]


# ── H5 无任务饿死(蓝图 §3 H5+任务书 §1.0b 第 7 条两级受检:固定优先级调度器无
#     公平性保证,「所有 ready 必获调度」过强;断言面任务必须调度=FAIL 级,其余
#     未调度=如实记录=观察级;等待时长字段保留) ──────────────────────────────────
def _h5_ready_tasks(health):
    name='h5_no_starvation'
    if not isinstance(health.get('ready_tasks'),list):
        return [_check(name,
            missing='d1_window.health.ready_tasks[](停点 TCB 扫描 state==ready 的任务集;'
                    '[]=扫描过且空=vacuous pass;键缺=未扫描)')]
    tasks=health['ready_tasks']
    window_end=_window_end_evidence(health)
    if not tasks:
        return [_check(name,
            detail='ready set empty at the stop scan (vacuous pass, recorded; '
                   'blueprint §3 H5)')]
    checks=[]
    for task in tasks:
        if not isinstance(task,dict):
            checks.append(_check(name,missing='ready_tasks[] 条目形状'
                    '({tcb,entry,assertion_face,schedulings,…})'));continue
        tcb=task.get('tcb');entry=task.get('entry')
        face=task.get('assertion_face')
        if face is None and entry is not None:
            face=_pc_eq(entry,APP_CORE_ENTRY)
        sched=task.get('schedulings')
        wait=(f"ready_since={task.get('ready_since_step')} "
              f"longest_wait={task.get('longest_wait')} "
              f"first_scheduled={task.get('first_scheduled_step')}")
        if face is None:
            checks.append(_check(name,missing=f'ready_tasks[tcb={tcb}].assertion_face/'
                    f'entry(两级受检分级依据:断言面=app_core 消费链(常量 '
                    f'{APP_CORE_ENTRY}),缺=无法分级)'));continue
        if not isinstance(sched,int):
            checks.append(_check(name,missing=f'ready_tasks[tcb={tcb}].schedulings'
                    '(调度次数;scheduled 布尔由此推导,不单独存——任务书 §1.0b 第 9 条)'))
            continue
        if face and sched==0:
            checks.append(_check(name,fail=True,
                detail=f'assertion-face task tcb={tcb} entry={entry} ready since '
                       f'{task.get("ready_since_step")} but never scheduled in-window '
                       f'(starved; {wait})',
                evidence=_violation('H5',
                    'assertion-face (app_core consumption-chain) task scheduled >=1 '
                    'in-window',
                    f'tcb={tcb} entry={entry} schedulings=0',
                    first_violation={'model_step':task.get('ready_since_step'),
                                     'core':None,'pc':entry},
                    subject=f'tcb {tcb} entry {entry}',extra={'wait_fields':wait},
                    window_end=window_end)))
        elif face:
            checks.append(_check(name,
                detail=f'assertion-face task tcb={tcb} scheduled {sched}x in-window'))
        elif sched==0:
            checks.append(_check(name,
                detail=f'non-assertion-face task tcb={tcb} entry={entry} unscheduled '
                       '(observation-level, recorded not failed: fixed-priority scheduler '
                       f'carries no fairness guarantee; {wait})'))
        else:
            checks.append(_check(name,
                detail=f'non-assertion-face task tcb={tcb} scheduled {sched}x'))
    return checks


# ── H6 队列积压有界+消费指针前进(蓝图 §3 H6+任务书 §1.0b 第 6 条勘误:环形队列
#     指针正常按容量取模回绕,cursor_monotonic 检查的是逻辑入队/出队序号单调+
#     物理指针=序号 mod 容量的一致式;FAIL=积压超限/序号回退/取模不一致) ─────────
def _h6_queue(health):
    queue=health.get('queue')
    if not isinstance(queue,dict):
        return [_check('h6_backlog_bounded',
                missing=f'd1_window.health.queue{{count_final,qlimit,capacity,'
                        f'cursor_trace}}(契约 {SOURCE_HL})'),
               _check('h6_cursor_seq_monotonic',missing='d1_window.health.queue'
                       '(同上;键缺两子项皆 pending)')]
    checks=[]
    count=queue.get('count_final')
    if not isinstance(count,int):
        checks.append(_check('h6_backlog_bounded',
            missing='queue.count_final(窗口末队列计数 h[TCB+6])'))
    elif count==0:
        checks.append(_check('h6_backlog_bounded',
            detail='queue fully consumed at the window end (count_final == 0)'))
    elif isinstance(queue.get('qlimit'),int):
        checks.append(_check('h6_backlog_bounded',fail=count>queue['qlimit'],
            detail=f'count_final={count} <= qlimit={queue["qlimit"]} (bounded backlog)'
                   if count<=queue['qlimit'] else
                   f'count_final={count} > qlimit={queue["qlimit"]} (unbounded growth)',
            evidence=None if count<=queue['qlimit'] else _violation(
                'H6',f'count_final == 0 or <= qlimit ({queue["qlimit"]})',
                f'count_final={count}',subject='queue',
                window_end=_window_end_evidence(health))))
    else:
        checks.append(_check('h6_backlog_bounded',
            missing=f'queue.qlimit(队列上限;count_final={count}>0 时必须有界判据才可判)'))
    trace=queue.get('cursor_trace')
    if not isinstance(trace,list):
        checks.append(_check('h6_cursor_seq_monotonic',
            missing='queue.cursor_trace(游标轨迹;勘误口径=逻辑序号单调+取模一致式)'))
        return checks
    if not trace:
        if isinstance(count,int) and count>0:
            checks.append(_check('h6_cursor_seq_monotonic',fail=True,
                detail=f'empty cursor trace but count_final={count}>0: entries must have '
                       'moved the cursor (contradiction)',
                evidence=_violation('H6','cursor trace consistent with count_final',
                    f'empty trace, count_final={count}',subject='queue',
                    window_end=_window_end_evidence(health))))
        else:
            checks.append(_check('h6_cursor_seq_monotonic',
                detail='no cursor movement recorded in-window (vacuous; queue drained '
                       'before the window)'))
        return checks
    capacity=queue.get('capacity')
    window_end=_window_end_evidence(health)
    bad_shape=[i for i,t in enumerate(trace)
               if not isinstance(t,dict) or t.get('op') not in ('enqueue','dequeue')
               or not isinstance(t.get('seq'),int)]
    seq_back=[]
    for op in ('enqueue','dequeue'):
        seqs=[t['seq'] for t in trace if isinstance(t,dict) and t.get('op')==op
              and isinstance(t.get('seq'),int)]
        seq_back+=[(op,a,b) for a,b in zip(seqs,seqs[1:]) if b<=a]
    modulo_bad=[t for t in trace if isinstance(capacity,int) and capacity>0
                and isinstance(t,dict) and isinstance(t.get('physical'),int)
                and isinstance(t.get('seq'),int)
                and t['physical']!=t['seq']%capacity]
    reported=queue.get('cursor_monotonic')
    derived_ok=not bad_shape and not seq_back and not modulo_bad
    reported_bad=isinstance(reported,bool) and reported!=derived_ok
    if bad_shape or seq_back or modulo_bad or reported_bad:
        index=bad_shape[0] if bad_shape else 0
        first={'model_step':trace[index].get('step') if isinstance(trace[index],dict)
               else None,'core':None,'pc':None}
        around=trace[max(0,index-1):index+2]
        checks.append(_check('h6_cursor_seq_monotonic',fail=True,
            detail=f'cursor violations: shape {len(bad_shape)}, seq regressions {seq_back}'
                   f', modulo mismatches {len(modulo_bad)}'
                   +(f', reported cursor_monotonic={reported} != derived {derived_ok}'
                     if reported_bad else ''),
            evidence=_violation('H6',
                'logical enqueue/dequeue sequence numbers strictly increase and '
                'physical == seq mod capacity'
                +(f' (capacity={capacity})' if isinstance(capacity,int) else ''),
                f'{len(seq_back)} seq regression(s), {len(modulo_bad)} modulo mismatch(es)',
                first_violation=first,subject='queue',around=around,
                extra={'seq_regressions':seq_back[:4],
                       'modulo_mismatches':modulo_bad[:4],'capacity':capacity},
                window_end=window_end)))
    else:
        checks.append(_check('h6_cursor_seq_monotonic',
            detail='logical cursor sequence numbers strictly increase; physical == seq mod '
                   f'capacity ({capacity}) on every recorded move (physical wraparound is '
                   'legal; blueprint §3 H6 as corrected by taskbook §1.0b item 6)'
                   +(f'; reported cursor_monotonic={reported} agrees' if isinstance(
                     reported,bool) else '')))
    return checks


# ── H7 状态变迁可追溯(蓝图 §3 H7:每核状态时间线完整记录于报告,机器可复查非仅
#     终态快照;FAIL=时间线断链/矛盾——步号倒流/非法 kind/非法相邻转移) ───────────
def _h7_timelines(health):
    per_core=health.get('per_core')
    if not isinstance(per_core,dict):
        return [_check('h7_state_timeline_traceable',
            missing='d1_window.health.per_core[].timeline(每核状态转换流)')]
    checks=[]
    for core in sorted(per_core,key=str):
        entry=per_core[core]
        name=f'h7_core{core}_timeline'
        if not isinstance(entry,dict) or not isinstance(entry.get('timeline'),list):
            checks.append(_check(name,
                missing=f"per_core['{core}'].timeline(entered_idle_wait/woken/running "
                        '状态转换流;键缺=断链)'));continue
        timeline=entry['timeline']
        if not timeline:
            wake=entry.get('wake_count')
            if str(core)=='1':
                checks.append(_check(name,
                    detail='core1 recorded no state transition in-window (silent core, '
                           'vacuous; its wake data stays in the H4 record face)'))
            elif isinstance(wake,int) and wake>0:
                checks.append(_check(name,fail=True,
                    detail=f'core0 wake_count={wake} but an empty timeline: transitions '
                           'exist but were not recorded (broken trace)',
                    evidence=_violation('H7','per-core timeline records every transition',
                        f'wake_count={wake}, empty timeline',subject=f'core{core}',
                        window_end=_window_end_evidence(health))))
            else:
                checks.append(_check(name,
                    missing='core0 timeline empty (the zero-wake failure is H4\'s face; '
                            'the trace itself is missing here)'))
            continue
        window_end=_window_end_evidence(health)
        violations=[]
        for i,item in enumerate(timeline):
            if not isinstance(item,dict) or item.get('kind') not in HEALTH_TIMELINE_KINDS:
                violations.append((i,'kind'));break
            if i and (not isinstance(item.get('model_step'),int)
                      or not isinstance(timeline[i-1].get('model_step'),int)
                      or item['model_step']<=timeline[i-1]['model_step']):
                violations.append((i,'step'));break
            if i and item['kind'] not in \
                    HEALTH_TIMELINE_NEXT.get(timeline[i-1].get('kind'),()):
                violations.append((i,'transition'));break
        if violations:
            index,why=violations[0]
            first,around=_timeline_violation(timeline,index)
            checks.append(_check(name,fail=True,
                detail=f'timeline violation at entry {index} ({why}): broken/contradictory '
                       'transition stream',
                evidence=_violation('H7',
                    'steps strictly increase; kinds in '
                    f'{HEALTH_TIMELINE_KINDS}; transitions follow {HEALTH_TIMELINE_NEXT}',
                f'entry {index} violates {why}',first_violation=first,
                subject=f'core{core}',around=around,extra={'violation':why},
                window_end=window_end)))
        else:
            checks.append(_check(name,
                detail=f'{len(timeline)} transitions, steps '
                       f'{timeline[0].get("model_step")}..{timeline[-1].get("model_step")}, '
                       'machine-recheckable stream (not a terminal snapshot)'))
    return checks


# ── H8 观察窗相容性自检(REVIEW 盲区#5 裁决,路 PR-2;契约 docs/d1-health-checks.md)
#     窗口公式 N=max(S_tick+160k,200k) 的 160k 余量按 P=40,000 形状值预留 3 周期,
#     BY 实测 delay 函数会按场景重编 PRD(59999/47999/11999 三态),任意实读 P_park
#     下余量可破——折算 N//P_park<3 时输出 insufficient_window_coverage 拒绝给结论
#     (pending):窗口配置问题不是 DUT 行为违约,既不判过也不误判成 H1 FAIL;
#     证据复用 FAIL 证据契约七规则结构(rule='H8',first_violation 锚窗口配置点)。 ──
def _h8_window_coverage(health):
    name='h8_window_cycle_coverage'
    coverage,n,period=_window_cycle_coverage(health)
    if period is None:
        return [_check(name,missing='per_source[timer0].period(park 态实读 P_park;'
                'BY 实测 PRD 会被重编三态,不预锁 40,000 兜底——未实读=不猜)')]
    if not isinstance(n,int) or n<0:
        return [_check(name,missing='health.window.steps(或 model_step_end−'
                'model_step_start;H8 覆盖折算的窗口长度 N)')]
    if coverage>=MIN_HEALTH_CYCLES:
        return [_check(name,
            detail=f'window N={n} covers {coverage} full Timer0 cycles at the measured '
                   f'P_park={period} (>= {MIN_HEALTH_CYCLES}; margin N-3*P_park='
                   f'{n-MIN_HEALTH_CYCLES*period})')]
    return [_check(name,
        missing=f'insufficient_window_coverage: window N={n} at the measured '
                f'P_park={period} covers only {coverage} full cycles (<{MIN_HEALTH_CYCLES}; '
                'the health-check sample floor cannot be met inside this window — refuse '
                'to conclude rather than misjudge; window config, not DUT behavior)',
        evidence=_violation('H8',
            f'window N covers >= {MIN_HEALTH_CYCLES} full cycles at the measured P_park '
            f'(N >= {MIN_HEALTH_CYCLES}*P_park = {MIN_HEALTH_CYCLES*period})',
            f'N={n}, P_park={period}, coverage={coverage}',
            first_violation={'model_step':_health_window(health).get('model_step_start'),
                             'core':None,'pc':None,
                             'note':'window-config-level condition: no faulting '
                                    'instruction; anchored at W_park (window start)'},
            subject='window(timer0@core0)',
            extra={'coverage':coverage,'p_park':period,'n':n,
                   'required_n_min':MIN_HEALTH_CYCLES*period},
            window_end=_window_end_evidence(health)))]


def d1_5_health(scenario):
    result=_base('D1-5','窗口健康度 H1-H8:定时事件真实发生/同源重发有界/无同事件无限'
                 '重发/必要任务可唤醒/无断言面饿死/队列积压有界/状态变迁可追溯/'
                 '观察窗相容(否定式总闸:D1-1..4 全过而 H 任一 FAIL 则 D1 仍 FAIL;'
                 'H8 覆盖不足=insufficient 拒绝给结论,不判过也不误判)',
                 f'{SOURCE_D1} §1 D1-5;{SOURCE_BG} §3;{SOURCE_TB} §1.0b;'
                 f'{SOURCE_HL}(键契约);{SOURCE_RV} §3.1 盲区#5(H8)')
    window=_d1_window(scenario)
    why='d1_window report present (D1 acceptance-window scenario shape)'
    if not isinstance(window,dict):
        opened,reason=park_window(scenario)
        why=reason+'; H1-H8 stay pending'
    else:
        opened=True
    checks=[_check('park_window',status='pass' if opened else 'pending',detail=why)]
    if not opened:return _finalize(result,checks)
    health=window.get('health') if window else None
    if not isinstance(health,dict):
        checks.append(_check('health_report_keys',
            missing='d1_window.health.*(H1-H8 数据面:timer0_expiries/per_source/'
                    'event_reposts/queue_balance/per_core/ready_tasks/queue/window;'
                    f'冻结契约 {SOURCE_HL};生产者=probe 侧 PR-2)'))
        return _finalize(result,checks)
    checks+=_h1_timer0_events(health,window)
    checks+=_h2_source_bounds(health,window)
    checks+=_h3_reposts(health)
    checks+=_h4_wake_rewait(health)
    checks+=_h5_ready_tasks(health)
    checks+=_h6_queue(health)
    checks+=_h7_timelines(health)
    checks+=_h8_window_coverage(health)
    return _finalize(result,checks)


ASSERTIONS=(('D1-1',d1_1_park),('D1-2',d1_2_timer0_cycles),('D1-3',d1_3_core_tick),
            ('D1-4',d1_4_message_chain),('D1-5',d1_5_health),
            ('BI-A1',bi_a1_first_timer0_wake),
            ('BI-A2',bi_a2_period_conservation),('BI-A3',bi_a3_no_event_honest_stop),
            ('BI-A4',bi_a4_timer0_only_horizon),('BI-A5',bi_a5_c2_exit_two_levels))


def run_all(scenario):
    results=[fn(scenario) for _,fn in ASSERTIONS]
    summary={state:sum(1 for r in results if r['status']==state) for state in ('pass','pending','fail')}
    # 否定式总闸(蓝图 §3/任务书 §1.0b 第 4 条):D1-1..4 全 PASS 而 H1-H8 任一 FAIL
    # → D1 仍 FAIL——实现为 d1_5 的 fail 态在此不抵消;H8 覆盖不足(insufficient,
    # pending 面)同样使 gate 非 pass——H1-H7 全 PASS 不抵消窗口覆盖缺口(REVIEW
    # 盲区#5);BI-A* 不进 D1 gate(BI 首验独立于 D1 五断言)。
    d1={r['id']:r['status'] for r in results if r['id'].startswith('D1-')}
    gate=('fail' if 'fail' in d1.values()
          else 'pass' if d1 and all(v=='pass' for v in d1.values())
          else 'pending')
    return {'scenario':scenario.get('name','?'),'assertions':results,'summary':summary,
            'd1_gate':gate}


def main():
    parser=argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument('--report',type=Path,help='probe_irq_entry JSON report to evaluate')
    parser.add_argument('--scenario',default='p11_heap_experimental_config',
                        help='scenario name inside the report (default: the conditional main scenario)')
    parser.add_argument('--json',action='store_true',help='emit the structured result as JSON')
    args=parser.parse_args()
    if args.report is None:
        for aid,fn in ASSERTIONS:
            print(f'{aid:6s} {fn({}).get("title","")}')
            print(f'       source: {fn({}).get("source","")}')
        print('\nPass --report analysis/local-checks/irq-entry.json to evaluate a scenario report.')
        return 0
    report=json.loads(args.report.read_text())
    scenarios={s.get('name'):s for s in report.get('scenarios',())}
    scenario=scenarios.get(args.scenario)
    if scenario is None:
        print(f'scenario {args.scenario!r} not found; available: {sorted(scenarios)}',file=sys.stderr)
        return 2
    result=run_all(scenario)
    if args.json:
        print(json.dumps(result,indent=1,ensure_ascii=False))
        return 0
    print(f"scenario {result['scenario']}: "
          f"{result['summary']['pass']} pass / {result['summary']['pending']} pending / "
          f"{result['summary']['fail']} fail  (pending != pass)")
    print(f"D1 gate (D1-1..D1-5, negation gate): {result['d1_gate']}")
    for assertion in result['assertions']:
        print(f"\n[{assertion['status'].upper():7s}] {assertion['id']} {assertion['title']}")
        print(f"          source: {assertion['source']}")
        for check in assertion['checks']:
            line=f"  - {check['status']:7s} ({check['window']}) {check['name']}"
            if check.get('missing'):line+=f"  MISSING: {check['missing']}"
            elif check.get('detail'):line+=f"  {check['detail']}"
            print(line)
    return 0


if __name__=='__main__':
    sys.exit(main())
