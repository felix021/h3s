# 控制面板全功能调研 + 「更多功能」tab + 按键 wire 修正（路 MOREFUNC / sim-morefunc）

2026-11-19。用户真机反馈：BLE 命令通道全通（电量/ANC ✓），但**按键自定义发送失败/未确认**；
另要求把协议里「更多功能」未实现的都调研做了，点名漏了**左右声道平衡**。本页把官方控制面板
（`data/controlpanel_qcyh3s_16764.json`，功能清单权威来源）逐功能过一遍，用固件命令注册表反解
（表 A=[0x060a9b78,0x060a9d1c) 15×28B、表 C=[0x060a9b24,0x060a9b78) 3×28B，docs/r4-command-hook.md §1
布局 + 本路逐 handler 反汇编）钉死每个功能的 cmdid/载荷，并在 android-query 落地「更多功能」tab。

证据强度沿用：**[静态]** = 本路反汇编/镜像钉死；**[面板]** = 控制面板 JSON 字节；**[文档]** = 既有
docs/HANDOFF；**[SDK]** = jl_bt_ota.aar 反编译（javap）；**[推断]** = 上述链推出；**[未钉]** = 如实标注。

反汇编基线：官方 app.bin sha `18f3d573…`（pi32v2-isa @3608ed1，`tools/pi32v2_asm.py` 离线解码），
只读；未连真机（红线不变），真机相关结论全部标「待真机复验」。

## 0. 一句话结论

1. **按键自定义发送失败的根因**：固件命令注册表（两张表全部 cmdid）里**没有 0x2b**，旧默认 wire
   ADV type2（opcode C2）的写对偶也未经固件证实——两条旧候选发出去固件都不会回执。真 wire 已钉死：
   **批量同步 opcode 0xC0/0xC1，表 C cmdid 2**（§3）。App 默认 wire 已切换，另加「从设备读取」。
2. **声道平衡无命令可发**：官方面板该项是占位条目（type=8、无 cmdid、文案是从降噪模式复制的），
   固件注册表 18 cmdid 里也没有平衡命令——不猜字节、不放假滑杆（§5，复证 docs/config-key-map.md §4）。
3. 控制面板其余功能全部钉死并落地：定时/断连关机（0x14/0x1f，分钟 LE16）、提示音语言（0x19，
   4B key "JL00"/"JL01"）、LDAC/双设备（0x23/0x24，互斥+重启）、设备名（批量同步 cmdid 1）、恢复
   出厂（0xC5/01）、找耳机响声（0xC5/02 [推断]）、清除配对（FF/0x03 [推断]，实验性）。佩戴检测、
   连接设备列表读取：**面板/注册表/SDK 三方都无**（§5/§6）。

## 1. 控制面板 × 固件注册表 全功能对照（调研清单）

面板 16 个条目逐个过，固件侧 cmdid 全部来自注册表反解（表内容与 r4-command-hook.md §1.2 逐字节
互证，本路复扫一致）：

| 面板功能 | 面板字段 | 固件 cmdid | 载荷（写，App→耳机） | 回读 | 状态 |
| --- | --- | --- | --- | --- | --- |
| 降噪模式 | type 9，startcmdid 0x10100 族（=mode<<16\|scene<<8\|strength） | FF/0x17 | `17 03 mode scene strength`（data=后 3B） | `17 03 m s t` | [已钉]已实现 ✓ |
| 音乐均衡器 | type 2，character 0x0B | FF/0x22 | 3B 头+每段 7B | `22 n …` | [已钉]eqfix 路 |
| 耳机按键自定义 | type 14，eventID 769/1281/1793-1796 | **批量同步 0xC0/0xC1，表 C cmdid 2** | item=`{04 02 key gesture action}`（或 46B 全表） | C1 mask 00 00 00 04 → 45B | [静态]本路钉死（§3） |
| 模式切换（音乐/游戏/电影） | cmdid 46 | FF/0x2e | data=[mode]（len 1；1/3/4） | `2e 01 mode` | [已实现]data_len=1 仍 [假设]待真机 |
| 提示音音量 | cmdid 29，值 3/6/8 | FF/0x1d | data=`value 00`（len 2） | `1d 02 v ?` | [已钉]已实现 ✓ |
| 提示音切换（语言） | cmdid 25，itemKey 4a4c3030/4a4c3031 | FF/0x19 | data=4B key "JL00"/"JL01"（len 4） | `19 04 key` | [静态]本路（§2.1） |
| 定时关机 | cmdid 20，值 0500..FFFF | FF/0x14 | data=`[分钟 LE16, 00, 00]`（len 4；FFFF=不关机，**0=立即关机**） | `14 04 设定LE16 剩余LE16` | [文档+静态]（§2.2） |
| 断连关机 | cmdid 31 | FF/0x1f | data=`[分钟 LE16]`（len 2） | `1f 02 分钟LE16` | [静态]本路（§2.2） |
| LDAC 开关 | cmdid 35，sub cmd 230101/230100 | FF/0x23 | data=[01/00]（len 1） | `23 01 v` | [静态]本路（§2.3） |
| 双设备连接 | cmdid 36，sub cmd 240101/240100 | FF/0x24 | data=[01/00]（len 1） | `24 01 v` | [静态]本路（§2.3） |
| 恢复默认设置 | character 0x0A，id 3，cmd 01 | **opcode 0xC5 子命令 01** | body=SN+`01` | 0xC5 status 帧 | [静态]本路（§4） |
| 清除配对记录 | character 0x0A，id 4，cmd 02 | FF/0x03 | data=[02]（setter 无视 data） | 无 getter | [推断]实验性（§4） |
| 给设备取名 | type 101，maxLength 20 | 批量同步 0xC0，表 C cmdid 1 | item=`{len 01 名字≤32B}`（面板从严 20B） | C1 mask 00 00 00 02 → 字串 | [静态]本路（§3） |
| 寻找耳机 | type 3（无 cmdid） | **opcode 0xC5 子命令 02** | body=SN+`02` → 有链路时播两声提示音 | status 帧 | [推断强]（§4；地图定位在手机侧） |
| 声道平衡 | type 8（**无 cmdid/id/hasValue**） | **无** | — | — | [静态]无命令（§5） |
| 固件升级 | type 1 | E1..E8 OTA | 既有 UpgradeService | — | [已实现] ✓ |

注册表里**有注册、面板没有**的（完整起见）：0x2d（45，写 QCY+33/config key 14 的单字节开关，
与 LDAC 互斥——按互斥链与面板文案[推断]=空间音效开关，但面板空间音效走 EQ 预设 id 10，此命令
不放进 UI）；0x2f 电量、0x10 睡眠、0x30 充电状态（只读已用）；表 C cmdid 12（1B，b[0x125b7d]，
语义[未钉]不放行）；opcode 0x0B（SDK CMD_SWITCH_DEVICE_REQUEST=双设备场景下切换活动设备，
b[QCY+284] 已开双设备才应答——本 app 未放开）。

## 2. FF 自定义命令族新增钉死（载荷逐字节）

以下 setter ABI 均为 `(resp,128,data,len)`（分发器 0x0600f932），**全部返回 0=静默无回执**
（0x0600f942 `ifs n<1` 跳过响应）——所以这些命令的「成功确认」只能靠 FF 只读回读（`FE 01 cmd`）
或固件主动通知（flags=0x80 广播，0x06025642 构造）。App 侧已按此实现「发送→回读确认」，
并把 0x80 通知帧透出为 device_message（此前 H3SBridge 直接丢弃 0x80 帧——这也是部分
「未确认」体验的来源之一）。

### 2.1 提示音语言 0x19（cmdid 25）[静态]

- getter `0x06025ed8`：回 4B=当前 key（串表 `[0x609e284]` 2 项 `{u32 id, char* key}`，
  "JL00"@0x0609d1b3=英文(id 0)、"JL01"@0x0609d1b8=中文(id 1)）→ 回读 `19 04 4a 4c 30 3x`。
- setter `0x06025f00`：要求 len>=4；把 data[0..3] 按 **BE u32** 与表里两项的 key 串比对
  （0x06025f1e-0x06025f52 逐字节组装比对），命中 → `0x0601878e(id)` 切语言 +
  `0x06025642(25, data, len)` BLE 广播。不匹配静默忽略。
- 面板 itemKey `4a4c3030`(英文)/`4a4c3031`(中文) 与串表逐字节一致 [面板互证]。

### 2.2 定时关机 0x14（cmdid 20）/ 断连关机 0x1f（cmdid 31）[文档+静态]

- 0x14 setter `0x06025a2a`：data[0..1]=分钟 **LE u16** → h[0x124C60]；==0xFFFF 跳过（不设定时）；
  否则 0x0005..0x0E10 立即排定时（deadline=now+分钟×60000，延迟投递 0x06066a0a）。
  **0=立即关机**（protocol.md「电源命令的陷阱」实测记录：设置格式 `[14,04,lo,hi,00,00]`，
  第 3/4 字节固件不读——反汇编复核一致）。App 白名单只放行 5/10/30/60/FFFF，**0 拒绝**。
- 0x14 getter `0x0602597c`：4B=`[设定分钟 LE16, 剩余分钟 LE16]`（(deadline-now+59999)/60000）。
- 0x1f setter `0x06025b98`：data[0..1]=分钟 LE u16 → h[0x1249FC]，`0x06025642(31,…)` 广播 +
  `set_config(12, 0x1249FC, 2)`；getter 回 2B；init `0x06025b5e` 读 config key 12，无记录用
  恢复默认值 **5**（0x06025b48 写 0x0005——面板默认 5 分钟互证）。面板值 0500/0A00/1E00/3C00/FFFF
  即 LE16 5/10/30/60/不关机 [面板互证]。

### 2.3 LDAC 0x23（cmdid 35）/ 双设备 0x24（cmdid 36）[静态]

- 0x23 setter `0x06025fa0`：data[0]=01/00 → `0x06022670(v)`：开（v=1）时先清
  **QCY[33]**（config key 14=空间音效类开关）与 **QCY[34]**（config key 8=双设备）并持久化、
  呼吸重启链 0x06025914，最后写 LDAC 影子 b[0x1249BC]（config key 7）；关（v=0）只置脏标志。
  面板文案「打开LDAC会重启且关闭双设备连接和空间音效」与互斥链逐点吻合 [面板互证]。
- 0x24 setter `0x06026268`：data[0]=01/00 → QCY[34]=v、`set_config(8,…)`；开时清 b[0x1249BC]
  （config key 7=LDAC）+ `0x060225fc`（重启/重配链）。getter 读 QCY[34]（`0x06026252`）。
- 0x23 getter `0x06025f8c`：回 b[0x1249BC] 1B。
- 面板 cmd 串 `230101`/`230100`/`240101`/`240100` = `cmdid | data_len=01 | data` 与本载荷形状一致
  （对照 0x14 的 `[14,04,…]` 同族）[面板互证]。

## 3. 批量同步（opcode 0xC0/0xC1）——按键自定义的真 wire [静态]

分发器 `0x0600fae0`（被 RCSP RX `0x0600fd2a` 经 `0x0600fe1e` 以 opcode 调起；`r0=opcode-0xC0`
后 tbh 五路，仅 192..196 落表内）：

- **写（opcode 0xC0，case 0 → 0x0600fb14）**：body（SN 已被上游剥离）= item 序列，item=
  `{u8 itemLen(=1+dataLen，含 cmdid 不含本字节), u8 cmdid, data[itemLen-1]}`；按 cmdid 查表 C
  取 [+12] 写 handler，调 `handler(data=body+2, len=itemLen-1)`；逐 item 推进 itemLen+1。全批次
  完成后经 `0x0600b66e(0xC0,…)` 回 0xC0 状态帧（响应前缀字节语义 [未钉]，App 宽容解析）。
- **读（opcode 0xC1，case 1 → 0x0600fbcc）**：body=4B 大端 mask（**C1 读帧 SDK 实测形状=`SN+mask`**，
  QueryPolicy §ancCycle `00 00 04 00` 即 bit2）；对表 C 每个 cmdid 位查 [+8] 读 handler
  `handler(buf+2, 254)`，组装 item=`{n+1, cmdid, value[n]}` 回 0xC1 帧（前缀 [未钉]）。
- 0xC2/0xC4 空操作、0xC3 回空帧（SDK 侧 194/195/196=ADV notify/settings request 另有归属）。

**表 C 三项**（`[0x060a9b24,0x060a9b78)`，`{+0 cmdid,+4 装载,+8 读,+12 写,+24 恢复}`）：
cmdid 2=按键映射（0x060254ce/0x060254f2/**0x06025564→0x0602550e**/0x0602556c）、cmdid 1=设备名
（0x0602557a strlen+拷出 / 0x060255e4 NUL 补齐 32B 校验写）、cmdid 12（1B，[未钉]）。

**按键写核心 0x0602550e**：len==45 → 整表 memcpy(RAM 表 0x12730C←data)；否则按 3B 三元组
`{key,gesture,action}` 循环（`r6=data[0], r2=data[1], r0=data[2]`），在 15 项表里按 `{key,gesture}`
匹配改 action；末尾 `set_config(48, 表, 45)` 持久化。读 handler 0x060254f2：拷出 45B（缓冲<45 返回 -1）。

App 落地：`KeyRemapFrame.buildBatchSyncCommand`（单三元组 item=`04 02 K G A`）/
`buildBatchSyncTableCommand`（46B item 全表）/`buildBatchReadCommand`（C1+mask 00000004）；
EqWritePolicy 放行 cmdid∈{1,2} 两种 item（cmdid 12 [未钉] 不放行）；Loopback 桩按 0x0602550e
语义改表/回表，端到端单测覆盖。**真机抓包定案项**：0xC0/0xC1 响应帧的精确字节（[未钉]，
当前宽容解析+如实标注）。

**旧候选修正**（docs/app-protocol.md §2.5 的两条 wire）：ADV type2 写（opcode C2）在固件 RX
分发里只是空操作 case；0x2b 在两表全 cmdid 里不存在（docs/r4-command-hook.md §1.1 负面结论
复证）——两条都保留作对照，不再是默认。

## 4. 0xC5 恢复/查找族 + 清除配对 [静态/推断]

RCSP RX `0x0600fd78`：opcode 0xC5 → body[0]（SN 后）分三路：

- **01=恢复出厂**：`0x0600f856`（依次跑表 C/表 A 的恢复默认 walker）——与面板「恢复默认设置」
  cmd 01 对上 [静态+面板]；危险操作，App 端 UI 双重 confirm。
- **02=找耳机响声**：`0x06008298`（数活动链路）>0 则先播提示音 256、再播 260 [推断=两声呼叫
  音]；面板「寻找耳机」文案「也可以让耳机发出声音呼叫你」互证，地图定位半边在手机 App 侧
  （协议无对应命令）[面板互证]。03=no-op 桩（call 到一条 rts 空函数），其它回 status=1。
- **清除配对记录**：面板给的是 character 0x0A + cmd 02；固件侧最接近的是 **FF/0x03 setter
  `0x0602609a`**——无视 data 固定投内部事件 594（0x06023326：h[QCY+122]==0 时按 bit0x10 走
  `0x0601a6da(0x4203)` BT 控制调用；594 事件完整消费链本路未追完，[推断]）。App 标「实验性」
  + 强警示（清除后断连、需重新配对），载荷 data=[02] 取面板 cmd 字节（固件不读）。

## 5. 声道平衡 / 佩戴检测 / 多设备上限 / 连接设备读取（调研收口）

- **声道平衡**：面板条目 type=8 **无 cmdid/id/hasValue**，info 文案是从「降噪模式」复制的占位
  （docs/config-key-map.md §4 已钉，本路复核面板原文一致）；固件 18 cmdid 无平衡命令、config
  key 1..99 无 2 元素 L/R key（最近=key 6，12B=3×float32 默认全 1.0f，语义[未钉]）。**结论：
  现固件无「声道平衡」可下发命令**——App 只放调研结论卡，不做假滑杆。要支持需新增自定义
  命令+空 key（key 30 方案见 config-key-map §5-6，R4 链已有 key 30/31 占用，声道平衡可挑
  27-36 段其它空 key）。
- **佩戴检测**：H3S 面板 JSON 无此条目；注册表亦无（0x30 getter=0x06007f0a 充电状态查询族
  [静态]）。本机型不支持，不放 UI。
- **多设备连接上限（追加调研项 1）**：0x24 只是模式开关；上限是**编译死的 2 台**——QCY 应用层
  链路记录表 `0x1281fa` 2×40B（遍历界 `base+74`、步长 40，恰 2 项 [静态]），分配函数
  `0x060082ce` 有 `if (idx > 2)` 界检查、名字/信息池 h[QCY+146] 上限 400B [静态]。BLE 命令面
  无法突破（改上限=改固件常量表+BT 栈配置，btstack 在另一分区，本仓只读不动）。双设备场景
  的「切换活动设备」另有标准 opcode 0x0B（SDK CMD_SWITCH_DEVICE_REQUEST=11；固件在
  b[QCY+284] 双设备已开时应答 3B 状态 [静态]），本 app 未放开（需求未提，留档）。
- **读取当前连接设备信息（追加调研项 2）**：注册表全部 getter 枚举完毕——无任何返回
  连接设备列表/MAC/名字的命令；0x03 设备信息=设备自身 LTV 属性（22 个构造器、请求 4B BE mask
  选择 [静态]）；JL OTA SDK（jl_bt_ota.aar `Command` 类 javap 全量常量：1/2/3/6/11/194/195/196/
  209/212/225-232/240/255）同样无「连接设备查询」。**结论：协议上读不到耳机视角的连接列表**；
  手机侧系统蓝牙可见自己连了什么（App 内不重复造）。链路表 0x1281fa 是运行态 BSS，BLE 不暴露。

## 6. App 落地清单（tools/android-query）

- **Java**：`MoreFuncFrame.java`（新：0x14/0x1f/0x19/0x23/0x24/0x03/C0/C1/C5 组帧+回读解析）；
  `KeyRemapFrame`（批量同步三组帧+45B 表解析）；`EqWritePolicy`（新形状白名单：六条 FF cmdid
  精确载荷域 + C0 item cmdid∈{1,2} + C1 mask=00000004 + C5∈{01,02}，仍不开任意字节写）；
  `QueryPolicy`/`H3SBridge.readStatus` 扩只读 cmdid {1f,19,23,24,2e}；`H3SBridge` 新方法 10 个
  + **flags=0x80 通知帧透出 device_message**（此前直接丢弃）；`LoopbackTransport` 按真固件行为
  桩化（静默 setter→状态表→只读回读应答；C0 改表/C1 回表/C5 恢复重置桩态）。
- **页面**（assets/index.html，第四个 tab「更多功能」；EQ 区未动留给 eqfix 路）：定时/断连关机、
  提示音语言、LDAC/双设备（互斥前端校验+重启警示）、设备名（≤20B）、找耳机、危险区（恢复默认/
  清除配对 confirm，后者实验性徽标）、声道平衡+佩戴检测调研结论卡；按键卡默认 wire 切批量同步
  +「从设备读取」（C1 回 45B 灌网格，域外动作如实标未知）；**助听 tab「读取两 key 原始值」修复**
  （key30/31 独立结果卡：进行中灰提示/成功 hex+解码/失败红字原因——超时、回执形状不符、未武装
  等分别给一句人话；回调透传原始事件区分超时与形状不符；key30→key31 串行不再吞失败）。
  全部选择 localStorage 记忆（`h3s.morefunc.*`），连接就绪自动回读 9 个只读 cmdid 对齐设备真值。
- **测试**：`MoreFuncFrameTest`（新，14 项含 Loopback 端到端）；KeyRemapFrameTest/H3SBridgeTest/
  H3sTransportTest 更新默认 wire 断言；test.sh 14 套全过 EXIT=0。
- 版本 13 / 0.8.0-morefunc；真机行为（0xC0/C1/C5 响应帧形状、静默 setter 实际时序、0x19 语言切换
  生效表现）**待真机复验**。

## 7. 证据锚点

- 注册表/分发器：表 A/C 布局与全部 handler 地址=r4-command-hook.md §1 复扫一致；批量分发器
  0x0600fae0（写 0x0600fb14 读 0x0600fbcc item/mask 解析、响应 0x0600b66e）；RCSP RX 0x0600fd2a
  （opcode 分发 + 0xC5 三路 0x060100a2/c0/de + 0x0B 0x0600ff28）。
- 0x19：串表 0x609e284{00000000,0609d1b3}{01000000,0609d1b8}，串 "JL00"/"JL01"@0x0609d1b3/b8；
  setter BE 组装比对 0x06025f1e-52；切语言 0x0601878e；广播 0x06025642。
- 0x14/0x1f：0x06025a2a（LE16→0x124C60、FFFF 短路、分钟×60000 deadline）/0x0602597c（4B 回读）、
  0x06025b98（LE16→0x1249FC+config12+广播）/0x06025b7e/0x06025b48（默认 5）。
- 0x23/0x24：0x06025fa0→0x06022670（清 QCY[33]/config14、QCY[34]/config8、0x06025914、写
  0x1249BC/config7）；0x06026268（QCY[34]+config8、清 0x1249BC、0x060225fc）；getter
  0x06025f8c/0x06026252。
- 按键：表 C 首项 cmdid 2 四件套 0x060254ce/f2/564(→50e)/6c；写核心 0x0602550e（45B 全表/
  3B 匹配改写/config48）；RAM 表 0x12730C、默认表 0x0609e250（45B 与 key-action-map §3.2 逐
  字节一致）；C1 mask 族与 QueryPolicy §ancCycle 实测形状互证。
- 链路表：0x1281fa 2×40B（0x06008298 遍历界 base+74）、0x060082ce `idx>2` 检查、池 400B。
- SDK：jl_bt_ota.aar `com/jieli/jl_bt_ota/constant/Command.class` javap 常量全量（无连接设备查询）。
- 验证：`bash tools/android-query/test.sh` 14 套 EXIT=0；`python3 tools/check_local.py --units` ok；
  index.html 两段 `<script>` node --check 通过。

## 8. 未钉清单（如实标注）

| 项 | 状态 | 说明 |
| --- | --- | --- |
| 0xC0 写/0xC1 读响应帧前缀字节 | [未钉] | 响应经 0x0600b66e 构造，前 2 字节参数（r1=0、r2=分发器内部值）语义未追；App 宽容解析（按 item 模式匹配），真机抓一次包可定 |
| 静默 setter 的实发 flags | [未钉] | 0x14/0x1f/0x19/0x23/0x24 写帧按 SDK 惯例 flags=C0（等响应超时释放队列）；QCY App 是否发 0x80 未抓包。若真机验证 0x80 可行可免 5s 等待 |
| 0xC5/02 找耳机的确切提示音语义 | [推断] | 链路检查+双提示音调用链成立；「两声」的音效内容未验证 |
| FF/0x03 清除配对 | [推断] | setter 无视 data 投事件 594→0x0601a6da(0x4203)；事件消费终点未追完，UI 标实验性 |
| 0x2e data_len=1 | [假设] | wired-game-mode 既有标注，真机联调对照（本次只加进只读回读确认链） |
| 0x2d（cmdid 45）语义 | [推断] | 写 QCY[33]/config14、与 LDAC 互斥——空间音效开关候选，面板无对应条目，不放 UI |
