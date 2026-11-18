/* mic_effect (DHA / 辅听) 激活载荷 · lfix 修订版 + A3 时钟压舱 — 路 LFIX-IMPL, 2026-11-19
 *
 * 候选 F1a = F1（HAL5 合一 + lfix：A1+B1+B2）+ A3 clock_alloc("lfix_hold",128M)。
 * 与 mic_effect_open_lfix.S（F1b，无压舱）的差异仅三处（plan §4-A3，codex 所有权
 * 纪律采纳）：
 *
 *   A3a  uuid 命中后、构建音频流（pipeline_parse）之前：
 *        clock_alloc("lfix_hold", 128000000) —— Σ = base24 + mic24 + hold128
 *        → DVFS 目标恒为 160M 封顶值，抑制运行期升降挡振荡（esco 通话 128M
 *        同款效果的现成先例）。名串放追加段 rodata（本段尾），地址构建器回填。
 *   A3b  open 失败共享尾（open_err_clock，event_notify(11) 之后）：
 *        clock_free("lfix_hold") —— parse/ioctl/scene/start 任一失败都释放压舱。
 *        uuid==0 路径（open_err_inval）在 A3a 之前，不释放（未申请）。
 *   A3c  对应的 close 侧 free 在 mic_effect_close_lfix.S（stop/release/notify
 *        完成后），与 A3a 恰好一次配对；重复 open 被槽守卫挡住不会二次申请。
 *
 * 诚实边界（plan §4-A3 codex 裁决）：首次升频切换不可避免（每轮 open 仍有
 * 一次 24→160M）；A3 成功≠C1 坐实（160M 同时改善算力/调度，只能判「高频保持
 * 相关」）；长期续航/温升未测——A3 是短期诊断档，不是最终产品方案。
 *
 * 其余（A1 等待 / B1 删 set_scene(10) / B2 start 后 SET_CHANNEL(1) / 错误路径
 * 回收）与 F1b 逐指令一致，完整注释见 mic_effect_open_lfix.S。
 */

    /* —— 实施时可调参数（本文件仅此两处随镜像布局变化：槽 + 压舱名串） —— */
    .equ MIC_EFX_SLOT, 0x060e3200    /* 示例：与 close 载荷共用的数据槽 4B */
    .equ LFIX_HOLD_NAME_ADDR, 0x060e3400 /* "lfix_hold" rodata（占位，构建器回填） */

    /* —— 固件现成 API 锚点（静态钉死，不随布局变化） —— */
    .equ GET_PIPELINE_UUID, 0x060057e6  /* r0=15(事件), r1=pipeline名串 -> r0=uuid */
    .equ PIPELINE_PARSE,    0x06074938  /* r0=uuid, r1=节点uuid -> r0=stream(0=失败) */
    .equ NODE_IOCTL,        0x06074ec6  /* r0=stream, r1=目标节点id(0x1100=根/SOURCE),
                                          r2=cmd, r3=arg -> r0=err（lfix §1.2 勘误） */
    .equ SET_SCENE,         0x06074a5e  /* r0=stream, r1=scene, r2=场景恢复回调(0=无) */
    .equ START,             0x06074ffe  /* r0=stream -> r0=err */
    .equ RELEASE,           0x06074204  /* r0=stream; 失败路径回收 */
    .equ EVENT_NOTIFY,      0x060057e6  /* 事件分发（11=CLOSE_PLAYER -> clock_free） */
    .equ EVENT_CLOSE_PLAYER, 11         /* SDK 枚举 STREAM_EVENT_CLOSE_PLAYER */
    .equ CLOCK_ALLOC,       0x06000978  /* r0=名串, r1=频率Hz（记账+DVFS 重算） */
    .equ CLOCK_FREE,        0x0600572c  /* r0=名串；按名摘链（未命中=no-op 仅重算） */

    .equ NAME_MIC_EFFECT,   0x060a1de2  /* 固件内 "mic_effect" 字符串 */
    .equ NODE_UUID_ADC,     53357       /* 0xD06D（SDK fallback 节点） */
    .equ THREAD_ROOT,       4352        /* 0x1100 = pipeline_parse 自建根 pipeline 的 id */
    .equ SET_PRIV_FMT_CMD,  131085      /* 0x2000D = NODE_IOC_SET_PRIV_FMT */
    .equ PRIV_FMT_POINTS,   256         /* 0x100（官方 DHA 同参：HEARING_AID 点数） */
    .equ SCENE_HEARING_AID, 16          /* SDK 枚举 STREAM_SCENE_HEARING_AID */
    .equ SET_CHANNEL_CMD,   131079      /* 0x20007 = NODE_IOC_SET_CHANNEL */
    .equ CHANNEL_COUNT_1,   1           /* 通道数量语义（mono） */
    .equ ERR_INVAL,         -22         /* -EINVAL */

    /* —— A3：时钟压舱参数 —— */
    .equ LFIX_HOLD_HZ,      128000000   /* 128MHz：Σ 顶到 160M 封顶（esco 同款） */

    /* —— A1：ANC 过渡等待锚点（docs/hearing-aid-lfix-plan.md §4-A1/§8） —— */
    .equ QCY_STRUCT_BASE,   0x1265c0    /* QCY 全局设备结构基（RAM 槽证据同源） */
    .equ ANC_CTX_PTR_OFF,   500         /* ANC ctx 指针字段 [QCY+500]（db3c 反汇编） */
    .equ ANC_TRANS_OFF,     29          /* b[ctx+29] 过渡标志：db3c 置 1，退出链清 0 */
    .equ ANC_TARGET_OFF,    33          /* b[ctx+33] 目标模式：内部枚举 OFF=1 */
    .equ ANC_MODE_OFF,      1           /* 内部 ANC 模式 OFF（勿与线上 ID 混淆） */
    .equ ANC_DELAY_FN,      0x06064efc  /* 任务上下文延时 r0=ms（ANC 退出链自用原语） */
    .equ ANC_DELAY_MS,      10          /* 每次轮询间隔 */
    .equ ANC_WAIT_TRIES,    60          /* 上限 60 次 ≈ 600ms（超时取消本次 open） */

    .section .text.mic_effect_open,"ax",@progbits
    .global mic_effect_open
    .global lfix_hold_name
mic_effect_open:
    /* ABI：无参数。返回 r0 = 0 成功 / -22 失败（含 A1 超时取消）。r4-r6 callee-saved。 */
    [--sp] = {rets, r6-r4}

    /* 重入保护：已激活（数据槽存 stream 指针）则直接成功返回（不重复申请压舱） */
    r0 = MIC_EFX_SLOT
    r5 = [r0+0]
    if (r5 != 0) goto open_done_ok

    /* A1. ANC 退出等待：db3c(1,1) 已由 toggle 桩调用（写 b[ctx+29]=1、b[ctx+33]=1），
     *    等硬件退出链清过渡标志且目标仍为 OFF。ctx==0（子系统未建，db3c 空转）
     *    直接跳过等待。 */
    r6 = QCY_STRUCT_BASE
    r4 = [r6+ANC_CTX_PTR_OFF]
    if (r4 == 0) goto anc_settled
    r5 = ANC_WAIT_TRIES
    r6 = ANC_DELAY_FN
anc_wait:
    r0 = b[r4+ANC_TRANS_OFF] (u)
    if (r0 != 0) goto anc_poll
    r0 = b[r4+ANC_TARGET_OFF] (u)
    if (r0 == ANC_MODE_OFF) goto anc_settled   /* 过渡清零 + 目标 OFF：放行 */
anc_poll:
    r5 += -1
    if (r5 == 0) goto open_err_cancel           /* ~600ms 超时：取消本次 open */
    r0 = ANC_DELAY_MS
    call r6                                     /* 0x6064efc(10)：任务上下文睡眠 */
    goto anc_wait
anc_settled:

    /* 1. uuid = get_pipeline_uuid(15, "mic_effect") —— 命中分支即 clock_alloc(24MHz) */
    r0 = 15
    r1 = NAME_MIC_EFFECT
    r6 = GET_PIPELINE_UUID
    call r6
    r0 = r0.l (u)                    /* 高 16 位是 clock_alloc 结果，取低 16 位 uuid */
    if (r0 == 0) goto open_err_inval /* uuid==0：名字未命中，无任何已取资源 */
    r4 = r0                          /* r4 = pipeline uuid */

    /* A3a. clock_alloc("lfix_hold", 128MHz) —— uuid 命中后、构建音频流之前；
     *      申请失败（堆尽）不记账直接继续（clock_free 未命中名为无害 no-op，
     *      0x0600572c 反汇编核实）。 */
    r0 = LFIX_HOLD_NAME_ADDR
    r1 = LFIX_HOLD_HZ
    r6 = CLOCK_ALLOC
    call r6

    /* 2. stream = pipeline_parse(uuid, NODE_UUID_ADC) —— r0 已被 A3a 破坏，
     *    跨 call 重载 uuid（与 L1KEY 的 stream 重载同一纪律） */
    r0 = r4
    r1 = NODE_UUID_ADC
    r6 = PIPELINE_PARSE
    call r6
    r4 = r0                          /* r4 = stream */
    if (r4 == 0) goto open_err_clock /* parse 失败：时钟已申请（mic 24M + hold），须回收 */

    /* 3. node_ioctl(stream, 根 0x1100, SET_PRIV_FMT, 256) —— 官方 DHA 同参 */
    r0 = r4                          /* 跨 call 重载 stream */
    r1 = THREAD_ROOT
    r2 = SET_PRIV_FMT_CMD
    r3 = PRIV_FMT_POINTS
    r6 = NODE_IOCTL
    call r6
    if (r0 != 0) goto open_err_release /* 失败：release stream + 回收时钟 */

    /* 4. set_scene(stream, HEARING_AID, 0) —— 无恢复回调 */
    r0 = r4                          /* 跨 call 重载 stream */
    r1 = SCENE_HEARING_AID
    r2 = 0
    r6 = SET_SCENE
    call r6

    /* B1. 原第 5 步 set_scene(stream,10)（误标 set_notify）已删除 */

    /* 5. err = start(stream) */
    r0 = r4                          /* 跨 call 重载 stream */
    r6 = START
    call r6
    if (r0 != 0) goto open_err_release /* 失败：release stream + 回收时钟 */

    /* B2. node_ioctl(stream, 根 0x1100, SET_CHANNEL, 1) —— start 返回后对协商
     *    结果做最后覆盖；arg=1 是通道数量语义。返回值不检查。 */
    r0 = r4                          /* 跨 call 重载 stream */
    r1 = THREAD_ROOT
    r2 = SET_CHANNEL_CMD
    r3 = CHANNEL_COUNT_1
    r6 = NODE_IOCTL
    call r6

    /* 成功：记录 stream 到数据槽；压舱保持至 close（A3c）释放 */
    r0 = MIC_EFX_SLOT
    [r0+0] = r4
open_done_ok:
    r0 = 0
    goto open_ret

open_err_cancel:
    /* A1 超时：尚未取任何资源（无 uuid/clock/hold），直接取消本次 open */
    r0 = ERR_INVAL
    goto open_ret

open_err_release:
    r0 = r4                          /* r0 = stream */
    r6 = RELEASE
    call r6
open_err_clock:
    /* 回收 clock_alloc("mic_effect", 24MHz)（CLOSE_PLAYER 分支 strcmp 命中 -> clock_free） */
    r0 = EVENT_CLOSE_PLAYER
    r1 = NAME_MIC_EFFECT
    r6 = EVENT_NOTIFY
    call r6
    /* A3b. 压舱释放：凡走到本尾（parse/ioctl/scene/start 失败）hold 已申请。
     *      未申请即 free 无害（clock_free 按名摘链，未命中=no-op）。 */
    r0 = LFIX_HOLD_NAME_ADDR
    r6 = CLOCK_FREE
    call r6
open_err_inval:
    r0 = ERR_INVAL
open_ret:
    {pc, r6-r4} = [sp++]

lfix_hold_name:
    /* A3 压舱时钟名串（clock_alloc/clock_free 按名记账；与 "mic_effect" 不同名
     * 才不会被 CLOSE_PLAYER 分支顺带释放）。10B，地址由构建器回填到两处
     * LFIX_HOLD_NAME_ADDR（本文件 + mic_effect_close_lfix.S）。 */
    .asciz "lfix_hold"
