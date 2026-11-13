/* mic_effect (DHA / 辅听) 离开通透档接驳桩 — 路 Y / sim-l1closeimg, 2026-11-14
 *
 * 本文件是 build_quiet_candidate.py --hearing-aid-l1-close 的组合输入片段
 * （在 mic_effect_open.S + mic_effect_close.S + mic_effect_hook.S 之后拼接，
 * 单一代码段现场汇编，单独不可汇编：call 的 mic_effect_close 在前面定义）。
 * 接驳语义见 docs/hearing-aid-l1-payload.md §10.5/§11：
 *
 *   模式 dispatch 0x0600db3c 两条链的 ON/OFF 落点窗口（ON-1 0x0600db9a /
 *   OFF-1 0x0600dc74 / ON-2 0x0600dbc4 / OFF-2 0x0600dc9a，各 8B =
 *   call 0x0600c2aa 取 ANC ctx + 槽位装载 [ctx+112]=ON / [ctx+120]=OFF）
 *   整窗替换为 r6=<本桩地址>; goto r6。本桩重放原窗口两条指令（ctx 与槽位
 *   装载），插入幂等 mic_effect_close()（离开通透 = 先关辅听），再回原流
 *   （窗口+8：链 1/链 2 的原 2B goto 汇合指令，或 ON-2 的 call 0x0600bbba）。
 *
 * 进入状态（路 X 静态钉死）：r4 = apply flag（汇合点 r1=r4 语义）、
 * r7 = QCY 基址（两条尾声从 [r7+500] 重载）。本桩只写 r0/r1/r5/r6：
 *   - r5 在重放槽位装载时被复用为 apply fn 暂存——mic_effect_close 自带
 *     {rets, r6-r4} 保存恢复，r4/r5/r6 跨 call 不变；窗口后原流里 r5
 *     均为死值（链 1 汇合只消费 r4，链 2 后续路径全部覆写 r5）。
 *   - ON-2 返回点 0x0600dbcc 的 call 0x0600bbba 自建参数（r0=15/r1/r2，
 *     不读传入参数，本路反汇编核对），重取 ctx 到 r0 是防御性重放。
 *   - r1-r3 在原窗口后本来就是 call ctx_get 之后的 caller-saved 暂存，
 *     close 调用不引入新的活性约束。
 *
 * 落点扫描（路 T 方法，全 app 线性反汇编 51,701 个分支落点）：四窗口内部
 * 零落点；ON-1/ON-2 纯顺延进入（零起点落点），OFF-1/OFF-2 起点各只有
 * 一条 = 模式分支（0x0600db8e / 0x0600dbb8）。0x0600dc98 的 pop 是 4 个
 * 早退分支的活落点，不在任何窗口内。
 */

    /* —— 固件锚点（路 X 静态钉死，不随布局变化） —— */
    .equ LEAVE_CTX_GET,  0x0600c2aa  /* ANC ctx getter（纯读叶子：只写 r0-r2） */
    .equ LEAVE_ON_SLOT,  112         /* [ctx+112]=ON 应用函数槽 */
    .equ LEAVE_OFF_SLOT, 120         /* [ctx+120]=OFF 应用函数槽 */
    .equ RET_ON1,        0x0600dba2  /* ON-1 窗口+8：原 2B goto → 汇合 0x0600dc86 */
    .equ RET_OFF1,       0x0600dc7c  /* OFF-1 窗口+8：原 2B goto → 汇合 0x0600dc86 */
    .equ RET_ON2,        0x0600dbcc  /* ON-2 窗口+8：原 call 0x0600bbba（自建参数） */
    .equ RET_OFF2,       0x0600dca2  /* OFF-2 窗口+8：原 2B goto → 汇合 0x0600dccc */

    .global leave_on1
leave_on1:
    r6 = LEAVE_CTX_GET
    call r6                  /* 重放原窗口第一条：取 ANC ctx */
    r5 = [r0+LEAVE_ON_SLOT]  /* 重放槽位装载：r5 = ON apply fn（跨 close 暂存） */
    call mic_effect_close    /* 幂等关辅听（保存恢复 r4-r6） */
    r0 = r5                  /* 恢复 apply fn（原窗口出口 r0 语义） */
    r6 = RET_ON1
    goto r6                  /* 回原流：2B goto → 链 1 汇合点 */

    .global leave_off1
leave_off1:
    r6 = LEAVE_CTX_GET
    call r6
    r5 = [r0+LEAVE_OFF_SLOT] /* r5 = OFF apply fn */
    call mic_effect_close
    r0 = r5
    r6 = RET_OFF1
    goto r6

    .global leave_on2
leave_on2:
    r6 = LEAVE_CTX_GET
    call r6
    r5 = [r0+LEAVE_ON_SLOT]  /* 原窗口装载目标就是 r5（链 2 ON 槽） */
    call mic_effect_close
    r6 = LEAVE_CTX_GET       /* 重取 ctx 到 r0（close 清了 r0-r3；getter 纯读） */
    call r6
    r6 = RET_ON2
    goto r6                  /* 回原流：call 0x0600bbba（自建参数，不读 r0-r3） */

    .global leave_off2
leave_off2:
    r6 = LEAVE_CTX_GET
    call r6
    r5 = [r0+LEAVE_OFF_SLOT]
    call mic_effect_close
    r0 = r5
    r6 = RET_OFF2
    goto r6                  /* 回原流：2B goto → 链 2 汇合点 */
