#!/usr/bin/env python3
"""Real CPU0 platform/OS startup to the strict CPU1 release boundary.

Explicit virtual port/reset/wake inputs; no ready injection or device access.
"""
import argparse
import json
from pathlib import Path

from br28_model import SDTAP_CON, PSRAM_CON0, IOMAP_CON0
from probe_arch_startup import architecture_case, candidate_view, REPAIRED_SHA, FAILED_SHA, REGIONS
from probe_boot import application, check_rom, OFFICIAL_SHA, APP_SHA, ROM_SHA
from probe_boot_handoff import package_header
from probe_pi32v2 import ROOT, PIN, upstream, require

POR_HISTORY={0xfd0004:0x80,0xfd0000:2,0xf2a400:0x80,0xf2a404:1,0xf2c0c0:0x80,0xf2c048:1}
NO_WAKE={0xf2a60c:0,0xf2c158:0,0xf2c15c:0,0xf2c194:0,0xf2c198:0,0xf2c2a0:0,0xf2d2a0:0}
SNAPSHOTS=(('cpu1_entry',0x19fef8,4),('cpu1_ready',0x1265f5,1),
           ('app_core_frame_head',0x19f7b0,16),('app_core_frame_unknown_gprs',0x19f7c0,60))
WATCH=[0x060352d8,0x060352de,0x060352e4,0x060352ea,0x060352f8,0x06035334,
       0x06035338,0x06035350,0x06035356,0x06065f9e,0x060660c8]


def platform_case(module,app,header,rom,name,*,options=None,iomap=0,stop=None,readonly=(),machine=None,diagnostic_options=None,
                  extra_regions=(),extra_snapshots=(),budget=250000,post_stop=None):
    parameters={'port_initialization_projection':True,'port_input_words':{SDTAP_CON:0,PSRAM_CON0:0},
        'initial_usb_io_word':0,'reset_history_projection':True,'reset_history_words':{**POR_HISTORY,**NO_WAKE},
        'debug_execution_projection':True,'initial_debug_controls':(0,None),
        'mpu_configuration_projection':True,'initial_mpu_write_gate':0}
    parameters.update(options or {})
    diagnostics={'exact_doubleword_stores':True,'watch_pcs':WATCH,'watch_registers':[0,1,2,4],
                 'watch_ram_after_pc':{0x06068a1e:[('port_keep',0x1237fe,14)]}}
    if stop is not None:diagnostics['stop_before_pc']=stop
    diagnostics.update(diagnostic_options or {})
    return architecture_case(module,app,header,rom,name,options=parameters,registers={IOMAP_CON0:iomap},
        diagnostic_options=diagnostics,snapshots=SNAPSHOTS+tuple(extra_snapshots),readonly=readonly,machine=machine,
        regions=REGIONS+tuple(extra_regions),budget=budget,post_stop=post_stop)


def run(module,app,header,rom):
    port_stop=0x060352f0
    cases=[('port_sdtap_unknown',{'port_input_words':{}},0,None),
        ('port_psram_unknown',{'port_input_words':{SDTAP_CON:0}},0,None),
        ('port_iomap_unknown',{},None,None),
        ('port_usb_io_unknown',{'initial_usb_io_word':None},0,None),
        ('port_default',{},0,port_stop),
        ('port_debug_usb',{'port_input_words':{SDTAP_CON:1,PSRAM_CON0:0},'initial_usb_io_word':None},0x20000,port_stop),
        ('port_psram_active',{'port_input_words':{SDTAP_CON:0,PSRAM_CON0:2}},0,port_stop),
        ('port_spi_alternate',{},0x10000,port_stop),
        ('reset_history_unknown',{'reset_history_words':{}},0,None),
        ('reset_source_unknown',{'reset_history_words':{0xfd0004:0x80}},0,None),
        ('wake_history_unknown',{'reset_history_words':POR_HISTORY},0,None),
        ('wake_pending_unknown',{'reset_history_words':{**POR_HISTORY,0xf2a60c:0}},0,None),
        ('reset_por_no_wake',{},0,0x06035334),
        ('debug_initial_unknown',{'initial_debug_controls':None},0,None),
        ('mpu_write_gate_unknown',{'initial_mpu_write_gate':None},0,None),
        ('cpu1_release_boundary',{},0,None)]
    return [platform_case(module,app,header,rom,name,options=opts,iomap=iomap,stop=stop)
            for name,opts,iomap,stop in cases]


def check_release(r,upper=0x060c3338):
    require(r['completed_steps']==127239 and r['stop_pc']=='0x60660da' and
            r['stop']['kind']=='cpu1_release_not_implemented','CPU1 release prefix changed or stopped early')
    pcs={m['pc'] for m in r['milestones'] if m['kind']=='watched_pc_executed'}
    require({hex(p) for p in WATCH if p!=0x060352f8}<=pcs,'real platform/task/frame calls missing')
    snap=r['memory_snapshots']
    require(snap['cpu1_entry']['bytes_with_unknowns']=='f4ca1200' and snap['cpu1_ready']['bytes_with_unknowns']=='00',
            'CPU1 trampoline not written or readiness injected')
    require(snap['app_core_frame_head']['bytes_with_unknowns']=='28200106000000000000000000000000' and
            snap['app_core_frame_unknown_gprs']['initialized_bytes']==0,'task frame changed or unknown GPRs zero filled')
    guard=r['peripherals']['debug_execution_projection']
    require(guard['checked_executing_fetches']==254480 and guard['violation'] is None and
            tuple(guard['initial_control_fixtures'])==(0,None),'executing PC guard was bypassed or initial state inferred')
    require(r['instruction_adapters']['proven_dead_p33_read']==2 and
            r['instruction_adapters']['proven_dead_pwm_configuration_read']==4,'opaque value proofs were bypassed')
    task_returns=[m for m in r['milestones'] if m.get('pc')=='0x6035356' and m['kind']=='watched_pc_executed']
    require(len(task_returns)==1 and task_returns[0]['r0']==task_returns[0]['registers']['r0']=='0x0',
            'task_create failed or success evidence missing')
    debug=guard['registers']
    for address,value in {0xffe200:0xe000001f,0xffe218:upper,0xffe21c:0x6000100,
                          0xffe220:0x1344c6,0xffe224:0x12a640,0xffe228:0x20000,0xffe22c:4}.items():
        require(debug[hex(address)]==hex(value),'actual programmed debug range differs')
    mpu=r['peripherals']['mpu_configuration_projection']
    require(mpu['registers']=={'0xff0300':'0x60607','0xff0340':'0xa4a3','0xff0380':'0x12a640','0xff03c0':'0x1344c5'} and
            mpu['key_writes']==2 and mpu['write_gate']==0,'real MPU parse/configuration differs')


def check(report):
    rows={r['name']:r for r in report['scenarios']}
    for name,address in [('port_sdtap_unknown',SDTAP_CON),('port_psram_unknown',PSRAM_CON0),
                         ('port_iomap_unknown',IOMAP_CON0),('port_usb_io_unknown',0xfd6000),
                         ('reset_history_unknown',0xfd0004),('reset_source_unknown',0xfd0000),
                         ('wake_history_unknown',0xf2a60c),('wake_pending_unknown',0xf2c158)]:
        require(rows[name]['stop'].get('address')==hex(address),'unknown platform input inferred: '+name)
    expected={'port_default':(115221,'ffffffffffffe0ffffffffffffff'),
              'port_debug_usb':(114529,'ffffffffffffe0ffffffffffffff'),
              'port_psram_active':(115224,'ffffffffffffe0ffd8ffffffffff'),
              'port_spi_alternate':(115224,'ffffffffffffecffffffffffe7ff')}
    for name,(steps,mask) in expected.items():
        r=rows[name]
        require(r['completed_steps']==steps and r['stop_pc']=='0x60352f0' and
                r['stop']['kind']=='diagnostic_milestone_reached','port_init did not really return')
        masks=[m['bytes_with_unknowns'] for m in r['milestones'] if m['kind']=='watched_ram_after_pc']
        require(masks==[mask],'actual protected-port masks differ')
    require(rows['debug_initial_unknown']['completed_steps']==0 and
            rows['debug_initial_unknown']['stop']['kind']=='unknown_initial_pc_guard','initial PC guard fabricated')
    require(rows['mpu_write_gate_unknown']['stop']['kind']=='unknown_initial_mpu_write_gate','MPU acknowledgment fabricated')
    require(rows['reset_por_no_wake']['stop_pc']=='0x6035334' and
            rows['reset_por_no_wake']['stop']['kind']=='diagnostic_milestone_reached','reset/wake record did not return')
    check_release(rows['cpu1_release_boundary'])
    for r in report.get('candidate_prefixes',[]):check_release(r,0x060e31c5 if r['ufw_sha256']==REPAIRED_SHA else 0x060c3338)
    report['platform_prefix_checks_passed']=True


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--rom',type=Path,required=True)
    p.add_argument('--firmware',type=Path,default=ROOT/'firmware/QCY_H3S_7018F_1T2_release_note_V1.1.8_011.ufw')
    p.add_argument('--repaired-candidate',type=Path);p.add_argument('--failed-r2',type=Path)
    p.add_argument('--report',type=Path,required=True);args=p.parse_args()
    rom=args.rom.read_bytes();check_rom(rom);module=upstream()
    app=application(args.firmware);header=package_header(args.firmware)
    report={'firmware_sha256':OFFICIAL_SHA,'application_sha256':APP_SHA,'rom_sha256':ROM_SHA,'simulator_commit':PIN,
        'device_accessed':False,'whole_h3s_boot_simulated':False,'candidate_whole_boot_validated':False,
        'suitable_for_flash_approval_alone':False,'runtime_pc_range_projection_enforced':True,
        'mpu_cpu_dma_access_permissions_enforced':False,'scenarios':run(module,app,header,rom)}
    for name,path,digest in [('repaired_candidate',args.repaired_candidate,REPAIRED_SHA),('archived_failed_r2',args.failed_r2,FAILED_SHA)]:
        if path is None:continue
        candidate,readonly=candidate_view(path,digest,args.firmware)
        r=platform_case(module,candidate,header,rom,name,readonly=readonly);r['ufw_sha256']=digest
        report.setdefault('candidate_prefixes',[]).append(r)
    check(report);args.report.parent.mkdir(parents=True,exist_ok=True)
    args.report.write_text(json.dumps(report,indent=2)+'\n')


if __name__=='__main__':main()
