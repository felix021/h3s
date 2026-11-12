#!/usr/bin/env python3
"""暂停点2事件时钟最小契约的合成双核 probe(opt-in,默认关)。

契约全文见 docs/event-clock.md:全核 IDLE 时虚拟时间以已建模定时器计数为
唯一时钟源,推进到最近的已建模事件(core tick 溢出/已置位且合格的 pending
IRQ)再按 IRQ 资格经既有交付投影唤醒;没有可唤醒事件就诚实停。

诚实边界:当前真实固件前缀(前沿 289,849/300,187)在到达「全核同时 IDLE」
之前就停在别的边界,本 probe 因此用与 tools/test_br28_machine.py 同级的
合成双核机器端到端走 enter_idle_wait → 事件时钟推进 → IrqDeliveryRequest →
既有交付唤醒的完整链路(与 probe_boot 主循环的交付分发同一形状),不构成
D1「事件循环可服务」的证据;真实前缀撞上全核 IDLE 后的启用验证是剩余工作。
不访问任何设备,不构成 flash 审批依据。
"""
import argparse
import json
from pathlib import Path

from br28_machine import DualCoreMachine
from br28_model import BR28Model, CPU1_CONTROL, CORE_TICK_BASES
from probe_boot import Blocked, IrqDeliveryRequest
from probe_pi32v2 import require

VECTOR_SLOT=0x19fe0c  # IRQ3 向量槽(0x19fe00+3*4,既有交付投影读取)
TICK_HANDLER=0x60661a4


class ParkedCpu:
    """停泊在 IDLE 的最小 CPU 桩(单测同级,如实标注非真实固件上下文)。"""

    def __init__(self,pc,core_id):
        self.mem=_SlotMem({VECTOR_SLOT:TICK_HANDLER})
        self.pc=pc;self.reti=0;self.steps=0;self.core_id=core_id;self.flags=0
        self.sp_known=True;self.sp=0x4013f0;self.usp=None;self.ssp=0x123840
        self.last_explicit_ssp=0x123840
        self.bootstrap_context=None;self.after_then=None;self.repeat=None
        self.active_context=None
        self.icfg=0x300;self.icfg_known=True


class _SlotMem:
    def __init__(self,slots):self.slots=dict(slots)

    def read(self,address,length):
        require((address,length)==(VECTOR_SLOT,4),'unexpected vector-slot read')
        return self.slots[address].to_bytes(4,'little')


def hardware(core0_irq3_nibble,core1_irq3_nibble):
    h=BR28Model(cpu1_held_projection=True,initial_cpu1_control=0,
                initial_irq_config=[core0_irq3_nibble<<12]+[0]*31,initial_ipmask=0,
                core_tick_configuration=True,initial_core_tick_controls=(0,0),
                core_tick_progress=(1,1),core_tick_delivery_requests=True,
                software_irq_command_projection=True,
                initial_software_irq_pending=(0,0),software_irq_delivery_requests=True)
    h.enable_multicore([core1_irq3_nibble<<12]+[0]*31,0,lambda value:None)
    h.write(CPU1_CONTROL,1)  # 真实释放路径;irq125(word15)两核默认屏蔽
    h.core_icfg={0:0x300,1:0x300}
    return h


def arm_tick(h,core,cnt,prd):
    h.write(CORE_TICK_BASES[core]+8,prd)
    h.write(CORE_TICK_BASES[core]+4,cnt)
    h.write(CORE_TICK_BASES[core],1)


def parked_machine(h):
    m=DualCoreMachine(software_irq_entry=True,core_tick_entry=True,
                      idle_wait_projection=True,event_clock_projection=True)
    cpu0=ParkedCpu(0x6066318,0)
    cpu1=ParkedCpu(0x12c5ca,1)
    m.cores={0:cpu0,1:cpu1};m.hardware=h;m.idle_wait_cores={0}
    return m,cpu0,cpu1


def enter_and_deliver(m,cpu):
    """镜像 probe_boot 主循环:enter_idle_wait 抛出的交付请求走既有交付投影。"""
    try:
        m.enter_idle_wait(cpu)
        return None
    except IrqDeliveryRequest as request:
        if request.details['irq']==3:
            m.deliver_core_tick(request.details['core'])
        else:
            m.deliver_software_irq(m.cores[request.details['core']],request.details['irq'])
        return request.details


def scenario_nearest_tick_wake():
    h=hardware(core0_irq3_nibble=3,core1_irq3_nibble=0)
    m,cpu0,cpu1=parked_machine(h)
    arm_tick(h,0,4,5)   # 2 步后溢出,合格
    arm_tick(h,1,2,5)   # 4 步后才溢出,被屏蔽(不唤醒)
    before=h.counts['completed_model_steps']
    request=enter_and_deliver(m,cpu1)
    return {'name':'event_clock_nearest_tick_wake','request':request,
            'virtual_steps':h.counts['completed_model_steps']-before,
            'machine_advances':m.event_clock_advances,
            'idle_cores_after':sorted(m.idle_wait_cores),
            'tick_deliveries':[(d['core'],d['reti_from']) for d in m.core_tick_deliveries],
            'masked_core1_tick':h.core_ticks[1].report()}


def scenario_same_tick_order():
    h=hardware(core0_irq3_nibble=3,core1_irq3_nibble=3)
    m,cpu0,cpu1=parked_machine(h)
    arm_tick(h,0,4,5);arm_tick(h,1,4,5)  # 同刻溢出,两核都合格
    first=enter_and_deliver(m,cpu1)
    with_ctx=None
    try:
        h.advance(0x300)  # 下一虚拟步,core1 按同一扫描序请求交付
    except IrqDeliveryRequest as second:
        with_ctx=second.details
        m.deliver_core_tick(second.details['core'])
    return {'name':'event_clock_same_tick_core_order','first_request':first,
            'second_request':with_ctx,
            'delivery_order':[d['core'] for d in m.core_tick_deliveries],
            'idle_cores_after':sorted(m.idle_wait_cores)}


def scenario_masked_pending_inert():
    h=hardware(core0_irq3_nibble=3,core1_irq3_nibble=0)
    m,cpu0,cpu1=parked_machine(h)
    h.software_irq.command(0xffe9a0,2)  # 固件真实命令路径置位 core1 pending(irq125,屏蔽)
    arm_tick(h,0,3,5)
    request=enter_and_deliver(m,cpu1)
    return {'name':'event_clock_masked_pending_inert','request':request,
            'software_deliveries':len(m.software_irq_deliveries),
            'pending_core1_after':{'known':h.software_irq.pending[1].known,
                                   'value':h.software_irq.pending[1].value},
            'machine_advances':m.event_clock_advances}


def scenario_honest_stop():
    h=hardware(core0_irq3_nibble=3,core1_irq3_nibble=0)  # 不武装任何 tick
    m,cpu0,cpu1=parked_machine(h)
    stop=None
    try:
        m.enter_idle_wait(cpu1)
    except Blocked as e:
        stop=e.details
    h2=hardware(core0_irq3_nibble=3,core1_irq3_nibble=0)
    m2,_,cpu1b=parked_machine(h2)
    h2.software_irq.command(0xffe9a0,2)  # 只有被屏蔽的 pending:也不是事件
    stop2=None
    try:
        m2.enter_idle_wait(cpu1b)
    except Blocked as e:
        stop2=e.details
    return {'name':'event_clock_honest_stop','stop':stop,'advances':m.event_clock_advances,
            'masked_pending_only_stop':stop2,'masked_pending_only_advances':m2.event_clock_advances,
            'masked_pending_untouched':h2.software_irq.pending[1].value}


def check(report):
    rows={r['name']:r for r in report['scenarios']}
    nearest=rows['event_clock_nearest_tick_wake']
    require(nearest['request']=={'core':0,'irq':3},'nearest-event wake must be the eligible core0 tick')
    require(nearest['virtual_steps']==2 and nearest['machine_advances']==2,
            'time must advance exactly to the overflow point, no further')
    require(nearest['idle_cores_after']==[1],'only the woken core leaves IDLE')
    require(nearest['masked_core1_tick']['pending'] is False,'the masked peer tick must not have fired yet')
    order=rows['event_clock_same_tick_core_order']
    require(order['first_request']=={'core':0,'irq':3} and order['second_request']=={'core':1,'irq':3},
            'same-tick overflows must wake in core order')
    require(order['delivery_order']==[0,1] and order['idle_cores_after']==[],'both cores wake, core 0 first')
    pending=rows['event_clock_masked_pending_inert']
    require(pending['request']=={'core':0,'irq':3} and pending['software_deliveries']==0,
            'a masked pending IRQ must never wake a core')
    require(pending['pending_core1_after']=={'known':15,'value':2},
            'the masked pending bit must stay set and undelivered')
    require(pending['machine_advances']==3,'time must move past the masked pending to the next event')
    stop=rows['event_clock_honest_stop']
    require(stop['stop']['kind']=='all_cores_idle_waiting' and 'no modeled event' in stop['stop']['note']
            and stop['advances']==0,'no modeled event must stop honestly with zero advances')
    require(stop['masked_pending_only_stop']['kind']=='all_cores_idle_waiting'
            and stop['masked_pending_only_advances']==1 and stop['masked_pending_untouched']==2,
            'a masked pending alone is not an event (one probe step, honest stop, pending untouched)')


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--report',type=Path,required=True);args=p.parse_args()
    report={'synthetic_two_core_only':True,
            'real_firmware_prefix_reached_all_idle':False,
            'd1_event_loop_evidence':False,
            'device_accessed':False,'whole_h3s_boot_simulated':False,
            'suitable_for_flash_approval_alone':False,
            'contract':'docs/event-clock.md',
            'clock_source':'modeled timer counters only (two per-core core ticks, PRD+1); no wall-clock conversion',
            'wake_paths':'existing delivery projections only; no new event sources invented',
            'remaining_work':'enable and validate on the real prefix once it reaches an all-cores-IDLE point',
            'scenarios':[scenario_nearest_tick_wake(),scenario_same_tick_order(),
                         scenario_masked_pending_inert(),scenario_honest_stop()]}
    check(report);args.report.parent.mkdir(parents=True,exist_ok=True)
    args.report.write_text(json.dumps(report,indent=2)+'\n')


if __name__=='__main__':main()
