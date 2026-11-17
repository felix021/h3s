/* 助听 L3 MPO 参数消费载荷 — 路 HAL3 / sim-hal3, 2026-11-17
 *
 * 「App 下发助听参数 → 固件音频参数生效」的消费链最小闭环（MPO 部分）：
 * BLE cmdid 0x30 写 VM key 30（r4-keyhook 已接驳）→ 本载荷读 key 30 →
 * 校验版本化协议 → 按 L2 曲线规则构造 wdrc 76B 参数包 →
 * jlstream_set_node_param(0xdefe, "Drc1Eff", 包, 76) → 框架消息 → wdrc ioctl
 * 0x06078d0a 事件 0x2000F 分支整包复制 + updata 标志 → 下一音频块生效。
 *
 * 消费时机（codex 参谋 ~/tmp/codex/202611172140-hal3/final.md 方案 a2）：
 * 挂在 open 成功之后（key7 toggle 桩在 mic_effect_open 返回 0 时调用本载荷），
 * 即「已保存，下次开启辅听时应用」。运行中即时调节需把应用请求串行化到
 * 管理 open/close 的同一任务（下一阶段，本载荷不含）。
 *
 * key 30 协议（8B，与 App 侧 KeyStoreFrame 约定一致）：
 *   +0..1  魔数 "HA"（0x48 0x41）
 *   +2     版本 1
 *   +3     保留 0（非零拒绝）
 *   +4..7  float32 MPO 值 M（little-endian 位型）
 *
 * 76B 包 = 官方 Drc1Eff 模板（stream.bin §5 @0x1038，本段 .rodata 内嵌，
 * 构建时与官方字节断言一致——唯一差异 th[2].in 110.3f→120.0f）运行时写
 * 三个 float：th[1].in=+16、th[1].out=+20、th[2].out=+28 都写 M。
 * 曲线 (0.3,0.3)/(M,M)/(120,M)：段0 单位增益到 M，段1 斜率 0、截距 M，
 * 任意过驱动输入映射 M（L2 docs/hearing-aid-l2-mpo.md §1 语义）。th[2].in
 * 固定 120.0f 而非 M+10.3：末段斜率 0 下 in 值不参与输出，只需严格递增
 * （M 范围上界 120 独占保证 120>M，分母非零）。
 *
 * M 范围校验用 IEEE754 位型的有符号比较（0.3f < M < 120.0f 独占）：
 * 负 float 位型（最高位 1）在有符号比较中为负 → 落 ≤0.3f 拒绝；NaN/±Inf
 * 位型 ≥ 0x7F800000 → 落 ≥120.0f 拒绝。一次边界对即排除全部非法位型。
 *
 * 调用上下文要求：0x0607538a（=jlstream_set_node_param）投递后同步等待
 * 消费者（0x6074192 投递 + 0x60741e2 等待 semaphore，codex 复核）——必须在
 * 流服务任务之外的上下文调用（本设计挂在按键 dispatch 链 = cmd7 handler
 * 上下文，与固件 0x22 setter 同域）；在流服务任务自身上下文调用会等自己。
 *
 * 离线载荷：encode->decode 往返逐条零 diff（单测）；未在真机/完整仿真跑过
 * 参数应用链，不构成「App 改 MPO 运行时生效」的运行验证声明（候选 UNTESTED）。
 */

    /* —— 固件 API 锚点（静态钉死，不随布局变化） —— */
    .equ GET_CONFIG,      0x06000f88  /* r0=key, r1=buf, r2=len -> r0=读回长度(负=错误) */
    .equ SET_NODE_PARAM,  0x0607538a  /* r0=uuid16, r1=name16, r2=param, r3=len -> r0=结果 */

    /* —— 段内数据地址（组合段两遍定址：构建器量出偏移后回填实际运行地址，
     *    与 MIC_EFX_SLOT 同模式；独立汇编时为下方占位值） —— */
    .equ MPO_TEMPLATE_ADDR, 0x060e3400 /* mpo_template 76B（占位，构建器回填） */
    .equ MPO_NAME_ADDR, 0x060e3450     /* mpo_name 16B（占位，构建器回填） */

    /* —— 协议/参数常量 —— */
    .equ KEY_ID,          30          /* VM key 号（r4-keyhook 同 key） */
    .equ VALUE_LEN,       8
    .equ HA_MAGIC_H,      0x48        /* 'H' */
    .equ HA_MAGIC_A,      0x41        /* 'A' */
    .equ HA_VER,          1
    .equ NODE_UUID_WDRC,  0xdefe      /* stream.bin §2 Drc1Eff 记录 uuid */
    .equ MSG_LEN,         76          /* wdrc_param_tool_set 整包（无 subcommand 头） */
    .equ MPO_MIN_BITS,    0x3E99999A  /* 0.3f 位型（下界，独占） */
    .equ MPO_MAX_BITS,    0x42F00000  /* 120.0f 位型（上界，独占；同界排 NaN/Inf） */
    .equ FRAME_STACK,     88          /* 8B key buf + 76B 参数包（4 对齐） */

    .section .text.apply_mpo,"ax",@progbits
    .global apply_mpo
apply_mpo:
    /* ABI：无参。返回 r0 = 0 已提交框架 / 1 跳过（未配置/格式拒/范围拒）。
     * 框架结果（消费者写 msg+20）由 0x0607538a 透传到本 r0：调用方可区分
     * 「未配置跳过」与「提交但消费者报错」。r4-r6 callee-saved（本函数保存）。 */
    [--sp] = {rets, r6-r4}
    sp += -FRAME_STACK
    r4 = sp                          /* r4 = 帧基：key buf @+0，参数包 @+8 */

    /* 1. get_config(30, sp, 8) —— 无记录/长度不符视为未配置，静默跳过 */
    r0 = KEY_ID
    r1 = r4
    r2 = VALUE_LEN
    r6 = GET_CONFIG
    call r6
    if (r0 != VALUE_LEN) goto mpo_skip

    /* 2. 协议头 {magic, ver, rsv} 逐字节校验 */
    r5 = b[r4+0] (u)
    if (r5 != HA_MAGIC_H) goto mpo_skip
    r5 = b[r4+1] (u)
    if (r5 != HA_MAGIC_A) goto mpo_skip
    r5 = b[r4+2] (u)
    if (r5 != HA_VER) goto mpo_skip
    r5 = b[r4+3] (u)
    if (r5 != 0) goto mpo_skip

    /* 3. M 位型范围 (0.3f, 120.0f) 独占——有符号比较即排除负数/NaN/Inf */
    r5 = [r4+4]
    r6 = MPO_MIN_BITS
    if (r5 <= r6) goto mpo_skip
    r6 = MPO_MAX_BITS
    if (r5 >= r6) goto mpo_skip

    /* 4. 模板 76B → 栈参数包（19 字循环拷贝；固件 handler 侧同长度 rep 拷贝
     *    的调用方版本，此处不依赖 rep 编码宽度），写 M 到三个拐点字 */
    r0 = r4 + 8
    r1 = MPO_TEMPLATE_ADDR
    r2 = 19
mpo_copy:
    r3 = [r1++=4]
    [r0++=4] = r3
    r2 += -1
    if (r2 != 0) goto mpo_copy
    r3 = r4 + 8
    [r3+16] = r5                     /* th[1].in  = M */
    [r3+20] = r5                     /* th[1].out = M */
    [r3+28] = r5                     /* th[2].out = M */

    /* 5. jlstream_set_node_param(0xdefe, "Drc1Eff", 包, 76) —— 同步等消费者 */
    r0 = NODE_UUID_WDRC
    r1 = MPO_NAME_ADDR
    r2 = r3
    r3 = MSG_LEN
    r6 = SET_NODE_PARAM
    call r6
    goto mpo_ret                     /* r0 = 框架/消费者结果，透传 */

mpo_skip:
    r0 = 1
mpo_ret:
    sp += FRAME_STACK
    {pc, r6-r4} = [sp++]

    .section .rodata.mpo_template,"a",@progbits
    .global mpo_template
mpo_template:
    /* 官方 Drc1Eff 76B（stream.bin sha 19c9deeb §5 @0x1038 逐字节，单测断言）；
     * 唯一差异：th[2].in（+24）110.3f → 120.0f（尾点抬高，见头部注释）。 */
    .long 0x00000000                 /* +0  is_bypass = 0 */
    .long 0x00C80064                 /* +4  attack=100 / release=200 */
    .long 0x3E99999A                 /* +8  th[0].in  = 0.3f */
    .long 0x3E99999A                 /* +12 th[0].out = 0.3f */
    .long 0x42B4999A                 /* +16 th[1].in  = 90.3f（运行时写 M） */
    .long 0x42B4999A                 /* +20 th[1].out = 90.3f（运行时写 M） */
    .long 0x42F00000                 /* +24 th[2].in  = 120.0f（官方 110.3f 抬尾） */
    .long 0x42DC999A                 /* +28 th[2].out = 110.3f（运行时写 M） */
    .long 0x42B4999A                 /* +32 th[3].in  = 90.3f（官方残留，tnum=3 不消费） */
    .long 0x42B4999A                 /* +36 th[3].out */
    .long 0x42B4999A                 /* +40 th[4].in */
    .long 0x42B4999A                 /* +44 th[4].out */
    .long 0x42B4999A                 /* +48 th[5].in */
    .long 0x42B4999A                 /* +52 th[5].out */
    .long 0x00000000                 /* +56 inputgain = 0 */
    .long 0x00000000                 /* +60 outputgain = 0 */
    .long 0x41C80000                 /* +64 rms_time = 25.0f */
    .long 0x01000003                 /* +68 tnum=3 / rms_time(u8)=0 / algorithm=0(PEAK) / mode=1(TWOPOINT) */
    .long 0x00010000                 /* +72 holdtime=0 / reserved 01 00（官方原样） */

    .section .rodata.mpo_name,"a",@progbits
    .global mpo_name
mpo_name:
    /* 节点名 16B（0x0607538a 只复制 16 字节名字域；"Drc1Eff" 与 stream.bin
     * §2 实例目录 @0x7ec 的 name 字段一致） */
    .asciz "Drc1Eff"
    .space 8
