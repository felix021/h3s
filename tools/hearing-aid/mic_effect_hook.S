/* mic_effect (DHA / 辅听) 接驳入口桩 — 路 T / sim-l1image, 2026-11-13
 *
 * 本文件是 build_quiet_candidate.py --hearing-aid-l1-patch 的组合输入片段：
 * 构建器把 mic_effect_open.S（在前）与本片段（在后）拼成单一
 * .text.mic_effect_open 段现场汇编，因此本片段单独不可汇编（call 的
 * mic_effect_open 在前一个文件里定义）。接驳语义见
 * docs/hearing-aid-l1-payload.md §3/§9：
 *
 *   两个 TRANS 落点窗口（0x0600dc7e / 0x0600dca4，各 8B = call ctx_getter +
 *   r0=[r0+116] 取 TRANS 提示音资源名）整窗替换为 r6=<本桩地址>; goto r6，
 *   本桩重放原窗口第一条（call 0x0600c2aa 取 ANC 静态结构）但改取 OFF 提示音
 *   名 [ctx+120]，再调 mic_effect_open()（开辅听），最后 goto 回对应链的原
 *   函数尾声（跳过原 TRANS 提示音播放）。
 *
 *   语义勘误（2026-11-17 路 L1KEY，docs/hearing-aid-l1-key7.md §2.1）：
 *   [ctx+112/116/120] 不是「模式应用函数槽」，而是 anc_on/anc_trans/anc_off
 *   提示音资源名字指针（0x0609c7fd/c813/c82c）；0x0600dae4(fn, flag) 只播
 *   提示音，不写 b[ctx+33]=OFF。故本桩原称「先关 ANC 通透」是误读——它只播
 *   anc_off 提示音，不真正关 ANC。本桩保留仅供旧候选（--hearing-aid-l1-close/
 *   slotram/r4-keyhook）复现字节；真正关 ANC 走 0x0600db3c(1,1) 请求，见新桩
 *   mic_effect_toggle_hook.S（cmd7_toggle）。
 *
 * 进入状态（路 L 反汇编钉死）：r4 = apply flag（原汇合点 0x0600dc86 /
 * 0x0600dccc 的 r1=r4 语义）、r7 = QCY 基址（两条尾声都从 [r7+500] 重载
 * r0/r1）。本桩只写 r0/r1/r6；r4/r5/r7 不碰（mic_effect_open 自带
 * {rets, r6-r4} 保存恢复），函数出口 pop {pc, r8-r4} 会整体恢复 callee
 * 寄存器，r6 死值无害。全部固件目标经 li.u32 + call/goto r6 间接到达，
 * 本段对装载基址完全不敏感（零重定位）。
 */

    /* —— 固件锚点（路 L 静态钉死，不随布局变化） —— */
    .equ ANC_CTX_GET,   0x0600c2aa  /* ANC ctx getter（原窗口第一条 call 的目标） */
    .equ MODE_APPLY,    0x0600dae4  /* 提示音播放包装（原汇合点 call，flag=r4；非 ANC 应用） */
    .equ MODE_SLOT_OFF, 120         /* [ctx+120]=anc_off 提示音名（原窗口取 +116=anc_trans 名） */
    .equ EPILOGUE_A,    0x0600dc8c  /* 窗口 A 链原函数尾声 */
    .equ EPILOGUE_B,    0x0600dcd2  /* 窗口 B 链原函数尾声 */

    .global trans_branch_1
trans_branch_1:
    r6 = ANC_CTX_GET
    call r6                 /* 重放原窗口第一条：取 ANC ctx */
    r0 = [r0+MODE_SLOT_OFF] /* 改取 anc_off 提示音名（原窗口取 anc_trans 名 +116） */
    r1 = r4                 /* apply flag（原汇合点语义） */
    r6 = MODE_APPLY
    call r6                 /* 播 anc_off 提示音（不真正关 ANC，见文件头勘误） */
    call mic_effect_open    /* 再开辅听（保存恢复 r4-r6） */
    r6 = EPILOGUE_A
    goto r6                 /* 跳回窗口 A 链尾声，跳过原 TRANS apply */

    .global trans_branch_2
trans_branch_2:
    r6 = ANC_CTX_GET
    call r6
    r0 = [r0+MODE_SLOT_OFF]
    r1 = r4
    r6 = MODE_APPLY
    call r6
    call mic_effect_open
    r6 = EPILOGUE_B
    goto r6                 /* 跳回窗口 B 链尾声 */
