#!/usr/bin/env python3
"""Conditional external PMU status exploration; never creates a P11 response."""
import argparse
import json
from pathlib import Path

from probe_boot import OFFICIAL_SHA, ROM_SHA, application, check_rom
from probe_boot_handoff import package_header
from probe_mmu import run
from probe_pi32v2 import PIN, ROOT, require, upstream


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--rom',type=Path,required=True)
    p.add_argument('--firmware',type=Path,default=ROOT/'firmware/QCY_H3S_7018F_1T2_release_note_V1.1.8_011.ufw')
    p.add_argument('--report',type=Path,required=True);args=p.parse_args()
    rom=args.rom.read_bytes();check_rom(rom)
    results=run(upstream(),application(args.firmware),package_header(args.firmware),rom,p11_status_cases=True)
    rows={r['name']:r for r in results}
    require(rows['p11_status_unknown']['stop']['kind']=='unknown_external_pmu_status','unknown PMU value fabricated')
    zero=rows['p11_status_zero'];one=rows['p11_status_one']
    require(zero['stop']['kind']=='instruction_budget' and
            zero['peripherals']['p11_access_counterfactual']['completed_sequences']>2 and
            zero['peripherals']['p11_access_counterfactual']['fixed_external_status_hypothesis']==0,
            'zero PMU status magically became ready')
    require(one['stop_pc']=='0x600a57c' and one['stop']['address']=='0xf2a804' and
            one['peripherals']['p11_access_counterfactual']['completed_sequences']==1,
            'status1 conditional boundary changed')
    report={'firmware_sha256':OFFICIAL_SHA,'rom_sha256':ROM_SHA,'simulator_commit':PIN,
            'device_accessed':False,'whole_h3s_boot_simulated':False,'p11_response_causality_proven':False,
            'suitable_for_flash_approval_alone':False,'conditional_status_regression_passed':True,'scenarios':results}
    args.report.parent.mkdir(parents=True,exist_ok=True);args.report.write_text(json.dumps(report,indent=2)+'\n')


if __name__=='__main__':main()
