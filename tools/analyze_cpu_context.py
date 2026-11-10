#!/usr/bin/env python3
from pathlib import Path
import json,hashlib
import argparse
from types import SimpleNamespace
from probe_boot import application, ENTRY
from analyze_qcy_irq import source_address
from probe_pi32v2 import ROOT, CACHE, upstream, require
p=argparse.ArgumentParser(description='Static QCY CPU1/task frames and pinned AC791N stack observations; no CPU execution')
p.add_argument('--report',type=Path,required=True)
args=p.parse_args()
m=upstream();table=m.table.load()
firmware_app=application(ROOT/'firmware/QCY_H3S_7018F_1T2_release_note_V1.1.8_011.ufw')
def decode(pc):
 src=source_address(pc);i,r,n=m.codec.decode(table,firmware_app[src-ENTRY:src-ENTRY+6]);return i,r,n,None
s=SimpleNamespace(decode=decode,source_address=source_address,a=firmware_app,ENTRY=ENTRY,m=m)
root=CACHE
pins=json.loads((ROOT/'data/dependencies.json').read_text())['gpio_irq_semantics_reference']['source_sha256']
for name in ('hw/fm1-2026-10-24-app4/observations.json','hw/fm1-2026-10-24-app4/attempts.json'):
 require(hashlib.sha256((root/name).read_bytes()).hexdigest()==pins[name],'hardware observation source changed')
obs=json.loads((root/'hw/fm1-2026-10-24-app4/observations.json').read_text())
att=json.loads((root/'hw/fm1-2026-10-24-app4/attempts.json').read_text())
b=next(x for x in obs if x['run_id']==0x4005)
a=next(x for x in att if x['run_id']==0x4005)
rows=[]
for x in b['batches']:
 require(x['status']==2 and x['samples'][5:9]==[1,0x1c12000,0x1c14000,0x1c12000],'CPU1 observed endpoint changed')
 rows.append({'batch':x['batch'],'cnum_usp_ssp_sp':[hex(v) for v in x['samples'][5:9]]})
rti=json.loads((root/'hw/fm1-2026-10-24-v4/results.jsonl').read_text().splitlines()[290])
require(rti['id']=='rti' and len(rows)==9,'RTI record or batch count changed')
def region(lo,hi):
 result=[];pc=lo
 while pc<hi:
  i,r,n,d=s.decode(pc);addr=s.source_address(pc)
  result.append({'pc':hex(pc),'bytes':s.a[addr-s.ENTRY:addr-s.ENTRY+n].hex(),'text':s.m.codec.format(i,r)})
  pc+=n
 return result
out={
 'application_sha256':hashlib.sha256(s.a).hexdigest(),
 'isa_commit':'3608ed19bc21324f8ecfe79cc0018d05e43d9563',
 'scope':'Read-only static research. No CPU execution, no hardware access, no proposed full IRQ model.',
 'qcy_cpu1_trampoline':region(0x12cad4,0x12cb08),
 'qcy_first_task_restore':region(0x6066106,0x6066132),
 'qcy_frame_constructor':region(0x6065f96,0x6065fa6),
 'frame_layout':{'size':76,'base':'F = saved TCB stack pointer','slots':{'F+0':'RETI task entry','F+4':'RETS zero','F+8':'PSR zero','F+12':'R0 pdata','F+16..F+72':'R1..R15 not written by constructor'},'post_pop_sp':'F+76','negative':'No zero synthesis for unknown slots. Preserve unknown provenance or stop before consuming unknown bytes.'},
 'v4_rti':{'source':'hw/fm1-2026-10-24-v4/results.jsonl:291','batch':rti['batch'],'flags':rti['psr_out'],'sp_before':hex(rti['sp_in']),'sp_after':hex(rti['sp_out']),'icfg_before':hex(rti['out'][9]),'icfg_after':hex(rti['out'][8]),'limit':'ROM download runner context only; cannot infer every bootstrap/task/IRQ mode'},
 'app4_boot_verified':{'source':'hw/fm1-2026-10-24-app4/observations.json run_id=0x4005','worker_sha256':a['worker_sha256'],'worker_bytes':a['worker_size'],'source_snapshot':'hw/fm1-2026-10-24-app4/attempts.json run_id=0x4005 profile_sources[tools/hwtest/app/peer.S]','source_snapshot_sha256':hashlib.sha256(a['profile_sources']['tools/hwtest/app/peer.S'].encode()).hexdigest(),'setup':['cli','ICFG=0','USP=0x1c12000','SP=0x1c14000','SSP=0x1c14000','RETI=peer_main','RTI'],'before_sampling':['cli','ICFG=0'],'results':rows,'limit':'Endpoint establishes SP equals USP after whole sequence; ICFG/PSR not sampled immediately after RTI. AC791N measured, BR28 transfer not measured.'},
 'implementable_local_parts':[
  'Per-core CNUM read from explicit core identity, not hard-coded zero and not copied from CPU0.',
  'Aligned USP/SP/SSP visible writes; do not infer unspecified automatic bank synchronization.',
  'RETI immediate/general/pop writes retain bits26..1, as measured by v4.',
  'First-task software pops use ascending special-register number: RETI, RETS, PSR; GPR R0..R15.',
  'Execute actual OS task selection, helper, allocation, lock release and STI; STI changes observed bit9 only.',
  'Keep independent strict stops at CPU1 bootstrap RTI 0x12cb06 and task RTI 0x6066130 until mode-specific contract supplied.'
 ],
 'qcy_live_dependencies':[
  'CPU1 release actual write PMU_CON0 ffe8c0 bit0 at60660da; reads entry word19fef8 via unestablished ROM/hardware release path.',
  'QCY trampoline sets equal USP/SP124840 but no explicit SSP or ICFG before RTI; hidden entry mode and SSP remain unmeasured.',
  'At12cad4 actual code reads local ICFG, masks300, writes it, executes ROM IcuInitial1fdbc and DcuInitial1fdc0, then branches6034f38.',
  'CPU1 readiness must come from actual606602a store1265f5=1 and6066034 store19ff54=1, never from release side effect.',
  'Actual first task selected from OSTCBCur126c5c indexed by cnum. No force-app_core shortcut.'
 ],
 'negative_cases':[
  'Unknown CPU1 release protocol/entry mode stays strict; holding/releasing is not a reset API.',
  'Do not use AC791N C1_CON address/bits as BR28 PMU_CON0 implementation.',
  'Reject unknown RETI, invalid executable mapping, misaligned frame, and unmapped frame bytes.',
  'High/odd RETI write truncates; later fetch validates truncated address.',
  'Keep full state unchanged on a pre-instruction strict stop; no half-restored frame.',
  'Do not implement RTI as unconditional SP=USP: v4 preserves current SP in its tested context.',
  'Do not implement RTI as unconditional SP unchanged: app4 bootstrap endpoint differs from assigned pre-RTI SP.',
  'Do not restore a made-up old whole ICFG on RTI: v5 exception entry/exit disproves this general rule.',
  'After STI, eligible actual pending IRQ must still stop before unimplemented IRQ delivery.',
  'A general IRQ RTI, nested RTI, debug RTE, or unknown context cannot reuse a bootstrap-only conditional projection.'
 ]
}
args.report.parent.mkdir(parents=True,exist_ok=True)
args.report.write_text(json.dumps(out,indent=2)+'\n')
print('CPU1/first-task contract written; app4 nine raw records verified')
