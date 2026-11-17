/* mic_effect (DHA / 辅听) 双击多功能键 toggle 接驳桩 — 路 L1KEY / sim-l1key,
 * 2026-11-17
 *
 * 本文件是 build_quiet_candidate.py --hearing-aid-l1-key7 的组合输入片段：
 * 构建器把 mic_effect_open.S（修正版）+ mic_effect_close.S + 本片段拼成单一
 * 代码段现场汇编，因此本片段单独不可汇编（call 的 mic_effect_open/close 在
 * 前面定义）。接驳语义见 docs/hearing-aid-l1-key7.md：
 *
 *   cmd 7（多功能键双击）默认绑定为「音频模式循环」——handler 0x060252c6 的
 *   动作跳表 0x06025382（表字节 0x40）落到分支体入口 0x06025404。本候选把该
 *   入口的 8B 窗口（add.s14.rri + li.u8 + li.u8）整窗替换为
 *   r6=<本桩地址>; goto r6，把双击改接为 HA toggle：
 *
 *     读数据槽 → 0（未激活）→ 先请求关 ANC（内部模式 OFF，db3c(1,1)）→
 *       mic_effect_open()；
 *     非 0（已激活）→ mic_effect_close()；
 *     最后 r4=1 回到 handler 的「已处理」返回点 0x0602530c。
 *
 * 进入状态（路 AO/L1KEY 反汇编钉死）：handler 0x060252c6 的 `push.rets_rl l=9`
 * 栈帧仍在；r7=QCY 基址、r8=字符串表基址在本桩内均不消费。本桩不经 call
 * 进入（窗口 = li.u32 r6 + goto r6），不 push/pop，最终 goto HANDLER_RET 复用
 * handler 的返回帧（r0=r4; pop.pc_rl l=9）——栈保持平衡。
 *
 * ANC 互斥（路 I §3.2 + codex L1KEY 复核）：ANC 通透与 mic_effect 辅听共享
 * mic-ADC→DAC 通路，不可共存；开辅听前先请求 ANC OFF。注意 0x0600db3c 是
 * 「请求」路径（写 b[ctx+33] 目标 + 置过渡标志 + 提示音 + 续行），返回不代表
 * anc_task 已完成硬件退出——完成时序在真机前仍是开放项（docs/hearing-aid-l1-key7.md
 * 诚实边界）。旧路 T/Y 桩把「[ctx+116/120] 当 OFF 应用函数槽」是误读：那两槽
 * 实际是 anc_trans/anc_off 提示音资源名指针，0x0600dae4 只播提示音，不能关 ANC。
 * 本桩因此改走完整 db3c(1,1) OFF 请求，不复用旧桩的 0x0600dae4 形状。
 *
 * 本桩只写 r0-r6；r7/r8 不碰（handler 返回帧统一恢复）。全部固件目标经
 * li.u32 + call/goto r6 间接到达，本段对装载基址完全不敏感（零重定位）。
 */

    /* —— 固件锚点（路 AO/L1KEY 静态钉死，不随布局变化） —— */
    .equ MIC_EFX_SLOT, 0x00125574  /* RAM 槽（路 CT，构建器统一注入实际槽值） */
    .equ ANC_DISPATCH,  0x0600db3c  /* 内部模式 dispatch：r0=1(OFF)/2(ON)/3(TRANS)，r1=flag */
    .equ HANDLER_RET,   0x0602530c  /* handler 返回已处理：r0=r4; pop.pc_rl l=9 */

    .global cmd7_toggle
cmd7_toggle:
    /* 读槽：0 = 未激活 → open；非 0 = 已激活 → close */
    r0 = MIC_EFX_SLOT
    r4 = [r0+0]
    if (r4 != 0) goto do_close

    /* open：先请求关 ANC（互斥），再开辅听 */
    r0 = 1                  /* ANC 内部模式 OFF */
    r1 = 1                  /* flag=1（键循环邻域三入口 0x06018c8e/ca4/cba 同款） */
    r6 = ANC_DISPATCH
    call r6                 /* db3c(1,1)：写目标+过渡标志+提示音+续行（返回≠硬件已退） */
    call mic_effect_open    /* 开辅听（自带 {rets,r6-r4} 保存恢复） */
    goto done

do_close:
    call mic_effect_close   /* 幂等关辅听（自带保存恢复） */

done:
    r4 = 1                  /* handler 返回值 = 已处理（不把 open 的 -22 当事件返回值） */
    r6 = HANDLER_RET
    goto r6                 /* 复用 handler 返回帧 */
