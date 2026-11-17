/* 助听 L4 EQ 十段热更载荷 — 路 HAL4 / sim-hal4, 2026-11-17
 *
 * 「App 下发十段 EQ 曲线 → 固件 Eq1Eff 段级热更」的消费链（HAL3 MPO 的姊妹篇）：
 * BLE cmdid 0x30 写 VM key 31（keystore_hook 的 key-31 分支，本路新增）→
 * 本载荷读 key 31 → 整包先验（协议头 + 每段 freq/gain 门）→ 逐段构造 EQ_SEG
 * 44B 段包 → jlstream_set_node_param(0x737b, "Eq1Eff", 包, 44) → eq ioctl
 * 0x0607898e subcmd 7 分支 → 0x0607c6aa/0x0607c5c4 把 16B 段写进运行段数组
 * → 淡入参数驱动的逐步生效。
 *
 * 消息形状 = 固件两处生产者先例逐字节钉死（docs/hearing-aid-l4-eq.md §2）：
 *   - QCY 自定义 EQ 应用循环 0x0602503a（App 0x22 命令的消费端）：subcmd8 →
 *     subcmd3 → 10×subcmd7，全 44B 经 0x0607538a(0x737b, name, sp, 44)；
 *   - stream.bin 文件配置应用循环 0x06024f22（节点 open 装载文件曲线）：
 *     同形状 subcmd8/3/7×n。
 * 本载荷只发 subcmd 7（段级消息）：段数(10)与主增益(0)在节点 open 时已按
 * stream.bin §5 Eq1Eff 记录（blob @0xeac）装载，subcmd 8/3 无须重发；两处
 * 先例的 fade 区常量（packet+20 起 24B：{u8 10, u8 100, f32 0.1f, 余 0}）
 * 原样烘进模板。
 *
 * 只发 Eq1Eff（codex 参谋 ~/tmp/codex/202611172318-hal4/final.md Q6 + 本路
 * 独立复核）：stream.bin §0 边表（@0x2d0 起 8B 记录）解出 mic_effect 主链
 * ADC→NoiseGate→VolEff→Eq1Eff→Drc1Eff→Fshift→HowlingSuppress→Vchanger→
 * sound_splitter→…→DacEff——Eq1Eff 是 MPO（Drc1Eff）上游的输入 EQ；
 * Eq4Eff/Eq5Eff（subid 8/9，与 Eq1Eff 共享 §5 blob @0xeac）在 splitter 之后
 * 的输出支路（MPO 下游、DacEff 前），同曲线全发会在限幅器之后重复整形、
 * 改变 MPO 后输出电平——不做。
 *
 * key 31 协议（88B，与 App 侧 KeyStoreFrame 约定一致）：
 *   +0..1  魔数 "HA"（0x48 0x41）
 *   +2     版本 2
 *   +3     保留 0
 *   +4     段数，必须 == 10
 *   +5..7  保留 0
 *   +8+8i  段 i = {u16 freq LE(Hz), u16 pad(忽略), float32 gain LE(dB 位型)}
 *
 * 整包先验后才发第一条消息（codex：不要第九段才发现非法值、前八段已生效）：
 *   - 头两个字分别 == 0x00024148 / 0x0000000A（一次覆盖魔数/版本/保留/段数）；
 *   - 每段 freq 门 1..21599（官方 Eq1Eff blob 观测域，非算法验证上界）；
 *   - 每段 gain 幅值门：|bits| <= 0x42200000（40.0f 位型；正 float 位型单调，
 *     一个上界同时挡超范围/NaN/±Inf，对称覆盖 ±40dB，零浮点运算）。
 * 范围门的口径：MPO（Drc1Eff）在 EQ 下游兜底输出上限，但救不了上游系数
 * 异常——NaN 必须拒；±40dB 覆盖助听演示用到的 +39dB（L0 演示值）。
 *
 * 返回值极性（codex 纠正，本路复核 0x06078a0c r4=1 / 尾部 0x06078ba4）：
 * eq ioctl 0x2000F 常规返回 1（未报错），ctx+44 状态字非零时返回该字（如
 * open 失败 -2）；0x0607538a 可能透传 -12（分配失败）/ -22（名字无节点）/
 * 21（队列满）。本载荷每条消息都要求 r0 == 1，否则停止本轮（返回 2）——
 * r0==1 只证明「消息分发未报错」，不证明段参数已在音频块上生效（诚实边界）。
 *
 * 调用上下文与 apply_mpo 同域：0x0607538a 投递后同步等待（timeout=0 无限
 * 等待，流任务不跑则无界——见文档诚实边界），必须挂在流服务任务之外的
 * 上下文（本设计挂在按键 dispatch 链 open 成功路径，与固件 0x22 setter
 * 24 条同量级消息的先例同域）。
 *
 * 离线载荷：encode->decode 往返逐条零 diff（单测）；未在真机/完整仿真跑过
 * 段消息消费链，不构成「App 改 EQ 运行时生效」的运行验证声明（UNTESTED）。
 */

    /* —— 固件 API 锚点（静态钉死，不随布局变化） —— */
    .equ GET_CONFIG,      0x06000f88  /* r0=key, r1=buf, r2=len -> r0=读回长度(负=错误) */
    .equ SET_NODE_PARAM,  0x0607538a  /* r0=uuid16, r1=name16, r2=param, r3=len -> r0=消费者结果 */

    /* —— 段内数据地址（组合段两遍定址，与 MPO_*_ADDR 同契约） —— */
    .equ EQ_PKT_ADDR,   0x060e3500    /* eq_pkt_template 44B（占位，构建器回填） */
    .equ EQ_NAME_ADDR,  0x060e3540    /* eq_name 16B（占位，构建器回填） */

    /* —— 协议/参数常量 —— */
    .equ EQ_KEY_ID,        31         /* VM key 号（keystore_hook key-31 分支同 key） */
    .equ EQ_VALUE_LEN,     88         /* 8B 头 + 10 段 × 8B */
    .equ EQ_HDR_WORD0,     0x00024148 /* {'H','A',ver2,rsv0} */
    .equ EQ_HDR_WORD1,     0x0000000A /* {seg_count=10, pad×3} */
    .equ EQ_SEG_NUM,       10
    .equ EQ_FREQ_MAX,      21599      /* 官方 Eq1Eff blob 最高频点（观测域） */
    .equ EQ_GAIN_MAX_BITS, 0x42200000 /* 40.0f 位型（幅值上界，同界排 NaN/Inf） */
    .equ EQ_MASK_BITS,     0x7FFFFFFF /* 幅值掩码（清符号位） */
    .equ NODE_UUID_EQ,     0x737b     /* stream.bin §2 Eq1Eff 记录 uuid */
    .equ EQ_MSG_LEN,       44         /* EQ_SEG 段包（subcmd 7） */
    .equ EQ_IOCTL_OK,      1          /* eq ioctl 0x2000F 常规返回值 */
    .equ EQ_FRAME_STACK,   132        /* 88B key buf + 44B 段包 */

    .section .text.apply_eq,"ax",@progbits
    .global apply_eq
apply_eq:
    /* ABI：无参。返回 r0 = 0 全部 10 条已提交 / 1 跳过（未配置/整包先验拒）
     * / 2 中途停止（某条消息返回非 1：-12/-22/21/0/状态字）。
     * r4-r9 callee-saved（本函数保存）。 */
    [--sp] = {rets, r9-r4}
    sp += -EQ_FRAME_STACK
    r4 = sp                          /* r4 = 帧基：key buf @+0，段包 @+88 */

    /* 1. get_config(31, sp, 88) —— 无记录/长度不符视为未配置，静默跳过 */
    r0 = EQ_KEY_ID
    r1 = r4
    r2 = EQ_VALUE_LEN
    r6 = GET_CONFIG
    call r6
    if (r0 != EQ_VALUE_LEN) goto eq_skip

    /* 2. 协议头两字校验（魔数/版本/保留/段数/pad 一次覆盖） */
    r5 = [r4+0]
    r6 = EQ_HDR_WORD0
    if (r5 != r6) goto eq_skip
    r5 = [r4+4]
    r6 = EQ_HDR_WORD1
    if (r5 != r6) goto eq_skip

    /* 3. 整包先验：每段 freq∈[1,21599]、|gain 位型|≤40.0f —— 全过才发第一条 */
    r7 = EQ_GAIN_MAX_BITS
    r8 = EQ_FREQ_MAX
    r5 = r4 + 8                      /* 段游标 */
    r6 = EQ_SEG_NUM
eq_gate:
    r0 = h[r5+0] (u)                 /* freq u16 */
    if (r0 == 0) goto eq_skip
    if (r0 > r8) goto eq_skip
    r1 = [r5+4]                      /* gain 位型 */
    r2 = r1 & EQ_MASK_BITS
    if (r2 > r7) goto eq_skip
    r5 += 8
    r6 += -1
    if (r6 != 0) goto eq_gate

    /* 4. 十段发送循环：段包 = 模板 44B（subcmd/iir/q/fade 常量）+ 运行时
     *    写 idx(半字)/freq(零扩展 u16)/gain(位型原样) 三个字段 */
    r9 = SET_NODE_PARAM
    r5 = EQ_NAME_ADDR
    r8 = 0                           /* 段序号 */
eq_seg:
    r0 = r4 + 88                     /* 段包基 */
    r1 = EQ_PKT_ADDR
    r3 = 11
eq_copy:
    r2 = [r1++=4]
    [r0++=4] = r2
    r3 += -1
    if (r3 != 0) goto eq_copy
    r3 = r4 + 88
    h[r3+4] = r8                     /* seg.index = i */
    r2 = r8 << 3
    r2 += r4
    r2 += 8                          /* r2 = 帧 + 8i + 8 = 段 i 源 */
    r1 = h[r2+0] (u)                 /* freq u16（零扩展成 i32 字段） */
    [r3+8] = r1
    r1 = [r2+4]                      /* gain 位型原样拷贝 */
    [r3+12] = r1
    /* 5. jlstream_set_node_param(0x737b, "Eq1Eff", 包, 44) —— 同步等消费者 */
    r0 = NODE_UUID_EQ
    r1 = r5
    r2 = r3
    r3 = EQ_MSG_LEN
    call r9
    if (r0 != EQ_IOCTL_OK) goto eq_stop
    r8 += 1
    if (r8 != EQ_SEG_NUM) goto eq_seg
    r0 = 0                           /* 全部 10 条已提交 */
    goto eq_ret

eq_skip:
    r0 = 1
    goto eq_ret
eq_stop:
    r0 = 2
eq_ret:
    sp += EQ_FRAME_STACK
    {pc, r9-r4} = [sp++]

    .section .rodata.eq_pkt,"a",@progbits
    .global eq_pkt_template
eq_pkt_template:
    /* EQ_SEG 44B 段包模板：+0 u32 subcmd=7；+4..20 16B 段 {u16 index(运行时
     * 写), u16 iir=2(band_pass), i32 freq(运行时写), f32 gain(运行时写),
     * f32 q=0.7f}；+20..44 24B 淡入参数 {u8 10, u8 100, u16 0, f32 0.1f,
     * 16B 0}——段字段/淡入常量与官方 Eq1Eff blob（iir/q）及两处生产者
     * 先例的 fade 区（0x06025096/0x06024f68 同款立即数）逐字节同源。 */
    .long 0x00000007                 /* +0  subcmd 7 = EQ_SEG */
    .short 0x0000                    /* +4  index（运行时写 0..9） */
    .short 0x0002                    /* +6  iir_type = 2 band_pass（官方 blob 全段） */
    .long 0x00000000                 /* +8  freq（运行时写） */
    .long 0x00000000                 /* +12 gain（运行时写位型） */
    .long 0x3F333333                 /* +16 q = 0.7f（官方 blob 全段） */
    .byte 0x0A                       /* +20 fade 段数字节（先例常量 10） */
    .byte 0x64                       /* +21 fade 时间字节（先例常量 100） */
    .short 0x0000                    /* +22 */
    .long 0x3DCCCCCD                 /* +24 fade 步进 0.1f（先例常量） */
    .long 0x00000000                 /* +28 */
    .long 0x00000000                 /* +32 */
    .long 0x00000000                 /* +36 */
    .long 0x00000000                 /* +40 */

    .section .rodata.eq_name,"a",@progbits
    .global eq_name
eq_name:
    /* 节点名 16B（0x0607538a 只复制 16 字节名字域，NUL 截断 strcmp 匹配）：
     * "Eq1Eff" = stream.bin §2 实例目录 subid 10、mic_effect 主链 MPO 上游
     * 的输入 EQ（§0 边表 0x2e4/0x2ec）。 */
    .asciz "Eq1Eff"
    .space 9
