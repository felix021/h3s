# SIMBRIDGE M1 前置预研：P2/P2b/P3（路 SIMPRE / sim-simpre，只读预研）

2026-11-17 00:00-01:30。基线 worktree `8417522`（分支 sim-simpre）。本页补齐
docs/sim-bridge-design.md §5.0 剩余三项前置（回执面 P1 已由路 RCBACK 钉死，
docs/rcsp-return-path.md）：

- **P2**：0x22 返回值与 setter 响应基址 ABI（「resp/resp+2 矛盾」+ 0x22 返回值 [未钉]）；
- **P2b**：停点世界直调可跑性（0x600f85e 与 setter 直调的接口/墙）；
- **P3**：0x300000 事件泵消费语义（M1-c 解冻续跑的依据）。

**纯静态只读**：零 tools/ 改动、零场景运行、probe 五文件未动。证据源=官方
app.bin（sha `18f3d573…`，load base 0x06000100）钉死 codec 线性反汇编（复用
~/tmp/btmap 的 codec/caches，scratch 在 `~/tmp/simpre/`，不进仓）。**PC/常量一律
脚本复算**——本路两次抓到自己的十六进制手算错误并纠正（r11=0x60A8C68 非
0x60A8BE8；分发器 li 100816554=0x60256AA 非 0x602568A），文中地址均以脚本为准。
全部结论 **derived-not-measured**。

证据强度：**[静态·本路]**=本路反汇编钉死；**[文档]**=既有 docs/HANDOFF 条目
（本路复算一致或勘误）；**[推断]**=链路推出+判据随注；**[未钉]**=如实标注。

## 0. 一句话结论（三项）

1. **P2**：表 A 全部 13 个 setter **恒返 0=写静默**（含 0x22：成功与长度校验失败
   同返 0，无任何确认回执）；setter 收到的 r0=响应缓冲**基址**，但 0x22/0x17/0x1d
   从不写它——resp/resp+2 之争是**死代码之争**，分发器代码钉死「载荷应在 resp+2、
   cmdid|n 两字节头由分发器在 setter 返回后补写」[静态·本路]。
2. **P2b**：S 路 `key_chain_direct_call` 先例可直接套用到 **0x600f85e 读路径**
   （FE 01 cmdid→getter→包装，全程无 pend，冻结世界可跑）与 **T1 setter 直调**
   （0x22 setter 内联 apply 全同步；0x17 setter 四门全纯读+fire-and-forget post）；
   **写路径直调 0x600f85e 本体不可行**——app_core 存在时它 post 0x300000 后
   **pend 信号量**，冻结世界必 Blocked [静态·本路]。
3. **P3**：**0x300000 的消费者不在 0x60123C0 泵里，而在 get_event（0x6064c8c）
   内部的 0x6064c0e**——回调事件在 pend 返回路径上**内联执行**（w0=回调、
   w1=flags{n,retslot,sem}），泵的两张静态表里根本没有 0x300000 记录；消费时机=
   目标任务下一次跑 get_event，冻结世界只入队不消费 [静态·本路]。**对 M1 的重大
   简化：EQ 0x22 的 apply 在 setter 内联同步执行，M1-b 即得完整写效果，不需要
   M1-c；M1-c 只为 ANC 0x17 族（apply 走 post）与经 0x600f85e 的全链**。

## 1. P2：setter 返回值与响应基址 ABI

### 1.1 分发器 0x600f85e 完整 ABI（复钉+扩展 RCBACK）[静态·本路]

入口：`r0=SN、r1=body 指针、r2=忽略、r3=len`（body[0]=cmdid 写形态 /
body=`FE 01 cmdid` 读形态）。帧内 `sp += -192`，`[sp+172]` 初始化 -1（outlen 槽）。

**读路径**（body[0]==0xFE 且 body[1]==1，0x600f87a-0x600f8b2）：
表 A 匹配 body[2] → `getter(r0=resp+2=sp+46, r1=126)` → `goto 0x600f940` 进公共
包装。0x22 getter `0x602580a` 实测形状：N=b[0x1265C0+5499]，需 N*7+3≤126 否则
返 -1（静默）；从镜像 b[0x1265C0+5356] 拷 **N*7+3 字节（含 3B 头）** 到调用方
缓冲，返回同长 [静态·本路]。

**写路径**（0x600f8b4-0x600f968）——本路新钉的**双路径结构**：

```
0x600f8b4  r0 = [r7+8]（setter）；空 → 静默跳尾
0x600f8b8  call 0x12b1a2（RAM-code OS 辅助，字节不在静态镜像）
0x600f8c6  call 0x1fd84(r0, r1='app_core')（ROM 0x1FC000 窗口）
0x600f8cc  if (r0 == 0) goto 0x600f932          ← app_core 不存在 → 同步路径
   异步路径（app_core 存在，常态）：
0x600f8ce-0x600f8fa  组 9 字参数块 @sp+8：
   w0=0x60256AA（四参 trampoline）、w1=flags 0x305、w2=setter、
   w3=resp(sp+44)、w4=128、w5=body+2、w6=body[1]、w7=&outlen(sp+172)、w8=&sem(sp+176)
   （0x600f8fe 调 0x6064f18 在 sp+176 原地初始化计数信号量，count=0）
0x600f90e  post('app_core', 0x300000, 9, sp+8)
0x600f916  pend(sp+176, 0)；0x6065174 destroy；r0=[sp+172]   ← 同步 RPC：等 app_core 执行完
           （post 失败 → 跳过 pend，outlen 保持 -1 → 静默返回）
   同步路径 0x600f932：
0x600f932-0x600f93e  setter(r0=resp=sp+44, r1=128, r2=body+2, r3=body[1]) → r0=n
公共包装（两路径汇合，0x600f940-0x600f968）：
0x600f942  ifs (n < 1) → 静默跳尾
0x600f946-0x600f94e  b[resp+0]=cmdid（表 A +24 字段）、b[resp+1]=n；总长 n+2
0x600f968  call 0x600b66e(0xFF, 0(status), SN, resp, [sp]=n+2, [sp+4]=原帧 len)
```

- 0x60256AA trampoline 体（0x60256aa-0x60256bc）：`r5=r0(fn)；r3=[r2+12]；
  r4=[r2+8]；r1=[r2+4]；r2=[r2+0]；call r5`——即 `fn(w'0,w'1,w'2,w'3])`，与
  消费者（§3）的调用形状严丝合缝：setter(resp,128,data,len) 在 app_core 上下文
  原样重放 [静态·本路]。
- `0x12b1a2/0x1fd84` 字节不在 app.bin（RAM-code/ROM）[推断]：按名查任务句柄、
  查不到返 0——判据=0x600f97c（"在 app_core 里跑 X"辅助函数）同构使用该对：
  查不到 → 直接调用兜底；查到 → post+失败重试（sleep 5）。

### 1.2 表 A 全 setter 返回值扫描 [静态·本路]

表 A `[0x060a9b78,0x060a9d1c)` 15 项；官方镜像有 setter 的 13 个（0x2f/0x30 槽
setter=0，与设计文档一致）。逐个反汇编尾部：

| cmdid | setter | 返回值 | 响应缓冲 r0 | 内部动作（同步部分） | 异步 |
|---|---|---|---|---|---|
| 0x22 EQ | 0x602583c | **恒 0**（len%7!=3 也返 0） | 不写 | 段数→b[0x1265C0+5499]；memcpy 镜像 b[0x1265C0+5356]；key34 落空桩；**内联 call apply 0x6025118** | 无 post |
| 0x14 | 0x6025a2a | 恒 0 | 不写 | 模式检查+0x6011fd8+sys_event(0x6066a0a) | sys_event |
| 0x1f | 0x6025b98 | 恒 0 | 不写 | key31 保存(0x6025642)+0x6001f12 | 无 |
| 0x2d | 0x6025c26 | 恒 0 | 不写 | data[0]→0x602262e | 无 |
| 0x17 ANC | 0x6025ca4 | **恒 0**（四门任一失败也返 0） | 不写 | 四门（§2.4）→直存 b[0x12666B..D]→**post {0x6025D6A,1,0}**→本地归一化 | apply 回调 post |
| 0x1d 提示音 | 0x6025e82 | 恒 0 | 不写 | len≥2→b[0x1249FE]=data[0]→VM key17 写(0x6001f12，源=0x1249FE)→h[0x127716]=data[0]（=h[0x1265C0+2219*2]，脚本复算；rcsp-return-path §3 记的 h[0x124f3e]/h[0x127696] 与本路逐指令值差 0x540/0x80——疑同族滑差，回报主协调核对） | 无 |
| 0x19 | 0x6025f00 | 恒 0 | 不写 | 查表+key25 保存 | 无 |
| 0x23 | 0x6025fa0 | 恒 0 | 不写 | data[0]→0x6022670 | 无 |
| 0x01 | 0x6026090 | 恒 0 | 不写 | call 0x600f856 | 无 |
| 0x03 | 0x602609a | 恒 0 | 不写 | 594→0x6023326 | 无 |
| 0x2e | 0x6026134 | 恒 0 | 不写 | 定时器族(0x606683a) | 定时器 |
| 0x10 | 0x6026234 | 恒 0 | 不写 | key7/key8 写(0x6001f12)+0x60225fc | 无 |
| 0x24 | 0x6026268 | 恒 0 | 不写 | 同 0x10 | 无 |

**结论**：写命令的设备侧回执**不存在**——成功/失败同返 0，无长度确认；「写生效
确认」只能靠读回（FE 01 cmdid）或异步通知（0x2E 族）。这把 qcy-command17 的
「0x17 恒返 0」推广到全部 setter [静态·本路]。

### 1.3 响应基址 ABI：resp/resp+2 之争的裁决 [静态·本路]

- 分发器给 setter 传 **r0=resp 基址**（sp+44）、r1=128；给 getter 传 **r0=resp+2**
  （sp+46）、r1=126。
- 分发器在 setter 返回 n≥1 后**自己补写** `resp[0]=cmdid、resp[1]=n`
  （0x600f946-0x600f94e）。
- ∴ 若某 setter 想回执：**载荷应写在 resp+2 起、返回载荷长度 n**——
  r4-command-hook §2「resp 写在 +2」正确；tools/r4cmd/keystore_hook.S:65-78
  写 `[r4+0]` 且注释自称 resp[2] 系错误（会被 cmdid|n 头覆写）。
- **但该 ABI 是死代码**：13 个 setter 全部返 0 且不写缓冲（§1.2），官方镜像里
  setter 路径的响应包装从未触发。ABI 只对 getter 生效（getter 拿到的就是
  resp+2，返回 n）。
- 0x602568A trampoline 参数块（异步路径）同样以 resp 基址重放 setter ABI，
  返回值经 *w7（&outlen）回传、信号量唤醒 poster——与同步路径语义等价。

### 1.4 0x600b66e 栈参勘误（对 RCBACK 的小修正）[静态·本路]

`call 0x600b66e` 前的 `r2 = r4 #`（**并行传送**，见 §1.5）使 `[sp] = r2` 存的是
**旧 r2 = n+2（响应总长）**，不是 SN（SN 在 r2 新值=寄存器参里）。即 ABI：
`0x600b66e(r0=0xFF, r1=status, r2=SN, r3=resp, [sp]=n+2, [sp+4]=原帧 len)`。
R1 拦截（入口 hook）只需 r0-r3 即可重组 app 可消费的响应 body，[sp] 白送总长。

### 1.5 方法论警示：`#` 并行传送是静态分析陷阱 [静态·本路]

pi32v2 反汇编文本中形如 `rX = rY  #` 的指令与其**下一条**并行执行——下一条读到的
是 **rX 旧值**。本页多处结论依赖它（泵 walk 的 `call [record+8]`、分发器 w0=
trampoline、[sp]=n+2、0x6025652 尾部 [sp]=0），并用泵 walk 与 BW/AJ 仿真实证
互锁（键 handler 0x60252c6 确实在跑 → 旧值语义成立）。**后续读反汇编必须先看
`#`**——RCBACK 的「[sp]=SN」与本路初期两次误读均栽在此。

## 2. P2b：停点世界直调可跑性

### 2.1 S 路先例解剖（tools/probe_irq_entry.py:842-876，只读引用）[文档]

```python
KEY_CHAIN_DIRECT_SENTINEL = 0x00100000      # ROM/flash 之间未映射空洞，从不取指

def key_chain_direct_call(cpu, machine, pc, args, *, max_steps=200_000, watch=()):
    saved = {'pc','sp','rets','flags','R[0..15]'}       # 全量保存
    cpu.pc = pc
    for i, value in enumerate(args): cpu.R[i] = value    # r0..r3 = args
    cpu.rets = KEY_CHAIN_DIRECT_SENTINEL                 # 出口哨兵
    while cpu.pc != SENTINEL:                            # 受控步进：
        watch 命中记录（pc + r0-r3）                      #   decode→step→
        insn,raw,size = cpu.decode(cpu.pc)               #   machine.after_step
        cpu.step(); machine.after_step(cpu, before)      #   【不调 hardware.advance】
        超预算 → Blocked('..._budget')
    result = cpu.R[0]                                    # 返回值=哨兵处 r0
    finally: 恢复 pc/sp/rets/flags/R                     # 世界存储副作用保留
```

配套 API（同文件 make_key_chain_post_stop 内）：`cpu.mem.read(addr,size)` /
`cpu.mem.write(addr,data)`（fixture 安装与观测全用它）；外设时间用
`write_ram(SYS_TICK, tick+1)` 手工推进（不 advance 的替代面）；post 观测=
watch 调用点 PC（记 r0-r3）。post_stop 契约：`post_stop(machine,hardware,
stopped_cpu) -> dict|None`，explore 在停点后、报告组装前调用
（probe_boot.py:98-116 docstring、:2076-2078 调用点）。

### 2.2 直调驱动器接口清单（实施路照抄）[设计·基于先例]

1. `direct_call(cpu, machine, pc, args, *, max_steps=1_000_000, watch=())`——
   逐行复刻 key_chain_direct_call + 设计文档 §4.1 的三条纪律（①成功返回先校验
   SP 与入口平衡、栈未越界再恢复；②限定 CPU0、已知 SP、非 IRQ 上下文、无在途
   repeat/after_then；③Blocked/预算耗尽→世界标记不可继续，finally 只恢复寄存器）。
2. **栈参扩展**（S 先例没有）：若直调带 `[sp]` 参的函数（如 0x600b66e），需先
   `cpu.sp -= 8; cpu.mem.write(cpu.sp, pack)` 预压栈字。**0x600f85e 与全部
   setter/getter 都只需 r0-r3，M1 不需要此扩展**。
3. **body/scratch 构造**：`cpu.mem.write(scratch, body_bytes)`；`body` 写形态=
   `cmdid | data_len | data...`、读形态= `FE 01 cmdid`；调
   `direct_call(cpu, machine, 0x600f85e, [SN, scratch, 0, len])`。SN 是普通字节值
   （r0 直传），无需构造指针。scratch 选址与守卫=设计文档 §4.1（P3 项，实施路）。
4. **watch 面**：watch PC 集合见 §3.4；`watch` 记录 r0-r3，拦 0x600b66e/
   0x6009f6a 时 r0-r3 恰好携带全部重组信息（§1.4、RCBACK §2.2）。

### 2.3 0x600f85e 直调可跑性判定 [静态·本路]

| 路径 | 冻结世界可跑性 | 依据 |
|---|---|---|
| **读**（FE 01 cmdid） | **可跑** | 表 walk→getter→包装→0x600b66e 全程无 pend；BT 池门（[0x1265C0+1004] 非零+b[pool+24]）失败仅返 4 丢帧——R1 拦截在入口，先于门；池在位则 0x6009db0 组帧入队+0x606474a 信号量 post（非阻塞）。预算：getter 链几十条指令，200k 步绰绰有余 |
| **写**（cmdid|len|data） | **不可行（常态世界）** | app_core 存在 → 异步路径：post 9 字参数块后 **pend(sp+176, 0)**（count=0 计数信号量，等 app_core 消费）——冻结世界 app_core 永不调度 → 预算耗尽 Blocked，且事件已留在 app_core 队列里（世界半写） |
| 写（app_core 未创建的超早期停点） | 理论可跑（同步路径 0x600f932） | `0x1fd84 查名返 0 → 内联 setter+包装`；但超早期世界缺 rcsp/config 状态，M1 不用 |
| **T1 setter 直调**（推荐） | **可跑** | 0x22：len 校验+镜像 memcpy+内联 apply，全同步（§2.4）；0x17：四门纯读+直存+fire-and-forget post（0x6064978 失败也不重试不检查）；0x1d：VM 写+镜像。setter 全族无 pend |

**差距如实标注**：S 先例证明的是「停点世界直调无 OS 依赖的函数」可跑；
0x600f85e 写路径引入了**任务间同步依赖**（post+pend），这超出了先例的覆盖面——
不是驱动器能力问题，是被调函数语义问题。T1 绕开它 [静态+推断]。

### 2.4 setter 直调的墙清单（P2 试跑项的静态预判）[静态·本路]

- **0x17 四道门全部纯 RAM 读**（修正「可能撞外设」的担忧）：gate1 0x6008170→
  0x6003578（连接查询）；gate2 0x6000c6c=[0x1265C0+2444] 模块记录判 id==5；
  gate3 0x60065bc=`return b[0x1265C0+5]` 一条指令；gate4 0x6018994 零调用。
- **0x22 apply 链**（0x6025118→0x602503a→0x607538a…）在静态窗口内**零 MMIO
  立即数**（0xF00000-0x1400000/0x1F0000-0x200000 扫描为空）；JL_EQ 寄存器
  （0xfe1500 族）若被触达应在更深的节点运行时（堆 ctx/函数指针表）——JLEQ 路
  在钉域，撞墙风险如实留给 P2 试跑 [未钉·动态]。
- **apply 的递归**：0x60252a8 处 apply 自递归（type==1 先装预设 0 分支），深栈
  预算要留余量。
- **0x17 的 post 在队列对象不存在时**：0x6064978→0x606487c 查名失败返错，
  0x17 setter 不检查返回值 → 静默丢，不 Blocked [静态]。

## 3. P3：0x300000 事件泵消费语义

### 3.1 消费者身份 [静态·本路]

**0x300000 不是 0x60123C0 泵的表事件，而是 get_event 内联执行的回调消息**：

```
app_core 泵 0x60123C0:  get_event(sp,16)=0x6064c8c
  └ 0x6064c8c 出队（0x6064b98 拷到调用方块）后调 0x6064c0e(size, block)：
      r1 = block[0] & 0xF00000
      if (r1 != 0x300000) → 返 0（普通事件，交还泵走 0x10000C 模块分派/表 walk）
      ；回调事件（本页主题）：
      n = flags.b0；总字数 = n+1+2(+1 若 bit9)（越界保护 vs size）
      n==1 → cb(block.w2)                       （0x17 setter 的 post {0x6025D6A,1,0} 走这）
      n==2 → cb(block.w2, block.w3)（64 位对传）
      其它  → cb(block.w2, n-1, &block.w3)       （分发器 RPC：trampoline(setter,4,&args4)）
      bit8(0x100)：*w(2+n) = cb 返回值；标记"已消费"（事件不再交还泵）
      bit9(0x200)：0x6064a06(sem_ptr) 唤醒等待者
      → 返 1 → get_event 循环取下一条（不返回给泵）
```

- 事件字 ABI（poster 0x606487c 打包 `(event<<8)|(nwords+1)`+参数字，入任务环形
  队列 [queue+16] 缓冲、头 [queue+8]、容量 [queue+12]、计数 [queue+14]）：
  **w0=回调指针、w1=flags{b0=参数字数 n（自 w2 计）、0x100=回写返回值、
  0x200=信号量唤醒}、w2..w(1+n)=参数、w(2+n)=返回值槽指针、(bit9 时)
  w(3+n)=信号量指针**。
- 泵的两张静态表**没有 0x300000 记录**（walk1=0x60A8C68..0x60A8CB0 六条、
  walk2=0x60A89E0..0x60A8C68 五十四条，全为 0x1000xx）——即使 bit8 缺失的
  0x300000 事件漏到泵，也只会在 walk 无匹配后被静默丢弃（回调已在 get_event
  里跑完，无害）。
- **勘误线索（回报主协调）**：bt-stack-map §8.1 锚点行记「表1 walk
  0x60A8BE8..0x60A8C30/第二 walk 0x60A89D8..0x60A8BE8」——本路脚本复算 r11=
  0x60A8C68、r13=0x60A8CB0、r10=0x60A89E0、r7=0x60A8C68，与 rcsp-return-path
  §1.7 的「表 2（0x60a8c68，6×12B）」一致；疑 BTMAP 转写滑了 0x80。语义结论
  （12B 记录 {module,event,handler}、模块过滤 walk）不受影响。

### 3.2 消费时机 [静态+推断]

- 消费发生在**目标任务下一次执行 get_event** 时——即需要调度器跑该任务。真机：
  poster 的 wake 路径（任务+49 bit4 → 就绪）+ 调度随即/稍后切过去。
- **冻结/停点世界：只入队，不消费**。post 后可观察：app_core 队列 count+1、
  缓冲里出现 `(0x300000<<8)|(n+1)` 头字+参数字（M1-b ③ 断言面，纯 RAM 读）。
- 解冻续跑（resume_steps）后：停点上下文（通常就停在 app_core 泵的 get_event
  pend 里）继续步进，pend 观察到事件即出队 → 0x6064c0e 内联执行回调 → apply
  链跑 [推断]——判据：pend 的唤醒只依赖 RAM（count/就绪位已由 poster 写）或
  tick（hardware.advance 恢复后自然到来）；**「外设时间解冻后停点是否稳定推进」
  仍是 P2b 实验项**（本路零场景运行，未验）[未钉·动态]。

### 3.3 消费后的可观察效果（apply 链落点）[静态·本路+文档]

| post 源 | 回调 | 执行体 | 落点 |
|---|---|---|---|
| 0x17 setter（T1 直调后） | 0x6025D6A | 读 b[0x12666B..D] → 0x6012be2(m,s,st) + 0x600dcee(m,s,st,1) | ANC ctx [0x1265C0+500]+33、VM key16（AO 域既有钉死） |
| 分发器写路径（0x600f85e） | 0x60256AA | setter(resp,128,data,len) 原样重放 + *w7 返回值 + sem 唤醒 | 同步 RPC 闭合，poster 侧继续走响应包装（n<1 静默） |
| 0x22 setter | **无 post** | apply 0x6025118 在 setter 内联 | EQ 镜像 b[0x1265C0+5356]、段数 +5499、EQ 引擎（0x602503a→0x607538a 节点运行时） |

**对 M1 分级验收的直接修正**：设计文档 §5.1 M1-b 对 EQ 写的预期「apply 0x6025118
watch PC 到达≠完成、全环要 M1-c」——**过虑**。0x22 的 apply 在 setter 直调段内
同步完成，M1-b 的 EQ 写=「直存+apply 全效果」（apply 返回后断言写集即可）；
M1-c 只服务 0x17 族的异步 apply 与（若走）0x600f85e 写路径的 RPC 闭合。

### 3.4 watch PC 集合（M1 验收/调试用）[静态·本路]

`0x6064c0e`（任意回调事件消费入口——解冻后首个铁证）、`0x60256aa`（分发器 RPC
trampoline）、`0x6025D6A`→`0x6012be2`/`0x600dcee`（ANC apply 链）、
`0x6025118`/`0x602503a`（EQ apply，0x22 直调段内）、`0x600b66e`/`0x6009f6a`
（回执/通知出口，R1/R2 拦截点）。

### 3.5 未钉小项（如实）

1. **信号量槽位的 ±1 歧义**：消费者尾部的返回值槽与信号量槽静态解码均指向
   `[block+12+n*4]`（r6=r7，字节级复核过 0x6064c76=`76 16`），而分发器的
   marshalling 把 &outlen 放 w7、&sem 放 w8（差一字）——两者必有一处的语义
   依赖本路未捕获的细节（如 0x6064a06 的 ABI 差一位）。**不影响本页三项结论**
   （bridge 的 M1 不依赖分发器内部信号量：T1 绕开；M1-c 由真固件自己解），留作
   RAM-code OS（0x12b1a2/0x1fd84 同族）专项的引子 [未钉]。
2. 0x12b1a2/0x1fd84（查名）字节不在静态镜像，语义靠 0x600f97c 结构互证 [推断]。
3. 消费时机/续跑稳定性 = P2b 实验（§3.2）[未钉·动态]。

## 4. 三态汇总

| 结论 | 状态 |
|---|---|
| 全部 13 个 setter 恒返 0=写静默；0x22 无确认回执 | **钉死** [静态·本路] |
| 响应基址 ABI：setter 拿基址、载荷应放 +2、cmdid|n 头由分发器补写；现为死代码 | **钉死** [静态·本路] |
| 分发器写路径=post+pend 的同步 RPC；读路径全程无 pend | **钉死** [静态·本路]（查名环节 [推断]） |
| 0x300000 消费者=get_event 内 0x6064c0e，内联回调执行，泵表无此事件 | **钉死** [静态·本路] |
| EQ 0x22 apply 在 setter 内联（M1-b 即全效果）；0x17 apply 走 post（需 M1-c） | **钉死** [静态·本路] |
| 0x17 四门纯读；setter 直调无 pend 墙 | **钉死** [静态·本路] |
| EQ apply 深层是否触 JL_EQ MMIO（0xfe1500 族） | **未钉**（JLEQ 在途；静态窗口零 MMIO 立即数） |
| 解冻续跑停点稳定性、直调实测耗时 | **未钉**（P2 实验，本路零运行） |
| sem 槽位 ±1、0x12b1a2/0x1fd84 字节级语义 | **未钉**（不影响 M1 路线） |
| BTMAP 表1/表2 地址差 0x80；rcsp §3 提示音音量两地址同族滑差 | **勘误线索**（回报主协调核对） |
| RCBACK「0x600b66e [sp]=SN」→ 实为 [sp]=n+2（并行传送） | **勘误**（本路 §1.4） |

## 5. 对 M1 实施的影响（增量结论）

1. **T1 setter 直调是写路径唯一可行形态**（0x600f85e 写直调在常态世界必
   Blocked）；读路径可自由选 0x600f85e 直调（固件真实表 walk+包装）或 getter
   直调。server 查表 A 逻辑维持设计文档原案。
2. **M1-b 的 EQ 验收上调**：直调 0x602583c 返回后即可断言镜像+段数+（apply
   无 Blocked 时）EQ 引擎写集——不需要等 M1-c。M1-c 范围收窄为 ANC 0x17 族。
3. **app 侧写命令超时语义**：真机对所有自定义写命令恒静默——app 的 5s 帧超时
   在写命令上「真机也会超时」，server 不合成回执即最高保真（设计文档 R1 维持，
   依据从 0x17/0x1d 两个样本扩大到全表）。
4. **M1-c 的判定面**：watch 0x6064c0e（任何回调消费）+ 0x6025D6A→apply 链 +
   world_diff；解冻用 explore 主循环同款步进（含 hardware.advance）。
5. **静态分析纪律**：读 pi32v2 反汇编先查 `#` 并行传送（§1.5）——已有两处
   既有文档结论需要按此复核（RCBACK [sp] 参已勘误；keystore_hook 偏移错误
   确认）。

## 6. 复现

```
# 环境：~/tmp/simpre/env.py（复用 ~/tmp/btmap codec/caches，只读）+ dis3.py（本路新增，带跳转目标注释）
python3 - <<'EOF'
import sys; sys.path.insert(0,'/home/felix021/tmp/simpre')
import env
env.dis2(0x600f85e, 0x600f970)   # 分发器全body（双路径+包装）
env.dis2(0x6064c0e, 0x6064c8c)   # 0x300000 消费者
env.dis2(0x6064c8c, 0x6064cd8)   # get_event 过滤调用点
env.dis2(0x60256aa, 0x60256bc)   # 四参 trampoline
env.dis2(0x602583c, 0x6025882)   # 0x22 setter（恒返0+内联apply）
env.dis2(0x6025ca4, 0x6025d32)   # 0x17 setter（四门+直存+post+归一化）
env.dis2(0x602580a, 0x602583c)   # 0x22 getter（返回 3+7N 含头）
env.dis2(0x6025e82, 0x6025eb0)   # 0x1d setter
EOF
# 常量复核（脚本，禁手算）：101354600=0x60a8c68、100816554=0x60256aa、
#   100818282=0x6025d6a、773=0x305、3145728=0x300000
# 表 dump：表A [0x060a9b78,0x060a9d1c)；walk1 [0x60A8C68,0x60A8CB0)；walk2 [0x60A89E0,0x60A8C68)
```
