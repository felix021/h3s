/* mic_effect 双击 toggle + MPO 参数消费桩 — 路 HAL3 / sim-hal3, 2026-11-17
 *
 * 本文件是 build_quiet_candidate.py --hearing-aid-l3-mpo 的组合输入片段，
 * 与路 L1KEY 的 mic_effect_toggle_hook.S 同源，仅 open 分支多两条指令：
 *
 *     call mic_effect_open
 *     if (r0 != 0) goto done       ← 新增：open 失败（r0=-22）不应用参数
 *     call apply_mpo               ← 新增：读 key 30 → MPO 曲线 → wdrc 热更
 *     goto done
 *
 * 消费语义（docs/hearing-aid-l3-mpo.md，codex 方案 a2）：App 经 BLE cmdid 0x30
 * 写 VM key 30（版本化 8B：魔数 "HA"+版本+float MPO），本桩在 open 成功后调
 * apply_mpo 把最新 M 应用于 Drc1Eff ——「已保存，下次开启辅听时应用」。
 * apply_mpo 返回值（0 已提交/1 跳过/负框架错误）不改 handler 结果：done 统一
 * 置 r4=1（已处理），参数是否应用由 key 30 的读写回执与后续验证链独立判断。
 *
 * 除上述两点外与 mic_effect_toggle_hook.S 逐注释/指令一致（含 ANC 互斥
 * db3c(1,1) 前置、HANDLER_RET 返回帧复用）；该文件的完整依据注释不重复，
 * 见 mic_effect_toggle_hook.S 与 docs/hearing-aid-l1-key7.md。
 */

    /* —— 固件锚点（与 mic_effect_toggle_hook.S 相同） —— */
    .equ MIC_EFX_SLOT, 0x00125574  /* RAM 槽（路 CT，构建器统一注入实际槽值） */
    .equ ANC_DISPATCH,  0x0600db3c  /* 内部模式 dispatch：r0=1(OFF)/2(ON)/3(TRANS)，r1=flag */
    .equ HANDLER_RET,   0x0602530c  /* handler 返回已处理：r0=r4; pop.pc_rl l=9 */

    .global cmd7_toggle_mpo
cmd7_toggle_mpo:
    /* 读槽：0 = 未激活 → open；非 0 = 已激活 → close */
    r0 = MIC_EFX_SLOT
    r4 = [r0+0]
    if (r4 != 0) goto do_close

    /* open：先请求关 ANC（互斥），再开辅听 */
    r0 = 1                  /* ANC 内部模式 OFF */
    r1 = 1                  /* flag=1（键循环邻域三入口 0x06018c8e/ca4/cba 同款） */
    r6 = ANC_DISPATCH
    call r6                 /* db3c(1,1)：写目标+过渡标志+提示音+续行（返回≠硬件已退） */
    call mic_effect_open    /* 开辅听（自带 {rets,r6-r4} 保存恢复） */
    if (r0 != 0) goto done  /* open 失败：节点未起，参数消息无消费者，不应用 */
    call apply_mpo          /* 应用最新 MPO（自带保存恢复；返回值见头部注释） */
    goto done

do_close:
    call mic_effect_close   /* 幂等关辅听（自带保存恢复） */

done:
    r4 = 1                  /* handler 返回值 = 已处理 */
    r6 = HANDLER_RET
    goto r6                 /* 复用 handler 返回帧 */
