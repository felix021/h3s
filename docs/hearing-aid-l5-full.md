# 助听 L5「全功能合一」候选：L1+L2+L3+L4 单固件（路 HAL5 / sim-hal5）

2026-11-18。任务：四条助听功能线（L1 双击 toggle / L2 MPO 镜像 / L3 key30 MPO 热更 /
L4 key31 EQ 十段热更）各自收口为独立候选、从未合一；用户真机验证前需要一份「全功能
合一」候选固件。本路产出**离线构建 + 审计**，不刷机、不连真机；候选 UNTESTED。
前置：main 3b563df（hal2/hal3/hal4 全链已 merge）。

## 0. 一句话结论

codex 参谋（gpt-6-astra，`~/tmp/codex/202611180021-hal5/final.md`）判：**采纳方案 B
精简版——新开关 `--hearing-aid-full` 只做组合/命名/审计层，固件字节必须等于既有组合
`--hearing-aid-l4-eq --hearing-aid-mpo M`（零新增载荷）**。理由：L4 的双击桩本身已是
L1 桩的累积超集（open 成功 → apply_mpo → apply_eq 的链现成存在），合一在代码侧没有
任何要写的东西；full 的收益是「不漏 L2、优先级语义、固定验收入口」变成机器可检查的
约束。本路交付：

1. **互斥面钉死**（§1）：三个 toggle 桩接驳同一 8B 窗口，但互为超集而非竞争——
   L4 桩 = L1 桩 + open 失败门 + apply_mpo + apply_eq。
2. **构建器** `--hearing-aid-full`（§2）：要求显式 `--hearing-aid-mpo M`（0.3<M<120，
   与 key30 消费范围一致）；拒 demo / l1-key7 / eq-elf；产出
   `H3S_…_HEARING_AID_FULL_MPO<M>_OFFLINE_UNTESTED.ufw`；build-report 新增
   `hearing_aid_full` 键承载优先级语义（§4）。
3. **独立分桶审计**（§5）：从最终 .ufw 解码（不 import 构建器），每个变化字节恰归
   一桶（L1 基座/L3 消费/L4 消费/共享接驳/L2 参数/封装），确定性区域逐值复核。
4. **规范候选**（§6）：M=95，sha256 `9ad0f0881a7996cb4dea81477d8731aa9b4db69a
   6f352603118663d68eda5465`，内容账 916B / 文件账 956B，verify_ufw_integrity 全过。
   r4-keyhook 家族字节不变（world_checks 复测 sha `2576df46…` 仍复现），**无需 re-pin**。

## 1. 互斥面（任务①：钉死）

**三个 toggle 桩是同一接驳窗、互为累积超集，不是三个竞争洞**：

| 桩源（tools/hearing-aid/） | 指令数/字节 | open 分支形状 |
|---|---|---|
| mic_effect_toggle_hook.S（L1） | 13 / 38B | db3c(1,1) → open → done |
| mic_effect_toggle_mpo_hook.S（L3） | 15 / 46B | … open → **失败门** → **apply_mpo** → done |
| mic_effect_toggle_l4_hook.S（L4） | 16 / 52B | … open → 失败门 → apply_mpo → **apply_eq** → done |

- **同一窗**：三者都整窗替换 cmd 7 分支体入口 **0x06025404**（跳表 0x06025382 表字节
  0x40 的落点，原 8B `01e1518740204420`）。这就是构建器里 `--hearing-aid-l3-mpo` /
  `--hearing-aid-l4-eq` 与 `--hearing-aid-l1-key7` 互斥报错的全部原因——「互斥同一
  接驳窗」不是洞不够，是**三个变体桩不能同时占用一个 8B 窗**。
- **链已存在**：L4 桩的 open 路径就是任务要的「open 成功 → apply_mpo → apply_eq」；
  close → 幂等关辅听（原 L1 逻辑）；ANC 互斥 db3c(1,1) 前置与 HANDLER_RET 返回帧
  复用与 L1 桩逐指令一致（L1 桩是 L4 桩的前缀）。**合一零新增载荷。**
- **洞空间**：追加段可用区 = flash 尾 padding [0xE5073, 0xE5FF8) 共 3981B，减 33B
  JLFS 文件头+0xff 标记 = **3948B 预算**；L4 用 874B（open 156 + close 64 +
  apply_mpo 128 + mpo rodata 92 + apply_eq 180 + eq rodata 60 + 桩 52 + keyhook
  142），余量 3074B。full 不增加一字节。
- **L2 是参数层**：写 stream.bin Drc1Eff 76B 的四个拐点字段，不碰任何代码洞，与
  代码侧天然无冲突（构建器此前也无互斥，本路实证组合构建直通）。

## 2. 构建器开关（任务②：方案落地）

`--hearing-aid-full`（tools/build_quiet_candidate.py）：

- **M 必填**：必须同时给 `--hearing-aid-mpo <dB>`（codex B1：100 是演示约定无标定
  依据，显式值便于复现审计；烘进镜像的兜底曲线是候选契约的一部分）。
- **范围门**（复核修正后的顺序与口径）：demo→缺 M→范围→key7 依次检查；先挡非有限/
  越界 double（1e100 不能 OverflowError 崩栈），再按 float32 化后的值判 0.3f < M < 120.0f
  （0.300000001 舍入到 0.3f，须在 CLI 拒绝而非后面 ValueError）——与 key30 消费侧范围
  一致（0x3E99999A < bits < 0x42F00000）；该门只约束 full 合一契约，不改变 L2 独立开关
  原行为。
- **互斥**：`--hearing-aid-demo`（demo 还会改 Eq1Eff/Eq4Eff/Eq5Eff 共享 blob——后两者
  在 MPO 下游输出支路，full 口径是「输出支路维持官方平坦」）；`--hearing-aid-l1-key7`
  （同一接驳窗）；`--eq-toggle-elf`（追加区冲突，沿用既有检查）。
- **隐含链**：full → l4_eq → l3_mpo → r4_keyhook → slotram → close → patch；显式
  l3/l4 冗余声明可接受。
- **命名**：`H3S_0.1.1.8_tones_minus0dB_HEARING_AID_FULL_MPO{M:.9g}_OFFLINE_UNTESTED.ufw`
  （M 文本用 float32 化后的 9 位有效数字，可往返——`:g` 的 6 位会把 119.99999 显示成
  120，而该值烘焙的是不同字节；隐含链的 L4 后缀不叠加）。报告的 `equivalent_build`
  同口径。标准命令必须显式 `--attenuation-db 0`（默认 10 会混入提示音衰减改动——
  codex 提醒的易漏项）。
- **等价契约（单测钉死）**：full 产物字节 == `--hearing-aid-l4-eq --hearing-aid-mpo M`
  组合构建（含 stream.bin 逐字节）。

## 3. L2 兜底 vs key30 运行时值的优先级（任务③语义，必须随候选读）

**key30 运行时值 > L2 烘焙值；L2 是无 key 时的兜底**。执行序（设计语义，静态链路
证据，完整消费链未执行验证）：

| 本次 open 时的 key 30 状态 | 预期 Drc1Eff 曲线 |
|---|---|
| 缺失 / 长度/格式/范围拒 | 保持本次从 stream.bin 装载的 L2 曲线 M |
| 合法 M1，运行时更新成功 | 先装载 M，随后覆盖为 (0.3,0.3)/(M1,M1)/(120,M1) |
| 已开启时修改 key 30 | 不立即重应用；下次 close→open 重读 |
| 前次成功应用 M1，本次 key 30 已失效 | 本次重建先装载 M，跳过覆盖；不承诺保留前次 M1 |
| 参数提交返回错误 / 等待不返回 | 不能仅凭 key 合法推断最终音频状态 |

- **M 无 min(M,M1) 钳位**：L2 的 M 是默认值不是硬上限，合法 M1 可以大于 M。要「烘焙
  M 不可突破」需改载荷（超本路零新增载荷边界）。
- **open 已启动流后才应用参数**：start 返回与首个音频块消费之间的窗口用 L2 烘焙曲线
  ——这正是 L2 兜底的价值。
- **尾点差异**：L2 烘焙 th[2].in = 110.3（M≥110.3 时抬到 M+10.3 保证严格递增）；运行时
  模板固定 120.0f（范围门保证 M1<120）。末段斜率 0 下 in 值不参与输出，两者静态传递
  曲线等价，但 76B 参数包不逐字节相同——记录，不为统一尾点改载荷。
- **App 侧无需新增**：hal3 的 setHearingCfg（key30）/ hal4 的 setHearingEq（key31）
  即 full 候选的参数写入通道；不写 key 时 L2 烘焙值生效。

## 4. 差异归因审计（任务③：每个字节归因）

两种口径都钉死（值全部确定，计数是工件不是公式）：

**内容账（plain 空间 + boot + UFW 外层，M=95 共 916B）**——test_04 独立分桶：

| 桶 | 归属 | 差异字节 |
|---|---|---|
| L1 基座 | KEY7 窗 8B + 追加段 open 156B + close 64B（扣除 20 个 0xff 偶合字节） | 208 |
| L3 消费 | apply_mpo 128B + mpo rodata 92B（模板 76 + 名字 16；扣 7 偶合） | 213 |
| L4 消费 | apply_eq 180B + eq rodata 60B（段包 44 + 名字 16；扣 6 偶合） | 234 |
| 共享接驳 | L4 toggle 桩 52B + keyhook 142B + cmdid 0x30 setter 指针 4B（扣 9 偶合） | 189 |
| L2 参数 | Drc1Eff 四拐点字段中 9 个实变字节（th[1].in/out、th[2].out 各 3；th[2].in 110.3→110.3 不变）+ stream JLFS 头 CRC 4B | 13 |
| 封装 | plain 侧 45（fence 立即数实变 2 + mic_effect.bin 头 32 −0xff 标记偶合 + config.dat 索引/CRC + app 区两对 CRC）+ boot-info CRC/size 8 + UFW 外层 CRC 6 = **59** | 59 |

六行合计 916B。

keyhook 142B 整体归「共享接驳」（功能标签 L3/L4）：key30/key31 分支、共同序言、回执
处理无法按字节拆分，硬拆会因相对跳转/布局耦合说谎；L4 桩 52B 同理不拆「L1 前缀 +
增量」。fence 归封装（覆盖整个追加段，不专属于 L1）。

**文件账（整 .ufw 逐字节 vs 官方，M=95 共 956B）**：内容账 916 + 追加段密文化与
0xff 填充密文的偶合差（扣掉的偶合字节在密文空间大多仍然不同）。两种口径都由
verify_l1_image（构建内）+ test_04/test_05（独立重算）双向钉死。

**增量归因**：official→full 减 official→L4(943B 文件账) = 恰好 L2 桶 13B（内容账），
test_06 断言逐位置相等；L4 字节集是 full 的子集。

## 5. 单测（tools/test_hearing_aid_full_image.py，15 项）

- **CLI 契约**（5）：缺 M / M∈{0.3,0.2,120,121,nan,inf,-5} / demo / l1-key7 /
  eq-elf 全部拒绝且报错文案可辨。
- **工件层**（10，需 firmware+analysis，自动跳过否则）：
  - test_01/02 零新增载荷契约：full == 组合构建逐字节；追加段 code_sha256/地址/fence/
    注册表与 L4-only 完全一致。
  - test_03 命名 + `hearing_aid_full` 报告键（模式/曲线/优先级语义/诚实边界存在）。
  - test_04 **独立分桶审计**：从 .ufw 解码后六桶互不重叠、每个变化字节恰归一桶、计数
    钉死；确定性区域从解码字节重导（KEY7 窗反汇编出 `r6=0x060e331c; goto r6`、fence
    立即数 0x060e33de、注册表字 0x060e3350、四拐点 float 值、76B 其余字节==官方、
    rodata==钉死模板、桩形状==L4_TOGGLE_INSN_IDS、keyhook==独立汇编）；**追加段整段
    874B 与 .S 源重汇编逐字节一致**（字节身份——改一个槽操作数/调用目标并重算 CRC
    也骗不过），**文件级守卫**整文件枚举差异只允许 UFW 外层 CRC 6B + 追加段覆盖的
    flash 区（UFW 头/条目区、bootloader、flash 尾零差异）。
  - test_05 文件账 956 双向核对（builder 记账 vs 独立重数）。
  - test_06 增量恰等于 L2 桶。
  - test_07 尾点两分支（M=100: X=110.3 不变；M=115: X=125.3 且 th[2].in 3 字节入账，
    L2 桶 14B）+ 独立曲线语义（末段斜率 0、过驱动外推 ≤M）。
  - test_08 verify_ufw_integrity 全过。
  - test_09/10 **反例**：桶外翻 1 字节审计必红；L4-only（无 L2 烘焙）喂 full 审计必红
    （拐点值断言拒绝）——审计有判别力，不是「换个文件名就算验收」。

## 6. 规范候选与复跑（任务④⑤）

```sh
python3 tools/build_quiet_candidate.py --attenuation-db 0 --hearing-aid-full \
    --hearing-aid-mpo 95 --output-dir <不存在的目录>
# H3S_0.1.1.8_tones_minus0dB_HEARING_AID_FULL_MPO95_OFFLINE_UNTESTED.ufw
# sha256 9ad0f0881a7996cb4dea81477d8731aa9b4db69a6f352603118663d68eda5465
python3 tools/verify_ufw_integrity.py <产物>/…FULL_MPO95….ufw
python3 tools/test_hearing_aid_full_image.py
bash tools/android-query/test.sh
python3 tools/check_local.py --units        # 含 test_candidate_world_checks（r4 回归）
```

**r4 re-pin 面（任务⑤）**：full 不改 keystore_hook.S 与 r4-keyhook 候选的构建路径；
test_candidate_world_checks 工件层重建 r4 候选 sha `2576df46…` / 639B / plain 0xE3286
仍复现（本路复测过），三处钉死无需同步。l1_slotram `6f267a2e…` 同理不受影响。

**units 全绿**：check_local.py --units exit 0（2:24）；android-query test.sh exit 0
（11 套）；verify_ufw_integrity 全过（四层 CRC/JLFS 含 mic_effect.bin 条目）。

## 7. 诚实边界（必须随候选一起读）

1. **离线候选 UNTESTED，不刷机**。交付=零新增载荷的字节等价契约 + 独立分桶审计 +
   四层 CRC/JLFS 自洽 + 单测全绿；完整功能与设备运行保持 UNTESTED。
2. **消费链零执行证据**（继承 hal3 §7.2 / hal4 §6.2）：apply_mpo/apply_eq →
   0x0607538a → 框架队列 → ioctl 分支 → 音频块重算的完整路径未在任何仿真/真机跑过；
   0x0607538a 的同步等待在冻结单上下文会撞 EQPIPE 同族墙。§3 的优先级表是设计语义。
3. **非事务性**（codex §4）：十段 EQ 段间可能跨音频块逐步生效（fade 0.1f 平滑），
   中途失败留部分曲线、桩仍回「已处理」；MPO 失败不阻止 EQ（apply_mpo 返回后无条件
   apply_eq；apply_mpo 阻塞则 EQ 也到不了）；key30/key31 无跨 key 事务。
4. **ANC 互斥只有请求动作**：db3c(1,1) 返回 ≠ anc_task 硬件退出完成；HA 开启期间外部
   再开 ANC 无「先关 HA」处理；open 失败后不恢复此前 ANC 模式（继承 L1KEY §5.2）。
5. **保存回执 ≠ 消费 ≠ 出声**三个层级；r0==1 只证明消息分发未报错（hal4 §1.3）。
6. M 是调音工具数字域 dB 刻度（与 110.3 同标尺），非 dB SPL；attack=100/TWOPOINT 下
   稳态上限成立 ≠ 瞬时峰值不越限（L2 §6 口径）。±40dB EQ 增益门、freq≤21599 是产品
   口径/观测域（hal4 §6.5）。
7. cmdid 0x30 / key 30 / key 31 的占用残余风险沿用 r4-command-hook §5.4 与 hal4 §6.7。

## 8. 证据锚点

- codex 参谋全文：`~/tmp/codex/202611180021-hal5/final.md`（不入库）；prompt 同目录
  prompt.txt，log.md。实现复核（6 条 P2/P3 全采纳）：`~/tmp/codex/202611180041-hal5rev/
  final.md`——追加段字节身份断言、文件级守卫、x_tail 报告公式（f32 先行）、命名
  `.9g` 可往返、范围门顺序/溢出/舍入边界、文档封装桶 59B。
- 互斥面：tools/hearing-aid/mic_effect_toggle_hook.S / _mpo_hook.S / _l4_hook.S（同窗
  0x06025404，累积超集）；tools/hearing_aid_l1_image.py（KEY7_WINDOW、
  L4_TOGGLE_INSN_IDS、_compose 的 l3/l4 互斥）；洞预算 = [0xE5073, 0xE5FF8) padding
  3981B − 33B 头/标记。
- 构建：tools/build_quiet_candidate.py（--hearing-aid-full、hearing_aid_full 报告键、
  命名）；tools/hearing_aid_l1_image.py（apply_l1_patch/verify_l1_image 不变）。
- 审计：tools/test_hearing_aid_full_image.py（15 项；六桶常数、独立解码
  verify_eq_candidate.load_image、反例两枚）。
- 候选：M=95 sha256 `9ad0f0881a7996cb4dea81477d8731aa9b4db69a6f352603118663d68eda
  5465`（== 组合构建 9ad0f088…；L4-only 2b31e020… + L2 增量）。
