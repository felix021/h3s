/* mic_effect (DHA / 辅听) 激活载荷 · lfix 修订版 — 路 LFIX-IMPL / sim-lfiximpl, 2026-11-19
 *
 * mic_effect_open.S 的 F1b 变体（候选 F1 = HAL5 合一 + lfix 修复，无 A3 时钟压舱）。
 * 基座载荷不动，只叠加 docs/hearing-aid-lfix-plan.md §4 的三个修复点：
 *
 *   A1  open 前 ANC 退出等待（超时取消本次 open）
 *       db3c(1,1) 由 toggle 桩先行调用（返回 ≠ 硬件已退）。本载荷在槽守卫之后
 *       轮询 ANC 过渡标志：b[[QCY+500]+29]==0（过渡清零）且
 *       b[[QCY+500]+33]==1（目标=OFF，内部枚举 OFF=1）才继续；否则小步延时
 *       0x6064efc(1 tick) 重试，上限 60 次 ≈ 590ms（ANC 退出 3ms-100ms+ 的
 *       裕量）。超时=前置条件未成立（codex 裁决）：直接返回 -22 取消本次
 *       open，槽保持 0，用户再按一次即可重试——不放行、不挂死。
 *       延时原语 0x6064efc = os_time_dly(n tick)（codex 实施审勘误：参数是
 *       OS tick 非毫秒，1 tick=10ms，仓内 s19-preboundaries §4.4 钉死）；ANC
 *       退出链以 (1)/(2)/(100) tick 实调（本路反汇编复核）。ctx 指针为 0
 *       （ANC 子系统未建）时 db3c 空转，本等待跳过。
 *   B1  set_scene 两参调用改值 10 → 16（保留调用，非删除）
 *       原载荷第 5 步 0x06074e64(stream,10) 是场景字节真正到达 ADC 的唯一
 *       入口（写 b[stream+28] + 派发 0x20006；第 4 步三参 0x06074a5e 只写
 *       stream+80 族字段不派发——codex 实施审反汇编核实）。删除会把场景
 *       留在默认值，故按 plan §4-B1 的「改值」形态把 PC_SPK(10) 改成
 *       HEARING_AID(16)：ADC GET_FMT 三态 16→44100（plan 预期态），
 *       VolEff 场景注册也归 HEARING_AID。字节数与原第 5 步相同（0B 净增）。
 *   B2  start 返回后显式写 ADC 通道数字节 =1
 *       node_ioctl(stream, 0x1100, 0x2000C, 1)。必须在 start 之后：ADC 开链
 *       （cmd 0x40001 兜底块 0x6032b6c-0x6032b94）会按 en_map 重算通道数字节
 *       b[priv+28]——先写初值会被覆盖；start 后设是对协商结果的最后覆盖
 *       （codex 修订）。cmd 用 0x2000C：ADC ioctl 跳表里 0x2000C 落
 *       0x06032a58 `b[priv+28]=arg` 直存；0x20007（SDK 头的 SET_CHANNEL 值）
 *       在 ADC 落 0x06032d6e 纯返回（codex 实施审离线执行勘误）。arg=1 是
 *       通道数量语义（17/18/20 是模式枚举，AUDIO_CH_NUM=ch>>4 编码不同）。
 *
 * 与基座的其余步骤（uuid → parse → SET_PRIV_FMT → set_scene(16) → start →
 * 失败路径回收）逐指令一致；错误路径回收语义不变（parse/start 失败 =
 * release + event_notify(11) 收回 24MHz 时钟）。
 *
 * 离线载荷：encode->decode 往返逐条零 diff；未在真机/完整仿真跑通激活链，
 * 不构成「助听器可用」声明。运行时行为（ANC 等待是否如期放行、SET_CHANNEL
 * 覆盖是否消左耳噪声）留给真机刷写后用户实测。
 */

    /* —— 实施时可调参数（本文件仅此一处随镜像布局变化） —— */
    .equ MIC_EFX_SLOT, 0x060e3200    /* 示例：与 close 载荷共用的数据槽 4B */

    /* —— 固件现成 API 锚点（静态钉死，不随布局变化） —— */
    .equ GET_PIPELINE_UUID, 0x060057e6  /* r0=15(事件), r1=pipeline名串 -> r0=uuid */
    .equ PIPELINE_PARSE,    0x06074938  /* r0=uuid, r1=节点uuid -> r0=stream(0=失败) */
    .equ NODE_IOCTL,        0x06074ec6  /* r0=stream, r1=目标节点id(0x1100=根/SOURCE),
                                          r2=cmd, r3=arg -> r0=err（lfix §1.2 勘误：
                                          原 ADD_THREAD 标签错，实为 jlstream_node_ioctl；
                                          与已在真机出声的 SET_PRIV_FMT 调用同构） */
    .equ SET_SCENE,         0x06074a5e  /* r0=stream, r1=scene, r2=场景恢复回调(0=无)；
                                          只写 stream+80/+84/+88 字段，不向节点派发
                                          （codex 实施审反汇编核实） */
    .equ SET_SCENE_NOTIFY,  0x06074e64  /* r0=stream, r1=场景值：写 b[stream+28] 并派发
                                          0x20006（NODE_IOC_SET_SCENE）——场景字节真正
                                          到达 ADC 的唯一入口（路 L1 误标 set_notify） */
    .equ START,             0x06074ffe  /* r0=stream -> r0=err */
    .equ RELEASE,           0x06074204  /* r0=stream; 失败路径回收 */
    .equ EVENT_NOTIFY,      0x060057e6  /* = GET_PIPELINE_UUID 同一入口（tbh switch，
                                          事件 11=CLOSE_PLAYER -> clock_free "mic_effect"） */
    .equ EVENT_CLOSE_PLAYER, 11         /* SDK 枚举 STREAM_EVENT_CLOSE_PLAYER */

    .equ NAME_MIC_EFFECT,   0x060a1de2  /* 固件内 "mic_effect" 字符串 */
    .equ NODE_UUID_ADC,     53357       /* 0xD06D（SDK fallback 节点） */
    .equ THREAD_ROOT,       4352        /* 0x1100 = pipeline_parse 自建根 pipeline 的 id */
    .equ SET_PRIV_FMT_CMD,  131085      /* 0x2000D = NODE_IOC_SET_PRIV_FMT */
    .equ PRIV_FMT_POINTS,   256         /* 0x100（官方 DHA 同参：HEARING_AID 点数） */
    .equ SCENE_HEARING_AID, 16          /* SDK 枚举 STREAM_SCENE_HEARING_AID */
    .equ SET_CHANNEL_CMD,   131084      /* 0x2000C = ADC 通道数字节直存 ioctl
                                          （codex 实施审勘误：0x20007 在 ADC 跳表
                                          落 0x06032d6e 纯返回；0x2000C 落
                                          0x06032a58 `b[priv+28]=arg` 直存——经
                                          root 类 0x06076d24-30 透传到 D06D plug） */
    .equ CHANNEL_COUNT_1,   1           /* 通道数量语义（mono） */
    .equ ERR_INVAL,         -22         /* -EINVAL */

    /* —— A1：ANC 过渡等待锚点（docs/hearing-aid-lfix-plan.md §4-A1/§8） —— */
    .equ QCY_STRUCT_BASE,   0x1265c0    /* QCY 全局设备结构基（RAM 槽证据同源） */
    .equ ANC_CTX_PTR_OFF,   500         /* ANC ctx 指针字段 [QCY+500]（db3c 反汇编） */
    .equ ANC_TRANS_OFF,     29          /* b[ctx+29] 过渡标志：db3c 置 1，退出链清 0 */
    .equ ANC_TARGET_OFF,    33          /* b[ctx+33] 目标模式：内部枚举 OFF=1 */
    .equ ANC_MODE_OFF,      1           /* 内部 ANC 模式 OFF（勿与线上 ID 混淆） */
    .equ ANC_DELAY_FN,      0x06064efc  /* os_time_dly(r0=n tick)：任务睡眠让出调度
                                          （s19-preboundaries §4.4 钉死：h[TCB+6]=n
                                          tick；1 tick = 10ms） */
    .equ ANC_DELAY_TICKS,   1           /* 每次轮询睡 1 tick = 10ms */
    .equ ANC_WAIT_TRIES,    60          /* 上限 60 次 ≈ 590ms（超时取消本次 open） */

    .section .text.mic_effect_open,"ax",@progbits
    .global mic_effect_open
mic_effect_open:
    /* ABI：无参数。返回 r0 = 0 成功 / -22 失败（含 A1 超时取消）。r4-r6 callee-saved。 */
    [--sp] = {rets, r6-r4}

    /* 重入保护：已激活（数据槽存 stream 指针）则直接成功返回 */
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
    if (r5 == 0) goto open_err_cancel           /* ~590ms 超时：取消本次 open */
    r0 = ANC_DELAY_TICKS
    call r6                                     /* os_time_dly(1 tick=10ms) */
    goto anc_wait
anc_settled:

    /* 1. uuid = get_pipeline_uuid(15, "mic_effect") —— 固件分支 @0x06005982 存在；
     *    该分支命中即 clock_alloc(24MHz)，后续任何失败都必须 event_notify(11) 回收 */
    r0 = 15
    r1 = NAME_MIC_EFFECT
    r6 = GET_PIPELINE_UUID
    call r6
    r0 = r0.l (u)                    /* 高 16 位是 clock_alloc 结果，取低 16 位 uuid */
    if (r0 == 0) goto open_err_inval /* uuid==0 = 名字未命中，clock_alloc 未发生，无需回收 */
    r4 = r0                          /* r4 = pipeline uuid */

    /* 2. stream = pipeline_parse(uuid, NODE_UUID_ADC) —— stream.bin 有 0x9c2d 描述符 */
    r1 = NODE_UUID_ADC
    r6 = PIPELINE_PARSE
    call r6
    r4 = r0                          /* r4 = stream */
    if (r4 == 0) goto open_err_clock /* parse 失败：时钟已申请，须回收 */

    /* 3. node_ioctl(stream, 根 0x1100, SET_PRIV_FMT, 256) —— 官方 DHA 同参
     *    （lfix §1.2：原 add_thread 标签勘误，语义等价 SET_PRIV_FMT(256)） */
    r0 = r4                          /* 跨 call 重载 stream（pc_mic 同款 r0=[r4+0]） */
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

    /* B1. 两参场景调用改值：0x06074e64(stream, 16) —— 场景字节 + 0x20006 派发
     *    都归 HEARING_AID（原值 10=PC_SPK 是抄 pc_mic 的错值；这个调用本身
     *    必须保留——它是场景到达 ADC 的唯一入口）。 */
    r0 = r4                          /* 跨 call 重载 stream */
    r1 = SCENE_HEARING_AID
    r6 = SET_SCENE_NOTIFY
    call r6

    /* 5. err = start(stream) */
    r0 = r4                          /* 跨 call 重载 stream */
    r6 = START
    call r6
    if (r0 != 0) goto open_err_release /* 失败：release stream + 回收时钟 */

    /* B2. node_ioctl(stream, 根 0x1100, 0x2000C, 1) —— start 返回后对协商
     *    结果做最后覆盖（先设会被 ADC 开链重算覆盖，codex 修订）；0x2000C
     *    在 ADC 直存 b[priv+28]=arg（通道数量）。返回值不检查：协商默认即
     *    mono 时本调用为无害重申。 */
    r0 = r4                          /* 跨 call 重载 stream */
    r1 = THREAD_ROOT
    r2 = SET_CHANNEL_CMD
    r3 = CHANNEL_COUNT_1
    r6 = NODE_IOCTL
    call r6

    /* 成功：记录 stream 到数据槽（重入保护 + 供后续 close 路径使用） */
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
open_err_inval:
    r0 = ERR_INVAL
open_ret:
    {pc, r6-r4} = [sp++]
