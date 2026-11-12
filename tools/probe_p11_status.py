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
    # projection and the core-tick chain wired too (core_tick_configuration
    # with the explicit disabled initial words (0,0), the CPU1-held projection,
    # SYS_DIV/P33-voltage/PLL1-CON3 configuration projections — all the same
    # switches and synthetic initial words the clock-scenario family or its
    # voltage variant uses), the prefix crosses the CPU1 core-tick CON store
    # 0xffe8ec = 0 (only the first half of the walker table entry executes; the
    # enable leaf never runs, so no counter behaviour is invented), the peer
    # PMU_CON0 release-flag read (the real code takes the not-released branch
    # and skips the IPI), the SYS_DIV divider RMW, the P3_PVDD1_AUTO read-RMW
    # (SDK p33_sfr.h P33_ACCESS(0x44)) and the PLL1 enable RMW. The independent
    # JL_L1P CON (0xff0500) idle-bit fixture (l1p_idle_projection, only 0xff0500,
    # never the two ICU CON status words) then serves the ROM DcuWaitIdle
    # (0x1fdb8 -> 0xbb20) bit31 poll — no cache maintenance precedes it in this
    # prefix, so idle is the natural no-operation-in-flight read — and the
    # independent two-core ICU CON idle fixture (icu_idle_projection, only
    # 0xffe400/0xffec00 bit31, never bit30 command-busy / enable-bit / region
    # registers) serves the ROM IcuWaitIdle (0x1fdb4 -> 0xc792) bit31 poll the
    # same way. After both cache-idle waits return, the RAM SFC suspend /
    # cache-drop helper 0x12e1cc (= flash 0x60c79a4) resumes with its primary
    # SFC suspend sequence: r0=0xfe0200 (SFC_BASE) then the CON bit31 poll read
    # (ifs r1<0 loop, waiting for the SFC busy bit to clear), then clear-bit0 /
    # BAUD=r4 / RMW mode field / |=1 enable. This is the same read-back shape
    # the reused SFCReadBus already models (CON read-back = last written word,
    # bit31=0 in this synchronous no-queued-transaction model, BAUD while
    # disabled arbitrary / while enabled only 0), so only scenario wiring is
    # needed — no new fixture. The SFC part is NOT a cache/ICU access: no bit30
    # command-busy or ICU CON enable-bit write is consumed, so the ICU idle
    # fixture stays scoped to the bit31 poll it serves. After the helper
    # returns, the PWM init loop 0x6034a70..0x6034a86 reads the four MCPWM
    # channel CON0 registers (0xfd2964/0xfd2974/0xfd2984/0xfd2994 = SDK br28.h
    # JL_MCPWM CH0..CH3_CON0) — the same arch_startup pwm_value_strict boundary.
    # These are proven-dead reads (the value is overwritten by the loop-head
    # mov.rr r0=r4 and the caller walker's ld.w r0=[r5+4] before any numeric
    # consumption), so the arch_startup opaque_pwm_read diagnostic is wired in
    # (reused, no new fixture) to cross all four without fabricating a value.
    wdt=run(upstream(),application(args.firmware),package_header(args.firmware),rom,p11_ready_wdt_cases=True)
    w=wdt[0]
    require(w['completed_steps']==109903 and w['stop_pc']=='0x6065616' and
            w['stop']['kind']=='unmapped_or_readonly_write' and
            w['stop']['address']=='0x19faf4' and w['stop']['value_bytes']=='00000000',
            'watchdog+EFUSE+oscillator+GPCNT+core-tick-chain projections plus the SFC read bus and the '
            'opaque PWM dead-read wiring did not cross the KEY pair, page reads, WLA trim, counter '
            'cleanup, core tick store, peer PMU read, SYS_DIV/P33/PLL1 configuration, the JL_L1P idle '
            'poll, the two-core ICU idle poll, the full SFC suspend/reconfiguration sequence, the four '
            'dead PWM reads and the held CPU1 core-tick enable leaf to the allocator PTE boundary')
    require(w['instruction_adapters']['proven_dead_pwm_configuration_read']==4,
            'the four dead PWM configuration reads were not proven opaque')
    require(w['peripherals']['assumptions']['pwm_configuration_reads'].startswith('R0 remains unknown'),
            'the opaque PWM read assumption label is missing')
    # The next stop is the allocator's hardware-PTE tag store: 0x6065616
    # `[r6+4] = r2` writes 0x19faf4 (the PTE for VA 0x49fff4 — "actual b3f5
    # maps VA49fff4->19faf4", CPU RAM backing ((tag&7fff)<<7)+offset), the same
    # byte-identical boundary arch_startup's heap_mapping_strict case pins. The
    # held CPU1 core-tick enable leaf (0x606617a) was crossed first: its
    # CNT=0/PRD=479999999/CON=1 stores all land under the opt-in held-core-tick
    # policy (core_tick_count_while_held=False + explicit clock progress (4,4)),
    # but the held CPU1 tick never counts (cnt stays 0, expirations 0 — no
    # counter/overflow/ACK behaviour invented). CPU1 never executes in this line.
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
    # WLA_CON8 final word 0x3e0c4191 = the clock_default_trim value 0x360c4191
    # plus bit27 re-asserted later inside the voltage-configuration segment —
    # byte-identical to the clock_voltage_configuration variant at the same
    # merged frontier (108,859 @ 0xbb28), a two-line cross-check.
    require(regs['0xfd1940']=='0x40000' and regs['0xfd4600']=='0x408929b3' and
            regs['0xfd1920']=='0x3e0c4191' and regs['0xfd1954']=='0x0',
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
    # The core-tick chain is wired with the same opt-ins and synthetic initial
    # words the clock-scenario family (or its voltage variant) uses: the
    # observed CPU1 tick CON=0 disable leaf 0x606616e executed (first half of
    # the walker table entry {0x606616e disable, 0x606617a enable}); after the
    # PWM loop the second-half walker pass reaches the enable leaf 0x606617a,
    # whose CNT=0, PRD=479999999 (the SDK per-core tick conversion from
    # [0x126540+0x2C0], byte-identical to the known CPU1 PRD) and CON=1 stores
    # all land under the opt-in held-core-tick policy (core_tick_count_while_held
    # =False: the held CPU1's tick does NOT count — counting while held would be
    # the counterfactual, held cores do not actually tick) plus explicit clock
    # progress (4,4) so the CON=1 enable store is a real recorded register
    # write. The held CPU1 tick then never advances (cnt stays 0, expirations 0
    # — no counter/overflow/ACK behaviour invented); the CPU0 tick receives a
    # real PRD=1/CNT=0 reset pair in the newly-crossed startup steps (recorded,
    # never fabricated). The peer PMU_CON0 read returned the held fixture 0 so
    # the real code chose the not-released branch itself. CPU1 never executed
    # in this branch line.
    require(any(x['kind']=='watched_pc_executed' and x['pc']=='0x6066174' for x in w['milestones']),
            'the CPU1 core-tick CON store at the old boundary did not execute')
    cpu0,cpu1=w['peripherals']['core_tick_configuration']
    require((cpu0['writes'],cpu1['writes'])==(2,4) and
            cpu0['con']==0 and cpu0['cnt']==0 and cpu0['prd']==1 and
            cpu1['con']==1 and cpu1['cnt']==0 and cpu1['prd']==479999999 and
            cpu0['initial_control_fixture']==cpu1['initial_control_fixture']==0 and
            cpu0['counter_projection_enabled'] and cpu1['counter_projection_enabled'] and
            cpu0['expirations']==cpu1['expirations']==0 and
            not cpu0['pending'] and not cpu1['pending'],
            'core tick configuration invented writes or counter behaviour')
    require(w['peripherals']['assumptions']['core_tick_count_while_cpu1_held'] is False,
            'the held CPU1 core-tick clock policy is not the honest do-not-count hypothesis')
    held=w['peripherals']['cpu1_held_projection']
    require(held['initial_control_fixture']==0 and not held['secondary_cpu_executed'] and
            held['kick_rmw_writes']==0,'CPU1 held fixture labels missing or CPU1 executed')
    require(assumptions.get('sys_div_configuration_projection') is True and
            'voltage_configuration_projection' in assumptions and
            'pll1_con3_configuration_projection' in assumptions,
            'SYS_DIV/P33-voltage/PLL1 configuration projections not labelled')
    # JL_L1P CON idle-bit independent fixture: the DcuWaitIdle poll really read
    # the explicit synthesized 0x80000000 (bit31=1=idle, the only bit the poll
    # consumes), no other bit is claimed as silicon, and CON writes stay
    # unmodeled.
    l1p=w['peripherals']['l1p_idle_projection']
    require(l1p['idle_word_fixture']=='0x80000000' and
            l1p['idle_is_no_operation_in_flight_not_maintenance_completion'] is True and
            l1p['other_bits_claimed_as_silicon'] is False and
            l1p['silicon_reset_value_verified'] is False and
            l1p['con_write_modeled'] is False,
            'JL_L1P CON idle fixture labels or synthesized word missing')
    # Two-core ICU CON idle-bit independent fixture: the IcuWaitIdle poll really
    # read the same explicit synthesized 0x80000000 on both 0xffe400 and
    # 0xffec00, bit30 command-busy / enable-bit / region registers stay
    # unmodeled, no other bit is claimed as silicon, and CON writes stay
    # blocked. The continuation then runs the SFC suspend/reconfiguration
    # sequence via the reused SFC read bus (a different domain, not a cache/ICU
    # access), and only then stops honestly on the first dead PWM read.
    icu=w['peripherals']['icu_idle_projection']
    require(icu['idle_word_fixture']=='0x80000000' and
            icu['bit30_command_busy_not_modeled'] is True and
            icu['idle_is_no_operation_in_flight_not_maintenance_completion'] is True and
            icu['other_bits_claimed_as_silicon'] is False and
            icu['silicon_reset_value_verified'] is False and
            icu['con_write_modeled'] is False and
            icu['sdk_bit31_evidence'].startswith('cache.h WAIT_ICACHE_IDLE'),
            'two-core ICU CON idle fixture labels or synthesized word missing')
    # The SFC suspend/cache-drop helper 0x12e1cc (= flash 0x60c79a4) runs its
    # primary SFC sequence through the reused SFCReadBus (the same model the
    # crc_bankb/arch_startup scenarios validate): CON bit31 poll reads the last
    # written word (bit31=0 in this synchronous model, so the poll exits at the
    # first read), then &=~1 disables, BAUD=r4(=1), the CON mode field
    # bits[20:24]=r5 low nibble(=3) is RMW'd in, and |=1 re-enables — the exact
    # final state arch_startup pins as (con=0x300001, baud=1, 4 writes).
    sfc=w['peripherals']['sfc_read_bus']
    require((sfc['con'],sfc['baud'],sfc['writes'],sfc['initial_control_fixture'])==(0x300001,1,4,1),
            'the reused SFC read bus did not run the suspend/reconfiguration sequence to the '
            'arch_startup-pinned final state')
    require(sfc['busy_projection']=='zero outstanding transactions; synchronous decoded-Flash memory',
            'SFC busy/read-back semantics label missing')
    report={'firmware_sha256':OFFICIAL_SHA,'rom_sha256':ROM_SHA,'simulator_commit':PIN,
            'device_accessed':False,'whole_h3s_boot_simulated':False,'p11_response_causality_proven':False,
            'suitable_for_flash_approval_alone':False,'conditional_status_regression_passed':True,
            'pmu_ready_response_projection_passed':True,'p11_ready_watchdog_passed':True,'scenarios':results,
            'ready_response_scenarios':ready,'ready_watchdog_scenarios':wdt}
    args.report.parent.mkdir(parents=True,exist_ok=True);args.report.write_text(json.dumps(report,indent=2)+'\n')


if __name__=='__main__':main()
