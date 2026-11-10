#!/usr/bin/env python3
"""Run repository checks locally. Never connects to a headset or dispatches CI."""
import argparse
import os
from pathlib import Path
import subprocess
import sys

ROOT=Path(__file__).resolve().parents[1]


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--fetch-inputs',action='store_true',help='download hash-pinned firmware and simulator if missing')
    parser.add_argument('--reproduce',action='store_true',help='also rebuild the three published firmware baselines offline')
    parser.add_argument('--android-build',action='store_true',help='also build the APK for a synthetic target; requires local SDK35/JDK')
    parser.add_argument('--rom',type=Path,help='also run ROM execution probes with an explicitly supplied private ROM file')
    args=parser.parse_args()

    def run(*command,env=None):
        print('+ '+' '.join(str(part) for part in command),flush=True)
        subprocess.run(command,cwd=ROOT,env=env,check=True)

    if args.fetch_inputs:
        run(sys.executable,'tools/fetch_firmware.py','--variant','both')
    run(sys.executable,'-m','compileall','-q','tools','-x','jl-misctools|build|reference-cache')
    run('bash','tools/android-query/test.sh')
    run('bash','tools/eq-toggle/test.sh')
    run(sys.executable,'tools/usb-recovery/test_recovery.py')
    run(sys.executable,'tools/usb-recovery/test_rom.py')
    run(sys.executable,'tools/test_br28_model.py')
    options=['--fetch-simulator'] if args.fetch_inputs else []
    run(sys.executable,'tools/probe_pi32v2.py',*options,'--report','analysis/local-checks/functions.json')
    run(sys.executable,'tools/test_boot_bus.py')
    run(sys.executable,'tools/probe_boot.py','--check-known-blockers','--report','analysis/local-checks/boot-prefix.json')
    run(sys.executable,'tools/probe_boot_handoff.py','--report','analysis/local-checks/boot-handoff.json')
    run(sys.executable,'tools/probe_boot_boundaries.py','--report','analysis/local-checks/boot-boundaries.json')
    run(sys.executable,'tools/analyze_qcy_irq.py','--report','analysis/local-checks/qcy-irq.json')
    run(sys.executable,'tools/analyze_p11.py','--report','analysis/local-checks/p11-resource.json')
    run(sys.executable,'tools/analyze_startup_chain.py','--report','analysis/local-checks/startup-chain.json')
    run(sys.executable,'tools/analyze_cpu_context.py','--report','analysis/local-checks/cpu-context.json')
    run(sys.executable,'tools/probe_debug_startup.py','--report','analysis/local-checks/debug-startup.json')
    if args.rom:
        run(sys.executable,'tools/probe_rom.py','--rom',args.rom.resolve(),'--report','analysis/local-checks/rom-execution.json')
        run(sys.executable,'tools/probe_hardware.py','--rom',args.rom.resolve(),'--report','analysis/local-checks/hardware-execution.json')
        run(sys.executable,'tools/probe_gpio_irq.py','--rom',args.rom.resolve(),'--report','analysis/local-checks/gpio-irq-execution.json')
        run(sys.executable,'tools/probe_mmu.py','--rom',args.rom.resolve(),'--report','analysis/local-checks/mmu-execution.json')
        run(sys.executable,'tools/probe_p11_status.py','--rom',args.rom.resolve(),'--report','analysis/local-checks/p11-status.json')
        run(sys.executable,'tools/probe_watchdog.py','--rom',args.rom.resolve(),'--report','analysis/local-checks/watchdog-execution.json')
        run(sys.executable,'tools/probe_clock_startup.py','--rom',args.rom.resolve(),'--report','analysis/local-checks/clock-startup.json')
        run(sys.executable,'tools/probe_arch_startup.py','--rom',args.rom.resolve(),'--report','analysis/local-checks/architecture-startup.json')
        run(sys.executable,'tools/probe_platform_startup.py','--rom',args.rom.resolve(),'--report','analysis/local-checks/platform-startup.json')
    if args.reproduce:
        run(sys.executable,'tools/check_reproduction.py','--output','analysis/local-checks/reproduction.json')
    if args.android_build:
        run(sys.executable,'tools/bootstrap.py','--android')
        target_env={**os.environ,'H3S_BLE_ADDRESS':'02:00:00:00:00:01'}
        run('bash','tools/android-query/build.sh',env=target_env)
    run('git','diff','--check')
    print('Local checks passed. No hardware was accessed.',flush=True)


if __name__=='__main__':
    main()
