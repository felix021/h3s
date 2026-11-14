# D3 真机对照打包会话：C1+C2+C3+C5 单会话计划单（离线 dry-run 交付物）

2026-11-14 23:40，路 SESS（sim-sesspack，纯离线预演路）。基线 main `fe41d68`。
本页把 d3-preparation §2.4 的「一次授权会话打包 C1+C2+C3+C5」落成**离线可验证的
交付物**：会话命令总表、每读点的期望值推导链与三分支判读、`--dry-run` 预演输出、
授权请求件预填。工具侧=tools/usb-recovery/read_rom.py 新增 `--session-pack`
（复用 BM 的 `--p11-heap-probe` 机制，零新协议代码）。

**本路性质（红线，原样沿用 BS/d3-preparation）**：零真机操作——本页一切「会话」
均为计划与 dry-run 输出，**不构成授权**；设备动作授权边界以 AGENTS「Device actions
and publication」节与当次会话用户明示为准。FD07 专项纪律（p11-heap-measurement/
rom-read 钉死）原样有效：只走 RAM-loader 路径（ROM 原生 FD07 原地变换严禁指向
P11 窗）、128B 实测分块、双读逐字节比对、零重试。

## 1. 会话命令总表

固定 pinned loader（23552B，SHA256 `ab1b77eb…40267c`，FB06 上传至 RAM 0x120000）。
会话=进入序列 + 8 读点 × 双读，**共 65 条命令**；无退出命令（拔 USB 物理结束，
手动重启耳机并确认正常——rom-read 收尾先例）。

### 1.1 进入序列（49 条，与 ROM 读取/救砖会话完全相同的既有序列）

| 步 | 命令 | 参数 | 说明 |
|---|---|---|---|
| 1 | INQUIRY | cdb `12 00 00 00 24 00`，size 36 | 校验 `BR28 / UBOOT1.00` 身份；前置=fresh 正常 QCY USB `3654:4a55` 一次性切模式后 `4c4a:3442` 枚举（已有 `open_session` 机制，拒绝已有 loader 身份） |
| 2–47 | FB06 ×46 | address=0x120000+block×512，length=512，逐块 CRC | pinned loader 逐块上传，块字节必须与 loader 文件一致 |
| 48 | FB08 | entry 0x120000，argument 1 | 跳转执行 loader（唯一允许的跳转，参数钉死） |
| 49 | FC0A | size 16 | 确认 SPI NOR（响应 `fc 0a 03`）；此后进入 loader-ready 相位 |

### 1.2 读序列（16 条 FD07：8 读点 × 2 遍，128B，双读字节比对）

| 步 | 读点 | 块地址 | probe | 角色 | 观察对象（块内偏移） |
|---|---|---|---|---|---|
| 50–51 | cpu1_entry_snapshot | 0x19fef4 | C1 | 控制组 | +4：0x19fef8 CPU1 入口字 |
| 52–53 | cpu1_ready_snapshot | 0x1265f4 | C1 | 控制组 | +1：0x1265f5 ready 标志 |
| 54–55 | p11_code_head | 0xf20080 | C1 | 控制组 | +0..32：p11_code.bin 头（需 `--p11-code` 参考） |
| 56–57 | lrct_num | 0xfd3604 | C1 | 记录型控制 | +0：JL_LRCT NUM（SFR 窗观察点，复位态数据预期，低诊断价值） |
| 58–59 | p11_heap_descriptor | 0xf27f40 | C1 | **目标** | +0xa..e：heap begin/size 四字节 |
| 60–61 | adc_con_res | 0xfd3100 | C2 | **目标** | +0：JL_ADC CON（记录）；+4：RES 半字（判读） |
| 62–63 | p3_ana_read | 0xf2c064 | C3 | **目标** | +0 低字节 bit1/bit2：充电检测位 |
| 64–65 | core_tick_cnt_prd | 0xffe0f0 | C5 | **目标** | +0：CNT 复位值；+4：PRD 复位值（均记录型） |

每读点恰一次 128B FD07 ×2 遍（`probe_read_two`：两读逐字节比对，不一致即抛错终止
会话，无重试）。块大小 128B=实测安全值（512B 会毁 loader USB 设备对象，p11-heap-
measurement §loader FD07 handler）；计划外地址/非 128B 长度被 transport 白名单拒绝。

### 1.3 命令数预算（对照 rom-read 先例量级）

| 会话 | INQUIRY | FB06 | FB08 | FC0A | FD07 | 合计 |
|---|---|---|---|---|---|---|
| ROM 全窗口读取（2026-11-10 实测） | 1 | 46 | 1 | 1 | 2054（含探测） | 2103 |
| P11 heap probe（BM 计划） | 1 | 46 | 1 | 1 | 10 | 59 |
| **本 session-pack** | 1 | 46 | 1 | 1 | **16** | **65** |

量级判断：65 条与既有会话同族（进入序列占 49，读序列约为 BM 计划的 1.6 倍），单会话
时长与 ROM 读取的 2054 次 FD07 相比短两个数量级，无预算风险。

### 1.4 零写断言（机器可验证）

`session_pack_zero_write_assertions`（dry-run 报告内嵌，单测钉死）：

- `post_entry_all_fd07_reads`：进入序列（49 条）之后**全部命令为 FD07 读**；
- `forbidden_opcode_count=0`：FB00/FB01（擦）/FB02/FB04（编程）/FC0C/FC12（跳转
  /复位）/FD05（flash 读，本会话也不需要）等写/擦/OTP/跳转类命令零出现；
- `flash_erase_or_program_commands=0`、`otp_write_commands=0`。

注：FB06 上传（写 RAM）与 FB08（loader 入口跳转）是进入序列固有步骤，属于
p11-heap-measurement 会话先例的既定边界，不计入「会话读阶段」——读阶段零写。
transport 层 opcode 白名单（INQUIRY/FB06/FB08/FC0A/FD07 之外全拒）双保险。

## 2. 读点判读表（期望值来源 + 三分支）

三分支判读纪律（d3-preparation §1.3 统一失败判据，源自 p11-heap-measurement
判据表）：**匹配→升 measured／不符→推导链复审（如实记录字节，不拟合）／控制组
失败→inconclusive 记录阻塞（不重试）**。工具报告 verdict 为**建议性标注**
（`enforced=false`，BM「报告不强制」先例）；真实判读以会话后人工复核为准。

控制组门（仅作用于 C1 目标）：三个保持性正控制组（cpu1_entry/ready/p11_code_head）
全 match → C1 目标可判；任一 mismatch → C1 目标=inconclusive_control_failed；
`--p11-code` 参考未提供 → controls=incomplete（C1 目标 verdict 降级为 review-only）。
C2/C3/C5 判读独立于 P11 RAM 保持性控制组（各自的 not-retained/复位态分支自答，
见下）。

### 2.1 C1（P11 heap 描述符，docs/p11-heap-measurement.md 全文沿用）

| 检查 | 期望 | 来源（推导链） |
|---|---|---|
| cpu1_entry_word（+4，4B） | `f4ca1200` | 0x19fef8=0x12caf4，三方仿真互证（p11-heap-measurement §候选地址表） |
| cpu1_ready_flag（+1，1B） | `01` | 0x1265f5 post-boot 残留 1（RAM 保持则陈旧值=1） |
| p11_code_head_32B（+0，32B） | p11_code.bin 头 32B | 公开 UFW 资源 BANKCB entry0，CRC 离线校验，SHA1 `fe360081…78282`（`--p11-code` 钉死） |
| **heap_begin_size（+0xa，4B）** | **`0c3df439`**（LE u16 begin=0x3d0c/size=0x39f4） | 路 X 三方互证推导（p11_code.bin 链接期立即数+SDK 消费宏+VAD_DVAD_CONFIG_BEGIN 布局）；结构自洽旁证：保护区间终点/0x8000 界内（p11-heap-measurement §target） |

三分支：控制组三读全 match 且四字节=`0c3df439` → **p11_heap_info_bytes 由 derived 升
measured**；控制组 match 但字节不符 → 如实记录、推导链复审（不拟合）；p11_code_head
全 0/全 FF → P11 RAM 保持性未证 → C1 inconclusive 挂账终止判读。

### 2.2 C2（ADC RES/WBG04，docs/adc-res-consumer.md §6/§8）

| 检查 | 期望 | 来源 |
|---|---|---|
| **adc_res_halfword（+4，u16）** | 域 1..1023（十位域非零）；候选 **400**（WBG04）/ **500**（VBAT_4） | RES 保持末次转换值，启动链末次≈WBG04（BU §6 评估）；候选值表=BU §6（十位域+常量样本自洽+4.0V 锂电池物理锚 4×(500×K/400)≈4004mV，K=801±3d）；BY §8 实测修正后 VBAT_4 float=945→3780mV 过门 |
| adc_con_halfword（+0，u16） | 无（记录型） | CON 复位态观测；0x46105d 族投影是运行态末写读回值，loader 会话=复位世界，只记录不判读 |

三分支：读值=400 → WBG04 码值升 measured（并可反解 Vref）；=500 → VBAT_4 末次转换
+电池门核验过（4×(500×K/400)≥3200mV 对 K∈[756,846] 全域成立）→ VBAT_4 升
measured；域内其他值 → 末次通道身份/码值推导链复审（域内非候选=新输入，如实记录）；
=0 或 >1023（如 0xFFFF）→ RES 复位后不保持 boot 态 → inconclusive（不能区分推导错
与复位清零，与 P11 RAM 保持性同挂账，d3-preparation §5.1）。

### 2.3 C3（P3_ANA_READ 充电检测位，docs/appcore-tail-sfr-semantics.md §9 / 路 CB）

| 检查 | 期望 | 来源 |
|---|---|---|
| **charge_detector_bits（+0 低字节，mask 0b110）** | **bit1=1, bit2=1**（掩码位=0b110） | SDK charge_hw.h 逐位语义：bit1=LVCMP_DET（ldoin>vbat）、bit2=LDO5V_DET（ldoin>0.6V=充电线在位），charge.c 阈值注释（路 CB 逐行钉死）；**USB 会话场景推导**：读时 VBUS≈5V 在位 → ldoin≈5V > 0.6V 且 > vbat(≈3.7-4.2V) → 两位均应置位 |
| other_bits（其余位含 bit0） | 无（记录型） | 其余位无推导链（CB derived_mask=0b110 把其余位显式标非推导）；bit0=CHARGE_FULL_FLAG 只记录 |

三分支：掩码位=0b110 → 「USB 在位」场景下充电检测位语义获真机对照（与 CB 电池
场景 fixture 0b000 互补成对——两个场景锚一起约束位语义）；掩码位≠0b110 → 位语义/
阈值/复位态检测器行为推导链复审（不拟合）；块双读不一致 → 会话终止（通用纪律）。
注意：C3 的期望值场景=USB 插着的复位态世界，与 CB fixture 的电池启动场景是
**两个不同场景锚**，判读时不得混用。

### 2.4 C5（core-tick CNT/PRD 复位值，d3-preparation §1.3 C5 / D3-SFR-02）

| 检查 | 期望 | 来源 |
|---|---|---|
| core_tick_cnt（+0，u32） | **无（记录型读点）** | 复位值无推导链（芯片手册未取得；「多数计数器复位为 0」属发明期望，不做）——读回值本身=measured 复位值锚，供模型 con/cnt/prd=None→严格停的复位面参照 |
| core_tick_prd（+4，u32） | 无（记录型） | 同上；PRD 复位值≠1199999 不构成矛盾（1199999 是固件启动后写的运行值，复位/运行分账，D3-SFR-02） |

三分支退化：C5 只有「记录」与「会话终止」两支——读回即锚（upgrade 的是「复位值
存在性」而非「与推导匹配」）；unresolved overflow 期间 CNT 是否冻结属运行时动态
语义，loader 复位态世界**不可答**，本会话不触碰该问题（能力边界如实声明）。

## 3. 工具侧（--session-pack 与 --dry-run）

```
# 预演（不碰设备；loader 文件可选，缺省时 FB06 CRC 为占位）：
python3 tools/usb-recovery/read_rom.py --session-pack --dry-run [--loader <pinned loader>] \
    [--p11-code <pinned p11_code.bin>]

# 授权后实跑（需 fresh 正常 QCY USB + 新建 0700 私有目录）：
sudo python3 tools/usb-recovery/read_rom.py --session-pack \
    --loader analysis/usb-loader/br28loader.bin --workdir analysis/private-sesspack-NEW \
    [--p11-code <pinned p11_code.bin>]
```

机制复用（零新协议代码）：`ROMReadTransport` 的 probe 白名单（`probe_addresses=
SESSION_PACK_ADDRESSES`）、`probe_read_two` 双读、`load_ram`/`open_session` 进入
序列、`pinned_p11_head` SHA1 校验，全部为 BM `--p11-heap-probe` 已离线单测的实现；
本路新增的只是计划表、期望表、verdict 建议器、命令计划器与 dry-run 出口。

dry-run 报告（JSON，stdout）=预演交付物，含：65 条命令逐步清单（opcode/参数/
相位/读点名/遍次）、每读点期望检查表（label/offset/width/policy/expected/source/
`verdict:"branch_pending_session"`）、零写断言四项、`device_accessed:false`、
`session_not_authorized:true`。实跑报告同构，另含每读点双读 sha256/`two_pass_match`/
actual_hex/verdict 建议/控制组状态与原始块文件（0600）。

离线单测（tools/usb-recovery/test_rom.py，29 用例=旧 15+新 14）：计划结构
（C1 前缀与 BM 逐项一致）、白名单/128B/相位门拒绝、命令计划 65 条与步骤连续、
零写断言（含注入 FD05/FB01 后被抓）、dry-run CLI（exit 0/JSON 字段/--dry-run
单独拒绝）、双读不一致终止、verdict 全分支（exact/masked/domain×候选/record、
控制组 failed/incomplete 门控、C2 0/777/400/500 四形状、C3 位不符）。

## 4. 授权请求件预填（BS §3 模板，A 型）

按 d3-preparation §3 模板骨架预填；**固定声明原样有效**：本请求不因「仿真全绿」
自动生效，会话内每一步以用户当次明示为准。

```
请求类型：A 只读观测会话（FD07 读，经 pinned RAM loader；非刷机、非 BLE）
目标：D3 session-pack 八读点（C1 heap 描述符/C2 ADC RES/C3 P3_ANA_READ/C5 CNT-PRD），
  命令总表=docs/d3-session-pack.md §1（65 条；预演输出=--dry-run JSON）
前置状态：D1=进行中（BJ 未落，前沿 3,747,578 @ core_tick_overflow_requires_resolution）
  D2=进行中（D2-b 场景在途） 三镜像 parity=A1-PAR-01 FAIL（core-tick 交付相位差，
  2026-11-14 已归因；对本只读会话的影响面=读点期望推导链不依赖 parity 结论，见下检②）
证据包：1.验收记录汇总=docs/acceptance-samples/d3/（6 NOT_RUN，本会话目标=D3-PRB-01/
    SFR-01/SFR-02 三条+补 SFR-03）
  2.候选 sha=不适用（A 型只读会话无候选镜像；设备当前固件=已验证 b79
    1326848B sha256 b79cb3a6…）；读点期望值推导链账=本页 §2 表
  3.差异账=不适用（无镜像差异）；替换物=§2 期望值-推导链逐项账（每期望值挂来源）
  4.执行证据=--dry-run 输出（65 命令可逐条审查）+29 离线单测+工具机制复用清单（§3）；
    边界声明：本会话一切结果为单台设备复位态世界快照，SFR 动态语义不可答（§2.3 能力表）
  5.回滚=A 型零写无回滚需求；收尾=拔 USB→手动重启耳机→确认播放/ANC 键正常
    （rom-read 会话收尾先例，含 AGENTS「手动重启一次」条目）
本次动作边界：只 FD07 读（128B×16，双读比对）；FB06/FB08/FC0A 仅进入序列；
  无 FD05/擦/编程/OTP/跳转/复位命令；不自动重试；双读不一致或任何失败即停记录
预期获得：C1 四字节/或 C2 码值 derived→measured；C3 充电位 USB 场景对照；C5 复位值锚
不预期：unresolved overflow 冻结语义、PND 完成时序、IRQ 资格等运行时动态语义；
  他通道 ADC 码值（只有末次转换在 RES 里）；时窗/事件流比较面（C6/C7 另属）
```

**触发条件三检**（BS §3，当前状态如实）：

1. **对应验收面板全绿**：✅ 满足——D3 面板 6 条全 NOT_RUN 无 FAIL/BLOCKED；
   NOT_RUN 与请求范围的交集（PRB-01/SFR-01/02/03）=本会话目的本身，已明示。
2. **三镜像 parity 链当前前沿绿**：⚠️ 不满足——A1-PAR-01 FAIL（core-tick 交付
   相位差，判据①②命中，已归因为 447k 后时序判据机制性不可用）。本页评估：本会话
   为 A 型只读，期望值推导链（§2 表）全部来自反汇编/SDK 位语义/物理场景推导，
   不依赖 parity 结论；此检在 BS 模板中主要面向 B 型刷机。**按模板原文不打勾，
   留给用户决策。**
3. **候选镜像身份与差异账可逐字节复现**：✅ 满足（不适用即无争议）——A 型无候选
   镜像；工具/期望表/计划可由 `--dry-run`+单测逐字节复现。

## 5. 记录与验证

- 本路 dry-run 全绿记录：`docs/acceptance-samples/d3/D3-PRB-02.json`（PASS，
  evidence_level=none——离线工具验证，非模型条件、非真机）。
- C3 补记录：`docs/acceptance-samples/d3/D3-SFR-03.json`（NOT_RUN 预填，判读三分支
  =本页 §2.3；BS 底稿漏列 C3，本路补齐）。
- d3-preparation §1.3 C1/C2/C3/C5 行已加「会话打包就绪」标注；HANDOFF 条目见
  该文件「已实现」区。
- 复核命令：`python3 tools/usb-recovery/test_rom.py`；
  `python3 tools/acceptance_record.py --validate docs/acceptance-samples/d3`；
  `python3 tools/check_local.py --units`。

## 6. 不确定处（如实标注）

1. **0xffe 窗（C5）与 0xfd3 窗（C2/lrct）的 loader 自用证据**：0xf2c 窗有 loader
   自用实证（0xf2c120/0xf2c2c8/0xf2c254，p11-heap-measurement）；0xfd3/0xffe 窗
   只有「命令层无地址检查」的静态结论，无 loader 同窗自用先例。若首次读挂起/失败
   →按纪律即停记录（不做窗探测重试）。
2. **复位态世界的「保持性」分歧面**：C1（P11 RAM）、C2（RES）各自带 not-retained
   分支；C3 是读取时刻 live 输入位（USB 在位即有定义）；C5 复位值无保持性问题——
   四个 probe 的保持性假设不同，判读不得跨 probe 外推（§5.1 BS 原文「其余读点
   首会话观测」）。
3. **C2 末次通道身份**：域内非 400/500 值可能是「末次转换非 WBG04/VBAT_4」而非
   「推导错」——domain 分支的复审输入，不是 FAIL。
4. dry-run 的 FB06 CRC 为占位（本地无私有 loader 文件）；实跑前 `fetch-loader`
   取得 pinned loader 后 CRC 为真实值，transport 层 FB06 校验不变。
