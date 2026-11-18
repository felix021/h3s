/* mic_effect (DHA / 辅听) 关闭载荷 · lfix + A3 压舱释放 — 路 LFIX-IMPL, 2026-11-19
 *
 * mic_effect_close.S 的 F1a 变体：与基座逐指令一致（幂等守卫 → stop(0) →
 * release → 清槽 → event_notify(11,"mic_effect")），仅在其后追加一步
 * （docs/hearing-aid-lfix-plan.md §4-A3）：
 *
 *   A3c. clock_free("lfix_hold") —— open 侧 A3a 的配对释放（stop/release 完成
 *        后、返回前）。名串在 mic_effect_open_lfix_hold.S 段尾 rodata，地址
 *        由构建器回填（与 MPO_*_ADDR 同模式）。幂等性不受影响：槽==0 早退
 *        在最前，未激活时不会重复 free；open 失败路径的释放（A3b）与 close
 *        的释放（本步）互斥——成功 open 才轮到 close 释放。
 *
 * F1b（无压舱）构建继续使用基座 mic_effect_close.S，本文件不参与。
 * 完整依据注释见 mic_effect_close.S 与 docs/hearing-aid-lfix-plan.md。
 */

    /* —— 实施时可调参数（与 open 载荷保持同值） —— */
    .equ MIC_EFX_SLOT, 0x060e3200    /* 示例：与激活载荷共用的数据槽 4B */
    .equ LFIX_HOLD_NAME_ADDR, 0x060e3400 /* "lfix_hold" rodata（占位，构建器回填） */

    /* —— 固件现成 API 锚点（静态钉死，不随布局变化） —— */
    .equ JLSTREAM_STOP,   0x060741ee  /* r0=stream, r1=fade_msec(0=立即) */
    .equ JLSTREAM_RELEASE, 0x06074204 /* r0=stream；post cmd9 同步等待回收 */
    .equ EVENT_NOTIFY,    0x060057e6  /* r0=事件id, r1=arg（tbh switch） */
    .equ CLOCK_FREE,      0x0600572c  /* r0=名串；按名摘链（未命中=no-op） */

    .equ EVENT_CLOSE_PLAYER, 11       /* SDK 枚举 STREAM_EVENT_CLOSE_PLAYER */
    .equ NAME_MIC_EFFECT,   0x060a1de2 /* 固件内 "mic_effect" 字符串 */

    .section .text.mic_effect_close,"ax",@progbits
    .global mic_effect_close
mic_effect_close:
    /* ABI：无参数。返回 r0 = 0（幂等：未激活也成功）。r4-r6 callee-saved。 */
    [--sp] = {rets, r6-r4}

    /* 重入保护（反向）：槽==0 = 未激活，直接成功返回 */
    r0 = MIC_EFX_SLOT
    r4 = [r0+0]
    if (r4 == 0) goto close_done_ok

    /* 1. jlstream_stop(stream, 0) —— 无淡出立即停 */
    r0 = r4
    r1 = 0
    r6 = JLSTREAM_STOP
    call r6

    /* 2. jlstream_release(stream) —— 同步回收 pipeline */
    r0 = r4
    r6 = JLSTREAM_RELEASE
    call r6

    /* 3. 清数据槽 = g_mic_effect_player = NULL（先于 notify，SDK 同序） */
    r0 = MIC_EFX_SLOT
    r5 = 0
    [r0+0] = r5

    /* 4. jlstream_event_notify(11, "mic_effect") —— 释放 24MHz 时钟 + 场景仲裁 */
    r0 = EVENT_CLOSE_PLAYER
    r1 = NAME_MIC_EFFECT
    r6 = EVENT_NOTIFY
    call r6

    /* A3c. clock_free("lfix_hold") —— 压舱释放（stop/release 完成后） */
    r0 = LFIX_HOLD_NAME_ADDR
    r6 = CLOCK_FREE
    call r6

close_done_ok:
    r0 = 0
close_ret:
    {pc, r6-r4} = [sp++]
