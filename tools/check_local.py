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
    parser.add_argument('--fetch-inputs',action='store_true',help='download hash-pinned firmware, simulator and reference SDK inputs if missing')
    parser.add_argument('--reproduce',action='store_true',help='also rebuild the three published firmware baselines offline')
    parser.add_argument('--android-build',action='store_true',help='also build the APK for a synthetic target; requires local SDK35/JDK')
    parser.add_argument('--rom',type=Path,help='also run ROM execution probes with an explicitly supplied private ROM file')
    # 分层回归（默认全量不变）：
    #   --units      只跑快速单测族（秒-分钟级；每个里程碑的单测已覆盖语义大头）。
    #   --only a,b   单测族 + 指定工具（按文件名匹配，如 probe_p11_status,probe_mmu；
    #                用于定向复核改动域，跳过全部其他 probe/analyze）。
    parser.add_argument('--units',action='store_true',help='run only the fast unit-test tier')
    parser.add_argument('--only',default='',help='unit tier plus only these named tools (comma-separated file names)')
    args=parser.parse_args()
    only={name.strip() for name in args.only.split(',') if name.strip()}

    def run(*command,env=None,tool=None):
        # 无 tool 标记 = 单测族（compileall/脚本 test/test_*.py/probe_pi32v2）。
        # tool 标记 = probe/analyze；分层模式下按 --units/--only 过滤。
        if args.units and tool is not None:return
        if only and tool is not None and tool not in only:return
        print('+ '+' '.join(str(part) for part in command),flush=True)
        subprocess.run(command,cwd=ROOT,env=env,check=True)

    if args.fetch_inputs:
        run(sys.executable,'tools/fetch_firmware.py','--variant','both')
        run(sys.executable,'tools/fetch_boot_reference.py','--hardware')
    run(sys.executable,'-m','compileall','-q','tools','-x','jl-misctools|build|reference-cache')
    run('bash','tools/android-query/test.sh')
    run('bash','tools/eq-toggle/test.sh')
    run(sys.executable,'tools/usb-recovery/test_recovery.py')
    run(sys.executable,'tools/usb-recovery/test_rom.py')
    run(sys.executable,'tools/test_br28_model.py')
    options=['--fetch-simulator'] if args.fetch_inputs else []
    run(sys.executable,'tools/probe_pi32v2.py',*options,'--report','analysis/local-checks/functions.json')
    run(sys.executable,'tools/test_boot_bus.py')
    run(sys.executable,'tools/test_architectural_models.py')
    run(sys.executable,'tools/test_task_returns.py')
    run(sys.executable,'tools/test_br28_machine.py')
    run(sys.executable,'tools/test_task_unknown.py')
    run(sys.executable,'tools/test_icfg.py')
    run(sys.executable,'tools/test_irq_entry.py')
    run(sys.executable,'tools/test_core_tick.py')
    run(sys.executable,'tools/probe_boot.py','--check-known-blockers','--report','analysis/local-checks/boot-prefix.json',tool='probe_boot.py')
    run(sys.executable,'tools/probe_boot_handoff.py','--report','analysis/local-checks/boot-handoff.json',tool='probe_boot_handoff.py')
    run(sys.executable,'tools/probe_boot_boundaries.py','--report','analysis/local-checks/boot-boundaries.json',tool='probe_boot_boundaries.py')
    run(sys.executable,'tools/analyze_qcy_irq.py','--report','analysis/local-checks/qcy-irq.json',tool='analyze_qcy_irq.py')
    run(sys.executable,'tools/analyze_p11.py','--report','analysis/local-checks/p11-resource.json',tool='analyze_p11.py')
    run(sys.executable,'tools/analyze_startup_chain.py','--report','analysis/local-checks/startup-chain.json',tool='analyze_startup_chain.py')
    run(sys.executable,'tools/analyze_cpu_context.py','--report','analysis/local-checks/cpu-context.json',tool='analyze_cpu_context.py')
    run(sys.executable,'tools/analyze_icfg_status.py','--report','analysis/local-checks/icfg-status.json',tool='analyze_icfg_status.py')
    run(sys.executable,'tools/analyze_image_resources.py','--report','analysis/local-checks/image-resources.json',tool='analyze_image_resources.py')
    run(sys.executable,'tools/probe_debug_startup.py','--report','analysis/local-checks/debug-startup.json',tool='probe_debug_startup.py')
    run(sys.executable,'tools/probe_event_clock.py','--report','analysis/local-checks/event-clock.json',tool='probe_event_clock.py')
    if args.rom:
        run(sys.executable,'tools/probe_rom.py','--rom',args.rom.resolve(),'--report','analysis/local-checks/rom-execution.json',tool='probe_rom.py')
        run(sys.executable,'tools/probe_hardware.py','--rom',args.rom.resolve(),'--report','analysis/local-checks/hardware-execution.json',tool='probe_hardware.py')
        run(sys.executable,'tools/probe_gpio_irq.py','--rom',args.rom.resolve(),'--report','analysis/local-checks/gpio-irq-execution.json',tool='probe_gpio_irq.py')
        run(sys.executable,'tools/probe_mmu.py','--rom',args.rom.resolve(),'--report','analysis/local-checks/mmu-execution.json',tool='probe_mmu.py')
        run(sys.executable,'tools/probe_p11_status.py','--rom',args.rom.resolve(),'--report','analysis/local-checks/p11-status.json',tool='probe_p11_status.py')
        run(sys.executable,'tools/probe_watchdog.py','--rom',args.rom.resolve(),'--report','analysis/local-checks/watchdog-execution.json',tool='probe_watchdog.py')
        run(sys.executable,'tools/probe_clock_startup.py','--rom',args.rom.resolve(),'--report','analysis/local-checks/clock-startup.json',tool='probe_clock_startup.py')
        run(sys.executable,'tools/probe_arch_startup.py','--rom',args.rom.resolve(),'--report','analysis/local-checks/architecture-startup.json',tool='probe_arch_startup.py')
        run(sys.executable,'tools/probe_platform_startup.py','--rom',args.rom.resolve(),'--report','analysis/local-checks/platform-startup.json',tool='probe_platform_startup.py')
        run(sys.executable,'tools/analyze_cpu1_rom.py','--rom',args.rom.resolve(),'--report','analysis/local-checks/cpu1-rom.json',tool='analyze_cpu1_rom.py')
        run(sys.executable,'tools/analyze_usb_rom.py','--rom',args.rom.resolve(),'--report','analysis/local-checks/usb-rom-dispatch.json',tool='analyze_usb_rom.py')
        run(sys.executable,'tools/probe_dualcore.py','--rom',args.rom.resolve(),'--report','analysis/local-checks/dualcore-startup.json',tool='probe_dualcore.py')
        run(sys.executable,'tools/probe_icfg_startup.py','--rom',args.rom.resolve(),'--report','analysis/local-checks/icfg-startup.json',tool='probe_icfg_startup.py')
        run(sys.executable,'tools/probe_task_returns.py','--rom',args.rom.resolve(),'--report','analysis/local-checks/task-returns.json',tool='probe_task_returns.py')
        run(sys.executable,'tools/probe_irq_entry.py','--rom',args.rom.resolve(),'--report','analysis/local-checks/irq-entry.json',tool='probe_irq_entry.py')
        run(sys.executable,'tools/analyze_image_trace.py','--rom',args.rom.resolve(),'--self-check','--report','analysis/local-checks/image-trace-diff.json',tool='analyze_image_trace.py')
        run(sys.executable,'tools/probe_core_tick.py','--rom',args.rom.resolve(),'--report','analysis/local-checks/core-tick.json',tool='probe_core_tick.py')
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
