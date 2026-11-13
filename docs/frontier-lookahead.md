# 前沿前瞻扫描：0x6001342（JL_IMAP 写）之后的未来停点清单（路 U / sim-lookahead，只读预研）

2026-11-14。基线 main `64a7e6b`（路 R 之后、路 S/T 并行提交之上）。本页是给后续实施路的弹药库：从当前条件前沿 **3,073,402 @ 0x6001342**（p11_heap_experimental_config 场景，写 0xfd5c90=JL_IMAP->FI_UART0_RX，gpio_set_fun_input_port 助手 0x600132e 锁临界区内）静态前瞻，把「未来停点」提前批量识别 + 每个给三选一倾向。

**只读**：未改 tools/ 任何代码、未跑任何 probe。反汇编用仓库钉死的 pi32v2-isa codec（`decoder_for(mcpu='r3')`，与 vendor objdump 同源；call/branch 目标按 codec 符号扩展×scale 公式计算）。一次性脚本在 `~/tmp/h3s-lookahead/`（不进仓）。

**核心结论（三句话）**：
1. 从停点到 uart_init 事务结束，**必经主路径上只有 1 个新 SFR 停点**：0x60340d8 对 JL_UART0->CON1（0xfd2004）的原子 RMW `&= 0xFFFFFFEF`（irq 使能位），路 M 白名单「RMW 严格停」边界；其余全部落既有投影域（CON0 容器 / 写白名单 / gpio 容器）。
2. 条件分支 `[cfg+4]==208`（PP0 指示脚）门控 **PORTP 五连原子 RMW**（0xfd5708/0c/20/10/14）——这五条**不需要新做**，已在 gpio 容器（GPIO_BASES 含 0xfd5700）的原子 RMW 位记录域内，路 BV 已预判。
3. uart_init 事务完成后经 ops 表（0x609d9d0）间接返回，**静态前瞻到边界为止**：之后是 D1 事件循环域；本页把事件域的第一族候选（UART0 IRQ handler 的 HRXCNT __RO 读、suspend 面 canary 读、PLL0 面）一并钉死供后续路线用。

## 0. 方法与证据强度

- **[静态]** = 本路指令锚定反汇编（codec mcpu=r3，全 app 线性扫描 + 目标正向重解；call 目标=pc+size+符号扩展位移×scale，与 0x12b746 已知 211 个调用点互证公式）。
- **[SDK]** = 参考 SDK 头（GitLab 701n 仓库，pinned commit `67328f4`，与 data/dependencies.json 的 reference_sdk 同 commit）。**注意**：`io_imap.h`/`br28.h` 不在 hardware_reference_files pin 清单里（路 R 只 pin 了 io_omap.h/gpio_hw.h）；本路从同一 commit 直接取用，sha256：
  - `interface/driver/cpu/br28/asm/io_imap.h` = `a4df681d11927a26a79c9ce6dc8b7c28774ddec301bc950ae4f4759c68f7794b`
  - `interface/driver/cpu/br28/asm/br28.h` = `111e29ea8d3d72e504a221caf035552250dcda959d4b79e8e1cd13623a6aad01`
  - 建议实施路把这两个头补进 dependencies.json（同 io_omap.h 先例）。
- **[模型]** = tools/probe_irq_entry.py / tools/br28_model.py 源码阅读（场景旗标清单见 §4）。
- 静态限制：运行时值决定的分支方向（RAM 配置、返回值）两个方向都列并标注；RAM 代码（0x12xxxx，母本在 app flash）只按母本反汇编。

## 1. 返回链重构（停点的动态上下文）[静态]

```
0x60343e8 uart_init 容器（经 ops 表 0x609d9d4 间接调用，无直接 caller）
  └ 0x60344ba call gpio_set_function(0x6001456)（RX 对，r2=表[r9+1592]=2）
      └ 0x6001856 call.l 0x600132e gpio_set_fun_input_port
          └ 0x600133c call 0x12b746（GPIO 锁 pend）
            0x6001342 [r4+0]=r3   ← 当前停点（写 0xfd5c90=47）
            0x6001344 call 0x12b772（GPIO 锁 post）
          0x600134a pop 返回
      0x600185a pop 返回（gpio_set_function 到此为止，无后续）
  └ 0x60344bc ← 本页前瞻起点
```

uart 配置表钉死（flash 已知字节，r9=0x60A18C0）[静态]：
- `[r9+1568]`=0x60a1ee0：UART 基址 {0xfd2000, 0xfd2100, 0xfd2200}
- `[r9+1580]`=0x60a1eec：TX 对 function 参数 {1,3,7}（PORT_FUNC_UARTx_TX）
- `[r9+1592]`=0x60a1ef8：RX 对 function 参数 {2,4,8}（PORT_FUNC_UARTx_RX）
- `[r9+1628]`=0x60a1f1c：三个 IRQ veneer 入口 {0x60355e0, 0x60355f2, 0x6035604}

## 2. 必经主路径 SFR 访问清单（按执行序）[静态+模型]

停点释放（路 S 在途的 IMAP 写侧）后，顺序经过：

| # | 站点 | 地址 | SDK 名 [SDK] | 形状 | 所属事务/锁 | 模型现状 | 判定 |
|---|---|---|---|---|---|---|---|
| 1 | 0x6001342 | 0xfd5c90 | JL_IMAP->FI_UART0_RX（成员 36，编译布局 4 字步进；io_imap.h 全族 __RW） | st.w 整字写=47 | GPIO 锁（0x12b746/772） | unmapped | **当前停点**（路 S/sim-imapwr 在途） |
| 2 | 0x60340c6→0x6085126 | — | gpio_set_mode(224, mask, 17) | 纯调用 | — | — | **SFR 空操作**（见 §3.1，静态钉死） |
| 3 | **0x60340d8** | **0xfd2004** | **JL_UART0->CON1（br28.h __RW；编译布局字 1）** | **原子 rmw.and.nmodimm `&= 0xFFFFFFEF`（清 bit4=irq 使能）** | OS 临界区（0x12b55c/0x12b5b0，IPMASK 0→6） | jl_uart_init_write_records：**RMW 读→Blocked** | **未来停点 1**（§5 预案） |
| 4 | 0x60344ce/0x60344dc/0x60344ee | — | dev 层 irq 注册（0x6064162/0x6064f18） | RAM only | — | 通过 | 无停点（返回值运行时定，历史恒成功） |
| 5 | 0x603453c→0x601d200 | 0xfd2000/08/10 | uart_set_baudrate：CON0 RMW（&=~3/\|=0x3400/整字写/\|=3）+ BAUD [r7+8] 整字写 + OTCNT（0x601d1c2，CON0 bit6 门控）+ uart_is_idle 读 CON0 bit0 | CON0 走路 F 容器（读回=当前值）；BAUD/OTCNT 走路 M 白名单 | mutex 0x6064538/0x606474a | 通过 | 无停点（uart0 不触 0x12cc40，见 §3.2） |
| 6 | 0x603454e | — | `if ([cfg+4] != 208) goto 0x60345f8` | 条件 | — | — | **运行时才能定**（§3.3，两方向都无新停点） |
| 6a | 0x6034568/82/9c/b6/d0 | 0xfd5708/0c/20/10/14 | JL_PORTP->DIR/DIE/DIEH/PU/PD（JL_PORT_FLASH_TypeDef +8/0xc/0x20/0x10/0x14，全 __RW） | 原子 rmw.or/and（\|=1×3、&=~1×2，PP0=bit0） | **每条独立 GPIO 锁段**（0x12b746/772×5，=5 个 IRQ 交付窗口） | **gpio 容器原子 RMW 位记录（GPIO_BASES 含 0xfd5700）** | **通过，无需新做**[模型] |
| 7 | 0x6034668/6a/6c | 0xfd201c/20/24 | RXSADR=0x125BFC / RXEADR=+0x220 / RXCNT=0x220（RX DMA 环形缓冲，r5=0x125BFC、r10=544） | st.w/st.w.l 整字写 | mutex 段内 | 路M白名单字 7/8/9 | 通过 |
| 8 | 0x6034670/74 | 0xfd2000 | CON0 `\|= 0x68`（bit3/5/6=RX DMA/使能族）、`\|= 0x4`（bit2=RX irq） | 原子 rmw.or.modimm | mutex 段内 | 路F CON 容器（读回=当前值 0x3400→0x346c） | 通过 |
| 9 | 0x603468e→0x6065d86 | — | 事件/回调注册（r2=0x60355e0 IRQ veneer、r0=b[0x60A18DB]） | RAM only | — | 通过 | 无停点 |
| 10 | 0x60346a4→0x601d200 | 同 #5 | 第二次 baudrate | 同 #5 | — | 通过 | 无停点 |
| 11 | 0x60346aa→0x603407a | — | 忙检查（h[obj+48]==h[obj+50]，单线 UART 恒等，静态保证返回 1） | RAM only | — | 通过 | 无停点 |
| 12 | 0x60346b8 | — | b[obj+80]=r5 后返回 | RAM | — | 通过 | 事务结束 |

## 3. 关键细节钉死

### 3.1 gpio_set_mode 对 USB 引脚是 SFR 空操作（解开「为何前缀没停过」）[静态]

0x6085126 gpio_set_mode 按 mode 分发到 0x6084eac/0x6084f12/0x6084f7e/0x6084fe8/0x6085052/0x60850bc 六个助手。它们各有两条路径：
- **port==14 特例**：直接 RMW **0xfd6000 = JL_USB_IO->CON0**（br28.h `map_adr(0x60,0)`；0x6084f1a `if (r6 != 14)`——比较的是 gpio 号，**实参 224（USB DP）≠14，不进**）；
- **通用路径**：0x6084e40 端口掩码（`if (port>13) return 0`——224>13 直接 0）+ 0x6084e76 字节表线性搜索（表 @0x60852c0 无 224）→ 返回 0 → `if (r3 == 0) goto` 跳过 RMW。

所以 uart 引脚配置里两次 gpio_set_mode（0x603448c mode=1、0x60344a8 mode=17）+ 0x60340c6 一次（mode=17）对 SFR 全部空操作（只剩延时循环 + 0x12b746/0x12b772 锁开销）。USB 引脚复用只走 OMAP/IMAP（路 M/R/S 域）——与已观测行为自洽。**0xfd6000 不在必经路径上**；只有 PA14（gpio 14）或真 port-14 调用才会碰它（本世界不可达）。若日后其它路径撞 0xfd6000：SDK 仅 JL_USB_IO CON0 一名（__RW），模型侧已有 `initial_usb_io_word` fixture 接口（需 port_initialization_projection，br28_model.py）。

### 3.2 uart0 不触发 0x12cc40（canary 面）[静态]

uart_set_baudrate 0x601d200 入口 `ifs (r0 >= 3)`：**uart 序号 ≥3 才**调 0x601d18e（时钟选择 thunk）→ 0x12cc40（RAM 代码，母本 0x60c6418；读 P3_PCNT_SET0 0xf2c128 + **P3_VLVD_CON 0xf2c044 canary 读**，路 BV 域）。容器 0x60345ee 的 `ifs (r4 >= 3)` 同门。本世界 uart0（r4=0）：**两处都不进**，0x12cc40 不在必经路径。⚠️ 对照：当前 p11 场景旗标里有 p33_analog_tail 但**没有** p33_canary_projection/首读 fixture——若日后 suspend/uart3+ 路径撞 0xf2c044，要单独评估（§6 事件域 b）。

### 3.3 条件分支 `[cfg+4]==208`（PORTP 五连的门）[静态，运行时才能定]

cfg=[0x1265C0+648]=RAM 指针，+4 字段静态不可读。208=0xD0=PP0 的 gpio 号编码（port13×16+0）。两方向：
- **==208**：PP0 作输出指示脚，跑 §2 #6a 五连 RMW（gpio 容器放行，无停点）；
- **!=208**：直接跳 0x60345f8（跳过五连，其余不变）。
**两方向都无新停点**——实施路不需要为它做任何模型决定，只需在报告里记录走向。

### 3.4 交错提醒（非停点）[模型]

本段有 ~9 个锁/临界区退出窗口（IMAP post、0x12b55c/5b0 临界区、五连的 5 个锁段、mutex pend/post）：在途 Timer0（IRQ4，每 40k 步到期）与 core tick（IRQ3）pending 会在窗口交付——路 R 已实测该模式（297 次到期在 omap 锁退出后交付），BE/BF 机制就位，无需新决策。

## 4. 场景现状核对 [模型]

p11_heap_experimental_config 旗标（probe_irq_entry.py:3667-3690）已含：jl_uart_con、jl_uart_init_writes、jl_omap_writes、p33_analog_tail、p3_ana_read_ldoin_low、gpcnt_con_readback、jl_clock_dcck、timer0_irq4_delivery、core_tick_irq3_delivery、gpio_model=True（probe_clock_startup.py startup() parameters 固定带）。**未含**：jl_imap 写侧（路 S 在途）、p33_canary、port_initialization_projection。

## 5. 未来停点三选一预案

### 停点 1（唯一必经新停点）：0x60340d8 JL_UART0->CON1 原子 RMW

- **形状**：`rmw.and.nmodimm [r0+4] &= 0xFFFFFFEF`（c1 ef 10 00），清 bit4；同族姊妹形状 0x60340f6 `[r0+4] |= 0x10`（disable 路径，0x6034096 的 r4==0 分支）；三基址对称。
- **当前模型行为**：原子 RMW → 通用兜底 mem.read → JLUartInitWriteRecords.read → `Blocked('jl_uart_init_word_readback_not_modeled')`。
- **三选一倾向**：**(a) 读回=最后写值投影**（把 CON1（连同三基址对称）从纯写白名单升级为「写记录+读回=最后写值」，lp_timer CON 路 AL 同族先例）。依据：①SDK br28.h JL_UART_TypeDef **CON1 __RW**（布局分歧不影响标注）；②路 M 白名单已记录 CON1=0 的整字写（0x603446c），最后写值已知 → RMW 读 0、写 0，行为平凡、零发明；③irq 使能位 RMW 是配置面，非状态轮询。**置信度：高**。
- 方案 (b) 钉住（诚实停着不推进）代价：整个 uart_init 事务卡死在第一半，后面 RX 三连写/双 baudrate 全到不了——明显劣。方案 (c) fixture 不适用（不是缺输入值，是缺读语义）。
- **打包建议**：与路 S 的 IMAP 写侧**同一批打包**（IMAP 白名单 + CON1 读回扩展一起合并），一次回归覆盖到 RX 三连写落地。

### 事件域候选（事务之后，静态边界外，供后续路参考）

| 候选 | 入口 | 关键 SFR | 性质 | 倾向 |
|---|---|---|---|---|
| a. UART0 IRQ handler | 0x60354e0（veneer 0x60355e0，注册于 #9） | CON0 读+RMW（容器 ✓）；**0x6035584 读 [base+40]=0xfd2028 HRXCNT（br28.h __RO，硬件接收计数）** | 需 UART RX 活动才有 IRQ；模型无输入源 → **不可达**（不发明输入就不停） | 红线读（HRXCNT 同 ADC RES 类）：到那再说 |
| b. suspend/resume ops | 0x6034722（r1==4/16 分发） | PORTP 五连同族（0x6034752..ba，gpio 容器 ✓）+ 0x6034808→0x601d18e→**0x12cc40 读 0xf2c044（canary）** | 低功耗事件触发，非顺序路径 | canary 首读 fixture（路 BV 已建模待接线） |
| c. PLL0 面 | 0x6035616 | 读 JL_PLL0->CON0 0xfd4600 bit0（模型 initial PLL0=1 → 跳过 _pll_init）；不走则 call ROM 0x1fe14 `_pll_init` | uart3+/时钟 ops 域 | PLL0 CON 已在 registers；_pll_init 是 probe_boot 时代已知停 |
| d. 音频/tone 域 | — | 0xfe05xx/0xfe0a04 族 | D1 后 | 见 docs/d2-audio-first-scene.md、docs/appcore-tail-sfr-semantics.md |

## 6. 覆盖范围声明

- 本页静态覆盖：返回链 3 帧 + 容器尾段 0x60344bc..0x60346bc（144 条）+ 全部 callee 展开（uart_set_baudrate/irq-enable/dev 层/mutex/post/gpio 六助手族/忙检查等）+ suspend 兄弟函数 + UART IRQ handler + PLL 面 ≈ **1050 条唯一指令**（不含 OS 锁/veneer 体在 RAM 代码里的重复执行，~15 次调用各数十条）。到 uart_init 事务返回为止的必经路径**全覆盖**（100%）。
- **静态边界**：容器经 ops 表 0x609d9d0 间接被调（全 app 无直接 caller），返回后的续链属 OS 设备层/事件循环（D1 域），静态不可钉——这是本页诚实终点，不是遗漏。
- 分支方向凡运行时才能定的（§3.3 cfg+4、错误路径返回值、r4>=3）均两侧列出且两侧结论一致（无新停点）或标注不可达。

## 7. 实施路行动清单（按 ROI）

1. **路 S 合并**（IMAP 49+ 字白名单，io_imap.h 全 __RW，omap 同款 write-only 记录；io_imap.h/br28.h 建议补 pin）。
2. **CON1 读回扩展**（停点 1 三选一方案 (a)，lp_timer CON 先例；连带 0x60340f6 disable 面）。
3. 验证跑：预期前沿一次滑过整个 uart_init 事务（RX 三连写落地、CON0 RMW、条件 PORTP 五连、双 baudrate），停在**事务后第一个事件域边界**——该边界形状由运行时决定，届时按事件域候选表对号入座。
4. 不需要做：PORTP 五连（已有域）、gpio_set_mode 面（空操作）、0x12cc40（uart0 不可达）。
