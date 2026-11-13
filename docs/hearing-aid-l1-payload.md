# hearing-aid L1 激活载荷（路 L / sim-l1impl，R2 实施）

2026-11-13。本页是路 I（hearing-aid-l1-wiring.md）结论的落地实施：**用路 G 汇编器薄封装新写
mic_effect(DHA/辅听) 激活载荷，并做离线验证**。路 I 钉死了 hearing_aid 激活层整段被链接器裁掉，
L1 的最小改动 = 「新写激活载荷 + 在 ANC 通透档插接驳」。本页交付：①阶段 A 激活序列反汇编证据
（只读）；②载荷汇编源 `tools/hearing-aid/mic_effect_open.S`；③接驳方案（文档级）；④可复跑离线
验证 `tools/test_hearing_aid_payload.py`（已接 `check_local.py --units`）。

**基线** aadd609。反汇编用钉死 codec（pi32v2-isa @3608ed1，worktree reference-cache）+ 官方 UFW
公开字节（app.bin sha `18f3d573…`，加载基址 0x06000100）；stream.bin 用官方基线提取
（sha `6c1f1950…`，与 b79 资源区逐字节相同）；SDK 参考 = 路 CJ 拉取的 pin 67328f4 源码 +
h3s-tools-research 的 jlstream.h。一次性脚本在本地 tmp（~/tmp/hearaid-l），不进库；私有 ROM
路径/字节不进本文。**不刷机、不连真机；EQ 修复候选 67ba97ef UNTESTED 不变。**

证据强度：**[静态]** = 指令锚定反汇编/字节扫描（本路）；**[镜像]** = stream.bin/公开字节直接
解码；**[SDK]** = 参考 SDK 源（非精确 QCY 源）；**[文档]** = 既有 docs/HANDOFF 钉死条目；
**[推断]** = 上述链推出。

## 0. 一句话结论

激活载荷已写好并离线验证：**41 条指令 / 134 字节代码 + 4 字节数据槽，零重定位自包含**，只调
固件里现成的 7 个 jlstream API 锚点，调用约定由 tone/pc_mic 播放器等 5 组真实激活序列反汇编
钉死；stream.bin 里 mic_effect(0x9c2d) 描述符存在（激活的硬前提成立）。**路 O 已把载荷放进
函数级仿真真实执行（§8）：get_pipeline_uuid 真实返回 0x9c2d、pipeline_parse 建出 11 个真实
节点、add_thread/set_scene 在真实部分流上直调全成、start 入场后停在合成状态边界。路 T 已把
载荷+接驳做进离线候选镜像（§9：追加段+双窗口替换+fence 扩界+全 CRC 链，UNTESTED 不刷机）。
仍未在真机跑通激活链，不构成「助听器可用」声明**（见 §6/§8/§9.4 诚实边界）。

## 1. 阶段 A：激活序列模板反汇编（只读）

### 1.1 固件内 5 组现成激活序列 [静态]

全 app 线性解码 + call 目标反查，锚点 API 的调用点全集：

| 播放器函数 | 序列 | pipeline 名 | 节点 uuid | add_thread id |
|---|---|---|---|---|
| tone 播放器 0x0600d9be | 0x0600d9ca..0x0600da4e | "tone" @0x060a1990 | 0x768A | 0x1A85 |
| esco/linein 族 0x06016e84 起 | 0x06016e84..0x06016f0c | （r6 动态） | 0x8458 | 0xA576, 0x1100, 0x3BC9 |
| 0x06017078 起 | 0x06017078..0x060170ce | （r6 动态） | 0xCADC | … |
| pc_spk 族 0x06033504 起 | 0x06033504..0x06033568 | （r6 动态） | … | … |
| **pc_mic 播放器 0x060339a4** | 0x060339ca..0x06033a54 | "pc_mic" @0x060a1b01 | **0xD06D** | **0x1100, 0x48** |

**pc_mic 是 mic_effect 的最佳模板**：同为 mic-ADC→输出 pipeline、激活参数全是静态立即数、
且它的 pipeline_parse 用 NODE_UUID_ADC(0xD06D)——正是 SDK mic_effect.c 的 fallback 路径
（`jlstream_pipeline_parse(uuid, NODE_UUID_ADC)`，"IIS0_RX1" 串不在 QCY 固件，路 I §1.2）。

pc_mic 完整序列 [静态]：

```
060339a4  fn()                               ; QCY[600] 已有 player → 返回 -16(-EBUSY)
060339c2  r1 = 0x60a1b01 ("pc_mic"); r0 = 15
060339ca  call 0x060057e6                    ; uuid = get_pipeline_uuid(15, "pc_mic")
060339ce  r5 = r0.l (u); if uuid==0 → ret    ; 低 16 位 uuid，0 = 失败
060339d2  r0 = 8; call 0x0012e16e            ; player = ROM malloc(8)
060339de  r1 = 53357 (0xD06D)
060339e6  call 0x06074938                    ; stream = pipeline_parse(uuid, NODE_UUID_ADC)
060339ec  [r4+0] = r0; if stream==0 → ret
060339f0  r2 = 0x2000D; r1 = 0x1100; r3 = 0x100
060339fe  call 0x06074ec6                    ; add_thread(stream, 根 0x1100, 0x2000D, 0x100)
                                              ; 返回非 0 → release + 错误（根线程必须成功）
06033a08  r2 = 0x20003; r3 = 96000; r1 = 0x48
06033a18  call 0x06074ec6                    ; add_thread(stream, sub 0x48, …)
                                              ; 0 → 继续；-2 → 容忍继续；其他 → release
06033a26  r2 = 0x6033a78; r1 = r0(stream 自身)
06033a30  call 0x06074a5e                    ; set_scene(stream, stream, 场景恢复回调)
06033a36  r1 = 10
06033a3a  call 0x06074e64                    ; set_notify(stream, 10)
06033a42  call 0x06074ffe                    ; err = start(stream)
06033a4c  err != 0 → call 0x06074204         ; jlstream_release（失败回收）
```

### 1.2 API 调用约定（逐条从反汇编钉死）[静态]

| API | 入口 | 调用约定（r0-r3 传参，返回 r0） | 内部行为锚点 |
|---|---|---|---|
| get_pipeline_uuid | 0x060057e6 | r0=15(STREAM_EVENT_GET_PIPELINE_UUID), r1=pipeline 名串 → r0=uuid | "mic_effect" 分支在 0x06005982 clock_alloc(24MHz)+0x9c2d [文档:路 I] |
| jlstream_pipeline_parse | 0x06074938 | r0=pipeline uuid, r1=节点 uuid → r0=stream（0=失败） | 先调 0x060743f6 查 flash/res/stream.bin 描述符表；自建根 pipeline 对象（malloc 48B，id=**0x1100**） |
| jlstream_add_thread | 0x06074ec6 | r0=stream, r1=线程/pipeline id, r2=flags, r3=extra → r0=err | 薄壳：r0=[stream+40]（根 pipeline）后调 0x06074e7e（按 id 匹配 pipeline 树；0x1100=根，特殊分支 @0x06074e92） |
| jlstream_set_scene | 0x06074a5e | r0=stream, r1=scene 值, r2=场景恢复回调（0=无） | stream[80]=r1, stream[88]=r2, ROM 0x12b1a2 → stream[84] |
| set_notify | 0x06074e64 | r0=stream, r1=val | stream[28]=r1（字节）+ 调 0x06074d5e(stream, 0x20006) 事件派发 |
| jlstream_start | 0x06074ffe | r0=stream → r0=err | — |
| jlstream_release | 0x06074204 | r0=stream | 失败路径回收 |

两个与 SDK 参考头的差异点（QCY 构建的 jlstream 与参考 SDK 不同版本）[静态+SDK]：

- **add_thread 第二参数是数字 id 不是字符串**（SDK 头声明 `jlstream_add_thread(stream, const
  char *thread_name)`；固件里 5 组调用点全部传 16 位数字：0x1100/0x48/0xA576/0x3BC9/0x1A85）。
  0x06074e7e 按 id 匹配 pipeline 树；**0x1100 是 pipeline_parse 自建根对象的 id**（0x06074992
  `h[r4+2] = 0x1100`），所以 add_thread(stream, 0x1100, …) 对任何 pipeline 通用。
- **set_scene 是三参数**（SDK 头两参数）：r2 是「场景恢复回调」函数指针——pc_mic 传 0x06033a78
  （被抢占后关旧 player、重新调 0x060339a4 自拉起），tone 传 0x06033794，且 r1 语义灵活
  （tone 传字节、pc_mic 传 stream 自身）。

### 1.3 硬前提：stream.bin 有 mic_effect 描述符 [镜像]

pipeline_parse 第一步 0x060743f6 按流水线 uuid 遍历 **flash/res/stream.bin**（0x06074234 lazy
单例，资源名串 @0x0609d3c4 = "flash/res/stream.bin"）的 4 字节节头条目流；查不到则 pipeline_parse
返回 0，载荷必然失败。官方基线 stream.bin（9351B，sha `6c1f1950…`）扫描结果：

- **0x9c2d 描述符条目在 offset 0x2d0**：`2d 9c d0 00 | 6d d0 03 00 | c4 b7 01 00 | c4 b7 01 01`
  ——uuid 0x9c2d + **NODE_UUID_ADC(0xD06D) 计数 3** + 0xB7C4 节点 ×2（0xB7C4 是 DacEff/输出侧
  效果节点类 uuid [推断]）。ADC 源节点在描述符里存在 = pipeline_parse(0x9c2d, 0xD06D) 有东西可建。
- **coexist 表有条目**：offset 0x1dea `9d d7 2d 9c 01 00`（0xd79d↔0x9c2d 互斥对），0x2223 处
  `2d 9c 01 00 …85 1a`（0x9c2d 条目带 tone id 0x1A85 相邻数据）。mic_effect 在场景仲裁层是
  注册过的 [镜像]。
- 佐证：init 期 0x0602ea68 用 0x060755b2 成功读 0x9c2d 的场景配置字节进 [0x126584]
  [文档:路 I §2.2]——同一张 uuid 表能查到。

### 1.4 接驳点：模式 dispatch 0x0600db3c 的 TRANS 分支 [静态]

函数 0x0600db3c（r0=内部模式 1/2/3=OFF/ON/TRANS，r1=同步标志）有两条链，各有一个 TRANS 分支：

| 链 | 分支指令 | TRANS 落点 | 落点内容 |
|---|---|---|---|
| 链 1（r1≠0 路径） | 0x0600db92 `if (r5==3) goto 232` | **0x0600dc7e** | `call 0x0600c2aa`(取 ANC ctx); `r0=[r0+116]`(TRANS apply fn) → 汇合 0x0600dc86 `r1=r4; call 0x0600dae4(fn,r4)` → 尾声 0x0600dc8c |
| 链 2（内部状态 b[r2+0]==2 路径） | 0x0600dbbc `if (r5==3) goto 228` | **0x0600dca4** | `call 0x0600c2aa`; `r0=[r0+116]` → `goto 0x0600dccc` 汇合 `r1=r4; call 0x0600dae4` → 尾声 0x0600dcd2 |

- ctx getter 0x0600c2aa：读 QCY[348] 判断后返回 ctx 基（0x060a379c 或 +224）[静态]。
- apply 0x0600dae4(fn, flag)：flag≠0 走 0x0600dab6 异步包装，flag==0 直接调 fn [静态]。
- **模式应用函数槽**：[ctx+112]=ON、[ctx+116]=TRANS、[ctx+120]=OFF [静态]。
- **「先关 ANC 通透」的现成调用面** = 复用 OFF 槽：`call 0x0600c2aa; r0=[r0+120]; r1=r4(flag);
  call 0x0600dae4`——与 OFF 模式路径（0x0600dc74..0x0600dc88）逐指令同形 [静态]。
- App 命令 0x17 直接设置路径 0x0600dcee 是另一套函数（独立 `if r0==3` 分支），接驳它才能让
  App 侧通透与按键语义一致；本路只文档级标注，不实施 [文档:路 I §1.3]。
- DAC_NOISEGATE_OFF/ON：宏体在 media 层 audio_dac.h（不在本地 SDK 缓存），固件内无 "noise"
  字符串、对应函数随激活层一起被裁，**固件锚点未钉死**——载荷暂不显式操作 noisgate，理由与
  风险见 §6。

## 2. 载荷：`tools/hearing-aid/mic_effect_open.S`

**41 条指令 / 134B 代码 + 4B 数据槽（共 138B），零重定位**（`.equ` 常量折叠，完全自包含）。
结构 = pc_mic 激活序列的 mic_effect 参数化 + 重入保护 + 错误回收：

```
mic_effect_open():
    [--sp] = {rets, r6-r4}                    ; callee-saved 保存（tone/pc_mic 同款序言）
    r0 = MIC_EFX_SLOT; r5 = [r0+0]
    if (r5 != 0) return 0                     ; 重入保护：已激活
    uuid = get_pipeline_uuid(15, "mic_effect")     ; @0x060057e6，名串 @0x060a1de2
    if (!uuid) return -22
    stream = pipeline_parse(uuid, 0xD06D)          ; @0x06074938，ADC 节点
    if (!stream) return -22
    err = add_thread(stream, 0x1100, 0x2000D, 0x100)   ; @0x06074ec6，根线程（pc_mic 形状）
    if (err) { release(stream); return -22 }        ; @0x06074204
    set_scene(stream, 16, 0)                        ; @0x06074a5e，HEARING_AID，无恢复回调
    set_notify(stream, 10)                          ; @0x06074e64，pc_mic 同参数
    err = start(stream)                             ; @0x06074ffe
    if (err) { release(stream); return -22 }
    [MIC_EFX_SLOT] = stream                    ; 数据槽记录（重入保护 + 供 close 路径用）
    return 0
```

参数决策与依据：

| 参数 | 值 | 依据 | 强度 |
|---|---|---|---|
| 事件 id 15 | STREAM_EVENT_GET_PIPELINE_UUID | SDK 枚举第 15 项 + 固件 5 组调用点全部 r0=15 | 静态+SDK |
| "mic_effect" 串 | 0x060a1de2 | 固件字符串表实测读出 | 镜像 |
| 节点 uuid | 0xD06D(NODE_UUID_ADC) | SDK fallback 路径 + stream.bin 描述符 ADC 节点存在 + pc_mic 同节点 | 静态+镜像+SDK |
| add_thread id/flags/extra | 0x1100 / 0x2000D / 0x100 | 0x1100=pipeline_parse 自建根（通用）；flags/extra 抄 pc_mic 根线程 | 静态 |
| set_scene r1 | 16 | SDK 枚举 STREAM_SCENE_HEARING_AID=16；**QCY 构建的枚举值未证**（见 §6） | SDK+推断 |
| set_scene r2 | 0（无恢复回调） | 被抢占后不自动重拉起；保守最小值 | 推断 |
| set_notify val | 10 | pc_mic 同参数；**语义未钉死**（写 stream[28]+派发 0x20006） | 静态+推断 |
| 单 add_thread（不加第二个） | — | SDK 第二线程 mic_effect2 受 CONFIG_JLSTREAM_MULTI_THREAD_ENABLE 门控；pc_mic 的第二个 add_thread(-2) 是容忍路径非必需 | SDK+静态 |

寄存器约定：r4-r6 是 callee-saved（固件函数序言 `[--sp]={rets,r6-r4}` 实证），载荷跨 call 用
r4(stream)/r5(临时)/r6(API 地址) 保存状态安全；r0-r3 只做传参，与全部 5 组模板一致 [静态]。

## 3. 接驳方案（文档级，不实施）

**语义**：通透档 = 「先关 ANC 通透、再开辅听」的**替换**（路 I §3.2：ANC 与 mic_effect 抢同一
mic-ADC→DAC 通路，不可叠加）。两个 TRANS 落点各替换一个 8B 窗口（4B call + 4B load，恰好两条
完整指令，无半指令切割）为 `goto.l 载荷`（6B）+ 2B pad：

| 窗口 | 原字节范围 | 原语义（重放进载荷） | 接驳后 |
|---|---|---|---|
| A | 0x0600dc7e..0x0600dc86 | `call 0x0600c2aa; r0=[r0+116]`（TRANS apply 取函数） | goto.l trans_branch_1；载荷做 OFF apply + mic_effect_open() 后 goto 0x0600dc8c（原函数尾声，跳过原 TRANS apply） |
| B | 0x0600dca4..0x0600dcac | `call 0x0600c2aa; r0=[r0+116]` | goto.l trans_branch_2；同上，goto 0x0600dcd2 尾声 |

接驳载荷入口（示意，实施时并入载荷源）：

```
trans_branch_N:            ; 进入时 r4=apply flag（上游 0x0600c2a0 结果）、r7=QCY 基址（窗口 A 链）
    call 0x0600c2aa        ; 重放：取 ANC ctx（原窗口第一条）
    r0 = [r0+120]          ; 改取 OFF apply fn（原窗口是 [r0+116] TRANS）
    r1 = r4                ; apply flag（原汇合点语义）
    call 0x0600dae4        ; 应用 OFF —— 先关 ANC 通透
    call mic_effect_open   ; 再开辅听（本路载荷）
    goto <对应尾声>         ; A→0x0600dc8c / B→0x0600dcd2
```

配套（实施时必做，本路不实施）：

1. **fence 约束（复用 EQ 载荷追加模式）**：载荷按追加 JLFS 文件放入 app 明文区尾部（EQ 先例
   0x60e3074；示例基址取 0x060e3200）。执行取指受 LIM_PC0 上限约束：原上限 0x060c3338 会拒绝
   载荷取指，须像 EQ 修复候选一样把 0x6034e98 的 32 位立即数改到 ≥ 载荷末指令末字节（0x060e3200
   + 0x8A + 接驳入口段长度）；下限 0x06000100 不受影响。数据槽 4B 的**读/写不受 PC fence 约束**
   （RuntimePCguard 只查执行取指 [文档:AGENTS]）。
2. **镜像完整性链**：追加文件要更新子文件 JLFS CRC + app_area_head CRC + boot-info 三层 CRC +
   外层 UFW CRC（AGENTS 技术不变量 3；EQ 修复候选同链）。两个接驳窗口在原代码区（< 0x060c3338），
   替换后 app 明文区 CRC 相应更新。
3. **关闭路径**：通透档再次离开（OFF/ON）时应先关辅听（jlstream_stop+release+清数据槽）再进新
   模式；本路载荷只有 open，close 载荷是下一步工作。
4. **App 0x17 路径**（0x0600dcee）同步接驳，否则 App 通透与按键语义分叉。
5. **noisgate 配对**：见 §6 第 3 条——拿到 audio_dac.h 宏体或反汇编 DAC 配置函数后补
   DAC_NOISEGATE_OFF/ON 配对调用。

## 4. 离线验证（可复跑）

`tools/test_hearing_aid_payload.py`（unittest，已接入 `check_local.py --units` 单测族），
6 条断言全过 [实测]：

1. **往返零 diff**：载荷 41 条指令逐条 `encode(decode(x)) == x`（路 G 闭环判据），同时断言
   指令数落在预估形状区间 [30, 60]。
2. **API 锚点核对**：7 个锚点地址各以 `r6 = imm` 装载恰好一次，无多余 API 装载。
3. **参数常量核对**：证据表全部常量（0x060a1de2/0xD06D/0x1100/0x2000D/0x100/16/10/15/-22）
   逐个在指令流中存在。
4. **调用配对**：每个 `call r6` 的前一条必是装载对应 API 地址的 li.u32——call 与目标绑定，
   不存在「装载了没用/调用了没装载」。
5. **槽参数化**：把 MIC_EFX_SLOT 从 0x060e3200 改 0x060e4000 重汇编，断言差异字节全部落在
   两个槽地址 li.u32 指令内（载荷对追加段基址不敏感，实施时只改一个 .equ）。
6. **激活顺序**：API 装载顺序 == pc_mic/tone 钉死的激活链顺序（get_uuid → parse → add_thread →
   set_scene → set_notify → start），release 只在错误路径末尾。

复跑：`python3 tools/test_hearing_aid_payload.py`（0.5s，无需 ROM/镜像输入）。

## 5. 与路 I 建议形状的差异

路 I §4.1 建议「get_pipeline_uuid → pipeline_parse → add_thread → start 最小子集，再补
set_scene/add_thread/场景值」。本路实做后有两处更新：

- **set_scene/set_notify 不是可选项**：固件 5 组激活序列全部调用二者（set_scene 还是三参数版
  带 QCY 扩展的恢复回调位），start 之前不设场景的路径在固件里没有先例，故载荷保留。
- **add_thread 传数字 id 不传字符串**：SDK 的 "mic_effect1/2" 字符串调用形在 QCY 构建里不存在，
  根线程 0x1100 是唯一无 pipeline 特异性的通用值，故单 add_thread(0x1100) 是最小可行形状。

## 6. 诚实边界（必须随载荷一起读）

1. **离线载荷，未激活验证**：未在真机跑通过激活链；函数级仿真验证（§8，路 O）已确认
   uuid/parse/build/add_thread/set_scene/set_notify/start 各入口的真实行为与停点，但音效链
   是否出声仍未验证。本页+§8 交付的是「载荷字节与静态证据一致 + 汇编器可信 + 载荷在仿真里
   可执行到 start 入场」三件事，**不构成「助听器可用」声明**。
2. **set_scene r1=16 是 SDK 枚举值**：QCY 构建的 STREAM_SCENE 枚举未经固件内证据钉死（固件调用
   点的 r1 要么动态要么传指针，没有一个静态 scene 立即数可对照）。若 QCY 枚举偏移不同，场景
   仲裁（coexist/优先级）会按错误场景处理——失败模式是仲裁行为错，不是崩溃。
3. **noisgate 未配对**：DAC_NOISEGATE_OFF/ON 的宏体与固件锚点未钉死（§1.4 末条）。推断
   mic_effect pipeline 的 DAC 侧节点（stream.bin 描述符的 0xB7C4×2）open 时自行配置 DAC，显式
   noisgate 操作可能冗余——但这是 [推断]，若不冗余，ANC 与辅听切换时 noisgate 状态可能错配
   （路 I §3.2 第 2 条风险仍在）。
4. **单线程形状**：SDK 的 mic_effect1/2 双线程被简化为单根线程。若 QCY 的 mic_effect pipeline
   在 stream.bin 里声明了双线程结构，单 add_thread(0x1100) 是否充分未验证；pc_mic 的第二个
   add_thread 返回 -2（容忍）暗示 sub 线程可以缺。
5. **接驳已做进离线候选（§9，路 T）但未运行验证**：窗口 A/B 替换、fence 扩界、CRC 链已
   实施为 `--hearing-aid-l1-patch` 离线候选镜像（UNTESTED 不刷机）；close 路径、App 0x17
   同步仍未实施；接驳后的完整路径未在仿真跑（§9.4）。EQ 修复候选 67ba97ef UNTESTED 状态
   不变。
6. **数据槽是追加段内新全局**（示例 0x060e3200）：不在 QCY 配置区，不与 [0x126584] 等既有全局
   冲突；但 close 载荷未写，重入保护只防重复 open，不处理「通透→OFF 再回通透」的辅听残留
   （数据槽非零 → 直接返回 0，旧 stream 不重建也不关——语义是「辅听粘住」，需 close 载荷收口）。

## 7. 证据锚点表（本路新增钉死）

- API：get_pipeline_uuid 0x060057e6 / pipeline_parse 0x06074938 / add_thread 0x06074ec6 /
  set_scene 0x06074a5e / set_notify(0x06074e64，写 stream[28]+派发 0x20006 @0x06074d5e) /
  start 0x06074ffe / release 0x06074204；call 目标反查全表见 §1.1 五组序列。
- pipeline_parse 内部：stream.bin 句柄单例 0x06074234（资源名 @0x0609d3c4）；描述符表遍历
  0x060743f6（4B 节点 {uuid16,len16} 流）；根 pipeline 对象 malloc(48B) 且 id=0x1100
  @0x06074992；add_thread 薄壳换根 [stream+40] 后按 id 匹配（0x1100 特殊分支 @0x06074e92）。
- stream.bin（官方基线 sha 6c1f1950…，9351B）：0x9c2d 描述符 @offset 0x2d0（含 0xD06D×3 +
  0xB7C4×2）；coexist 对 0xd79d↔0x9c2d @0x1dea；0x9c2d 条目 @0x2223。
- 接驳：模式 dispatch 0x0600db3c；TRANS 分支 0x0600db92→落点 0x0600dc7e（窗口 A 8B =
  0x0600dc7e..0x0600dc86）、0x0600dbbc→落点 0x0600dca4（窗口 B 8B = 0x0600dca4..0x0600dcac）；
  ctx getter 0x0600c2aa；apply 0x0600dae4(fn, flag)；模式槽 [ctx+112]=ON/[+116]=TRANS/[+120]=OFF；
  尾声 0x0600dc8c / 0x0600dcd2；App 0x17 路径 0x0600dcee（另一套，未接驳）。
- 激活序列模板：tone 0x0600d9be / 0x06016e84 组 / 0x06017078 组 / 0x06033504 组 /
  **pc_mic 0x060339a4**（载荷直接模板）+ 其场景恢复回调 0x06033a78。
- 载荷：tools/hearing-aid/mic_effect_open.S（41 insns / 134B + 4B slot，0 relocs）；
  验证 tools/test_hearing_aid_payload.py（6 tests OK）。

## 8. 仿真执行验证（路 O / sim-l1sim，2026-11-13）

probe_pi32v2 `--tone-scene` 新场景 **`mic_effect_payload`**（opt-in，不开时 legacy 报告逐字节
不变）把本页载荷放进函数级仿真 harness 真实执行。载荷在场景运行时从
`tools/hearing-aid/mic_effect_open.S` 现场汇编（`MIC_EFX_SLOT` .equ 按 §2 改为 基址+134 =
追加段尾 4B；离线单测已证载荷对其余字节基址无关——「源码→字节→执行」同源），映射到追加段
0x060e3200（EQ 候选载荷末端 0x060e31c5 之后，无重叠）。执行 fence 按 §3 配套 1 扩到载荷末端
（**负例断言钉死**：不扩界时载荷取指被 stock fence 0x060c3338 拒绝）。单任务桩/allocator/
stream-slot 哨兵复用 d2c 场景机制；stream.bin 以 CD 实测运行时基址喂公开字节；新增具名
synthetic 仅三件——fence 扩界、4B 槽可写、0x124880 时钟链表哨兵（空自环 boot 态，
0x1264b8/0x1264c8 同 d2c 先例）。

激活链逐环节结论（b79↔official 双镜像全等断言过）：

| 环节 | 结论 | 强度 |
|---|---|---|
| 载荷取指 | fence 扩界后 41 条指令从入口真实执行，进入 pipeline_parse 内部停点；错误路径 release 全程未触发 | 实测（仿真） |
| get_pipeline_uuid(15,"mic_effect") | **真实返回 0x9c2d**（载荷调用点回填处观测 r0=0x9c2d）；内部 clock_alloc("mic_effect",24MHz) 真实跑通（zalloc44+互斥往返+2 次 OS post，RTOS 面桩吸收） | 实测（仿真） |
| pipeline_parse(0x9c2d, 0xD06D) | 真实执行：§0 扫描在公开 stream.bin **offset 0x2d0** 命中 0x9c2d 记录头、读入 208B 边表（首边 0xd06d/3→0xb7c4/1 与 §1.3 镜像一致），**建出 11 个真实节点**（0xb7c4/1→0x74e3/21→**0x737b/10=Eq1Eff**→0xdefe/6(wdrc)→0x6195/1→0xc482/1→0x7293/1→0xd911/2→0x5101/1→0xbf8e/1→0x1b9d/2）；第 12 边（0x1b9d/2→0x737b/9=Eq5Eff）的节点复用搜索在 0x60746cc 撞非指针读**诚实停** | 实测（仿真） |
| add_thread(stream, 0x1100, 0x2000D, 0x100) | parse 已建的真实部分 0x9c2d 流上直调（BW/CM/d2c 直调先例，载荷同参）：**返回 0（成功）**，callee-saved/栈不动 | 实测（仿真） |
| set_scene(stream, 16, 0) | 同上直调：**返回 0**；stream[80]=**16**（场景值的可见消费点=存储）、stream[88]=**0**（无恢复回调，§2 参数决策被真实执行）、RAM-code 助手 0x12b1a2 真实执行回填 stream[84]；16 在停点前无更下游消费者可观察 | 实测（仿真） |
| set_notify(stream, 10) | 同上直调：b[stream+28]=**10** 真实写入后，0x20006 事件派发停在 0x6074a96（见下） | 实测（仿真） |
| start(stream) | 入口真实执行、0x20004 派发开始，停在 0x6074a96（与 set_notify 同一停点） | 实测（仿真） |

**停点性质**（两条停点同因）：合成单任务/bump-allocator 状态边界，**不是固件或载荷的判决**——
同一节点复用搜索在 d2c 音乐场景（`d2c_effect_pipeline`）真实执行 231 次调用不崩；0x6074a96 是
事件派发对同一棵合成态节点树做适配器分发。0x9c2d 图是 26 边深线性链（比音乐图深），合成堆布局
让节点的 +44/+48 链读到非指针（uuid/浮点参数当指针）。桩面之外零发明。

诚实边界：这仍是**函数级仿真**——未建真机 boot 状态、RTOS/队列/调度是具名桩、音效链未出声；
4B 数据槽在 harness 里是可写内存，真实植入还需决定可写落点（flash 编程 vs 保留 RAM）；
**不构成「助听器可用」声明；EQ 候选 67ba97ef UNTESTED 不变**。

复跑：`python3 tools/probe_pi32v2.py --tone-scene --rom <公开 ROM 资产> --official-ufw <官方 UFW>`。

## 9. 镜像实施：离线候选 `--hearing-aid-l1-patch`（路 T / sim-l1image，R2 固件侧）

2026-11-13。本节把 §1-6 的载荷 + §3 的接驳方案做进**离线候选镜像**：
`tools/build_quiet_candidate.py` 新增 opt-in 开关 `--hearing-aid-l1-patch`（默认关，
legacy 行为逐字节不变——`--attenuation-db 10 --anc-cycle-two-state` 仍复现已测镜像
sha `b79cb3a6…`）；实施代码在 `tools/hearing_aid_l1_image.py`（构建器专用模块，
独立校验器不导入），接驳桩源码 `tools/hearing-aid/mic_effect_hook.S`。§2 的
`mic_effect_open.S` **一字未改**（其单测原样通过）。**不刷机、不连真机；候选
UNTESTED；EQ 修复候选 67ba97ef 状态不变**（两开关互斥——同一追加点）。

### 9.1 追加段（「源码→字节→镜像」同源）

构建时用 `tools/pi32v2_asm.py` 现场汇编「载荷 + 接驳桩」单一代码段（41+2×9=59
条指令 / 198B，零重定位），两遍汇编定槽位：第一遍量长度，第二遍把
`MIC_EFX_SLOT` .equ 改为 实际段尾 4B（0x060E313A）——离线单测（§4 第 5 条）+ 构建
时断言都证明除两个槽位 li.u32 立即数外字节不变。追加几何照抄 EQ 载荷先例
（apply_eq_patch）：新 JLFS 文件头 @flash 0xE5073、0xff 对齐标记、代码起
0x060E3074（4 对齐）、槽在段尾；与 §8 仿真示例基址 0x060e3200 不同是**因为本候选
独立于 EQ 载荷**（第一个空闲追加点即 EQ 先例点；§2/§8 的示例值本来就注明随布局
变化，载荷对基址不敏感）。

| 项 | 值 |
|---|---|
| 追加文件 | `mic_effect.bin` 235B（头 32 + 0xff + 代码 198 + 槽 4×00），flash [0xE5073, 0xE515E) |
| mic_effect_open | 0x060E3074，134B（=§2 载荷逐字节，构建时断言前缀==单汇编结果） |
| trans_branch_1（窗口 A 落点） | 0x060E30FA（32B） |
| trans_branch_2（窗口 B 落点） | 0x060E311A（32B） |
| 数据槽 | 0x060E313A，初始 00 00 00 00（读 0=未激活，重入保护放行；见 9.4 边界 3） |
| 代码段 sha256 | b0feed11b7d3c896423faf876b5300232e5277f2ad9e7b3b6b78e43f84ea4a7d |

桩指令形状（与 §3 示意一致，两点差异见 9.2）：`r6=0x0600c2aa; call r6`（重放原窗口
取 ANC ctx）→ `r0=[r0+120]`（改取 OFF 槽）→ `r1=r4`（apply flag）→
`r6=0x0600dae4; call r6`（先关 ANC 通透）→ `call mic_effect_open` →
`r6=<对应尾声>; goto r6`。进入状态 r4=flag、r7=QCY 基址；桩与载荷都只写 r0-r3/r6，
两条尾声（0x0600dc8c / 0x0600dcd2）都从 [r7+500] 重载、函数出口 `pop {pc,r8-r4}`
整体恢复，r6 死值无害（已反汇编核对）。

### 9.2 接驳窗口与 fence

两处 8B 窗口整窗替换（§3 方案的两点实施差异，均有明确理由）：

| 窗口 | 原字节 | 新字节 | 语义 |
|---|---|---|---|
| A @0x0600dc7e | `bfea14f3 d0ec0407`（call 0x0600c2aa; r0=[r0+116]） | `c6fffa300e06 d600`（r6=0x060E30FA; goto r6） | TRANS 落点改道桩 1 → 尾声 0x0600dc8c |
| B @0x0600dca4 | `bfea01f3 d0ec0407` | `c6ff1a310e06 d600`（r6=0x060E311A; goto r6） | 同上 → 尾声 0x0600dcd2 |

- **跳转编码差异**：§3 写的「goto.l（6B）+2B pad」在钉死 ISA 表里不存在 6 字节
  无条件 goto（goto.l 是 4B，6B 的 gotoss.xl 语义未钉死不能用）；实作
  `li.u32 r6=<桩地址> + goto r6`（6B+2B）恰好整窗填满、零死字节、且与载荷本体
  `r6=API; call r6` 同一位置无关惯用法。
- **安全性前提（本路新钉）**：全 app 线性反汇编收集 51,709 个分支落点，两窗口
  **内部**（起点后任意字节）零落点；落在窗口起点的各只有一条=§1.4 钉死的 TRANS
  分支（0x0600db92→A、0x0600dbbc→B）；两窗口后的原汇合点（0x0600dc86/0x0600dccc）
  是 OFF/ON 路径仍在用的活代码，不在窗口内、不受影响。

**fence 扩界**（硬前提，§8 负例钉死 stock fence 0x060c3338 拒载荷取指）：
0x06034e98 的 LIM_PC0 上限 32 位立即数 0x060C3338 → **0x060E313E**（=追加文件
末端运行地址，代码末指令 0x060E3138 之后含 4B 槽裕量；EQ 修复候选同款「执行
许可以镜像为限」语义，下限 0x06000100 与使能位不动）。字节差异仅 3B
（`38 33 0c`→`3e 31 0e`，高位字节 06 不变）。

### 9.3 CRC 链与字节级验证（E 路先例）

CRC 全链照抄既有链路：config.dat 末文件标志清零+其头 CRC → 新文件头/数据 CRC →
app.bin 双层 JLFS CRC（窗口在其数据区）→ app_area_head 双层 CRC → boot-info
app_size 0xE3053→0xE313E + 明文/密文/头三层 CRC（32B 区独立于 SFC）→ 追加区整段
SFC 重加密（0x3496）→ UFW 条目 CRC + 外层头 CRC。

规范构建（`--attenuation-db 0 --hearing-aid-l1-patch`，除 L1 外零改动）产物
`H3S_0.1.1.8_tones_minus0dB_HEARING_AID_L1_OFFLINE_UNTESTED.ufw`，sha256
`6c7247a5833213ffee847a2e7a009a868d55242d1f8492076ab5a6bba056d7eb`。与官方镜像
**全文件逐字节差异恰为 279 字节**，构成：

| 区域 | 字节数 |
|---|---|
| UFW 外层（头 CRC 2 + 条目表 CRC 2 + flash 条目 CRC 2） | 6 |
| boot-info 32B 编码区（头/明文/密文 CRC 6 + app_size 2） | 8 |
| app_area_head + app.bin 各双层 JLFS CRC | 8 |
| config.dat 末文件标志 1 + 头 CRC 2 | 3 |
| 接驳窗口 2×8 | 16 |
| fence 立即数 | 3 |
| 追加文件 mic_effect.bin 全部 235（密文对 0xff 填充） | 235 |

验证清单（全部通过 [实测]）：①59 条指令逐条 encode(decode)==原字节零 diff，桩
指令形状与全部固件目标（0x0600c2aa/0x0600dae4/两尾声/载荷入口/槽地址）从最终
镜像字节反解码核对；②组合段前 134B==§2 载荷单独汇编结果；③SFC 应用区密文从
明文独立重算一致；④boot-info 五字段独立重算一致；⑤全文件差异集合==上表并集
（多一字节少一字节都抛错；组合 attenuation/ANC/demo 等开关时同样成立，其字节
并入同一断言）；⑥独立校验器 `verify_ufw_integrity.py` 全过（含新 mic_effect.bin
条目 JLFS CRC 与双备份 CRC）；⑦legacy 复现 sha 不变 + `check_local.py --units`
exit 0。复跑：`python3 tools/build_quiet_candidate.py --attenuation-db 0
--hearing-aid-l1-patch --output-dir <空目录>`（构建器要求输出目录不存在）。

### 9.4 诚实边界（必须随候选一起读）

1. **离线候选 UNTESTED，不刷机**。本节交付的是「字节与 §1-6/§8 证据一致 + 四层
   CRC/JLFS 结构自洽 + 逐字节差异精确可控」三件事，不是任何形式的运行验证。
2. **接驳后的完整路径未在仿真跑**：§8（路 O）只验证了载荷本体的激活链；接驳
   涉及的 ANC 键循环（0x0600db3c 两条 TRANS 链、ctx getter 0x0600c2aa、apply
   0x0600dae4、两条尾声）**主仿真前沿还没推进到那**，桩的跳转正确性目前只有静态
   反汇编证据（指令边界/落点/寄存器活性，9.1/9.2），没有执行证据。
3. **数据槽在 flash 里**：真机上 `[槽]=stream` 的 st.w 大概率写不进 flash 映射区
   （普通 store 不能编程 flash）——若写被静默丢弃，重入保护永不闭锁，每次进通透
   档都会重跑激活链（重复建 pipeline + clock_alloc 泄漏风险）。§8 已标注「可写
   落点待定（flash 编程 vs 保留 RAM）」，本候选选择先按 §2/§8 布局镜像内落槽，
   真机验证前需要 close 载荷 + 可写落点一并解决。
4. §6 的全部既有边界不变：set_scene 16 是 SDK 枚举值、noisgate 未配对、close 载荷
   未写（「辅听粘住」语义）、App 0x17 直设路径（0x0600dcee）未接驳（App 通透与
   按键语义会分叉）。
5. 与 EQ 修复候选共用追加点与 fence 机制但**互斥**（开关同时给会报错）；EQ 候选
   67ba97ef UNTESTED 不变，本候选不影响其状态。
