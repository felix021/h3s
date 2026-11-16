#!/usr/bin/env python3
"""D1 验收窗口观察器(生产者半边,docs/d1-window-producer-spec.md §2-§3;路 PR2OBS)。

本文件是 PR-2 的**观察器半边提前量**:按规格实现 d1_window 键面的生产逻辑
(W_park 锚定/窗口步数 N+H8 自动延长/窗口截断含事件时钟快进段/per_source 四源
emit 纪律/(18,0)/(20,2) 链十站采集锚/双轴步数),供 probe 侧挂接(挂接=PR-2
实施路在 probe_boot/probe_irq_entry 加的几行,见模块尾 PR2_HOOKUP 注记)。

设计契约(规格 §1 决策 B/C):
- 纯观察 API:全部钩子只读;唯一行为件=窗口耗尽停(stop kind='window_complete',
  规格决策 C 授权)与事件时钟快进段 horizon 截断(观察器返回剩余上限,机器侧
  raise Blocked 转 stop,br28_machine.advance_event_clock 内实现)。
- 不 import checker(验收入口在 probe_irq_entry 侧调 check_d1_acceptance.run_all,
  观察器只产数据);键名一致性由 tools/test_d1_window_observer.py 对照单测钉死。
- 状态全住观察器实例,不往 DualCoreMachine/hardware 加实例属性——world_snapshot
  M5/M3 的 capture_static 枚举 vars(),新增可变实例属性会被当静态配置捕获、
  恢复世界必然失配拒载;br28_machine 的观察注册点是模块级变量(不在捕获面)。
- 观察器不依赖 machine.events(32 条滑窗,规格 §8.7):park/engagement/交付/RTI
  序列自累计(machine_face() 产 machine 面键)。

口径标注(规格红线的「不臆造」义务):
- [待 PR-2 实测] 的取数点(CHAIN_STAGES 的 id_read/表 1 walk 命中/handler 返回
  锚、sys_timer first_deadline 换算、TCB ready 扫描偏移、qlimit/capacity、
  queue_head、RAM 采样字段)一律产 None/缺键(=checker pending 语义),不硬编码。
- 双轴步数:模型步轴=DualCoreMachine.model_step_total(completed)(=completed+
  stalls+event_clock_advances,与 hardware.advance 调用数一一对应,公式源码核
  见该方法注记);指令步轴=completed 差分。两轴不互填换算(RBKREV P0)。
"""
import sys
from pathlib import Path

sys.path.insert(0,str(Path(__file__).resolve().parent))
from br28_model import DIVISORS  # divisor 表与模型/checker 同源(规格 §2.2:查表不自推导)
import br28_machine

# ── 文档锚点常量(docs/d1-acceptance.md §1/§2、d1-window-producer-spec §2.2/§2.6/§3.3、
#    d1-scene-blueprint §2.1-§2.4;与 check_d1_acceptance 同名常量的一致性由单测钉) ──
PARK_IDLE_PC='0x12c5ca'            # 两核 idle 任务体 idle 指令(BP §1.1)
K1_PC='0x601249c'                  # 金丝雀 K1
K2_PC='0x60124ae'                  # 金丝雀 K2
S19_LOOP_HEAD=0x60123e6            # S19 消息循环头
GET_EVENT_CALL=0x6064c8c           # get-event(buf,16),空队列 pend
CONSUME_PC=0x60123f0               # get-event 返回值消费点(r0!=13 重调;13=取得事件)
BIT20_PC=0x60123f6                 # bit20 检查(0x100000 位)
SPECIAL_PATH_PC=0x60108fe          # id==0x10000c 特路
S18_HANDLER_PC=0x6023962           # (18,0) 认领 handler,表 1[39]
TIMESLICE_CALL=0x60653ae           # 时间片调度 call
POST_API=0x6000cba                 # S18 post API(BZ §1.1)
POST_SITES=(0x60123aa,0x60123b2)   # post 调用站(规格 §3.3)
ENQUEUE_SITES=(0x6064978,0x606487c)  # 入队表示(BZ §1.1)
TABLE2_WALK_SITES=(0x600434a,0x60252c6)  # 表 2 walk 循环体(不认领返回 0)
PEND_WRITE_FACE=0x6064cf6          # 空队列 pend 写面首 PC(W_park 机械判据主锚,§2.2)
PEND_WRITE_FACE_END=0x6064d06      # 写面末 PC(BA §6.2)
YIELD_CHAIN_PCS=(0x6064188,0x60642bc,0x6064340)  # 让出链(BA §6.2)
SYS_TIMER_POLLER=0x6066de0         # sys_timer 轮询器(锚点,S9/S8)
SYS_TIMER_FIRE_SITES=(0x6066e32,0x6066e7c)  # 火回调点(到期判定命中,§2.4)
KEYSCAN_CALLBACK=0x6001944         # 键扫回调入口(S9 §1.1)
ADC_SCAN_CALLBACK=0x60349d2        # adc_scan 回调入口(S8 §1.1)
TIMER0_HANDLER_PC='0x12ece8'       # Timer0 IRQ4 handler(路 BE)
JIFFIES_WORD=0x125004              # core tick handler 的 jiffies 计数字(D1-3 jiffies+1 面)
TIMER0_RTI_PC='0x12ed0c'           # Timer0 RTI 尾(路 BE)
CORE_TICK_HANDLER_PC='0x60661a4'   # core tick handler(路 BF)
TASK_TABLE_PTR=0x126c5c            # 任务表[cnum=0]→TCB(first_task_setup 先例)
QUEUE_COUNT_OFFSET=6               # h[TCB+6] 队列计数(BA §6.2,半字)
CALLBACK_TABLE_PTR=0x126F6C        # 回调表(d1_4 verdict 互证,BZ)
WINDOW_BASE_STEPS=200_000          # N 公式下限(蓝图 §1.3)
WINDOW_MARGIN_STEPS=160_000        # N 公式余量(蓝图 §1.3)
H8_MIN_CYCLES=3                    # H8:N//P_park>=3(任务书 §1.2 第 7a 条)
KEYSCAN_TICKS=10                   # sys_timer 键扫 tick 数(静态,S9 §1.1)
ADC_SCAN_TICKS=5                   # sys_timer adc_scan tick 数(立即数,S8 §1.1)
CORE_TICK_ASSUMPTION=('core_tick_cnt_held_snapshot:'
                      'assumed-not-measured (NOT PRD-1)')  # CNT 冻结假设原文(任务书 §1.0)
PROJECTION_NOTE=('D1 window verdicts are conditional on the delivery/event-clock projection '
                 'chain (idle_wait/event_clock/timer0/core_tick/…; hardware_mode_certified'
                 '=false 不升级)')

# D1-4 消息链十站(checker CHAIN_STAGES 同名同序;一致性由单测钉死,观察器不 import checker)。
CHAIN_STAGES=('posted','enqueued','get_event_returned_13','id_read','bit20_checked',
              'special_path','table_record_hit','handler_executed','handler_returned',
              'pend_again')
# 已钉锚的站→watchpoint PC 集;不在表中的站=id_read/table_record_hit(表 1 命中)/
# handler_returned,[待 PR-2 实测:锚 PC]——站值保持 None(unobserved),不硬编码。
STAGE_SITES={'posted':(POST_API,),
             'enqueued':ENQUEUE_SITES,
             'get_event_returned_13':(CONSUME_PC,),
             'bit20_checked':(BIT20_PC,),
             'special_path':(SPECIAL_PATH_PC,),
             'handler_executed':(S18_HANDLER_PC,),
             'pend_again':(PEND_WRITE_FACE,)}
# 观察器维护的 watchpoint 面(d1_1/d1_3 消费的 machine.watchpoint_hits 键集)∪ 链/源锚。
WATCHPOINT_PCS=frozenset({S19_LOOP_HEAD,GET_EVENT_CALL,CONSUME_PC,BIT20_PC,SPECIAL_PATH_PC,
                          TIMESLICE_CALL,POST_API,PEND_WRITE_FACE,PEND_WRITE_FACE_END,
                          SYS_TIMER_POLLER,S18_HANDLER_PC,
                          *POST_SITES,*ENQUEUE_SITES,*TABLE2_WALK_SITES,
                          *YIELD_CHAIN_PCS,*SYS_TIMER_FIRE_SITES,
                          KEYSCAN_CALLBACK,ADC_SCAN_CALLBACK,
                          int(K1_PC,16),int(K2_PC,16)})


def timer0_period_closed_form(con,prd):
    """park 态 Timer0 周期闭式(规格 §2.2,与 checker _timer0_expected_period_steps
    同公式同源:DIVISORS 查表、per_step 由源字段判;非整步数→None 不猜)。"""
    if not isinstance(con,int) or not isinstance(prd,int) or not con&3:return None
    per_step=3 if (con>>10)&15==5 else 4
    counts=DIVISORS[(con>>4)&15]*(prd+1)
    return counts//per_step if counts%per_step==0 else None


def core_tick_deadline_closed_form(cnt,prd,progress):
    """core tick 首溢出相对步数 S=ceil((prd+1-cnt)/progress)(event-clock §2 条 3;
    缺任一输入或 progress<=0→None)。"""
    if not all(isinstance(v,int) for v in (cnt,prd,progress)) or progress<=0 or prd<0:
        return None
    return -(-(prd+1-cnt)//progress)


def core_tick_period_closed_form(prd,progress):
    """core tick 周期=(PRD+1)/progress(PRD 读回恒等链,progress 实读;非整除→None)。"""
    if not all(isinstance(v,int) for v in (prd,progress)) or progress<=0:
        return None
    counts=prd+1
    return counts//progress if counts%progress==0 else None


def _read_ram(mem,address,size):
    """RAM 直读(规格 §3.4:watchpoint 命中间的采样用 mem.ram 直读,模型簿记先例)。
    任一字节未初始化→None(不猜)。"""
    raw=[mem.ram.get(address+i) for i in range(size)]
    if any(byte is None for byte in raw):return None
    return int.from_bytes(bytes(raw),'little')


def _hex_or_none(value):
    return hex(value) if isinstance(value,int) else None


class _ChainTracker:
    """(18,0)/(20,2) 消息链跟踪(全程口径,规格 §3.3:主样本发生在 park 前首批迭代)。

    时间序状态机:POST_API 执行(call 不改通用寄存器,寄存器实参可在步后采样)→
    开样本;各站锚命中→三值 True;下一次 post/pend 写面/停点→闭合。站三值:
    True=锚观察到;False=后续站命中而本站零命中(观测到没发生=链断);
    None=锚未钉([待 PR-2 实测])或链未走到(未观测)。
    """
    def __init__(self):
        self.samples=[];self.open_sample=None;self.posts=0;self.last_producer_pc=None

    def note_pc(self,pc,step,registers=None):
        if pc in POST_SITES:
            self.last_producer_pc=hex(pc)
        if pc==POST_API:
            self.posts+=1
            if self.open_sample is not None:self._close(tail_unreached=True)
            args=None
            if registers is not None and len(registers)>=4:
                args=[hex(registers[i]) if isinstance(registers[i],int) else None
                      for i in range(4)]
            subcode=registers[2] if registers is not None and isinstance(registers[2],int) else None
            code=registers[3] if registers is not None and isinstance(registers[3],int) else None
            self.open_sample={'event':(f'({subcode},{code})' if subcode is not None
                                       and code is not None else '(?,?)'),
                              'origin':'firmware-natural',
                              'producer_pc':self.last_producer_pc,
                              'first_post_step':step,'post_args':args,
                              'hits':{**{stage:0 for stage in CHAIN_STAGES},
                                      'posted':1},
                              'first_hit_step':{'posted':step}}
        elif self.open_sample is not None:
            for stage,sites in STAGE_SITES.items():
                if pc in sites:
                    self.open_sample['hits'][stage]+=1
                    self.open_sample['first_hit_step'].setdefault(stage,step)
                    break
            if pc==PEND_WRITE_FACE and self.open_sample['hits']['get_event_returned_13']:
                self._close(tail_unreached=False)

    def _close(self,tail_unreached):
        sample=self.open_sample;self.open_sample=None
        if sample is None:return
        stages={}
        observed=[i for i,stage in enumerate(CHAIN_STAGES)
                  if sample['hits'][stage]>0 or stage not in STAGE_SITES]
        last_observed=max((i for i,stage in enumerate(CHAIN_STAGES)
                           if sample['hits'][stage]>0),default=-1)
        for i,stage in enumerate(CHAIN_STAGES):
            hits=sample['hits'][stage]
            if hits>0:stages[stage]=True
            elif stage in STAGE_SITES and i<last_observed:
                stages[stage]=False  # 已钉锚:后续站命中而本站零命中=观测到没发生
            else:
                stages[stage]=None   # 锚未钉([待 PR-2 实测])或链未走到:未观测,不猜
        closed={**{k:stages[k] for k in CHAIN_STAGES},
                'event':sample['event'],'origin':sample['origin'],
                'first_post_step':sample['first_post_step'],
                'post_args':sample['post_args'],'id_read_value':None,
                'producer_pc':sample['producer_pc'],'closed_at_step':sample['first_hit_step'].get('pend_again')}
        self.samples.append(closed)

    def finish(self):
        if self.open_sample is not None:self._close(tail_unreached=True)


class D1WindowObserver:
    """D1 验收窗口观察器(纯观察;唯一行为件=窗口耗尽停,规格决策 C)。

    生命周期:构造(注册 br28_machine.d1_window_observer)→ probe 每步喂
    note_step(executed_pc,completed)→ 停点后 finalize(stop) 产 d1_window 树;
    machine_face() 产 machine 面观察键(挂接路 merge 进报告 machine 键)。
    """

    def __init__(self,machine,hardware,*,d1_window_steps=None):
        require_ok=(d1_window_steps is None or
                    (isinstance(d1_window_steps,int) and not isinstance(d1_window_steps,bool)
                     and d1_window_steps>0))
        if not require_ok:
            raise ValueError('d1_window_steps must be None (formula) or a positive int')
        self.machine=machine;self.hardware=hardware
        self.explicit_steps=d1_window_steps
        # 全程/窗口计数面
        self._watch={}          # hex(pc)->执行次数(全程;stall 圈 idle 未执行不计)
        self._watch_first={}    # hex(pc)->首次执行模型步(全程)
        self._window_watch={}   # hex(pc)->窗口内执行次数(W_park 起)
        self._completed=0       # 最近一次 note_step 的 completed(指令步轴缓存)
        self._instructions=0    # 自累计指令步(机器钩子挂接模式,completed=None)
        # 到期步号观察面(D1RUN 首轮实测归因:D1-2/BI-A1/A2 判定需要「每次到期
        # 的模型步号」,模型层无该键——观察器侧 pending 边沿检测自累计,br28_model
        # 零触碰。采样点=每圈 note_step+交付/engage 钩子:真实步精度 ±1 步
        # (after_step 挂接位在 advance 前),快进段边沿=段终点即到期点)。
        self._pending_edges={'timer0':None,'core_tick':None}
        self._expiry_steps={'timer0':[],'core_tick':[]}
        # jiffies 写序观测(D1-3;0x125004,写后值影子链)
        self._jiffies_observations=[];self._jiffies_last=None
        self._loop_head_after_drain=0
        # W_park 锚定(§2.2:pend 写面首达后的首次 event_clock_engaged;boot 停泊
        # 全部发生在 S19 pend 写面之前,不触发)
        self._pend_write_seen=False
        self._w_park=None;self._park_instruction_step=None
        self._park_cores={};self._park_queue_count=None
        self._n_effective=None;self._n_extended=False;self._p_park=None;self._s_tick=None
        self._timer0_first_deadline=None
        # 窗口停(两处归一 stop['kind']='window_complete',§5)
        self._stopped=False;self._window_stop=None
        # 差分基线(W_park 落定瞬间快照,§2.4 全窗口差分)
        self._base={}
        # 事件序列(自累计,不依赖 machine.events 滑窗)
        self._park_log=[]       # entered/woken 序列(条目含 model_step,§2.6)
        self._engagements=[]    # 每次 engage 的计数器快照(§2.6 路CH需求键)
        self._deliveries=[]     # 窗口判别用交付记录(含 model_step,§2.4)
        self._rtis=[]           # RTI 记录(timer0/core_tick)
        self._chain=_ChainTracker()
        self._timeslice_seen_step=None;self._timeslice_returned=False
        self._pending_running={}  # core->irq(deliver 唤醒后待记 running 转换)
        self._timeline={0:[],1:[]}
        self._dispatch_handler_steps=[]  # 窗口内 handler 执行步(last_dispatch_step)
        self._queue_ops=[]        # 窗口内入队/出队 (op,model_step) 流(H6 cursor_trace)
        self._per_source=[]       # W_park 落定时初始化(§3.1);boot 前恒空
        self._last_producer_pc=None
        br28_machine.d1_window_observer=self

    def detach(self):
        if br28_machine.d1_window_observer is self:
            br28_machine.d1_window_observer=None

    # ── 模型步轴(§2.1;两轴纪律:指令步轴=completed 差分,不互填) ─────────────
    def _model_step(self,machine,extra_completed=0):
        """当前全局模型步(=hardware.advance 总调用数口径;extra_completed=钩子
        发生在本圈 c.step() 已过、explore 尚未 completed+=1 的窗口时的 +1 修正)。"""
        return machine.model_step_total(self._completed+extra_completed)

    # ── machine 侧钩子(br28_machine 调用;全部只读) ──────────────────────────
    def _eligibility_note(self,machine,event):
        """core-tick 交付时刻的资格面快照(IRQController.eligible 同款只读判定)。"""
        irq=machine.hardware.irq
        index=event.get('irq')
        value=irq.config[index//8]
        nibble=(value>>4*(index%8))&15
        priority=nibble>>1
        core=event.get('core',0)
        cpu=machine.cores.get(core)
        icfg=cpu.icfg if cpu is not None and getattr(cpu,'icfg_known',False) else None
        return {'core':core,'ipmask':irq.ipmask,'priority':priority,
                'eligible':bool(irq.eligible(index,icfg)),
                'note':'sampled at delivery dispatch (pending recheck per BF semantics; '
                       'delivery having happened = the gate check passed at that time)'}

    def _sample_pending_edges(self,machine):
        """Timer0/core-tick pending False→True 边沿=到期步号(观察器侧观察面,
        见 __init__ 注记;窗口未开/已停不采)。"""
        if self._w_park is None or self._stopped:return
        timer=machine.hardware.timer
        tick=(machine.hardware.core_ticks or (None,))[0]
        step=self._model_step(machine)
        for name,obj in (('timer0',timer),('core_tick',tick)):
            if obj is None:continue
            pending=obj.pending
            last=self._pending_edges.get(name)
            if last is False and pending is True and step>self._w_park:
                self._expiry_steps[name].append(step)
            self._pending_edges[name]=pending

    def note_idle_wait_entered(self,machine,cpu):
        step=self._model_step(machine)
        self._park_log.append({'kind':'core_idle_wait_entered','core':cpu.core_id,
                               'pc':hex(cpu.pc),'model_step':step})
        if self._w_park is not None and step>self._w_park:
            self._timeline[cpu.core_id].append({'model_step':step,
                                                'kind':'entered_idle_wait','pc':hex(cpu.pc),
                                                'irq':None,'nesting':0})

    def note_event_clock_engaged(self,machine,cpu):
        step=self._model_step(machine)
        timer=machine.hardware.timer
        ticks=machine.hardware.core_ticks or ()
        self._engagements.append({
            'model_step':step,
            'timer0':{'con':timer.con,'cnt':timer.cnt,'prd':timer.prd,
                      'remainder':timer.remainder,'source_remainder':timer.source_remainder},
            'core_ticks':{'0':{'con':ticks[0].con,'cnt':ticks[0].cnt,'prd':ticks[0].prd,
                               'progress':ticks[0].progress}} if ticks else {},
            # [待 PR-2 实测核对差值]:段级 advances/段间执行需要 advance_event_clock
            # 的段边界钩子,本半边不挂——BI-A1/A2 消费面按观测缺口 pending。
            'first_segment_advances':None,'inter_segment_execution':None})
        if self._w_park is None and self._pend_write_seen:
            self._open_window(machine,cpu,step)

    def note_irq_delivery(self,machine,event,woke_from_idle_wait):
        extra=0 if woke_from_idle_wait else 1
        step=self._model_step(machine,extra)
        self._sample_pending_edges(machine)   # 快进段终点=交付点的到期边沿
        record={**event,'model_step':step,'woke_from_idle_wait':woke_from_idle_wait}
        if event.get('irq')==3:
            # D1-3 资格面([路 CH 需求] 键的观察器落地):交付时刻采样 ipmask/
            # priority/eligible(交付已发生=当时 pending 重检通过;BF 语义)。
            try:
                record['eligibility_note']=self._eligibility_note(machine,event)
            except Exception:
                record['eligibility_note']=None
        self._deliveries.append(record)
        core=event.get('core')
        if woke_from_idle_wait:
            self._park_log.append({'kind':'core_woken_by_delivery','core':core,
                                   'irq':event.get('irq'),'model_step':step})
            self._pending_running[core]=event.get('irq')
            if self._w_park is not None and step>self._w_park:
                self._timeline[core].append({'model_step':step,'kind':'woken',
                                             'pc':event.get('reti_from'),
                                             'irq':event.get('irq'),'nesting':0})
        if self._w_park is not None and step>self._w_park:
            source=f"irq{event.get('irq')}"
            if source not in {entry.get('source') for entry in self._per_source} and \
                    source not in ('irq3','irq4'):
                # 非周期源触达才入表(§2.4:预期 0;软件 IRQ124-127)。irq3/irq4
                # 除外(D1RUN 首轮实测归因):timer0/core_tick 交付的 irq 字段
                # 即 4/3,它们已有专属周期条目——重复入表会把同一批交付记成
                # 「零到期高交付」的 interrupt-storm 假阳性(H2)。
                self._per_source.append({'source':source,'core':core,'period':None,
                                         'expirations':0,'deliveries':0,'completions':0,
                                         'first_deadline':None,
                                         'pending_at_window_start':False})

    def note_irq_rti(self,machine,kind,cpu):
        self._rtis.append({'kind':kind,'core':cpu.core_id,
                           'reti_from_frame':hex(cpu.reti) if isinstance(cpu.reti,int) else None,
                           'model_step':self._model_step(machine,1)})

    def note_memory_write(self,address,data):
        """RAM 写钩子(D1-3 jiffies 面):0x125004 4 字节写序观测。写后值=写
        载荷,写前值=观察器影子(上次写后值;首次不可知=RAM 已被覆盖,记 None
        ——+1 语义由相邻写后值差判定,等价口径如实标注)。全程记录带模型步,
        窗口切分归 checker。"""
        target=JIFFIES_WORD
        if not (address<=target and target+4<=address+len(data)):return
        after=int.from_bytes(data[target-address:target-address+4],'little')
        core_ticks_so_far=sum(1 for d in self._deliveries if d.get('irq')==3)
        self._jiffies_observations.append({'model_step':self._model_step(self.machine),
                                           'before':self._jiffies_last,'after':after,
                                           'delivery_index':core_ticks_so_far-1})
        self._jiffies_last=after

    def event_clock_horizon_limit(self,machine):
        """事件时钟快进段的窗口剩余步数上限(§5 机器侧 horizon 截断)。

        窗口未开/已停→None(零干预);剩余>0→int(br28_machine 并入本段 horizon 的
        min);剩余<=0→置位窗口停并返回 0,由机器侧 raise Blocked('window_complete')。"""
        if self._w_park is None or self._stopped:return None
        remaining=self._w_park+self._n_effective-self._model_step(machine)
        if remaining<=0:
            self._request_window_stop(machine)
            return 0
        return remaining

    def window_face(self):
        return {'model_step_start':self._w_park,'steps':self._n_effective}

    def expiry_steps_faces(self):
        """到期步号观察面(D1RUN:checker 的 D1-2/D1-3 消费 peripherals.timer0.
        expiration_model_steps 与 core_tick_configuration[0].expiration_model_
        steps——观察器侧 pending 边沿自累计,由 post_stop 注入 peripherals 面;
        窗口未开=空列表,如实呈现)。"""
        return {'timer0':list(self._expiry_steps['timer0']),
                'core_tick':list(self._expiry_steps['core_tick'])}

    # ── probe 挂点(explore 主循环尾每圈一次;§1 数据流) ─────────────────────
    def note_step(self,cpu,executed_pc,completed,stalled=False):
        """主循环每圈尾喂:本圈执行的指令 PC(stall 圈 idle 未执行,stalled=True)、
        圈尾 completed。观察点:watchpoint 计数/链跟踪/窗口耗尽停。
        completed=None=机器钩子挂接模式(br28_machine after_step/note_stall):
        观察器自累计指令步——attach 先于首指令,相对轴=绝对指令步轴。"""
        if completed is None:
            self._instructions+=0 if stalled else 1
            completed=self._instructions
        self._completed=completed
        machine=self.machine
        step=machine.model_step_total(completed)
        in_window=self._w_park is not None and step>self._w_park
        if not stalled:
            pc_hex=hex(executed_pc)
            self._watch[pc_hex]=self._watch.get(pc_hex,0)+1
            self._watch_first.setdefault(pc_hex,step)
            if in_window:
                self._window_watch[pc_hex]=self._window_watch.get(pc_hex,0)+1
                if executed_pc in ENQUEUE_SITES:
                    self._queue_ops.append(('enqueue',step))
                elif executed_pc==CONSUME_PC:
                    self._queue_ops.append(('dequeue',step))
            self._chain.note_pc(executed_pc,step,cpu.R)
            if executed_pc==S19_LOOP_HEAD and in_window:
                self._loop_head_after_drain+=1
            if executed_pc==TIMESLICE_CALL and in_window:
                self._timeslice_seen_step=step
            elif self._timeslice_seen_step is not None and not self._timeslice_returned:
                self._timeslice_returned=True  # 时间片 call 后继续执行=返回的最小证据
            if executed_pc==S18_HANDLER_PC and in_window:
                self._dispatch_handler_steps.append(step)
            if executed_pc==PEND_WRITE_FACE:
                # W_park 锚(§2.2 修正二稿,D1RUN r1-r3 实测定稿):「末次空队列
                # pend 写面之后的首次 engage」。PR2SPEC §2.2 原锚(首次 pend 后
                # engage)被 r1 证伪——boot 期任务在各自消息队列 pend(Q1 实测
                # 17.7M 即出现),首锚落进 boot 中段;S19 循环头前置(r2/r3)也被
                # 证伪——当前前沿世界 S19 消息循环尚未建立(0x60123e6 零执行,
                # watchpoint 机器可证),窗口不可开。末次 pend 语义:窗口内出现
                # 新 pend=先前锚定的停泊不是稳态终形,重锚让位;在 S19 已建立
                # 的未来前沿(S19 空队列 pend=末次 pend)该锚自愈为 PR2SPEC 原义。
                # 口径如实:当前前沿下窗口锚定的是 JLEQ 判定口径的稳态 park
                # (两核 idle+事件循环),与 S19 终态 park 的差异在验收记录声明。
                self._pend_write_seen=True
                if self._w_park is not None and not self._stopped:
                    self._w_park=None
                    self._expiry_steps={'timer0':[],'core_tick':[]}
                    self._pending_edges={'timer0':None,'core_tick':None}
                    self._timeline={0:[],1:[]}
                    self._loop_head_after_drain=0
                    self._timeslice_seen_step=None;self._timeslice_returned=False
                    self._dispatch_handler_steps=[]
                    self._queue_ops=[]
                    self._n_effective=None;self._n_extended=False
            core=cpu.core_id
            if core in self._pending_running:
                # pop 不设窗口守卫(boot 期唤醒也要消化簿记);timeline 的 running
                # 条目同 woken/entered 一样只记窗口内(D1RUN 首轮实测归因:boot
                # 期 CPU1 唤醒后的 running 混进 timeline,与窗口内 woken/entered
                # 守卫不一致 → H7 转换流在首条 running 处断裂)。
                irq=self._pending_running.pop(core)
                if in_window:
                    self._timeline[core].append({'model_step':step,'kind':'running',
                                                 'pc':pc_hex,'irq':irq,'nesting':0})
        if self._w_park is not None and not self._stopped:
            self._sample_pending_edges(machine)
            if step-self._w_park>=self._n_effective:
                self._request_window_stop(machine)

    @property
    def window_stop_request(self):
        """主循环侧窗口停请求(挂接路:非 None → stop=该 dict 并 break,§5)。"""
        return self._window_stop

    def _request_window_stop(self,machine):
        self._stopped=True
        self._window_stop={'kind':'window_complete',
                           'note':'d1 window exhausted: N model steps after W_park',
                           'window':self.window_face()}

    # ── W_park 落定与窗口步数(§2.2/§2.3/§5) ─────────────────────────────────
    def _open_window(self,machine,cpu,step):
        hardware=machine.hardware
        timer=hardware.timer
        tick=(hardware.core_ticks or (None,))[0]
        self._w_park=step
        # 到期边沿基线(开窗瞬间的 pending 状态,防开窗前的未知态当 False 误判)
        self._pending_edges={'timer0':timer.pending,
                             'core_tick':tick.pending if tick is not None else None}
        self._park_instruction_step=self._completed
        self._park_cores={str(cid):{'pc':hex(core_cpu.pc),**
                                    ({'via':'scheduler_idle_switch'}
                                     if cid==0 and core_cpu.pc==int(PARK_IDLE_PC,16) else {})}
                          for cid,core_cpu in machine.cores.items()}
        self._p_park=timer0_period_closed_form(timer.con,timer.prd)
        self._s_tick=(core_tick_deadline_closed_form(tick.cnt,tick.prd,tick.progress)
                      if tick is not None else None)
        deadline=self._s_tick
        self._park_queue_count=self._queue_count(cpu.mem)
        # N 公式(§2.3):显式值覆盖(不参与 H8 延长);默认 max(S_tick+160k,200k)
        # (S_tick 缺→下限 200k,不猜);公式 N 且 P_park 实读可用→H8 自检
        # N//P_park<3 自动延长至 3*P_park(任务书 §1.2 第 7a 条延长优先)。
        if self.explicit_steps is not None:
            self._n_effective=self.explicit_steps
        else:
            self._n_effective=WINDOW_BASE_STEPS if deadline is None else \
                max(deadline+WINDOW_MARGIN_STEPS,WINDOW_BASE_STEPS)
            if self._p_park is not None and self._n_effective//self._p_park<H8_MIN_CYCLES:
                self._n_effective=max(self._n_effective,H8_MIN_CYCLES*self._p_park)
                self._n_extended=True
        if timer.pending:
            self._timer0_first_deadline=1  # pending 已置位:下一 advance 步即事件(相位容差对应)
        else:
            self._timer0_first_deadline=getattr(hardware,'timer0_wake_horizon',
                                                lambda icfg:None)(cpu.icfg)
        # 差分基线(§2.4:全窗口差分;timer0/core_tick 既有全程计数器)
        self._base={'timer0_expirations':timer.expirations,
                    'core_tick_expirations':tick.expirations if tick is not None else 0,
                    'core_tick_acks':tick.acknowledgements if tick is not None else 0}
        # per_source emit 纪律(§3.1/§3.2:W_park 落定点一次性初始化条目集——
        # timer0/core_tick/adc_scan 无条件;keyscan 当且仅当 cfg523_installed_at
        # 运行时证据非 None,不用场景 flag)。
        period_p=self._p_park
        self._per_source=[
            {'source':'timer0','core':0,'period':period_p,
             'expirations':0,'deliveries':0,'completions':0,
             'first_deadline':(step+self._timer0_first_deadline)
                              if isinstance(self._timer0_first_deadline,int) else None,
             'pending_at_window_start':bool(timer.pending)},
            {'source':'core_tick','core':0,
             'period':core_tick_period_closed_form(tick.prd,tick.progress)
                       if tick is not None else None,
             'expirations':0,'deliveries':0,'completions':0,
             'first_deadline':step+deadline if isinstance(deadline,int) else None,
             'pending_at_window_start':bool(tick.pending) if tick is not None else False},
        ]
        if getattr(machine,'cfg523_installed_at',None) is not None:
            self._per_source.append({'source':'keyscan','kind':'sys_timer','core':0,
                                     'period':KEYSCAN_TICKS*period_p
                                              if isinstance(period_p,int) else None,
                                     'expirations':0,'deliveries':0,'completions':0,
                                     'first_deadline':None,  # [待 PR-2 实测:链表节点换算]
                                     'pending_at_window_start':False})
        self._per_source.append({'source':'adc_scan','kind':'sys_timer','core':0,
                                 'period':ADC_SCAN_TICKS*period_p
                                          if isinstance(period_p,int) else None,
                                 'expirations':0,'deliveries':0,'completions':0,
                                 'first_deadline':None,  # [待 PR-2 实测:链表节点换算]
                                 'pending_at_window_start':False})
        self._window_watch={}

    def _queue_count(self,mem):
        tcb=_read_ram(mem,TASK_TABLE_PTR,4)
        if tcb is None:return None
        return _read_ram(mem,tcb+QUEUE_COUNT_OFFSET,2)

    def _callback_table_word(self):
        cpu=self.machine.cores.get(0)
        return _read_ram(cpu.mem,CALLBACK_TABLE_PTR,4) if cpu is not None else None

    # ── 停点后组装(§2 全键面;post_stop 通道注入顶层 d1_window 键) ────────────
    def finalize(self,stop=None):
        """停点后组装 d1_window 树。stop=explore 的停点 dict(挂接路喂;
        None=仅观察快照,window.complete 如实 False)。"""
        self._chain.finish()
        machine=self.machine;hardware=self.machine.hardware
        timer=hardware.timer
        tick=(hardware.core_ticks or (None,))[0]
        total=machine.model_step_total(self._completed)
        window_open=self._w_park is not None
        d1_4_samples=[s for s in self._chain.samples]
        natural=[s for s in d1_4_samples if s.get('origin')=='firmware-natural']
        broken=[s for s in natural if any(s.get(stage) is False for stage in CHAIN_STAGES)]
        complete_pre_park=[s for s in d1_4_samples
                           if window_open and isinstance(s.get('first_post_step'),int)
                           and s['first_post_step']<=self._w_park]
        tree={}
        if window_open:
            core_tick_state={'cnt':tick.cnt if tick is not None else None,
                             'prd':tick.prd if tick is not None else None,
                             'deadline_closed_form':(self._w_park+self._s_tick)
                                                    if isinstance(self._s_tick,int) else None,
                             'assumption':CORE_TICK_ASSUMPTION}
            # 到期步号=观察器 pending 边沿自累计(D1RUN:模型层无该键,见
            # __init__ 注记;不再 getattr 模型属性)。
            window_expiries=list(self._expiry_steps['timer0'])
            tree['park']={'model_step':self._w_park,
                          'instruction_step':self._park_instruction_step,
                          'cores':self._park_cores,'event_clock_engaged':True,
                          'timer0_state':{'con':_hex_or_none(timer.con),
                                          'cnt':timer.cnt,'prd':timer.prd,
                                          'period_closed_form':self._p_park},
                          'core_tick_state':dict(core_tick_state),
                          'queue_count':self._park_queue_count}
            tree['core_tick_state']=core_tick_state  # 顶层重复一份(键位=顶层优先,§1.4)
            tree['loop_head_first_step']=self._watch_first.get(hex(S19_LOOP_HEAD))
            tree['loop_head_count']=self._watch.get(hex(S19_LOOP_HEAD),0)
            tree['loop_head_count_after_drain']=self._loop_head_after_drain
            tree['s18_first_pass']=[
                {'subcode':_subcode_of(s),'handler_hits':([S18_HANDLER_PC]
                                                          if s.get('handler_executed') else []),
                 'return':None,  # handler 返回锚 [待 PR-2 实测]
                 'special_path':('sys_timer_0x126F3C_id2' if s.get('special_path') else None),
                 'first_post_step':s.get('first_post_step')}
                for s in complete_pre_park]
            tree['empty_queue_pend']={
                'write_face_pcs':[hex(pc) for pc in (PEND_WRITE_FACE,PEND_WRITE_FACE_END)
                                  if self._watch.get(hex(pc))],
                'yield_chain_pcs':[hex(pc) for pc in YIELD_CHAIN_PCS
                                   if self._watch.get(hex(pc))],
                'tcb':_hex_or_none(_read_ram(machine.cores[0].mem,TASK_TABLE_PTR,4))
                      if machine.cores else None,
                'queue_head':None,  # [待 PR-2 实测:环基址实读来源]
                'queue_count':self._park_queue_count}
            tree['dispatch']={'table2_records_walked':sum(self._watch.get(hex(pc),0)
                                                          for pc in TABLE2_WALK_SITES),
                              'dynamic_filter_ptr':None,  # [待 PR-2 实测:[0x126f4c] 读锚]
                              'table1_records_walked':None,  # [待 PR-2 实测:表 1 walk 锚]
                              'hit_records':[]}
            tree['canaries']={f'k1_{K1_PC}':self._watch.get(K1_PC,0),
                              f'k2_{K2_PC}':self._watch.get(K2_PC,0)}
            tree['timer0_cycles']=self._timer0_cycles(window_expiries)
            tree['core_tick']=self._core_tick_face(list(self._expiry_steps['core_tick']))
            tree['d1_4']=self._d1_4_face(natural,broken)
            tree['health']=self._health_face(stop,total,window_expiries,natural)
            tree['d1_model_steps_total']=total
            tree['d1_window_instruction_steps']=self._completed-self._park_instruction_step
            blocked=[]
            if stop is not None and stop.get('kind')!='window_complete':
                # 窗口已开而停点非 window_complete(§5:D1-2 FAIL 面;boot 前停=正常 pending)
                blocked=[{'kind':stop.get('kind'),'note':stop.get('note'),
                          'window_model_step':total,'stop':stop}]
            tree['window_blocked_events']=blocked
        else:
            tree['window_blocked_events']=[]  # 窗口未开:前沿未到,pending 语义
        tree['projection']=PROJECTION_NOTE
        return tree

    # ── 子树组装 ──────────────────────────────────────────────────────────────
    def _timer0_cycles(self,window_expiries):
        cycles=[]
        previous=None
        deliveries=[d for d in self._deliveries
                    if d.get('irq')==4 and self._w_park is not None
                    and d['model_step']>self._w_park]
        rti_steps=[r for r in self._rtis if r['kind']=='timer0_irq_rti']
        repark_steps=[e['model_step'] for e in self._park_log
                      if e['kind']=='core_idle_wait_entered' and e.get('core')==0
                      and e['model_step']>self._w_park]
        for index,delivery in enumerate(deliveries):
            expiry=(window_expiries[index] if window_expiries is not None
                    and index<len(window_expiries) else None)
            gap=(expiry-previous if isinstance(expiry,int) and isinstance(previous,int)
                 else None)
            previous=expiry if isinstance(expiry,int) else previous
            matched_rti=[r for r in rti_steps
                         if r.get('reti_from_frame')==delivery.get('reti_from')]
            re_parked=any(step>delivery['model_step'] for step in repark_steps)
            cycles.append({'index':index,'expiry_model_step':expiry,
                           'gap_from_previous':gap,'delivered':True,
                           'handler_entry_pc':TIMER0_HANDLER_PC,
                           'acked':None,        # w1c 计数键 [路 CH 需求,挂接路带出]
                           'w1c_count':None,
                           'rti_pc':TIMER0_RTI_PC if matched_rti else None,
                           'reti_value':delivery.get('reti_from'),'re_parked':re_parked,
                           'tick_count_delta':None,'tick_ms_delta':None,
                           'sys_timer_polled':None,   # RAM 差分 [待 PR-2 实测:采样点]
                           'delivery_delay_steps':None})
        return cycles

    def _core_tick_face(self,window_expiries):
        deliveries=[d for d in self._deliveries
                    if d.get('irq')==3 and self._w_park is not None
                    and d['model_step']>self._w_park]
        tick=(self.hardware.core_ticks or (None,))[0]
        base_expirations=self._base.get('core_tick_expirations',0)
        base_acks=self._base.get('core_tick_acks',0)
        first_expiry=next((s for s in window_expiries if isinstance(s,int)),None)
        return {'deadline_closed_form':(self._w_park+self._s_tick)
                                       if isinstance(self._s_tick,int) else None,
                'actual_overflow_model_step':first_expiry,
                'handler_entry_pc':CORE_TICK_HANDLER_PC if deliveries else None,
                'ack_count':(tick.acknowledgements-base_acks) if tick is not None else None,
                'jiffies_before':None,'jiffies_after':None,  # [路 CH 需求,挂接路带出]
                'timeslice_call':{'executed':self._timeslice_seen_step is not None,
                                  'returned':self._timeslice_returned},
                'task_switch':{'occurred':False}}  # 观察项 [路 CH 需求,挂接路带出]

    def _d1_4_face(self,natural,broken):
        if natural:
            sample=natural[0]
            args=sample.get('post_args')
            return {'post':{'call_pc':hex(POST_API),'args':args},
                    'enqueue':{'slot_header_word':None,   # RAM 采样 [待 PR-2 实测]
                               'queue_count_before':None,'queue_count_after':None},
                    'get_event':{'return_code':13
                                 if sample.get('get_event_returned_13') else None},
                    'event_id_read':None,                 # id_read 锚 [待 PR-2 实测]
                    'special_path':{'taken':sample.get('special_path') is True,
                                    'subcode':_subcode_of(sample)},
                    'walk':{'table2_hits':sum(self._watch.get(hex(pc),0)
                                              for pc in TABLE2_WALK_SITES),
                            'filter_ptr':None,            # [待 PR-2 实测:[0x126f4c] 读锚]
                            'table1_hit_record':None},    # 表 1 命中锚 [待 PR-2 实测]
                    'handler':{'entry_pc':S18_HANDLER_PC
                               if sample.get('handler_executed') else None,
                               'executed_steps':None,'gates':None}}  # R4 门控运行值 [待 PR-2 实测]
        reason=('queue empty with the callback table populated: link up, no firmware-natural '
                'event source in window' if self._w_park is not None
                else 'window not reached (frontier below S19 park)')
        return {'no_sample_reason':reason}

    def _health_face(self,stop,total,window_expiries,natural):
        machine=self.machine;hardware=self.hardware
        timer=hardware.timer
        tick=(hardware.core_ticks or (None,))[0]
        complete=(stop is not None and stop.get('kind')=='window_complete') or self._stopped
        per_source=[dict(entry) for entry in self._per_source]
        by_source={entry['source']:entry for entry in per_source}
        def _bump(source,key,amount):
            if source in by_source:by_source[source][key]=amount
        _bump('timer0','expirations',
              timer.expirations-self._base.get('timer0_expirations',0))
        _bump('core_tick','expirations',
              (tick.expirations if tick is not None else 0)
              -self._base.get('core_tick_expirations',0))
        _bump('core_tick','completions',
              (tick.acknowledgements if tick is not None else 0)
              -self._base.get('core_tick_acks',0))
        for entry in per_source:
            source=entry['source']
            if source in ('timer0','core_tick'):
                irq=4 if source=='timer0' else 3
                entry['deliveries']=len([d for d in self._deliveries
                                         if d.get('irq')==irq and d['model_step']>self._w_park])
            elif source=='keyscan':
                entry['expirations']=sum(self._window_watch.get(hex(pc),0)
                                         for pc in SYS_TIMER_FIRE_SITES)
                entry['deliveries']=self._window_watch.get(hex(KEYSCAN_CALLBACK),0)
                entry['completions']=None  # 回调返回锚 [待 PR-2 实测]
            elif source=='adc_scan':
                entry['expirations']=sum(self._window_watch.get(hex(pc),0)
                                         for pc in SYS_TIMER_FIRE_SITES)
                entry['deliveries']=self._window_watch.get(hex(ADC_SCAN_CALLBACK),0)
                entry['completions']=None
            elif entry['period'] is None:
                name=entry['source']
                irq=int(name[3:]) if name.startswith('irq') else None
                entry['deliveries']=len([d for d in self._deliveries
                                         if irq is not None and d.get('irq')==irq
                                         and d['model_step']>self._w_park])
        if 'timer0' in by_source:
            by_source['timer0']['completions']=len(
                [r for r in self._rtis if r['kind']=='timer0_irq_rti'
                 and r['model_step']>self._w_park])
        # per_core(§2.5:H4 判据面=CPU0;timeline 步号严格递增由事件序保证)
        per_core={}
        for core in (0,1):
            woken=[e for e in self._park_log
                   if e['kind']=='core_woken_by_delivery' and e.get('core')==core
                   and e['model_step']>self._w_park]
            entered=[e for e in self._park_log
                     if e['kind']=='core_idle_wait_entered' and e.get('core')==core
                     and e['model_step']>self._w_park]
            reasons={}
            for e in woken:
                key=f"irq{e.get('irq')}"
                reasons[key]=reasons.get(key,0)+1
            cpu=machine.cores.get(core)
            last_open=None
            if cpu is not None and core not in machine.idle_wait_cores:
                last_open={'last_pc':hex(cpu.pc),
                           'in_flight_irq':getattr(cpu,'active_context',None),
                           'nesting_level':0}
            per_core[str(core)]={'wake_count':len(woken),'rewait_count':len(entered),
                                 'wake_reasons':reasons,
                                 'timeline':self._timeline[core],'last_open_wake':last_open}
        # event_reposts/queue_balance(窗口口径;正常窗口 reposts 为空=H3 vacuous)
        reposts={}
        for sample in natural:
            if not (isinstance(sample.get('first_post_step'),int)
                    and sample['first_post_step']>self._w_park):
                continue
            key=(sample.get('event'),tuple(sample.get('post_args') or ()))
            entry=reposts.setdefault(key,{'event':'?','event_id':None,'subcode':None,
                                          'origin':'firmware-natural','posts':0,'consumed':0,
                                          'producer_pc':None,
                                          'first_post_step':sample['first_post_step'],
                                          'pending_at_window_end':False})
            entry['posts']+=1
            entry['consumed']+=1 if sample.get('get_event_returned_13') else 0
        repost_list=[]
        for (event,args),entry in reposts.items():
            entry['event']=event
            if len(args)>=4:
                entry['event_id']=args[1]
                entry['subcode']=int(args[2],16) if isinstance(args[2],str) else args[2]
            repost_list.append(entry)
        queue_count_final=(self._queue_count(machine.cores[0].mem)
                           if machine.cores else None)
        for entry in repost_list:
            entry['pending_at_window_end']=(None if queue_count_final is None
                                            else bool(queue_count_final))
        cursor_trace=[{'step':step,'op':op,'seq':seq,'physical':None}
                      for seq,(op,step) in enumerate(self._queue_ops)]
        cursor_monotonic=all(a['step']<b['step']
                             for a,b in zip(cursor_trace,cursor_trace[1:]))
        health={'window':{'model_step_start':self._w_park,
                          'model_step_end':total if self._stopped or stop is not None else None,
                          'steps':self._n_effective,'complete':complete},
                'timer0_expiries':((timer.expirations-self._base.get('timer0_expirations',0))
                                   if self._w_park is not None else 0),
                'timer0_expiry_steps':window_expiries,
                'per_source':per_source,
                'event_reposts':repost_list,
                'queue_balance':{'enqueued':sum(self._window_watch.get(hex(pc),0)
                                                for pc in ENQUEUE_SITES),
                                 'dequeued':sum(1 for s in natural
                                                if isinstance(s.get('first_post_step'),int)
                                                and s['first_post_step']>self._w_park
                                                and s.get('get_event_returned_13'))},
                'per_core':per_core,
                'queue':{'count_final':queue_count_final,
                         'qlimit':None,    # [待 PR-2 实测:实读来源]
                         'capacity':None,  # [待 PR-2 实测:实读来源]
                         'cursor_trace':cursor_trace,'cursor_monotonic':cursor_monotonic}}
        return health

    # ── machine 面观察键(§2.6 路CH需求;挂接路 merge 进 result['machine']) ─────
    def machine_face(self):
        self._chain.finish()
        samples=self._chain.samples
        broken=[s for s in samples if s.get('origin')=='firmware-natural'
                and any(s.get(stage) is False for stage in CHAIN_STAGES)]
        window_covered=bool(self._dispatch_handler_steps)
        if broken:
            verdict='chain_broken'
        elif samples:
            verdict='samples'
        elif self._w_park is not None:
            # BZ 判据:回调表 [0x126F6C] 非空+队列空+无样本=queue_empty_no_source
            # (链路通、窗口内无固件自然事件源);表空=None(checker missing,不猜)。
            table=self._callback_table_word()
            verdict=('queue_empty_no_source'
                     if isinstance(table,int) and table!=0 else None)
        else:
            verdict=None
        observations={'samples':samples,
                      's18_preset_samples':[[s['event'],
                                             f"first-pass posted at model step "
                                             f"{s.get('first_post_step')}"]
                                            for s in samples
                                            if self._w_park is not None
                                            and isinstance(s.get('first_post_step'),int)
                                            and s['first_post_step']<=self._w_park],
                      'verdict':verdict,
                      'window_covered':window_covered,
                      'last_dispatch_step':(self._dispatch_handler_steps[-1]
                                            if self._dispatch_handler_steps else None)}
        if broken:
            observations['broken_evidence']=[
                {'event':s['event'],'false_stages':[stage for stage in CHAIN_STAGES
                                                    if s.get(stage) is False]}
                for s in broken]
        return {'watchpoint_hits':{**{hex(pc):0 for pc in WATCHPOINT_PCS
                                      if hex(pc) not in self._watch},
                                   **dict(sorted(self._watch.items()))},
                # ↑ 锚定 PC 面(D1RUN 归因):WATCHPOINT_PCS 全集铺 0——「零执行」
                # 的机器可证表达=键在而值为 0(checker canary 判定语义);键缺会被
                # 误读为 MISSING(pending)。
                'idle_wait_park_log':self._park_log,
                'event_clock_engagements':self._engagements,
                'jiffies_observations':self._jiffies_observations,
                'message_chain_observations':observations}

    def project_delivery_faces(self,machine_report):
        """把观察器的交付步号/资格面注入机器 projection 的 deliveries(D1-3
        checker 消费 core_tick_entry_projection.deliveries[].{model_step,
        eligibility_note};观察器记录与机器 deliveries 按同序追加,zip 对齐,
        短的截断=错位不冒充)。返回注入后的 report(原 dict 原位修改)。"""
        projection=machine_report.get('core_tick_entry_projection')
        if not isinstance(projection,dict):return machine_report
        deliveries=projection.get('deliveries')
        if not isinstance(deliveries,list):return machine_report
        observed=[d for d in self._deliveries if d.get('irq')==3]
        for entry,record in zip(deliveries,observed):
            if isinstance(entry,dict):
                entry.setdefault('model_step',record.get('model_step'))
                if record.get('eligibility_note') is not None:
                    entry.setdefault('eligibility_note',record['eligibility_note'])
        return machine_report


def _subcode_of(sample):
    args=sample.get('post_args')
    if args and len(args)>=3 and isinstance(args[2],str):
        return int(args[2],16)
    return None


# ── PR-2 挂接注记(实施路在 probe 侧加的几行;本半边不动 probe 五文件) ──────────
# PR2_HOOKUP(probe_boot.explore,3 处):
#   1) 构造传参(irq_case d1_acceptance=True 时):
#        observer=D1WindowObserver(machine,hardware,d1_window_steps=d1_window_steps)
#      (dual_case 构造后、explore 调用前;detach 在 finally。)
#   2) 主循环尾(trace.append 之后、stop 检查之前,每圈):
#        if observer is not None:
#            observer.note_step(c,int(row['pc'],16),completed,stalled='stall' in row)
#            if observer.window_stop_request is not None:
#                stop=observer.window_stop_request;break
#   3) post_stop 回调(probe_irq_entry 场景行传 post_stop=):
#        def post_stop(machine,hardware,stopped_cpu):
#            face=observer.machine_face()
#            return {'d1_window':observer.finalize(stop),
#                    'machine':{**machine.report(),**face}}
# PR2_HOOKUP(check_d1_acceptance 正式验收入口,probe_irq_entry main 侧):
#   from check_d1_acceptance import run_all
#   d1_scenario_row['d1_window']['verdicts']={...};d1_gate!='pass'→sys.exit(1)
# (观察器不 import checker——verdicts 由该入口回填,单源纪律。)
