/* mic_effect (DHA / 辅听) 激活载荷 — 路 L / sim-l1impl, 2026-11-13
 *
 * 重写被链接器裁掉的 hearing_aid_player_open() = mic_effect_player_create(MIC_EFX_DHA)
 * 的精简版：只调固件里现成的 jlstream 框架 API，不依赖任何被裁函数。
 *
 * 模板：pc_mic 播放器 0x060339a4 的激活序列（同为 mic-ADC pipeline，静态立即数）；
 * API 调用约定由 tone 播放器 0x0600d9be + pc_mic 0x060339a4 + 固件内三组序列
 * （0x06016e84 / 0x06017078 / 0x06033504 / 0x060339ca）反汇编钉死，见
 * docs/hearing-aid-l1-payload.md。
 *
 * 离线载荷：encode->decode 往返逐条零 diff；未在真机/完整仿真跑通激活链，
 * 不构成「助听器可用」声明。
 *
 * 布局约束：本段按 EQ 载荷「追加 + fence」模式放入追加 JLFS 文件的代码体，
 * 运行地址由实施时的实际段地址决定。MIC_EFX_SLOT 是唯一随段地址变化的常量
 * （数据槽 = 追加段尾 4 字节）；双基址往返测试断言载荷对其余字节不敏感。
 *
 * 路 L1KEY 修正（2026-11-17，codex 参谋 + 主会话逐指令复核）：
 *   1. 跨 call 的 r0 参数恢复：pc_mic 模板在每个 API 调用前都 `r0=[r4+0]` 重载
 *      stream（player[0]=stream），本载荷把 stream 存 r4 后必须同样重载——
 *      旧版在 add_thread 返回 r0=0 后直接调 set_scene/set_notify/start，
 *      传的是 r0=err 而非 stream（0x06074a5e 入口 `r4=r0+80` 写字段，传零会
 *      访问低地址），三个调用点各补一条 `r0 = r4`。
 *   2. 失败时钟回收：get_pipeline_uuid("mic_effect") 一旦命中即 clock_alloc
 *      (24MHz)，parse/add_thread/start 任一失败都需 event_notify(11,
 *      "mic_effect") 触发 clock_free（与 close 载荷同款，docs §10.1）——
 *      旧版失败路径只 release stream 或直接返回，24MHz 时钟申请不闭合。
 */

    /* —— 实施时可调参数（本文件仅此三处随镜像布局变化） —— */
    .equ MIC_EFX_SLOT, 0x060e3200    /* 示例：紧跟 EQ 候选载荷末端的追加段；数据槽 4B */

    /* —— 固件现成 API 锚点（静态钉死，不随布局变化） —— */
    .equ GET_PIPELINE_UUID, 0x060057e6  /* r0=15(事件), r1=pipeline名串 -> r0=uuid */
    .equ PIPELINE_PARSE,    0x06074938  /* r0=uuid, r1=节点uuid -> r0=stream(0=失败) */
    .equ ADD_THREAD,        0x06074ec6  /* r0=stream, r1=线程id(0x1100=根), r2=flags, r3=extra -> r0=err */
    .equ SET_SCENE,         0x06074a5e  /* r0=stream, r1=scene, r2=场景恢复回调(0=无) */
    .equ SET_NOTIFY,        0x06074e64  /* r0=stream, r1=val; 写 stream[28] + 派发 0x20006 */
    .equ START,             0x06074ffe  /* r0=stream -> r0=err */
    .equ RELEASE,           0x06074204  /* r0=stream; 失败路径回收 */
    .equ EVENT_NOTIFY,      0x060057e6  /* = GET_PIPELINE_UUID 同一入口（tbh switch，
                                          事件 11=CLOSE_PLAYER -> clock_free "mic_effect"） */
    .equ EVENT_CLOSE_PLAYER, 11         /* SDK 枚举 STREAM_EVENT_CLOSE_PLAYER */

    .equ NAME_MIC_EFFECT,   0x060a1de2  /* 固件内 "mic_effect" 字符串 */
    .equ NODE_UUID_ADC,     53357       /* 0xD06D（SDK fallback 节点；"IIS0_RX1" 不在 QCY 固件） */
    .equ THREAD_ROOT,       4352        /* 0x1100 = pipeline_parse 自建根 pipeline 的 id */
    .equ THREAD_FLAGS,      131085      /* 0x2000D（pc_mic 根线程 flags） */
    .equ THREAD_EXTRA,      256         /* 0x100（pc_mic 根线程 extra） */
    .equ SCENE_HEARING_AID, 16          /* SDK 枚举 STREAM_SCENE_HEARING_AID（QCY 值未证，见文档§5） */
    .equ NOTIFY_VAL,        10          /* pc_mic 同款 set_notify 值（语义未钉死，见文档§5） */
    .equ ERR_INVAL,         -22         /* -EINVAL */

    .section .text.mic_effect_open,"ax",@progbits
    .global mic_effect_open
mic_effect_open:
    /* ABI：无参数。返回 r0 = 0 成功 / -22 失败。r4-r6 callee-saved（本函数保存）。 */
    [--sp] = {rets, r6-r4}

    /* 重入保护：已激活（数据槽存 stream 指针）则直接成功返回 */
    r0 = MIC_EFX_SLOT
    r5 = [r0+0]
    if (r5 != 0) goto open_done_ok

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

    /* 3. add_thread(stream, 根 0x1100, 0x2000D, 0x100) —— pc_mic 形状；失败即回收 */
    r1 = THREAD_ROOT
    r2 = THREAD_FLAGS
    r3 = THREAD_EXTRA
    r6 = ADD_THREAD
    call r6
    if (r0 != 0) goto open_err_release /* 失败：release stream + 回收时钟 */

    /* 4. set_scene(stream, HEARING_AID, 0) —— 无恢复回调 */
    r0 = r4                          /* 跨 call 重载 stream（pc_mic 同款 r0=[r4+0]） */
    r1 = SCENE_HEARING_AID
    r2 = 0
    r6 = SET_SCENE
    call r6

    /* 5. set_notify(stream, 10) —— pc_mic 同参数 */
    r0 = r4                          /* 跨 call 重载 stream */
    r1 = NOTIFY_VAL
    r6 = SET_NOTIFY
    call r6

    /* 6. err = start(stream) */
    r0 = r4                          /* 跨 call 重载 stream */
    r6 = START
    call r6
    if (r0 != 0) goto open_err_release /* 失败：release stream + 回收时钟 */

    /* 成功：记录 stream 到数据槽（重入保护 + 供后续 close 路径使用） */
    r0 = MIC_EFX_SLOT
    [r0+0] = r4
open_done_ok:
    r0 = 0
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
