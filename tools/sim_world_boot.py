#!/usr/bin/env python3
"""SIMB1C 世界引导：explore 停点世界的构造链（docs/sim-m1-direct-spec.md §3.2）。

回退案（不等 JLEQ）：irq_case 没有 post_stop 透传参（probe 五文件禁区，等 §5.2
合并后加），但 dual_case 有——本模块用 analyze_image_trace.frontier_run 的同一条
缝合线（monkeypatch probe_irq_entry.dual_case 注入 kwarg，先例=budget 注入）把
serve_loop 塞进构造链尾部：

    probe_irq_entry.irq_case(module, app, header, rom, name, **flags,
                             flash_image_bytes=image, snapshot=snapshot_cfg)
        └ dual_case(..., post_stop=serve_loop)      ← shim 注入

开关集零复刻：scenario_flags('p11_heap_experimental', probe_irq_entry) 单源
import（frontier_registry），fixture/预算推导全走 irq_case 原体——「复刻段腐化」
风险（R16/D8）降到一条 kwarg 注入。世界停点=实施时点最新已过线前沿（当前
29,215,313 @ 0x6072b92 写 0xfe1500，FRONTIERS 单点）；JLEQ 合并后重钉
（规格 §5.3 第一刀），本模块零改动（前沿变了它跟着 registry 走）。

快照生命周期：首轮 capture（全量重放到停点后捕获，~27min 量级）→ 之后 restore
（~8s）+budget=1 首步即重撞停点 → post_stop 进入 serve_loop。world_dead 后外层
循环 restore 重建复役。
"""
from __future__ import annotations

from pathlib import Path

WORLD_NAME = 'sim_bridge_world'          # 场景名兼快照 expected_context 钉子（capture/restore 一致）


def snapshot_path(snapshot_dir, scenario='p11_heap_experimental') -> Path:
    return Path(snapshot_dir) / f'{scenario}-{WORLD_NAME}.bin'


def build_world(serve_loop, *, rom_path, firmware_path, snapshot_dir,
                scenario='p11_heap_experimental', capture=False, world_name=WORLD_NAME):
    """构造一个停点世界并把 serve_loop 挂进 post_stop；返回 explore 场景结果 dict。

    serve_loop(machine, hardware, stopped_cpu) -> dict|None（post_stop 契约，
    probe_boot.py:98-116 docstring）：停点后、报告组装前在活机器上回调；返回的
    dict 并进场景报告（probe_boot.py:2140-2141）。serve_loop 返回 {'world_dead':
    {...}} 即外层循环的重建信号（§3.3）。
    """
    import frontier_registry
    import probe_irq_entry
    from probe_boot import application, check_rom, flash_image
    from probe_boot_handoff import package_header
    from probe_pi32v2 import upstream

    module = upstream()
    app = application(Path(firmware_path))
    header = package_header(Path(firmware_path))
    rom = Path(rom_path).read_bytes()
    check_rom(rom)
    image = flash_image(Path(firmware_path))
    flags = frontier_registry.scenario_flags(scenario, probe_irq_entry)

    target = snapshot_path(snapshot_dir, scenario)
    if capture or not target.exists():
        snapshot_cfg = {'capture': target}
    else:
        # restore：budget=1 → 恢复后首步重撞停点（捕获时点的 Blocked 在恢复世界
        # 里原样复现，快照-续跑既有语义），post_stop 拿到与捕获时同构的停点世界。
        snapshot_cfg = {'restore': target, 'expected_context': {'scenario': world_name},
                        'budget': 1}

    original_dual = probe_irq_entry.dual_case

    def shim(*args, **kwargs):
        kwargs['post_stop'] = serve_loop
        return original_dual(*args, **kwargs)

    probe_irq_entry.dual_case = shim
    try:
        result = probe_irq_entry.irq_case(module, app, header, rom, world_name,
                                          **flags, flash_image_bytes=image,
                                          snapshot=snapshot_cfg)
    finally:
        probe_irq_entry.dual_case = original_dual
    return result
