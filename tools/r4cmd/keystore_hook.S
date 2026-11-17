/* VM key-store App 命令 handler 载荷 — 路 AQ / sim-r4hook, 2026-11-14
 *                                     路 HAL4 key-31 扩展, 2026-11-17
 *
 * R4「App 写配置 + 固件读配置」的固件侧最小 setter：把一条 App 自定义命令接到
 * 真实的 VM get/set_config 分派器上。HAL4 起管理两个 key（按 data_len 与
 * data[0] 的 key 字节分路）：
 *
 *   写：data = [30, v0..v7]（len=9）  → set_config(30, data+1, 8)
 *       回执 n=1：resp[2] = 0(成功)/1(set_config 返回非 0)
 *   写：data = [31, v0..v87]（len=89）→ set_config(31, data+1, 88)   ← HAL4
 *       回执 n=1：resp[2] = 0/1（同上；key 31 = 助听 EQ 十段 88B 版本化协议，
 *       消费端见 tools/hearing-aid/apply_eq.S / docs/hearing-aid-l4-eq.md）
 *   读：data = [30]（len=1）         → get_config(30, resp+2, 8)
 *       回执 n=8：resp[2..9] = 存的 8 字节（读回长度 != 8 → 静默不回）
 *   读：data = [31]（len=1）         → get_config(31, resp+2, 88)    ← HAL4
 *       回执 n=88（0x58）：resp[2..89]（读回长度 != 88 → 静默不回；
 *       88B 值 + 2B 头 = 90B，分发器 128B 响应缓冲装得下）
 *   其它形状：返回 0（分发器 ifs (n<1) 跳过响应，与 0x1d setter 对 len<2 的
 *   「什么都不写直接返回 0」同一行为）。
 *
 * 调用 ABI（RCSP 自定义命令分发器写路径，docs/cmd-0x17-0x1d-layout.md §1 路P 钉死；
 * 异步投递路径经 trampoline 0x060256aa 以同一四参形状调起，本路静态复核）：
 *   setter(r0=响应缓冲, r1=缓冲容量128, r2=data指针, r3=data_len) → r0=回执字节数 n
 *   分发器随后覆写 resp[0]=cmdid、resp[1]=n，只发送 resp[0..n+2)——所以本载荷把
 *   回执数据写在 resp+2 起，写路径用字节存储 b[resp+2]=status（路 L3 勘误：旧版
 *   字存储 [resp+0]=status 实际把 status 落在 resp[0]，resp[2] 恒为 0，失败也被
 *   App 解析成成功；分发器覆写点 0x0600f94a/0x0600f94e 为 b[sp+44]/b[sp+45]）。
 *
 * 固件锚点：
 *   - set_config 0x06001f12 / get_config 0x06000f88：路 AB（docs/vm-key-roundtrip.md）
 *     在函数级仿真里直调过全链（写→同机读回→重启重扫仍在），ABI (key, buf, len)。
 *   - VM 后端对 key 30/31 无需注册（路 K：key-1<256 全命中，set 仅校验 len<=4095；
 *     88B 远低于上限）。key 31 与 key 30 同属 27..36 空 key 段（路 K 扫描）。
 *
 * 零重定位：除两个 API 锚点 li.u32 外无绝对地址，段基址无关（单测钉死）。
 * callee-saved r4-r6 序言/尾声保存（r7=注册表项指针是分发器跨 setter 调度的
 * 活寄存器，0x0600f946 在调用后仍读 b[r7+24]，本载荷不碰 r7）。
 *
 * 离线载荷：encode->decode 往返逐条零 diff；未在真机/仿真跑过命令分发链，
 * 不构成「App 可读写 key 30/31」的运行验证声明（候选 UNTESTED 不刷机）。
 * HAL4 勘误口径：key-31 分支改变了载荷字节，r4-keyhook 家族候选以 HAL4
 * 路产出为准（路 L3 候选 sha 88483a67… 就此作废，同路 L3 勘误先例）。
 */

    /* —— 固件 API 锚点（静态钉死，不随布局变化） —— */
    .equ GET_CONFIG,  0x06000f88  /* r0=key, r1=buf, r2=len -> r0=读回长度(负=错误) */
    .equ SET_CONFIG,  0x06001f12  /* r0=key, r1=buf, r2=len -> r0=0 成功(负=错误) */

    /* —— 命令形状常量（与 App 侧 KeyStoreFrame 约定一致） —— */
    .equ KEY_ID,        30        /* MPO 用 VM key 号（路 K/AB：空 key） */
    .equ EQ_KEY_ID,     31        /* EQ 十段用 VM key 号（HAL4；同空 key 段） */
    .equ VALUE_LEN,     8         /* key 30 值长度（路 AB 往返实证） */
    .equ EQ_VALUE_LEN,  88        /* key 31 值长度（8B 头 + 10 段 × 8B） */
    .equ WRITE_LEN,     9         /* key 30 写形状 data_len = key(1) + value(8) */
    .equ EQ_WRITE_LEN,  89        /* key 31 写形状 data_len = key(1) + value(88) */
    .equ READ_LEN,      1         /* 读形状 data_len = key(1) */

    .section .text.r4_keystore_hook,"ax",@progbits
    .global r4_keystore_hook
r4_keystore_hook:
    /* ABI：r0=resp_buf, r1=cap, r2=data, r3=len → r0=回执 n（<1 = 无响应）。 */
    [--sp] = {rets, r6-r4}
    r4 = r0                          /* r4 = 响应缓冲（跨 call 保存） */

    if (r3 == WRITE_LEN) goto hook_write
    if (r3 == EQ_WRITE_LEN) goto hook_write_eq
    if (r3 == READ_LEN) goto hook_read
    goto hook_silent

hook_write:
    r5 = b[r2+0] (u)
    if (r5 != KEY_ID) goto hook_silent
    /* set_config(30, data+1, 8) —— 源缓冲是命令帧体内存，既有 setter 同款同步读 */
    r6 = SET_CONFIG
    r0 = KEY_ID
    r1 = r2 + 1
    r2 = VALUE_LEN
    call r6
    /* 回执 1 字节状态：resp[2] = (r0==0 ? 0 : 1) */
    r5 = 0
    if (r0 == 0) goto hook_w_ok
    r5 = 1
hook_w_ok:
    b[r4+2] = r5                     /* 字节直写 resp[2]（分发器覆写 resp[0..1]；路 L3
                                        勘误：旧版字存储 [r4+0] 把 status 落在 resp[0]，
                                        resp[2] 恒 0——失败也回 30 01 00 假成功，codex 复核） */
    r0 = 1
    goto hook_ret

hook_write_eq:
    r5 = b[r2+0] (u)
    if (r5 != EQ_KEY_ID) goto hook_silent
    /* set_config(31, data+1, 88) —— 与 key 30 写同款，仅 key/长度不同 */
    r6 = SET_CONFIG
    r0 = EQ_KEY_ID
    r1 = r2 + 1
    r2 = EQ_VALUE_LEN
    call r6
    r5 = 0
    if (r0 == 0) goto hook_we_ok
    r5 = 1
hook_we_ok:
    b[r4+2] = r5                     /* 回执同 key 30：`30 01 status`（cmdid 0x30 是
                                        十六进制命令号，key 31 线上是 data[0]=0x1F） */
    r0 = 1
    goto hook_ret

hook_read:
    r5 = b[r2+0] (u)
    if (r5 == KEY_ID) goto hook_read30
    if (r5 == EQ_KEY_ID) goto hook_read31
    goto hook_silent
hook_read30:
    /* get_config(30, resp+2, 8) —— 8 字节直接落进回执数据位 */
    r6 = GET_CONFIG
    r0 = KEY_ID
    r1 = r4 + 2
    r2 = VALUE_LEN
    call r6
    if (r0 != VALUE_LEN) goto hook_silent   /* 无记录/长度不符：静默不回 */
    r0 = VALUE_LEN
    goto hook_ret
hook_read31:
    /* get_config(31, resp+2, 88) —— 回执 `30 58 v0..v87`，90B < 128B 缓冲 */
    r6 = GET_CONFIG
    r0 = EQ_KEY_ID
    r1 = r4 + 2
    r2 = EQ_VALUE_LEN
    call r6
    if (r0 != EQ_VALUE_LEN) goto hook_silent
    r0 = EQ_VALUE_LEN
    goto hook_ret

hook_silent:
    r0 = 0
hook_ret:
    {pc, r6-r4} = [sp++]
