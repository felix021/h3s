# 辅听 lfix 修复候选 F1（HAL5+lfix）实施报告 — 路 LFIX-IMPL / sim-lfiximpl

2026-11-19 03:00–04:0x。任务：按 [hearing-aid-lfix-plan.md](hearing-aid-lfix-plan.md) §4
已收口方案构建候选 **F1 = HAL5 合一基座（9ad0f088…，已在真机）+ lfix 修复载荷**，产出
可刷写 ufw 与离线自验报告。**只构建不刷写**；两候选 UNTESTED。

前置：main eedb3f8（含 HAL5 全链与 lfix 方案）；worktree sim-lfiximpl。

## 0. 一句话结论

F1 按方案落地为**双产物**（plan §4-C 双镜像裁决，见 §5 偏差①）：

| 候选 | 构成 | sha256 | 追加段 |
|---|---|---|---|
| **F1a**（默认，诊断档） | HAL5 全链 + A1+B1+B2 + **A3 时钟压舱** | `869b7f9b55498c1b6b208502ba2e51d01555dcceff97edd04e37a947c639302d` | 994B（+120B） |
| **F1b**（对照镜像） | HAL5 全链 + A1+B1+B2（无 A3） | `0274431f50f7eac7171594d6faf6b3903b5b31cc851d205d80d62a4b9e6fd514` | 934B（+60B） |

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
`tools/test_hearing_aid_lfix_image.py` 14/14；既有助听 10 套 + full 套 +
`test_candidate_world_checks`（r4 re-pin `2576df46…` 复现）全绿；`check_local.py
--units` exit 0；**HAL5 基座复现逐字节 `9ad0f088…` 不受扰动**（test_09 钉死）。

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
0x060E3074；F1b/F1a 开头相同，仅 A3 块差）。完整反汇编在 build-report.json 的
`hearing_aid_l1.listing`。

### B1 删除 set_scene(stream,10)（−12B）

原 HAL5 open @0x060e30ce..0x060e30da（12B，4 指令）：

```
060e30ce  4016         r0 = r4
060e30d0  412a         r1 = 10
060e30d2  c6ff644e0706 r6 = 101142116      ; 0x06074e64 set_scene 两参
060e30d8  c600         call r6
```

F1b/F1a：**整块删除**（set_scene(16) 调用结束 @0x060e30fa 后直接进入 start 的
r0 重载）。场景保持第 4 步 set_scene(stream,16,0) 的 HEARING_AID；ADC GET_FMT
三态从「10→全局」变为「16→44100」[静态，plan §4-B1]。

### B2 start 返回后显式 SET_CHANNEL(1)（+22B）

新块 @F1b 0x060e3108..0x060e311e（F1a 0x060e311e..0x060e3134 同形）：

```
060e3106  8053         if (r0 != 0) goto 38      ; start 失败检查（原有）
060e3108  4016         r0 = r4                   ; 重载 stream（跨 call 纪律）
060e310a  41e00011     r1 = 4352                 ; 0x1100 根/SOURCE
060e310e  c2ff07000200 r2 = 131079               ; 0x20007 NODE_IOC_SET_CHANNEL
060e3114  4321         r3 = 1                    ; 通道数量（mono）非模式枚举
060e3116  c6ffc64e0706 r6 = 101142214            ; 0x06074ec6 node_ioctl
060e311c  c600         call r6
060e311e  c0ff74551200 r0 = 1201524              ; 记槽（原有后续）
```

按 codex 修订：**必须 start 后设**（ADC 开链 0x6032b6c-0x6032b94 按 en_map 重算
b[priv+28]，start 前设会被覆盖——本路已重验该重算块反汇编）；arg=1 是数量语义。
返回值不检查：协商默认 mono 时为无害重申。

### A1 open 前 ANC 退出等待（+48B：等待块 44B + 取消路径 4B）

新块 @F1b 0x060e3080..0x060e30ac（插在槽守卫与 get_pipeline_uuid 之间）：

```
060e307e  a553         if (r5 != 0) goto 166     ; 槽守卫（原有）
060e3080  c6ffc0651200 r6 = 1205696              ; 0x1265C0 QCY 结构基
060e3086  d1ec644f     r4 = [r6+500]             ; ANC ctx 指针
060e308a  0450         if (r4 == 0) goto 32      ; ctx 未建（db3c 空转）→跳过
060e308c  4d3c         r5 = 60                   ; 上限 60 次
060e308e  c6fffc4e0606 r6 = 101076732            ; 0x06064efc 任务延时
060e3094  50ee4d01     r0 = b[r4+29] (u)         ; 过渡标志
060e3098  8044         if (r0 != 0) goto 8       ; 未清 → 轮询
060e309a  50ee4102     r0 = b[r4+33] (u)         ; 目标模式
060e309e  00f80502     if (r0 == 1) goto 10      ; 已到 OFF → 放行去 r0=15
060e30a2  fd3f         r5 += -1
060e30a4  2542         if (r5 == 0) goto 132     ; ~600ms 超时 → 取消
060e30a6  402a         r0 = 10                   ; 10ms
060e30a8  c600         call r6                   ; 0x06064efc(10)
060e30aa  f794         goto -24                  ; 回轮询头
060e30ac  402f         r0 = 15                   ; get_pipeline_uuid（原有）
```

超时取消路径（新 label open_err_cancel）@F1b 0x060e312a：

```
060e312a  102a         r0 = -22
060e312c  048e         goto 28                   ; → open_ret（弹栈返回）
```

退出条件=过渡清零**且**目标仍为 OFF（后者防等待期间用户按 ANC 键把目标改走）。
超时=前置条件未成立（codex 裁决）：取消本次 open，槽保持 0，桩见 r0≠0 跳过
apply_mpo/apply_eq，用户再按一次即重试。db3c(1,1) 仍由 toggle 桩先行调用（顺序
不变）；A1 在 open 载荷内做，对调用方透明。

### A3 时钟压舱（仅 F1a：open +36B / close +14B / 名串 10B）

A3a（uuid 命中后、parse 前）@F1a 0x060e30c2..0x060e30d6：

```
060e30be  2056         if (r0 == 0) goto 172     ; uuid==0 → open_err_inval
060e30c0  0416         r4 = r0                   ; r4 = uuid（原有）
060e30c2  c0ff70310e06 r0 = 101593456            ; "lfix_hold" 名串地址
060e30c8  c1ff0020a107 r1 = 128000000            ; 128MHz
060e30ce  c6ff78090006 r6 = 100665720            ; 0x06000978 clock_alloc
060e30d4  c600         call r6
060e30d6  4016         r0 = r4                   ; 跨 call 重载 uuid（parse 要）
060e30d8  c1ff6dd00000 r1 = 53357                ; pipeline_parse（原有）
```

A3b（open 失败共享尾，event_notify(11) 之后）@F1a 0x060e315e：

```
060e315c  c600         call r6                   ; event_notify(11)（原有）
060e315e  c0ff70310e06 r0 = 101593456            ; "lfix_hold"
060e3164  c6ff2c570006 r6 = 100665612            ; 0x0600572c clock_free
060e316a  c600         call r6
060e316c  102a         r0 = -22                  ; （原有收尾）
```

A3c（close 尾部）@F1a close 0x060e31b6：

```
060e31b4  c600         call r6                   ; event_notify(11)（原有）
060e31b6  c0ff70310e06 r0 = 101593456            ; "lfix_hold"
060e31bc  c6ff2c570006 r6 = 100665612            ; clock_free
060e31c2  c600         call r6
060e31c4  4020         r0 = 0                    ; （原有收尾）
```

名串 rodata `"lfix_hold\0"` @0x060e3170（open 代码尾、close 之前；与 "mic_effect"
不同名，不会被 CLOSE_PLAYER 分支顺带释放）。所有权纪律（codex）：重复 open 被
槽守卫挡住不二次申请；uuid==0 路径在 A3a 之前无需释放；申请后任一步失败走
A3b；成功 open 由 close 的 A3c 释放——每轮恰一次配对。alloc 失败（堆尽）不记账
直接继续；free 未命中名=无害 no-op（0x0600572c 逐名遍历，本路反汇编核实）。

### 寄存器/调用约定说明

A1 循环的 r4(ctx)/r5(计数)/r6(延时函数) 跨 `call r6` 存活，依赖 pi32v2 ABI 的
r4-r6 callee-saved（0x06064efc 自身只压 {rets,r4}，其被调链同守约）——与既有
载荷对每个固件调用的同一信任假设。A3a 破坏 r0=uuid 后必须 `r0 = r4` 重载
（L1KEY 跨 call 重载纪律），实现时该 bug 由自检发现并修正（§7 偏差④）。

## 3. 预算账

追加段可用区 = flash 尾 padding [0xE5073, 0xE5FF8) 3981B − 33B JLFS 头/标记 =
**3948B**（HAL5 先例口径）。HAL5 已用 874B，余 3074B。

| 项 | F1b | F1a |
|---|---|---|
| B1 删 set_scene(10) | −12B | −12B |
| B2 SET_CHANNEL(1) | +22B | +22B |
| ioctl 前 stream 重载（防御，语义冗余） | +2B | +2B |
| A1 等待块+取消路径 | +48B | +48B |
| A3a（含 uuid 重载） | — | +22B |
| A3b 失败尾释放 | — | +14B |
| A3c close 释放 | — | +14B |
| 名串 rodata | — | +10B |
| **open 载荷小计** | **216B（+60）** | **252B+10B（+106）** |
| close 载荷 | 64B（=HAL5） | 78B（+14） |
| **段合计（vs HAL5 874B）** | **934B（+60）** | **994B（+120）** |
| **剩余预算** | 3014B | 2954B |

实耗 60/120B，均在 plan §4「<400B」预算内（F1a 实耗为预算的 30%）。

## 4. 锚点复核记录（本路对 plan §8 的独立反汇编复核）

全部以官方 app.bin（sha `18f3d573…`）反汇编重验：

| 锚点 | plan 断言 | 本路复核 |
|---|---|---|
| db3c 0x0600db3c ctx/标志 | ctx=[QCY+500]；写 b[ctx+5]/[+29]/[+33] | ✓ @0x0600db3e-76：`r7=0x1265C0; r3=[r7+500]`；同模早退读 b[ctx+33]；置位序 = 先 [+29]=1 后 [+33]=目标（**目标立即写，非完成态**——等待以 +29 为准，+33 是护栏） |
| 延时 0x06064efc | ANC 退出链自用，任务上下文 | ✓ 退出链 0x0606ec18/ec58/eca2 以 call.xl 实调 (1)/(2)/(100)；函数体 = 上下文检查→任务路径尾调 0x06064e80 睡眠（参数 ms 尺度） |
| clock_alloc 0x06000978 | (名, 频率) 记账 | ✓ `r5=r1(频率); r6=r0(名)`，malloc 44B 挂链表 @0x125340 |
| clock_free 0x0600572c | 按名摘链 | ✓ 逐名 strcmp；未命中=不摘不 free，仅走重算调度 0x060008ca（无害 no-op） |
| node_ioctl 0x06074ec6 | (stream,id,cmd,arg)，0x1100=root/SOURCE | ✓ `r0=[r0+40]` 取 root pipeline → worker 0x06074e7e（0x1100 特例分支 @0x06074e92）；与已在真机出声的 SET_PRIV_FMT 调用同构 |
| ADC 通道数字节重算 | 0x6032b6c-94 按 en_map 重算 b[priv+28] | ✓ 清零 @0x06032b6c → 逐通道测试 en_map 位 → 每使能通道 `b[+28]+1`（0x06032b8e-92）——B2「start 后设」的根据 |
| SET_CHANNEL=0x20007 | [SDK] | 固件侧两个通用 fmt 分发器（0x0606a526/0x60c43d0）均有 0x20007 比较（它们按模式枚举 17/18/20/37/38 解读 arg——**B2 只以 0x1100 发向 source 节点，不广播到这些类**） |

**本路未能重验、沿 plan 边界的两项**（诚实记录）：b[ctx+29] 的清零站点（plan
称退出链清零、先后未钉；本路找到 0x606ebe0 的 3 个调用者证明异步消费者链存在，
但清零指令本身未定位——A1 的 600ms 超时兜底正是为该不确定性设计）；ADC 自身
SET_CHANNEL「直存字节」消费点（plan/codex 称 [静态] 已钉；B2 与重算默认值一致，
即使存点行为不同也无害）。

## 5. 与 plan 的偏差（逐条）

1. **A3 未做成运行时可开关，出双产物**。plan §4-C 优先「复用 key30/key31 家族
   参数通道」做运行时开关，但那需要 keystore_hook 扩 key 32 + App 通道 + 三层
   钉死测试面（R4_INSN_IDS/android-query 白名单/工件重建）——改造面远超「一次
   刷机对照」的收益，plan 自身预留了「双产物更划算」出口，照做。**F1a=默认
   （含压舱，诊断档）**，`--lfix-no-hold` 出 F1b。
2. **A3a 位置从「ANC 等待通过后」微调为「uuid 命中后」**（仍在 parse 之前）。
   原因：uuid==0 失败路径在 plan 排布下会走到未申请先释放的边角；放在 uuid
   之后，uuid==0 走 open_err_inval 自然无需释放，错误路径无需新增分支。DVFS
   语义不变（Σ 同为 base24+mic24+hold128，plan 已注明每轮 open 的首次切换
   不可避免）。
3. **B1 计 −12B（plan 估 ~14B）**；B2 计 +22B（plan 估 ~24B）——估算宽度差，
   非语义偏差。
4. **实现期自检修正一处**：A3a 调用破坏 r0=uuid 的跨调用不变量（parse 依赖
   r0 仍=uuid），补 `r0 = r4` 重载（§2 A3a 反汇编可见；测试 test_04 的 A3 锚点
   断言 + `_verify_lfix_open` 的重载位置断言双钉）。
5. **F1b 的 ioctl 调用前多一条防御性 `r0 = r4` 重载**（+2B；stock 靠 parse 返回
   值碰巧仍在 r0）。语义等价，选择保留（与载荷其它调用点的重载纪律一致）。

## 6. 离线自验清单（结果）

| # | 项 | 结果 |
|---|---|---|
| 1 | 镜像结构完整：UFW 头/条目/boot-info/JLFS 四层 CRC/外层 CRC | ✓ verify_ufw_integrity 双产物 exit 0 |
| 2 | 与基座 diff 逐字节枚举，全部落在预期补丁区 | ✓ test_02：F1a/F1b vs HAL5、F1a vs F1b 三组，diff 仅在 UFW 外层 CRC 6B + boot-info 32B + 四对嵌套 CRC + KEY7 窗 8B + fence 立即数 4B + R4 注册表字 4B + 追加文件区 |
| 3 | 镜像内追加段 == 载荷源独立重汇编（逐字节） | ✓ test_03（两遍定址后 code_sha/listing 对照） |
| 4 | 「仅差 A3」契约 | ✓ test_04：F1b close==HAL5 close 逐字节；F1b 无 clock 锚点无名串；F1a open 恰含 {alloc×1, free×1, 名址×2, 128MHz×1}，close 恰含 free+名址；名串字节 `6c 66 69 78 5f 68 6f 6c 64 00` 在位 |
| 5 | A1 循环接线（从镜像独立解码） | ✓ test_05：轮询读 +29/+33、超时分支落 open_err_cancel、重试 goto 回轮询头、放行分支落 r0=15、延时调用 0x06064efc |
| 6 | 预算 | ✓ test_06：F1a 段尾 0xE5476 / F1b 0xE5447，均 < 0xE5FF8 |
| 7 | 反例判别力 | ✓ test_08：预期区外翻 1 字节，区域审计必红 |
| 8 | 基座不受扰动 | ✓ test_09：HAL5 复现 sha `9ad0f088…` |
| 9 | 既有链零回归 | ✓ 助听 11 套 + world_checks（r4 `2576df46…` / l1_slotram 复现）+ android-query test.sh + check_local --units 全绿 |
| 10 | 形状/目标钉死 | ✓ 构建器 `_verify_lfix_open/close`：LFIX_*_INSN_IDS 全形 + li.u32 载荷序逐位 + A3 重载位置；encode→decode 往返逐条零 diff |

**不做**（本路红线）：真机刷写、UpgradeGate 登记（候选过验收后主协调再登记）、
probe 五文件与 analyze_image_trace.py。

## 7. 诚实边界

1. **静态补丁无法验证运行时行为**：A1 等待是否如期放行（依赖退出链清 +29）、
   B2 覆盖是否消左耳噪声、A3 是否抑制运行期振荡——全部留给真机刷写后用户实测。
   b[ctx+29] 清零站点未定位（§4），若该标志实际永不清零，A1 将每次 600ms 超时
   后取消 open（表现为「双击有提示音但辅听不开」）——这本身是可观察的判别信号，
   不是不可诊断的挂死。
2. A3 判读边界（plan §4-A3 codex 裁决）：每轮 open 首次 24→160M 切换不可避免；
   F1a 不掉线只判「高频保持相关」（C1 时钟扰动 vs C5 算力不足不分）；长期续航/
   温升未测，A3 是短期诊断档非最终方案。
3. 消费链零执行证据继承 HAL5 §7（key30/key31 消费链、非事务性等全部照旧）。
4. F1 相对 HAL5 的预期差异面：open 多出 ≤600ms 的 ANC 等待（正常应 <100ms 级）、
   场景字节 16、start 后一次 node_ioctl；其余行为同 HAL5。

## 8. 真机刷写验收清单（候选过验收后另行授权；此处只列标准）

**验收标准 = open→运行→close→再 open 全程稳定 + 左耳无刺耳噪声**（plan §4-C）。
建议顺序：先刷 **F1b**（无压舱，验 A1+B1+B2 的噪声面）→ 观察断联是否仍存在 →
再刷 **F1a**（加压舱，验崩溃面）→ 对照归因。每轮按三时间窗分开记录（codex）：

| 时间窗 | 记录项 |
|---|---|
| open 当场 | 双击→「关降噪」提示音→出声延迟（含 A1 等待）；是否断联及时间差；左/右耳听感（刺耳噪声有无） |
| 持续运行（≥3 个 4s timer 周期） | 断联/噪声/音量爬升；F1a 与 F1b 的差别 |
| close 后再 open | 第二次 open 是否断联（原故障场景）；循环 ≥5 次；超时取消是否出现（双击有声无输出=可疑 +29 不清零，见 §7.1） |

对照变量：蓝牙通话中开辅听（③）、提前手动关 ANC 再开（⑤）、纯 open/close 循环
（④）沿 plan §5 判别表。恢复目标=b79（既有 USB profile）；HAL5→F1 为增量包，
预期差异同 HAL5（ANC 键官方三态、提示音不衰减）。

## 9. 纪要（进展时间戳）

- 03:00 开路：worktree sim-lfiximpl（main eedb3f8）；解包官方镜像到 analysis/v1.1.8
  （app.bin sha `18f3d573…` 校验通过）；lfix 方案 + HAL5 构建链复读。
- 03:0x-03:2x 锚点复核（§4 表）：db3c 标志布局、0x06064efc 实调与任务路径、
  clock_alloc/free 签名与 no-op 语义、node_ioctl 同构、ADC 通道数重算块；
  0x606ebe0 三调用者（异步消费者链存在）。call.xl（6B call）形态导致的首轮
  扫描漏报已备注。
- 03:2x-03:3x 三个载荷源 + l1_image/builder 接入；A3a 破坏 r0=uuid 的 bug 自检
  发现并补重载（偏差④）；data_spans 排序 bug（name 段地址最低）修正——该 bug
  只影响新增 span 顺序，stock 变体原本有序故零回归（既有 11 套全绿佐证）。
- 03:4x-03:5x 双产物构建 + 三方逐字节 diff 审计（0xac29c=R4 注册表字随段长
  变化，全落预期区）；test_hearing_aid_lfix_image.py 14 项落地；check_local
  --units + android-query 全绿；HAL5 基座复现钉死。
- 04:0x 本报告定稿；codex 审实施待跑（见下）。
