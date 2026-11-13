# JL_ADC->RES 读回值的消费者推导链（路 BU/sim-adcres，只读预研）

2026-11-13。本文件为只读调研产物：不改 tools/、不跑 probe、全部结论来自对官方 app.bin
（sha256 `65cafc7f…`，加载基址 0x06000100）的静态反汇编（pinned pi32v2-isa codec 线性扫描 +
逐条解码，call/goto 目标全部程序化解析），加上参考 SDK（gitlab 701n commit
`67328f4741c69993c88e504c67377b196c724f97`，`not_exact_qcy_source`）的交叉佐证。
对接停点：**689,402 @ 0x6011704 `fd31_adc_res_not_modeled`**（路 BS 后的条件前沿，
第一轮采样的 RES 首读）。

证据强度标注：**[钉]**=反汇编逐指令钉死（含字段级解码）；**[SDK]**=参考 SDK 交叉佐证
（非 QCY 精确源）；**[推]**=静态推理（未执行验证）；**[疑]**=存在未闭合的解释缺口，已列出。

---

## 1. 数据结构：0x127D08 处的 11 项 ADC 通道队列 [钉]

app_core 尾段 0x60120f4..0x6012100 先建立采样队列（与参考 SDK `gpadc_init` 的
`memset(adc_queue, -1, max_ch*16)` 模板一一对应）：

- 0x60120f8 `r0 = r8 + 5960`（r8=0x1265C0 设备结构基址）→ 0x127D08；
- 0x6012100 `call ROM 0x1fd80` memset(0x127D08, **0xFF**, **176**)：176=11×16，
  即 **ADC_MAX_CH=11**、每项 16 字节，全字段先置 0xFF/0xFFFFFFFF（空闲标记）。

每项 16 字节布局（5 个独立函数的寻址互证，[钉]；参考 SDK `adc_info_t` 同族但字段序不同，
见 §5 注）：

| 项内偏移 | 地址（项 i） | 字段 | 证据锚点 |
|---|---|---|---|
| +0 | 0x127D08+16i | next_due（u32，tick 预算） | 0x60348a2 扫描器 `[r2+0]`、0x601160e `[r1+0]=tick+50` |
| +4 | 0x127D0C+16i | key（u32 通道字；0xFFFFFFFF=空闲） | 0x601160e `[r1+4]`、walker 0x6000e74 `[r1-4]`、0x6001230 `[r3+0]` |
| +8 | 0x127D10+16i | value（u32；DEFAULT 模式存原始 u16，电压路径存 float） | EMA 写 0x6034956 `[r5+r7]`(st.w)、32 轮循环写 0x601216e `h[r4+0]`(st.h) |
| +12 | 0x127D14+16i | mode（u16；==1 → AD_MODE_VOLTAGE） | IRQ handler 0x6034926 `h[r0+2986<<1]`、0x6011dd6 `h[r0+0]` 检 `==1` |
| +14 | 0x127D16+16i | period（u16，ms） | 0x601160e `h[r1+14]=50`、0x6001230 `h[r1+0]=0` |

通道注册（[钉]，与 SDK `gpadc_init` 模板顺序一致）：0x6012112/0x6012126/0x6012134
`call 0x6001230`（adc_add_sample_ch）依次注册 **0x10000（WBG04 基准）→ 项0**、
**0x10006（VBAT_4=VBAT/4）→ 项1**、**0x10004（VTEMP 温度）→ 项2**；
0x601211a/0x601212c/0x601213a `call 0x601160e` 各设 period=50ms。
通道字语义见 §4（SDK gpadc_hw.h 枚举）。

0x6001230 内部 [钉]：命中空闲项后写 key=value=1、period=0，且
`0x6001266 if ((ch|2) != 0x10006) { mode = 0 }`——**仅对非电池族通道清 mode=0**；
VBAT_4/VTEMP 两项的 mode 保持 memset 残留 **0xFFFF**（见 §5 的[疑]点）。

## 2. 生产侧：RES 的读取点与值流 [钉]

### 2.1 阻塞式单次读取 caller A = 0x60116d0（RES 全 app 唯一读点）

```
0x60116dc  [0x127DAC] = r0        # 项9 的 key 槽当暂存：写入通道字
0x60116e6  r0 = [0xfd3100]        # CON 读（bit4=EN 门控；304,317 旧停点）
0x60116ec  call 0x601163a         # bit4=1 时：轮询 helper（bit7=PND 置位→完成退出；bit4=0→放弃）
0x60116ee  call 0x60115b4         #   再触 ADC 控制函数（CON=0x20000/0x20040/0x40 三连写）
0x60116f2  call 0x12b55c          # RAM veneer（OS post 链）
0x60116fc  r0 = [0x127DAC]        # 读回槽内通道字
0x6011700  call 0x6011666         # setter：tbb 通道 case1 → call 0x6067d8a 对
                                 #   P3_ANA_CON4(0xf2c250) 五次 RMW 选模拟通道（P3_ANA_CON4[4:1]）
                                 #   → CON=0x46105d + |=0x40
0x6011702  call 0x601163a         # 轮询 helper（等 bit7 PND）
0x6011704  r2 = [r4+4]            # ★ RES 读（r4=0xfd3100；ld.w n 字段 scale=4）← 689,402 停点
0x6011706  call 0x60115b4         # ADC 控制函数（CPND=0x40 写 1 清 pending）
0x601170a  h[0x127DB0] = r2      # RES 低 16 位存进项9.value
0x601170c  [0x127DAC] = 0xFFFFFFFF # 项9.key 复位空闲
0x6011710  call 0x12b5b0          # RAM veneer
0x6011716  r0 = h[0x127DB0] (u)   # 返回值 = RES 低 16 位（u16）
```

**结论：caller A 的返回值 = RES 的低 16 位（u16）**。全 app 对 0xfd3100 的字面量引用恰 7 处
（0x60115b6/0x6011642/0x60116c4/0x60116e2/0x6034908/0x60349e8/0x6068086），逐条核对后
**读 +4（RES）的只有 0x6011704 一处**；ROM 整块零引用、0xfd3104 字面量 0（路 BG 已钉）。

### 2.2 32 轮采样循环 0x6012148..0x601215e（app_core 内）[钉]

```
0x601213e  r4 = -1   (min)     0x6012140  r5 = 0  (max)
0x6012142  r6 = 32   (计数)    0x6012144  r7 = 0  (sum)
0x6012148  r0 = 0x10000; call 0x60116d0     # 每轮采样 WBG04
0x6012150  if (r0 <= r5) { r4 = umin(r0, r4) } else { r5 = r0 }   # if 块 t/e 字段解码钉死
0x601215a  r7 += r0; r6 += -1; 循环至 r6==0
0x6012160  r0 = r7 - r4 - r5; r1 = 30; r0 = r0 / r1 (u)          # (sum-min-max)/30
0x601216a  r4 = r8 + 5968 → 0x127D10
0x601216e  h[0x127D10] = r0                 # ★ 项0.value = WBG04 原始均值（u16）
```

即：**h[0x127D10] = 32 次 RES(0x10000) 的截尾均值（去掉最大最小后 /30）**——VBG 基准通道
的原始读数基线。SDK 模板对应 `adc_get_value_blocking_filter(vbg, 16)`（SDK 16 次、QCY 32 次）。

### 2.3 12 轮双通道 getter = 0x601174c [钉]

```
r11 = 目标通道字（-1 = 只采基准）；12 轮循环内：
  r5 = callerA(0x10000)          # 每轮同时采一次 WBG04
  r8 += callerA(r11); r9=umin; r10=umax     # 目标通道 sum/min/max
  r7 += r5;        r12=umin; r6=umax        # 基准通道 sum/min/max
出口：r0 = (基准 sum-min-max)/10             # 自带 vref 均值（不引用 h[0x127D10]！）
      r1 = (目标 sum-min-max)/10（无目标通道则 1023）
      call 0x601171a               # 归一化 divider(vref, target)
```

app_core 尾段两次调用 [钉]：
- 0x6012176 `r0=0x10006` → 结果 `utof` 后 `[0x127D20] = r0`（**项1.value = float(VBAT 归一化值)**）；
- 0x6012186 `r0=0x10004` → `utof` → `[0x127D30] = r0`（**项2.value = float(VTEMP 归一化值)**）。
SDK 模板对应 `adc_get_voltage_blocking(vbat)<<4`/`(vtemp)<<4`（SDK 存定点 <<4，QCY 版存 float——
驱动版本差异，见 §5）。

### 2.4 归一化 divider = 0x601171a [钉] + [SDK]

```
r2 = [0x1265C0+676] >> 27        # efuse 镜像的 VBG trim 字（bits27-30=d、bit31=符号）
r2 = ±3d；r2 += 801              # ★ K = 801 ± 3d
r1 = r1 * K；r0 = (r0==0) ? 1 : r0
return r1 / r0                   # 电压(mV) = target_raw × K / vbg_raw
```

[SDK] 参考 SDK `cpu.a:gpadc.c.o`（LLVM IR，`adc_value_to_voltage`）：
`K = 800 ± 3×(efuse_get_gpadc_vbg_trim()&15)`，符号由 bit4 决定，`ADC_VBG_CENTER 800`、
`ADC_VBG_TRIM_STEP 3`、`ADC_VBG_DATA_WIDTH 4`（gpadc_hw.h）。
**QCY 与参考两点偏差 [钉]：常数 801（SDK 800，+1）；基准通道用 0x10000 WBG04
（SDK 模板 0x10100 MBG08）**。d 的符号/幅值来自 [0x126864]（写入者 0x6080d8e 域，
efuse/时钟初始化链）→ **K ∈ {801±3d} ⊆ [756, 846]**（trim 字实际值由 EFUSE fixture 决定，
模拟按真实指令计算，无需假设）。

### 2.5 通道字语义 [SDK]

gpadc_hw.h 枚举（ADC_CH_TYPE_PMU=0x1<<16）：0x10000=`AD_CH_PMU_WBG04`（bandgap 基准）、
0x10004=`AD_CH_PMU_VTEMP`（片内温度）、0x10006=`AD_CH_PMU_VBAT_4`（VBAT/4）。
模拟通道选择=P3_ANA_CON4[4:1]（`ADC_PMU_CHANNEL_ADC(x)`，与 0x6067d8a 五次 RMW 互证）。
gpadc_hw_v10.h 钉 CON 位域：**bit7=PND（转换完成）、bit6=CPND（写 1 清）、bit5=IE、bit4=EN**、
bits8-11=CH_SEL、bit17=CLKEN、bits18-20=ASEL——与路 BN 从轮询行为推断的 bit7/bit4 语义一致。
满量程 **1023（10 位）** [钉]：0x60117a6 `r1 = 1023`（无通道时 divider 输入）、
0x6000ebc 温度表哨兵 `if (r3 > 1022)`。

## 3. 消费侧全图（每个消费者给指令锚点）[钉]

### 3.1 表查找 walker = 0x6000e74（`adc_get_value` 同形）

以 key（通道字）查 11 项队列（`[r1-4]`=key、`h[r1]`=value 低 16 位），命中返回 u16，
未命中返回 1。调用者 7 处：0x6000ea6（温度档位，见 3.4）、0x60041de/0x6004410（音频域
DAC 电压档，路 BO 域）、0x6011df8/0x6011e04（adc_get_voltage 内部）、0x6012478（NTC 启动门，
见 3.5）、0x6023ece。

### 3.2 `adc_get_voltage` = 0x6011dd6 与电池电压 getter = 0x6011e20

```
0x6011dd6: 走队列找 key==ch 且 mode==1 → 0x6011e0c: r0=[value]; ftou; 返回（电压模式直读 float）
           否则 0x6011df4: r5 = walker(0x10000)      # vbg = h[0x127D10]（32 轮均值）
                    r1 = walker(ch)                  # value 低 16 位
                    返回 divider(vbg, r1)
0x6011e20: r0 = 0x10006; call 0x6011dd2(=0x6011dd6 包装); r0 <<= 2; 返回
           # ★ battery_mv = 4 × adc_get_voltage(VBAT_4)   （VBAT_4=VBAT/4 → ×4）
```

[SDK] `battery_level.c`（earphone 参考应用）同形：`get_vbat_voltage()=gpadc_battery_get_voltage()`
+ `battery_calc_percent()` 分段插值 + `get_vbat_need_shutdown(): vbat <= poweroff_v`。

### 3.3 电池门（★ 分支消费存在，boot-or-die）

**(a) 11 连败低电门，app_core 0x601232e..0x6012356** [钉]：

```
0x6012338  call 0x6011e20            # battery_mv（每轮重取，纯 RAM 读，无新转换）
0x601233c  r1 = h[0x1265C0+2234×2]   # ★ 阈值 h[0x127734]
0x6012340  if (r0 >= r1) goto 0x6012350   # 达标 → r6++（好样本计数）、r7 清零，循环 ≤11 次
           否则 0x6012344: r7++（连败计数）、r6 清零，r7<11 继续
0x601234e  11 连败 → 0x601248e: call 0x6064efc; r0=4; call 0x6011fd8; call 0x60682a4（关机）
           + 0x60124a2 死循环
```

**阈值不是零 [钉]**：电池配置默认函数（入口 0x6011a08——push 帧完整、128 字节栈缓冲 +
config id 101/102/20… 逐项缺省填充，app_core 0x60121ca 调用，先于本门）
0x6011c1a `h[0x127734] = 3200`（r7=0x1265C0 基址 +2234×2）——config id 20 缺失时的
**编译内置默认 3200mV**。
即：**battery_mv ≥ 3200 才能继续启动，11 次连续低于即关机**。路 BH 记的「阈值静态零、
11 连败才可达」按本路证据修正为「阈值默认 3200、由 RES 推导值决定」。

**(b) 后续同形门**：0x6012a56（app_core 后段，同阈值）、0x602211e（运行域）、
充电域 0x60686b2（`getter(0x10006)<<2 − 3700 > 1300` → 电池须在 [3700,5000]mV 窗内才进
充电初始化；本路径由 config id 99 缺失在前置 0x606867c `if (r0 != 7)` 跳过，条件前沿不触及）。

### 3.4 温度档位消费者 = 0x6000e94（NTC）

`[0x1253A0]` → NTC 配置块（0x6001868 初始化：config id 523 解析 → [0x1253A0]=配置指针，
通道字存配置+4）[钉]。enable 字节非零时：`walker(通道字)` 原始值 → 20 级半字阈值表
（`if (r3 > 1022)` 哨兵）线性定档 → 返回档位字节。**条件前沿上 config 523 缺失 →
enable=0 → 整链旁路**（返回 255）。VTEMP 项2 的 float（0x127D30）在 D1 前无消费者 [钉]：
全 app 对 +5984/+6000 偏移零个已解码寄存器加法、0x127D20/0x127D30 字面量零命中。

### 3.5 NTC 启动等待门 = app_core 0x6012458..0x6012482 [钉]

`b[0x1274F4]`（NTC enable）非零时：`walker([0x1274F8])` 值须 >9，150 次重试全失败 →
0x6012486 → 0x601248e 关机路径。**config 523 缺失 → enable=0 → 跳过**（条件前沿不触及）。

### 3.6 IRQ 域（D1 后才运转）[钉]

app_core 0x6012192 注册 **IRQ39**（handler 0x60348e8，ADC 完成中断）、0x60121a4 注册
周期扫描回调 0x60349d2（adc_scan：0x60348a2 找到期项 → setter(key,1) 带 IE=1 启动下一转换）。
handler 处理：mode==1 → divider + **0.2/0.8 EMA float** 维护项 value（WEIGHTING_MODE，
SDK gpadc.h `MEAN_FILTERING/WEIGHTING` 枚举同族）；key==0x10000 → WBG04 整数域 EMA 维护
h[0x127D10]；否则原始 u16 直存。**注意 [疑]**：新样本读自 [0x1265C4+idx×16]（设备结构 +4 的
另一 16 字节跨步数组），全 app 静态未找到其 CPU 写者（推测 OS RAM 链交付，D1 后才可观测）。

### 3.7 电池档位/电量发布链 [钉]

电池配置解析器 0x6011eae（app_core 0x6012302/0x601235a/0x60128c6 三次调用）：
config id 529 缺失 → 2 项默认曲线；随后 0x6011f78 `h[0x12662E] = 0x6011e20()`（电池 mV）、
0x6011f80 `b[0x1265D1] = 0x6011e30()`（mV → 分段线性插值档位字节）、
0x6011f88 `b[0x1265D2] = 0x6011e92()`（(档位/10)−1 索引）。
运行期 0x601297e（10ms 定时器，0x6012938 创建）：每 6 次采样滑动平均回写 h[0x12662E]
（SDK `VBAT_DETECT_CNT 6` 同形）。**这些是发布型消费者（写状态不挡启动），但 3.3(a) 的门
用同一取值函数。**

## 4. 值域约束汇总（fixture 必须满足的硬约束）

1. **[钉] RES 消费的是低 16 位，物理量程 0..1023（10 位）**；0 与 0xFFFF 都是合法编码但
   vbg=0 会触发 divider 除 1 兜底（值爆炸，仍不停机）。
2. **[钉] 电池门（3.3a）**：`4 × divider(h[0x127D10], walker(0x10006)) ≥ 3200`，11 连败关机。
   这是条件前沿上**真实存在且可达**的 boot-or-die 分支（阈值默认 3200 编译内置）。
3. **[钉] 比例约束（物理）**：VBAT_4/WBG04 原始比 = (VBAT/4)/VBG_mV；4×归一化值 ≈ 电池 mV。
   K=801±3d 是 VBG 的 mV 标称（trim 后），与 SDK `ADC_VBG_CENTER 800` 互证。
   ADC 参考 Vref 无 SDK 证据钉死 → **绝对原始码值无推导链，比例可推导**。
4. **[钉] 常量样本自洽**：32/12 轮循环取常量样本时 (sum−min−max)/n 恰等于该常量。
5. [SDK] 传感器域：WBG04=基准、VBAT_4=VBAT/4、VTEMP=温度传感——具名通道的物理身份。

## 5. [疑] 未闭合点（如实列出，不猜）

- **mode=0xFFFF 疑点**：静态链显示 VBAT_4/VTEMP 项 mode 保持 memset 残留 0xFFFF
  （0x6001230 仅对非电池族清 0；全 app 无 mode=1 写者），则 §3.2 的 mode==1 float 直读分支
  在本固件上可能永不触发，`adc_get_voltage` 走 divider 兜底，而兜底输入
  `walker(0x10006)` = **float 的低 16 位**（app_core 刚写入的 float 字的低位）——
  算术确定但非物理。模拟按真实指令执行会自动落在这条路径上；真机若走 float 直读分支
  （说明另有 mode 写者或驱动布局不同），取值为物理 mV。**§6 候选值按两条路径都过关选取。**
- IRQ 域新样本数组 [0x1265C4+idx×16] 的写者未见（D1 后才可观测，见 3.6）。
- [0x126864] trim 字的具体值：由 EFUSE fixture 经 0x6080d8e 域写入，模拟按真实指令计算；
  影响仅为 K∈[756,846]，§6 候选对其鲁棒。

## 6. 三选一建议

### 建议：方案 (a) 具名档位 fixture（derived-not-measured，显式假设+推导链）

**结论：分支消费真实存在（§3.3a 电池门，阈值 3200mV 编译内置），RES 值域可被约束、
可给出带推导链的候选值，且模拟器可在到达时按真实指令验证门的方向——满足 fixture 先例
（lrct_trim_result=38400）的「显式、具名、版本管理、derived-not-measured」要求。**

候选值（每通道具名常量，模拟按当前槽 [0x127DAC] 的通道字返回对应值）：

| 通道字 | 通道 | 候选 RES | 推导链 |
|---|---|---|---|
| 0x10000 | WBG04 | **400** | §4.1 十位域内非零；§4.4 常量样本 ⇒ h[0x127D10]=400 |
| 0x10006 | VBAT_4 | **500** | 物理锚：4×(500×K/400) = 4×floor(K×1.25)；K=801 时 ≈ **4004mV = 标称 4.0V 锂电池** |
| 0x10004 | VTEMP | **400** | 条件前沿无消费者（§3.4），仅域内非零要求 |

门的方向核验（脚本复核，K 取端点亦鲁棒）[钉]：
- **float 直读路径**（若 mode==1 生效）：battery_mv = 4×1001 = **4004 ≥ 3200 → 过门**。
- **divider 兜底路径**（静态链实际路径）：X=1001 → float=0x447A4000 → 低 16 位=0x4000=16384 →
  battery_mv = 4×(16384×K/400)：K=801 → **131,232**；K=846 → 69,304；K=756 → 123,860，
  全部 ≥ 3200 → **过门**（余量 >20 倍，对 trim 字任意值鲁棒）。
- 副作用面：充电窗口 [3700,5000]（两路径的值都在窗外 → 跳过，与真机不插充电线一致）；
  NTC 门 enable=0 跳过；电量档位字节会取到曲线端点档（显示非物理电量）——**作为已知模型
  注意事项记录，与「真机正常启动」不矛盾（两路径都过门）**。

命名建议 `fd31_adc_res_named_values`（模型参数+probe 场景参数，需 fd31_adc_con_readback，
默认关 legacy 不变），报告全文标注 derived-not-measured + §5 疑点 + 本文档指针。

**证据升级路径**（不改本建议，标注优先级）：真机 FD07 RAM-loader 路径（路 BA：纯 memcpy、
地址无检查）可读 0xfd3104——USB 会话里 RES 保持最后一次转换值（启动链最后一转换≈WBG04），
一次实测即可把 WBG04 码值从推导升级为 measured（并反解 Vref）。会话仍 deferred/需授权。

### 备选：方案 (b) 钉住

若用户不接受 §5 疑点下的 fixture：保持 `fd31_adc_res_not_modeled` 钉在 689,402。
预期停点形状：`stop_pc=0x6011704, kind=fd31_adc_res_not_modeled, value_bytes 不可知`；
后续 32 轮 × 每轮 ~386k 步轮询预算（当前投影不发明 PND 完成）意味着 fixture 放行后
到采样链跑完还需 ~31M 模型步（32+24+24 次 caller A 轮询），预算需同步扩容——
另一条（正交）路线是 SDK 有据的 **PND 完成投影**（gpadc_hw_v10.h bit7=PND 语义 +
lrct_trim_done 先例：读回=末写值|bit7），可把每轮轮询折叠为立即退出；是否做归下一路三选一，
本路只记录证据。

### 不建议：留空/任意值

任何 0..1023 值并非全部行为等价：§3.3a 的门在低值（如全 0：battery_mv=0）下 11 连败 →
关机死循环——**存在禁区 [钉]**；具名常量+门核验是最低成本的可辩护选择。

## 7. 指令锚点索引（复核用）

| 结论 | 锚点 |
|---|---|
| RES 唯一读点/返回 u16 | 0x6011704、0x6011716 |
| 32 轮均值 → 项0.value | 0x6012148..0x601216e |
| 12 轮双采 + divider → float 项1/项2 | 0x601174c..0x60117ac、0x6012176/0x6012186 |
| divider K=801±3d | 0x601171a..0x6011746（0x601173a `+801`） |
| battery_mv=×4 | 0x6011e20..0x6011e2e |
| 电池门 11 连败/阈值 3200 | 0x601232e..0x6012356、0x6011c1a、0x601248e |
| 队列布局/memset 11 项 | 0x60120f8..0x6012100、0x601160e、0x6001230、0x6000e74 |
| mode==1 分支 | 0x6011dd6..0x6011e0c、0x603492a |
| IRQ39/扫描注册 | 0x6012192、0x60121a4 |
| NTC 门/温度档位 | 0x6012458..0x6012482、0x6000e94、0x6001868 |
| 充电窗口门 | 0x60686b2..0x60686c4 |
| 电量发布链 | 0x6011eae..0x6011f8a、0x601297e..0x60129bc |
| 10 位满量程 | 0x60117a6（1023）、0x6000ebc（>1022 哨兵） |

参考文件身份：br28.h sha256 `111e29ea…`（data/dependencies.json 已钉）；gpadc.h/gpadc_hw.h/
gpadc_hw_v10.h 与 gpadc.c.o/gpadc_hw.c.o/gpadc_hw_v10.c.o 取自 pinned SDK commit
`67328f4`（cpu.a，data/dependencies.json 已钉 archive）；battery_level.c/charge.c 同 commit
（参考应用，仅形状佐证）。

## 8. 路 BY 实测修正（2026-11-13，sim-adcresfix，fixture 实施后的执行证据）

本节是方案 (a) 落地（`fd31_adc_pnd_completion` + `fd31_adc_res_named_values`，
probe 场景 `p11_heap_experimental_config`）后的执行侧修正/确认，原文保留、以本节为准：

1. **§5 的 mode=0xFFFF [疑]点已由执行关闭：mode=1 写者存在**。实测项1/项2 的
   mode 半字 = **0x0001**（快照 `0600010000406c4401003200` /
   `0400010000003d4401003200`）——getter 存 float 的同路径也写 mode=1，
   静态扫描漏看了它。因此 `adc_get_voltage(0x10006)` 走 **float 直读分支**
   （0x6011e0c），battery_mv = 4×945 = **3780 ≥ 3200 过门**（§6 预测的
   兜底路径在此固件上不触发；两条路径都过门的结论不变）。
2. **runtime K=756**（=801−3×15，trim 字下端）：固件真实算术算出项1 float
   = **945.0**（0x446C4000 = 500×756/400）与项2 float = **756.0**
   （0x443D0000 = 400×756/400）——divider 与 §2.4 的 K 推导链互证。
3. **通道识别改用 P3_ANA_CON4[4:1] 复用域而非 RAM 槽**（§6 原文「按槽
   [0x127DAC] 通道字返回」）：外设模型不接触 guest RAM；setter case1 →
   0x6067d8a 把 (通道字&0xFFFF)<<1 写进复用域（idx=0 走 `|0x20/0x40/0x80|
   1` 五连、idx≠0 走 `(读&0x1FE1)|(idx<<1)` 两形状，逐指令重解钉死），
   RES 读时刻的域值=硬件实际转换的通道（WBG04 轮末值 0xE1/VBAT_4 轮
   0xED/VTEMP 轮 0xE9，bit5-7 使能位首轮后保持）。另加一致性门：最后
   CON 写须 EN=1 且 CH_SEL(bits8-11)=0（PMU 模拟路径），否则显式严格停
   `fd31_adc_res_channel_not_modeled`（codex 参谋采纳）。
4. **PND 完成投影的证据补充**：SDK gpadc_hw_v10.c 的 `adc_wait_pnd`/
   `adc_get_res` IR（§2 的 SDK 佐证升级为驱动级证据——轮询 bit7、RES 有效性
   钉在 bit7、`adc_close` 三连写与 QCY 控制函数逐值相同）；投影语义=无状态
   读时强制（EN=1 才强制 bit7），**原子复合 RMW 的内部读从裸最后写值合成**
   （边界抽象只钉死到 rmw.* 指令形状）——开启前后 CON 写记录逐值相同。
5. **前沿换形状**（PND 折叠的后果，非本 fixture 的值假设）：80 次轮询塌缩后
   Timer0 到期时刻全部重排（延时函数 0x12e256 实测每次 call 重编 TIMER0
   PRD，delay(1000µs)→11999），首个合格到期 336,337 起产生 **14 次真实
   IRQ4 交付往返**；CPU0 core tick 未溢出（停点 447,383 在路径无关的
   447,490 地标前 ~107 步）；新前沿=**447,383 @ 0x6012f22
   `p3_ana_read_not_modeled`**（W1[0] 的 P3_ANA_READ 轮询读，路 BV 钉住的
   模拟量红线，RES 同款）。
