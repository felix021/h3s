# 前沿侦察：资源 VFS 域 EXT_RESERVED/TON1 分区头停点 41,858,013 @ 0x6010ca6（路 VFSSCOUT / sim-vfsscout，只读静态调研）

2026-11-18 06:0x。基线 main `fc6e75e`（前沿 **41,858,013 @ 0x6010ca6** = sdfile
分区目录走查器 0x6010c3c 循环体首字节读 **flash 0x61eafe0**，镜像外 unmapped
诚实停）。本页任务：钉死停点函数与消费者链、判定该读对两核 park 是否必经、
给出三选一预案。**纯静态反汇编+官方工件解包+快照解码，未跑任何仿真**；
本路不改 tools/。

**方法与证据等级。** 反汇编双层：vendor objdump（`~/tmp/h3s-eqcmd/app-linear.asm`）
+ 仓库钉定 pi32v2-isa codec 流式重解（本路新增 `gotarget.py`：call/goto/if 目标 =
pc+size+raw.n，call 0x6010a64/0x6000f0c 与路 CD/FLS 实测锚点互证通过）。快照：
sim-batcha `cut-41866039.bin`（41,850,980 @ 0x60195f8，BATCHA on 世界）经
world_snapshot 指纹确认后只读解码。工件解包：官方 UFW（sha `65cafc7f…`，本路
取 `~/tmp/ae-audioin/orig.ufw` 核验）flash 头分区表（每 32B 独立 JL-ENC
0xffff）、应用区 sfc 明文（SFC 明文偏移 0xCDCA0 条目表）、TON/TON1 容器
（jl-misctools cipher，SFC 相位=UFW 绝对偏移，全部 CRC 复算通过）。
**[静态·本路]** = 本路新增钉死；**[文档]** = 已合并条目；**[运行]** =
既有路的活机器实测。全部 derived-not-measured。

## 0. 一句话结论

**停点 0x61eafe0 不是随机镜像外地址——它是 TON1 分区容器在固件 SFC 明文视图
中的头**（分区条目 addr 字段 0x1ED000 按固件统一换算 `x-0x2020+0x06000000`
落到 CPU 0x61EAFE0），与路 FLS 的 ANCIF 停点 0x61fbfe0（addr 0x1FE000 换算位）
**完全同族同律**；容器内容（头 32B name='tone_en' + 目录 + 17 个 wts/aac
文件）在**官方 UFW 外层 type50 条目里全量可得、CRC 全过**——**codex 已裁
（gpt-6-astra，全文 ~/tmp/codex/202611180638-vfsscout/final.md）：选 (a)
先行**，单开关 `ton_official_partition_pair` 成对映射 TON/TON1、窗口=容器
有效全长，(b) 真机窗留作后续对齐批、不阻塞，(c) 不必维持。该 open 走固件
正常成功/失败路径，**无「绕行」可言也不需要**。

## 1. 停点函数 0x6010c3c：sdfile 分区目录走查器 A 版（全指令钉死 [静态·本路]）

```
0x6010c3c  走查器 A（无重试预算版；兄弟 0x6010cec = B 版：32 条目预算，CRC 失败
           r7-=1 继续，扫尽返 -254——FLS 路钉死其走 flash 0x06000080 目录三项）
入口: r0=out结构, r1=查找名, r2=分区名
0x6010c46: r6 = 0x60A1966（串基：+1870"app_area_head"/+1759"EXT_RESERVED"/+0"res"）
0x6010c52/64/6e: 三次 call 0x6010a64(r4=分区名, 串) 分区名匹配
  → 命中1: r7 = QCY+824*4；命中2: r7 += 4 → QCY+823*4；命中3: 不加 → QCY+822*4
  → 全不命中: 返回 -255（QCY=0x1265C0）
0x6010c80: r0 = [r8+8]（caller 预填：首次=槽值；后续=上次命中条目 addr 字段）
  ≠0 → 恢复: r6 = 0x6010c20(r0) = r0 - [0x127290] + [0x127294]   ★统一坐标换算
       （运行时对 0x2020/0x06000000 [文档：FLS]）
  ==0 → 首开: r9=0; r6 = [r7]（槽值直用）
循环 @0x6010ca0:
  rep 32 { b[r0++]=b[r1++] }        ★32 字节条目拷贝（勘误①：不是 128B）
  CRC: call 0x6000f0c(条目+2, 30) vs h[sp]（条目头 CRC@0）
       失败 → 返回 -251（A 版立即失败，无重试）
  名字: call 0x6010a64(条目+16, r1) 命中 → 0x6010cd2
       rep 8×4 拷条目到 [r8+4..36]
       ★命中后 0x6010ce2 [r8+8] = r6 **无条件执行**（if.eq.ri t=1：then 体只
         有 0x6010ce0 一条）——首次命中（r9==0）: r6 先被换成 [sp+4]=条目.addr
         字段，[r8+8]=addr 字段（下一段走查起点=换算(本条目 addr)，本函数的
         核心链转语义）；恢复遍历命中（r9==1）: r6=当前条目 CPU 地址，
         [r8+8]=该地址——**[r8+8] 语义阶段性换义**，watch 时同时记录 r9/r6
         （codex 修正②）
       返回 0
  不命中: r1 = h[条目+14]（last 终止标记）
       ≠0 → 返回（r0=strcmp 结果≠0 = 未找到）
       ==0 → r6 += 32 顺序下一条目
```

条目布局（与 0x6010cec/flash 头表/UFW 容器目录**同一种 32B JLFS 项**，四方
一致 [静态·本路+文档]）：`{hcrc@0 覆盖[2..32), dcrc@2, addr@4, len@8,
flags@12, last@14, name@16}`。

**调用者面** [静态·本路：parse_asm call 边]：
- 0x6010c3c ← 0x6010dfc/0x6010e16（0x6010da6 = mount ctor 'APP'/'app_area_head'
  分派入口 + sdfile init 0x6011202 直调）、0x60607ac/0x60607c6（0x606075c =
  'EXT_RESERVED' 分派入口 [文档：CD 实测分派落点]）。
- 两个入口体同构：`r3=[QCY+82x*4]（分区表基址槽）→ rep 32 拷分区头到栈 →
  [sp+8]=r3 → 提取路径段（0x6010bc8 大写化）→ 第一次 call 0x6010c3c(r1=段1) →
  第二次 call 0x6010c3c(r1=段2) → 第三段 call 0x6010d54 → 0x6010cec`。
- **[r8+8] 在两次调用间被第一次命中的 `条目.addr` 更新**（§0x6010ce0）——这是
  「第二次调用从 TON1 条目 addr 换算处起步」的机制。

## 2. 停点反解：读的是 TON1 容器头（三方互证链）

停点 0x61eafe0 **地址反解唯一**（在「恢复路径+换算槽值 0x2020/0x06000000」
给定下）：`[r8+8] = 0x1ED000`（0x61eafe0 = 0x06000000+0x1ED000−0x2020）。
反解只钉地址，不钉上层 caller 身份（§4 两候选）。0x1ED000 的身份——
**官方 flash 明文里就有答案** [静态·本路]：

**SFC 明文偏移 0xCDCA0 处的 EXT_RESERVED 分区条目表**（= boot 期槽 823 值
0x060CDCA0 指向的表；官方 flash.bin 应用区 sfc 明文，镜像内，hcrc 全过）：


| 条目 | addr | len | flags | last | name |
|---|---|---|---|---|---|
| [0] | 0x20 | 0x60 | 0x93 | 0 | EXT_RESERVED |
| [1] | **0x1E1000** | 0xC000 | 0x92 | 0 | TON |
| [2] | **0x1ED000** | **0x11000** | 0x92 | **1（最后）** | **TON1** |

紧邻的 0xCDD00（= boot 槽 824 值指向）= ALIGN_DIR 表（addr=0x20 len=0xF310 +
anc_ext.bin addr=0x40 len=0xF2D0 last=1），其内容区 0xCFD20 与
firmware-layout.md 布局表逐项一致 [文档互证]。

**坐标惯例（本路核心钉死 + codex 修正①的坐标术语）**：分区条目 addr 字段是
**SFC 应用区相位坐标**（应用区 SFC 明文起点 = 相位 0），固件换算
`addr − 0x2020 + 0x06000000` 得到 **CPU 映射地址 = flash.bin 应用区 SFC 明文
偏移 + 0x06000000**。三套坐标必须分开记：①条目 addr 字段（sdfile/SFC 相位），
②SFC 明文偏移（= CPU−0x06000000，模型映射、本页布局用的坐标系），③**设备
flash 密文视图偏移（= SFC 明文偏移 + 0x2020，FD05 真机读取与 usb-recovery
表用的坐标系）**。三个独立锚互证：
1. **[文档：FLS]** ANCIF 停点 0x61fbfe0 = 换算(0x1FE000)，官方 ANCIF 内容映射
   该处后 parse CRC/魔数全过——addr 字段换算后就是容器头在 SFC 明文视图的
   位置（FLS 代码明确区分 ANCIF 设备位 0x1FE000 与 CPU 0x61FBFE0）；
2. [静态·本路] SFC 明文 0xCDCA0 条目表 addr=0x20 → 内容区 SFC 明文
   0xCDCA0+0x2020+0x20 = 0xCFCC0 = firmware-layout「EXT_RESERVED顺序资源块」
   （0xCFCC0 是 flash.bin 密文视图偏移口径）✓；
3. [静态·本路+文档] TON1.len=0x11000 恰好覆盖 SFC 明文 0x1EAFE0..0x1FBFE0 =
   到 ANCIF 头（0x61fbfe0）为止，TON 容器 0x1E0FE0..0x1EC790 与 TON1 头
   0x1EAFE0 无重叠——**全保留区排列自洽**。

由此**保留区布局图（SFC 明文偏移坐标；括号内=设备 flash 密文视图=+0x2020）**：

| 分区 | addr 字段 | 容器头 SFC 明文位（=停点位族） | 容器长（UFW 实测） | 尾 |
|---|---|---|---|---|
| VM | 0x1DA000（fs_info+4） | 0x1D7FE0（设备 0x1DA000） | —（运行配置区 0x4000） | 0x1DDFE0 |
| BTIF | 0x1E0000 | 0x1DDFE0（FLS erased_bankb 窗 ✓；设备 0x1E0000） | 0x1000 | 0x1DEFE0 |
| TON | 0x1E1000 | **0x1E0FE0**（设备 0x1E1000） | 0xB7B0 | 0x1EC790 |
| TON1 | 0x1ED000 | **0x1EAFE0 = 本停点**（设备 0x1ED000） | 0x10340 | 0x1FB320 |
| ANCIF | 0x1FE000 | 0x1FBFE0（FLS 停点 ✓；设备 0x1FE000） | 0x100 | 0x1FC0E0 |
| ANCIF1 | 0x1FE100 | 0x1FC0E0（FLS 窗 ✓；设备 0x1FE100） | 0xAC8 | 0x1FCBA8 |

usb-recovery.md 真机实测表（2026-11-10 整片备份）记的 `TON/TON1 @
1e1000/1ed000` 是**设备 flash 视图偏移**——数值上与 addr 字段重合（SFC 相位
0 在设备视图的 0x2020、应用区起点重合所致），**FD05 真机读取窗按设备坐标
[0x1ED000, 0x1FE000) 取**（codex 修正①），读回密文须按设备视图解扰后才能
与官方明文容器比对（不能套 UFW 外层绝对偏移相位）。

## 3. 官方工件可得性（fixture 的原料）

UFW 外层 type50 条目（SFC 相位=UFW 绝对偏移；本路 jl-misctools cipher 重提取，
与 FLS 路当年 `~/tmp/fls-outer/tones/` 产物同源）：

- **TON1**：UFW 偏移 0xF1DE0（勘误②：firmware-layout.md 表记 990688=0xF1CE0
  差 0x100；TON padded 尾 0xE6620+0xB7C0=0xF1DE0 与 TON1 数据 CRC 通过的
  start 相接互证），有效长 0x10340；**头 32B = {hcrc 7e4e OK, addr=0x20,
  len=0x10340, flags=3, name='tone_en'}**，目录自 +0x20（index.idx /
  power_on.wts @0x360 len 0xB61 / power_off.wts / low_power.wts / …），
  容器数据 CRC + 全目录 hcrc 复算通过。
- **TON**：偏移 0xE6620，0xB7B0；头 32B name='tone_zh'，目录同构。

**fixture 语义**：把 TON1 容器（官方提取、SHA 钉死）只读映射到
[0x61EAFE0, +0x10340)——SFC 明文视图位置与布局图精确一致，零合成字节。停点
首字节读到的就是官方容器头第一字节 0x4E（hcrc 低字节）。

## 4. 停点上游执行流与必经性判定

快照（41,850,980）钉死的现场 [静态·本路：world_snapshot 解码]：
- 槽 820..824 = 0x2020 / 0x06000000 / 0x06000000 / **0x060CDCA0** / **0x060CDD00**
  （boot 期 sdfile init 填的**分区条目表 CPU 地址**，均在镜像内——所以第一次
  0x6010c3c 走查条目表用官方字节、不停）；
- 语言字 QCY+348 = 0x41A358 ≠ 1 → **en 表**（CA 路 entry10 模式串
  'TON1/tone_en/power_on.*'）；tone 门 QCY+4451 = 1；
- fs_info@0x122800 = {+4=0x1DA000(VM), +C=0x4000, +10=0x2020, +14=0x06000000}。

最后 7,033 步 adapter 形状（on-main − cut 差分）：+463 后增量写 / +227 全字写 /
+156 任务栈传输 / +23 共享锁 / +37 rep 多块 / +22 IRQ 返回 = **OS 任务安装 +
sdfile 二次 init（0x601201a→0x6011000：SFC 读 flash 头+XOR 解扰+CRC+按名写槽）**
的形状 [静态·本路]。槽值在这段内被改写（0x6011000 是 822/823/824 槽唯一写者
[slotscan]；0x1ED000 的精确写指令=st.w.idxs 形状未逐条解——**运行观测项**，
非本路必须）。

**推断的停点链**（caller 身份未运行钉死，两个候选同窗）：
BATCHA 放行 → LED helper 完成 → S19 环装任务 → 二次 sdfile init → tone
en 事件（power_on 开机音）→ fill 0x600d8f0 拼
'flash/EXT_RESERVED/TON1/tone_en/power_on.*' → mount ctor 0x6060802 →
分派 0x606075c → **第一次 0x6010c3c(r1='TON1')** 在条目表（0x60CDCA0 镜像内）
命中 TON1 条目、[r8+8]=0x1ED000 → **第二次 0x6010c3c(r1='tone_en')** 恢复路径
r6=换算(0x1ED000)=0x61EAFE0 → 读 TON1 容器头首字节 → **停**。
候选 caller 2：sdfile init 二段 open 'mnt/sdfile/boot/otp_cfg'
（0x6011202 直调 0x6010da6）——两条路的 0x61EAFE0 窗语义相同（TON1 容器头），
fixture 无差别。

**必经性**：这个 open（或这个域的任何 EXT_RESERVED open）是装任务后资源域的
自然第一步，**跳过 VFS 不存在也不需要**——固件自身对 open 失败有完整处理
（CA 场景 1 实测 not-found 优雅返回 [运行：CD]）。喂官方容器（成功路）或喂
FF 擦除窗（失败路，§6）都是固件走过的合法世界，两核 park 的推进不因选哪个
而阻塞；差别只是 tone 域走多深。

## 5. 停点后的执行流预判（(a) 放行后）

第二次 0x6010c3c 读容器头 32B：hcrc 官方字节天然过 → 名字 'tone_en' 命中 →
[r8+4..36]=容器头、[r8+8]=当前条目 CPU 地址 0x61EAFE0（恢复命中语义，§1
修正后）→ 第三段 0x6010d54/0x6010cec 在容器目录（0x61EB000=0x61EAFE0+0x20
起）找 'power_on.wts' → dir[1] 命中（addr=0x360 → CPU 0x61EB340）→ 文件内容
读出（0xB61B）→ tone 解码/播放链（音频域，新前沿域）。zh 语言臂（QCY+348==1
时）走 TON/tone_zh——同批映射 TON 容器即可覆盖。
若执行停在容器头之后（目录走查/wts parse 中途），回读本页 §2/§3 对应结构
再议，不直接升级 (b)。

## 6. 三选一预案（codex 已裁，2026-11-18 06:41，gpt-6-astra）

**裁决：(a) 先推进**（离线资源域推进不等设备）；**(b) 留作后续真机对齐批**
（需新设备会话授权，不阻塞）；**(c) 不必维持**。

### (a) 落地形状（codex 裁定）

- **单开关 `ton_official_partition_pair`，默认关**（TON/TON1 来源、格式、解析
  机制、假设全同，成对开放利于 zh/en 对照，不设两个生产开关）：

| 资源 | CPU 只读窗口（右端不含） | 有效长度 | 解扰后容器 SHA256（codex 独立复算） |
|---|---|---|---|
| TON | **[0x61E0FE0, 0x61EC790)** | 0xB7B0 | `6a511c89acfa4433fbab7d46f22bbbb62c8524cdd93cd72c9b5101f104ae52b5` |
| TON1 | **[0x61EAFE0, 0x61FB320)** | 0x10340 | `f765091dadf26e5042cbfd94ad8570f71be4714e5fdfa98ed0bcf3f0b4c1fc66` |

- 窗口=容器**有效全长**（不按本次文件读取范围裁剪——目录/索引/其余 tone
  文件同属一个资源对象；FLS「有效长」先例）；**不映射** padded 尾、不补齐
  分区声明容量（TON 0xC000/TON1 0x11000），两窗间及尾部空隙维持 unmapped。
- 挂载自校验：长度、完整 SHA256、容器/目录 CRC、文件数据 CRC、范围；拒绝
  重叠与写入（ancif_official_resources 同款）。
- **显式假设措辞（codex 原文）**：「本场景假设目标 TON/TON1 分区在固件 SFC
  明文视图中携带指定官方 UFW 提取的资源内容，并按当前 sdfile 地址换算映射。
  映射字节全部来自官方工件；未假定分区剩余容量的内容，未验证该设备当前资源
  与官方内容一致。执行结论为 conditional / derived-not-measured，不构成真机
  播放验证。」（不写「出厂一致概率高」——无量化依据，裁决不依赖它。）
- **验证臂**：关闭臂（必做，复现 41,858,013@0x6010ca6 全键相等）；开启臂
  （必做，记录查找名/CRC/句柄 base+size/读出/新停点，official↔b79 同条件
  差分）；**zh 敏感性臂**（语言分支前状态施加明确标注的语言输入，先钉语言字
  读取宽度与比较指令——快照语言字 0x41A358 像指针值，不能据整字值决定改法；
  比较路径与资源归属，不要求数值相同）；完整性负例（错长度/篡改内容/越界
  目录项在挂载校验拒）；FF 对照（可选独立诊断场景：只给首 32B 擦除头，让
  固件自己算出 CRC 0x75F8≠0xFFFF 返 -251，**不直接继承 CA 的「优雅失败」
  结论**）。快照续跑按 CKPT 前缀不变性条件；zh 改动若影响已执行前缀不能复用
  分支后快照；合并前从零回归。

### (b) 真机窗（后续批，授权门）

- **能力核查结论（本路）**：flash 直读能力**存在且实战过**——usb-recovery
  2026-11-10 整片备份×2+恢复（真机 FD05，512B/块全片 2MiB），SIMB2A AVD 回环
  FD05 2MiB 双读==fixture（仿真）；工具=h3s_usb.py backup（FD05）。
  **read_rom.py 的 FD07 是 RAM loader 窗读，flash 直读不走它**。
- **读取窗（codex 修正①）**：设备 flash 密文视图 **[0x1ED000, 0x1FE000)**
  （usb-recovery 表坐标；不是 SFC 明文坐标 [0x1EAFE0,0x1FB320)）；读回密文
  按设备视图解扰后再与官方明文容器比对，**不套 UFW 外层绝对偏移相位**。
- 2026-11-10 备份存在过但不在本机可达位置（本机仅 SIMB2A 仿真 fixture=
  官方 942080B+FF 合成，无真机字节）。
- **授权话术**（写预案不执行）：「需要耳机 flash 0x1ED000-0x1FE000 窗
（TON1 分区，~68KiB）只读字节用于官方资源一致性核对。两条路：① 2026-11-10
救砖的两份整片备份若仍保留，直接复用零操作；② 否则重连耳机 FD05 只读（同
backup 流程的只读子集，无擦写，~1 分钟）。」

### (c) 诚实停

仅当 (a) 放行后停在容器头之后的意外地址且 §2/§3 结构解释不了时回退；
codex 明确「不必为了等待 (b) 维持 (c)」。

## 7. 勘误（对既有记录）

1. **HANDOFF/BATCHA「rep 128B 目录条目拷贝」误读**：rep.i 编码 32 迭代×1 字节
   = **32 字节**条目拷贝（vendor asm `rep 4 32` 的 4 是循环体字节数；0x6010cec
   内同款 rep 对应 FLS 钉死的 32B 条目；r6 += 32 步进互证）。
2. **firmware-layout.md UFW 表 TON1 偏移 990688 系笔误 0x100**：TON
   padded 尾 0xE6620+0xB7C0 = **0xF1DE0（990944）** 才是 TON1 start（本路数据
   CRC 通过的解包值；TON 行 943648=0xE6620 无误）。ANCIF 1057056=0x102120 =
   TON1 尾恰接，自洽。
3. **usb-recovery.md 布局表数值是设备 flash 密文视图偏移**（数值上与 sdfile
   addr 字段重合），容器头在 SFC 明文视图的位置 = 表值 −0x2020（TON1 容器头
   SFC 明文位 0x1EAFE0 而非 0x1ED000）。表本身不错，只是「地址/范围」列是
   设备坐标，FD05 计划与模型映射各用各的坐标系（§2 坐标惯例）。
4. （确认）任务文「已穿 3 个目录条目 CRC 轮 ×30B=90 fed bytes」与第一次
   0x6010c3c 调用的条目表走查**精确吻合**（本路独立推出：条目表恰 3 条
   'EXT_RESERVED'/'TON'/'TON1'，每条一轮 CRC 30B → 90 fed bytes，第三条名字
   命中后 [r8+8]=0x1ED000）；停点是**第二次调用**的容器头首字节，非第一次。
5. **HANDOFF「OTA 镜像 0x1442C0 之外」口径混用**（codex 勘误③采纳）：官方
   UFW 总长 0x143F00、应用 SFC 明文长 0xE3053——0x1442C0 混入了其他工件长度。
   建议表述改为「超出当前已映射的官方 SFC 明文及既有具名资源窗口」。

## 8. 与 prior work 交叉核对

| 记录处 | 已有内容 | 本路增量 |
|---|---|---|
| HANDOFF 路 FLS（ancif_official_resources） | ANCIF/ANCIF1 官方容器 fixture + addr 换算惯例（0x61fbfe0 单点） | 换算惯例升级为**全保留区布局图**（六分区 addr/SFC 明文位/长/尾互洽）；TON/TON1 同族容器的 fixture 直接模板 |
| docs/resfile-transform.md（路 CD） | mount/分派/RES 目录遍历实测；QCY 槽 boot 期值 | 停点=第二次 0x6010c3c 的恢复路径机制（[r8+8]=条目.addr 链转）；EXT_RESERVED 表 SFC 明文位与内容全解 |
| docs/usb-recovery.md | 真机布局表（addr 坐标）+FD05 能力 | 表值坐标性质勘误；flash 直读能力结论（≠read_rom.py 的 RAM 窗） |
| CA 路（tone 场景） | entry10 模式串/语言字/gate | 快照实测语言字=en、gate=1（41,850,980 时点），停点域名 TON1 与 en 臂一致 |
| firmware-layout.md | UFW 条目表/flash 布局 | TON1 偏移勘误 0x100；SFC 明文 0xCDCA0 条目表内容（原表未列） |

## 9. 诚实边界与未知

1. 全部布局/容器结论 derived-not-measured；「官方 UFW 容器 == 真机设备视图
   [0x1ED000,0x1FE000) 处（解扰后）字节」是显式假设（FLS 同款措辞，codex
   定稿），(b) 才能闭环。
2. 停点 caller（tone en power_on vs sdfile otp_cfg）与槽 0x1ED000 的精确写
   指令未运行钉死——放行后 watch **0x127290/294/298/29C/2A0 五槽**（codex
   补的 0x127290=换算减数）+ last_instructions 即证；不影响 fixture 窗语义。
3. (a) 放行后的下一停点预判（容器目录走查→wts 读→音频域）未经验证；**放行
   证明的是「官方资源内容假设下固件如何继续执行」，打开/读出/解码/输出/
   后续停点应分别判定，不预承诺进入音频域**（codex）。若新停点出乎预期：
   保存失败指令、访问宽度、有效地址、句柄、最近目录项，逆追「目录 addr →
   换算 → 容器内偏移 → read 长度」；窗口内故障查映射实现、窗口外故障查地址
   构造；**不自动扩窗、不补 FF、不改指针**。
4. FF 擦除窗备选的边界：CRC16(0xFF×30)=0x75F8≠0xFFFF，第一轮 CRC 失败即返
   -251，无重试循环（A 版无预算）；仅作可选 FF 对照诊断臂（codex：固件自己
   算 CRC，不继承 CA 优雅失败结论）。
5. zh 敏感性臂的语言字钉法（读取宽度+比较指令）未做——快照语言字 0x41A358
   像指针值（codex 指出），改法不能凭整字值定，落地路先钉。
6. 本路一次性脚本（gotarget.py/slotscan.py/vfsdis.py）在 `~/tmp/frontier-scout/`
   （不进仓）；本仓新增本文档与
   [evidence/sim-routes/frontier-scout-vfs.json](../evidence/sim-routes/frontier-scout-vfs.json)。
   codex 参谋全文见 `~/tmp/codex/202611180638-vfsscout/final.md`（含独立复算
   的 TON/TON1 容器 SHA256、验证臂与风险表全文）。
