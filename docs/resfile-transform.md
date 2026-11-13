# resfile 层变换语义钉死（路 CD / sim-resfile，只读调研）

2026-11-13。本页回答 CA（sim-tonescene）留下的第一缺口：**stream.bin/TON/TON1 的
VFS 运行时视图与 flash 原始字节的关系**。方法 = 活机器实测：跑既有
`crc_bankb_erased_fixture` boot 前缀到 289,849 停点（VM init 已完成、QCY 分区槽已填），
在停点状态上直接调用固件真实函数（资源 VFS open/read、stream.bin 加载器、stream
create 全链），全部真实执行、零合成输入（唯一显式假设处单独标注）。
一次性脚本在本地 tmp 不进库；ROM = 公开 Release 资产（SHA 校验）；本路未改 tools/
任何代码。

证据强度：**[运行]** = boot 前缀停点状态上的真实执行（本路活机器）；
**[静态]** = 指令锚定反汇编；**[SDK]** = 参考 SDK IR（参考材料）；**[推断]** = 上述链推出。

## 0. 三句话结论

1. **resfile 层没有字节变换**：'flash/res/stream.bin' 经真实 VFS 链 open+read，返回
   字节与公开 JLFS 内容逐字节相同 [运行]——CA 的「运行时视图≠flash 原始字节」推论
   不成立，真机不需要 JS 头。
2. **加载器的 'JS' 校验失败是非致命的** [运行]：失败只使版本字节为 0，节访问器
   0x60742a8 随之走「透传读」分支（seek+read，无 XOR、无节解密），stream create
   全链在真字节上成功前进到框架队列 pend 边界（比 CA 合成头链更远）。
3. **0x1fe30 的 -7 归因头字节** [运行]：它是加密分支（JS 头版本）里的多参内存校验
   API，-7 产生于输入校验段；CA 喂的合成头字节 2..15 全零导致校验早退。真机无 JS
   头不走该分支，0x1fe30 不被调用。

## 1. 变换机制：resfile 层读路径全图 [运行+静态]

链路（每一步都在活机器上真实执行过或反汇编锚定）：

```
0x6007ae8  资源 VFS open('flash/res/stream.bin')
  ├─ ROM strchr(0x1fd98) 取首段 → mount 表 0x060C321C（5 项指针组，有效项仅 'flash'）
  │    {name@+0='flash', open@+4=0x6060802, read@+8=0x6060894, seek@+12=0x606091c, ...}
  └─ mount open 0x6060802
       ├─ zalloc(36) 句柄 + 0x6010bc8 首段提取（大写化、≤15 字节）
       ├─ strcmp 分派（落点为模拟器单步实测，非直读）：
       │    'APP'          → 0x6010da6（app 分区）
       │    'RES'          → 0x6010ece（资源区：stream.bin/scene.bin 走这里）
       │    'EXT_RESERVED' → 0x606075c（外置保留区：TON/TON1 走这里）
       └─ RES 路径 0x6010ece → 0x6010e4e：分区目录遍历（基址 [QCY+824*4]=0x060CDD00，
            boot 期 VM init 填充）+ 0x6010a64 名字匹配 + 32B 目录条目 CRC 校验
            （0x6000f0c 硬件 CRC wrapper，头 bytes 2..31）→ 句柄 fs 私有结构
            {cur, ., base=0x060E0A4C, size=0x2487}
read 0x6060894：n=umin(size-cur, len)；ROM memcpy 0x1fd74 —— 纯字节拷贝，无变换
```

**实测判据**（boot 289,849 停点活机器，official 镜像 plain 只读映射）：

- open 5,981 步真实执行（RES 目录遍历+CRC 全部真跑），句柄 fs 私有
  base=**0x060E0A4C** = 公开 plain `content_offset 0xe0a4c` 的运行时地址、
  size=**0x2487** = 公开 `content_bytes`（analyze_image_resources 逐字段一致）。
- read(handle, buf, 32) 60 步真实执行，返回字节
  `0000a003 74767800 8a760100 851a0100 ...` 与公开 JLFS stream.bin 内容**逐字节相同**
  [运行]。
- boot 前缀停点时 QCY 分区槽实测：[+820*4]=0x2020、[+821*4]=0x06000000、
  [+822*4]=0x06000000、[+823*4]=0x060CDCA0、[+824*4]=0x060CDD00、[+830*4]=0x3328。

结论：**flash/res/ 路径上不存在解密、重排或头部合成——运行时视图恒等于公开镜像字节**
[运行]。CA §6.5 勘误第 5 条的推论（「resfile 层必提供变换视图」）据此再修正。

## 2. 公开工件可推导性

- **stream.bin**：运行时视图 = 公开 JLFS 字节恒等映射（§1 实测）[运行]。可推导性
  = 完全——不需要任何合成头或哈希。公开内容 offset 0 起就是「节头流」
  `{u16 uuid(LE), u16 count(LE)}`：`{0x0000,0x03a0}`、`{0x7674,0x0078}`、
  `{0x768a,1}`、`{0x1a85,1}`×2（decoder）、`{0x74e3,1}`×2（volume）…
  ——uuid 值与 BX §1.3 的 ops/plug 表互证 [静态]。
- **TON/TON1**：外层 UFW 条目 kind=50 SFC 解码后的容器（32B 目录+17 文件，CA 已逐条
  CRC 全过）→ EXT_RESERVED 分区（0x606075c 路径）。read 复用同一 mount read
  （0x6060894 memcpy）→ 运行时视图 = 公开容器字节 [推断（RES 同族类比）+静态]。
  EXT_RESERVED open 内部未在活机器上完整走通（停在 0x606079e 堆对象未初始化读——
  该路径期望 boot 更晚状态的堆内容，本前缀未覆盖），此处保留一处观测项，不影响
  「read 无变换」结论（变换若存在只能在 open 的指针构造里，而 RES 同族构造已实测
  为直通内容地址）。
- **SDK 对位** [SDK]：jlstream_file_open（media.a IR）的 20 字节头
  {flag u16=0x534A('JS'), hash u16, 16B 哈希区} + `_JBHash(3826, head+4..20, 16)`
  比对 + `CONFIG_STREAM_BIN_ENC_ENABLE` 开关 + zip/dec 双 1024B 缓冲——这是
  **加密版 stream.bin 的兼容分支**。QCY 公开镜像不带 JS 头，固件走非加密分支；
  SDK 的头校验失败也不致命（g_stream_protected=2 只关 bt-name-bind），与固件侧
  「JS 失败非致命」行为同构。

## 3. 0x1fe30 归因（CA 的 -7）

- 0x1fe30 = goto.l 跳板 → **0xbb70**（模拟器单步实测 [运行]）。
- 0xbb70 = 多参内存处理 API：参数自栈 sp+96..124 取 7 个字；入口即做参数范围检查
  （r12≠0 大跳 0xbdce、r15≠0 大跳 0xbde2 两个错误出口）；体内字节拷贝 rep 循环 +
  两次 ROM memcpy(0x413c) + 位运算（lsr 4 等）——哈希/解密形状 [静态]。
- **-7 产生路径实测** [运行]：CA 场景 2 复现（7,289 步、reads
  (4,4,4)/(4,4,20)/(928,928,928)、seeks 16/0 与 CA 记录一致），在 0x607438c 调用点
  拦截：参数 r0=0x1314e0（928B 节体缓冲）、r1=0x1318e0、r2=0x3a0=928、r3=0x400=1024
  （dec 缓冲容量）+ 栈参数块。单步进入 0xbb70 后 **67 步**即达错误出口：
  0xbd4a `r4=r5+r7`（数据指针+长度和）与 [sp+32] 比较（0xbd4e ble.rr）不过 →
  0xbd56..0xbd5a `r0=[sp+20]; r0+=-1; r0-=r6` → **r0=0xFFFFFFF9(-7)** 返回。
- **归因判定：头字节，非堆态** [运行+推断]。-7 出自输入校验段而非分配失败；其
  栈参数块来自节遍历器读到的 20 字节头数据（CA 合成头的字节 2..15 全零），堆缓冲
  本身充足（928<1024）。确切是头内哪个字段非法未逐位分解——加密分支真机不走
  （§4），继续深挖 ROI 为负，如实标注。

## 4. 加密分支 vs 真机分支：CA 场景 2 的路径修正

加载器 0x6074234 的真实语义（活机器实测补全 CA/BX 记录）[运行]：

```
b[0x12649D] != 0 → 直接返回 0（门关；boot 前缀后实测为 0，写者未查=观测项）
open 'flash/res/stream.bin' 失败 → free(buf) 返回 0
read 4 字节 → b[0]=='J' 且 b[1]=='S' → 版本字节 b[3] 存 buf+2048（加密分支）
                                  ↳ 失败 → 版本字节保持 0（buf+2048 头已清零）
无论 JS 成败：r5=r4（0x607427c）→ 返回 buf 非零（JS 失败非致命！）
节访问器 0x60742a8：读 buf+2048 版本字节
  ≠0 → 加密分支：seek@16、20B 头读、928B 节体、XOR 解密、0x607438c→0x1fe30（CA 停点）
  =0 → 透传分支 0x60743a8：seek(cur)+read(len) 顺序读，无 XOR、无节结构
```

**真字节（公开镜像）全链实测** [运行]：CA 场景 2 harness 去掉合成头、直接喂公开
JLFS 字节后，完整链（handler 0x6023a0a 注入 → … → stream create 0x6074938）从
CA 的 -7 清理退出改为**成功前进**：0x6074938(0x7674, 0x768a, 3) 内部
loader(7,791 步, JS 失败→透传) → 节扫描 → 0x607447a/0x60744b4 → OS 队列
0x6064162/0x6064f18 → ROM pend 0x12b55c，停在 0x12b56c idle 轮询
（IPMASK 0xffe1a8 读——框架任务语境的诚实边界，D2-B 域，非 resfile 问题）。
单点验证：活机器上直调 0x6074938 真字节 33,679 步返回非零 [运行]。

对照：合成 JS 头把真机推入了**真机不走的加密分支**——CA 的 0x1fe30/-7 停点是
合成输入的产物，不是 D2-A 的真机拦路虎。

## 5. D2-A 继续的最小合成集

**= 空**。CA 场景 2 不再需要 `synthetic_stream_head`：

1. 资源服务直接用公开 JLFS 字节（`flash/res/stream.bin` = 原始内容；TON/TON1 = 容器
   原始字节）——运行时视图恒等已实测。
2. 断言集改写：删「'JS' 校验通过 / 928B 节 0 / XOR 解密 / -7 清理」四个加密分支
   断言，改为「JS 校验真实执行且失败（读到的 4 字节=公开头）→ 版本字节 0 → 透传
   读序列（顺序 read，无 seek@16 重置、无 20B 头读）→ stream create 走到框架
   post/pend」。
3. 下一个诚实边界 = 0x12b56c idle（框架任务 pend-13 语义，D2-B 消息闭环域），
   与 D2-A 六 PC（0x607471e 等）的到达路径衔接由后续场景接线。
4. 观测项（不阻塞）：b[0x12649D] 的置位者与 tone 时点取值；EXT_RESERVED open
   （0x606075c）在 boot 更晚状态下的堆依赖；透传模式下 0x60743f6 节扫描的 uuid
   匹配细节（活机器单点直调 r0=0 返回，全链语境下由 0x6074938 内部正确驱动）。

## 6. 证据锚点

- 活机器探针：crc_bankb_erased_fixture 前缀（official UFW + 公开 ROM 资产 +
  flash plain 只读映射）→ 289,849 停点捕获 DualCoreMachine → 直调
  0x6007ae8/0x6007b7c/0x6074234/0x60743f6/0x6074938 并单步记录（tmp 脚本
  probe_fs_live4/probe_loader2/3/probe_chain3/probe_query7674/trace_1fe30/
  trace_full）。
- 静态：mount 表 0x060C321C/分区名串 0x60A1966 族/0x6010bc8/0x6010e4e/0x6060894/
  0xbb70 体/0xbd4a..0xbd5a 错误出口（pi32v2-isa @3608ed1，主仓 reference-cache）。
- SDK：~/tmp 参考缓存 jlstream_file_parse.ll（jlstream_file_open 20B 头/
  _JBHash/ENC_ENABLE/双 1024 缓冲；g_stream_protected 非致命先例）。
- 既有钉子：docs/d2-audio-first-scene.md §6（CA 勘误，本页再修正第 5 条）、
  HANDOFF「镜像资源级映射」节（stream.bin content_offset 0xe0a4c/content_bytes
  0x2487 与本路实测 base/size 互证）、docs/stream-volume.md（节表旧口径）。

**边界声明**：本页结论等级 = 模型内真实执行（derived-not-measured）；「真机 tone
正常播放」与「透传分支语义」的一致性是强推断而非设备测量；未做任何设备操作、
未改候选、未刷机。
