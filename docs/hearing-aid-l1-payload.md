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
路 X 已补 close 载荷+窗口设计（§10）。路 Y 已把 open+close 双向接驳做进离线候选镜像
（§11：close 载荷并入组合段 + 4 个 ON/OFF 离开窗口，`--hearing-aid-l1-close`，UNTESTED
不刷机；App 0x17 直设路径仍未接驳）。
仍未在真机跑通激活/关闭链，不构成「助听器可用」声明**（见 §6/§8/§9.4/§10.6/§11.5 诚实边界）。

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

## 10. close 载荷 + 数据槽写回设计（路 X / sim-l1close，R2 L1 收尾）

2026-11-14。本节补 §6.6/§9.4 钉下的两个收尾缺口：**close 载荷未写**（重入保护
语义=「辅听粘住」）和**数据槽在 flash 的写回未设计**（0x060E313A 槽只在 RAM 假设）。
交付：①close 链 API 反汇编钉死（关闭层**未被裁**，与激活层命运不同）；②载荷源
`tools/hearing-aid/mic_effect_close.S` + 离线验证 `tools/test_hearing_aid_close_payload.py`
（已接 `check_local.py --units`）；③数据槽写回方案（文档级，不实施）；④接驳扩展
`--hearing-aid-l1-close` 的窗口/桩设计（文档级，不实施）。

**基线** 64a7e6b。反汇编用钉死 codec + 官方 UFW 公开字节（与 §1 同源提取，
runtime = 0x06000000 + 文件偏移；本路对 §1.1 的 pc_mic 序列逐条复核对上）；
SDK 参考 = CJ 拉取的 pin 67328f4 mic_effect.c + h3s-tools-research 的 jlstream.h。
一次性脚本在本地 tmp（~/tmp/hearaid-x），不进库。**不刷机、不连真机；
候选 UNTESTED 不变；EQ 候选 67ba97ef 状态不变。**

### 10.1 close 链 API：全部在固件里 [静态]

SDK 参考序列（mic_effect.c `hearing_aid_player_close` → `mic_effect_player_delete`）：

```
jlstream_stop(player->stream, 0);            /* fade_msec = 0 */
jlstream_release(player->stream);
mic_effect_ram_code_unload();                /* TCFG_CODE_RUN_RAM 门控，可空 */
free(player);
g_mic_effect_player = NULL;
jlstream_event_notify(STREAM_EVENT_CLOSE_PLAYER, (int)"mic_effect");
```

与激活层（§1.2 穷举=整段被裁）不同，**这条链的三个框架 API 全部链接在固件里且有
多个活调用点**——mic_effect 专属的 close 壳（hearing_aid_player_close）虽随激活层
一起被裁，但它调的每个 API 都有现成锚点：

| API | 入口 | 固件内证据 | 强度 |
|---|---|---|---|
| jlstream_stop | **0x060741ee** | **7 个调用点**：tone 0x0600c1a4（fade 50）、0x06005c42/0x06006228、a2dp 族 0x06013a7c（fade 100）、esco/linein 族 0x06016c12/0x06016c58（fade 0）/0x06016cd4（fade 50）。本体=薄壳：`h[stream+46]=fade_msec` 后 post cmd4 给流服务线程（0x06074192 同步等完成） | 静态 |
| jlstream_release | 0x06074204 | 路 L §1.2 已钉（20 调用点，open 错误路径同款）；post cmd9 同步回收 | 静态+文档 |
| jlstream_event_notify | **0x060057e6** | **= 激活链 get_pipeline_uuid 的同一入口**：函数按 `r1 = r0 - 11` 建 tbh switch（13 项，覆盖事件 11..23），GET_PIPELINE_UUID(15)=idx4、**CLOSE_PLAYER(11)=idx0**。**case 11 分支存在且有 3 个固件调用点**（0x06005c66 "pc_spk"、0x06013a9e "a2dp"、0x06016c7a） | 静态 |

**三段式 close 惯用法**在固件里逐 call 可见（如 esco/linein close 0x06016c58..0x06016c7a）：

```
stop(stream, 0) → release(stream) → free(player) → g_player=NULL →
event_notify(11, "…")
```

与 SDK `mic_effect_player_delete` 逐 call 同形——载荷照抄这个形状即可。

**CLOSE_PLAYER 的 "mic_effect" 分支**（本路新钉）：case 11 落 0x06005820，是一条对
6 个 pipeline 名（a2dp@+198 / esco@+213 / linein@+500 / pc_spk@+570 / pc_mic@+577 /
**mic_effect@+1314**，字符串表基 0x060a18c0）的 strcmp 链；"mic_effect" 命中后调
**0x0600572C(name)**：锁 0x126E50 互斥 → 遍历 0x124880 时钟链表按 name（节点+8）
摘链+free 全部同名节点 → 0x060008CA（日志告警类助手，ICFG 上下文检查+带参日志，
无状态修改 [静态-浅层]）→ 解锁。即 **clock_free("mic_effect")**——与激活分支
（get_pipeline_uuid 的 clock_alloc("mic_effect",24MHz) @0x06005982）配对。tbh 表解度
交叉验证：idx4/event15 → 0x060058B4 正是 §1.1 钉死的 uuid 链（含 0x06005982
clock_alloc），idx0 → 0x06005820 指令边界齐整。附带性质：链表遍历对**全部同名节点**
摘链（循环不因命中退出），若 open 曾重复执行（重入保护失效场景）造成多个 24MHz
节点，一次 notify(11) 全部回收 [静态]。

辅助钉死：STREAM_EVENT_CLOSE_PLAYER=11（SDK 枚举 NONE=0…CLOSE_PLAYER=11，与固件
3 个调用点 r0=11 实证互证）；jlstream.h 声明 `void jlstream_stop(struct jlstream *,
u16 fade_msec)`（fade 0=立即）。

### 10.2 载荷：`tools/hearing-aid/mic_effect_close.S`

**20 条指令 / 64B 代码，零重定位**（.equ 常量折叠）。与激活载荷共用数据槽
（MIC_EFX_SLOT 同 .equ 语义），重入保护反向利用——槽==0 直接成功返回（幂等，
重复 close 无害）：

```
mic_effect_close():
    [--sp] = {rets, r6-r4}
    r4 = [MIC_EFX_SLOT]
    if (r4 == 0) return 0                    ; 未激活：幂等成功
    stop(r4, 0)                              ; @0x060741ee，fade 0（0x06016c12 同参）
    release(r4)                              ; @0x06074204
    [MIC_EFX_SLOT] = 0                       ; = g_mic_effect_player = NULL（先于 notify）
    event_notify(11, "mic_effect")           ; @0x060057e6 + 名串 0x060a1de2
    return 0                                 ; close 无错误语义（固件调用点同样不查返回值）
```

与 SDK 序列的差异及依据：

| 差异 | 依据 | 强度 |
|---|---|---|
| 不做 DAC_NOISEGATE_ON | noisgate 固件锚点未钉死（§1.4/§6.3 既有边界），与 open 不做 OFF 对称 | 文档 |
| 无 free(player)/player=NULL | open 载荷不 malloc player，槽直接存 stream；清槽即等价 g_player=NULL | 静态+文档 |
| 无 ram_code_unload | open 从未 ram_code_load（该对调用的门控宏在 QCY 构建未证为真） | SDK |
| 不查各 API 返回值 | 固件全部 close 调用点（tone/esco/a2dp…）均不查；stop/release 返回 void（SDK 头） | 静态+SDK |

寄存器约定与 open 相同：r4(stream)跨 call 保存，r5 临时，r6=API 地址；callee-saved
序言/尾声 `{rets, r6-r4}`。

### 10.3 离线验证（可复跑）

`tools/test_hearing_aid_close_payload.py`（unittest，已接 `--units`），6 断言全过
[实测]：①20 条指令逐条 encode(decode)==原字节零 diff + 数量落在 [14,30] 形状区间；
②3 个 API 锚点各以 r6 装载恰一次；③常量 11/0x060a1de2 在指令流中存在；④每个
`call r6` 前一条必是对应 API 地址装载；⑤MIC_EFX_SLOT 双基址（0x060e3200/0x060e4000）
变体差异字节全部落在槽 li.u32 内（基址不敏感）；⑥调用顺序==固件三段式
（stop→release→notify），槽恰装载两次（守卫+清零）、清零恰一次、stop 前 r1=0
（fade）、守卫分支在 stop 之前。复跑：`python3 tools/test_hearing_aid_close_payload.py`
（0.5s）。

**仿真验证的诚实结论：close 全链在路 O 场景验证不了。**原因：O 的
`mic_effect_payload` 场景是「跑到合成状态边界即停」的探针（open 在 start 入场的
0x20004 事件派发 0x6074a96 停机），不是可续跑机器——「open 后跑 close」没有可挂载
的中间态；而 close 自己的 stop 也走同一 cmd-post→流服务线程→事件派发路径，同一
单任务桩边界拦住。单独跑 close 只能覆盖「槽==0 早退」平凡路径（ROI 近零）。静态
往返+锚点核对为准，全链执行验证待接驳进入主仿真前沿（§9.4.2 同一欠账）。

### 10.4 数据槽写回设计（文档级，不实施）

问题重述（§9.4.3）：槽在追加 mic_effect.bin（flash 映射区）尾 4B；open 的
`[槽]=stream` st.w 在真机大概率被静默丢弃 → 重入保护永不闭锁 + close 找不到 stream。
需求本质：**一个 4B 运行时变量，boot 时读 0，open 写/close 清，仅载荷访问**。

| 方案 | 内容 | 评估 |
|---|---|---|
| A. 槽挪 RAM | 候选=BSS 区死字节/大结构保留字段；两载荷 .equ 改值+构建器去镜像内槽初始化 | **推荐（加固项）**。难点=钉地址：需证明候选「全 app 无绝对引用**且**无基址+偏移访问**且**落在 boot 清零范围」。本路已跑全 app 绝对引用扫描（li.u32 指向 0x126000..0x128400 的 154 个地址+空隙表，一次性脚本不进库）——只能排除绝对引用；QCY 大结构（基 0x1265c0，1248 处引用）字段全是基址+偏移访问，需 base-provenance xref（路 L 的 xref 雏形可扩）。工作量：钉地址≈半天，构建器侧 <20 行（fence 不动——数据访问不受 PC fence 约束，§3 配套 1 已钉） |
| B. 不存指针按框架推导 | close 时遍历框架结构找 stream | **不成立**：jlstream 无公开按 uuid/scene 查流 API（SDK 头穷举）；0x124880 时钟链表只有时钟节点（name@+8）无 stream 指针 |
| C. flash 写回走 syscfg/VM keyed store（路 CR/K） | open/close 时 syscfg_write 槽值 | **不推荐**：语义错位——槽是运行时指针（重启即失效），持久化反而引入跨重启脏指针新问题（还需 boot 清零钩子），且每次开关一次 flash 编程（磨损+延迟），syscfg_write 锚点也未钉 |
| D. 构造性消除依赖（改变优先级） | 若离开 TRANS 的**全部**路径都先调幂等 close，且同模式重入被 dispatch 0x0600db66 的 `b[r2+5]==r5` 早退过滤，则 open 永不会在未 close 时重复执行 | **短期正解**：此条件下 flash 槽读 0 时 close 早退无害，「写不进」只损失防御性不损正确性。前提=接驳全链（键循环 ON/OFF 两链 4 窗口 + **App 0x17 直设路径 0x0600dcee**，后者窗口未钉死） |

推荐顺序：**先 D 后 A**——接驳完整时 flash 槽已够用；A 作为加固项在候选升级真机
验证前做（半天的地址钉死 + 构建器小改）。C 除非 RAM 路线彻底堵死否则不碰。

### 10.5 接驳扩展设计：`--hearing-aid-l1-close`（文档级，不实施）

路 T 候选只接了两个 TRANS 窗（进通透=关 ANC 通透+开辅听）；离开通透（键循环
TRANS→ON、App 设 OFF/ON）不触发 close → 辅听残留。扩展=再替换 4 个 8B 窗口，
ON/OFF 路径先调幂等 mic_effect_close 再走原 apply。全部窗口本路静态钉死 + 全 app
分支落点扫描复核（同路 T 方法）：

| 窗口 | 起点 | 原字节（8B） | 语义 | 进入方式（唯一） | 落点扫描 |
|---|---|---|---|---|---|
| A TRANS-1（路 T 已接） | 0x0600dc7e | bfea14f3 d0ec0407 | call ctx_get; r0=[r0+116] | 0x0600db92 分支 | 内部零落点 [文档:路 T] |
| B TRANS-2（路 T 已接） | 0x0600dca4 | bfea01f3 d0ec0407 | 同上 | 0x0600dbbc 分支 | 同上 |
| ON-1 | **0x0600db9a** | bfea86f3 d0ec0007 | call ctx_get; r0=[r0+112] | 顺延（0x0600db96 `if (r5!=2)` 不跳） | **零落点**（纯顺延） [静态] |
| OFF-1 | **0x0600dc74** | bfea19f3 d0ec0807 | call ctx_get; r0=[r0+120] | 0x0600db8e 分支 | 起点 1 落点（该分支本身）、内部零 [静态] |
| ON-2 | **0x0600dbc4** | bfea71f3 d0ec0057 | call ctx_get; r5=[r0+112] | 顺延（0x0600dbc0 不跳） | 零落点 [静态] |
| OFF-2 | **0x0600dc9a** | bfea06f3 d0ec0807 | call ctx_get; r0=[r0+120] | 0x0600dbb8 分支 | 起点 1 落点（该分支本身）、内部零 [静态] |

注意 OFF-2 起点是 **0x0600dc9a 不是 0x0600dc98**（0x0600dc98 是上一函数的
`{pc,r8-r4}=[sp++]` pop 尾巴，且它自身是 4 个早退分支的活落点——0x0600db4a/db54/
db66/db6c——窗口若左移 2B 会砍掉活代码；0x0600dbb8 的分支恰好落在 call 起点
0x0600dc9a）。两链的 ON/OFF 窗后各自 2B `goto`（→汇合点 0x0600dc86/0x0600dccc）
不在窗口内、保留原样。

桩形状（进入状态同路 T 桩：r4=apply flag；close 只碰 r0-r3/r6，r4/r5 保存）：

```
leave_r0（ON-1/OFF-1/OFF-2 通用，槽位 112/120，返回地址=窗口+8）:
    r6 = 0x0600c2aa; call r6       ; 重放取 ANC ctx
    r5 = [r0+SLOT]                 ; 重放槽位读取，存 r5（close 不碰 r4-r6）
    call mic_effect_close          ; 幂等关辅听
    r0 = r5                        ; 恢复 apply fn
    r6 = <窗口+8>; goto r6         ; 回原流（原 2B goto / 后续指令）

leave_on2（ON-2 专用：fn 载入 r5 且后续 0x0600dbcc 的 call 0x0600bbba 消费 r0=ctx）:
    r6 = 0x0600c2aa; call r6
    r5 = [r0+112]
    call mic_effect_close
    r6 = 0x0600c2aa; call r6       ; 重取 ctx 到 r0（close 清了 r0-r3；getter 纯读）
    r6 = 0x0600dbcc; goto r6
```

段几何：close 载荷 64B 追加进路 T 组合段（open+close+桩 单段现场汇编，两遍定槽
不变——close 只读/清槽，槽仍在段尾 4B）；fence 上限相应扩到新段末；CRC 链照 §9.3
全套重算。开关语义建议 `--hearing-aid-l1-close` **隐含** `--hearing-aid-l1-patch`
（同一候选族超集，仍与 EQ 修复候选互斥）。App 0x17 直设路径（0x0600dcee）的窗口
族本路未反汇编钉死，接驳实施时补（§10.4 方案 D 的完整性前提包含它）。

### 10.6 诚实边界（必须随载荷一起读）

1. **离线载荷，未执行验证**：close 全链未在真机/仿真跑（§10.3 说明了 O 场景为何
   验不了）；本节交付「字节与固件惯用法逐 call 同形 + 汇编器可信 + 锚点有固件活
   调用点先例」三件事，**不构成「辅听可开关」声明**。「辅听粘住」的真正解决以
   §10.5 接驳实施为前提——本路交的是载荷+全部静态材料。
2. **同步等待的上下文风险**：stop/release 是 post 命令给流服务线程并同步等完成的
   形态（0x06074192 族）。固件现有 close 调用点所在任务上下文未逐一核对——按键/
   模式 dispatch 上下文同步调它们是否与流服务线程有优先级/互斥死锁风险，只有
   [推断]（固件 tone/esco close 与 dispatch 同为应用层同步风格）。接驳实施前应
   核对至少一个固件 close 调用点的调用者上下文。
3. **release 的 b[stream+17]==3 门**：字段/极性未钉死（QCY jlstream 结构与 SDK 头
   布局有差异，§1.2 已记）。自洽读法=该门拦「运行态直接 release」，固件全部 close
   都是 stop 先行使流离开运行态、pc_mic 错误路径在 start 失败后直接 release——载荷
   与固件惯用法同形，风险随固件 [静态+推断]。
4. **event_notify(11) 的整链副作用**：mic_effect 命中才走 clock_free；但 case 11 的
   strcmp 链在 mic_effect 之前还比对 5 个名字（strcmp×5 的开销，flash 串只读）。
   0x060008CA 日志助手每次 notify 都跑（ICFG 检查+日志调用）——与固件 pc_spk/a2dp
   的 notify 调用同款，无新增风险 [静态-浅层]。
5. **数据槽写回未实施**（§10.4 方案 A）；**接驳未实施**（§10.5）；App 0x17 路径
   窗口未钉死。§6 的既有边界（set_scene 16、noisgate、单线程形状）不变。

### 10.7 证据锚点表（本路新增钉死）

- close 链 API：jlstream_stop **0x060741ee**（h[stream+46]=fade + post cmd4；
  7 调用点 0x0600c1a4/0x06005c42/0x06006228/0x06013a7c/0x06016c12/0x06016c58/
  0x06016cd4，fade 实值 50/100/0）；jlstream_release 0x06074204（路 L）；jlstream_
  event_notify **0x060057e6 = get_pipeline_uuid 同入口**（tbh switch @0x06005804，
  表 13 项起 0x06005806，target=表基+2×条目）。
- 事件 11 调用点：0x06005c66（r1="pc_spk"@0x060a1afa）、0x06013a9e（"a2dp"@
  0x060a1986）、0x06016c7a；CLOSE_PLAYER 分支落点 0x06005820（strcmp 链 6 名），
  mic_effect 命中 → clock_free **0x0600572C**（互斥 0x126E50、时钟链表 0x124880、
  name@节点+8、循环摘链全部同名、尾随日志 0x060008CA）。
- 三段式 close 惯用法样本：0x06016c58..0x06016c7a（stop(fade0)→release→free→
  g=NULL→notify(11)）。
- 接驳窗口（§10.5 表）：ON-1 0x0600db9a / OFF-1 0x0600dc74 / ON-2 0x0600dbc4 /
  OFF-2 0x0600dc9a（注意 0x0600dc98 pop 是活落点不可入窗）；汇合点 0x0600dc86 /
  0x0600dccc 不变。
- 载荷：tools/hearing-aid/mic_effect_close.S（20 insns / 64B，0 relocs）；验证
  tools/test_hearing_aid_close_payload.py（6 tests OK）。

## 11. close 接驳镜像实施：离线候选 `--hearing-aid-l1-close`（路 Y / sim-l1closeimg，R2 L1 双向闭环）

2026-11-14。本节把 §10.2 的 close 载荷 + §10.5 的接驳方案做进**离线候选镜像**：
`tools/build_quiet_candidate.py` 新增 opt-in 开关 `--hearing-aid-l1-close`（**隐含
`--hearing-aid-l1-patch`**，路 X 的建议；仍与 EQ 修复候选互斥——同一追加点），实施代码在
`tools/hearing_aid_l1_image.py` 扩展，接驳桩新源 `tools/hearing-aid/mic_effect_close_hook.S`，
新单测 `tools/test_hearing_aid_close_image.py`（已接 `check_local.py --units`）。§10.2 的
`mic_effect_close.S` **一字未改**（构建时只 strip 其 `.section` 行并入单段现场汇编，其独立
单测原样通过）；`mic_effect_open.S` / `mic_effect_hook.S` 同样一字未改。**不刷机、不连真机；
候选 UNTESTED；EQ 修复候选 67ba97ef 状态不变。**

### 11.1 组合段几何（open+close+桩 单段，两遍定槽）

构建时现场汇编单一代码段：open 134B + close 64B + TRANS 桩×2（64B，路 T 原样）+
leave 桩×4（24/24/30/24B=102B）= **364B 代码 + 4B 槽**，零重定位。槽 = 段尾 4B
（0x060E31E0），两遍汇编定槽位（open/close 各 2 处 `r0=<槽>` li.u32 随槽值变化，其余字节
基址无关——单测+构建时断言都钉死）。

| 项 | 值 |
|---|---|
| 追加文件 `mic_effect.bin` | 401B（头 32 + 0xff + 代码 364 + 槽 4×00），flash [0xE5073, 0xE5204) |
| mic_effect_open | 0x060E3074，134B（=§2 载荷逐字节，构建时断言前缀==独立汇编） |
| mic_effect_close | 0x060E30FA，64B（=§10.2 载荷逐字节，同上断言） |
| trans_branch_1 / 2 | 0x060E313A / 0x060E315A（比 §9.1 的 T 候选 +0x40：close 载荷插在前） |
| leave_on1 / off1 / on2 / off2 | 0x060E317A / 0x060E3192 / 0x060E31AA / 0x060E31C8 |
| 数据槽 | 0x060E31E0，初始 00 00 00 00（open 写 / close 清，§10.4 方案 D 语义） |
| 代码段 sha256 | f476d27c32628eb9656772002cc14c26f31669321f296f300e2b8b1efa5d7f15 |

桩形状实作与 §10.5 成文一致，两点实作级补充（均本路反汇编核对）：

- **ON-2 的 ctx 重取是防御性的**：0x0600dbcc 的 call 0x0600bbba 反汇编为**自建参数**
  （入口即 `r4=QCY; r1=r4+37; r0=15; r2=1`，不读任何传入参数）——ctx 重放并非必需，
  但照抄 §10.5 形状保守保留（重放无害：0x0600c2aa 是纯读叶子，只写 r0-r2）。
- **r5 复用安全**：leave 桩把 apply fn 暂存 r5（close 自带 {rets,r6-r4} 保存恢复，r5 跨
  call 不变）；窗口后原流里 r5 均为死值（链 1 汇合只消费 r4=flag，链 2 后续路径全部覆写
  r5），复用不破坏任何活性。

### 11.2 接驳窗口与 fence（6 窗口）

四个新窗口按 §10.5 表整窗替换为 `r6=<桩地址>; goto r6`（6B+2B 惯用法，与 T 的 A/B 窗
同款）；A/B 两窗的**桩地址随段内插入 close 而整体 +0x40**（窗口字节因此与 T 候选不同，
语义不变）：

| 窗口 | 起点 | 原字节（8B） | 进入方式 | 桩 | 桩行为 |
|---|---|---|---|---|---|
| A（T 已接） | 0x0600dc7e | bfea14f3 d0ec0407 | 0x0600db92 分支 | trans_branch_1 | 关 ANC 通透+open，goto 尾声 0x0600dc8c |
| B（T 已接） | 0x0600dca4 | bfea01f3 d0ec0407 | 0x0600dbbc 分支 | trans_branch_2 | 同上 → 0x0600dcd2 |
| ON-1 | 0x0600db9a | bfea86f3 d0ec0007 | 顺延（0x0600db96 不跳） | leave_on1 | 重放 ctx+ON 槽 → close → r0=r5 → 回 0x0600dba2（原 2B goto→汇合 0x0600dc86） |
| OFF-1 | 0x0600dc74 | bfea19f3 d0ec0807 | 0x0600db8e 分支 | leave_off1 | 同形（OFF 槽）→ 回 0x0600dc7c |
| ON-2 | 0x0600dbc4 | bfea71f3 d0ec0057 | 顺延（0x0600dbc0 不跳） | leave_on2 | 重放 ctx+ON 槽(r5) → close → 重取 ctx(r0) → 回 0x0600dbcc（原 call 0x0600bbba） |
| OFF-2 | 0x0600dc9a | bfea06f3 d0ec0807 | 0x0600dbb8 分支 | leave_off2 | 同形 → 回 0x0600dca2 |

语义闭环：进通透（A/B）= 先关 ANC 通透再 open；离开通透进 ON/OFF（4 新窗）= 先幂等
close 再走原 apply。同模式重入被 0x0600db66 `b[r2+5]==r5` 早退过滤（§10.4 D 前提），无效
模式/无 ctx 的早退（0x0600db4a/db54）在全部窗口之前、不受影响。

**落点扫描复核（路 T 方法重跑，本路实测）**：全 app 线性反汇编（官方字节，加载基址
0x06000100）收集 **51,701 个分支落点**（T 记录 51,709——同一方法、指令形式集合微差，
结论一致），对 6 窗口逐一断言：**内部（起点后 1..7B）零落点**；起点落点 ON-1/ON-2=0
（纯顺延）、OFF-1 恰 1 条=0x0600db8e、OFF-2 恰 1 条=0x0600dbb8、A/B 各 1 条=0x0600db92/
0x0600dbbc（与 §1.4/§10.5 钉死一致）。**0x0600dc98 的 pop 确认是 4 个早退分支
（0x0600db4a/db54/db66/db6c）的活落点**，在 OFF-2 窗口外（OFF-2 从 0x0600dc9a 起）。
寄存器间接 goto/call（goto rN/call rN）无静态目标，不在扫描内——与 T 窗口同一残留限制。

**fence 扩界**：0x06034e98 的 LIM_PC0 上限 32 位立即数 0x060E313E（T 候选值）→
**0x060E31E4**（=追加文件末端运行地址）；字节差异 1B（`3e`→`e4`，0x060E31 前缀与
0x06 高位不变）。下限 0x06000100 与使能位不动。

### 11.3 CRC 链与字节级验证（E/T 路框架）

CRC 全链照 §9.3：config.dat 末文件标志+头 CRC → 新文件头/数据 CRC → app.bin 双层 JLFS
CRC → app_area_head 双层 CRC → boot-info app_size 0xE3053→0xE5204 + 三层 CRC → 追加区
整段 SFC 重加密（0x3496）→ UFW 条目 CRC + 外层头 CRC。

规范构建（`--attenuation-db 0 --hearing-aid-l1-close`，除 L1 双向外零改动）产物
`H3S_0.1.1.8_tones_minus0dB_HEARING_AID_L1_CLOSE_OFFLINE_UNTESTED.ufw`，sha256
`5a5f2ef7b89194c6f5db9c5f1d8b3c6eab1101fcc4997e1d351753a36ba6c96f`。与官方镜像全文件
**逐字节差异恰为 476 字节**：

| 区域 | 字节数 |
|---|---|
| UFW 外层（头 CRC 2 + 条目表 CRC 2 + flash 条目 CRC 2） | 6 |
| boot-info 32B 编码区（app_size 2 + 头/明文/密文 CRC） | 8 |
| app_area_head + app.bin 双层 JLFS CRC | 8 |
| config.dat 末文件标志 1 + 头 CRC 2 | 3 |
| 接驳窗口 6×8 | 48 |
| fence 立即数 | 3 |
| 追加文件 mic_effect.bin 401B（密文对 0xff 填充；1B 密文碰巧等于原 padding） | 400 |

与「只有 l1-patch 的候选」（§9.3，sha `6c7247a5…` 本路复现一致）**逐字节差异恰为 293
字节**，构成即「加 close」的增量：4 个新窗 32 + A/B 桩地址迁移 3 + fence 1 + 追加区密文
236 + CRC 链 21（boot-info 7 + 双层 JLFS 8 + UFW 6）。全部差异逐字节分类断言，无一例外
字节（一次性脚本在本地 tmp，不进库）。

验证清单（全部通过 [实测]）：①69 条指令（open 41+close 20+桩 8）逐条
encode(decode)==原字节零 diff（构建时逐条断言 + 单测复跑）；②组合段前 198B == open/close
独立汇编结果拼接（载荷源未改的机器证明）；③全部桩形状与固件目标（ctx getter
0x0600c2aa/槽位位移 112/120/内部 call 落 close 入口/返回=窗口+8/两尾声/载荷入口/槽地址）
从最终镜像字节反解码核对；④构建器 `verify_l1_image` 全镜像精确差异断言（SFC 密文独立
重算 + boot-info 五字段独立重算 + 窗口反解码 + 差异集合精确相等）；⑤独立校验器
`verify_ufw_integrity.py` 全过（含新 mic_effect.bin 401B 条目 JLFS CRC 与双备份 CRC）；
⑥legacy：`--hearing-aid-l1-patch` 产物 sha 仍为 `6c7247a5…`（T 候选逐字节复现）、
`check_reproduction.py` 过（official/b79 双复现）、`check_local.py --units` exit 0
（含新单测 7 tests：open 变体段 sha `b0feed11…` 钉死不变）。复跑：
`python3 tools/build_quiet_candidate.py --attenuation-db 0 --hearing-aid-l1-close
--output-dir <空目录>`（构建器要求输出目录不存在）。

### 11.4 App 0x17 直设路径评估（未实施，成文方案）

本路对 0x0600dcee 反汇编（只读）：**另一族 dispatch，不经 0x0600db3c**——`{rets,r7-r4}`
序言、sp-=12、`r0==3/==2/!=1` 三分支（0x0600dcf8/0x0600dcfc/0x0600dd00）、**tbb 跳转表**
（0x0600dd2e，r2=r1-1）、查询-再设置惯用法（call 0x0600BBD8 查态 → 分支 →
0x0600C092/0x0600C294/0x0600dcde 包装族设置），mode apply 点的形状与键循环的
[ctx+slot] 惯用法不同。接驳它需要：App wire 模式 id→内部模式映射、该链 ANC apply 点钉
死、新窗口族落点扫描——工作量 ≈ 一条独立静态路（≈ 路 T 的窗口钉死工作量级），本路按
任务边界**留文档不硬做**。后果：App 直设通透/离开不走 open/close（App 通透与按键语义
分叉，§3 配套 4 的既有欠账；§10.4 方案 D 的完整性前提因此仍差这一块——App 路径离开通透
后槽内残留 stale stream，下次按键进 ON/OFF 时 leave 桩的幂等 close 会 stop/release 它，
但 stop 作用在可能已释放的对象上的安全性未验证）。

### 11.5 诚实边界（必须随候选一起读）

1. **离线候选 UNTESTED，不刷机**。交付=「字节与 §1-10 证据一致 + 四层 CRC/JLFS 结构
   自洽 + 逐字节差异精确可控」，不是任何形式的运行验证。
2. **close 全链仍零执行证据**：§10.3 已说明路 O 场景为何验证不了 close；本节接驳后的
   完整路径（桩+载荷在 ANC 键循环里）同样未在仿真跑——主仿真前沿未推进到 0x0600db3c
   （§9.4.2 同一欠账）。静态往返+落点扫描+锚点核对是当前最强证据。
3. **数据槽仍在 flash**（§10.4 方案 A 未实施）：方案 D 的键循环前提（两链 6 窗口全接）
   本候选已满足；App 0x17 前提未满足（§11.4）。真机验证前建议先做方案 A（槽挪 RAM）。
4. §10.6 全部既有边界继承：stop/release 同步等的上下文风险（leave 桩在按键 dispatch
   上下文同步调 close，同一风险面）、release 的 b[stream+17]==3 门、event_notify(11)
   整链副作用、noisgate 未配对、set_scene 16 是 SDK 枚举值。
5. 与 EQ 修复候选互斥（开关同时给会报错）；EQ 候选 67ba97ef UNTESTED 不变。

### 11.6 证据锚点表（本路新增钉死）

- 组合段（open+close+6 桩）：364B+4B 槽 @0x060E3074..0x060E31E4，段 sha256
  f476d27c…；close 入口 0x060E30FA；leave 桩 0x060E317A/0x060E3192/0x060E31AA/
  0x060E31C8；TRANS 桩迁移后 0x060E313A/0x060E315A。
- 窗口替换字节（before→after）全表见构建报告 `hearing_aid_l1.hook_windows`（6 窗，
  build-report.json 随产物落盘）；A/B 窗 after 随桩迁移变化：A `c6ff3a310e06 d600`、
  B `c6ff5a310e06 d600`。
- leave 桩固件目标：ctx getter 0x0600c2aa（纯读叶子，只写 r0-r2）；返回点 0x0600dba2/
  0x0600dc7c/0x0600dbcc/0x0600dca2（=各窗+8）；0x0600bbba **自建参数不读传入 r0-r3**
  （ON-2 重取 ctx 仅防御性）；0x0600dc98 pop=4 早退分支活落点（0x0600db4a/db54/db66/
  db6c）。
- 落点扫描：51,701 落点全 app 复扫，6 窗内部零落点、起点落点与 §1.4/§10.5 全等。
- 候选：sha256 5a5f2ef7…（476B vs 官方）；与 l1-patch 候选差 293B；单测
  tools/test_hearing_aid_close_image.py（7 tests OK）；构建器
  hearing_aid_l1_image.py（apply_l1_patch close_hooks=True + verify_l1_image 泛化）。
