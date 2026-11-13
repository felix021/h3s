/* VM key-store App 命令 handler 载荷 — 路 AQ / sim-r4hook, 2026-11-14
 *
 * R4「App 写配置 + 固件读配置」的固件侧最小 setter：把一条 App 自定义命令接到
 * 真实的 VM get/set_config 分派器上（key 30，8 字节值），读写在同一 handler 内
 * 按 data_len 分路：
 *
 *   写：data = [30, v0..v7]（len=9）  → set_config(30, data+1, 8)
 *       回执 n=1：resp[2] = 0(成功)/1(set_config 返回非 0)
 *   读：data = [30]（len=1）         → get_config(30, resp+2, 8)
 *       回执 n=8：resp[2..9] = 存的 8 字节（读回长度 != 8 → 静默不回，n=0）
 *   其它形状：返回 0（分发器 ifs (n<1) 跳过响应，与 0x1d setter 对 len<2 的
 *   「什么都不写直接返回 0」同一行为）。
 *
 * 调用 ABI（RCSP 自定义命令分发器写路径，docs/cmd-0x17-0x1d-layout.md §1 路P 钉死；
 * 异步投递路径经 trampoline 0x060256aa 以同一四参形状调起，本路静态复核）：
 *   setter(r0=响应缓冲, r1=缓冲容量128, r2=data指针, r3=data_len) → r0=回执字节数 n
 *   分发器随后覆写 resp[0]=cmdid、resp[1]=n，只发送 resp[0..n+2)——所以本载荷把
 *   回执数据写在 resp+2 起，写路径用一次字存储 [resp+0]=status 写 status 到
 *   resp[2]（resp[0..1] 反正会被覆写，resp[3] 的残留字节落在 n+2 之外不会被发送）。
 *
 * 固件锚点：
 *   - set_config 0x06001f12 / get_config 0x06000f88：路 AB（docs/vm-key-roundtrip.md）
 *     在函数级仿真里直调过全链（写→同机读回→重启重扫仍在），ABI (key, buf, len)。
 *   - VM 后端对 key 30 无需注册（路 K：key-1<256 全命中，set 仅校验 len<=4095）。
 *   - key 30 属 1..99 的 54 个空 key（27..36 整段空），与固件既有 key 无冲突。
 *
 * 零重定位：除两个 API 锚点 li.u32 外无绝对地址，段基址无关（单测钉死）。
 * callee-saved r4-r6 序言/尾声保存（r7=注册表项指针是分发器跨 setter 调度的
 * 活寄存器，0x0600f946 在调用后仍读 b[r7+24]，本载荷不碰 r7）。
 *
 * 离线载荷：encode->decode 往返逐条零 diff；未在真机/仿真跑过命令分发链，
 * 不构成「App 可读写 key 30」的运行验证声明（候选 UNTESTED 不刷机）。
 */

    /* —— 固件 API 锚点（静态钉死，不随布局变化） —— */
    .equ GET_CONFIG,  0x06000f88  /* r0=key, r1=buf, r2=len -> r0=读回长度(负=错误) */
    .equ SET_CONFIG,  0x06001f12  /* r0=key, r1=buf, r2=len -> r0=0 成功(负=错误) */

    /* —— 命令形状常量（与 App 侧 KeyStoreFrame 约定一致） —— */
    .equ KEY_ID,      30          /* VM key 号（路 K/AB：空 key，无需注册） */
    .equ VALUE_LEN,   8           /* 值长度 8B（路 AB 读写往返实证的长度） */
    .equ WRITE_LEN,   9           /* 写形状 data_len = key(1) + value(8) */
    .equ READ_LEN,    1           /* 读形状 data_len = key(1) */

    .section .text.r4_keystore_hook,"ax",@progbits
    .global r4_keystore_hook
r4_keystore_hook:
    /* ABI：r0=resp_buf, r1=cap, r2=data, r3=len → r0=回执 n（<1 = 无响应）。 */
    [--sp] = {rets, r6-r4}
    r4 = r0                          /* r4 = 响应缓冲（跨 call 保存） */

    if (r3 == WRITE_LEN) goto hook_write
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
    [r4+0] = r5                      /* 字存储：resp[2]=状态（resp[0..1] 分发器覆写） */
    r0 = 1
    goto hook_ret

hook_read:
    r5 = b[r2+0] (u)
    if (r5 != KEY_ID) goto hook_silent
    /* get_config(30, resp+2, 8) —— 8 字节直接落进回执数据位 */
    r6 = GET_CONFIG
    r0 = KEY_ID
    r1 = r4 + 2
    r2 = VALUE_LEN
    call r6
    if (r0 != VALUE_LEN) goto hook_silent   /* 无记录/长度不符：静默不回 */
    r0 = VALUE_LEN
    goto hook_ret

hook_silent:
    r0 = 0
hook_ret:
    {pc, r6-r4} = [sp++]
