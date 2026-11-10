#!/usr/bin/env python3
"""Read-only, exact-official-application static startup anchors; no CPU execution."""
import argparse
import json
import struct
from pathlib import Path
from probe_boot import application,ENTRY,APP_SHA
from probe_pi32v2 import upstream,ROOT,PIN,require
from analyze_qcy_irq import source_address

def analyze(module,app):
    m=module;t=m.table.load();a=app
    def ins(pc):
     src=source_address(pc);i,r,n=m.codec.decode(t,a[src-ENTRY:src-ENTRY+6]);f=m.branch_field(i)
     d={'pc':hex(pc),'id':i.id,'fields':r}
     if f:d['target']=hex(pc+n+m.check.operands(i,r)[f])
     return d
    def call(pc,target):
     d=ins(pc);require(d['target']==hex(target),'call target changed');return d
    calls=[(0x6035188,0x6012b94),(0x603518e,0x6034fb0),(0x603519a,0x6034fb0),(0x60351a2,0x6034fb0),(0x60351aa,0x6034fb0),(0x60352d8,0x606536e),(0x60352de,0x6066134),(0x60352e4,0x6066c78),(0x6035334,0x6034e14),(0x6035350,0x6067242),(0x6035356,0x60652c8),(0x606534e,0x60660b8),(0x606535a,0x6064340),(0x606535e,0x60660e6),(0x6065364,0x6066106),(0x606611c,0x6066232)]
    frame=a[0x6066106-ENTRY:0x6066132-ENTRY]
    require(frame.startswith(bytes.fromhex('600041002000')) and frame.endswith(bytes.fromhex('a904d4e8ffff400061008100')),'first-task signature changed')
    row=struct.unpack_from('<IBBHHH',a,0x609d780-ENTRY);require(row==(0x60a1d11,1,0,768,512,0),'app_core task table changed')
    pcs=[0x12caf4,0x12cafa,0x12cb00,0x12cb06,0x12cad4,0x12cad8,0x12cadc,0x12cae0,0x12cae6,0x12caec,0x12caf2,0x60660b8,0x60660be,0x60660c4,0x60660ce,0x60660d0,0x60660d4,0x60660da,0x60660de,0x60660e2,0x6066024,0x606602a,0x606602e,0x6066034,0x6066116,0x6066120,0x6066122,0x6066126,0x6066128,0x606612c,0x606612e,0x6066130,0x6065f96,0x6065f98,0x6065f9a,0x6065f9c,0x6065f9e,0x6065fa2]
    report={'application_sha256':APP_SHA,'decoder_commit':PIN,'device_accessed':False,'execution_performed':False,'scope':'static exact-QCY call/address anchors, SDK labels are structural correspondence, not byte-match provenance','calls':[call(*x) for x in calls],'anchors':[ins(pc) for pc in pcs],
     'cpu1':{'enable_function':'0x60660b8','entry_pointer_address':'0x19fef8','entry':'0x12caf4','usp_sp':'0x124840','first_reti':'0x12cad4','first_rti':'0x12cb06','rom_calls':['0x1fdbc','0x1fdc0'],'cpu1_main':'0x6034f38','release_register':'0xffe8c0','release_bit':0,'run_notify':'0x6066018','run_flag':'0x1265f5','os_running_bitmap':'0x1265f4','first_wait_pc':'0x60660de','second_wait_pc':'0x6065354'},
     'first_task':{'entry':'0x6066106','current_tcb_array':'0x126c5c','prepare':'0x6066232','stack_from_tcb_offset':0,'sp_switch':'0x6066122','frame_bytes':76,'frame_words_low_to_high':['reti','rets','psr']+[f'r{i}' for i in range(16)],'sti':'0x606612e','rti':'0x6066130','frame_initializer':'0x6065f96','initializer_known_words':{'0':'task PC','4':0,'8':0,'12':'pdata'},'other_frame_words':'not initialized by OSTaskStkInit; do not infer zero','app_core':{'table':'0x609d780','name_pointer':'0x60a1d11','entry':'0x6012028','pdata':0,'priority':1,'affinity':0,'stack_words':768,'queue_bytes':512},'first_selected_tcb':'must execute scheduler, not force app_core'},
     'efuse':{'reader':'0x6034fb0','page_select':'0xf2c2c0','byte_control':'0xf2c2c4','byte_read':'0xf2c2c8','transaction':'page<<6; for i=0..3 write 0x82|(i<<2), delay50us, read low8 bits, write control0; little-endian accumulation','ram_page_words':{'0':'0x126860','1':'0x126864','2':'0x12686c','3':'0x126868'},'first_live_fields':[{'page':2,'bits':'15:12','sdk_name':'efuse_get_btvbg_xosc_trim','extract_pc':'0x60351ec','unknown_branch_pc':'0x60351fa','output':'0xfd1940','value':'nibble==15 ? 0x40000 : nibble<<15'},{'page':2,'bits':'19:16','sdk_name':'efuse_get_btvbg_syspll_trim','extract_pc':'0x6035286','unknown_branch_pc':'0x6035294','output':'0xfd4600','value':'nibble==15 ? 0x408929b0 : 0x8929b0|(nibble<<27)'}],'values_measured':False,'boot_handoff_equivalence_proven':False},
     'constraints':['No CPU1 ready injection','No RTI jump-only replacement','No zero filling unknown task-stack registers','No OTP all-zero/all-ones inference','No arbitrary MMIO RW fallback']}
    require(ins(0x0606891c)['fields']=={'n':6,'b':0} and
            ins(0x0606891e)['id']=='st.h.pin','port initialization REP changed')
    report['port_initialization']={
        'call':call(0x060352ea,0x06068914),
        'reference':'cpu.a/port.c.o/msys_io_init; structural correspondence',
        'reference_object_sha256':json.loads((ROOT/'data/dependencies.json').read_text())['hardware_reference_objects']['port.c.o']['sha256'],
        'anchors':[ins(pc) for pc in (0x06068918,0x0606891a,0x0606891c,0x0606891e,
                                     0x06068920,0x06068926,0x0606892e,0x06068932,0x06068934,
                                     0x06068a8a,0x06068b56)],
        'repeat':'seven halfword writes at SP+2..SP+14; old-base postincrement2; flags unchanged',
        'live_inputs':[
            {'address':'0xff0100','name':'JL_SDTAP.CON','bits':'0',
             'consumer':'when enabled, read IOMAP.CON0(fd6014)[18:17]; channel1 preserves USB DP/DM'},
            {'address':'0xf2c120','name':'P3_PINR_CON','bits':'0',
             'consumer':'when enabled, P3_PORT_SEL0(f2c300)[7:0] selects a live protected reset pin'},
            {'address':'0xfe0200','name':'JL_SFC.CON','bits':'11:8','consumer':'protect SPI pins'},
            {'address':'0xfe0300','name':'JL_PSRAM.PSRAM_CON0','bits':'1',
             'consumer':'when enabled protect pins64,65,66,69'}],
        'physical_initial_values_measured':False}
    # Correct exact PLL base constants from instruction literals rather than copied prose.
    report['efuse']['first_live_fields'][1]['literal_base']=ins(0x603528a)['fields']['n']
    report['efuse']['first_live_fields'][1]['literal_default']=ins(0x6035298)['fields']['n']
    return report

def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument("--firmware",type=Path,default=ROOT/"firmware/QCY_H3S_7018F_1T2_release_note_V1.1.8_011.ufw")
    p.add_argument("--report",type=Path,required=True)
    args=p.parse_args();report=analyze(upstream(),application(args.firmware))
    args.report.parent.mkdir(parents=True,exist_ok=True)
    args.report.write_text(json.dumps(report,indent=2)+"\n")
    print("Exact QCY startup anchors checked; CPU1 rendezvous/first-task RTI not executed.")

if __name__=="__main__":main()
