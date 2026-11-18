# 辅听 lfix 修复候选 F1（HAL5+lfix）实施报告 — 路 LFIX-IMPL / sim-lfiximpl

2026-11-19 03:00–04:3x。任务：按 [hearing-aid-lfix-plan.md](hearing-aid-lfix-plan.md) §4
已收口方案构建候选 **F1 = HAL5 合一基座（9ad0f088…，已在真机）+ lfix 修复载荷**，产出
可刷写 ufw 与离线自验报告。**只构建不刷写**；两候选 UNTESTED。

前置：main eedb3f8（含 HAL5 全链与 lfix 方案）；worktree sim-lfiximpl。
初版（commit 9f9575f）经 codex 实施审（gpt-6.2-astra）查出五条 P1，逐条反汇编核实后
全部修正（§5 偏差③-⑥ + §8 回滚文案），本报告为修订版事实。

## 0. 一句话结论

F1 按方案落地为**双产物**（plan §4-C 双镜像裁决，见 §5 偏差①）：

| 候选 | 构成 | sha256 | 追加段 |
|---|---|---|---|
| **F1a**（默认，诊断档） | HAL5 全链 + A1+B1+B2 + **A3 时钟压舱** | `73b45e659d127452697b5580b2474ef481ce9b8118f3f5dfa8b9c731bb4fef48` | 1060B（+186B） |
| **F1b**（对照镜像） | HAL5 全链 + A1+B1+B2（无 A3） | `f16576687dae42a583149aa9f8a2f1b89dc777228245a27c289f0faeec00f2c1` | 946B（+72B） |

```sh
# F1a（含压舱）
python3 tools/build_quiet_candidate.py --attenuation-db 0 --hearing-aid-lfix \
    --hearing-aid-mpo 95 --output-dir <新目录>
# → H3S_0.1.1.8_tones_minus0dB_LFIX_F1A_HOLD_MPO95_OFFLINE_UNTESTED.ufw
# F1b（无压舱）
python3 tools/build_quiet_candidate.py --attenuation-db 0 --hearing-aid-lfix \
    --lfix-no-hold --hearing-aid-mpo 95 --output-dir <新目录>
# → H3S_0.1.1.8_tones_minus0dB_LFIX_F1B_NOHOLD_MPO95_OFFLINE_UNTESTED.ufw
```

验证全绿：`verify_ufw_integrity` 双产物通过（四层 CRC/JLFS）；新测试套
`tools/test_hearing_aid_lfix_image.py` 14/14（含 codex 审后加严的五条 A1 边、
归一化「仅差 A3」断言、HAL5 对照反例）；既有助听/world_checks 12 套 +
`check_local.py --units` exit 0；**HAL5 基座复现逐字节 `9ad0f088…` 不受扰动**
（test_09 钉死）。

## 1. 实施形态（与方案的关系）

方案 §4 的修复点全部落在**追加段载荷源码层**（tools/hearing-aid/*.S 重汇编），
不是对 HAL5 产物打二进制补丁——这是本仓构建链的固有形态（HAL5 先例：候选=
构建器产物）。改动面：

| 文件 | 变化 |
|---|---|
| `tools/hearing-aid/mic_effect_open_lfix.S` | **新增**：F1b open 载荷（A1+B1+B2） |
| `tools/hearing-aid/mic_effect_open_lfix_hold.S` | **新增**：F1a open 载荷（+A3a/A3b + 名串 rodata） |
| `tools/hearing-aid/mic_effect_close_lfix.S` | **新增**：F1a close 载荷（+A3c 压舱释放） |
| `tools/hearing_aid_l1_image.py` | lfix 组合/两遍定址/形状与目标验证（`_verify_lfix_open/close`） |
| `tools/build_quiet_candidate.py` | `--hearing-aid-lfix` / `--lfix-no-hold` 开关、命名、`hearing_aid_lfix` 报告键 |
| `tools/test_hearing_aid_lfix_image.py` | **新增**：14 项（CLI 契约 5 + 工件层 9） |
| `tools/check_local.py` | units 列表登记 lfix 套件 |

toggle 桩（cmd7_toggle_l4）、apply_mpo/apply_eq、r4 keyhook、KEY7 窗口、fence、
注册表接驳全部**沿用 HAL5 原样**（桩源码零改动；内部 call 位移随段长自然变化，
形状/目标断言不变）。F1b 的 close 载荷与 HAL5 逐字节相同（test_04 钉死）。

## 2. 补丁表（每点：定位/原字节/新字节/字节数 + 前后反汇编）

载荷源=方案 §4 的四个修复点。原/新字节以**重汇编后的追加段**计（段基
0x060E3074；F1b/F1a 的 A1 块指令序列相同，仅两条跨距 goto 立即数随段布局不同、
另差 A3 块——test_04 归一化比较钉死：跳转目标归一后两版 open 的 67 条公共指令、
close 的 20 条公共指令分别完全相同）。完整反汇编在 build-report.json 的
`hearing_aid_l1.listing`。

### B1 场景值 10 → 16（保留调用，0B 净增）

**形态勘误（codex 实施审 P1-1，本路反汇编复核）**：plan §4-B1 写「删除
set_scene(stream,10)」，但 0x06074e64 恰是场景字节到达 ADC 的**唯一入口**——它写
b[stream+28] 并派发 0x20006，ADC 收到后在 0x06032a52 写场景字节；第 4 步三参
0x06074a5e 只写 stream+80/+84/+88 字段（0x06074a60-70），**不派发**。官方 pc_mic
调用点 0x06033a26-3a 也是两调用并用。删除会把场景滞留默认值，故按 plan 同节
预留的「改值」形态实施：

原 HAL5 open @0x060e30ce..0x060e30d8（4 指令，两参场景调用）：

```
060e30ce  4016         r0 = r4
060e30d0  412a         r1 = 10                   ; ← PC_SPK（抄 pc_mic 的错值）
060e30d2  c6ff644e0706 r6 = 101142116            ; 0x06074e64 set_scene 两参
060e30d8  c600         call r6
```

F1b @0x060e30fc..0x060e3106（F1a @0x060e3120..0x060e312a 同形，A1 块使地址平移）：

```
060e30fc  4016         r0 = r4
060e30fe  4130         r1 = 16                   ; ← 改值点：HEARING_AID
060e3100  c6ff644e0706 r6 = 101142116            ; 0x06074e64（调用保留）
060e3106  c600         call r6
```

字节变化仅 1 字节立即数：`2a`→`30`（li.u8 编码 = 值+0x20）。场景字节与
0x20006 派发都归 HEARING_AID：ADC GET_FMT 三态从「10→全局」变为「16→44100」，
VolEff 场景注册同归（plan §4-B1 预期态）。

### B2 start 后 SET_CHANNEL(1)（+22B）

**命令勘误（codex 实施审 P1-2，离线执行官方 ADC 处理函数实证）**：plan 裁决版
写 cmd=0x20007（SDK 头 NODE_IOC_SET_CHANNEL 值），但 ADC ioctl 跳表
（入口 0x0603293e，tbh @0x0603295e）里 0x20007 落 0x06032d6e **纯返回**；0x2000C
落 0x06032a58 `b[priv+28]=arg` **直存**（通道数字节）。codex 预置通道数=2 离线执行
官方函数：`0x20007,1` 调后仍 2；`0x2000C,1` 调后变 1。改用 0x2000C。路径经
node_ioctl → root 类表 0x060a8ec4（回调 0x06076cc4）→ 0x06076d24-30 透传到
D06D plug（ioctl 槽 0x060a91ac → 0x0603293e），不广播到 0x0606a526/0x060c43da
的模式枚举处理器。

新块 @F1b 0x060e3114..0x060e3128（F1a 0x060e3138..0x060e314c 同形）：

```
060e3112  8053         if (r0 != 0) goto 38      ; start 失败检查（原有）
060e3114  4016         r0 = r4                   ; 重载 stream（跨 call 纪律）
060e3116  41e00011     r1 = 4352                 ; 0x1100 根/SOURCE
060e311a  c2ff0c000200 r2 = 131084               ; 0x2000C ADC 通道数直存 ioctl
060e3120  4321         r3 = 1                    ; 通道数量（mono）非模式枚举
060e3122  c6ffc64e0706 r6 = 101142214            ; 0x06074ec6 node_ioctl
060e3128  c600         call r6
060e312a  c0ff74551200 r0 = 1201524              ; 记槽（原有后续）
```

**必须 start 后设**（codex 方案裁决，本路重验）：ADC 开链 cmd 0x40001 兜底块
0x06032b6c-0x06032b94 先清零 b[priv+28] 再按 en_map 逐使能通道 +1 重算——start
前设会被覆盖，start 后设是对协商结果的最后覆盖。返回值不检查：协商默认 mono
时为无害重申。arg=1 是通道**数量**语义（17/18/20 是模式枚举，AUDIO_CH_NUM=ch>>4
编码不同）。

### A1 open 前 ANC 退出等待（+48B：等待块 44B + 取消尾 4B）

**单位勘误（codex 实施审 P1-3）**：延时原语 0x06064efc 的参数是 **OS tick 非毫秒**
（0x06064f12-14 取参数低 16 位调 0x06064e80，0x06064eb8 直接写 h[TCB+6]=n tick，
全程无毫秒换算；1 tick=10ms，仓内 s19-preboundaries §4.4 / s19-endgame-map §202
钉死）。初版 delay(10)×59 ≈ 5.9s 会把按键处理挂住数秒——改为每轮 **1 tick**：
上限 60 次轮询、59 次睡眠 ≈ **590ms**。ANC 退出链三处 call.xl 实调
delay(1)/(2)/(100)（0x0606ec18/ec58/eca2），与 tick 口径一致。

新块 @0x060e3080..0x060e30ac（插在槽守卫与 get_pipeline_uuid 之间；指令序列两版
相同，仅两条跨距 goto 立即数随段布局不同——F1a 编码如下，F1b 槽守卫为
`a559 if (r5 != 0) goto 178`、超时为 `2548 goto 144`，test_04 的归一化比较正是
把这些跳转位移归一后断言指令流一致）：

```
060e307e  b54b         if (r5 != 0) goto 214     ; 槽守卫（原有，→open_done_ok）
060e3080  c6ffc0651200 r6 = 1205696              ; 0x1265C0 QCY 结构基
060e3086  d1ec644f     r4 = [r6+500]             ; ANC ctx 指针
060e308a  0450         if (r4 == 0) goto 32      ; ctx 未建（db3c 空转）→跳过
060e308c  4d3c         r5 = 60                   ; 轮询上限
060e308e  c6fffc4e0606 r6 = 101076732            ; 0x06064efc os_time_dly
060e3094  50ee4d01     r0 = b[r4+29] (u)         ; 过渡标志
060e3098  8044         if (r0 != 0) goto 8       ; 未清 → 去扣计数
060e309a  50ee4102     r0 = b[r4+33] (u)         ; 目标模式
060e309e  00f80502     if (r0 == 1) goto 10      ; 已到 OFF → 放行去 r0=15
060e30a2  fd3f         r5 += -1
060e30a4  255a         if (r5 == 0) goto 180     ; 超时 → 取消本次 open
060e30a6  4021         r0 = 1                    ; 1 OS tick = 10ms
060e30a8  c600         call r6                   ; os_time_dly(1)
060e30aa  f794         goto -24                  ; 回轮询头
060e30ac  402f         r0 = 15                   ; get_pipeline_uuid（原有）
```

超时取消路径（新 label open_err_cancel）@F1b 0x060e3136（F1a 0x060e315a）：

```
060e3136  102a         r0 = -22
060e3138  048e         goto 28                   ; → open_ret（弹栈返回）
```

退出条件=过渡清零**且**目标仍为 OFF（后者防等待期间用户按 ANC 键把目标改走）。
超时=前置条件未成立（codex 裁决）：取消本次 open，槽保持 0，桩见 r0≠0 跳过
apply_mpo/apply_eq，用户再按一次即重试。db3c(1,1) 仍由 toggle 桩先行调用（顺序
不变）；A1 在 open 载荷内做，对调用方透明。

### A3 时钟压舱（仅 F1a：open +70B / close +34B / 名串 10B）

**所有权强化（codex 实施审 P1-4）**：clock_alloc 失败路径有两条——同名已存在
（0x060009a8）与 44B 堆分配失败（0x060009b4），失败尾 0x060009e4 返 **-1**（成功
返 0）。初版对返回值不看、错误尾/close 无条件 free，违反所有权纪律：free 未命中
名虽不摘链，但 0x0600576c 仍调 0x060008ca 做一次时钟重算调度（任务路径清计数并
重设/新建 4000 单位 timer，0x0600092e-5a）——恰好污染 A3 要研究的变量；同名冲突
时还会摘掉本次没取得的条目。修订：**BSS 持有标志 b[0x125578]**（RAM 槽同洞
[0x12544e,0x12569f) 内 slot+4，boot 清零；构建器 `_lfix_flag_checks` 断言洞界），
仅 alloc 成功置 1，两侧释放都先查标志。

A3a（uuid 命中后、parse 前）@F1a 0x060e30c2..0x060e30e4：

```
060e30c0  0416         r4 = r0                   ; r4 = uuid（原有）
060e30c2  c0ff9e310e06 r0 = 101593502            ; "lfix_hold" 名串 @0x060e319e
060e30c8  c1ff0020a107 r1 = 128000000            ; 128MHz
060e30ce  c6ff78090006 r6 = 100665720            ; 0x06000978 clock_alloc
060e30d4  c600         call r6
060e30d6  0516         r5 = r0                   ; r5 = alloc 结果（0/-1）
060e30d8  c0ff78551200 r0 = 1201528              ; 0x125578 持有标志
060e30de  8542         if (r5 != 0) goto 4       ; -1：不置位直接继续
060e30e0  4521         r5 = 1
060e30e2  8d40         b[r0+0] = r5              ; 持有标志 = 1
060e30e4  4016         r0 = r4                   ; 跨 call 重载 uuid（parse 要）
060e30e6  c1ff6dd00000 r1 = 53357                ; pipeline_parse（原有）
```

A3b（open 失败共享尾，event_notify(11) 之后）@F1a 0x060e3178..0x060e3198：

```
060e3176  c600         call r6                   ; event_notify(11)（原有）
060e3178  c0ff78551200 r0 = 1201528              ; 持有标志
060e317e  0d40         r5 = b[r0+0] (u)
060e3180  054c         if (r5 == 0) goto 24      ; 未持有 → 跳过释放
060e3182  c0ff9e310e06 r0 = 101593502            ; "lfix_hold"
060e3188  c6ff2c570006 r6 = 100685612            ; 0x0600572c clock_free
060e318e  c600         call r6
060e3190  c0ff78551200 r0 = 1201528
060e3196  4520         r5 = 0
060e3198  8d40         b[r0+0] = r5              ; 清标志
060e319a  102a         r0 = -22                  ; （原有收尾）
```

A3c（close 尾部，stop/release/清槽/notify 之后）@F1a close 0x060e31e4..0x060e3204：
同 A3b 的门控释放（`r0=0` 成功收尾在其后）。名串 rodata `"lfix_hold\0"` @0x060e319e
（open 代码尾与 close 之间；与 "mic_effect" 不同名，不会被 CLOSE_PLAYER 分支
顺带释放）。

所有权配对（每轮恰一次）：uuid==0 / A1 超时路径在 A3a 之前退出，无申请无释放；
alloc 失败（标志 0）后续两侧都跳过；成功 open 由 close 的 A3c 释放；重复 open
被槽守卫挡住不二次申请；close 空槽早退在 A3c 之前，串行第二次 close 不再 free。

### 寄存器/调用约定说明

A1 循环的 r4(ctx)/r5(计数)/r6(延时函数) 跨 `call r6` 存活：0x06064efc 压回
{rets,r4}，其任务路径 0x06064e80 压回 {rets,r5,r4} 且不写 r6，下游 0x06064188
保存 r6-r4、调度器 0x06064340 保存 r12-r4（codex 逐函数核对）——与既有载荷对
每个固件调用的同一信任假设。A3a 破坏 r0=uuid 后必须 `r0 = r4` 重载（L1KEY 跨
call 重载纪律），实现时该 bug 由自检发现并修正（§5 偏差⑧）。

## 3. 预算账

追加段可用区 = flash 尾 padding [0xE5073, 0xE5FF8) 3981B − 33B JLFS 头/标记 =
**3948B**（HAL5 先例口径）。HAL5 段 874B = open 156 + close 64 + 其余载荷 654，
余 3074B。

| 项 | F1b | F1a |
|---|---|---|
| A1 等待块 44B + 取消尾 4B | +48B | +48B |
| B1 改值（立即数 0x2a→0x30，调用保留） | ±0B | ±0B |
| B2 SET_CHANNEL(1) 块 | +22B | +22B |
| step3 ioctl 前防御性 stream 重载（语义冗余） | +2B | +2B |
| A3a（含 uuid 重载 2B） | — | +36B |
| A3b 失败尾门控释放 | — | +34B |
| A3c close 门控释放 | — | +34B |
| 名串 rodata | — | +10B |
| **open 载荷小计** | **228B（+72）** | **298B（+142）** |
| close 载荷 | 64B（=HAL5） | 98B（+34） |
| **段合计（vs HAL5 874B）** | **946B（+72）** | **1060B（+186）** |
| 段尾（flash 坐标 0xE5073+33+段长） | 0xE5446 | 0xE54B8 |
| **剩余预算** | 3002B | 2888B |

实耗 72/186B，均在 plan §4「<400B」预算内（F1a 实耗为预算的 47%）。
初版报告把 F1b 段尾笔误为 0xE5447，正确值 0xE5446（codex P2-F 指出，已改）。

## 4. 锚点复核记录（本路 + codex 实施审双重反汇编复核）

全部以官方 app.bin（sha `18f3d573…`）反汇编重验；标注〔codex〕处为实施审独立
复核或离线执行实证：

| 锚点 | plan 断言 | 复核 |
|---|---|---|
| db3c 0x0600db3c ctx/标志 | ctx=[QCY+500]；写 b[ctx+5]/[+29]/[+33] | ✓ @0x0600db3e-76：`r7=0x1265C0; r3=[r7+500]`；同模早退读 b[ctx+33]（受 [ctx+568]==0 门控）；置位序 = 先 [+29]=1 后 [+33]=目标（目标立即写，非完成态）。〔codex〕另有在途转换早退 @0x0600db6c：本次 OFF 请求**不提交**直接返回，A1 不重投——旧转换结束若目标非 OFF 终会超时（§7.1 判别信号） |
| b[ctx+29] 清零站点 | plan 称退出链清零、站点未钉 | **已定位**〔codex〕：0x0600dafe-db02 取 ctx 移到 ctx+28，0x0600db36-38 写零到 +1（即 ctx+29）；提示音回调目标不匹配分支 0x060276e4-e6 也直接清 ctx+29。证明 busy 可清零；**不等于** ADC/ANC 硬件重配已结束，两次字节读取也非原子快照（§7.1） |
| 延时 0x06064efc | plan 初稿「(10ms)」 | **勘误**〔codex〕：参数是 OS tick——0x06064f12-14 取低 16 位调 0x06064e80，0x06064eb8 写 h[TCB+6]=n tick，无毫秒换算；1 tick=10ms（s19-preboundaries §4.4）。退出链 0x0606ec18/ec58/eca2 以 call.xl 实调 (1)/(2)/(100) tick |
| clock_alloc 0x06000978 | (名, 频率) 记账 | ✓ malloc 44B 挂链表 @0x125340；〔codex〕同名已存在 0x060009a8 / 堆尽 0x060009b4 → 失败尾 0x060009e4 返 -1，成功返 0 |
| clock_free 0x0600572c | 按名摘链，未命中无害 | ✓ 逐名 strcmp；未命中不摘不 free，〔codex〕但 0x0600576c 仍调 0x060008ca 做时钟重算调度（0x0600092e-5a 清计数+重设/新建 4000 单位 timer）——「无害」仅指不摘链，多余调度正是 A3 要避免的污染，故持有标志门控 |
| node_ioctl 0x06074ec6 | (stream,id,cmd,arg)，0x1100=root/SOURCE | ✓ `r0=[r0+40]` 取 root pipeline → worker 0x06074e7e（0x1100 特例分支 @0x06074e92）；〔codex〕root 类表 0x060a8ec4 回调 0x06076cc4 → 0x06076d24-30 透传到绑定 plug |
| ADC SET_CHANNEL 消费点 | plan 裁决版 0x20007 | **勘误**〔codex 离线执行〕：ADC ioctl 入口 0x0603293e（tbh @0x0603295e）里 0x20007→0x06032d6e 纯返回；**0x2000C→0x06032a58 `b[priv+28]=arg` 直存**。预置通道数=2 执行官方函数：0x20007 调后仍 2，0x2000C 调后 1 |
| ADC 通道数字节重算 | 0x6032b6c-94 按 en_map 重算 b[priv+28] | ✓ 清零 @0x06032b6c → 逐通道测试 en_map 位 → 每使能通道 `b[+28]+1`（0x06032b8e-92），块受 0x06032b46 已初始化标志控制——B2「start 后设」的根据 |
| set_scene 两参 0x06074e64 | plan §4 载荷标签勘误为 set_scene(10) 入口 | ✓ 写 b[stream+28] + 派发 0x20006；〔codex〕ADC 收 0x20006 在 0x06032a52 写场景字节；官方 pc_mic 调用点 0x06033a26-3a 证明「三参写字段 + 两参派发」是官方并用形态——B1 保留调用的根据 |
| 三参 0x06074a5e | （plan 误当场景入口之一） | **勘误**〔codex〕：0x06074a60-70 只写 stream+80/+88/+84，不派发——B1 不能删两参调用的根据 |

## 5. 与 plan 的偏差（逐条）

**实现级勘误四项（codex 实施审 P1，逐条反汇编/离线执行核实后修正）：**

1. **B1 形态：删调用 → 保留调用改值 10→16**。plan §4-B1 主形态是「删除
   set_scene(10)」，但其引用的 0x06074e64 是场景字节到达 ADC 的唯一入口（§4 表），
   删除会让场景滞留默认。按 plan 同节预留的「改值」出口实施。**净效果**：场景
   字节 + 0x20006 派发归 HEARING_AID，plan 预期的 ADC 采样率三态变化（10→全局
   vs 16→44100）得以成立。
2. **B2 命令：0x20007 → 0x2000C**。plan 裁决版沿用 SDK 头值 0x20007，但该命令
   在 ADC ioctl 跳表落纯返回（§4 表，离线执行实证）；0x2000C 落通道数字节直存。
   调用形状/时序/arg 语义不变。
3. **A1 延时单位：毫秒 → OS tick**。plan 写 0x6064efc(10ms)，实际参数是 tick
   （1 tick=10ms）。每轮延时 10→1，超时预算从 5.9s 回到 ≈590ms 契约值。
4. **A3 所有权：无条件释放 → BSS 持有标志门控**。plan 未规定 alloc 失败（同名
   已存在/堆尽返 -1）的处理；修订为成功才置 b[0x125578]=1，open 失败尾与 close
   都只在持有时释放并清零（每轮恰一次配对；顺带避免 free 未命中仍触发的一次
   时钟重算调度）。

**实施选择与既知偏差：**

5. **A3 未做成运行时可开关，出双产物**（同初版）。plan §4-C 优先「key32 参数
   通道」做运行时开关，但那需要 keystore_hook 扩 key + App 通道 + 三层钉死测试面
   ——改造面远超「一次刷机对照」的收益，plan 自身预留了「双产物更划算」出口。
   **F1a=默认（含压舱，诊断档）**，`--lfix-no-hold` 出 F1b。
6. **A3a 位置「uuid 命中后、parse 前」**。uuid==0 路径在 A3a 之前，无需释放，
   错误路径不新增分支。〔codex 附注〕此时 mic 的 24MHz 申请已先发生，静态上只能
   确认最终请求集合 {base24+mic24+hold128}，不能断言调度时序等价——DVFS 语义
   以 Σ 计，plan 已注明每轮 open 首次切换不可避免。
7. **step3 ioctl 前防御性 `r0 = r4` 重载（+2B，两版共有）**：stock 靠 parse
   返回值碰巧留在 r0；语义等价，保留（与载荷其它调用点的重载纪律一致）。
8. **实现期自检修正两处**：A3a 破坏 r0=uuid 补重载（test_04 锚点断言 +
   `_verify_lfix_open` 重载位置断言双钉）；`assemble_segment` 的 data_spans 排序
   bug（name 段地址最低却 append 在尾，zip 配对错乱）改 sorted——只影响新增
   span，stock 变体原本有序零回归（12 套既有全绿佐证）。
9. **预算/报告勘误（codex P2-F）**：初版 F1b 段尾 0xE5447 笔误（正确 0xE5446）；
   报告身份键初版误写 `payload_prefix_matches_route_L`，改为
   `payload_prefix_matches_lfix_source` / `close_payload_matches_lfix_source`。

## 6. 离线自验清单（结果）

| # | 项 | 结果 |
|---|---|---|
| 1 | 镜像结构完整：UFW 头/条目/boot-info/JLFS 四层 CRC/外层 CRC | ✓ verify_ufw_integrity 双产物 exit 0（test_07 独立跑） |
| 2 | 与基座 diff 逐字节枚举，全部落在预期补丁区 | ✓ test_02 三组（F1a/F1b vs HAL5、F1a vs F1b）；白名单按 codex P2-E1 收窄：boot 只放行 CRC [0x2000,0x2006) + 长度 [0x2008,0x200C)，boot-info CRC 区只 0x2020-4/0x2040-4 |
| 3 | 镜像内追加段 == 载荷源独立重汇编（逐字节）；追加头字段独立钉死 | ✓ test_03：code/listing 对照 + 追加头 hdr[16:30]==b'mic_effect.bin'、kind 0x82、0xff marker（不再依赖生产侧同一函数覆盖追加头） |
| 4 | 「仅差 A3」契约 | ✓ test_04：F1b close==HAL5 close 逐字节；F1b 无 clock 锚点无名串；F1a open 恰含 {alloc×1, free×1, 名址×2, 128MHz×1}，close 恰含 free+名址；名串字节在位；**归一化指令流比较**（codex P2-E2）：跳转目标归一后两版 open 67 条/close 20 条公共指令完全相同，差异恰为 insert-only 的 19 条 A3 指令（difflib 断言插入多重集） |
| 5 | A1 循环接线（从镜像独立解码，两版都查） | ✓ test_05 五条边：ctx==0→uuid、busy→扣计数、settled→uuid、超时→open_err_cancel（弹栈返回无槽写）、重试→回轮询头；延时调用 0x06064efc 且参数 1 tick（codex P2-D3 补 busy/空 ctx 两边） |
| 6 | 预算 | ✓ test_06：段尾 F1b 0xE5446 / F1a 0xE54B8，均 ≤ 0xE5FF8 |
| 7 | 反例判别力 | ✓ test_08（codex P2-E3 改造）：对照 HAL5（非官方源包），先断言未翻转时通过、翻转后**唯一**越界偏移=0xb00 |
| 8 | 基座不受扰动 | ✓ test_09：HAL5 复现 sha `9ad0f088…` |
| 9 | 既有链零回归 | ✓ 助听/world_checks 12 套（r4 `2576df46…` / l1_slotram 复现）+ android-query test.sh + check_local --units 全绿 |
| 10 | 形状/目标钉死 | ✓ 构建器 `_verify_lfix_open/close`：LFIX_*_INSN_IDS 全形 + li.u32 载荷序逐位 + A3a 置位纪律（`if (r5 != 0)` 在位）+ uuid 重载位置 + 持有标志洞界断言；encode→decode 往返逐条零 diff |

**不做**（本路红线）：真机刷写、UpgradeGate 登记（候选过验收后主协调再登记）、
probe 五文件与 analyze_image_trace.py。

## 7. 诚实边界

1. **静态补丁无法验证运行时行为**：A1 等待是否如期放行（依赖退出链清 +29——
   清零站点已定位 §4，但「标志清零≠硬件重配结束」，且两次字节读取非原子）、
   B2 覆盖是否消左耳噪声、A3 是否抑制运行期振荡——全部留给真机刷写后用户实测。
   若 +29 实际长期不清零，A1 将每次 ≈590ms 超时后取消 open（表现为「双击有提示
   音但辅听不开」）——这本身是可观察的判别信号，不是不可诊断的挂死。db3c 在途
   转换早退（本次 OFF 请求未提交）与等待期间目标被改两类场景，A1 的行为都是
   最终超时取消，语义安全。
2. **B2 的观察盲区（codex P1-2 附带）**：返回值不检查；若 start 后协商本就 mono，
   观察到 mono 不能证明 B2 生效（需以「start 后通道数初值=2 的反例」验证——
   离线已证命令有效，运行值核实留给真机 trace/听感）。plan §4-C 要求的
   ADC/DAC 协商采样率核对同理留给验收。
3. A3 判读边界（plan §4-A3 codex 裁决）：每轮 open 首次 24→160M 切换不可避免；
   F1a 不掉线只判「高频保持相关」（C1 时钟扰动 vs C5 算力不足不分）；长期续航/
   温升未测，A3 是短期诊断档非最终方案。
4. 消费链零执行证据继承 HAL5 §7（key30/key31 消费链、非事务性等全部照旧）。
5. F1 相对 HAL5 的预期差异面：open 多出 ≤590ms 的 ANC 等待（正常应 <100ms 级）、
   场景字节 16、start 后一次 node_ioctl(0x2000C,1)（F1a 另有 open 时一次
   clock_alloc/close 时一次 clock_free）；其余行为同 HAL5。

## 8. 真机刷写验收清单（候选过验收后另行授权；此处只列标准）

**验收标准 = open→运行→close→再 open 全程稳定 + 左耳无刺耳噪声**（plan §4-C）。
建议顺序：先刷 **F1b**（无压舱，验 A1+B1+B2 的噪声面）→ 观察断联是否仍存在 →
再刷 **F1a**（加压舱，验崩溃面）→ 对照归因。每轮按三时间窗分开记录（codex）：

| 时间窗 | 记录项 |
|---|---|
| open 当场 | 双击→「关降噪」提示音→出声延迟（含 A1 等待，正常 <100ms 级，≈590ms 仍无声=疑似 +29 不清零，见 §7.1）；是否断联及时间差；左/右耳听感（刺耳噪声有无） |
| 持续运行（≥3 个 4s timer 周期） | 断联/噪声/音量爬升；F1a 与 F1b 的差别 |
| close 后再 open | 第二次 open 是否断联（原故障场景）；循环 ≥5 次；超时取消是否出现 |

对照变量：蓝牙通话中开辅听（③）、提前手动关 ANC 再开（⑤）、纯 open/close 循环
（④）沿 plan §5 判别表。

**恢复前提（codex P1-5 文案修正）**：既有 USB 回滚 profile `accept-b79-rollback-v1`
在回滚时以 **HAL5 的 app 长度与 SHA** 检查 bank B 来源——F1a/F1b 都不满足该条件，
**F1 的离线回滚前提当前未满足**。候选过验收后需先扩 profile（登记 F1 长度+SHA，
保持严格检查不放宽）再谈设备验收，或走完整 USB 恢复流程。HAL5→F1 的预期差异
同 HAL5（ANC 键官方三态、提示音不衰减）。

## 9. codex 实施审采纳记录（2026-11-19 03:55，gpt-6.2-astra，~/tmp/codex/202611190355-lfiximpl/final.md）

结论「建议暂缓合并 9f9575f」——五条 P1 全部成立并已修正（载荷源+测试+文档+
重建重钉 SHA）：

| 级别 | codex 发现 | 核实 | 处置 |
|---|---|---|---|
| P1-1 | B1 删除了唯一场景入口，16 根本发不出去 | 反汇编+官方 pc_mic 调用点证实 | 改「保留调用改值 10→16」（§2 B1、§5 偏差1） |
| P1-2 | 0x20007 在 ADC 纯返回，B2 无效 | 离线单函数执行官方 0x0603293e 实证 | cmd 改 0x2000C（§2 B2、§5 偏差2）；运行值反例核实留验收（§7.2） |
| P1-3 | A1 把 tick 当毫秒，5.9s 挂住按键 | s19 双文档+函数体反汇编证实 | 每轮 1 tick，60 次轮询 ≈590ms（§2 A1、§5 偏差3） |
| P1-4 | alloc 失败后无条件 free 违反所有权（未命中 free 仍触发时钟重算调度） | clock_alloc/free 失败路径反汇编证实 | BSS 持有标志 0x125578 门控两侧释放（§2 A3、§5 偏差4） |
| P1-5 | 验收清单回滚 profile 不接受 F1 | accept_profile.py:212 反查证实 | §8 文案修正：扩 profile 前离线回滚前提未满足 |
| P2-D3/E2 | busy 分支接错测试仍绿；「仅差 A3」只靠锚点计数 | 内存改坏试验证实 | test_05 补 busy/空 ctx 两边；test_04 归一化指令流比较 + 19 条 A3 插入断言 |
| P2-E1 | 区域白名单比预期宽（整段 boot-info/追加区放行） | 属实 | boot/CRC 区收窄到具体字段；追加头字段在 test_03 独立钉死；**追加文件区整体放行保留**（该区本就是新文件 mic_effect.bin 的合法内容，由 test_03 恒等+头字段钉死补偿） |
| P2-E3 | test_08 对照官方源包，不翻转也有 16 个区外差异 | 属实 | 改 HAL5 对照 + 先断言干净通过 + 翻转后唯一越界=0xb00 |
| P2-F | 报告身份键错误 + 段尾笔误 0xE5447 | 属实 | 键名改 lfix_source 系列；段尾重算 0xE5446（§3） |

codex 合并前清单中三项运行时核实（场景入口实测采样率、start 后通道数运行值、
双产物 busy/改目标路径真机验证）属真机验收范畴，已并入 §7/§8，不在离线范围。

## 10. 纪要（进展时间戳）

- 03:00 开路：worktree sim-lfiximpl（main eedb3f8）；解包官方镜像到 analysis/v1.1.8
  （app.bin sha `18f3d573…` 校验通过）；lfix 方案 + HAL5 构建链复读。
- 03:0x-03:2x 锚点复核（§4 表）：db3c 标志布局、0x06064efc 实调与任务路径、
  clock_alloc/free 签名、node_ioctl 同构、ADC 通道数重算块；0x606ebe0 三调用者
  （异步消费者链存在）。call.xl（6B call）形态导致的首轮扫描漏报已备注。
- 03:2x-03:3x 三个载荷源 + l1_image/builder 接入；A3a 破坏 r0=uuid 的 bug 自检
  发现并补重载；data_spans 排序 bug 修正（stock 变体零回归）。
- 03:4x-03:5x 初版双产物构建 + 三方逐字节 diff 审计；test_hearing_aid_lfix_image.py
  14 项落地；全绿；commit 9f9575f。
- 03:55 codex 实施审开跑（gpt-6.2-astra，审 9f9575f）。
- 04:0x codex 出五条 P1 + P2：逐条反汇编核实（B1 唯一入口/B2 0x2000C/A1 tick/
  A3 所有权/回滚 profile 全部成立），载荷源与测试面修正（§9 表）。
- 04:1x-04:3x 重建双产物并重钉 SHA（F1a `73b45e65…` / F1b `f1657668…`）；
  test_04 归一化比较（difflib 对齐两次失败后改为归一化文本流比较）；构建报告
  `hearing_aid_lfix` 语义块同步修订版（b1_scene_value_change/0x2000C/tick/持有
  标志）；14/14 + 12 套既有 + units + verify_ufw_integrity 双产物全绿；本报告
  定稿。
