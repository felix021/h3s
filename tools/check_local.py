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
    # 并发（--jobs N）：各项检查独立写各自 report、互不依赖，可安全并行；有先后
    #   依赖的下载/构建步骤仍先按序串行跑完；git diff --check 收尾在全部结束后。
    #   默认 1=串行、遇错即停；-jN 跑完全部再聚合报错（exit 1）。
    parser.add_argument('--units',action='store_true',help='run only the fast unit-test tier')
    parser.add_argument('--only',default='',help='unit tier plus only these named tools (comma-separated file names)')
    parser.add_argument('--preflight',action='store_true',help='additionally run the offline assertion preflight (tools/assert_preflight.py) over the standard local-checks reports; missing reports are skipped')
    parser.add_argument('--jobs',type=int,default=1,help='run checks with N parallel workers (default 1: serial, stop at first failure)')
    args=parser.parse_args()
    only={name.strip() for name in args.only.split(',') if name.strip()}

    prelude=[]  # 有先后依赖的步骤（输入下载、构建引导），任何模式下都先按序跑完。
    checks=[]   # 可并行步骤：各项写各自 report，互不依赖。
    def run(*command,env=None,tool=None,serial=False):
        # 无 tool 标记 = 单测族（compileall/脚本 test/test_*.py/probe_pi32v2）。
        # tool 标记 = probe/analyze；分层模式下按 --units/--only 过滤。
        if args.units and tool is not None:return
        if only and tool is not None and tool not in only:return
        (prelude if serial else checks).append((command,env))

    if args.fetch_inputs:
        run(sys.executable,'tools/fetch_firmware.py','--variant','both',serial=True)
        run(sys.executable,'tools/fetch_boot_reference.py','--hardware',serial=True)
    run(sys.executable,'-m','compileall','-q','tools','-x','jl-misctools|build|reference-cache')
    run('bash','tools/android-query/test.sh')
    run('bash','tools/eq-toggle/test.sh')
    run(sys.executable,'tools/usb-recovery/test_recovery.py')
    run(sys.executable,'tools/usb-recovery/test_rom.py')
    run(sys.executable,'tools/test_br28_model.py')
    options=['--fetch-simulator'] if args.fetch_inputs else []
    run(sys.executable,'tools/probe_pi32v2.py',*options,'--report','analysis/local-checks/functions.json')
    run(sys.executable,'tools/test_boot_bus.py')
    run(sys.executable,'tools/test_o3_readopt.py')
    run(sys.executable,'tools/test_architectural_models.py')
    run(sys.executable,'tools/test_task_returns.py')
    run(sys.executable,'tools/test_br28_machine.py')
    run(sys.executable,'tools/test_exec_memo.py')
    run(sys.executable,'tools/test_task_unknown.py')
    run(sys.executable,'tools/test_icfg.py')
    run(sys.executable,'tools/test_irq_entry.py')
    run(sys.executable,'tools/test_assert_preflight.py')
    run(sys.executable,'tools/test_image_trace_diff.py')
    run(sys.executable,'tools/test_core_tick.py')
    run(sys.executable,'tools/test_timer0_irq.py')
    run(sys.executable,'tools/test_audio_dma_delivery.py')  # D2AI：ADC DMA 交付投影（模型件+IRQ68 进入/RTI）
    run(sys.executable,'tools/test_src_sync_delivery.py')   # SRCSYNC5：SRC_SYNC 完成事件+IRQ89 交付投影（模型件+进入/RTI）
    run(sys.executable,'tools/test_jl_anc_registers.py')    # D2ANC：JL_ANC 44 字容器（INJ-05 配置面载体）
    run(sys.executable,'tools/test_d1_acceptance.py')
    run(sys.executable,'tools/test_d1_window_observer.py')
    run(sys.executable,'tools/test_pi32v2_asm.py')
    run(sys.executable,'tools/test_hearing_aid_payload.py')
    run(sys.executable,'tools/test_hearing_aid_close_payload.py')
    run(sys.executable,'tools/test_hearing_aid_close_image.py')
    run(sys.executable,'tools/test_hearing_aid_slotram_image.py')
    run(sys.executable,'tools/test_hearing_aid_key7_image.py')
    run(sys.executable,'tools/test_hearing_aid_mpo_image.py')
    run(sys.executable,'tools/test_hearing_aid_l3_mpo_payload.py')
    run(sys.executable,'tools/test_hearing_aid_l3_mpo_image.py')
    run(sys.executable,'tools/test_hearing_aid_l4_eq_payload.py')
    run(sys.executable,'tools/test_hearing_aid_l4_eq_image.py')
    run(sys.executable,'tools/test_hearing_aid_full_image.py')
    run(sys.executable,'tools/test_hearing_aid_lfix_image.py')
    run(sys.executable,'tools/test_hearing_aid_lfix2_image.py')
    run(sys.executable,'tools/test_r4_keyhook_payload.py')
    run(sys.executable,'tools/test_r4_keyhook_image.py')
    run(sys.executable,'tools/test_candidate_world_checks.py')
    run(sys.executable,'tools/test_acceptance_record.py')
    run(sys.executable,'tools/test_acceptance_dashboard.py')
    run(sys.executable,'tools/test_falsification.py')
    run(sys.executable,'tools/test_sim_bridge_server.py')   # SIMBRIDGE M1-a：bridge server 骨架（静态 fixture 状态源）
    run(sys.executable,'tools/test_sim_usb_device.py')      # SIMBRIDGE M2：USB MSC BOT 设备端（字节级红绿双向）
    run(sys.executable,'tools/test_sim_flash_fixture.py')   # SIMBRIDGE M2：2MiB raw fixture 构造（真实产档缺资产 skip）
    run(sys.executable,'tools/test_sim_restore_planner.py') # SIMBRIDGE M3：刷机扇区计划器（diff→sectors/写完模拟/CLI）
    run(sys.executable,'tools/test_sim_m3_verdicts.py','--record','analysis/local-checks/sim-m3-verdicts.json')  # SIMB3V M3：P1-P7/N1-N5 socket 级机检（自管真 server 子进程；缺 UFW 资产回退合成 fixture，约 10s）
    run(sys.executable,'tools/test_sim_world_driver.py')      # SIMB1C：直调驱动器（合成 machine 形状）
    run(sys.executable,'tools/test_sim_world_boot.py')        # SIMB1C：世界引导缝合线（dual_case post_stop 注入）
    run(sys.executable,'tools/test_sim_m1_helpers.py')       # SIMB1C：验收执行器纯函数面（帧形/EQ 曲线编码）
    run(sys.executable,'tools/test_call_scene.py')           # CALLIMPL：通话态注入场景纯函数面（帧/watch/队列/节点环）
    run(sys.executable,'tools/test_keycons_scene.py')        # KEYCONS：按键事件消费面场景纯函数面（队列/环/watch/泵寄存器面）
    run(sys.executable,'tools/test_world_snapshot.py')
    run(sys.executable,'tools/test_jlstream_kick_param2.py')  # EVIMPL:单点槽 unknown 放行守卫/消费负例/具体值对照
    run(sys.executable,'tools/test_jl_audio_dac_dma_idle.py')  # DMAIMPL:HRP/SWP 两字读回守卫/失效矩阵/真件形状钉子
    run(sys.executable,'tools/test_jl_audio_dac_dma_setup.py')  # DACDMAREAD:配置五死读放行守卫/sites=4 负例/真件形状钉子
    run(sys.executable,'tools/test_jl_audio_dac_dma_len.py')  # DACDMALEN:LEN 活读回读守卫/零写负例/真件形状钉子
    run(sys.executable,'tools/test_br28_dsp_models.py')  # COPEXIMPL:mode-22 指数近似守卫/域外停点/.p 拒绝/真件形状钉子
    run(sys.executable,'tools/test_frontier_iteration.py')  # CKPT:前沿迭代 runner 纯函数面(比较口径/值解析)
    run(sys.executable,'tools/test_handoff_lint.py')
    run(sys.executable,'tools/probe_boot.py','--check-known-blockers','--report','analysis/local-checks/boot-prefix.json',tool='probe_boot.py')
    run(sys.executable,'tools/probe_boot_handoff.py','--report','analysis/local-checks/boot-handoff.json',tool='probe_boot_handoff.py')
    run(sys.executable,'tools/probe_boot_boundaries.py','--report','analysis/local-checks/boot-boundaries.json',tool='probe_boot_boundaries.py')
    run(sys.executable,'tools/analyze_qcy_irq.py','--report','analysis/local-checks/qcy-irq.json',tool='analyze_qcy_irq.py')
    run(sys.executable,'tools/analyze_p11.py','--report','analysis/local-checks/p11-resource.json',tool='analyze_p11.py')
    run(sys.executable,'tools/analyze_startup_chain.py','--report','analysis/local-checks/startup-chain.json',tool='analyze_startup_chain.py')
    run(sys.executable,'tools/analyze_cpu_context.py','--report','analysis/local-checks/cpu-context.json',tool='analyze_cpu_context.py')
    run(sys.executable,'tools/analyze_icfg_status.py','--report','analysis/local-checks/icfg-status.json',tool='analyze_icfg_status.py')
    run(sys.executable,'tools/analyze_image_resources.py','--report','analysis/local-checks/image-resources.json',tool='analyze_image_resources.py')
    run(sys.executable,'tools/analyze_hearing_aid_app_path.py','--report','analysis/local-checks/hearing-aid-app-path.json',tool='analyze_hearing_aid_app_path.py')
    run(sys.executable,'tools/analyze_hearing_aid_slot_address.py','--report','analysis/local-checks/hearing-aid-slot-address.json',tool='analyze_hearing_aid_slot_address.py')
    run(sys.executable,'tools/probe_debug_startup.py','--report','analysis/local-checks/debug-startup.json',tool='probe_debug_startup.py')
    run(sys.executable,'tools/probe_event_clock.py','--report','analysis/local-checks/event-clock.json',tool='probe_event_clock.py')
    if args.rom:
        run(sys.executable,'tools/analyze_hearing_aid_slot_address.py','--rom',args.rom.resolve(),'--report','analysis/local-checks/hearing-aid-slot-address.json',tool='analyze_hearing_aid_slot_address.py')
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
    if args.preflight:
        # 断言离线预检（路 CHK）：对标准 local-checks 报告集重放 check() 断言面，
        # 零仿真、秒级；报告缺失记 skipped 不算失败（--rom 档/手工产物未必在）。
        run(sys.executable,'tools/assert_preflight.py','--allow-missing',
            'analysis/local-checks/boot-prefix.json','analysis/local-checks/irq-entry.json',
            'analysis/local-checks/image-trace-diff.json',
            'analysis/local-checks/image-trace-selfcheck-conditional.json',
            tool='assert_preflight.py')
    if args.reproduce:
        run(sys.executable,'tools/check_reproduction.py','--output','analysis/local-checks/reproduction.json')
    if args.android_build:
        run(sys.executable,'tools/bootstrap.py','--android',serial=True)
        target_env={**os.environ,'H3S_BLE_ADDRESS':'02:00:00:00:00:01'}
        run('bash','tools/android-query/build.sh',env=target_env,serial=True)

    def execute(command,env):
        print('+ '+' '.join(str(part) for part in command),flush=True)
        subprocess.run(command,cwd=ROOT,env=env,check=True)

    for command,env in prelude:
        execute(command,env)
    failures=[]
    if args.jobs<=1 or len(checks)<2:
        # 串行：保持原顺序，遇错即停。
        for command,env in checks:
            try:
                execute(command,env)
            except subprocess.CalledProcessError as error:
                failures.append((command,error.returncode))
                break
    else:
        # 并发：各项独立 report，跑完全部再聚合报错。
        from concurrent.futures import ThreadPoolExecutor,as_completed
        with ThreadPoolExecutor(max_workers=args.jobs) as pool:
            futures={pool.submit(execute,command,env):command for command,env in checks}
            for future in as_completed(futures):
                command=futures[future]
                try:
                    future.result()
                except subprocess.CalledProcessError as error:
                    failures.append((command,error.returncode))
    if failures:
        for command,code in failures:
            print(f'FAILED ({code}): '+' '.join(str(part) for part in command),file=sys.stderr,flush=True)
        sys.exit(1)
    # 收尾屏障：全部检查结束后再看工作区（前缀检查只看已跟踪改动，与 report 输出无竞争）。
    execute(('git','diff','--check'),None)
    print('Local checks passed. No hardware was accessed.',flush=True)


if __name__=='__main__':
    main()
