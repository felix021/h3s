#!/usr/bin/env python3
"""Three-image startup-trajectory diff under the shared frontier projection stack.

Loads two or three named UFW images (official / tested_b79 / eq_fence_repair,
each SHA-pinned), runs the real startup prefix for each under the IDENTICAL
projection stack — imported straight from probe_irq_entry.irq_case — and
compares the two executions STEP BY STEP: per executed instruction the compared
signature is (core, before_pc, next_pc, flags, all 16 GPRs in canonical form
including UnknownWord byte provenance and IcfgValue masks, and every guest RAM
write of that step). The report states the first divergence (step, PC,
classification, root cause against the byte-diff runs of the two images) or
that no divergence was observed up to the shared stop point. No device access,
no flash approval.

Two shared-stack scenarios (--scenario): the default crc_bankb runs the pinned
crc_bankb_erased_fixture frontier flag set; p11_heap_experimental layers the
p11_heap_experimental_config conditional opt-ins (derived-not-measured heap
fixture, Timer0 STD12M, LP timer, P33 family, P3_LS, reset-projection mask
sets, subword stores) on the same stack up to the current conditional frontier.
Every conclusion under the conditional scenario is conditional, not a verified
boot fact; the default scenario's behavior and pins stay byte-identical.

Snapshot resume rounds (route SNAPX, docs/snapshot-resume.md):
--capture-snapshots DIR captures each image's world at its natural stop during
a full replay (once per code state), --resume-from DIR starts every arm from
its captured world instead of replaying from zero — verification rounds
(parity / stop re-checks / freshness) then cost seconds per arm. Boundary
(documented in the doc): resume re-verifies the STOP and REPORT surface only;
the per-step signature comparison covers post-restore steps, so pinned
full-prefix trace facts are not re-executed, and any simulation-source change
refuses at the snapshot fingerprint gate (behavior changes require a fresh
full replay by construction).

Honest comparison basis (what a "no divergence" result does and does not mean):
- per-step comparison covers scheduling, fetch PC, control flow, flags, the
  full register file and guest RAM writes;
- peripheral-register writes are NOT in the per-step signature (no hook): a
  divergence there only becomes visible through a later read-back into a GPR,
  a stop-point difference, or the final report comparison;
- when both step traces are identical the two final reports (stop, registers,
  adapter counters, peripheral records, memory snapshots, machine events) are
  compared as a whole;
- after a CONTROL-flow divergence the tool searches for a step lag under which
  the two control sequences realign for the whole remaining suffix up to both
  stops (reconvergence): the realignment point, the zero-divergence lengths,
  and every remaining signature-divergent span under that lag (register state
  and guest RAM writes) are reported; if no lag realigns the traces that fact
  is reported honestly;
- every pair also reports stop-point parity (steps, per-core steps, full stop
  records): reaching the same honest boundary under the shared stack;
- identical or reconverging trajectories on this prefix only mean the prefix
  did not CONSUME the image differences into any compared observable (or
  consumed them into values that died without changing control flow); it is
  NOT an equivalence proof for the images (unconsumed hook sites stay
  unconsumed evidence).
"""
import argparse
import bisect
import hashlib
import io
import itertools
import json
import pickle
import sys
import tempfile
from collections import OrderedDict, deque
from pathlib import Path

import probe_dualcore
import probe_irq_entry
import frontier_registry
from br28_machine import DualCoreMachine
from br28_icfg import IcfgValue
from br28_unknown import UnknownWord
from probe_arch_startup import REPAIRED_SHA, candidate_view
from probe_boot import APP_SHA, OFFICIAL_SHA, application, check_rom, flash_image
from probe_boot_handoff import package_header
from probe_pi32v2 import PIN, ROOT, require, upstream
from verify_eq_candidate import load_image
from verify_ufw_integrity import verify

B79_SHA = 'b79cb3a6f8702eb9e1d12eaf181a968f6b5848a66c051b2c792c39aa8b42fa1b'
B79_APP_SHA = '8f594d5908f6d4eac9b63e5e4538d3c0dbf0ed1a1e285547fcf2d9c6b0914517'
EQ_APP_SHA = '3788e3a1e25ed4e14ac3e8167674279e2d9e48e1904684c4aeaa06bc11d579cb'
# 候选镜像(d1-acceptance §6.5 / candidate-image-cotest §1.2;UNTESTED 不刷机):
# sha/plain 长度钉死值来自 candidate_world_checks.CANDIDATES(单源,勿复制);
# app 切片 sha 为 2026-11-15 路 DIF 从规范构建产物实测钉死(build_quiet_candidate
# --attenuation-db 0 --hearing-aid-l1-slotram / --r4-keyhook)。
L1_SLOTRAM_SHA = '6f267a2ed0b62ed118047e889a64483907c727d36eb5172a5d0f0067d37b1b93'
L1_APP_SHA = 'd830222c902bf5663027ecbbf540d983054fb39af45fd47e0dce955a0c1f52ab'
# HAL3 re-pin: keystore_hook status-byte erratum fix (st.w -> st.b at
# resp+2, docs/hearing-aid-l3-mpo.md §5) — the old candidate bfc3f681… made
# set_config failures read as successes; app-slice sha unchanged (the fix
# lives in the appended segment, the registry slot still points at the same
# entry address).
R4_KEYHOOK_SHA = '2576df46f583a8ac8b235705fd0bb75eaa6e6ebcb04c86afac125e9b018f00cf'
R4_APP_SHA = '3a186192370195f9823f0427cf7c6d4f9ab9dffb7e25d8956baab35b7e23451f'
CANDIDATE_APP_SHAS = {'l1_slotram': L1_APP_SHA, 'r4_keyhook': R4_APP_SHA}
PLAIN_LENGTHS = {'official': 0xE3053, 'tested_b79': 0xE3053, 'eq_fence_repair': 0xE31C5,
                 'l1_slotram': 0xE31F8, 'r4_keyhook': 0xE3286}
OFFICIAL_DEFAULT = ROOT / 'firmware/QCY_H3S_7018F_1T2_release_note_V1.1.8_011.ufw'

# Shared-stack scenarios. The crc_bankb flag set is the pinned shared-frontier
# stack (main scenario; default). The p11_heap_experimental flag set layers the
# conditional opt-ins of probe_irq_entry's p11_heap_experimental_config scenario
# (derived-not-measured heap fixture, Timer0 STD12M, LP timer, P33 family,
# P3_LS, reset-projection mask sets, subword stores...) on top of the same
# stack. The scenario construction (probe_irq_entry.irq_case) is imported, and
# both flag lists and the freshness stop pins derive from the single-source
# tools/frontier_registry.py (same registry probe_irq_entry.main() itself
# uses); the data constants referenced by name (P11_HEAP_INFO_DERIVED etc.)
# are the real probe_irq_entry module values, never copies. Adding an opt-in
# switch or advancing the frontier only touches the registry; registry drift
# (unregistered irq_case switches, non-closed dependency stacks) fails
# tools/test_image_trace_diff.py instead of silently changing what runs here.
BASE_SCENARIO_FLAGS = frontier_registry.base_flags()
CONDITIONAL_EXTRA_FLAGS = frontier_registry.extra_flags(probe_irq_entry)

SCENARIOS = {
    'crc_bankb': {
        'irq_case_flags': BASE_SCENARIO_FLAGS,
        'stack_description': ('probe_irq_entry.irq_case with the crc_bankb_erased_fixture '
                              'frontier flag set (imported)'),
        'default_budget': 300000,
        # 配置新鲜度钉子:official 必须复现主前沿停点,否则本工具的 flag 集已与
        # 主前沿场景漂移(或上游场景变更),结果不可信。停点字面值单点在
        # tools/frontier_registry.py,前沿推进后只改 registry(本值自动跟随)。
        'freshness': frontier_registry.freshness('crc_bankb'),
    },
    'p11_heap_experimental': {
        'irq_case_flags': {**BASE_SCENARIO_FLAGS, **CONDITIONAL_EXTRA_FLAGS},
        'stack_description': ('probe_irq_entry.irq_case with the p11_heap_experimental_config '
                              'conditional flag set (crc_bankb stack plus the derived-not-measured '
                              'opt-ins: every conclusion under this scenario is conditional, not '
                              'a verified boot fact)'),
        # 路 B3/S19 批 2 续:两案(快照+模拟族)放行后前沿滑过 retry 环+第
        # 12 次溢出窗+ADDA 模拟 init(4.5M)+OS 等待族(事件时钟首次真实推进
        # 13.4M 虚拟步)到 19,139,239 @ 0x606abbe,预算 16M→20M(留停点后余量,
        # 单测钉 default_budget>=前沿+1000)。路 DT/S19 批 2 续二:DAC_TM0/TM1
        # setter 容器放行后链越过两次 -600 半字写(DAC 启动尾段+分配对象+
        # OS 等待族再走 2.66M 步)到 21,798,206 @ 0x606b5ee 读 0xfe0a20
        # (JL_ADDA ADA_CON0),预算 20M→40M(20M 实测在 19,996,266 耗尽)。
        # 路 ADA/S19 批 2 续三:JL_ADDA ADA 子族九字容器放行后链走过停点
        # 函数 0x606b4d0 的 ADA 配置段(12 写)再 +51 步到 21,798,257 @
        # 0x606b68e 读 0xf2b404(P11_LPVAD->VAD_ACON0 bit28 置位 RMW 内部读),
        # 预算 40M 保持(余量 >18M)。
        # 路 WDFEED:喂狗邮箱事务闭合→P11 侧 WDT feed 副作用放行后,park
        # 稳态越过看门狗伪到期(32,737,356;喂狗 162 笔全重装,watchdog 未
        # 到期)到 41,828,530 @ 0x60649d0 st.w.pin.l 全字存储写未初始化物理
        # 堆 0x19f7a8——上游 store 模型簿记读边界(183,486 st.d.sp.l/236,415
        # 半字 pair 同族第三例),预算 40M→50M(留停点后余量)。
        # 路 STOREG3:独立(非并行)后增整字存储直写(exact_postincrement_
        # word_stores,word 4 形全族)放行后过 41,828,530 簿记读停点,S18
        # 事件消费链走通 +18,773 步(纯 CPU0 段)到 41,847,303 @ 0x6019458
        # LED 驱动 RMW [0xfd610c]&=~0x20 的架构性读(JL_PLED 域 slot3,
        # 未建模寄存器,读侧无证据诚实停)——PLED/N2 电源框架三选一归下一
        # 路。预算 50M 保持。
        # 路 FD610C:JL_PLED CON3(0xfd610c)单字容器(合成初始 0+读回=当前
        # 值;codex 定调单字最小假设)放行后,0x6019458 的配置 RMW 内部读
        # 服务合成 0→清 bit5 写回 0,helper 0x601945c 返回,再 +503 步(纯
        # CPU0 段,per-core1 11496 不变)到 41,847,806 @ 0x60194b4 读
        # 0xfd6100=JL_PLED CON0 的 save-restore 首读(r6=[0xfd6100] 存使能
        # 位→清 bit0→if(r6&1)恢复——CON0 是分支消费者,单字容器不纳入,
        # 诚实停;0x6069b0e 读 bit0 定亮度步进同域)。预算 50M 保持。
        # 路 FD610C-con0:JL_PLED CON0(0xfd6100)具名整字初态容器(首写前
        # 读=具名 fixture、写后读=最后写值;codex 三选一 (a) 定调条件主臂
        # 0x00000000、敏感性对照臂 0x00000001,derived-not-measured 非硅复位
        # 值;显式读/RMW/整字写共享同一状态走通用 mmio 路径零新分发)放行
        # 后,0x60194b4 save-restore 首读服务 0→0x60194b6 清 bit0 RMW 写回
        # 0(word_writes 1)→0x60194ca if(r6&1)假分支跳过恢复→0x60194a4
        # 返回,再 +576 步(纯 CPU0 段,per-core1 11496 不变)到 0x6019502
        # helper 首条 [0xf2a610]&=0xFFFFFF1F 清 bits5-7 RMW 的内部读;路
        # P2MCLK:P11_SYSTEM P2M_CLK_CON0(0xf2a610)单字配置 RMW 容器(helper
        # =SDK 宏 LED_CLK_SEL(3),全 app 唯一引用、读值只进写回组装零分支
        # 零轮询,GET_LED_CLK_SEL 无人引用;codex 三选一 (a) CON3 式:合成
        # 初始 0+RMW 内部读回=当前值,显式读一律拒绝,只服务已观测两种立即
        # 数形状)放行后,两腿 0→0→0x60 写回、RAM 标志 b[0x12603C]=1、返回
        # 一次性 LED 初始化守卫调用者(0x601a30c 臂,b[ctx+48]|=1)走逐通道
        # 重配序列进 0x601a41c pend(IPMASK 0→6,OS 临界区机制不动 CLI
        # bit9)于 0x601a422 在 pend 窗内调 LED 复位 helper 0x60195e2,其
        # CON0 清位/整字清零(0x60195ec/f0/f6)由 CON0 容器服务,再 +2,598
        # 步(纯 CPU0 段,per-core1 11496 不变)到 41,850,980 @ 0x60195f8
        # 写 0xfd6104=JL_PLED CON1 整字 0(pend 窗内停,两容器之外的
        # JL_PLED 字,诚实停,归下一路)。预算 50M 保持。
        'default_budget': 50000000,
        # 条件前沿新鲜度钉子:official 在条件 flag 集下必须复现当前条件前沿停点
        # (probe_irq_entry 的 p11_heap_experimental_config 场景前沿)。停点字面值
        # 单点在 tools/frontier_registry.py,前沿推进后只改 registry(本值自动
        # 跟随)。以下为前沿推进史(叙事,HANDOFF 同步维护)。路 BY:PND 完成
        # 投影+RES 具名 fixture 闭合采样事务,前沿 447,383 @ P3_ANA_READ 首读。
        # 路 CB:p3_ana_read_ldoin_low 具名常量 fixture(SDK charge_hw.h bit1=
        # LVCMP_DET/bit2=LDO5V_DET+charge.c 阈值+无外部供电场景锚→两比较器读 0)
        # 放行首读;L5V/CHG_CON2 计算寻址面并入 tail 容器;~447,490 core tick
        # 首溢出真实交付,其 handler 在途期间 Timer0 请求被延期机制挂起(14 次,
        # 挂起不丢弃);OS tick 级联把世界带进 0x6069538 的 GPCNT_CON 读,路 CB
        # 停在 601,880 @ unknown_gpcnt_configuration_bits。
        # 路 CG:gpcnt_con_readback 位粒度读回投影(br28.h CON __RW+全 app+ROM
        # 唯一写者=固件自己的 disable+clear 位 RMW 对)放行该读——bit0=0(app
        # 自己关断)→ 真实跳过分支,任务 0x6069538 九次调度全空手返回;W1[0]
        # 20 轮超时路径全预算执行(41 次 fixture 读、不创建充电任务);core tick
        # 溢出交付合计 9 次/延期 138 次/Timer0 交付 286 次;超时后进 mode 切换
        # 函数 0x6068152(r0==1 分支)——3,070,626 @ 0x606817a 读
        # [0xfd0008]=JL_CLOCK->DCCK_CON0 停。
        # 路 CL:jl_clock_dcck 投影(路 AM 容器同款「显式合成初始字 0 + 读回=
        # 当前寄存器值」;br28.h __RW + SDK power_clock.c save/restore 双证据)
        # 放行该读——mode 切换函数体真实执行(DCCK_CON0 0x46000e0c/DCCK_CON1
        # 0xff000100/DCCK_CON2 0x10000 + WLA_CON8/P3_ANA_CON0/7/8/9/10 配置 +
        # 软件延时),W1[0] 返回,W1[1] walker call scheduler 中断上下文 helper
        # 0x6065c3a。路 CO:partial_icfg_masked_branch 处理器放行 0x6065c4c 的
        # active-priority 低字节掩码测试 `if ((r0&0xFF)!=0)`(bne.and.modimm.ri.l
        # i=0xFF;empty-active-priority fixture 下低字节已知 0 → 不跳);helper
        # 随后 call 0x12cb08(RTOS 临界查询→1)跳过错误路径、经 0x60659ee→
        # 0x12b55c 重进临界区(IPMASK 0→6),停在 0x6065ccc `st.w.sp.l
        # [sp+8]=r9` 的 upstream 存储模型簿记合并读未初始化局部 0x19e2d4。
        # 路 CQ:exact_word_store_sp_l 独立子开关放行该整字存储(全 4 字节
        # 覆盖写、旧值无关,同 st.w.sp/st.d.sp.l 论证;形状在 legacy 前缀已执行
        # 3 次故单独 opt-in),helper 尾段写两局部并返回 W1[1] walker,最终停在
        # 0x601d1ac `ld.w r0=[r2+0]` 读 0xfd2000(0x601d198 读辅助函数的 r1==0
        # 分支;r1==1/2 读 0xfd2100/0xfd2200)。路 F:命名修正——0xfd2000/0xfd2100/
        # 0xfd2200 = JL_UART0/1/2 基址(br28.h `JL_UART0_BASE=ls_base+
        # map_adr(0x20,0)`,非「SDK 参考头未命名」),读辅助函数 = SDK uart_v1.c
        # `uart_is_idle`(bitcode 逐字节同形,return (word&1)^1);读侧三选一=
        # 方案 (a) 投影 `jl_uart_con_projection`(合成 0=空闲,derived-not-measured)
        # 放行后 uart_init 真实执行 CON0=0x3400,停 0x603446a 写 0xfd2004
        # (路 F 记作 JL_UART.BAUD|BUF,BAUD __WO)。路 M:UART 写侧三选一选方案
        # (a1) 一次性打包初始化写白名单 `jl_uart_init_write_records`——**布局钉死
        # (修正路 F 命名)**:实际 SDK 构建对象 uart_v1.c.o bitcode 声明
        # JL_UART_TypeDef={i32 x12}(成员 k 在 +4k),QCY 固件写序列逐点互证
        # (0x6034466/0x603446c/0x6034472/0x601d286/0x601d1f2/0x6034668..cc)——
        # 0xfd2004 是 CON1 字、真 BAUD 写在 0xfd2008;公开 br28.h 的 u16/u8
        # 紧凑布局与该构建对象不一致(BR31 等系列确有紧凑布局,布局分歧非笔误)。
        # 白名单=初始化序列实际出现的纯配置整字写 CON1/BAUD/OTCNT/RXSADR/
        # RXEADR/RXCNT/CON2(三基址对称),write-only 记录(lp_timer SYS_DIV/
        # P3_PORT_SEL 先例);TXADR/TXCNT/BUF/HRXCNT 不进白名单(TX 路径=启动
        # 硬件活动信号,codex 风险点);读回/RMW 严格停;波特率/收发/UART IRQ
        # 不建模。放行后 CON1=0/CON2=0 落地,uart_init 进 TX/RX 引脚复用,
        # gpio_set_mode/gpio_set_function 助手链停在 0x60012de 写 0xfd58b8=
        # 0x9f(0xfd58xx GPIO 端口组,GPIO_BASES 未覆盖、公开 br28.h 无名)——
        # UART 引脚复用 GPIO 面归下一路。
        # **新前沿 3,072,236 @ 0x60012de**(unmapped_or_readonly_write 写 0xfd58b8)。
        # 路 R:GPIO 引脚复用写侧三选一选 write-only 整字记录 `jl_omap_writes`
        # ——**识别钉死:0xfd58xx = JL_OMAP**(SDK io_omap.h `JL_OMAP_BASE=
        # ls_base+map_adr(0x58,0)`=0xfd5800,组 0x58 逐引脚输出功能选择字;
        # SDK 驱动表 gpio_out_regs 与固件表 0x609EA9C 逐字节一致,gpio 224/
        # 225=IO_PORT_DP/DM 双侧特例 0xfd58b8/0xfd58bc,停点值 0x9f=io_omap.h
        # FO_UART0_TX=((39<<2)|BIT(1)|BIT(0))=uart_init 的 TX 引脚复用)。
        # 49 字白名单(A16/B12/C9/G9/USBDP/USBDM/PP0)放行后 TX 复用链完成,
        # 第 297 次 Timer0 到期在临界区退出后真实交付(287 次),RX 复用链
        # gpio_set_fun_input_port 助手 0x600132e 停在 0x6001342 写 **0xfd5c90=
        # 0x2f**——JL_IMAP->FI_UART0_RX(io_imap.h map_adr(0x5c,0)=0xfd5c00,
        # 成员 36、编译布局 4 字步进;值 47=USBDP_IN,UART0 RX 与 TX 同用 USB
        # DP 脚)。前沿 3,073,402 @ 0x6001342。
        # 路 V:输入面写侧三选一选 write-only 整字记录 `jl_imap_writes`
        # ——**识别钉死:0xfd5c00..0xfd5cf8 = JL_IMAP**(io_imap.h 63 成员
        # FI_GP_ICH0..FI_CHAIN_RST;csfr.h `__u8→unsigned int` 显式 4 字编译
        # 布局,gpio_hw.c.o PFI_* 枚举 16604160..16604408 逐成员互证;
        # gpio2crossbar_inport(224)=47=USBDP_IN 与固件 resolver 0x60012e8 双
        # 侧同值,通道号表与固件表 0x609EAB8 逐字节一致;全镜像扫描零读回)。
        # 63 字白名单放行 0xfd5c90 写后 RX 复用链完成,链继续 +516 步停在
        # **0x60340d8** 复合 RMW `[r0+4] &= 0xFFFFFFEF` 的内部读——**读
        # 0xfd2004=JL_UART0->CON1**(SDK uart_fifo_reset:|=16→RXSADR/RXEADR/
        # RXCNT→&=-17,bit4=RX DMA 重启位;读回=真实消费,读回类真决策点
        # 诚实停,归下一路三选一;停点在 0x60340cc 的 OS unmask 临界区内,
        # IPMASK 0→6、ICFG 保持 0x300)。路 V 前沿 3,073,918 @ 0x60340d8。
        # 路 AA:CON1 读回投影 `jl_uart_con1_readback`(读回=最后写值,lp_timer
        # CON 路 AL/fd31 路 BN 同族,只在写过之后返回;条件假设「读回=软件
        # 配置值」derived-not-measured,RMW 走完整通用路径、写回落记录)放行
        # 该读——CON1 读 0、写回 0&0xFFFFFFEF=0(平凡零发明),uart_init 事务
        # 全部收尾:BAUD=0x270×2(两次 baudrate)、OTCNT=0x1e834、RX 三连写
        # RXSADR=0x125bfc/RXEADR=0x125e1c/RXCNT=0x220、CON0 |=0x68/|=0x4→
        # 0x346f(路 F 容器),无新到期无新交付。事务后世界进入事件域:
        # app_core 尾 0x6011fee 的 init 调用链 0x60c32a4..0x60c32b8(W1[0]
        # 0x6012e3e/0x601321e/0x6013246 之后)第 4 项 0x602ea68 的首指令
        # `st.w.modimm [0xfe2200]=1`——0xfe2200 = br28.h **JL_WL_AUD->CON0**
        # (hs_base+map_adr(0x22,0),单成员 __RW)整字写,SDK 头有名、模型
        # unmapped,诚实停(unmapped_or_readonly_write)——音频/无线域首个
        # 新边界,独立决策点,不套用最后写值。**新前沿 3,079,241 @ 0x602ea72**
        # (写 0xfe2200=1)。
        # 路 AK:音频 init 写侧分域两开关 `wl_aud_con_writes`(JL_WL_AUD CON0
        # 0xfe2200 整字写记录+读回=最后写值,uart CON1 路 M→F→AA 最终形态;
        # 无写时显式合成 0=路 AM 先例,derived-not-measured)与
        # `audio_dac_init_writes`(DAC_CON 0xfe0500/AUD_CON 0xfe0558/JL_ASS
        # CLK_CON 0xfe0900 三字写记录+读回,层叠前者;条件族不进白名单)放行
        # S0 的 entry 写(+1,760 步)后,**实际停点偏离 AG 图预期、如实停住**:
        # 0x602ea68 头部场景 walker 段(AG 图判「通过」的漏项——审计只覆盖
        # SFR/RAM 性,未覆盖 st.d×MMU 页交互)0x602ea76→0x60755b2→0x6074234→
        # 0x6007ae8 查名 helper 函数头 0x6007af2 `st.d.sp.l d[sp+8]` 在
        # app_core 虚拟栈 sp+8=0x400afc 跨 128B MMU 页界(全 boot 首例,此前
        # 双字存储全在物理栈/页内)——跨页双字的硬件语义无测量证据,
        # mmu_cross_page_access_not_split 诚实停,归下一路三选一。S1..S5
        # (DAC init SFR 簇)本前缀未到达,audio 容器登记但零写零读。
        # **新前沿 3,081,001 @ 0x6007af2**(mmu_cross_page_access_not_split
        # 0x400afc size 8)。
        # 路 AN(sim-crosspage):opt-in `mmu_cross_page_split`(跨页双字存储
        # 拆两个独立 4 字节页内访问、各半各自 MMU 翻译;上游 m2 模型总线形状
        # 本就是两个独立 4 字节事务+AC791N 真机三例 128B 界跨越样本,现场两页
        # 物理倒序可区分,derived-not-measured)放行跨页 st.d 后,walker 链
        # +56,572 步全真实执行(5×场景表 walker 各 6 轮 CRC 全零种子六值循环、
        # 拆分执行 5 次、zalloc/config/ROM memset),停在 0x60719a6 ADC 节点
        # reset(配置门 b[0x1267E1]==0 分支)的并行对首写 **0xfe0538**
        # (JL_AUDIO+0x38 ADC_CON,路 AK 范围边界明确不进白名单的条件族首位)
        # ——unmapped_or_readonly_write 诚实停,归下一路三选一。S1..S5 仍未
        # 到达(0x6071cbc 在停点之后),audio 容器继续零写零读。
        # **新前沿 3,137,573 @ 0x60719c0**(unmapped_or_readonly_write
        # 0xfe0538=0)。
        # 路 AS(sim-adcreset):opt-in `jl_audio_adc_reset_writes`(域三,ADC
        # reset 写 [0xfe0538]=0 记录+读回;写=0x60719c0 并行对旧 r3 半,
        # FM-1 run3 旧寄存器语义首个 SFR 站点;路 AN 叙事门地址 b[0x1267E1]
        # 勘误为 b[0x127801]=0x1265C0+0x1241)放行后 +32,789 步:ADC 节点
        # reset 完成(guard 置 1/hdl 0x1277B4/槽 0x126A90 动态互证)→ S1..S5
        # DAC 簇落地(**S1 值勘误:0x6071cbc 是并行对旧 r1 半,实测 0x0,
        # AG 图「=0xfe2200 数值巧合」是并行对误读**;audio 容器 5 写+RMW 读
        # 4)→ initcall 尾块全过(Timer0 第 5 次到期落 ROM 0x4180 rep 体内,
        # 交付分发按路 CB 先例扩到指令中途延期,块尾 0x4196 交付),停在
        # **0x606b426 读 0xfe0a04=JL_ADDA->DAA_CON1**(+0x04 __RW,DAC 侧模拟
        # 配置;0x606b40e 配置 RMW insert 函数读→旧值>>3 入高位+flash 表位段
        # →写回,经 veneer 0x606db6c 调入)——**音频尾段首个非掩码平凡 RMW
        # 读**:读值实质参与写回且本世界该字零写,= 首个真实硬件反馈依赖
        # (已知音频 init 写序列已全部执行完,验收达成)。非轮询,归下一路
        # 三选一。新前沿 3,170,362 @ 0x606b426(unmapped_memory 读
        # 0xfe0a04)。**路 CV(sim-adda)再放行**(+17 步):DAA_CON1 两开关
        # 分立(显式初值探针 fixture 0x0+配置位读回;敏感性对照 {0x0,
        # 0xfffffff8} 同步数同停点=观察路径对初值不敏感)后 RMW 全链真实执行
        # (表址勘误 0x060A8608;写回 0x0),经真调用 veneer 返回,下一 call=
        # setter 0x606db36,停在 0x606db4c 读 0xfe051c=JL_AUDIO->DAC_VL0
        # (数字音量配置 RMW,分类=配置归下一路)。**新前沿 3,170,379 @
        # 0x606db4c**(unmapped_memory 读 0xfe051c)。
        # 路 CW(S19 批 0b/sim-s19b0)再放行(+31,769 步):DAC_VL0 配置读
        # (合成初始字 0 首读+读回;读值只进 insert 不进分支、双通道调用写回
        # 恒 0x0;ones 探针分叉同停点=观察路径对初值不敏感;**cap 槽勘误
        # [0x1265C0+1883*4]=0x12832C,原记 0x1272AC 系算术笔误**)+event_clock
        # 进条件栈(本前缀惰性零推进,首个全核 IDLE=W2[0] os_time_dly 在停点
        # 后未到达)后:setter 全链→dev 层→60KB DMA 缓冲 memset(0x4242 适配
        # 器 count 护栏按首个大清零形状 0x3c00 抬到 100000,语义假设零变化)→
        # 0x606ab0a 的 AUD_CON/DAC_CON RMW 链(AK 容器)后写 DAC_COP=0x7f 撞
        # unmapped。**旧前沿 3,202,148 @ 0x606ab2c**(unmapped_or_readonly_write
        # 0xfe0534=JL_AUDIO->DAC_COP,+0x34 成员 13 __WO,归下一路三选一)。
        # 路 B1(S19 冲刺批 1/sim-s19b1)再放行(+545,430 步):W4[4] 簇四件
        # (PVDD 容器/TIMER2 W1 投影/VLVD canary/DAC_COP write-only 记录)+尾段
        # 二次勘误件(DAC DMA 四字 write-only 记录)落地后,DAC 启动尾段+延时
        # 等待族真实执行,core-tick 第 12 次溢出在 IPMASK 屏蔽窗内 held 未决,
        # 固件 OS 时间更新路径在 0x12b0c2 并行对第二半读 core-tick PRD 撞 BF
        # 域严格边界。旧前沿 3,747,578 @ 0x12b0c2(
        # core_tick_overflow_requires_resolution;批 1 四件全部落地但零运行态
        # 消费者,BM 预期的 trim 环/TIMER2/canary/邮箱分支未到达,归批 2
        # 前置处理)。
        # 路 BQ(S19 冲刺批 2 一案)再放行(+7 步):core_tick_prd_readback
        # (三查分类:通用 overflow guard 挡 PRD+恢复后自然交付=只有读侧;
        # PRD 最近真实写入=tick_timer_init 0x606622c 的 1199999,模型步
        # 149,644 后零重编程)放行 0x12b0c2 并行对第二半的 PRD 读=回放
        # 最近真实写入,并行对完整提交(r10=旧 r2=0、r3=1199999)→PRD>9
        # 分支→跳过 cli veneer(arg2=0)→jiffies 缓存读 [0x125004](停点
        # 实测 21=基值+11 次 ACK 递增)→**新前沿 3,747,585 @ 0x12b0e8**:
        # retry 环读 CNT(0xffe0f0)撞同一通用 overflow guard——CNT 读回不
        # 在一案范围(codex「不改 CNT」),归下一轮三选一;第 12 次溢出仍
        # held 未决(IPMASK 6 窗口未结束,无漏仲裁,第二案不加)。
        # 路 B3(S19 批 2 续)两案放行到 19,139,239 @ 0x606abbe(读
        # 0xfe0524=JL_AUDIO->DAC_TM0,音频数字域运行态首读):
        # core_tick_cnt_held_snapshot(CNT 读=溢出后冻结快照,显式假设
        # assumed-not-measured 非 PRD-1)+jl_adda_analog_family(模拟 init
        # 0x606abf0 五字容器)——全文见 check_self_conditional 叙事。
        # 路 DT(S19 批 2 续二)jl_audio_dac_tm_readback 放行到 21,798,206 @
        # 0x606b5ee(读 0xfe0a20=JL_ADDA->ADA_CON0 bit5 清位 RMW 内部读,
        # 音频 ADC init 簇配置 RMW 族,归下一路)。
# 路 ADA(S19 冲刺批 2 续三)jl_adda_ada_family 九字容器放行到
        # 21,798,257 @ 0x606b68e(读 0xf2b404=P11_LPVAD->VAD_ACON0 bit28
        # 置位 RMW 内部读,P11 域配置 RMW 族,归下一路)。
        # 路 FLS ancif_official_resources 放行到 28,890,702 @ 0x6011976
        # (读 0xfd3b00=JL_RAND 族硬件熵源 ls_base+0x3b*0x100,随机种子
        # 函数 [0x601196a,0x60119ca) Lehmer MINSTD LCG 的种子首读,
        # sdfile ANCIF/ANCIF1 两文件 parse 真实走通后 ANC 系数装载段的
        # 下一边界,归下一路)。
        # 路 RAND jl_rand_seed 具名 fixture(0x12345678,synthetic-not-measured)
        # 放行到 29,215,313 @ 0x6072b92(写 0xfe1500=JL_EQ->CON0,hs_base+
        # map_adr(0x15,0),值 0=EQ 关闭配置;MAC 生成两调用点真实走通后
        # ANC/EQ 音频域配置续的下一边界,归下一路)。
        'freshness': frontier_registry.freshness('p11_heap_experimental'),
    },
}

COMPARISON_BASIS = (
    'per-step comparison covers scheduling, fetch PC, control flow, flags, the full 16-GPR register file '
    '(including UnknownWord byte provenance and IcfgValue masks) and every guest RAM write of the step; '
    'peripheral-register writes are NOT in the per-step signature (no hook), so a divergence there only '
    'becomes visible through a later read-back into a GPR, a stop-point difference or the final report '
    'comparison; when both step traces are identical the two final reports are compared as a whole; '
    'after a control-flow divergence a step-lag search reports reconvergence (the realignment point, '
    'zero-divergence lengths and every remaining signature-divergent span under the lag) or the honest '
    'no-realignment fact; every pair reports stop-point parity; identical or reconverging trajectories '
    'on this prefix only mean the prefix did not consume the image differences into any compared '
    'observable (or consumed them into values that died without changing control flow), it is NOT an '
    'image-equivalence proof')

# 展示用已知差异语义对照(来源:verify_eq_candidate.audit 站点 + HANDOFF 镜像
# 资源节)。只用于把首分歧 PC/差异游程标注成人类可读的语义,不参与判定。
KNOWN_SITE_LABELS = (
    (0x34E92, 0x34E98, 'debug_init lower PC limit load 0x06000100 (shared)'),
    (0x34E98, 0x34E9E, 'debug_init fence upper PC limit immediate (b79 0x060c3338, eq candidate 0x060e31c5, l1_slotram 0x060e31e0, r4_keyhook 0x060e3286, l4_eq 0x060e33de)'),
    (0xDB9A, 0xDBA2, 'hearing-aid hook window ON-1 (whole-8B replacement, l1/r4 candidates)'),
    (0xDBC4, 0xDBCC, 'hearing-aid hook window ON-2 (whole-8B replacement, l1/r4 candidates)'),
    (0xDC74, 0xDC7C, 'hearing-aid hook window OFF-1 (whole-8B replacement, l1/r4 candidates)'),
    (0xDC7E, 0xDC86, 'hearing-aid hook window TRANS-A (whole-8B replacement, l1/r4 candidates)'),
    (0xDC9A, 0xDCA2, 'hearing-aid hook window OFF-2 (whole-8B replacement, l1/r4 candidates)'),
    (0xDCA4, 0xDCAC, 'hearing-aid hook window TRANS-B (whole-8B replacement, l1/r4 candidates)'),
    (0xA9C7C, 0xA9C80, 'cmdid 0x30 registry setter slot 0 -> 0x060e31e0 (r4_keyhook only)'),
    (0x59CE, 0x59D4, 'eq hook site eq_source_read_hook'),
    (0x25030, 0x25036, 'eq hook site eq_record_preset_source'),
    (0x2510E, 0x25114, 'eq hook site eq_record_custom_source'),
    (0x25364, 0x2536A, 'eq hook site eq_key_hook'),
    (0x78846, 0x7884C, 'eq hook site eq_open_hook (inside power_init)'),
    (0x9E510, 0x9E511, 'official-vs-b79 ANC key-cycle table byte at app.bin 0x9e410'),
    (0x0, 0x4, 'resource table header CRC word'),
    (0x20, 0x24, 'resource table nested CRC word'),
    (0xE0A2C, 0xE0A30, 'stream.bin entry header CRC'),
    (0xE2344, 0xE23A2, 'stream.bin volume parameter region'),
    (0xE2ED3, 0xE2ED5, 'config.dat JLFS header CRC (recomputed over the flipped index field)'),
    (0xE2EE1, 0xE2EE2, 'config.dat JLFS index field (b79 terminator 1, eq/l1/r4 candidates 0: enumeration continues to the appended entry)'),
    (0xE3053, 0xE3246, 'appended payload segment (eq eq_toggle.bin / l1 mic_effect.bin / r4 mic_effect.bin + keystore_hook)'),
)


def site_label(offset):
    for low, high, label in KNOWN_SITE_LABELS:
        if low <= offset < high:
            return label
    return None


# 路 W3-2:int 路径签名串 memo(纯函数:同一 int 值的签名字符串恒定,
# 无失效义务;只防无界增长——条目上限照 O3I 闭包 memo 先例超限全清,
# 上限取 1<<16,20M 世界唯一串实测 33,364,docs/perf-budget-analysis.md
# §11.6 W3-2)。20M 世界命中率即寄存器取值重复率,收益=每步 16 寄存器
# 的重复 f'{value:08x}' 格式化。
_SIGNATURE_MEMO_CAP = 1 << 16
_signature_memo = {}


def _register_signature(value):
    if isinstance(value, bool):
        return repr(value)
    if isinstance(value, int):
        memo = _signature_memo
        cached = memo.get(value)
        if cached is not None:
            return cached
        cached = sys.intern(f'{value:08x}')  # intern 后同值命中同对象,
        if len(memo) >= _SIGNATURE_MEMO_CAP:  # 与 after_step 原有 intern
            memo.clear()  # 语义一致(值/相等比较零变化)
        memo[value] = cached
        return cached
    if isinstance(value, UnknownWord):
        data = ''.join(f'{b:02x}' if b is not None else '??' for b in value.bytes)
        origins = ','.join(hex(o) if o is not None else '-' for o in value.origins)
        return f'u{data}|{origins}'
    if isinstance(value, IcfgValue):
        return f'i{value.known:08x}.{value.value:08x}'
    return '?'


class TraceSpool:
    """Spill-backed trace sequence with plain-list semantics (route O6).

    Each appended row keeps its ORIGINAL in-memory representation — the
    6-tuple (core, before_pc, pc, flags str, 16-tuple of register signature
    strs, writes tuple of (address, hex-str)) built by TraceMachine.after_step.
    Rows are pickled into ONE unnamed temporary file (tempfile.TemporaryFile:
    no directory entry anywhere on any filesystem, reclaimed by the OS on
    close or crash, so nothing ever lands inside the repository and there is
    nothing to clean up after a kill; location honors TMPDIR) in fixed-size
    chunks. A FRESH Pickler per chunk keeps the pickle memo bounded (a single
    long-lived Pickler memoizes every row tuple it ever wrote, which would
    reintroduce linear memory growth) and makes each chunk's records
    self-contained, so reads use a fresh Unpickler per chunk.

    Resident cost is one append buffer plus a two-chunk read cache (~1-2 MB):
    the pre-O6 resident Python list cost ~353 B/row and was >99.9% of the
    dual-image RAM peak (docs/perf-budget-analysis.md §3).

    Sequence semantics match a plain list of rows exactly: len, integer
    indexing (negative included), slicing (returning a LAZY view — never a
    materialization, so reconvergence suffix scans cannot bring the blow-up
    back), iteration and zip. Field-exact round-trip, view equivalence
    against real list slices and truncation-raises-not-EOF are pinned by
    unit tests in tools/test_image_trace_diff.py."""

    DEFAULT_CHUNK = 2048

    def __init__(self, file=None, chunk=None):
        self._file = tempfile.TemporaryFile() if file is None else file
        self._chunk = self.DEFAULT_CHUNK if chunk is None else chunk
        self._offsets = []   # file offset where each flushed chunk starts
        self._counts = []    # row count of each flushed chunk
        self._starts = []    # first global row index of each flushed chunk
        self._flushed = 0    # total rows handed to the file
        self._tail = []      # appended rows not yet flushed (the final chunk)
        self._cache = OrderedDict()  # chunk index -> decoded rows, <= 2 kept
        self._staging = io.BytesIO()  # per-flush pickle staging buffer (W3-1)
        self._closed = False

    def append(self, row):
        if self._closed:
            raise ValueError('append to a closed TraceSpool')
        self._tail.append(row)
        if len(self._tail) >= self._chunk:
            self.flush()

    def flush(self):
        """Write the pending tail rows as one self-contained chunk."""
        if self._closed or not self._tail:
            return
        self._file.seek(0, 2)  # appends always land at the tail, even if a
        offset = self._file.tell()  # read moved the shared file position
        # 路 W3-1:逐行 pickle 先落内存 BytesIO,拼完整块再一次写盘。本机
        # BufferedWriter.write 每次调用 ~6.1µs(释放 GIL 的 C 调用异常,
        # docs/perf-budget-analysis.md §11.5),逐行直写=每行踩一次=spool
        # flush 25.7% 自耗时的本体;BytesIO.write 是不释放 GIL 的 C 内联
        # (~0.1µs)。盘上字节形状不变:仍一块一个新建 Pickler(memo 界限
        # 语义,路 O6)、块内逐行 dump 的 pickle 流按序拼接,读侧照旧。
        staging = self._staging
        staging.seek(0)
        staging.truncate()
        pickler = pickle.Pickler(staging, protocol=4)
        for row in self._tail:
            pickler.dump(row)
        self._file.write(staging.getvalue())
        self._starts.append(self._flushed)
        self._offsets.append(offset)
        self._counts.append(len(self._tail))
        self._flushed += len(self._tail)
        self._tail = []

    def _rows_of_chunk(self, chunk):
        rows = self._cache.get(chunk)
        if rows is None:
            self._file.seek(self._offsets[chunk])
            unpickler = pickle.Unpickler(self._file)
            rows = [unpickler.load() for _ in range(self._counts[chunk])]
            self._cache[chunk] = rows
            while len(self._cache) > 2:  # two chunks suffice: every comparator
                self._cache.popitem(last=False)  # scan is monotone per spool
        else:
            self._cache.move_to_end(chunk)
        return rows

    def _check_open(self):
        if self._closed:
            raise ValueError('TraceSpool is closed')

    def __len__(self):
        self._check_open()
        return self._flushed + len(self._tail)

    def __getitem__(self, key):
        if isinstance(key, slice):
            return _SpoolView(self, *key.indices(len(self)))
        self._check_open()
        i = key + len(self) if key < 0 else key
        if not 0 <= i < len(self):
            raise IndexError('TraceSpool index out of range')
        if i >= self._flushed:
            return self._tail[i - self._flushed]
        chunk = bisect.bisect_right(self._starts, i) - 1
        return self._rows_of_chunk(chunk)[i - self._starts[chunk]]

    def __iter__(self):
        self._check_open()
        for chunk in range(len(self._counts)):
            yield from self._rows_of_chunk(chunk)
        yield from self._tail

    def close(self):
        """Drop the spill file for good (rows already consumed into the pair
        report are unaffected). The sequence becomes unreadable afterwards."""
        self._cache.clear()
        self._tail = []
        self._file.close()
        self._closed = True


class _SpoolView:
    """Lazy slice view over a TraceSpool (or another view). Indexing,
    iteration and len resolve straight through the parent; nothing is ever
    materialized, for any slice size."""

    __slots__ = ('_parent', '_start', '_stop', '_step')

    def __init__(self, parent, start, stop, step):
        self._parent = parent
        self._start = start
        self._stop = stop
        self._step = step

    def __len__(self):
        if self._step > 0:
            return max(0, (self._stop - self._start + self._step - 1) // self._step)
        return max(0, (self._start - self._stop - self._step - 1) // (-self._step))

    def __getitem__(self, key):
        if isinstance(key, slice):
            start, stop, step = key.indices(len(self))
            return _SpoolView(self._parent,
                              self._start + start * self._step,
                              self._start + stop * self._step,
                              self._step * step)
        i = key + len(self) if key < 0 else key
        if not 0 <= i < len(self):
            raise IndexError('trace view index out of range')
        return self._parent[self._start + i * self._step]

    def __iter__(self):
        return (self._parent[i] for i in range(self._start, self._stop, self._step))


class TraceMachine(DualCoreMachine):
    """Observation-only DualCoreMachine: records one compact signature per
    executed instruction. Inherited scheduling, projections and honesty stops
    are untouched; the two recording overrides only delegate to super()."""

    instances = []

    def __init__(self, *args, **kwargs):
        # 内存形态(路 O6):签名链不再常驻——行按原表示(6 元组)pickle 进
        # TraceSpool 的无名临时文件,比较器经惰性视图顺序/随机读,常驻内存
        # 不随行数增长(读回逐字段相等/报告逐字节相等由单测+对照实跑钉死;
        # 行形状/值语义/相等比较零变化,见 TraceSpool docstring)。
        self.trace = TraceSpool()
        self.pending_writes = []
        TraceMachine.instances.append(self)
        super().__init__(*args, **kwargs)

    def note_memory_write(self, address, data):
        self.pending_writes.append((address, data.hex()))
        super().note_memory_write(address, data)

    def after_step(self, cpu=None, before_pc=None):
        super().after_step(cpu, before_pc)
        if cpu is not None:
            flags = getattr(cpu, 'flags', None)
            # 串池形状(路 DT):寄存器签名/flags 字符串每步全新分配但取值
            # 高度重复——intern 去重后唯一集极小(值语义/行形状/相等比较/
            # 报告零变化,trace 本就不序列化;O6 落盘按值序列化同样受益)。
            self.trace.append((cpu.core_id, before_pc, cpu.pc,
                               sys.intern(f'{flags:02x}') if isinstance(flags, int) else repr(flags),
                               tuple(sys.intern(s) for s in map(_register_signature, cpu.R)),
                               tuple(self.pending_writes)))
            self.pending_writes = []


def frontier_run(module, app, header, rom, name, *, flash_image_bytes, readonly=(),
                 budget=None, scenario='crc_bankb', snapshot=None):
    """One run of a shared-stack scenario via probe_irq_entry.irq_case (the
    scenario parameter construction is imported, never duplicated: the flag
    set comes from SCENARIOS and derives from the single-source
    tools/frontier_registry.py — the same registry probe_irq_entry.main()
    itself uses). scenario 'crc_bankb' is the pinned shared
    frontier (default); 'p11_heap_experimental' layers the conditional
    derived-not-measured opt-ins on the same stack. budget overrides
    irq_case's own budget through a dual_case wrapper (a snapshot= restore
    budget rides the same wrapper — the LAST budget set here wins, so callers
    resuming from a snapshot pass the resume cap as budget, not the flag
    budget). Returns (result, machine_trace)."""
    require(scenario in SCENARIOS, f'unknown scenario {scenario!r}')
    TraceMachine.instances = []
    original_machine = probe_dualcore.DualCoreMachine
    original_dual = probe_irq_entry.dual_case
    probe_dualcore.DualCoreMachine = TraceMachine
    if budget is not None:
        def shim(*args, **kwargs):
            kwargs['budget'] = budget
            return original_dual(*args, **kwargs)
        probe_irq_entry.dual_case = shim
    try:
        result = probe_irq_entry.irq_case(
            module, app, header, rom, name,
            **SCENARIOS[scenario]['irq_case_flags'],
            flash_image_bytes=flash_image_bytes, readonly=readonly,
            snapshot=snapshot)
    finally:
        probe_dualcore.DualCoreMachine = original_machine
        probe_irq_entry.dual_case = original_dual
    require(len(TraceMachine.instances) == 1, 'expected exactly one machine per run')
    return result, TraceMachine.instances[0].trace


def snapshot_path(snapshot_dir, scenario, label):
    """Per-image snapshot file name inside a snapshot round directory (route
    SNAPX): one snapshot per (scenario, label) — official and tested_b79 worlds
    DIVERGE from step 155,390 on, so each image needs its own captured world."""
    return Path(snapshot_dir) / f'{scenario}-{label}.bin'


def resume_snapshot_config(snapshot_dir, scenario, label, effective_budget):
    """Build the irq_case snapshot config for one resumed arm (route SNAPX).

    Loads the per-image snapshot (running both drift gates up front: a stale
    fingerprint refuses HERE with the regenerate message, before any stepping)
    and converts the scenario-wide effective budget into the resume cap
    max(1, effective - captured_steps). Both endpoint shapes land correctly:
    a frontier-stop snapshot re-attempts its blocked instruction on the first
    resumed step (the cap never binds), a budget-stop snapshot N advances to
    the same stop a full effective-budget replay reaches. The expected_context
    gate pins the snapshot to THIS label's run name — an official snapshot
    restored into a tested_b79 arm (or a different scenario) refuses instead
    of silently comparing a wrong world.
    """
    from world_snapshot import WorldSnapshot
    path = snapshot_path(snapshot_dir, scenario, label)
    loaded = WorldSnapshot.load(path)
    context = loaded.header.get('capture_context') or {}
    completed = context.get('completed_steps')
    require(isinstance(completed, int) and completed >= 0,
            f'{path}: snapshot header lacks a valid capture_context.completed_steps')
    return {'restore': path,
            'expected_context': {'scenario': f'trace_{label}'}}, max(1, effective_budget - completed)


class _ReadOnlySpool(TraceSpool):
    """Route PAR3: the parent-side view over a finished arm's handed-off
    spool. Everything read-side is inherited unchanged; the two mutating
    entry points refuse so a compared artifact cannot be edited in place
    after the handoff."""

    def append(self, row):
        raise ValueError('append to a reattached (parallel-arm) spool')

    def flush(self):
        raise ValueError('flush on a reattached (parallel-arm) spool')


def _reattach_spool(spool_path, index_path):
    """Route PAR3: rebuild a READ-ONLY spool view over the bytes + chunk index
    a parallel arm worker wrote. Same chunk semantics (a fresh Unpickler per
    chunk, the two-chunk cache); the backing file is opened 'rb' on top of the
    class-level refusal. Parent and child run one code version — the sidecar
    index is a run-local handoff, not a persisted format."""
    index = json.loads(index_path.read_text())
    spool = _ReadOnlySpool.__new__(_ReadOnlySpool)  # skip __init__ (temp file)
    spool._file = open(spool_path, 'rb')
    spool._chunk = TraceSpool.DEFAULT_CHUNK
    spool._offsets = list(index['offsets'])
    spool._counts = list(index['counts'])
    spool._starts = list(index['starts'])
    spool._flushed = index['flushed']
    spool._tail = list(index['tail'])
    spool._cache = OrderedDict()
    spool._closed = False
    return spool


class RunPool:
    """Per-label run cache (route REUSE): every DISTINCT image executes exactly
    ONCE per (scenario, budget) and the resulting (result, trace) pair serves
    every consumer — the freshness check plus every pair the image participates
    in (three images, all combinations: 6 pair arms + 1 freshness arm used to
    be 7 full simulations; now 3). Re-execution becomes spool RE-READS: each
    consumer iterates the TraceSpool from chunk 0 again, so a second consumer
    costs disk bandwidth, never RAM (two-chunk cache per spool) and nothing is
    ever materialized. That the replaced re-executions were value-identical is
    not a new assumption: the self-checks already pin in-process re-execution
    determinism (the freshness require and check_self both demand the very
    same registry stop literals from two independently executed official runs
    in one process). Spools are closed once, by close(), after the LAST
    consumer finishes (route O6 release discipline, ownership moved from the
    pair comparator to the pool).

    Route SNAPX snapshot rounds (docs/snapshot-resume.md): snapshot_mode
    'capture' additionally captures each arm's world at its natural stop into
    snapshot_dir (one full replay per image, once per code state); 'resume'
    starts every arm from its captured world instead of replaying from zero —
    a frontier-stop snapshot round costs seconds per arm. Validity boundary:
    resume only reproduces what the captured prefix already pinned (stop
    records, final reports, per-core parity); the per-step signature
    comparison only covers POST-restore steps, so the pinned full-prefix
    trace facts (first divergence, reconvergence) are NOT re-executed — those
    need a full replay (and the drift gates refuse any resume after a
    simulation-source change anyway)."""

    def __init__(self, module, rom, header, budget, scenario, loaded,
                 snapshot_mode=None, snapshot_dir=None):
        self._module = module
        self._rom = rom
        self._header = header
        self._budget = budget
        self._scenario = scenario
        self._loaded = loaded
        self._snapshot_mode = snapshot_mode
        self._snapshot_dir = snapshot_dir
        self.runs = {}

    def arm_config(self, label):
        """(app, plain, readonly, budget, snapshot) for one arm — the single
        source shared by the serial path, the parallel arm workers (route
        PAR3) and the snapshot-round modes."""
        app, plain, readonly, _sha = self._loaded[label][1]
        budget, snapshot = self._budget, None
        if self._snapshot_mode == 'capture':
            snapshot = {'capture': snapshot_path(self._snapshot_dir,
                                                 self._scenario, label)}
        elif self._snapshot_mode == 'resume':
            snapshot, budget = resume_snapshot_config(self._snapshot_dir,
                                                      self._scenario, label,
                                                      self._budget)
        return app, plain, readonly, budget, snapshot

    def run(self, label):
        """Execute label once (first call) and return its (result, trace);
        every later call returns the SAME pair — the reuse itself."""
        if label not in self.runs:
            app, plain, readonly, budget, snapshot = self.arm_config(label)
            self.runs[label] = frontier_run(
                self._module, app, self._header, self._rom, f'trace_{label}',
                flash_image_bytes=plain, readonly=readonly,
                budget=budget, scenario=self._scenario, snapshot=snapshot)
        return self.runs[label]

    def run_parallel(self, labels, jobs):
        """Route PAR3: execute the given labels in forked worker processes (up
        to `jobs` concurrently) and register each (result, spool) pair in
        self.runs — the three-image conditional round is three INDEPENDENT
        simulations, so its wall clock is the arm count times the per-arm time
        when serialized. Values are unchanged by construction: each worker
        runs the very same frontier_run in a forked copy of this process
        (module state, scenario flags and simulator PIN already imported and
        pinned); the parent only relays the finished artifacts. The worker
        flushes its TraceSpool and writes the spool bytes plus the chunk index
        and the result dict into a scratch directory; the parent re-attaches a
        read-only spool view (same class, same chunk semantics — parent and
        child run one code version, no on-disk format contract). Comparisons
        stay single-process in the parent, exactly as before."""
        import multiprocessing
        context = multiprocessing.get_context('fork')
        scratch = Path(tempfile.mkdtemp(prefix='h3s-par3-'))
        try:
            pending = list(labels)
            running = []  # (process, label, result_path, spool_path, index_path)
            while pending or running:
                while pending and len(running) < jobs:
                    label = pending.pop(0)
                    work = scratch / label
                    work.mkdir()
                    process = context.Process(
                        target=self._arm_worker, args=(label, work),
                        name=f'h3s-arm-{label}')
                    process.start()
                    running.append((process, label, work / 'result.pkl',
                                    work / 'spool.bin', work / 'spool.json'))
                process, label, result_path, spool_path, index_path = running.pop(0)
                process.join()
                require(process.exitcode == 0,
                        f'parallel arm {label!r} failed with exit code '
                        f'{process.exitcode}; its traceback is above')
                result = pickle.loads(result_path.read_bytes())
                self.runs[label] = (result, _reattach_spool(spool_path, index_path))
        finally:
            import shutil
            shutil.rmtree(scratch, ignore_errors=True)

    def _arm_worker(self, label, work):
        # Child process: run one arm exactly like the serial path, then hand
        # the artifacts to the parent. Any exception exits non-zero (the
        # parent requires exitcode 0); stderr is inherited so the traceback
        # surfaces in place.
        app, plain, readonly, budget, snapshot = self.arm_config(label)
        result, trace = frontier_run(
            self._module, app, self._header, self._rom, f'trace_{label}',
            flash_image_bytes=plain, readonly=readonly,
            budget=budget, scenario=self._scenario, snapshot=snapshot)
        trace.flush()
        trace._file.seek(0)
        (work / 'spool.bin').write_bytes(trace._file.read())
        (work / 'spool.json').write_text(json.dumps(
            {'offsets': trace._offsets, 'counts': trace._counts,
             'starts': trace._starts, 'flushed': trace._flushed,
             'tail': trace._tail}))
        with open(work / 'result.pkl', 'wb') as handle:
            pickle.dump(result, handle, protocol=4)

    def close(self):
        for _result, trace in self.runs.values():
            trace.close()
        self.runs.clear()


def load_official(path, _official=None):
    app = application(path)
    image = flash_image(path)
    return app, image, (), OFFICIAL_SHA


def load_b79(path, official_path):
    raw, flash, plain = load_image(path)
    require(hashlib.sha256(raw).hexdigest() == B79_SHA, 'tested_b79 label requires the exact tested ANC/tone image')
    require(verify(path)['passed'], 'tested_b79 CRC failure')
    official_flash = load_image(official_path)[1]
    require(flash[:0x2000] == official_flash[:0x2000], 'tested_b79 bootloader/header differs from official (shared handoff assumption broken)')
    require(len(plain) == PLAIN_LENGTHS['tested_b79'], 'tested_b79 plaintext length changed')
    app = plain[0x100:0x100 + 842654]
    require(hashlib.sha256(app).hexdigest() == B79_APP_SHA, 'tested_b79 application bytes changed')
    return app, plain, (), B79_SHA


def load_eq_candidate(path, official_path):
    app, readonly = candidate_view(path, REPAIRED_SHA, official_path)
    plain = load_image(path)[2]
    require(len(plain) == PLAIN_LENGTHS['eq_fence_repair'], 'eq_fence_repair plaintext length changed')
    require(hashlib.sha256(app).hexdigest() == EQ_APP_SHA, 'eq_fence_repair application bytes changed')
    return app, plain, readonly, REPAIRED_SHA


def _load_candidate_world(name, path, official_path):
    """l1_slotram / r4_keyhook 候选镜像世界入口(candidate-image-cotest
    §1.2)。与 b79/eq 不同,候选不改 app.bin 既有资源逻辑、是完整合法 UFW,
    直接 load_image 解密即可(无需 candidate_view 的 readonly 视图)。

    前置断言单源复用 tools/candidate_world_checks.run_checks(路 BU):
    ①UFW sha 双侧钉死 ②独立校验器 CRC ③共享 bootloader flash[:0x2000]
    逐字节同官方 ④plain 长度+byte-diff 白名单区域分类等式语义(6 窗口/
    fence 立即数/注册表槽 r4/config.dat CRC+index/追加段/两处 app 区 CRC
    对)+构建器字节账 493/574B 对账——任一断言红即拒载,候选世界不开门。
    本 loader 只增量钉 app 切片 sha(实测值,见 CANDIDATE_APP_SHAS 注释)。
    候选镜像保持 UNTESTED;本入口不构成任何刷机或行为验证声明。
    """
    import candidate_world_checks
    report = candidate_world_checks.run_checks(name, path, official_path)
    failed = [a['assertion'] for a in report['assertions'] if not a['passed']]
    require(not failed, f'{name} candidate-world pre-load assertions failed: {failed}')
    raw, flash, plain = load_image(path)
    require(len(plain) == PLAIN_LENGTHS[name], f'{name} plaintext length changed')
    app = plain[0x100:0x100 + 842654]
    require(hashlib.sha256(app).hexdigest() == CANDIDATE_APP_SHAS[name],
            f'{name} application bytes changed')
    sha = {'l1_slotram': L1_SLOTRAM_SHA, 'r4_keyhook': R4_KEYHOOK_SHA}[name]
    return app, plain, (), sha


def load_l1_slotram(path, official_path):
    return _load_candidate_world('l1_slotram', path, official_path)


def load_r4_keyhook(path, official_path):
    return _load_candidate_world('r4_keyhook', path, official_path)


def byte_diff_runs(a, b):
    """Non-overlapping [lo,hi) runs where the two plaintexts differ, plus a
    trailing run for the length extension (appended eq_toggle.bin)."""
    n = min(len(a), len(b))
    runs = []
    i = 0
    while i < n:
        if a[i] != b[i]:
            j = i
            while j < n and a[j] != b[j]:
                j += 1
            runs.append((i, j))
            i = j
        else:
            i += 1
    if len(a) != len(b):
        runs.append((n, max(len(a), len(b))))
    return runs


def pc_run_length_summary(trace, head=12, tail=12):
    """Run-length summary of the fetch-PC sequence. 路 O6:单遍流式聚合
    (计数+前 head 个 run+滚动尾 tail 个 run)——原实现先物化完整 runs
    列表(实测 run 数≈0.97×行数,21.8M 世界 ≈2.7GB 瞬时、50M 外推 ≈6GB,
    叠加在已回收的签名链之上会击穿 <1GB 目标);聚合是纯函数,输出与
    「先建表再裁剪」逐字段恒等(单测钉死)。"""
    run_count = 0
    head_runs = []
    tail_runs = deque()
    for core, before_pc, _next_pc, _flags, _regs, _writes in trace:
        current = tail_runs[-1] if tail_runs else (head_runs[-1] if head_runs else None)
        if current is not None and current[0] == before_pc and current[2] == core:
            current[1] += 1
            continue
        run_count += 1
        run = [before_pc, 1, core]
        if len(head_runs) < head:
            head_runs.append(run)
        else:
            tail_runs.append(run)
            if len(tail_runs) > tail:
                tail_runs.popleft()
    keep_tail = list(tail_runs) if run_count > head + tail else []
    return {'run_count': run_count, 'head': [[hex(pc), count, core] for pc, count, core in head_runs],
            'tail': [[hex(pc), count, core] for pc, count, core in keep_tail],
            'omitted_middle_runs': max(0, run_count - len(head_runs) - len(keep_tail))}


def classify_divergence(step, row_a, row_b, diff_runs, plain_a, plain_b):
    (core_a, pc_a, next_a, flags_a, regs_a, writes_a) = row_a
    (core_b, pc_b, next_b, flags_b, regs_b, writes_b) = row_b
    detail = {'step': step, 'core_a': core_a, 'core_b': core_b,
              'pc_a': hex(pc_a), 'pc_b': hex(pc_b),
              'next_pc_a': hex(next_a), 'next_pc_b': hex(next_b)}
    if core_a != core_b:
        detail['classification'] = 'core_scheduling_divergence'
    elif pc_a != pc_b:
        detail['classification'] = 'control_flow_divergence'
        detail['note'] = 'identical previous step, different next fetch PC: the divergence entered between the two steps (delivery/idle-wake bookkeeping differs)'
    elif next_a != next_b:
        detail['classification'] = 'branch_target_divergence'
        detail['note'] = 'same instruction, different next PC: a data input selected a different branch'
    elif regs_a != regs_b:
        changed = [i for i in range(16) if regs_a[i] != regs_b[i]]
        detail['classification'] = 'register_state_divergence'
        detail['registers'] = {str(i): {'a': regs_a[i], 'b': regs_b[i]} for i in changed}
    elif writes_a != writes_b:
        detail['classification'] = 'memory_write_divergence'
        detail['writes_a'] = [[hex(address), data] for address, data in writes_a]
        detail['writes_b'] = [[hex(address), data] for address, data in writes_b]
    else:
        detail['classification'] = 'flags_divergence'
        detail['flags_a'] = flags_a
        detail['flags_b'] = flags_b
    # root cause against the actual image byte differences: an instruction whose
    # fetched extent [pc, next_pc) overlaps a differing run consumes the image
    # difference directly (e.g. a patched immediate inside a shared opcode).
    flash_base = 0x06000000
    lo_pc = min(pc_a, pc_b) - flash_base
    hi_pc = max(next_a, next_b) - flash_base
    extent_hits = [(lo, hi) for lo, hi in diff_runs if lo < hi_pc and hi > lo_pc]
    if extent_hits:
        lo, hi = extent_hits[0]
        detail['instruction_bytes_differ_within_instruction'] = True
        detail['image_diff_run'] = {'plain_offset': [hex(lo), hex(hi)],
                                    'cpu_range': [hex(flash_base + lo), hex(flash_base + hi)],
                                    'known_site': site_label(lo)}
        detail['instruction_bytes_a'] = plain_a[max(lo_pc, 0):hi_pc].hex() if lo_pc >= 0 else ''
        detail['instruction_bytes_b'] = plain_b[max(lo_pc, 0):hi_pc].hex() if lo_pc >= 0 else ''
        if detail['classification'] == 'register_state_divergence':
            detail['note'] = 'the executed instruction encoding differs between the images inside its immediate operand; the register divergence consumes the patched bytes directly'
    else:
        detail['instruction_bytes_differ_within_instruction'] = False
        detail['note'] = detail.get('note') or (
            'instruction bytes are identical at this PC; the differing input is data loaded earlier '
            'from image-differing memory (flash/app/resource bytes) whose value entered this register '
            'or an earlier one')
    return detail


def json_diff(a, b, path='$', out=None, limit=24):
    if out is None:
        out = []
    if len(out) >= limit:
        return out
    if type(a) is not type(b):
        out.append({'path': path, 'a': _snippet(a), 'b': _snippet(b), 'kind': 'type'})
        return out
    if isinstance(a, dict):
        for key in sorted(set(a) | set(b)):
            if key not in a or key not in b:
                out.append({'path': f'{path}.{key}', 'kind': 'missing',
                            'a': _snippet(a.get(key)), 'b': _snippet(b.get(key))})
            else:
                json_diff(a[key], b[key], f'{path}.{key}', out, limit)
            if len(out) >= limit:
                return out
    elif isinstance(a, list):
        if len(a) != len(b):
            out.append({'path': path, 'kind': 'length', 'a': len(a), 'b': len(b)})
        for i, (x, y) in enumerate(zip(a, b)):
            json_diff(x, y, f'{path}[{i}]', out, limit)
            if len(out) >= limit:
                return out
    elif a != b:
        out.append({'path': path, 'kind': 'value', 'a': _snippet(a), 'b': _snippet(b)})
    return out


def _snippet(value, width=120):
    text = json.dumps(value, sort_keys=True, default=str)
    return text if len(text) <= width else text[:width] + '…'


def _control(row):
    return (row[0], row[1], row[2])


def _report_for_diff(result):
    """Report view for the final-report comparison: strips the run NAME and
    the SNAPSHOT-ROUND transport keys (route SNAPX). 'name' differs by design
    (trace_<label>); snapshot_capture/snapshot_restore are per-run provenance
    of HOW the world was produced (full replay vs capture vs restore), not
    world observation — in a snapshot round BOTH arms carry them and their
    digests differ per image, which would swamp the diff with transport noise.
    The comparison stays symmetric all-or-none: the tool refuses a mixed
    round (some arms resumed, some replayed) at startup."""
    return {k: v for k, v in result.items()
            if k not in ('name', 'snapshot_capture', 'snapshot_restore')}


def compare_pair(image_a, run_a, image_b, run_b, budget, scenario='crc_bankb',
                 resumed=False):
    """Compare one image pair over PRE-EXECUTED runs (route REUSE). Each arm's
    (result, trace) comes from the caller's RunPool: the same run object may
    already have served — or will later serve — other pairs and the freshness
    check. This function is a PURE consumer (read-only over the spools, no
    simulator state) and never closes them: the pool releases every spool
    after the last consumer (route O6 discipline, ownership at the pool)."""
    label_a, (_app_a, plain_a, _readonly_a, sha_a) = image_a
    label_b, (_app_b, plain_b, _readonly_b, sha_b) = image_b
    result_a, trace_a = run_a
    result_b, trace_b = run_b
    for label, result, plain in ((label_a, result_a, plain_a), (label_b, result_b, plain_b)):
        print(label, result['completed_steps'], result['stop_pc'], result['stop']['kind'], flush=True)
    diff_runs = byte_diff_runs(plain_a, plain_b)
    pair = {'images': {label_a: {'ufw_sha256': sha_a, 'flash_plain_sha256': hashlib.sha256(plain_a).hexdigest(),
                                 'flash_plain_bytes': len(plain_a)},
                       label_b: {'ufw_sha256': sha_b, 'flash_plain_sha256': hashlib.sha256(plain_b).hexdigest(),
                                 'flash_plain_bytes': len(plain_b)}},
            'image_byte_diff_runs': [{'plain_offset': [hex(lo), hex(hi)], 'known_site': site_label(lo)}
                                      for lo, hi in diff_runs],
            'shared_stack': SCENARIOS[scenario]['stack_description'],
            'budget': budget,
            'runs': {label_a: _run_summary(result_a), label_b: _run_summary(result_b)},
            'final_report_diffs': json_diff(_report_for_diff(result_a),
                                            _report_for_diff(result_b), limit=64)}
    if resumed:
        # SNAPX 尾巴:resume 轮轨迹面字段(trace_lengths=0/first_divergence=None
        # 等)只反映 restore 之后的步,pair 级就近标记消「字段为 0/None」的
        # 阅读困惑;全量重放/捕获轮报告不含该键(docs/snapshot-resume.md)。
        pair['resumed'] = True
    first = None
    first_control = None
    for step, (row_a, row_b) in enumerate(zip(trace_a, trace_b)):
        if first is None and row_a != row_b:
            first = classify_divergence(step, row_a, row_b, diff_runs, plain_a, plain_b)
        if _control(row_a) != _control(row_b):
            first_control = step
            break
    pair['trace_lengths'] = {label_a: len(trace_a), label_b: len(trace_b)}
    pair['parity'] = stop_parity(label_a, result_a, label_b, result_b)
    if first_control is not None:
        row_a, row_b = trace_a[first_control], trace_b[first_control]
        pair['first_control_divergence'] = {
            'step': first_control, 'core_a': row_a[0], 'core_b': row_b[0],
            'pc_a': hex(row_a[1]), 'next_pc_a': hex(row_a[2]),
            'pc_b': hex(row_b[1]), 'next_pc_b': hex(row_b[2]),
            'registers': {str(j): {'a': row_a[4][j], 'b': row_b[4][j]}
                          for j in range(16) if row_a[4][j] != row_b[4][j]},
            'note': 'identical previous step, different next fetch PC: the diverged control flow started here'}
        pair['reconvergence'] = find_reconvergence(trace_a, trace_b, first_control)
        prefix = trace_a[:first_control]
        pair['common_control_prefix'] = {'steps': first_control, 'summary': pc_run_length_summary(prefix)}
    else:
        pair['control_flow_identical_over'] = {'steps': min(len(trace_a), len(trace_b)),
                                               'summary': pc_run_length_summary(
                                                   trace_a[:min(len(trace_a), len(trace_b))])}
    if first is not None:
        prefix = trace_a[:first['step']]
        first['context_before'] = [[row[0], hex(row[1])] for row in prefix[-6:]]
        first['context_after_a'] = [[row[0], hex(row[1])] for row in trace_a[first['step']:first['step'] + 6]]
        first['context_after_b'] = [[row[0], hex(row[1])] for row in trace_b[first['step']:first['step'] + 6]]
        pair['first_divergence'] = first
        pair['common_prefix'] = {'steps': first['step'], 'summary': pc_run_length_summary(prefix)}
        if first_control is None:
            # 无控制流错位:分歧段(连续不同签名的区间)统计有效
            spans = _divergent_spans(trace_a, trace_b)
            pair['divergent_signature_spans'] = {
                'count': len(spans),
                'first': [{'steps': [s, e], 'pc': hex(trace_a[s][1]),
                           'registers': {str(j): [trace_a[s][4][j], trace_b[s][4][j]]
                                         for j in range(16) if trace_a[s][4][j] != trace_b[s][4][j]},
                           'writes_differ': trace_a[s][5] != trace_b[s][5]}
                          for s, e in spans[:8]]}
            pair['note'] = ('register/memory-signature divergences exist but the CONTROL FLOW never diverged '
                            'and both runs reached the same stop; see divergent_signature_spans for every '
                            'point where an image difference became visible in a compared observable')
    else:
        pair['first_divergence'] = None
        pair['no_divergence_within'] = min(len(trace_a), len(trace_b))
        pair['common_prefix'] = {'steps': min(len(trace_a), len(trace_b)),
                                 'summary': pc_run_length_summary(trace_a[:min(len(trace_a), len(trace_b))])}
        if not pair['final_report_diffs']:
            pair['note'] = ('identical trajectories AND identical final reports up to the shared stop point; '
                            'this only shows the prefix did not consume the image differences into any compared '
                            'observable, it is NOT an image-equivalence proof')
        else:
            pair['note'] = ('identical per-step signatures; the two final reports still differ (stop point, '
                            'peripheral records, counters or snapshots) — a divergence invisible to the '
                            'per-step comparison surfaced only in the report')
    # 路 O6 清理纪律(路 REUSE 更新):报告只携带从行里提取的值;两臂 spool
    # 的释放权在 RunPool——本函数只是纯消费者,close 移到全部消费者(本对+
    # 同臂其他对+freshness)结束之后,由 pool.close() 统一执行。
    return pair


def _divergent_spans(trace_a, trace_b, limit=100000):
    spans = []
    start = None
    for i, (row_a, row_b) in enumerate(zip(trace_a, trace_b)):
        differ = row_a != row_b
        if differ and start is None:
            start = i
        elif not differ and start is not None:
            spans.append((start, i))
            start = None
        if len(spans) > limit:
            break
    if start is not None:
        spans.append((start, min(len(trace_a), len(trace_b))))
    return spans


def _suffix_control_realign_start(trace_a, trace_b, lag, lo):
    """Smallest s >= lo such that _control(trace_a[i]) == _control(trace_b[i+lag])
    for EVERY i in [s, len(trace_a)); None when the tails never match or the
    lag is out of range."""
    if lo + lag < 0 or len(trace_a) + lag > len(trace_b):
        return None
    s = len(trace_a)
    for i in range(len(trace_a) - 1, lo - 1, -1):
        if _control(trace_a[i]) == _control(trace_b[i + lag]):
            s = i
        else:
            break
    return s if s < len(trace_a) else None


def find_reconvergence(trace_a, trace_b, first_control):
    """Reconvergence analysis after a control-flow divergence.

    Tests the exact trace-length lag (len(b) - len(a)) — the only lag under
    which the comparison covers BOTH traces all the way to their stops — and
    asks whether the two CONTROL sequences (core, fetch PC, next PC) are
    identical for the whole remaining suffix from some realignment step on:
    i.e. the divergence inserted (or removed) a bounded number of steps ONCE
    and the control flows never diverged again. On success the remaining
    signature divergences (register state / guest RAM writes) are reported as
    spans under that lag; on failure (permanent divergence, or more than one
    insertion, or trailing unmatched steps) the honest no-realignment fact is
    reported. Pure trace-array logic, no simulator state."""
    lag = len(trace_b) - len(trace_a)
    s = _suffix_control_realign_start(trace_a, trace_b, lag, first_control)
    if s is not None:
        if lag < 0:
            # a is the longer run: the earliest suffix match can slide into a
            # PERIODIC coincidence (e.g. every table-walk round runs the same
            # code with the same step count, so a's diverged round control-
            # matches b's previous round). The semantic realignment localizes
            # the insertion: a's steps from first_control+|lag| on correspond
            # to b's steps from first_control on. The scan result guarantees
            # that candidate also suffix-matches whenever it is >= s.
            semantic = first_control - lag
            if semantic >= s:
                s = semantic
        detail = {'realigned': True, 'lag': lag,
                  'realign_step_a': s, 'realign_step_b': s + lag,
                  'immediate': s == first_control,
                  'control_zero_divergence_steps': len(trace_a) - s,
                  'aligned_to_common_stop': True,
                  'method': ('the two PC sequences match step-for-step under the lag from the realignment '
                             'point to both stops; the diverged run executed the extra steps as one bounded '
                             'insertion and the control flows never diverged again')}
        if lag > 0:
            window = {'run': 'b', 'count': lag, 'steps': [first_control, s + lag]}
            window['exact_position'] = s == first_control
        else:
            window = {'run': 'a', 'count': -lag, 'steps': [first_control, s]}
            window['exact_position'] = s - first_control == -lag
        if not window['exact_position']:
            window['note'] = ('the extra step count is exact but its position inside the bracket is not '
                              'localized (realignment was not at the divergence step)')
        detail['extra_window'] = window
        # 路 O6:原实现把 trace_b 的对齐后缀物化成 list(逐行取索引);在
        # _suffix_control_realign_start 的护栏 len(trace_a)+lag <= len(trace_b)
        # 下,该 list 恰等于切片 trace_b[s+lag:](元素逐一对应、长度相同),
        # 改成惰性切片表示不变、内存不再随分歧后缀长度物化(对真 list 同样
        # 语义等价——切片就是那个 list)。
        spans = _divergent_spans(trace_a[s:], trace_b[s + lag:])
        spans = [(s + lo, s + hi) for lo, hi in spans]
        signature_realign = spans[-1][1] if spans else s
        detail['signature_after_realignment'] = {
            'divergent_span_count': len(spans),
            'divergent_steps': sum(hi - lo for lo, hi in spans),
            'spans_with_differing_writes': sum(
                1 for lo, hi in spans if any(trace_a[i][5] != trace_b[i + lag][5] for i in range(lo, hi))),
            'first': [{'steps': [lo, hi], 'pc_a': hex(trace_a[lo][1]),
                       'registers': {str(j): [trace_a[lo][4][j], trace_b[lo + lag][4][j]]
                                     for j in range(16) if trace_a[lo][4][j] != trace_b[lo + lag][4][j]},
                       'writes_differ': any(trace_a[i][5] != trace_b[i + lag][5] for i in range(lo, hi))}
                      for lo, hi in spans[:8]],
            'full_signature_realign_step_a': signature_realign,
            'full_signature_realign_step_b': signature_realign + lag,
            'full_signature_zero_divergence_steps': len(trace_a) - signature_realign}
        return detail
    return {'realigned': False,
            'note': ('no step lag realigns the two control sequences onto each other up to the stops; '
                     'the traces stay control-diverged to the end and per-step comparison beyond the '
                     'first control divergence only reports that the traces differ, not where')}


def _fmt_addr(address):
    return hex(address) if isinstance(address, int) else str(address)


def _slice_of_differing_registers(regs_a, regs_b, byte_a, byte_b):
    """True when the differing written byte pair is a byte slice of a register
    pair that differs at the same step (both signatures carry the byte at the
    SAME offset): the store transported a register that already carried
    propagated image bytes (verbatim copies and computed values alike — a
    register is the only path a stored data byte ever takes). Every character
    offset is scanned: int signatures hold their bytes at even offsets,
    unknown-word signatures ('u????????|…') at odd offsets."""
    for sig_a, sig_b in zip(regs_a, regs_b):
        if sig_a == sig_b:
            continue
        span = min(len(sig_a), len(sig_b)) - 1
        for offset in range(span):
            if sig_a[offset:offset + 2] == byte_a and sig_b[offset:offset + 2] == byte_b:
                return True
    return False


def write_delta_facts(trace_a, trace_b, plain_a, plain_b):
    """PARSTD storage-delta standard, facts pass: enumerate and explain EVERY
    guest-write difference between two control-aligned runs (same step count,
    identical per-step PCs — the stop/control parity the self-checks pin).

    This closes both coverage gaps of the old report field
    divergent_signature_spans['first'] (it only carried the FIRST 8 divergence
    spans, and per span only its START step): every step whose writes tuple
    differs is enumerated, inside any span and past the first 8.

    A differing written byte pair is EXPLAINED by the image delta only when it
    is a byte slice of a register pair that differs at that same step — a
    store's data bytes always come from a register, so a differing byte at a
    step whose registers are all identical is an INVENTED difference (same
    inputs, same instruction, different outcome: nondeterminism or a model
    bug), never image propagation. The register slice is then classified
    - 'image': the pair ALSO equals a differing image byte pair at some offset
      inside the known image diff runs (byte_diff_runs) — the firmware copied
      differing image bytes into RAM verbatim (rep/word copies), or
    - 'register': computed propagation — the register already carries image
      bytes transformed by earlier arithmetic (e.g. the software CRC
      accumulator byte stored at 0x60106a8: computed from, not equal to, the
      image bytes).
    With the two deterministic runs sharing flags, ROM, control flow and stop
    point, the flash images are the ONLY input difference — so a differing
    written byte that passes NEITHER class is an invented difference, which
    the old 'writes_differ all False' span-start standard could never see.
    The write stream STRUCTURE (write count, addresses, widths per step) must
    match exactly; only data bytes may differ — a structural mismatch is a
    behavioral store divergence.

    Pure trace-array + image logic, no simulator state. Returns
    {'steps', 'cells', 'image_cells', 'register_cells', 'structural',
     'unexplained'}: steps = count of write-differing steps; cells maps
    (address, byte_a, byte_b) -> occurrence count (address per differing
    BYTE, so a differing halfword store yields two adjacent cells);
    image_cells/register_cells partition cells by explanation; structural
    and unexplained list the violations (empty = standard holds)."""
    image_pairs = set()
    for lo, hi in byte_diff_runs(plain_a, plain_b):
        for offset in range(lo, min(hi, len(plain_a), len(plain_b))):
            if plain_a[offset] != plain_b[offset]:
                image_pairs.add((plain_a[offset], plain_b[offset]))
    steps = 0
    cells = {}
    image_cells = {}
    register_cells = {}
    structural = []
    unexplained = []
    for step, (row_a, row_b) in enumerate(zip(trace_a, trace_b)):
        writes_a, writes_b = row_a[5], row_b[5]
        if writes_a == writes_b:
            continue
        steps += 1
        if len(writes_a) != len(writes_b):
            structural.append({'step': step, 'pc': hex(row_a[1]), 'kind': 'write_count',
                               'a': [[_fmt_addr(address), data] for address, data in writes_a],
                               'b': [[_fmt_addr(address), data] for address, data in writes_b]})
            continue
        for (address_a, data_a), (address_b, data_b) in zip(writes_a, writes_b):
            if address_a != address_b or len(data_a) != len(data_b):
                structural.append({'step': step, 'pc': hex(row_a[1]), 'kind': 'store_shape',
                                   'a': [_fmt_addr(address_a), data_a],
                                   'b': [_fmt_addr(address_b), data_b]})
                continue
            for offset in range(0, len(data_a), 2):
                byte_a, byte_b = data_a[offset:offset + 2], data_b[offset:offset + 2]
                if byte_a == byte_b:
                    continue
                cell = (_fmt_addr(address_a)
                        + (f'+{offset // 2}' if offset else ''), byte_a, byte_b)
                cells[cell] = cells.get(cell, 0) + 1
                if not _slice_of_differing_registers(row_a[4], row_b[4], byte_a, byte_b):
                    unexplained.append({'step': step, 'pc': hex(row_a[1]), 'address': cell[0],
                                        'bytes': [byte_a, byte_b]})
                elif (int(byte_a, 16), int(byte_b, 16)) in image_pairs:
                    image_cells[cell] = image_cells.get(cell, 0) + 1
                else:
                    register_cells[cell] = register_cells.get(cell, 0) + 1
    return {'steps': steps, 'cells': cells, 'image_cells': image_cells,
            'register_cells': register_cells, 'structural': structural,
            'unexplained': unexplained}


def writes_explained_by_image_delta(trace_a, trace_b, plain_a, plain_b):
    """PARSTD assertion predicate: [] iff every guest-write difference between
    the two control-aligned runs is explained by the image byte delta — each
    differing byte is a slice of a register pair differing at that step (the
    mechanical transport proof), itself either a verbatim image byte pair or
    a computed propagation — and the store stream is structurally identical.
    Any violation is returned verbatim (structural mismatches first, then
    unexplained differing bytes)."""
    facts = write_delta_facts(trace_a, trace_b, plain_a, plain_b)
    return facts['structural'] + facts['unexplained']


def _require_pinned_write_delta(label, trace_a, trace_b, plain_a, plain_b,
                                pinned_steps, pinned_cells):
    """PARSTD require block shared by the official-vs-b79 self-checks: the
    storage-delta standard must hold with ZERO violations AND the enumerated
    propagation must reproduce the pinned facts exactly (steps + per-cell
    counts), so a new, removed, moved or re-valued storage difference fails
    the check even when it is perfectly image-explained. The pins come from a
    fresh full replay at pin time (route discipline: the frontier advance
    re-pins them)."""
    facts = write_delta_facts(trace_a, trace_b, plain_a, plain_b)
    require(not facts['structural'],
            f'{label}: the write streams diverged structurally (count/address/width) — '
            f'that is a behavioral store divergence, not image-byte propagation: '
            f'{facts["structural"][:3]}')
    require(not facts['unexplained'],
            f'{label}: storage differences NOT explained by the image byte delta '
            f'(not a slice of any register pair differing at that step — an invented '
            f'difference; an image byte-pair match alone transports nothing): '
            f'{facts["unexplained"][:3]}')
    require(facts['steps'] == pinned_steps,
            f'{label}: the image-delta storage propagation step count changed '
            f'({facts["steps"]} != pinned {pinned_steps})')
    require(facts['cells'] == pinned_cells,
            f'{label}: the image-delta storage propagation cells changed '
            f'(now-vs-pinned {sorted(facts["cells"].items())} vs '
            f'{sorted(pinned_cells.items())})')


def stop_parity(label_a, result_a, label_b, result_b):
    """Explicit stop-point parity between the two runs of a pair: step counts,
    per-core steps and the full stop records (kind/PC/address). Same-stop parity
    under the shared stack is the 'both images reach the same honest boundary'
    fact; it is NOT a behavioral-equivalence claim."""
    stop_a, stop_b = result_a['stop'], result_b['stop']
    cores_a = {k: v['steps'] for k, v in result_a['machine']['cores'].items()}
    cores_b = {k: v['steps'] for k, v in result_b['machine']['cores'].items()}
    return {'steps': {label_a: result_a['completed_steps'], label_b: result_b['completed_steps'],
                      'delta': result_b['completed_steps'] - result_a['completed_steps']},
            'per_core_steps': {label_a: cores_a, label_b: cores_b,
                               'delta': {k: cores_b.get(k, 0) - cores_a.get(k, 0)
                                         for k in sorted(set(cores_a) | set(cores_b))}},
            'stop_pc': {'a': result_a['stop_pc'], 'b': result_b['stop_pc'],
                        'same': result_a['stop_pc'] == result_b['stop_pc']},
            'stop_record': {'a': stop_a, 'b': stop_b, 'identical': stop_a == stop_b},
            'same_stop': stop_a == stop_b and result_a['stop_pc'] == result_b['stop_pc']}


def _run_summary(result):
    return {'completed_steps': result['completed_steps'], 'stop_pc': result['stop_pc'],
            'stop': result['stop'],
            'per_core_steps': {k: v['steps'] for k, v in result['machine']['cores'].items()}}


LOADERS = {'official': load_official,
           'tested_b79': lambda path, official: load_b79(path, official),
           'eq_fence_repair': lambda path, official: load_eq_candidate(path, official),
           'l1_slotram': load_l1_slotram,
           'r4_keyhook': load_r4_keyhook}


def check_self(report, arms=None):
    """钉死 2026-11-12 首轮 official vs tested_b79 结论(全量到前沿停点):

    - 双方 289,849 步、同停点 0x60679ba(power_init 内堆信息 0xf27f4b unmapped 读)、
      per-core 步数一致;stop parity 字段(步数/每核/完整停点记录)全同;
    - 控制流(核调度/PC/next_pc)289,849 步零分歧,无 reconvergence 字段;
    - 首个签名分歧 step 155,390 @ 0x6010b16(rep 4 32 资源表头拷贝体首装载
      r2=b[r1++]),r2=0x28 vs 0x88——两镜像资源表头 CRC 字节差异(plain 0x0
      首字节)被运输进寄存器;共 30 个分歧段,每段都是表头 CRC 字节经 rep 拷贝
      /硬件 CRC 引擎计算显形(CRC 读回结果 4 处不同),全部不改变控制流;
    - 存储差异标准(路 PARSTD 升级,替代旧「前 8 段起点 writes_differ 全
      False」——该口径漏检段内 store 与第 9 段以后,且镜像字节复制进 RAM
      本来就合法产生存储差异):全程 51 个写差异步、59 个差异字节 cell
      (58 个=镜像 diff run 字节对的逐字拷贝:资源表头/嵌套 CRC 字与 stream
      表项头 CRC 字被 rep/字拷贝运输进 RAM;1 个=0x19e298 的 b7/fb=共享
      CRC wrapper 0x6000f0c 软件累计器 r2 低字节(0x60106a8 st.b),是对已
      分歧表字节的计算传播,寄存器传输类),写流结构(次数/地址/宽度)两臂
      全同,零未解释差异——需要活的 traces(arms 参数;fresh --self-check
      总是提供;assert_preflight 的纯报告重放无法重验该面,跳过);
    - ANC 键表字节(0x609e510/app.bin 0x9e410)与 stream.bin 音量参数区
      (0x60e2344..)在前缀内零读取(任何装载都会显形为 GPR/写分歧);
    - final report 差异恰好 5 处(4 处 CRC result_reads + 1 处 watch r0)。
    上游前沿推进后按新结论重钉。
    """
    pairs = {p['name']: p for p in report['pairs']}
    require(set(pairs) == {'official_vs_tested_b79'}, 'self-check must run exactly the repository pair')
    p = pairs['official_vs_tested_b79']
    # 前沿停点字面值单点在 frontier_registry(推进前沿只改 registry)。
    base = frontier_registry.frontier('crc_bankb')
    for label in ('official', 'tested_b79'):
        run = p['runs'][label]
        require(run['completed_steps'] == base['completed_steps'] and
                run['stop_pc'] == base['stop_pc'] and
                run['stop']['kind'] == base['stop']['kind'] and
                run['per_core_steps'] == {'0': 278683, '1': 11181},
                f'{label} run changed: the shared-stack prefix is no longer the pinned frontier stop')
    parity = p['parity']
    require(parity['steps'] == {'official': base['completed_steps'],
                                'tested_b79': base['completed_steps'], 'delta': 0} and
            parity['stop_pc'] == {'a': base['stop_pc'], 'b': base['stop_pc'], 'same': True} and
            parity['stop_record']['identical'] and parity['same_stop'] and
            parity['stop_record']['a']['address'] == base['stop']['address'] and
            parity['per_core_steps']['delta'] == {'0': 0, '1': 0},
            'the official-vs-b79 stop parity changed: both images must reach the identical frontier stop')
    require('first_control_divergence' not in p and 'reconvergence' not in p,
            'official vs tested_b79 control flow diverged')
    first = p['first_divergence']
    require(first and first['step'] == 155390 and first['pc_a'] == '0x6010b16' and
            first['classification'] == 'register_state_divergence' and
            first['registers'] == {'2': {'a': '00000028', 'b': '00000088'}} and
            not first['instruction_bytes_differ_within_instruction'],
            'the first official-vs-b79 signature divergence is no longer the resource-table header '
            'CRC byte transported by the rep 4 32 copy at 0x6010b16')
    spans = p['divergent_signature_spans']
    # 路 PARSTD:count 钉值保留;旧「前 8 段起点 writes_differ 全 False」子条件
    # 由下面的写差异传播钉值蕴含(钉住的 59 个 cell 不含任何前 8 段起点步)。
    require(spans['count'] == 30,
            'the official-vs-b79 divergence-span count changed')
    if arms is not None:
        trace_official, plain_official = arms['official']
        trace_b79, plain_b79 = arms['tested_b79']
        _require_pinned_write_delta(
            'official-vs-b79 (crc_bankb)', trace_official, trace_b79,
            plain_official, plain_b79, pinned_steps=51, pinned_cells={
                ('0x1272b8', '28', '88'): 1, ('0x1272b8+1', '33', 'fa'): 1,
                ('0x19e284', '33', 'fa'): 1, ('0x19e298', 'b7', 'fb'): 1,
                ('0x19e2a4', '33', 'fa'): 1, ('0x19e2a8', '33', 'fa'): 1,
                ('0x19e2c0', '28', '88'): 1, ('0x19e2c0', 'a9', 'f2'): 1,
                ('0x19e2c1', '33', 'fa'): 1, ('0x19e2c1', '91', '64'): 1,
                ('0x19e2c2', '66', '7f'): 1, ('0x19e2c2', '7d', 'd2'): 1,
                ('0x19e2c3', '93', '53'): 1, ('0x19e2c3', 'c9', 'a5'): 1,
                ('0x19e2c8', '28', '88'): 2, ('0x19e2c8+1', '33', 'fa'): 1,
                ('0x19e2cc', '33', 'fa'): 2,
                ('0x19e304', '90', '41'): 1, ('0x19e305', 'c1', '7a'): 1,
                ('0x19e306', '9b', 'bd'): 1, ('0x19e307', 'a2', '77'): 1,
                ('0x19e350', '28', '88'): 1, ('0x19e351', '33', 'fa'): 1,
                ('0x19e352', '7d', 'd2'): 1, ('0x19e353', '93', '53'): 1,
                ('0x19e358', '28', '88'): 1, ('0x19e359', '33', 'fa'): 1,
                ('0x19e35a', '7d', 'd2'): 1, ('0x19e35b', '93', '53'): 1,
                ('0x19e35c', '28', '88'): 3, ('0x19e35d', '33', 'fa'): 3,
                ('0x19e35e', '7d', 'd2'): 3, ('0x19e35f', '93', '53'): 3,
                ('0x19e370', '33', 'fa'): 2, ('0x19e374', '33', 'fa'): 1,
                ('0x19e37c', '28', '88'): 1, ('0x19e37c', '33', 'fa'): 1,
                ('0x19e37c', 'a9', 'f2'): 1, ('0x19e37c+1', '33', 'fa'): 1,
                ('0x19e37c+1', '91', '64'): 1,
                ('0x19e3d8', '90', '41'): 1, ('0x19e3d8+1', 'c1', '7a'): 1,
                ('0x19e3e0', '90', '41'): 1, ('0x19e3e0+1', 'c1', '7a'): 1,
                ('0x19e3fc', '90', '41'): 1, ('0x19e3fd', 'c1', '7a'): 1,
                ('0x19e3fe', '9b', 'bd'): 1, ('0x19e3ff', 'a2', '77'): 1})
    diffs = sorted(d['path'] for d in p['final_report_diffs'])
    require(diffs == sorted(['$.milestones[23].r0',
                             '$.peripherals.hardware_crc_projection.result_reads[1]',
                             '$.peripherals.hardware_crc_projection.result_reads[8]',
                             '$.peripherals.hardware_crc_projection.result_reads[15]',
                             '$.peripherals.hardware_crc_projection.result_reads[22]']),
            'the official-vs-b79 final-report differences changed: ' + str(diffs))


def check_self_conditional(report, arms=None):
    """钉死 2026-11-15 路 JLA 条件场景结论(official vs
    tested_b79,条件 flag 集到当前条件前沿 28,764,184;一切结论条件通过、
    derived-not-measured;历史世界 689,402(路 BS)/447,383(路 BY)/601,880
    (路 CB)/3,070,626(路 CG)/3,070,786(路 CL)/3,070,955(路 CO)/
    3,071,380(路 CQ)/3,071,984(路 F)/3,072,236(路 M)/3,073,402(路 R)/
    3,073,918(路 V)/3,079,241(路 AA)/3,081,001(路 AK)/3,137,573(路 AN)/
    3,170,362(路 AS)/3,170,379(路 CV)/3,202,148(路 CW)/3,747,578(路 B1)/
    3,747,585(路 BQ)/19,139,239(路 B3)/21,798,206(路 DT)/21,798,257
    (路 ADA)/21,800,325(路 LPV)已被后续投影替换,见 HANDOFF 对应
    「步含义」段):

    - 双方 32,737,356 步、同停点 0x12c5ca(watchdog_expired_delivery_
      not_implemented,当前条件前沿——路 GPCNUM 具名值放行后的新边界:
      稳态测量任务 0x6069538 的 GPCNT 转换经 fixture(assumed 标称
      32768 Hz 恒等计数 15000)服务满 20 次(20 槽窗口填满,fill==20
      触发 epilogue 的 h[0x12A3DC] 消费+重启链),park 稳态继续推进至
      看门狗 CON 0x2e(time_code=14,周期 16384 名义单位)到期——
      到期交付(硬件复位/异常)不发明,诚实停;喂狗邮箱此前 11 次事务
      全闭合,第 12 次未在到期前到来,归下一路)。历史 29,601,125 @
      0x606955c(路 JLEQ 批量放行后的旧边界:unmapped_memory 读
      0xfd1104=JL_GPCNT->NUM __RO 计数值,稳态测量任务 0x6069538 首次
      转换的测量值读——路 GPCNUM 三选一 (a) 具名 fixture 放行;其前
      park 稳态已建立(11 次喂狗事务闭合/事件钟 14,953,935 虚拟步/
      CPU0 1791+57 次 0x12c5ca parked 唤醒)。历史 29,215,313 @ 0x6072b92
      (路 RAND jl_rand_seed 具名 fixture 放行
      后的旧边界:JL_RAND->R64L 0xfd3b00 映射为 readonly 单字 0x12345678
      (synthetic-not-measured,熵机制无文档值不可推导;R64H 0xfd3b04 零触达
      不进容器),MAC 生成两调用点(0x6011c64/0x6011c92)真实走通(6 字节
      MAC 31 54 23 93 fe eb 非退化;首写后重读仍=擦除 FF 哨兵→第二次
      再生成+再写),链 +324,611 步停在 JL_EQ->CON0(hs_base+map_adr
      (0x15,0),br28.h __RW,值 0=EQ 关闭配置)——音频域外设配置写族;
      历史 28,890,702 @ 0x6011976(路 FLS ancif_official_resources 放行
      后的新边界:sdfile flash 顶 ANCIF/ANCIF1 文件头=官方 UFW 外层资源
      内容具名 fixture(显式假设=目标分区携带官方资源内容,非设备实测),
      两文件 parse 真实走通(魔数/头内名/内层 CRC 全过、free 路径零执行,
      硬件 CRC 引擎新校验值恰=两文件存储 CRC 0x583d/0x4dcd)后 ANC 系数
      装载 +126,518 步,停在随机种子函数的 JL_RAND 族 0xfd3b00 首读
      (Lehmer MINSTD LCG 种子,硬件熵源——值不可推导,三选一按
      lrct_trim_result 探路值先例归下一路));
      历史 28,764,184(路 ASS 放行后的 flash 顶指针首读,值来源归 FLS
      已解:app 分区表 0x06000080 项 virt 经 0x6010c20 换算对
      [0x127290]=0x2020/[0x127294]=0x06000000 的文件头地址,内容=UFW
      外层 type50 资源)、21,800,455
      (路 JLA 放行 set_buffer 五写后的 JL_ASS MIC_CTL0 RMW 内部读停点,
      r0=r5+976 间接构造、li 枚举法免疫——间接构造地址的首个停点实证,
      基址+偏移传播解析法〔356 li 锚点全 GPR 常量跟踪+寄存器间传播,
      ~/tmp/ass-resolve.py,codex 独立复跑互证〕证实四字 {0xfe0904
      MIC_CTL0/0xfe0908 MIC_CTL1/0xfe0554 MBG_CON/0xfe055c ADC_CON1}
      访问面全部配置 RMW/insert 写回组装形状、读值零分支消费,codex 定调
      选 (a) 合成配置读回容器恰四字,0xfe0900/0xfe0558 留 AK、SRN
      0xfe0550 留 unmapped、零触达 0xfe090c/0xfe0910/0xfe0560 不进;放行
      后容器 9 写〔MIC_CTL0 0x2/0x2、MIC_CTL1 终值 0xff8=codex 静态预测、
      ADC_CON1 三写全 0、MBG_CON 门控关零写〕+3 合成初始读+6 写后读,链
      再 +6,963,729 步〔音频 pipeline 多轮重配:AS ADC_CON 1→12 写、B3
      0xfe0a0c +23、ADA +14、CV 0x33/0x73 PGA 切换、DT TM ×4、CRC +8 轮
      65405 字节、core tick 116→140、Timer0 3671→4401;事件时钟冻结
      13,429,113 不变=纯 CPU0 顺序段,per-core {0:+6,964,483,1:0}〕停在
      0x606c5ba 资源查找链首读 0x61fbfe0=2MB flash 顶-0x4020〔SFC 明文区
      0xe6000 外,构造链 0x600B15C 族写栈记录 +8 槽〕——flash 顶参数/
      资源区指针读族,归下一路三选一;静态访问面(全 app
      逐 2 字节独立解码枚举 li 常量+每站点线性窗数据流解析+数据区字面值
      扫描,codex 独立复跑互证)证实 set_buffer 0x606dee4 五写同基本块连续
      (0x606df20..30,首写=停点并行对 store 半)、五字 {0xfe053c/40/44/
      0x61fbfe0=2MB flash 顶-0x4020〔SFC 明文区 0xe6000 外,构造链
      0x600B15C 族写栈记录 +8 槽〕——flash 顶参数/资源区指针读族,归
      下一路三选一;函数全图存档 ~/tmp/ass-fn-0x606c54e.txt;历史停点
      21,800,455(路 JLA 放行 set_buffer 五写后的 JL_ASS MIC_CTL0 RMW
      内部读)与 21,800,325(路 LPV 单字容器后的 JL_AUDIO 数字域写族边界,
      详见 HANDOFF「已实现」对应条目)、per-core {0:28757497,1:11251}
      一致;stop parity
      全同——official↔b79 parity 延长到条件前沿 28,764,184,控制流零
      分歧、无 reconvergence;
    - 首个签名分歧仍 step 155,390 @ 0x6010b16(资源表头 CRC 字节 r2=0x28 vs
      0x88,与 crc_bankb 前沿完全一致);条件前缀新增步(601,880..28,764,184,
      超时窗口+OS tick 级联+mode 切换体+scheduler helper+uart_init 写侧+
      GPIO TX/RX 复用链+Timer0 交付往返+JL_IMAP 输入复用链+fifo_reset+
      uart_init 收尾+init 链前三项+JL_WL_AUD entry 写+场景 walker 查名链+
      跨页拆分后的完整 5×walker 链+ADC reset/S1..S5/音频 init 尾块+
      DAA_CON1 RMW→DAC_VL0 setter 入口→DAC_COP/DMA 写域→延时等待族+
      CNT held 快照+JL_ADDA 模拟族 RMW 群+模拟 init 尾段 OS 等待族+
      DAC_TM 两次 -600 半字写+DAC 启动尾段分配与 OS 等待族续段+ADA 九字
      配置段+51 步+LPVAD 单字 RMW+ADA 窗尾段/tbh case 体/缓冲配置 walk
      2,068 步+JLA set_buffer 五写/挂 handler/启动段序章 130 步+ASS 四字
      容器放行后的音频 pipeline 重配段 6,963,729 步)没有消费任何新的
      镜像差异(CRC 轮数 32/结果 82/65405 字节——路 ASS 段 +8 轮全部
      走共享 0x6000f0c wrapper 硬件路径,新增 8 值组 0xa1cd/0xb37d/
      0xa11d/0xc190/0xf271/0xb758/0x5ddd/0x370 与既有六值循环同构);
    - final report 差异共 30 处(29 处 CRC result_reads + 1 处 milestones[23]
      r0;路 AN 的 5 处=walker 链新 CRC 轮序号 37/43/49/55/61,路 AS 再加 2
      处=尾块新 CRC 轮序号 67/73,路 FLS 再加 8 处=77/83/97/103/109/115/
      121/127,路 RAND 再加 9 处=130/137/147/154/161/168/175/182/189,路
      FD610C 段到 result_reads[197] 的差异有真实镜像数据解释——30 字节
      窗口 0x60e0a2e..0x60e0a4c=stream.bin 表项头(official 66 c9 / b79
      7f a5,独立 crc_hqx 得 0x91a9/0x64f2);该项在 STOREG3 基线/关闭臂/
      开启臂的 official 报告里完全相等(均为 204 次结果读取),第 197 项
      早已存在、不是本路新增,不能归因于 +503 步;其动态源地址的完整
      记录留 TODO(后续重建 base/head 基线时补)。spans 147→151(+4 同属
      寄存器值分歧面,首分歧仍 155,390、控制流全同构);旧 writes_differ
      只查前 8 段起点,段内 store 与第 9 段以后未纳入——镜像字节复制进
      RAM 自然产生存储差异,不能据此断言存储字节零差异或证明模型有 bug
      (路 PARSTD 已升级:全程写差异枚举+镜像字节解释+钉值,见下方
      require 块——251 个写差异步、136 个差异 cell 共 287 次出现,286 次
      =镜像 diff run 字节对的逐字拷贝(资源表头/嵌套 CRC 字、stream 表项
      头 CRC 字与音量参数区字节被 rep/字/半字拷贝运输进 RAM,含条件前缀
      新增的音量参数区 00/f6、00/ff 族与表项头 a9/f2、91/64、66/7f、c9/a5
      族的多次重拷),1 个=0x19e298 的 b7/fb=共享 CRC wrapper 0x6000f0c
      软件 CRC 累计器 r2 低字节(0x60106a8 st.b,计算传播,寄存器传输类),
      写流结构(次数/地址/宽度)两臂全同,零未解释差异;需要活的 traces
      (arms 参数;fresh --self-check 总是提供;assert_preflight 的纯报告
      重放无法重验该面,跳过))。
    条件前沿推进后按新前沿重钉(前沿前移后可重跑跟进)。
    """
    pairs = {p['name']: p for p in report['pairs']}
    require(set(pairs) == {'official_vs_tested_b79'},
            'conditional self-check must run exactly the repository pair')
    p = pairs['official_vs_tested_b79']
    # 条件前沿停点字面值单点在 frontier_registry(推进前沿只改 registry)。
    conditional = frontier_registry.frontier('p11_heap_experimental')
    for label in ('official', 'tested_b79'):
        run = p['runs'][label]
        require(run['completed_steps'] == conditional['completed_steps'] and
                run['stop_pc'] == conditional['stop_pc'] and
                run['stop']['kind'] == conditional['stop']['kind'] and
                run['per_core_steps'] == {'0': 41855953, '1': 11496},
                f'{label} run changed: the conditional flag set no longer reaches the pinned '
                'conditional frontier stop (41850980 @ 0x60195f8, the unmapped '
                'write of JL_PLED CON1 0xfd6104 — reached after the route-STOREG3 standalone '
                'postincrement word-store release executes the byte-unpack '
                'st.w.pin.l of 0x60649b0 directly (crossing the route-WDFEED '
                '41,828,530 bookkeeping-read stop) and the route-FD610C single-'
                'word JL_PLED CON3 container serves the 0x6019458 clear-bit5 '
                'RMW read as synthetic 0 (derived-not-measured), then the '
                'helper returns and +503 pure-CPU0 steps walk into the CON0 '
                'save-restore read; the route-FD610C-con0 JL_PLED CON0 named '
                'whole-word initial fixture (0x00000000 main arm, codex '
                'three-way (a), derived-not-measured) serves the 0x60194b4 '
                'read as 0, the clear-bit0 RMW writes 0, the if(r6&1) restore '
                'is skipped on the false branch, the save-restore returns, '
                'and +576 pure-CPU0 steps walk into the 0x6019502 helper whose '
                'first [0xf2a610]&=0xFFFFFF1F RMW internal read is served by '
                'the route-P2MCLK P2M_CLK_CON0 single-word config-RMW '
                'container (SDK macro LED_CLK_SEL(3), codex three-way (a), '
                'synthetic initial 0 + last-write RMW-internal readback, '
                'explicit loads refused, derived-not-measured), the second '
                'leg writes 0x60, the RAM flag is set, the one-shot LED-init '
                'caller (0x601a30c arm) walks the per-channel reconfig into '
                'the 0x601a41c pend window (IPMASK 0->6) that calls the LED '
                'reset helper 0x60195e2 whose CON0 '
                'clears and zero store are served, and +2,598 pure-CPU0 '
                'steps walk into the 0x60195f8 st.w [0xfd6104]=0 which stops '
                'honestly on the unmapped JL_PLED CON1 word; per-core 1 '
                'unchanged at 11496 — the 0xfd6104 decision belongs to the '
                'next route)')
    parity = p['parity']
    require(parity['steps'] == {'official': conditional['completed_steps'],
                                'tested_b79': conditional['completed_steps'], 'delta': 0} and
            parity['stop_pc'] == {'a': conditional['stop_pc'], 'b': conditional['stop_pc'],
                                  'same': True} and
            parity['stop_record']['identical'] and parity['same_stop'] and
            parity['stop_record']['a']['kind'] == conditional['stop']['kind'] and
            parity['per_core_steps']['delta'] == {'0': 0, '1': 0},
            'the official-vs-b79 conditional stop parity changed: both images must reach the '
            'identical conditional frontier stop')
    require('first_control_divergence' not in p and 'reconvergence' not in p,
            'official vs tested_b79 control flow diverged under the conditional flag set')
    first = p['first_divergence']
    require(first and first['step'] == 155390 and first['pc_a'] == '0x6010b16' and
            first['classification'] == 'register_state_divergence' and
            first['registers'] == {'2': {'a': '00000028', 'b': '00000088'}} and
            not first['instruction_bytes_differ_within_instruction'],
            'the first official-vs-b79 conditional signature divergence is no longer the '
            'resource-table header CRC byte at 0x6010b16 (it must stay identical to the '
            'crc_bankb-scenario divergence)')
    spans = p['divergent_signature_spans']
    # 路 PARSTD:count 钉值保留;旧「前 8 段起点 writes_differ 全 False」子条件
    # 由下面的写差异传播钉值蕴含(钉住的 136 个 cell 不含任何前 8 段起点步)。
    require(spans['count'] == 151,
            'the official-vs-b79 conditional divergence-span count changed')
    if arms is not None:
        trace_official, plain_official = arms['official']
        trace_b79, plain_b79 = arms['tested_b79']
        _require_pinned_write_delta(
            'official-vs-b79 (p11_heap_experimental)', trace_official, trace_b79,
            plain_official, plain_b79, pinned_steps=251, pinned_cells={
                ('0x1272b8', '28', '88'): 1, ('0x1272b8+1', '33', 'fa'): 1, ('0x18c580', 'a9', 'f2'): 1, ('0x18c581', '91', '64'): 1,
                ('0x18c582', '66', '7f'): 1, ('0x18c583', 'c9', 'a5'): 1, ('0x18c82c', 'a9', 'f2'): 2, ('0x18c82d', '91', '64'): 2,
                ('0x18c82e', '66', '7f'): 2, ('0x18c82f', 'c9', 'a5'): 2, ('0x18d004', 'a9', 'f2'): 3, ('0x18d004+1', '91', '64'): 3,
                ('0x18d028', 'a9', 'f2'): 3, ('0x18d029', '91', '64'): 3, ('0x18d02a', '66', '7f'): 3, ('0x18d02b', 'c9', 'a5'): 3,
                ('0x18d050', '28', '88'): 2, ('0x18d051', '33', 'fa'): 2, ('0x18d052', '7d', 'd2'): 2, ('0x18d053', '93', '53'): 2,
                ('0x18d05c', 'a9', 'f2'): 1, ('0x18d05c+1', '91', '64'): 1, ('0x18d0a4', 'a9', 'f2'): 7, ('0x18d0a4+1', '91', '64'): 7,
                ('0x18d0b8', 'a9', 'f2'): 3, ('0x18d0b8+1', '91', '64'): 3, ('0x18d0c8', 'a9', 'f2'): 7, ('0x18d0c9', '91', '64'): 7,
                ('0x18d0ca', '66', '7f'): 7, ('0x18d0cb', 'c9', 'a5'): 7, ('0x18d0d8', '90', '41'): 2, ('0x18d0d8+1', 'c1', '7a'): 2,
                ('0x18d0dc', 'a9', 'f2'): 3, ('0x18d0dd', '91', '64'): 3, ('0x18d0de', '66', '7f'): 3, ('0x18d0df', 'c9', 'a5'): 3,
                ('0x18d0fc', '90', '41'): 2, ('0x18d0fd', 'c1', '7a'): 2, ('0x18d0fe', '9b', 'bd'): 2, ('0x18d0ff', 'a2', '77'): 2,
                ('0x18d290', 'a9', 'f2'): 4, ('0x18d291', '91', '64'): 4, ('0x18d292', '66', '7f'): 4, ('0x18d293', 'c9', 'a5'): 4,
                ('0x18d36c+3', 'd3', 'c9'): 1, ('0x18d374', '00', 'f6'): 1, ('0x18d374+1', '00', 'ff'): 1, ('0x18d374+2', '00', 'ff'): 1,
                ('0x18d374+3', '00', 'ff'): 1, ('0x19d644', 'a9', 'f2'): 7, ('0x19d645', '91', '64'): 7, ('0x19d646', '66', '7f'): 7,
                ('0x19d647', 'c9', 'a5'): 7, ('0x19e08c', '90', '41'): 1, ('0x19e08c+1', 'c1', '7a'): 1, ('0x19e094', '90', '41'): 1,
                ('0x19e095', 'c1', '7a'): 1, ('0x19e096', '9b', 'bd'): 1, ('0x19e097', 'a2', '77'): 1, ('0x19e0b0', '90', '41'): 1,
                ('0x19e0b1', 'c1', '7a'): 1, ('0x19e0b2', '9b', 'bd'): 1, ('0x19e0b3', 'a2', '77'): 1, ('0x19e0d0', 'a9', 'f2'): 1,
                ('0x19e0d0+1', '91', '64'): 1, ('0x19e0e8', '28', '88'): 1, ('0x19e0e9', '33', 'fa'): 1, ('0x19e0ea', '7d', 'd2'): 1,
                ('0x19e0eb', '93', '53'): 1, ('0x19e0f4', 'a9', 'f2'): 1, ('0x19e0f5', '91', '64'): 1, ('0x19e0f6', '66', '7f'): 1,
                ('0x19e0f7', 'c9', 'a5'): 1, ('0x19e170', '90', '41'): 1, ('0x19e170+1', 'c1', '7a'): 1, ('0x19e284', '33', 'fa'): 1,
                ('0x19e298', 'b7', 'fb'): 1, ('0x19e2a4', '33', 'fa'): 1, ('0x19e2a8', '33', 'fa'): 1, ('0x19e2c0', '28', '88'): 1,
                ('0x19e2c0', 'a9', 'f2'): 1, ('0x19e2c1', '33', 'fa'): 1, ('0x19e2c1', '91', '64'): 1, ('0x19e2c2', '66', '7f'): 1,
                ('0x19e2c2', '7d', 'd2'): 1, ('0x19e2c3', '93', '53'): 1, ('0x19e2c3', 'c9', 'a5'): 1, ('0x19e2c8', '28', '88'): 2,
                ('0x19e2c8+1', '33', 'fa'): 1, ('0x19e2cc', '33', 'fa'): 2, ('0x19e304', '28', '88'): 1, ('0x19e304', '90', '41'): 1,
                ('0x19e305', '33', 'fa'): 1, ('0x19e305', 'c1', '7a'): 1, ('0x19e306', '7d', 'd2'): 1, ('0x19e306', '9b', 'bd'): 1,
                ('0x19e307', '93', '53'): 1, ('0x19e307', 'a2', '77'): 1, ('0x19e350', '28', '88'): 1, ('0x19e351', '33', 'fa'): 1,
                ('0x19e352', '7d', 'd2'): 1, ('0x19e353', '93', '53'): 1, ('0x19e358', '28', '88'): 1, ('0x19e359', '33', 'fa'): 1,
                ('0x19e35a', '7d', 'd2'): 1, ('0x19e35b', '93', '53'): 1, ('0x19e35b', 'd3', 'c9'): 1, ('0x19e35c', '28', '88'): 3,
                ('0x19e35d', '33', 'fa'): 3, ('0x19e35e', '7d', 'd2'): 3, ('0x19e35f', '93', '53'): 3, ('0x19e360', '00', 'f6'): 1,
                ('0x19e361', '00', 'ff'): 1, ('0x19e362', '00', 'ff'): 1, ('0x19e363', '00', 'ff'): 1, ('0x19e370', '33', 'fa'): 2,
                ('0x19e374', '33', 'fa'): 1, ('0x19e37c', '28', '88'): 1, ('0x19e37c', '33', 'fa'): 1, ('0x19e37c', 'a9', 'f2'): 1,
                ('0x19e37c+1', '33', 'fa'): 1, ('0x19e37c+1', '91', '64'): 1, ('0x19e398', 'a9', 'f2'): 6, ('0x19e398+1', '91', '64'): 6,
                ('0x19e3bc', 'a9', 'f2'): 6, ('0x19e3bd', '91', '64'): 6, ('0x19e3be', '66', '7f'): 6, ('0x19e3bf', 'c9', 'a5'): 6,
                ('0x19e3d8', '90', '41'): 1, ('0x19e3d8+1', 'c1', '7a'): 1, ('0x19e3e0', '90', '41'): 1, ('0x19e3e0+1', 'c1', '7a'): 1,
                ('0x19e3fc', '90', '41'): 1, ('0x19e3fd', 'c1', '7a'): 1, ('0x19e3fe', '9b', 'bd'): 1, ('0x19e3ff', 'a2', '77'): 1})
    diffs = sorted(d['path'] for d in p['final_report_diffs'])
    require(diffs == sorted(['$.milestones[23].r0',
                             '$.peripherals.hardware_crc_projection.result_reads[1]',
                             '$.peripherals.hardware_crc_projection.result_reads[8]',
                             '$.peripherals.hardware_crc_projection.result_reads[15]',
                             '$.peripherals.hardware_crc_projection.result_reads[22]',
                             '$.peripherals.hardware_crc_projection.result_reads[37]',
                             '$.peripherals.hardware_crc_projection.result_reads[43]',
                             '$.peripherals.hardware_crc_projection.result_reads[49]',
                             '$.peripherals.hardware_crc_projection.result_reads[55]',
                             '$.peripherals.hardware_crc_projection.result_reads[61]',
                             '$.peripherals.hardware_crc_projection.result_reads[67]',
                             '$.peripherals.hardware_crc_projection.result_reads[73]',
                             '$.peripherals.hardware_crc_projection.result_reads[77]',
                             '$.peripherals.hardware_crc_projection.result_reads[83]',
                             '$.peripherals.hardware_crc_projection.result_reads[97]',
                             '$.peripherals.hardware_crc_projection.result_reads[103]',
                             '$.peripherals.hardware_crc_projection.result_reads[109]',
                             '$.peripherals.hardware_crc_projection.result_reads[115]',
                             '$.peripherals.hardware_crc_projection.result_reads[121]',
                             '$.peripherals.hardware_crc_projection.result_reads[127]',
                             '$.peripherals.hardware_crc_projection.result_reads[130]',
                             '$.peripherals.hardware_crc_projection.result_reads[137]',
                             '$.peripherals.hardware_crc_projection.result_reads[147]',
                             '$.peripherals.hardware_crc_projection.result_reads[154]',
                             '$.peripherals.hardware_crc_projection.result_reads[161]',
                             '$.peripherals.hardware_crc_projection.result_reads[168]',
                             '$.peripherals.hardware_crc_projection.result_reads[175]',
                             '$.peripherals.hardware_crc_projection.result_reads[182]',
                             '$.peripherals.hardware_crc_projection.result_reads[189]',
                             '$.peripherals.hardware_crc_projection.result_reads[197]']),
            'the official-vs-b79 conditional final-report differences changed: ' + str(diffs))


def check_self_candidate(report, label, scenario):
    """钉死 2026-11-15 路 DIF 首轮 official↔candidate 差分基线结论(独立于
    2026-11-12 的 official↔b79 钉子,candidate-image-cotest §1.2「新镜像对
    走独立 self-check」)。UNTESTED 候选的差分回归基线,不构成行为验证:

    crc_bankb 档(official vs l1_slotram,全量到前沿停点):
    - official 289,849 @ 0x60679ba(前沿钉点,frontier_registry 单源),
      l1_slotram 290,307 @ 0x60679ba(+458 步轴差,config.dat 多枚举一轮
      的 +458 族);停点记录逐字段相同(same_stop,同 kind 同 PC);
    - 首个签名分歧 step 116,148 @ 0x6034e98:r1=0x060c3338 vs 0x060e31e0
      (fence 上限立即数差,指令编码内差,P1/EQ 同款);
    - 首个控制流分歧 step 169,439 @ 0x6011154(config.dat JLFS 枚举段),
      reconvergence realigned lag=+458 immediate(一步重合,EQ 同款);
    - 表尾跨 4KiB 对齐桶派生族(l1 表尾 0xE31F8 对齐 0xE5000,与 official
      表尾 0xE3053 的 0xE3000 桶差 0x2000,BT §1.4 新观测项):实测形状
      与 EQ 同构——残余签名差持续 63,290 步,全签名重合于 step 232,729,
      无流入基址计算的控制流差(新形状未出现);
    - 差异游程 12 条全部命中已知区(白名单分类账在 loader 前置断言把关)。
    条件档(p11_heap_experimental,2026-11-15 路 DIF 首轮同日钉):
    - official 21,798,206 @ 0x606b5ee(条件前沿钉点)、l1 21,798,104 @
      0x606b5ee(-102 步轴差——C0(core-tick 首溢出交付,official 447,512/
      l1 447,490,δ₁=22)后交付相位彩票把等待域伸缩为 -102 尾段重锁),
      停点记录逐字段相同;前两个分歧锚(116,148 fence/169,439 config.dat)
      与 crc_bankb 档逐字段一致;reconvergence realigned lag=-102 于
      19,123,794 重锁,其后控制流零分歧到双停点(DIF 首轮实测 2,674,412
      步;停点步数与零分歧步数跟 frontier_registry 条件前沿单源派生
      label=base−102/czd=base−19,123,794,前沿推进自动跟随,路 L1FIX;
      偏移对的彩票预期形状,非回归;三层清单判定见
      docs/acceptance-samples/cot/R2-COT-01.json 与
      analysis/local-checks/difline/)。
    上游前沿推进后按新结论重钉(两条件档的停点/czd 除外——单源跟随)。
    r4_keyhook 走独立钉值函数(路 R4SNAP,
    R4-COT-01 归档报告实测值;见 _check_self_candidate_r4_* docstring)。
    """
    pairs = {p['name']: p for p in report['pairs']}
    require(set(pairs) == {f'official_vs_{label}'},
            f'candidate self-check must run exactly the official:{label} pair')
    p = pairs[f'official_vs_{label}']
    base = frontier_registry.frontier('crc_bankb' if scenario == 'crc_bankb'
                                      else 'p11_heap_experimental')
    run = p['runs'][label]
    require(p['runs']['official']['completed_steps'] == base['completed_steps'] and
            p['runs']['official']['stop_pc'] == base['stop_pc'],
            'official run changed: the shared-stack prefix is no longer the pinned frontier stop')
    if label == 'r4_keyhook':
        tier = (_check_self_candidate_r4_crc if scenario == 'crc_bankb'
                else _check_self_candidate_r4_conditional)
    elif scenario == 'crc_bankb':
        tier = _check_self_candidate_crc
    else:
        tier = _check_self_candidate_conditional
    tier(p, label, base)
    report['candidate_self_check_passed'] = True


def _check_self_candidate_crc(p, label, base):
    require(p['parity']['steps'] == {'official': base['completed_steps'],
                                     label: 290307, 'delta': 458} and
            p['parity']['same_stop'] and p['parity']['stop_record']['identical'] and
            p['parity']['stop_pc'] == {'a': base['stop_pc'], 'b': base['stop_pc'], 'same': True},
            'the official-vs-l1_slotram crc_bankb stop parity changed (+458 same-stop expected)')
    first = p['first_divergence']
    require(first and first['step'] == 116148 and first['pc_a'] == '0x6034e98' and
            first['classification'] == 'register_state_divergence' and
            first['registers'] == {'1': {'a': '060c3338', 'b': '060e31e0'}} and
            first['instruction_bytes_differ_within_instruction'],
            'the first official-vs-l1 signature divergence is no longer the fence immediate at 0x6034e98')
    control = p['first_control_divergence']
    require(control and control['step'] == 169439 and
            control['pc_a'] == '0x6011154' and control['pc_b'] == '0x6011154',
            'the first official-vs-l1 control divergence is no longer the config.dat enumeration at 0x6011154')
    rec = p['reconvergence']
    require(rec and rec['realigned'] and rec['lag'] == 458 and rec['immediate'],
            'the official-vs-l1 reconvergence is no longer the immediate +458 realignment')
    require(rec['signature_after_realignment']['full_signature_realign_step_a'] == 232729,
            'the table-tail derived-family survival changed (expected full signature realignment '
            'at step 232729, the EQ-shaped 63,290-step span; a cross-alignment-bucket new shape '
            'must be re-adjudicated, not silently accepted)')


def _check_self_candidate_conditional(p, label, base):
    """条件档钉死(2026-11-15 路 DIF 首轮):+458 偏移对过 C0 后被 core-tick
    交付相位彩票消费(相位重排),尾段重锁到恒定错位 -102(等待域伸缩把
    +458 磨成 -102),控制流在 19,123,794 重锁后零分歧到双停点;停点记录
    逐字段相同(DIF 首轮 0x606b5ee 读 0xfe0a20);C0=official 447,512/l1
    447,490(δ₁=22,A1 b79↔EQ 同值——+458 偏移族 C0 相位差同构)。
    停点步数与 czd 不写死字面、跟 frontier_registry 条件前沿单源
    (label=base−102、czd=base−19,123,794;−102/−103/19,123,794 为结构
    恒量——DIF 21,798,206/R4C 21,798,257 双前沿实测恒定,前沿推进自动
    跟随,路 L1FIX 消 R4SNAP 遗留的 DT 时点字面 2,674,412)。"""
    require(p['parity']['steps']['delta'] == -102 and
            p['parity']['steps'][label] == base['completed_steps'] - 102 and
            p['parity']['same_stop'] and p['parity']['stop_record']['identical'] and
            p['parity']['per_core_steps']['delta'] == {'0': -103, '1': 0},
            'the official-vs-l1_slotram conditional stop parity changed (-102 same-stop, '
            'CPU1 unchanged, expected)')
    first = p['first_divergence']
    require(first and first['step'] == 116148 and first['pc_a'] == '0x6034e98' and
            first['registers'] == {'1': {'a': '060c3338', 'b': '060e31e0'}},
            'the conditional first divergence is no longer the fence immediate at 0x6034e98')
    control = p['first_control_divergence']
    require(control and control['step'] == 169439 and control['pc_a'] == '0x6011154',
            'the conditional first control divergence is no longer the config.dat enumeration')
    rec = p['reconvergence']
    require(rec and rec['realigned'] and rec['lag'] == -102 and not rec['immediate'] and
            rec['realign_step_a'] == 19123794 and
            rec['control_zero_divergence_steps'] == base['completed_steps'] - 19123794,
            'the conditional reconvergence changed (expected the lottery-reshaped -102 '
            'realignment locking at step 19123794 with zero control divergence to the stops)')


def _check_self_candidate_r4_crc(p, label, base):
    """r4 侧 crc 档钉值(路 R4SNAP,2026-11-15 路 R4C 首轮实测,数字源=
    R4-COT-01 evidence_refs 归档报告 analysis/local-checks/r4c/
    cot01-r4-crc.json;R4C 挂账「r4 侧 candidate-self-check 钉值未接」消账):

    - official↔r4_keyhook 与 l1 对逐锚同形,镜像差仅在值:fence 上限立即数
      r4=0x060e322e(r4 专属,与 l1 0x060e31e0 同消费点不同值)、表尾指针
      r4 尾 0xE524E(与 l1 0xE5200 同落 0xE5000 对齐桶);
    - 停点 +458 same_stop(289,849 vs 290,307 @ 0x60679ba,per-core CPU0
      +458/CPU1 不变);唯一控制流分歧 169,439 config.dat 枚举,
      reconvergence realigned +458 immediate;
    - 表尾派生族 57 段/1,471 步,全签名重合于 step 232,729(=169,439+
      63,290,与 l1 对精确同值);
    - 差异面分类账 13 条游程(l1 对 12 条 + r4 专属注册表槽
      0xA9C7C..0xA9C80 = cmdid 0x30 setter 槽)全部命中已知区。
    上游前沿推进后按新结论重钉。"""
    require(p['parity']['steps'] == {'official': base['completed_steps'],
                                     label: 290307, 'delta': 458} and
            p['parity']['same_stop'] and p['parity']['stop_record']['identical'] and
            p['parity']['stop_pc'] == {'a': base['stop_pc'], 'b': base['stop_pc'], 'same': True} and
            p['parity']['per_core_steps']['delta'] == {'0': 458, '1': 0},
            'the official-vs-r4_keyhook crc_bankb stop parity changed (+458 same-stop expected)')
    first = p['first_divergence']
    require(first and first['step'] == 116148 and first['pc_a'] == '0x6034e98' and
            first['classification'] == 'register_state_divergence' and
            first['registers'] == {'1': {'a': '060c3338', 'b': '060e322e'}} and
            first['instruction_bytes_differ_within_instruction'],
            'the first official-vs-r4 signature divergence is no longer the r4 fence immediate '
            'at 0x6034e98 (r4 upper PC limit 0x060e322e)')
    control = p['first_control_divergence']
    require(control and control['step'] == 169439 and
            control['pc_a'] == '0x6011154' and control['pc_b'] == '0x6011154',
            'the first official-vs-r4 control divergence is no longer the config.dat enumeration at 0x6011154')
    rec = p['reconvergence']
    require(rec and rec['realigned'] and rec['lag'] == 458 and rec['immediate'],
            'the official-vs-r4 reconvergence is no longer the immediate +458 realignment')
    sig = rec['signature_after_realignment']
    require(sig['full_signature_realign_step_a'] == 232729 and
            sig['divergent_span_count'] == 57 and sig['divergent_steps'] == 1471,
            'the r4 table-tail derived-family survival changed (expected the l1-shaped '
            '57 spans / 1,471 divergent steps fully realigning at step 232729; a new shape '
            'must be re-adjudicated, not silently accepted)')
    diff_runs = p['image_byte_diff_runs']
    require(len(diff_runs) == 13 and all(run['known_site'] for run in diff_runs) and
            ['0xa9c7c', '0xa9c80'] in [run['plain_offset'] for run in diff_runs],
            'the official-vs-r4 image byte-diff ledger changed (13 runs all hitting known '
            'sites, the r4-only cmdid 0x30 registry setter slot 0xa9c7c..0xa9c80 present)')


def _check_self_candidate_r4_conditional(p, label, base):
    """r4 侧条件档钉值(路 R4SNAP,2026-11-15 路 R4C 首轮实测,数字源=
    R4-COT-01 evidence_refs 归档报告 analysis/local-checks/r4c/
    cot01-r4-p11heap.json;一切结论条件通过、derived-not-measured):

    - 停点轴差 -102 same_stop、per-core {0:-103,1:0}、重锁锚 19,123,794——
      与 l1 对同机制同值(+458 偏移对过 C0 δ₁=22 后被 core-tick 交付相位
      彩票消费,等待域伸缩把 +458 磨成 -102);R4C 首跑时点条件前沿
      21,798,257 @ 0x606b68e(路 ADA),停点步数不写死字面值、跟
      frontier_registry 条件前沿单源(路 ASS 放行到 28,764,184 后自动跟随,
      realign 之后控制流零分歧步数同式跟随前沿);
    - 首签名分歧 116,148 fence 立即数(r4=0x060e322e)、唯一控制流分歧
      169,439 config.dat 枚举——两锚与 crc 档逐字段一致。
    上游前沿推进后按新结论重钉(前沿前移后可重跑跟进)。"""
    require(p['parity']['steps']['delta'] == -102 and
            p['parity']['steps'][label] == base['completed_steps'] - 102 and
            p['parity']['same_stop'] and p['parity']['stop_record']['identical'] and
            p['parity']['per_core_steps']['delta'] == {'0': -103, '1': 0},
            'the official-vs-r4_keyhook conditional stop parity changed (-102 same-stop, '
            'CPU1 unchanged, expected)')
    first = p['first_divergence']
    require(first and first['step'] == 116148 and first['pc_a'] == '0x6034e98' and
            first['registers'] == {'1': {'a': '060c3338', 'b': '060e322e'}},
            'the conditional first divergence is no longer the r4 fence immediate at 0x6034e98')
    control = p['first_control_divergence']
    require(control and control['step'] == 169439 and control['pc_a'] == '0x6011154',
            'the conditional first control divergence is no longer the config.dat enumeration')
    rec = p['reconvergence']
    require(rec and rec['realigned'] and rec['lag'] == -102 and not rec['immediate'] and
            rec['realign_step_a'] == 19123794 and
            rec['control_zero_divergence_steps'] == base['completed_steps'] - 19123794,
            'the conditional reconvergence changed (expected the lottery-reshaped -102 '
            'realignment locking at step 19123794 with zero control divergence to the stops)')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--rom', type=Path, required=True)
    parser.add_argument('--image', action='append',
                        help='label=path with label in {official,tested_b79,eq_fence_repair,'
                             'l1_slotram,r4_keyhook}; repeat 2-3 times; the candidate labels '
                             'gate on the candidate_world_checks four-fold assertions '
                             '(UNTESTED candidates, no flash approval implied)')
    parser.add_argument('--pair', action='append',
                        help='labelA:labelB to compare (default: every combination)')
    parser.add_argument('--scenario', choices=sorted(SCENARIOS), default='crc_bankb',
                        help='shared-stack scenario: crc_bankb pins the main frontier (default, '
                             'unchanged behavior); p11_heap_experimental runs the conditional '
                             'derived-not-measured flag set up to the current conditional frontier '
                             '(every conclusion under it is conditional, not a verified boot fact)')
    parser.add_argument('--budget', type=int, default=None,
                        help='per-run step budget (default: scenario-specific; crc_bankb 300000, '
                             'p11_heap_experimental 5000000)')
    parser.add_argument('--self-check', action='store_true',
                        help='pin the official-vs-tested_b79 conclusion of the selected scenario with '
                             'assertions (repository images only, no private artifacts)')
    parser.add_argument('--candidate-self-check', metavar='LABEL=PATH',
                        help='pin the official-vs-<label> candidate differential baseline of the '
                             'selected scenario (label in {l1_slotram,r4_keyhook}, path = the '
                             'candidate .ufw built by build_quiet_candidate; independent from the '
                             '2026-11-12 official-vs-b79 pin; UNTESTED candidate, no flash approval)')
    parser.add_argument('--capture-snapshots', type=Path, metavar='DIR',
                        help='(snapshot resume rounds, docs/snapshot-resume.md) while running the '
                             'full replays, also capture each image\'s world at its natural stop '
                             'into DIR as <scenario>-<label>.bin — one full replay per image, once '
                             'per code state; every later verification round can then start from '
                             '--resume-from DIR in seconds')
    parser.add_argument('--resume-from', type=Path, metavar='DIR',
                        help='(snapshot resume rounds, docs/snapshot-resume.md) start every arm '
                             'from its captured snapshot in DIR instead of replaying from zero '
                             '(requires DIR to hold <scenario>-<label>.bin for EVERY --image '
                             'label; stop/report parity is re-verified, per-step comparison only '
                             'covers post-restore steps — pinned full-prefix trace facts are NOT '
                             're-executed, see the doc; refuses --self-check/--candidate-self-check)')
    parser.add_argument('--jobs', type=int, default=1, metavar='N',
                        help='(route PAR3) run the per-image simulation arms in N parallel '
                             'worker processes (fork; comparisons stay single-process in the '
                             'parent). Default 1 = the legacy serial order. Values are '
                             'unchanged either way — each arm is an independent simulation; '
                             'the wall clock of a three-image round drops from 3x arm time '
                             'to ~1x')
    parser.add_argument('--report', type=Path, required=True)
    args = parser.parse_args()
    scenario = args.scenario
    budget = args.budget if args.budget is not None else SCENARIOS[scenario]['default_budget']
    snapshot_mode, snapshot_dir = None, None
    if args.capture_snapshots is not None and args.resume_from is not None:
        require(False, '--capture-snapshots and --resume-from are mutually exclusive')
    if args.capture_snapshots is not None:
        snapshot_mode, snapshot_dir = 'capture', args.capture_snapshots
    if args.resume_from is not None:
        snapshot_mode, snapshot_dir = 'resume', args.resume_from
        require(not args.self_check and not args.candidate_self_check,
                '--resume-from cannot feed --self-check/--candidate-self-check: those pins '
                'assert full-prefix trace facts (first divergence step, reconvergence) that a '
                'resumed round does not re-execute — re-pin from full replays')

    if args.self_check:
        # 自检固定跑仓库内两镜像(official vs tested_b79)到所选场景的前沿停点;
        # 断言钉死已记录结论(crc_bankb=2026-11-12 首轮/路 AJ;p11_heap_experimental=
        # 路 BK 条件前沿)。上游前沿推进(probe_irq_entry 场景变化)后这些步数/PC
        # 断言会失败,按新前沿重新钉。
        require(not args.image and not args.pair, '--self-check runs the two repository images')
        args.image = [f'official={OFFICIAL_DEFAULT}',
                      f"tested_b79={ROOT / 'firmware/H3S_0.1.1.8_ANC_ON_TRANSPARENT.ufw'}"]
        args.pair = ['official:tested_b79']

    candidate_self_check = None
    if args.candidate_self_check:
        # 候选差分基线自检(路 DIF):official vs 候选(l1_slotram/r4_keyhook),
        # 独立于上方 2026-11-12 钉子(candidate-image-cotest §1.2「新镜像对走
        # 独立 self-check」)。候选 ufw 是 gitignored 构建产物,须显式给路径;
        # loader 的四重前置断言(candidate_world_checks)在此路径上照常把关。
        require(not args.image and not args.pair,
                '--candidate-self-check runs official plus the named candidate')
        clabel, _, cpath = args.candidate_self_check.partition('=')
        require(clabel in ('l1_slotram', 'r4_keyhook'),
                f'unknown candidate label {clabel!r} for the candidate self-check')
        candidate_self_check = clabel
        args.image = [f'official={OFFICIAL_DEFAULT}', f'{clabel}={cpath}']
        args.pair = [f'official:{clabel}']

    images = {}
    for item in args.image:
        label, _, raw_path = item.partition('=')
        require(label in LOADERS, f'unknown image label {label!r}; supported: {sorted(LOADERS)}')
        require(label not in images, f'duplicate image label {label!r}')
        images[label] = (Path(raw_path), label)
    require(len(images) >= 2, 'need at least two images')
    official_path = images['official'][0] if 'official' in images else None
    require(official_path is not None or {'tested_b79', 'eq_fence_repair'} <= set(images),
            'without the official image, only the tested_b79:eq_fence_repair pair is defined')

    rom = args.rom.read_bytes()
    check_rom(rom)
    module = upstream()
    if official_path is None:
        official_path = OFFICIAL_DEFAULT
    header = package_header(official_path)
    loaded = {}
    for label, (path, _) in images.items():
        loaded[label] = (label, LOADERS[label](path, official_path))

    pairs = [(item.split(':')[0], item.split(':')[1]) for item in (args.pair or [])]
    if not pairs:
        pairs = list(itertools.combinations(sorted(images), 2))
    for a, b in pairs:
        require(a in loaded and b in loaded, f'pair {a}:{b} references an unloaded image')

    # 配置新鲜度自检:official 必须复现所选场景的前沿停点,否则本工具的
    # flag 集已与主前沿场景漂移(或上游场景变更),结果不可信。
    # 路 REUSE:臂=每个不同镜像恰好执行一次。freshness 检查复用 official 的
    # pair 臂那一次运行(不再单独跑 frontier_freshness_check);每个 pair 的
    # 两臂从 RunPool 取同一次生成的 (result, spool),多对比较共享 spool 的
    # 顺序多遍读(TraceSpool 每消费者从头重读块,零物化、RAM 不随遍数涨)。
    # 复用替代的重复执行本就被 self-check 钉死为同值(freshness require 与
    # check_self 对同进程两次独立执行的 official 运行要求同一 registry 停点
    # 字面值),故报告逐字节不变;执行次数见各 trace_<label> 打印行计数。
    # 路 SNAPX 快照轮(--capture-snapshots/--resume-from):capture=全量重放
    # 顺带在自然停点捕获每镜像世界(一代码态一次);resume=验证轮从捕获世界
    # 起跑,停点/报告 parity 照常核验(freshness require 对 resumed 报告同样
    # 成立——restore 世界重现捕获停点),全或无(all-or-none)拒混合轮。
    if snapshot_mode == 'resume':
        missing = [label for label in sorted(loaded)
                   if not snapshot_path(snapshot_dir, scenario, label).exists()]
        require(not missing,
                f'--resume-from {snapshot_dir} lacks snapshots for {missing} '
                f'(expected <scenario>-<label>.bin for every image; capture them first with '
                f'--capture-snapshots under the SAME code state)')
    if snapshot_mode is not None:
        snapshot_dir.mkdir(parents=True, exist_ok=True)
    pool = RunPool(module, rom, header, budget, scenario, loaded,
                   snapshot_mode=snapshot_mode, snapshot_dir=snapshot_dir)
    try:
        if args.jobs > 1:
            # 路 PAR3:镜像臂=独立仿真,并行分叉执行后在父进程汇合;报告值与
            # 串行版逐字节相同(同一 frontier_run,每次分叉副本各跑一臂)。
            pool.run_parallel(sorted(loaded), jobs=args.jobs)
        if 'official' in loaded:
            fresh = SCENARIOS[scenario]['freshness']
            check = pool.run('official')[0]
            require(check['completed_steps'] == fresh['completed_steps'] and
                    check['stop_pc'] == fresh['stop_pc'] and
                    all(check['stop'].get(k) == v for k, v in fresh['stop_fields'].items()),
                    f'official frontier stop changed for scenario {scenario!r}: the imported flag set no '
                    f'longer reproduces the pinned frontier stop ({fresh["completed_steps"]} @ '
                    f'{fresh["stop_pc"]}); update this pin after the frontier advances')

        report = {'simulator_commit': PIN, 'device_accessed': False, 'whole_h3s_boot_simulated': False,
                  'candidate_whole_boot_validated': False, 'suitable_for_flash_approval_alone': False,
                  'comparison_basis': COMPARISON_BASIS,
                  'pairs': []}
        if snapshot_mode is not None:
            report['snapshot_round'] = {'mode': snapshot_mode, 'dir': str(snapshot_dir),
                                        'scenario': scenario}
        if scenario != 'crc_bankb':
            # 默认场景报告保持逐字节不变;非默认场景才标注场景键。
            report['scenario'] = scenario
        for a, b in pairs:
            print(f'== diff {a} vs {b} (scenario {scenario}) ==', flush=True)
            report['pairs'].append({'name': f'{a}_vs_{b}',
                                    **compare_pair(loaded[a], pool.run(a),
                                                   loaded[b], pool.run(b), budget, scenario,
                                                   resumed=snapshot_mode == 'resume')})
        if args.self_check:
            # 路 PARSTD:写差异标准断言需要活的 traces(报告 JSON 保持逐字节
            # 不变,新增事实不进报告);arms={label: (trace, flash plain)}。
            arms = {label: (pool.run(label)[1], loaded[label][1][1])
                    for label in ('official', 'tested_b79')}
            if scenario == 'crc_bankb':
                check_self(report, arms=arms)
            else:
                check_self_conditional(report, arms=arms)
            report['image_trace_self_check_passed'] = True
        if candidate_self_check:
            check_self_candidate(report, candidate_self_check, scenario)
    finally:
        # 路 O6 清理纪律(路 REUSE 更新):spool 生命周期=全部消费者(freshness+
        # 所属各 pair)结束;最后一位消费者完成后由 pool 统一释放(无名临时
        # 文件,close 即回收;require 抛错路径同样走这里,不留给 GC)。
        pool.close()
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(json.dumps(report, indent=2) + '\n')
    print('wrote', args.report, flush=True)


if __name__ == '__main__':
    main()
