#!/usr/bin/env python3
"""Actual watchdog initialization under explicit PMU and timeout hypotheses."""
import argparse
import json
from pathlib import Path

from probe_boot import OFFICIAL_SHA, ROM_SHA, application, check_rom
from probe_boot_handoff import package_header
from probe_mmu import run
from probe_pi32v2 import PIN, ROOT, require, upstream


def check(report):
    rows={r['name']:r for r in report['scenarios']}
    for name,steps,pc in [('watchdog_strict',83094,'0x600a57c'),
                          ('watchdog_unknown_clock',83377,'0x6012bd8'),
                          ('watchdog_expires',83378,'0x6012bdc')]:
        require(rows[name]['completed_steps']==steps and rows[name]['stop_pc']==pc,'watchdog strict boundary changed')
    require(rows['watchdog_strict']['stop']['address']=='0xf2a804','strict KEY boundary changed')
    require(rows['watchdog_unknown_clock']['stop']['kind']=='watchdog_timeout_clock_unknown',
            'unknown timeout clock silently allowed enable')
    require(rows['watchdog_expires']['stop']['kind']=='watchdog_expired_delivery_not_implemented',
            'explicit fast clock did not expire')
    for name,steps in [('watchdog_stopped_clock',83417),('watchdog_virtual_clock',83417),('watchdog_cold_virtual',84455)]:
        r=rows[name];w=r['peripherals']['watchdog_projection']
        require(r['completed_steps']==steps and r['stop_pc']=='0x6034fba' and r['stop']['address']=='0xf2c2c0',
                'EFUSE boundary changed')
        require(w['con']==0x2e and w['command_values']==[0,0,0,14,46],
                'real CON initialization sequence, including unchanged writes, differs')
        require(any(x['kind']=='watched_pc_executed' and x['pc']=='0x603518c' for x in r['milestones']),
                'wdt_init did not really return to caller')
    report['watchdog_regression_passed']=True


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--rom',type=Path,required=True)
    p.add_argument('--firmware',type=Path,default=ROOT/'firmware/QCY_H3S_7018F_1T2_release_note_V1.1.8_011.ufw')
    p.add_argument('--report',type=Path,required=True);args=p.parse_args()
    rom=args.rom.read_bytes();check_rom(rom)
    report={'firmware_sha256':OFFICIAL_SHA,'rom_sha256':ROM_SHA,'simulator_commit':PIN,
            'device_accessed':False,'whole_h3s_boot_simulated':False,'p11_response_causality_proven':False,
            'watchdog_key_lifetime_or_clock_calibrated':False,'suitable_for_flash_approval_alone':False,
            'scenarios':run(upstream(),application(args.firmware),package_header(args.firmware),rom,watchdog_cases=True)}
    check(report);args.report.parent.mkdir(parents=True,exist_ok=True)
    args.report.write_text(json.dumps(report,indent=2)+'\n')


if __name__=='__main__':main()
