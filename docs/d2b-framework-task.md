# D2-B 前置：框架任务创建链 + 消息消费上下文证据包（路 CI / sim-fwtask，只读预研）

2026-11-13。本页是 D2-B（效果节点状态 = EQ 验证的 D2 半场）的接线供料：回答 BX §5.1
留下的「框架任务创建点（init walker vs 惰性）一处运行时观测」，并把 CF 停点
（0x12b56c idle 轮询首读 IPMASK）两侧的创建链与消费链全部静态钉死，给出 D2-B
场景所需的 fixture 清单。**只读**：未改 tools/ 任何代码；反汇编用钉死 codec
（pi32v2-isa @3608ed1，worktree 内 firmware/reference-cache 软链复用主仓钉死缓存，
路 BX/CE 先例）；一次本地场景复跑（`--tone-scene --report` 指向本地 tmp，零基线
改动）；一次性脚本放本地 tmp 不进库。结论基于 main `da78bac`（= f7878d0 后
sim-d2arevise 合并点）。

证据强度：**[静态]** = 指令锚定反汇编/数据表解码（本路）；**[trace]** = 既有报告
实测（analysis/local-checks/irq-entry.json @601,880 与本次 tone 场景本地复跑）；
**[SDK]** = 参考 SDK media.a llvm-dis（参考材料，非精确 QCY 源）；**[文档]** = 既有
docs/HANDOFF 钉死条目；**[推断]** = 上述链推出。

## 0. 摘要（三个问题各一句话）

1. **框架主任务 'jlstream' 在 app_core 任务体的 init walker 里创建，不是惰性**：
   创建入口 0x60759ee 是 initcall 表项（数据引用 @0x60c3274），由 app_core 任务体
   0x6012216..0x6012228 的逐条 `call [r4++=4]` walker 执行；任务体入口 **0x6086980**，
   优先级 3、栈 0x300 字（3072B）。BX §5.1 的「一处运行时观测」就此静态闭合。
   **主前沿 601,880 前缀里它没有被创建**（app_core 任务体尚未执行到 init walker）。
2. **任务体 0x6086980 与 S19 主循环（0x60123e6）是同一 get-event 惯用法、独立队列**：
   同一 API 链 0x6064c8c→0x6064b98→0x60649b0→ROM pend 0x12b55c，S19 pend 的是
   'app_core' 任务的 taskq（buf 16 字），0x6086980 pend 的是 'jlstream' 任务的
   taskq（buf 8 字）；post 侧同一 API 0x6064d72 按名字路由。
3. **CF 停点精化**：0x12b56c 停在 **stream 槽分配函数 0x60744b4 内部的 pend 段
   （0x60744ea→0x12b55c）**；0x6064162/0x6064f18 是队列对象准备（q_init/q_create），
   真正的 stream-create 请求 post（0x6075414→0x6064d72('jlstream',2,…)）在停点
   之后、尚未执行——CF 条目「注入的事件已 post 进 OS 队列」的表述按本路证据
   精化（见 §5）。

## 1. 任务创建 API 全栈与 38 条任务注册表 [静态]

### 1.1 创建函数栈（自上而下）

| 函数 | 角色 | 关键证据 |
|---|---|---|
| **0x6067242** `task_create_byname(entry, pdata, name)` | 按名字查配置表创建 | 入参 r0=入口/r1=pdata/r2=名字串指针；walk 12B 记录表（基址 li 0x609d780，0x606724e），ROM 0x1fd84 strcmp(name_表, r2)；命中取 {b4→r2 优先级, h6→r3 栈字数} 后 call 0x60650a0，栈参 [sp..sp+8]={entry, name, pdata}（0x6067266..0x6067278） |
| **0x60650a0** `task_create_core` | TCB+栈分配 | r10=entry、r8=pdata、r9=name、r4=栈字数；TCB 88B 经 ROM 0x12e160 zalloc（0x60650b4）；0x60650da call 0x6065e80(栈字数)；0x606510a call 0x6065020 完成注册 |
| **0x6065e80** 栈/帧分配器 | OS 帧池位图扫描 | 基址 r10=0x1265C0（QCY/OS 全局结构），帧池表项 [0x1265C0+7856]=[0x127A20]（0x6065f38），帧大小按入参<<7 位运算定位（0x6065f40..0x6065f4e） |
| **0x6065020** `task_create_finish` | 查重+帧初始化+注册 | **0x6065034 call 0x60647fa(name) 同名查重**（错 25）；**0x606505a call 0x6065f96(entry)** 帧初始化；TCB 挂核任务链 [0x1265C0+1692+cnum*4]（0x6065060..0x6065074）；0x6065086 call 0x6064280 名字注册 |
| **0x6065f96** 帧初始化 tail | 写任务帧 | `r2+=-76; [r2+12]=r1; r1=0; [r2+8]=r1; d[r2+0]=r1_r0`——**0x6065f9e 双字存储 {RETS=0, RETI=entry}**，即 irq-entry.json milestones 的 watch 点（r0=任务入口的 4 次记录） |

- idle 任务例外：0x60652c8（per-core）直接调 0x6065e80(2048)+0x6065020（不走
  配置表；[r6+12]=0x200 PSR 初值，b[r6+0]=cnum）。[静态]
- **BZ 修正互证**：pmu_task 创建（HANDOFF 路 AF/X）即本栈——milestones
  `0x6065f9e r0=0x6068f0c`；「AF/AG 的任务创建实为 sys_timer_add thunk
  （0x6066c26→0x6066ba6）」与本栈无关，两口径并存不冲突。[文档+静态]

### 1.2 12B 任务配置注册表 @0x609d780（38 条，本路全量解码）[静态]

记录 {u32 name_ptr, u8 优先级, u8 flags, u16 栈字数, u16 qlimit}：

| # | 名字 | 优先级 | flags | 栈字数 | qlimit | 备注 |
|---|---|---|---|---|---|---|
| 0 | app_core | 1 | 0 | 0x300 | 0x200 | 与 milestones 创建记录互证 |
| 1 | btctrler | 4 | 0 | 0x200 | 0x200 | |
| 2 | btencry | 1 | 0 | 0x200 | 0x80 | |
| 3 | btstack | 3 | 0 | 0x400 | 0x200 | |
| **4** | **jlstream** | **3** | **0** | **0x300** | **0x80** | **框架主任务（§2）** |
| 5..12 | jlstream_0..7 | 5 | 0 | 0x300 | 0x0 | 框架子任务（§2.2），一实例一任务 |
| 13 | a2dp_dec | 4 | 1 | 0x100 | 0x0 | flags=1 组：a2dp_dec/spatial_sensor/aec/audio_vad/afq_common |
| 14 | spatial_sensor | 2 | 1 | 0x200 | 0x80 | |
| 15 | aec | 2 | 1 | 0x300 | 0x80 | |
| 16 | aec_dbg | 3 | 0 | 0x200 | 0x80 | |
| 17..23 | update/tws_ota/tws_ota_msg/dw_update/aud_capture/data_export/anc | … | 0 | … | … | |
| **24** | **dac** | **2** | **0** | **0x100** | **0x80** | DAC 任务（§2.3），入口 0x6076b4c |
| 25..37 | rcsp/usb_stack/ui/dev_mg/audio_vad/pmu_task/imu_trim/iis_play_task/periph_demo/CVP_RefTask/enc_mpt_sel/afq_common/mic_effect1/mic_effect2 | … | … | … | … | pmu_task[30] 与 HANDOFF 路X 互证 |

表尾哨兵 0 @0x609d954（38 条后全零）。表内字段名「优先级/栈字数/qlimit」中，
优先级与栈字数有调用链直接证据（0x6067266..0x6067278 传入 0x60650a0 的 r2/r3，
栈字数在 0x60650a0 内 <<2 后传 0x6065e80）；h8/qlimit 字段沿传入但消费点未逐层
跟（[推断]：os 任务 qlimit/时间片参数类）。

### 1.3 主前沿 601,880 前缀的任务创建状态 [trace]

analysis/local-checks/irq-entry.json（p11_heap_experimental_config @601,880 @
0x6069540 unknown_gpcnt_configuration_bits）的 milestones 记录 0x6065f9e 执行
恰 4 次，r0（任务入口）= 0x6012028（app_core）、0x12c57e（idle，CPU0/CPU1 各一次）、
0x6068f0c（pmu_task）。**'jlstream'/'jlstream_N'/'dac' 均未创建**。原因：app_core
任务体 0x6012028 在该前缀尚未执行（停点上下文=ADC 轮询段（0x60116xx）之后的
sys_timer 到期回调 0x6069538→0x6069540 读 GPCNT 停），init walker（0x6012216，
见 §2.1）在 ADC 段之后、尚未到达。[trace+推断]

## 2. 框架任务创建链（三个创建点）[静态]

### 2.1 主任务 'jlstream'：app_core init walker（非惰性）

```
app_core 任务体 0x6012028（OS 调度选中后首次执行）:
  ...0x60116xx ADC 采样段（主前沿当前停点域）...
  0x6012216: r4 = 0x60C3250; r5 = 0x60C327C
  0x6012224: r0 = [r4++=4]; call r0        ; initcall 表逐条调用
  0x6012228: if (r4 < r5) goto -8          ; 范围 [0x60C3250, 0x60C327C) = 11 条
  0x601222c/0x6012242: 另两段表（0x60C3298 单条、0x60C327C..0x60C3294 七条）
  ...0x60123e6 S19 消息循环...

initcall 表 [0x60C3250, 0x60C327C) 的 11 条:
  0x6004360, 0x60074e6, 0x601043a, 0x6027658, 0x602f5a8, 0x603346e,
  0x6033678, 0x603371e, 0x607bf2e, ★0x60759ee, 0x607f8be

0x60759ee（jlstream 任务创建入口，叶子函数）:
  0x60759f0: r0 = 0x6086980    ; 任务体入口
  0x60759f6: r2 = 0x609D3BB    ; 名字 'jlstream'
  0x60759fc: r1 = 0            ; pdata
  0x60759fe: call 0x6067242    ; task_create_byname
  → 配置表 [4]（优先级 3、栈 0x300 字）→ 0x60650a0 → 0x6065e80 → 0x6065020
  → 帧写入 0x6065f9e {RETI=0x6086980} → 名字 'jlstream' 注册进 OS 名字表
```

- 0x60759ee **无静态调用者**（全 app 19,587 条线性调用边零命中）；其唯一数据
  引用是 **0x60c3274 的表项**（全镜像 LE 指针扫描单命中）——它只能经 initcall
  表 walker 执行。[静态]
- [SDK] 互证：jlstream_task.c 的 `jlstream_task_create` 位于 `.initcall.bank.text`
  段（BX §1.3 已记）——QCY 对位就是 initcall 表项形态。
- **运行时条件**：OS 调度器选中 app_core（任务 RTI 进 0x6012028）→ 线性执行到
  0x6012216。早于 S19 循环、晚于 ADC 采样段。tone 首次播放**不是**创建条件
  （创建在 init 链，与首条消息无关）。

### 2.2 子任务 jlstream_0..7：stream create 内的惰性 ensure

```
0x6074a72（pipeline 节点操作大函数；含 BX 记的节点查找/创建域）内:
  0x6074d9c: r0 = r6 + 68                    ; r6=pipeline/stream 对象，+68 链表头
  0x6074da0: r9 = 1
  0x6074da4: r6 = 0x6086C8C                  ; 子任务体入口（共用一个任务体）
  0x6074dc4: r7 = [r0+0]                     ; walk 节点链表
  0x6074dac: r0 = b[r7+2]                    ; 已创建标志
  0x6074dae: if (r0 != 0) 跳过
  0x6074db0: b[r7+2] = 1                     ; 置已创建
  0x6074db4: r2 = r7 + 8                     ; 节点+8 = 内嵌名字（'jlstream_N'）
  0x6074db6: r1_r0 = r7_r6                   ; r0=0x6086c8c(入口), r1=r7(pdata=节点)
  0x6074db8: call 0x6067242                  ; 按名字建子任务（名字=节点+8 串）
```

子任务体 0x6086c8c：r4=r0(pdata=节点)；起 100-tick sys_timer（0x6066c26 thunk，
BZ 口径）；q_init(节点+24)（0x6064d9e）；主循环（回 0x6086ce6）：临界 0x12b55c/
0x12b5b0 对、事件位图 b[stream+5]、时间戳 h[stream+12] 水位（65486=0xFFF2 补偿）、
call 0x60867a0 数据推进、0x607607e——**PCM 数据搬运/时间戳任务**形状（每 pipeline
实例一个，pdata 区分）。[静态+推断]

### 2.3 dac 任务：设备打开事件惰性创建

0x6075250（dac 设备 init+任务创建宿主）：call 0x607520e（dac 设备初始化）→
r0=0x6076b4c（任务体）、r2=0x609d541（'dac'）→ call 0x6067242（0x6075276）。
调用者 0x6013fcc（音频设备事件 handler 0x6013fb2 内子码 54 分支）、0x6018000
——**首次 DAC 打开时创建**。[静态]

### 2.4 byname 创建点全集（16 处，本路扫描）

0x6012288、0x601231c（app_core 体内 init 段——两处直接调 byname，创建表内
flags=0 的默认任务族）、0x6015a10、0x60162de、0x6016346、0x601ffbe、0x6020ff4、
0x602f30a、0x6035350（**app_core 任务创建**——milestones 0x6035350 r0=0x6012028
互证：app_core 不走 initcall 表，由 0x6035xxx 启动链直接建）、0x6067a0c（pmu 域）、
0x6074db8（jlstream_N）、0x6075276（dac）、0x60759fe（jlstream 主）、0x60772b8、
0x60772ce（音频节点域）、0x60c28b8（RAM-code 窗内）。

## 3. 框架主任务体 0x6086980 与消息循环 [静态]

### 3.1 任务体全形状

```
0x6086980: sp += -56                         ; 任务体入口（无 push——任务帧由 OS 建）
  r11 = sp|2, r10 = sp+24（消息 buf）
  r12 = 0x1264D8（全局 stream 槽头哨兵）
  r9  = 0x609D3BB（'jlstream'——本任务队列名，post 自指用）
  r14 = 0x124F60, r13 = 10
0x60869a8: r0 = sp+24; r1 = 8; call 0x6064c8c   ; get-event(buf, 8)——空队列 pend
0x60869b2: if (r0 != 13) goto 循环头             ; 13=取得事件（与 S19 同码）
0x60869b6: r0=[buf+4], r6=[buf+8]               ; 载荷两字（对象指针/参数）
0x60869be: r1 = r0-4; if (r1 > 8) 丢弃            ; cmd ∈ [4,12]
0x60869cc: tbb [r1]                              ; 表内联 9 字节 @0x60869ce:
                                                 ; 05 11 9c 9c 2b 3d 72 24 77
  目标 = 0x60869ce + 字节×2（9 分支全部落有效块头，本路逐一验证）:
  cmd4  → 0x60869d8  st[r0+44]=r4（stream 槽登记）; b[obj+9]==3 检查;
                        0x6075a08(obj, 4)
  cmd5  → 0x60869f0  0x12b55c(pend/调度); h[obj+16] 时间戳; 无之则
                        sys_timer_add 0x6066ba6(…,50)
  cmd6/7→ 0x6086b06  循环尾（忽略——两表项同偏移 0x9c）
  cmd8  → 0x6086a24  b[obj+10]==16 状态迁移→b[obj+9]=1;
                        0x6064d72('jlstream',1,0)（0x6086aaa，自队列 post）
  cmd9  → 0x6086a48  0x12b55c; 0x6075bb4(close); 引用计数 b[obj+1]→0x6075be4;
                        0x6075cc8; 遍历 [r0+0] 链至 r12 哨兵
  cmd10 → 0x6086ab2  0x6075d8e(obj)
  cmd11 → 0x6086a16  ★0x6075e22(obj, [buf+12])——cmd-11 参数消息消费者
  cmd12 → 0x6086abc  0x6075de6(obj, [buf+12])
0x6086b06: call 0x6075ce0                       ; 流状态机推进
0x6086b1a..: stream 对象 walker（b[obj+17] 状态: 1→close 链 0x60746a4/0x6075e44/
             0x60747dc; 3→0x60867a0 数据回调; 16→…; 水位/时间戳维护 0x6066c26）
  → 回 0x60869a8 循环（永不退出，与 S19 同形）
```

### 3.2 cmd-11 参数消息消费链（D2-B/D2-D 节点面）

```
0x6075e22（cmd11 消费者; 调用点唯一 0x6086a1c=cmd11 分支内）:
  → 0x6075d8e → 0x6074a72（pipeline 节点操作大函数）
      ├─ 0x60746e6 节点查找/创建：复用或 0x607471e call 0x607452e（uuid→适配器
      │   28 条 ops 表 0x60a8ed0，BX 已钉）→ ROM 0x12e16e zalloc(44) 建节点
      │   → h[node+2]=uuid, b[node+0]=subid（0x6074734/0x6074738）
      ├─ 0x6074d9c 子任务 ensure walker（§2.2）
      └─ 节点 op1 ioctl → eq 0x6078594 / wdrc 0x607898e（subcommand 分发，
         subcmd2 → 0x6078a2e `b[node+35]=value`；0x60787ac open-config 内含
         钩子 0x6078846 与源选择 0x60057e6/0x60059ce——BT/BX 已钉，不重复）
```

### 3.3 与 S19 主循环（0x60123e6）的关系

| 维度 | S19（app_core） | 0x6086980（jlstream） |
|---|---|---|
| get-event API | 0x6064c8c(sp, **16**) | 0x6064c8c(sp+24, **8**) |
| pend 的队列 | 'app_core' 任务 taskq（post 0x6000cba→0x6064978 名字路由，BZ） | 'jlstream' 任务 taskq（post 0x6074192/0x6075414→0x6064d72 名字路由） |
| 成功码 | 13（同） | 13（同） |
| 分发 | bit20 检查→0x10000c 特路→两张 12B 表 60 条 walk | cmd=r0-4 越界检查→tbb 9 分支 |
| 循环退出 | 永不退出（BR） | 永不退出（本路） |
| 后处理 | —（回 pend） | 0x6075ce0 流状态机 + stream 对象 walker |

**同一 OS 队列机制的两条实例**：队列=任务的 taskq（创建时经 0x6065020→0x6064280
以名字注册；0x60647fa 按名字查重/查找——BZ 已钉环 slot 头字 (字数+1)|(id<<8)、
游标/容量 +8/+12/+14）。app→框架域的 98 条直接调用边（BX §1.2）是控制面同步
调用；消息面全部经命名 post 路由。

## 4. stream create 请求的 post 与消费全链 [静态+本次复跑 trace]

```
tone 引擎（0x6074ec6 族，app 侧）→ 0x60753e8（stream 请求入口）:
  0x60753f6: call 0x60744b4                    ; stream 槽分配（★CF 停点函数）
      h[0x1264B4C+4]==0（首次）:
        0x6064162(0x1264B4C+12)                ; q_init 槽位数组（=CF 的 OS_QUEUE_A）
        ROM 0x12e16e(92)                       ; zalloc stream 对象
        0x6064f18(obj+52, 0)                   ; 对象内嵌队列初始化（=CF 的 OS_QUEUE_B）
      0x60744ea: call 0x12b55c                 ; ★pend/调度复合点→内部 0x12b56c
      0x60744f0..0x6074520: 槽位图计数/置位, call 0x6074090
      0x6074524: call 0x12b5b0                 ; 配对出
  0x60753fe..0x607540c: b[obj+18]=1, b[obj+19]=5, b[obj+20]=1（状态置位）
  0x607540c..0x6075414: r0=0x609D3BB('jlstream'), r1=2
      call 0x6064d72('jlstream', 2, {r2=obj,…})  ; ★请求消息入队（CF 停点未达）
框架任务 0x6086980（存在时）: get-event 收 {cmd,obj} → cmd4 分支 → 0x6075a08
  → 流状态机（0x6075ce0/walker）→ 0x6074a72 → 0x60746e6 节点创建
  → decoder bind 0x6076b7a → open-config 0x60787ac（EQ 钩子 0x6078846）
  → cmd-11 参数消息（0x607538a 打包 post / 0x6075e22 消费）→ 0x6078a2e node+35
```

0x6074192（BX 记的框架 post）是**另一条 post 路径**（0x60741a4 内 q_find/create
0x6064f18 + 0x60741bc call 0x6064d72('jlstream',3,{cmd,a1,a2})，Q_FULL=21 重试
≤10——BX 原记录成立）；它服务于 0x6074200/0x6074228/0x60753d2/0x60754da/
0x60755a4 五个调用点（框架内部事件），stream-create 请求本身走 0x6075414。

## 5. CF 停点精化与既有记录修正

本次本地复跑 `--tone-scene`（report 落本地 tmp，零基线改动；exit 0 全断言过）
确认：coverage 含 0x60744c2/0x60744ca/0x60744e4/**0x60744ea**（stream 槽分配
函数内全部锚点），stop=0x12b56c `st.w.idxs`（0xffe1a8 未映射），761 步，
d2a_pc_reached 全 false。由此：

1. **CF 停点的函数语境**＝0x6074938(stream create)→0x6074962→**0x60744b4 内
   0x60744ea 的 pend**（os_taskq_pend/调度复合点 0x12b55c→内部 0x12b56c IPMASK
   操作）。CF 条目「注入的事件已 post 进 OS 队列（0x6064162/0x6064f18 真实执行）」
   的准确表述是：**队列对象准备（q_init/q_create）真实执行；stream-create 请求
   的 post（0x6075414→0x6064d72）在停点之后未执行**。CF 断言集本身无误
   （它只断言 OS_QUEUE_A/B 与 PEND 到达，未断言 post 完成；d2a_pc_reached
   [framework_post]=false 的记录与 post 未达一致）。
2. **0x12b56c 的角色**：调度复合点内部的 IPMASK 访问（BE 记录「idle loop
   0x12b56c …IPMASK addressing 0xFFE168+cnum*0x800」），在单任务 harness 里
   无可切目标、空转等待，首访 0xffe1a8 未映射停。probe_irq_entry 已有该域
   模型（BR：IPMASK addressing 已按真实形状建模）——**CF 场景（probe_pi32v2）
   未接该域**，属跨 probe 接线项（§6）。
3. **BX §1.3 修正**：「0x6086980 = 第二个消息循环任务…按 audio_event_manager.c
   对位归类为音频事件管理任务 [推断]」——**0x6086980 就是 'jlstream' 框架主
   任务体**（0x60759fe 创建名字='jlstream'、任务体自持队列名 r9=0x609d3bb），
   [推断] 升级为 [静态]。
4. **BX §5.1 闭合**：「框架任务创建点（init walker vs 惰性）」＝**init walker
   （主任务非惰性；jlstream_N 子任务与 dac 任务惰性）**。
5. **BX §4 D2-D 断言补充**：「'jlstream' 队列 ≥1 条消息被框架任务真实消费」的
   观测点前移：**首个被消费的消息不是参数消息（cmd-11），而是 stream-create
   请求（0x6075414 post 的 2 字载荷）**——D2-B 场景验收应先见 cmd4 分支执行
   （0x60869d8），再见 cmd-11。

## 6. D2-B 场景所需 fixture 清单（接线供料）

CF 场景（probe_pi32v2 `--tone-scene` 场景 2）要跨过 pend 边界让框架任务消费，
需以下五件（按依赖序；全部有先例）：

| # | 缺什么 | 为什么 | 先例/现成件 |
|---|---|---|---|
| 1 | **jlstream 任务存在**（TCB+帧+栈+名字注册） | 消费者本体；当前 harness 零任务上下文 | 二选一：(a) **模拟器直调真实创建链** 0x60759ee 或 0x6067242('jlstream')——CD 活机器直调 0x6074938 / BW 直调 handler 入口先例，opt-in 显式假设；(b) 前置 app_core init 前缀（成本高：需主场景推进到任务体，依赖 RES/p3_ana 边界，不在 D2-B ROI 内） |
| 2 | **任务切换/调度**（0x12b55c pend 时切到 jlstream，消费后切回） | post 之后请求方 pend、消费方就绪需往返 | probe_irq_entry 的调度器链（真实 0x12ADEC call、软件 IRQ127 交付、RTI 进 app_core 0x6012028 全链已验证——BR「Scheduler loop frontier」）；dualcore 的任务帧恢复（12B 特殊帧+UnknownWord GPR）；pmu_task 多任务往返（HANDOFF 路X：CPU0 两次 irq127 交付 RTI 进 pmu_task 执行时间片后切回） |
| 3 | **IPMASK 域 0xffe1a8/0xffe168+cnum*0x800** | 0x12b56c 首访即停的 direct 原因 | probe_irq_entry 已按真实形状建模（BR）；跨 probe 复用或最小移植（本路不改代码，落地归 D2-B 实施路） |
| 4 | **OS 队列消费侧最小语义**（环 slot 出队、buf 解包 0x10000C03 形） | get-event 返回 13 的载荷 | BZ 已钉全部静态结构（0x60649b0 解包、slot 头字、游标 +8/+12/+14）；0x6086980 的 get-event 与 S19 同 API |
| 5 | **stream 槽分配后续链的输入**（stream.bin 图节、堆） | 0x60744f0 之后的槽位登记与 0x6074a72 节点创建需要 pipeline 对象 | CF 已备：stream.bin 公开字节映射+read thunk；zalloc stub 先例（CA stub 表 16 条，帧池位图 0x6065e80 内部走 RAM 池需可写窗） |

优先级建议（ROI）：#1 直调创建（一次性、显式 synthetic-not-measured）+ #3
IPMASK 域 + #4 出队语义 = 最小可行；#2 任务切换若成本高可先「单任务交替」
（消费段直调 0x6086980 入口+合成任务帧，模拟调度一次往返——dualcore 任务帧
先例），诚实标注 synthetic。#5 大部分已备。

**验收断言建议（D2-B 场景首关）**：①0x6065f9e 执行时 r0=0x6086980（任务帧写入
实测）；②0x6075414 post 执行且 0x6064d72 返回 0；③0x60869ac（get-event）返回 13
且 buf 首字=cmd4 载荷；④cmd4 分支 0x60869d8 执行；⑤0x60746e6/0x607471e 节点
创建 ≥1（BX 的 D2-A 六 PC 首次转 true 的位置）；⑥（音乐场景后）cmd-11→0x6075e22
→0x6078a2e node+35 写入与 0x1265EA 状态字节往返（D2-D 会师）。

## 7. 证据锚点

- 创建栈五函数/38 条注册表/initcall 表 0x60c3250..0x60c327c/三个框架创建点/
  tbb 9 分支表/cmd-11 消费链/0x60753e8 请求链：本路一次性脚本反汇编
  （codec pi32v2-isa @3608ed1；19,587 条线性调用边+全镜像 LE 指针扫描；
  脚本本地 tmp 不进库）。
- 既有报告：analysis/local-checks/irq-entry.json（p11_heap @601,880 milestones
  的 4 次任务创建 watch 记录）；本次 tone 场景本地复跑（tmp 报告，非基线）。
- 历史钉子：docs/d2-audio-first-scene.md（BX 框架结构/§5.1 不确定处）、
  docs/d1-s18-consumption.md（BZ post/get-event/入队表示）、
  docs/d1-acceptance.md（BR S19 循环/分发表/调度器链）、HANDOFF 路 CF（场景 v2
  断言集）、路 BE（0x12b56c IPMASK 记录）、路 X（pmu_task 多任务往返）。

## 8. 不确定处（如实标注）

1. **12B 表 h8/qlimit 字段**的消费点未逐层跟（[推断] qlimit/时间片参数类）；
   b5=1 组（a2dp_dec 等 5 条）的语义未钉（疑「不随 init 建组/按需组」）。
2. **0x12b55c 的完整 ROM 语义**（pend/临界/调度复合的分支条件）未逐指令解码
   （ROM 域已有 BE/BF/probe_task_returns 钉子，本路未重解；引用其结论）。
   0x12b56c 在 CF 场景显示为 st.w.idxs（写 RMW 读半），CF 条目「首读」表述
   按错误信息应理解为「首次访问（RMW 读半）」。
3. **0x6086980 各 cmd 分支的完整语义**（cmd5/8/9/10/12 的对象字段效果）只钉了
   主调用与状态位，未逐字段展开；D2-B 场景按执行记录如实收口。
4. **jlstream_N 子任务链表头**（旧 r6+68）的运行时指向是 pipeline 对象内字段，
   静态只能到「stream create 内惰性 ensure」，实例序号（jlstream_0..7 分配次序）
   是运行时观测项。
5. 主前沿 601,880 的停点语境（ADC 轮询后 sys_timer 回调）依 AGENTS/HANDOFF 记录
   推断 app_core 未达 init walker——若主场景后续接线改变停点序列，该结论按新
   trace 复核。
