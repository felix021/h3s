#!/usr/bin/env python3
"""Conditional external PMU status exploration plus the opt-in bit0 readiness response."""
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
    # Opt-in PMU_STA bit0 readiness response projection: base word stays the
    # explicit 0 fixture and only bit0 is forced to 1 after the CON2 request
    # sequence, so the p11_init prefix must cross its 0x606890c poll and stop
    # at exactly the same next strict boundary as the full-word status1 case.
    ready=run(upstream(),application(args.firmware),package_header(args.firmware),rom,p11_ready_cases=True)
    r=ready[0]
    require(r['completed_steps']==one['completed_steps'] and r['stop']==one['stop'] and
            r['stop_pc']=='0x600a57c','PMU ready response did not cross the p11_init poll to the same boundary')
    projection=r['peripherals']['p11_access_counterfactual']
    require(projection['fixed_external_status_hypothesis']==0 and
            projection['status_polls']==1 and
            projection['pmu_ready_response_projection']['bit0_set_mechanism_proven'] is False and
            projection['pmu_ready_response_projection']['causal_link_to_pwr_con_bit5_assumed'] is False,
            'ready response projection labels or trigger evidence missing')
    # The 83,094 strict boundary writes value_bytes 9d158c04, the little-endian
    # form of 0x048c159d = keys[0] of the double KEY pair pinned from wdt.c.o
    # (app 0x600a570-0x600a582: the parallel pair stores the old r0 = first key
    # while its ALU half computes the second, then a plain store writes it), so
    # the existing watchdog projection already covers the observed shape and only
    # scenario wiring is needed to cross it.
    require(int.from_bytes(bytes.fromhex(one['stop']['value_bytes']),'little')==0x048c159d,
            'strict boundary word is not the pinned first watchdog key')
    # Opt-in watchdog projection stacked on the ready response: the same prefix
    # must cross the KEY pair and the SDK CON command sequence (wdt_init at
    # 0x6035180 really returns) and the EFUSE CON0 page-select write where the
    # watchdog scenarios stop; the explicit synthetic EFUSE fixture (same shape
    # as the clock scenarios, never an inferred erased device) lets the real
    # page reader run to completion (16 data reads assembling the four
    # page-cache words). With the oscillator/WLA configuration projection also
    # wired (the same switches and synthetic fixtures as the clock-scenario
    # family: WLA_CON8/CON21 initial words, the OSC_STATUS 0xfd1978 read-only
    # word and the LSB timer rate the real delay loops consume), the prefix
    # crosses the whole conditional oscillator read at 0x60351d2 and the
    # XOSC/PLL trim segment behind it. With the GPCNT counter-cleanup
    # projection wired too (the same switch the clock-scenario family uses),
    # the real disable+clear RMW pair on GPCNT_CON executes, stopping at the
    # next independent strict boundary: the CPU1 core-tick CON full-word store
    # 0xffe8ec = 0, a domain only the clock-scenario core_tick_configuration
    # models (their own frontier crosses the same access and continues to the
    # P33 voltage read 0xf2c110).
    wdt=run(upstream(),application(args.firmware),package_header(args.firmware),rom,p11_ready_wdt_cases=True)
    w=wdt[0]
    require(w['completed_steps']==108426 and w['stop_pc']=='0x6066174' and
            w['stop']['kind']=='unmapped_or_readonly_write' and w['stop']['address']=='0xffe8ec',
            'watchdog+EFUSE+oscillator+GPCNT projections did not cross the KEY pair, page reads, WLA trim '
            'and the counter cleanup to the core-tick boundary')
    wp=w['peripherals']['watchdog_projection']
    require(wp['con']==0x2e and wp['command_values']==[0,0,0,14,46] and
            list(wp['assumptions']['nominal_units_per_model_step'])==[0,1],
            'watchdog command sequence differs from the SDK projection scenarios')
    ep=w['peripherals']['efuse_read_projection']
    require(ep['completed_data_reads']==16 and ep['selected_page']==3 and ep['selected_byte']==4 and
            ep['words_fixture']==['0xffffffff']*4 and ep['device_otp_measured'] is False,
            'real EFUSE page reader did not consume the synthetic fixture honestly')
    for page in range(4):
        require(w['memory_snapshots'][f'efuse_page{page}']['bytes_with_unknowns']=='ffffffff',
                'real guest page-cache word assembly differs from the synthetic fixture')
    p11w=w['peripherals']['p11_access_counterfactual']
    require(p11w['fixed_external_status_hypothesis']==0 and p11w['status_polls']==1 and
            p11w['pmu_ready_response_projection']['bit0_set_mechanism_proven'] is False,
            'ready response labels missing in the watchdog scenario')
    require(any(x['kind']=='watched_pc_executed' and x['pc']=='0x603518c' for x in w['milestones']),
            'wdt_init did not really return to caller')
    # The oscillator projection is the same opt-in the clock-scenario family
    # uses: the WLA conditional read entry at 0x60351cc (the old 84,002 strict
    # stop) really executed, the real XOSC/PLL trim branches landed on the same
    # values as clock_default_trim (OTP_DEFAULT synthetic words), and the
    # status-word/LSB-rate fixtures stay explicit synthetic inputs.
    require(any(x['kind']=='watched_pc_executed' and x['pc']=='0x60351cc' for x in w['milestones']),
            'the conditional WLA_CON8 read at the old boundary did not execute')
    regs=w['peripherals']['registers']
    require(regs['0xfd1940']=='0x40000' and regs['0xfd4600']=='0x408929b3' and
            regs['0xfd1920']=='0x360c4191' and regs['0xfd1954']=='0x0',
            'real XOSC/PLL trim branches differ from the clock-scenario values')
    assumptions=w['peripherals']['assumptions']
    require(assumptions['oscillator_status_word_fixture']==16 and
            assumptions['timer_lsb_ticks_per_step']==4 and
            assumptions['initial_registers']['0xfd1920']=='0x0',
            'oscillator fixtures are not the explicit clock-scenario synthetic inputs')
    # The GPCNT cleanup projection is the same opt-in the clock-scenario family
    # uses: exactly the observed disable+clear RMW pair (0x606951a &=
    # 0xFFFFFFFE then 0x606951e |= 0x40) executed with write-only bit
    # bookkeeping, same final partial word as clock_default_trim, unknown
    # neighbouring bits never fabricated (readback stays strict).
    gpcnt=w['peripherals']['gpcnt_cleanup_projection']
    require(gpcnt['known_mask']=='0x81' and gpcnt['known_value']=='0x0' and
            gpcnt['disable_writes']==gpcnt['clear_writes']==1 and
            w['instruction_adapters']['gpcnt_unknown_preserving_cleanup_rmw']==2,
            'GPCNT cleanup did not record exactly the observed disable+clear pair')
    report={'firmware_sha256':OFFICIAL_SHA,'rom_sha256':ROM_SHA,'simulator_commit':PIN,
            'device_accessed':False,'whole_h3s_boot_simulated':False,'p11_response_causality_proven':False,
            'suitable_for_flash_approval_alone':False,'conditional_status_regression_passed':True,
            'pmu_ready_response_projection_passed':True,'p11_ready_watchdog_passed':True,'scenarios':results,
            'ready_response_scenarios':ready,'ready_watchdog_scenarios':wdt}
    args.report.parent.mkdir(parents=True,exist_ok=True);args.report.write_text(json.dumps(report,indent=2)+'\n')


if __name__=='__main__':main()
