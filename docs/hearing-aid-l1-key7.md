# hearing-aid L1 双击多功能键接驳（路 L1KEY / sim-l1key）

2026-11-17。本路把「双击多功能键」从「音频模式循环」改接到 **hearing-aid（mic_effect 辅听）开关**。
用户意图：双击 → 开/关 hearing aid mode。离线候选 `--hearing-aid-l1-key7`，UNTESTED 不刷机，
不构成「助听器可用」声明。

证据等级：**[静态]** = 指令锚定反汇编/字节扫描；**[SDK]** = 参考 SDK 源；**[镜像]** = 官方镜像字节；
**[文档]** = 既有 docs 钉死条目；**[实测]** = 离线单测/构建器断言；**[推断]** = 上述链推出。

## 0. 一句话结论

codex 参谋三选一结论：**采纳 (a)——双击直接替换成 HA toggle**。同时参谋逐字节复核发现两处既有
接驳的前提错误，本路一并修正：

1. **「[ctx+112/116/120] = 模式应用函数槽」是误读**：这三槽实为 **anc_on/anc_trans/anc_off 提示音
   资源名字指针**（0x0600c2aa 返回静态结构 0x060a379c/0x060a387c，+112→"anc_on.*"、+116→
   "anc_trans.*"、+120→"anc_off.*"）；`0x0600dae4(fn, flag)` 走的是提示音播放+回调路径，不是「关
   ANC 通透」。旧路 T/Y 桩「取 [ctx+120] OFF 应用函数 → 0x0600dae4 → 关 ANC 通透」只播了 anc_off
   提示音，**没有真正关 ANC**。
2. **open 载荷跨 call 丢 stream**：`mic_effect_open.S` 在 `add_thread` 返回 r0=0 后没把 r4 里的
   stream 重载回 r0，就调 set_scene/set_notify/start——三个 API 收到的 r0=err 而非 stream
   （pc_mic 模板每个 API 前都 `r0=[r4+0]` 重载）。旧路 O 仿真对这些 API 是「分别直调」，没覆盖串
   联载荷的寄存器流，未捕获此 bug。

本路交付：①修正 open 载荷（r0 重载 ×3 + 失败时钟回收）；②新增 cmd7_toggle 桩 + cmd 7 入口窗口
接驳；③`--hearing-aid-l1-key7` 离线候选镜像 + 独立校验；④全部既有单测几何/摘要随修正更新，
`check_local.py --units` exit 0。

## 1. codex 三选一

调用 `codex --yolo --model gpt-6-astra exec -C <worktree>`（结论 final.md 存 ~/tmp，不入库）。
基线 main 3b93d23、官方 app.bin sha `18f3d573…`、codec pi32v2-isa @3608ed19 全部吻合。

| 方案 | 满足意图 / 改动面 | ANC 互斥及既有接驳 | 取舍 |
|---|---|---|---|
| **(a) 双击 HA toggle** | 一次双击开、再双击关；替换 cmd 7 动作 | 开 HA 前必须完整退出 ANC；ANC 键保留原 OFF/ON/TRANS 语义 | **采纳** |
| (b) ANC ON→TRANS→HA→OFF 四态 | 把二态需求扩成四态 | 与 ANC 键职责重叠；TRANS=HA 与 HA 独立档语义重复；cmd 7 当前驱动的是音频模式(key 22)不是 ANC 模式(key 16)，要循环 ANC 状态需重接驳到 db3c/0x06018b60 那台状态机 | 收益不足 |
| (c) 保留双击、换手势 | 三击挤掉语音助手；四击可用 | 互斥工作与 (a) 相同 | 备选（用户愿意改手势时选四击） |

参谋对 cmd 7 语义的纠正：cmd 7 当前是**音频模式循环**（config key 22=音乐/游戏/电影，模式字节
0x124a04，取值 {1,3,4}，1↔3 往复），**不是 ANC 模式循环**（ANC 是 key 16 + b[[QCY+500]+33]，由
物理 ANC 键 cmd 11 + App 0x17 驱动）。任务卡「ANC 模式循环」是措辞偏差，实际是音频模式。

## 2. 修正既有证据（两条，均本路逐指令复核）

### 2.1 模式应用函数槽 → 提示音资源名指针 [静态]

`0x0600c2aa`（ANC ctx getter）返回的是**静态结构** 0x060a379c（[QCY+348]==1）或 0x060a387c
（否则），不是 [QCY+500] 的 zalloc 572B 动态对象。该静态结构 +112/+116/+120 存的是提示音资源名：

```text
[0x060a387c + 112] = 0x0609c7fd → "TON1/tone_en/anc_on.*"
[0x060a387c + 116] = 0x0609c813 → "TON1/tone_en/anc_trans.*"
[0x060a387c + 120] = 0x0609c82c → "TON1/tone_en/anc_off.*"
```

`0x0600db3c` 的 ON/OFF/TRANS 窗口 `call 0x0600c2aa; r0=[r0+112/116/120]` 加载的是这些**名字指针**，
随后 `0x0600dae4(fn, flag)`：flag!=0 → `0x0600dab6`（r1=0x0602767c 回调，经 `0x0600d95e` 按名字
装载提示音对象 + `0x0600da64` 播放）；flag==0 → `0x0600dad8`（同样走提示音）。**真正的 ANC 模式
应用**是 0x0600db3c 早于窗口的 `b[ctx+5]=target`(0x0600db74) + `b[ctx+33]=target`(0x0600db76) +
过渡标志 `b[ctx+29]=1`(0x0600db72)，后续 anc_task 续行提交。

结论：旧 trans_branch 桩「取 [ctx+120] → 0x0600dae4 → 关 ANC 通透」只播 anc_off 提示音，
**不写 b[ctx+33]=OFF**。本路的 toggle 桩改走完整 `0x0600db3c(1, 1)` OFF 请求，不复用旧桩形状。

### 2.2 open 载荷跨 call 丢 stream + 失败时钟回收 [静态+实测]

`mic_effect_open.S` 修正两处（源内注释 + 单测 test_hearing_aid_payload 8 条断言钉死）：

- **r0 重载 ×3**：`add_thread` 返回 r0=err 后，set_scene/set_notify/start 各补 `r0 = r4`
  （pc_mic 模板 `r0=[r4+0]` 同款）。0x06074a5e 入口 `r4=r0+80` 写字段，传 r0=0 会访问低地址。
- **失败时钟回收**：get_pipeline_uuid("mic_effect") 命中即 clock_alloc(24MHz)，parse/add_thread/
  start 任一失败都补 `event_notify(11,"mic_effect")`（= 0x060057e6 的 CLOSE_PLAYER 分支 →
  clock_free），旧版失败路径只 release 或直接返回，24MHz 时钟申请不闭合。

载荷从 41 指令/134B 变为 **48 指令/156B**。close 载荷（20 指令/64B）逐 call 重载正确，未改。

## 3. 接驳：cmd 7 → HA toggle

### 3.1 窗口（8B，整窗替换）

cmd 7 分支体入口 **0x06025404**（handler 0x060252c6 动作跳表 0x06025382 表字节 0x40 的落点；
第二项默认绑定 flash 0x0609e250 `07 02 07`）。原 8B = `add.s14.rri r1=r8+1873; li.u8 r0=0;
li.u8 r4=0`（音频模式循环的 publish-name 准备）→ 替换为 `li.u32 r6=<桩>; goto r6`。

```text
0x06025404: 01e1518740204420  →  c6ff<桩地址>06 d600   (r6=<toggle 桩>; goto r6)
```

落点扫描（本路全 app 线性解码，279,401 条指令 / 51,710 个 pc-relative 分支落点 / 27,695 个唯一
目标，与路 T/Y/AC 同法）：窗口内部零落点；唯一进入 = 跳表 tbb 字节 0x40（tbb 不是 pc-relative
分支，不在扫描内——已知入口）。

### 3.2 toggle 桩（`tools/hearing-aid/mic_effect_toggle_hook.S`，13 指令/38B）

```text
cmd7_toggle:
    r0 = MIC_EFX_SLOT; r4 = [r0+0]
    if (r4 != 0) goto do_close
    r0 = 1; r1 = 1; r6 = 0x0600db3c; call r6   ; 请求关 ANC（OFF，flag=1）
    call mic_effect_open                        ; 开辅听
    goto done
do_close:
    call mic_effect_close                       ; 幂等关辅听
done:
    r4 = 1; r6 = 0x0602530c; goto r6            ; handler 返回「已处理」
```

- **ANC 互斥**：开辅听前先 db3c(1,1) 请求关 ANC（键循环邻域三入口 0x06018c8e/ca4/cba 同款
  flag=1）。注意 db3c 是「请求」路径，返回 ≠ anc_task 已完成硬件退出——完成时序真机前仍是开放项
  （§5 边界 2）。
- **栈平衡**：桩不经 call 进入（窗口是 goto），不 push/pop；调用的 db3c/open/close 各自平衡；
  最终 goto 0x0602530c 复用 handler 的 `push.rets_rl l=9` 返回帧（r0=r4; pop.pc_rl）。
- **返回值**：r4=1（已处理），不把 open 的 -22 当事件返回值。

### 3.3 数据槽与模式字节

- HA 状态用独立 RAM 槽 **0x125574**（路 CT 五类死洞证据钉死，boot BSS 清零区内），open 写/
  close 清/toggle 读——「0 → stream → 0」自洽支撑二次 toggle。
- **模式字节 0x124a04（key 22）在 (a) 下不再由双击写**，但该字节仍被 App 音频面板/恢复链消费
  （0x06026100/0x06026124/0x06026148/0x060261a0 族），故保留原音频含义，不给 HA 借编码；
  HA 也不新增 key 16 的 wire 编号。

### 3.4 与既有 ANC 键接驳的关系

本候选**不接驳 ANC 键的 TRANS/ON/OFF 窗口**（组合段只含 open+close+toggle，无 trans/leave 桩）：
ANC 键保持原 OFF/ON/TRANS 语义。旧路 T/Y「TRANS=HA」接驳保持在其自身候选开关
（--hearing-aid-l1-close/slotram）里，但该接驳的「关 ANC 通透」语义已被 §2.1 否定——如后续要
「ANC 键请求时先关 HA」，需按 §5 边界 2 钉死完成信号后另做，不在本路范围。

## 4. 离线验证（可复跑）

- 载荷/桩单测：`python3 tools/test_hearing_aid_payload.py`（8 OK，含 r0 重载 + 失败时钟回收新断言）、
  `test_hearing_aid_close_payload.py`（6 OK）、`test_hearing_aid_key7_image.py`（6 OK，桩形状/
  目标/toggle 几何）。
- 既有单测几何/摘要随 open 载荷修正同步更新：close_image（open 变体 sha
  `4ffd736e…`、close 段 388B）、slotram_image（388B）、r4_keyhook_image（offset 388）、
  candidate_world_checks + analyze_image_trace 的候选 sha/plain 长度/字节账（l1_slotram
  `6f267a2e…` 493B、r4_keyhook `bfc3f681…` 574B）。
- 候选镜像构建（`--attenuation-db 0 --hearing-aid-l1-key7`）：
  `H3S_0.1.1.8_tones_minus0dB_HEARING_AID_L1_KEY7_OFFLINE_UNTESTED.ufw`，sha256
  `7465cd435e79700c7f09ed60bc71e6286a3cdca121cd428882986a5d0d1839ac`；319 flash 字节差
  （窗口 8 + fence 3 + 追加段 open+close+toggle 258B 密文 + CRC 链），`verify_l1_image`
  全镜像精确差异断言 + `verify_ufw_integrity.py` 全过（mic_effect.bin 397B 条目 JLFS CRC）。
- `python3 tools/check_local.py --units` exit 0。

## 5. 诚实边界（必须随候选一起读）

1. **离线候选 UNTESTED，不刷机**。本路交付「字节与静态证据一致 + 四层 CRC/JLFS 自洽 + 逐字节
   差异精确可控」三件事，不是任何形式的运行/出声验证。
2. **ANC OFF 完成时序未钉死**：db3c(1,1) 返回 ≠ anc_task 硬件退出完成；open 与「ANC 已退出」的
   先后在真机前是开放项。旧路 O 仿真前沿未推进到 0x0600db3c，接驳后的完整路径（桩+载荷在按键
   handler 里）同样无执行证据。
3. **set_scene(16) 仍是 SDK 枚举值**（QCY 构建枚举未证）、noisgate 未配对、单线程形状——继承
   路 L 既有边界。
4. **改 cmd 7 = 改所有绑定动作 7 的手势**（默认表只有双击 [7,2,7]；key 48 存档可覆盖其他手势到
   动作 7）；本候选不改 flash 默认表，只改动作 7 的分支体。
5. **既有两个发现影响旧候选**：旧 --hearing-aid-l1-close/slotram/r4-keyhook 的 TRANS=HA 桩
   「关 ANC 通透」只播提示音（§2.1），其字节仍能复现但语义不成立——本路不改它们的接驳窗口，
   只在其组合段内的 open 载荷同步修正（几何/摘要已更新）。
6. **调用面穷举复核**（任务「核实+补齐」）：get_pipeline_uuid 0x060057e6 的 20 个直接调用、
   jlstream_pipeline_parse 0x06074938 的 10 个调用、li.u32 0x9c2d 仅 2 处（0x06005986/0x0602ead4），
   与路 I 穷举一致；hearing_aid_player_open/mic_effect_player_create 激活层仍不在固件，无现成
   入口可接——接驳 = 复用修正后的 open/close 载荷，不是调用现成函数。

## 6. 证据锚点表

- cmd 7：handler 0x060252c6 / 跳表 0x06025382（表字节 0x40）→ 入口 **0x06025404**（原 8B
  `01e1518740204420`）；模式字节 0x124a04（key 22）；返回点 **0x0602530c**（r0=r4; pop.pc_rl l=9）。
- ANC：dispatch **0x0600db3c**（r0=1 OFF / r1=flag）；ctx getter 0x0600c2aa（静态结构
  0x060a379c/0x060a387c）；+112/116/120 → "anc_on/anc_trans/anc_off" 提示音名（0x0609c7fd/
  c813/c82c）；0x0600dae4=提示音 apply 包装（0x0600dab6/0x0600dad8→0x0600d95e/0x0600da64）。
- HA：get_pipeline_uuid 0x060057e6 / parse 0x06074938 / add_thread 0x06074ec6 / set_scene
  0x06074a5e / set_notify 0x06074e64 / start 0x06074ffe / release 0x06074204 / event_notify
  =0x060057e6（event 11 CLOSE_PLAYER → clock_free）；数据槽 0x125574。
- 载荷：open 48 指令/156B（修正）、close 20 指令/64B、toggle 桩 13 指令/38B；追加段 258B，
  段 sha `3aaf81f4…`；toggle 桩地址 0x060E3150、fence 上限 0x060E3176。
- 工具：tools/hearing-aid/mic_effect_toggle_hook.S、tools/test_hearing_aid_key7_image.py、
  hearing_aid_l1_image.py（key7_toggle 组合/接驳/校验）、build_quiet_candidate.py
  （--hearing-aid-l1-key7）。
