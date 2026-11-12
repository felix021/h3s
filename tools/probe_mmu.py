#!/usr/bin/env python3
"""Bounded real MMU initialization replay; P11 handshake remains a strict stop."""
import argparse
import hashlib
import json
import struct
from pathlib import Path

from br28_model import BR28Model, CLOCK0, PLL0, PLL1_CON3, SYS_DIV, TIMER0, VOLTAGE_CONFIG
from probe_boot import APP_SHA, OFFICIAL_SHA, ROM_SHA, STACK_TOP, application, check_rom, explore
from probe_boot_boundaries import UPDATE, update_block
from probe_boot_handoff import HANDOFF, SENTINEL_A, package_header, profile
from probe_clock_startup import OTP_CACHE, OTP_DEFAULT
from probe_pi32v2 import PIN, ROOT, require, upstream


def run(module,app,header,rom,*,p11_status_cases=False,watchdog_cases=False,p11_ready_cases=False,
        p11_ready_wdt_cases=False):
    require(len({bool(v) for v in (p11_status_cases,watchdog_cases,p11_ready_cases,p11_ready_wdt_cases) if v})<=1,
            'independent scenario groups required')
    results=[]
    cases=[('mmu_unknown_control',False,None,True),
                               ('mmu_unmapped_table',False,0,False),
                               ('warm_mmu_initialized',False,0,True),
                               ('cold_mmu_initialized',True,0,True)]
    if p11_status_cases:cases=[(name,False,0,True) for name in ('p11_status_unknown','p11_status_zero','p11_status_one')]
    if p11_ready_cases:cases=[('p11_ready_response',False,0,True)]
    if p11_ready_wdt_cases:cases=[('p11_ready_watchdog',False,0,True)]
    if watchdog_cases:
        cases=[(name,name=='watchdog_cold_virtual',0,True) for name in ('watchdog_strict','watchdog_unknown_clock',
               'watchdog_stopped_clock','watchdog_virtual_clock','watchdog_expires','watchdog_cold_virtual')]
    for name,cold,con,table in cases:
        h=profile(header,SENTINEL_A)
        h['description']['phase']='continuous application entry with functional MMU configuration'
        if p11_status_cases:h['description']['phase']='conditional startup with fixed external PMU status hypothesis; no P11 response model'
        if p11_ready_cases:h['description']['phase']='conditional startup with base PMU status0 plus the opt-in PMU_STA bit0 readiness response projection'
        if p11_ready_wdt_cases:h['description']['phase']=('conditional startup with base PMU status0 plus the opt-in PMU_STA bit0 readiness '
                                                         'response, SDK watchdog command projections and the explicit synthetic EFUSE '
                                                         'read fixture')
        if watchdog_cases:h['description']['phase']='conditional PMU=1 startup with optional SDK watchdog command/nominal-time projection'
        h['regions'] += [(UPDATE,UPDATE+128,'retained_update_parameters'),(0x19fd68,0x19fda4,'rom_mask_context')]
        h['seeds'].append((UPDATE+8,update_block('empty')[0]))
        if table:h['regions'].append((0x120000,0x122800,'mmu_page_table'))
        h['snapshots'].append(('mmu_page_table',0x120000,0x2800))
        if p11_ready_wdt_cases:
            # EFUSE 页缓存字快照(与 clock 场景同一形状):验证真实页读取器逐字节组装结果。
            h['snapshots'] += [(f'efuse_page{page}',address,4) for page,address in enumerate(OTP_CACHE)]
        # Actual pmalloc descriptor and V_MEM object: values written by guest,
        # never used as initial seeds to make initialization appear successful.
        h['snapshots'] += [('physical_heap_descriptor',0x12953c,24),('virtual_heap_descriptor',0x126f14,24)]
        initial={CLOCK0:0,CLOCK0+4:0,CLOCK0+8:0,PLL0:0 if cold else 1}
        if p11_ready_wdt_cases:
            # Oscillator/WLA configuration per the clock-scenario family: the same
            # explicit synthetic initial words (WLA_CON8 0=oscillator off,
            # WLA_CON21 0, SYS_DIV 0 for the divider-field configuration RMW and
            # the P33/CMNG voltage words all 0 per the clock_voltage_configuration
            # variant) plus the OSC_STATUS word fixture and the LSB timer rate
            # the real delay loops consume. Never an inferred analog state.
            initial.update({**dict.fromkeys((0xfd1920,0xfd1954,SYS_DIV,PLL1_CON3,
                                            *VOLTAGE_CONFIG),0)})
        hardware=BR28Model(initial_registers=initial,
            initial_irq_config=[0]*32,initial_ipmask=0,reset_inputs_inactive=True,
            bus_lock_model=True,initial_lock_owner=None,gpio_model=True,mmu_model=True,initial_mmu_con=con,
            p11_access_probe=p11_status_cases or watchdog_cases or p11_ready_cases or p11_ready_wdt_cases,
            p11_pmu_ready_projection=p11_ready_cases or p11_ready_wdt_cases,
            p11_status=1 if watchdog_cases else {'p11_status_zero':0,'p11_status_one':1,
                                                 'p11_ready_response':0,'p11_ready_watchdog':0}.get(name),
            watchdog_projection=(watchdog_cases and name!='watchdog_strict') or p11_ready_wdt_cases,
            watchdog_progress={'watchdog_stopped_clock':(0,1),'watchdog_virtual_clock':(1,4000),
                               'watchdog_expires':(16384,1),'watchdog_cold_virtual':(1,4000),
                               'p11_ready_watchdog':(0,1)}.get(name),
            # EFUSE 读投影按 clock 场景先例接线:显式合成 fixture 字(0xffffffff,)*4,
            # 非推导的设备擦除态;只服务 p11_ready_watchdog 支线。
            efuse_read_projection=p11_ready_wdt_cases,efuse_words=OTP_DEFAULT if p11_ready_wdt_cases else None,
            # Oscillator/WLA 配置投影按 clock 场景先例接线(同款开关+fixture:
            # 五个 WLA RW 配置寄存器 + OSC_STATUS 0xfd1978 只读状态字 16 + LSB
            # 定时器速率 4 供真实 delay 循环消费);只服务 p11_ready_watchdog 支线。
            oscillator_configuration_projection=p11_ready_wdt_cases,
            oscillator_status_word=16 if p11_ready_wdt_cases else None,
            timer_lsb_ticks_per_step=4 if p11_ready_wdt_cases else None,
            # GPCNT 计数器清理投影按 clock 场景先例接线(同一开关:0xfd1100 只接受
            # 已观测的 disable &=0xFFFFFFFE / clear |=0x40 位 RMW,读回保持严格);
            # 只服务 p11_ready_watchdog 支线。
            gpcnt_cleanup_projection=p11_ready_wdt_cases,
            # Core tick 配置投影按 clock 场景先例接线(同一开关 + 同样的显式
            # 禁用初始字 (0,0)):初始化 walker 表项 0x60a89c8 = {0x606616e,
            # 0x606617a} 只调第一半,本前缀真实执行只有 CPU1 tick CON=0
            # disable 写(clock 族越过同一访问后 CPU1 tick writes=1);enable
            # (bit0=1) 无 progress 证据仍严格停。只服务 p11_ready_watchdog 支线。
            core_tick_configuration=p11_ready_wdt_cases,
            initial_core_tick_controls=(0,0) if p11_ready_wdt_cases else None,
            # CPU1 held 投影按 clock 场景先例接线(同一开关+同样的显式 held
            # 初始字 0):core tick walker 之后的下一个访问是 RAM 0x12b60a 跨核
            # 请求函数读对核 PMU_CON0 0xffe8c0 测释放位 bit0——CPU1 在本支线
            # 从未执行,held fixture 0 让真实代码自己走「未释放→跳过 IPI」
            # 分支(与 clock 族同一机制越过同一访问);释放效果不模拟。
            cpu1_held_projection=p11_ready_wdt_cases,
            initial_cpu1_control=0 if p11_ready_wdt_cases else None,
            # SYS_DIV 配置投影按 clock 场景先例接线(同一开关 + 同样的显式
            # 初始字 0):链上下一访问 0x60002a0 对 SYS_DIV 0xfd0014 做
            # &=0xFFFFFF00 分频字段配置 RMW(读回=初始字/后写值,与 clock 族
            # sys_div_initial_unknown 负例的严格形状一致);只服务本支线。
            clock_divider_projection=p11_ready_wdt_cases,
            # P33 电压配置投影按 clock_voltage_configuration 变体先例接线
            # (同一开关 + 同样的五寄存器全 0 显式合成初始字):链上下一访问
            # 0x60002d0/d2 读 P3_PVDD1_AUTO (SDK p33_sfr.h P33_ACCESS(0x44)
            # =0xf2c110) 两次,低字节 |0xEE 写回 + P3_ANA_CON6 族位段组装,
            # 全族都在投影域内;模拟电压/爬升/时序不建模;只服务本支线。
            voltage_configuration_projection=p11_ready_wdt_cases,
            # PLL1 CON3 配置投影按同一变体先例接线(同一开关 + 同样的
            # 0xfd470c 初始字 0):链上下一访问 0x60003ce 对 PLL1_CON3 做
            # |=1 enable RMW;模拟 PLL1 锁定/模拟特性不建模;只服务本支线。
            pll1_configuration_projection=p11_ready_wdt_cases,
            # JL_L1P CON(0xff0500)空闲位读回独立 fixture(独立开关,只覆盖
            # 0xff0500、不碰两核 ICU 状态字 0xffe400/0xffec00):ROM
            # DcuWaitIdle(0x1fdb8->0xbb20)的 bit31 轮询读在本前缀无任何前置
            # cache 维护写,「空闲」=无在途操作的自然读(非维护完成时序假设);
            # 合成字 0x80000000(bit31=1),其余位不声称硅值;CON 写无观测形状
            # 仍拒绝。只服务本支线,默认关 legacy 场景不变。
            l1p_idle_projection=p11_ready_wdt_cases,
            # 两核 ICU CON(0xffe400/0xffec00)空闲位读回独立 fixture(独立开关,
            # 只覆盖两核 ICU CON bit31、不碰 bit30 命令忙/使能位/region 寄存器):
            # ROM IcuWaitIdle(0x1fdb4->0xc792)对两核的 bit31 轮询读在本前缀无任何
            # 前置 ICU 命令/使能写,「空闲」=无在途操作的自然读(非维护完成时序
            # 假设);合成字 0x80000000(bit31=1=空闲),其余位不声称硅值;CON 写无
            # 观测形状仍拒绝。只服务本支线,默认关 legacy 场景不变。
            icu_idle_projection=p11_ready_wdt_cases)
        diag={'zero_rep':'skip_hypothesis','adapt_icfg':True,'icfg_initial':0x300,'irq_repeat_return':True,
              'update_repeat':True,'rom_exact_stores':True,'rom_memset_rep':True,'rom_zero_rep_skip':True,
              'visible_cli':True,'opaque_pin_read':True,'exact_parallel_stores':True,
              'watch_pcs':[0x060350dc,0x06035116]}
        if watchdog_cases or p11_ready_wdt_cases:diag['watch_pcs'].append(0x0603518c)
        if p11_ready_wdt_cases:diag['watch_pcs'].append(0x060351cc)  # WLA_CON8 条件读入口(旧支线停点)
        if p11_ready_wdt_cases:diag['watch_pcs'].append(0x06066174)  # CPU1 core tick CON=0 写(旧支线停点)
        r=explore(module,app,name=name,adapt_stack=True,adapt_store=True,r0=HANDOFF,rom=rom,handoff=h,
                  diagnostics=diag,hardware=hardware,startup_loops=True,
                  budget=85000 if p11_status_cases or p11_ready_cases else
                         (110000 if p11_ready_wdt_cases else 120000))
        results.append(r)
    h=profile(header,SENTINEL_A)
    regs=[0]*16;regs[0]=37;regs[1]=TIMER0
    hardware=BR28Model(source_ticks_per_step=0)
    if not p11_status_cases and not watchdog_cases and not p11_ready_cases and not p11_ready_wdt_cases:
        results.append(explore(module,app,name='parallel_store_reads_old_r0',rom=rom,handoff=h,hardware=hardware,
            diagnostics={'entry_pc':0xa30c,'initial_gprs':regs,'initial_sp':STACK_TOP,
                         'icfg_initial':0,'exact_parallel_stores':True},budget=1))
    for r in results:
        print(r['name'],r['completed_steps'],r['stop_pc'],r['stop'],flush=True)
        r['last_instructions']=r['last_instructions'][-8:]
        for row in r['last_instructions']:row.pop('bytes',None)
        if len(r['memory_events'])>8:r['memory_events']=r['memory_events'][:4]+r['memory_events'][-4:]
        r['memory_events_truncated']=sum(r['memory_event_counts'].values())>len(r['memory_events'])
    return results


def check(report):
    rows={r['name']:r for r in report['scenarios']}
    require(rows['mmu_unknown_control']['stop']['kind']=='uninitialized_mmu_register','unknown MMU control fabricated')
    require(rows['mmu_unmapped_table']['stop']['address']=='0x120000','unmapped page table was writable')
    for name,steps in [('warm_mmu_initialized',83050),('cold_mmu_initialized',84088)]:
        r=rows[name]
        require(r['completed_steps']==steps and r['stop_pc']=='0x60688f8' and
                r['stop']['address']=='0xf2af10','P11 boundary changed')
        require(r['peripherals']['mmu']['con']==1 and r['peripherals']['mmu']['table_begin']==0x120000 and
                r['peripherals']['mmu']['table_end_inclusive']==0x1227ff,'MMU configuration differs')
        snap=r['memory_snapshots']['mmu_page_table']
        require(snap['initialized_bytes']==10240 and snap['sha256']==hashlib.sha256(bytes(10240)).hexdigest(),
                'real ROM did not initialize entire page table')
        require(r['peripherals']['mmu']['walks']==0,'unexpected virtual access during initialization')
        descriptor=bytes.fromhex(r['memory_snapshots']['physical_heap_descriptor']['bytes_with_unknowns'])
        start,end,size,first,last=struct.unpack_from('<IIIHH',descriptor)
        require((start,end,size)==(0x134500,0x19fb80,0x6b680) and
                first==((start-0x100000)>>7) and last==((end-0x100000)>>7),
                'actual QCY allocator relative bitmap indices changed')
    r=rows['parallel_store_reads_old_r0']
    require(r['completed_steps']==1 and r['last_registers']['r'][0]=='0x1011' and
            r['peripherals']['timer0']['prd']==37,'parallel store read the new R0')
    report['mmu_initialization_regression_passed']=True


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--rom',type=Path,required=True)
    p.add_argument('--firmware',type=Path,default=ROOT/'firmware/QCY_H3S_7018F_1T2_release_note_V1.1.8_011.ufw')
    p.add_argument('--report',type=Path,required=True);args=p.parse_args()
    rom=args.rom.read_bytes();check_rom(rom)
    report={'format_version':1,'simulator_commit':PIN,'firmware_sha256':OFFICIAL_SHA,'application_sha256':APP_SHA,
            'rom_sha256':ROM_SHA,'device_accessed':False,'whole_h3s_boot_simulated':False,
            'real_guest_virtual_memory_allocation_validated':False,'suitable_for_flash_approval_alone':False,
            'scenarios':run(upstream(),application(args.firmware),package_header(args.firmware),rom)}
    check(report);args.report.parent.mkdir(parents=True,exist_ok=True)
    args.report.write_text(json.dumps(report,indent=2)+'\n')


if __name__=='__main__':main()
