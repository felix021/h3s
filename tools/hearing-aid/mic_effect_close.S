/* mic_effect (DHA / 辅听) 关闭载荷 — 路 X / sim-l1close, 2026-11-14
 *
 * 重写被链接器裁掉的 hearing_aid_player_close() 的精简版：
 *
 *   SDK mic_effect.c: hearing_aid_player_close() = DAC_NOISEGATE_ON()
 *                     + mic_effect_player_delete()
 *   mic_effect_player_delete() = jlstream_stop(stream, 0)
 *                     + jlstream_release(stream) + ram_code_unload()
 *                     + free(player) + g_mic_effect_player = NULL
 *                     + jlstream_event_notify(STREAM_EVENT_CLOSE_PLAYER,
 *                       (int)"mic_effect")
 *
 * 固件内三段式 close 惯用法有现成先例（本路反汇编钉死，见
 * docs/hearing-aid-l1-payload.md §10）：
 *   - esco/linein close 0x06016c12: stop(stream,0) → release → …
 *     → notify(11, "…")，与 SDK 序列逐 call 同形；
 *   - event_notify(11, "mic_effect") 的 CLOSE_PLAYER 分支在固件分发函数
 *     0x060057e6（= 激活链 get_pipeline_uuid 的同一入口，tbh switch 按
 *     r0-11 分发）里存在：case 11 落 0x06005820 strcmp 链，"mic_effect"
 *     命中 → 0x0600572C 按 name 遍历 0x124880 时钟链表摘链释放
 *     （= clock_free，与激活分支的 clock_alloc 配对）。
 *
 * 与激活载荷（mic_effect_open.S）的差异：
 *   - 不做 DAC_NOISEGATE_ON（noisgate 固件锚点未钉死，与 open 不做
 *     DAC_NOISEGATE_OFF 对称，风险见文档 §6.3）；
 *   - 无 player 结构（open 载荷不 malloc player，数据槽直接存 stream），
 *     故无 free/player=NULL，清数据槽即等价 g_mic_effect_player = NULL；
 *   - ram_code_unload 不适用（open 从未做 ram_code_load）。
 *
 * 重入保护复用激活载荷的数据槽（反向）：槽==0 直接成功返回（幂等，
 * 重复 close 无害）；槽!=0 = 已激活，按上述序列关闭后清槽。
 *
 * 离线载荷：encode->decode 往返逐条零 diff；未在真机/完整仿真跑通关闭链，
 * 不构成「辅听可开关」声明。
 *
 * 布局约束：与激活载荷同一追加段模式；MIC_EFX_SLOT 是唯一随段地址变化的
 * 常量（与 mic_effect_open.S 共用同一数据槽），双基址往返测试断言载荷对
 * 其余字节不敏感。
 */

    /* —— 实施时可调参数（与 mic_effect_open.S 保持同值） —— */
    .equ MIC_EFX_SLOT, 0x060e3200    /* 示例：与激活载荷共用的数据槽 4B */

    /* —— 固件现成 API 锚点（静态钉死，不随布局变化） —— */
    .equ JLSTREAM_STOP,   0x060741ee  /* r0=stream, r1=fade_msec(0=立即)；
                                       h[stream+46]=fade 后 post cmd4 给流服务线程 */
    .equ JLSTREAM_RELEASE, 0x06074204 /* r0=stream；post cmd9 同步等待回收
                                       （激活载荷错误路径已用同一入口） */
    .equ EVENT_NOTIFY,    0x060057e6  /* r0=事件id, r1=arg；与激活链
                                       get_pipeline_uuid 同一入口（tbh switch） */

    .equ EVENT_CLOSE_PLAYER, 11       /* SDK 枚举 STREAM_EVENT_CLOSE_PLAYER；
                                         固件 3 个调用点 r0=11 实证 */
    .equ NAME_MIC_EFFECT,   0x060a1de2 /* 固件内 "mic_effect" 字符串（CLOSE_PLAYER
                                          分支 strcmp 链的匹配名） */

    .section .text.mic_effect_close,"ax",@progbits
    .global mic_effect_close
mic_effect_close:
    /* ABI：无参数。返回 r0 = 0（幂等：未激活也成功）。r4-r6 callee-saved。 */
    [--sp] = {rets, r6-r4}

    /* 重入保护（反向）：槽==0 = 未激活，直接成功返回 */
    r0 = MIC_EFX_SLOT
    r4 = [r0+0]
    if (r4 == 0) goto close_done_ok

    /* 1. jlstream_stop(stream, 0) —— 无淡出立即停（0x06016c12 同参形状） */
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

    /* 4. jlstream_event_notify(11, "mic_effect") —— 释放 24MHz 时钟 +
       通知场景仲裁（CLOSE_PLAYER 分支存在，"mic_effect" 命中 clock_free） */
    r0 = EVENT_CLOSE_PLAYER
    r1 = NAME_MIC_EFFECT
    r6 = EVENT_NOTIFY
    call r6

close_done_ok:
    r0 = 0
close_ret:
    {pc, r6-r4} = [sp++]
