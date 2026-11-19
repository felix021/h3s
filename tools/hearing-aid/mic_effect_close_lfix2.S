/* mic_effect (DHA / 辅听) 关闭载荷 · lfix2 + C2 ANC 恢复补刀 — 路 LFIX2-IMPL / sim-lfiximpl2, 2026-11-19
 *
 * F1a close 载荷的 F2 变体。F1a 真机（2026-11-19 17:2x 用户验收）确认：
 * 左耳刺耳已修（B2 功臣）、助听开着不断联（A1/A3 生效）；新症状=「关助听后按
 * ANC 键：进降噪无提示音、再切断联重连（稳定复现）」——断联从 HAL5 版的
 * 「进助听」节点挪到了 ANC 节点（docs/hearing-aid-lfix2-impl.md 归因表）。
 *
 * 归因（详见 impl 文档）：开助听必经 db3c(1,1)（ANC→OFF，mic 让给助听），关助听
 * 后 ANC 停在 OFF；用户随后按 ANC 键 = ANC 引擎从 OFF 冷启（audio_anc_run 停-重配-
 * 启全链 + ADA 模拟前端上电序列，docs/d2-audio-input-pre-study.md §3），与 close
 * 刚拆完的 ADC/DAC/时钟残余并发——b79 两态表上该路径不可达（b79 无助听、
 * ON↔TRANS 浅切换无数遍无断联）。F1a 上用户第一次走到这条路径。
 *
 * C2 补刀：close 尾部（流拆除+时钟释放全部完成后）主动把 ANC 恢复到 ON：
 *
 *   C2a. os_time_dly(20 tick = 200ms)：让 stop/release 的 DAC/ADC 下电与
 *        A3c 触发的时钟重算调度先走完（ANC 链实测延时档 1/2/100 tick，200ms
 *        给最深档 2 倍缓冲），再触碰 ANC。
 *   C2b. db3c(2, 1)：请求 ANC ON（内部模式 2，flag=1 与 ANC 键官方路径
 *        0x06018a7a-7e 调用同参——会播「开启降噪」提示音，顺带验证提示音
 *        链在助听关闭后是否恢复，即症状 3 的判别信号）。
 *   C2c. 等待进入完成（A1 同款循环：b[ctx+29]==0 且 b[ctx+33]==2，60 次×
 *        1 tick ≈600ms 上限）；超时=放弃等待直接返回（db3c 已投递，ANC
 *        链自会跑完，不回滚不重试——与 A1 超时取消 open 的语义不同：
 *        close 本身已完成，这里等的只是 ANC 收尾）。
 *
 * 效果：ANC 的 OFF 冷启从「用户按键（与未知并发面赛跑）」挪到「close 内受控
 * 窗口（资源已拆净+200ms 缓冲）」；用户之后按 ANC 键只剩 ON↔TRANS 浅切换
 * （b79 验证安全；需 F2 叠加两态表——用户 2026-11-19 17:3x 追加需求，builder
 * --anc-cycle-two-state，非本载荷职责）。副作用：关助听后 ~0.3-0.9s 才恢复
 * 降噪+提示音（用户可感知的延迟，换断联面收口）。A3c 释放触发的 DVFS 降挡
 * 在 close 返回后 ~4s（timer 重评）一次性发生，与 C2 的 ANC 冷启窗口及用户
 * 后续浅切换按键天然错开；若 F2 验收仍断联，下一轮候选=hold 常驻（跨周期
 * 保持 152M，见 impl 文档「后续候选」节，本轮不实现）。
 *
 * 幂等性不受影响：槽==0 早退在最前（未激活时不动 ANC——只有真正关闭过助听
 * 的那次 close 才恢复）。重复 close 不再 db3c。open 载荷原样沿用 F1a
 * （A1+B1(16)+B2+A3a/A3b，见 mic_effect_open_lfix_hold.S）。
 */

    /* —— 实施时可调参数（与 open 载荷保持同值） —— */
    .equ MIC_EFX_SLOT, 0x060e3200    /* 示例：与激活载荷共用的数据槽 4B */
    .equ LFIX_HOLD_NAME_ADDR, 0x060e3400 /* "lfix_hold" rodata（占位，构建器回填） */

    /* —— 固件现成 API 锚点（静态钉死，不随布局变化） —— */
    .equ JLSTREAM_STOP,   0x060741ee  /* r0=stream, r1=fade_msec(0=立即) */
    .equ JLSTREAM_RELEASE, 0x06074204 /* r0=stream；post cmd9 同步等待回收 */
    .equ EVENT_NOTIFY,    0x060057e6  /* r0=事件id, r1=arg（tbh switch） */
    .equ CLOCK_FREE,      0x0600572c  /* r0=名串；按名摘链（未命中=no-op） */
    .equ ANC_DISPATCH,    0x0600db3c  /* r0=内部模式(1=OFF/2=ON/3=TRANS), r1=flag */
    .equ ANC_DELAY_FN,    0x06064efc  /* os_time_dly(r0=n tick)，1 tick=10ms */

    .equ EVENT_CLOSE_PLAYER, 11       /* SDK 枚举 STREAM_EVENT_CLOSE_PLAYER */
    .equ NAME_MIC_EFFECT,   0x060a1de2 /* 固件内 "mic_effect" 字符串 */

    /* —— A3：持有标志（与 mic_effect_open_lfix_hold.S 同值） —— */
    .equ LFIX_HOLD_FLAG,    0x125578    /* BSS 持有标志字节（RAM 槽同洞内 slot+4，
                                          boot 清零；仅 A3a 成功入账时为 1） */

    /* —— C2：ANC 恢复补刀参数 —— */
    .equ ANC_MODE_ON,       2           /* 内部 ANC 模式 ON（勿与线上 ID 混淆） */
    .equ ANC_RESTORE_FLAG,  1           /* flag=1：与 ANC 键官方路径同参（播提示音） */
    .equ C2_SETTLE_TICKS,   20          /* C2a 稳定缓冲 20 tick=200ms（ANC 链最深
                                          延时档 100ms 的 2 倍） */
    .equ C2_WAIT_TRIES,     60          /* C2c 等待上限 60 次×1 tick ≈600ms */
    .equ QCY_STRUCT_BASE,   0x1265c0    /* QCY 全局设备结构基（A1 同源） */
    .equ ANC_CTX_PTR_OFF,   500         /* ANC ctx 指针字段 [QCY+500] */
    .equ ANC_TRANS_OFF,     29          /* b[ctx+29] 过渡标志 */
    .equ ANC_TARGET_OFF,    33          /* b[ctx+33] 目标模式 */

    .section .text.mic_effect_close,"ax",@progbits
    .global mic_effect_close
mic_effect_close:
    /* ABI：无参。返回 r0 = 0（幂等：未激活也成功）。r4-r6 callee-saved。 */
    [--sp] = {rets, r6-r4}

    /* 重入保护（反向）：槽==0 = 未激活，直接成功返回（不动 ANC——幂等语义：
     * 只有真正关闭过助听的那次 close 才恢复 ANC） */
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

    /* 4. jlstream_event_notify(11, "mic_effect") —— 释放 24MHz 时钟 */
    r0 = EVENT_CLOSE_PLAYER
    r1 = NAME_MIC_EFFECT
    r6 = EVENT_NOTIFY
    call r6

    /* A3c. clock_free("lfix_hold") —— 压舱释放（stop/release 完成后）。
     *      持有标志为 1（open 侧 A3a 成功入账）才释放并清零；open 失败轮次
     *      的释放已在 open 错误尾（A3b）做过，标志为 0 时直接跳到 C2。 */
    r0 = LFIX_HOLD_FLAG
    r5 = b[r0+0] (u)
    if (r5 == 0) goto c2_settle
    r0 = LFIX_HOLD_NAME_ADDR
    r6 = CLOCK_FREE
    call r6
    r0 = LFIX_HOLD_FLAG
    r5 = 0
    b[r0+0] = r5

c2_settle:
    /* C2a. 稳定缓冲 20 tick（200ms）：DAC/ADC 下电与 A3c 触发的时钟重算
     *      调度先走完，再触碰 ANC（ANC 退出/进入链自用延时档 1/2/100 tick，
     *      此处给最深档 2 倍）。 */
    r0 = C2_SETTLE_TICKS
    r6 = ANC_DELAY_FN
    call r6

    /* C2b. db3c(2, 1) —— 请求 ANC ON（flag=1 与 ANC 键官方路径同参，播
     *      「开启降噪」提示音）。ctx==0（子系统未建）时 db3c 自身空转。 */
    r0 = ANC_MODE_ON
    r1 = ANC_RESTORE_FLAG
    r6 = ANC_DISPATCH
    call r6

    /* C2c. 等待 ANC 进入完成：b[ctx+29]==0 且 b[ctx+33]==2；60 次×1 tick
     *      上限，超时放弃等待（db3c 已投递，ANC 链自会跑完——close 语义
     *      已完成，这里只是收尾等待，与 A1 超时取消 open 不同）。ctx==0
     *      跳过（db3c 已空转，无可等）。 */
    r6 = QCY_STRUCT_BASE
    r4 = [r6+ANC_CTX_PTR_OFF]
    if (r4 == 0) goto close_done_ok
    r5 = C2_WAIT_TRIES
    r6 = ANC_DELAY_FN
c2_wait:
    r0 = b[r4+ANC_TRANS_OFF] (u)
    if (r0 != 0) goto c2_poll
    r0 = b[r4+ANC_TARGET_OFF] (u)
    if (r0 == ANC_MODE_ON) goto close_done_ok   /* 过渡清零+目标 ON：完成 */
c2_poll:
    r5 += -1
    if (r5 == 0) goto close_done_ok             /* 超时：放弃等待 */
    r0 = 1
    call r6                                     /* os_time_dly(1 tick=10ms) */
    goto c2_wait

close_done_ok:
    r0 = 0
close_ret:
    {pc, r6-r4} = [sp++]
