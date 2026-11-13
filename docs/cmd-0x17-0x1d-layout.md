# FF/0x1d 提示音音量 + FF/0x17 ANC 帧形状钉死（路 P / sim-cmd1d）

2026-11-13。本页钉死 QCY App 经 BLE 下发的**提示音音量命令**（RCSP opcode `0xFF` 自定义命令
`0x1d`）的载荷形状，并核对路 CS 记录的 **ANC 命令 `0x17`** 载荷形状。方法照路 H
（[docs/eq-cmd-0x22-layout.md](eq-cmd-0x22-layout.md)）：注册表定位 → 反汇编 setter/getter/
init/恢复默认四个 handler + RCSP 分发器，双向互证。**只读**：未连真机、未刷机、未跑改机器状态
probe；一次性反汇编脚本与产物在库外临时目录，不进库。反汇编同路 H 锚点：app.bin sha
`18f3d573…`（官方 UFW `65cafc7f…` 解出）+ pi32v2-isa objdump。私有 ROM/路径不进本文。

证据强度沿用：**[静态]** = 反汇编指令锚定（本路）；**[面板]** = 控制面板 JSON；**[文档]** =
既有 docs；**[推断]** = 上述链推出；**[未钉]** = 无证据、如实标注。

## 0. 一句话结论

0x1d 写命令的 **data = `value 占位`（2 字节，第 1 字节是音量值、第 2 字节固件忽略）**——路 N
「data=`01 值`」的假设**是错的**（那样发固件会把音量存成 1）。固件只要求 `data_len>=2`，值不校
验不钳位（3/6/8 是控制面板档位 [面板]，同 EQ ±8 的定性）。0x17 的 CS 记录核对**一致**：setter
读 `data=mode scene strength`（3 字节），`17 03` 是分发层 body 的 cmdid+len 字节，不在 data 里。

## 1. RCSP 自定义命令分发层（先钉公共层，0x17/0x1d/0x22 共用）

分发器 `0x0600f85e` [静态]，命令注册表 = `[0x060a9b78, 0x060a9d1c)`（app.bin `+0xa9a78` 起，
15 项 × **28 字节**），表项布局：

| 表内偏移 | 字段 |
|---|---|
| +0 | init（读持久化配置） |
| +4 | getter（只读查询） |
| +8 | setter（写） |
| +20 | 恢复默认回调（`0x600f836` 全表遍历调用，恢复出厂用） |
| +24 | cmdid（u8） |

RCSP 帧 body（opcode 0xFF）剥掉 SN 后的命令体两种形态 [静态]：

```text
写:  cmdid(1B) | data_len(1B) | data[data_len]      → body[0]=cmdid 直接查表
读:  FE 01 cmdid                                    → 只读查询（getter）
```

- **写路径** `0x600f932`：`r3 = b[body+1]`（=data_len）、`r2 = body+2`（=data 指针），
  调 `setter(r0=响应缓冲, r1=128, r2=data, r3=data_len)` [静态]——与路 H 钉的 0x22 ABI 一致。
- **读路径** `0x600f89c`：按 body[2] 查表调 `getter(r0=缓冲, r1=126)`；响应体由分发器包装为
  **`cmdid | n | getter数据…`**（`0x600f946`：`resp[0]=cmdid`、`resp[1]=返回字节数 n`）[静态]。
  例：0x17 查询响应 `17 03 mode scene strength` 里 `17 03` 正是这层包的（getter 只填 3 字节参数）。

由此统一帧形状（App→耳机完整帧）：

```text
FE DC BA | C0 | FF | len_BE16 | SN | cmdid | data_len | data[data_len] | EF
```

## 2. 0x1d 提示音音量：逐字节钉死

注册表项 `app.bin+0xa9b04` [静态]：init `0x06025e2c`、getter `0x06025e68`、setter `0x06025e82`、
恢复默认 `0x06025eb0`、cmdid `0x1d`。

### 2.1 setter `0x06025e82` [静态]

```text
r3 < 2        → 什么都不写，直接返回 0（静默忽略，len=1 的单字节值不发）
r3 >= 2       → r0 = data[0]（u8，音量值本体）
                b[0x124f3e] = r0          （运行时影子状态，getter 用）
                写配置 key17，长度 1 字节    （持久化）
                h[0x127696]（槽 2219）= r0 （音量引擎消费）
                返回 0
data[1..]     → 完全不读（忽略）
值校验        → 无（不校验枚举、不钳位）
```

### 2.2 布局表

| body 字段 | 值 | 说明 |
|---|---|---|
| cmdid | `1d` | 固定 |
| data_len | `02` [推断] | 固件接受 ≥2；按 getter 对称读回取 2 |
| data[0] | 音量值 | **值本体**（面板档位 3/6/8 [面板]；固件不钳） |
| data[1] | 占位（发 `00`）[推断] | 固件不读；取 00 因 getter 读回的第二字节是 RAM 初值 |

完整帧示例（音量 6）：`FE DC BA C0 FF 00 05 | 00 | 1D | 02 | 06 00 | EF`。

### 2.3 旁证链 [静态]

- init `0x06025e2c`：读配置 key17（1 字节），失败兜底 **6**（3/6/8 的中间档）；恢复默认回调
  `0x06025eb0` 置 **6**——「默认中等」与面板 3/6/8 档位自洽。
- getter `0x06025e68`：返回 **2 字节** = `h[0x124f3e]` 低/高字节 → 查询响应
  `1d 02 value <?>`；`0x124f3f`（高字节）在全部反汇编中无写入者（立即数检索仅 0x1d 四个
  handler 引用 `0x124f3e`），按 BSS 初值 0 读回 → 与「写 `value 00`」对称。
- 消费者：音量引擎 `0x0601b10` 按通道跳表选槽 **2217（默认兜底）/2218/2219（提示音）**，
  `0x0601c40` 族的音量应用写同一槽族——0x1d setter 写槽 2219，语义=提示音通道音量 ✓。
- 3/6/8 的定性：与 EQ ±8 同构——**面板/服务器配置层**（controlpanel `cmdid 0x1d`、值 3/6/8
  [面板]）约束，固件接收层不校验（发 5 也存 5，实际效果由音量引擎决定，[未钉] 非档位值的
  播放行为未验证）。

## 3. 0x17 ANC：核对结论（与路 CS 记录一致）

注册表项 `app.bin+0xa9ae8` [静态]：init `0x0600dddc`、getter `0x06025c84`、setter `0x06025ca4`、
恢复默认 `0x06025d32`、cmdid `0x17`。

- setter `0x06025ca4` [静态]：`data_len>=3`；**data[0]=mode、data[1]=scene、data[2]=strength**，
  三字节存 `b[0x1265EB..ED]`。无 mode 枚举校验（1..3 是 QCY 线协议层值 [文档]）；应用链里
  mode=1 时要求 scene∈1..5（scene=5 映射内部 1/2）。前置检查链含一个「返回值==5」的能力查询
  （`call 0x6007fb0` 结果需为 5，推测=支持的降噪场景数 [推断]）。
- getter `0x06025c84` [静态]：返回 3 字节 `mode scene strength` → 查询响应
  `17 03 mode scene strength` ✓ 与路 CS/protocol.md 实测响应吻合。
- 恢复默认 `0x06025d32` [静态]：置 `01 01 02`（默认降噪）✓ 与 CS「默认降噪 17 03 01 01 02」的
  参数部分吻合。
- **结论**：CS 记录的「载荷 `17 03 mode scene strength`」= 命令体（cmdid+len+data），setter 实际
  收到的 data=`mode scene strength` 3 字节，与反汇编一致，**无需修正**。

## 4. 对独立 app 的影响（android-query 落地）

- `H3SBridge.setPromptVolume`：data 从假设的 `01 值` **改为 `值 00`**（本路修正路 N）。
- `EqWritePolicy` 白名单扩为三条 setter 形状（armed 门控不变，仍不开任意字节写）：
  - `0x22`：data_len%7==3（路 H）；
  - `0x17`：data_len==3 且 mode∈1..3；
  - `0x1d`：data_len==2 且值∈{3,6,8}（白名单从严收面板档位，固件本身不钳）。
- 测试：EqFrameTest/H3SBridgeTest 更新为「0x17/0x1d 合法形状放行、畸形（len 错/mode 越界/值非
  档位）拒绝、未武装拒绝」。

## 5. 证据锚点

- 注册表：app.bin `+0xa9a78`(0x22) `+0xa9a94`(0x14) `+0xa9ab0`(0x1f) `+0xa9acc`(0x2d)
  `+0xa9ae8`(0x17) `+0xa9b04`(0x1d) `+0xa9b20`(0x19)…，stride 28，dispatch 表区间
  `[0x060a9b78, 0x060a9d1c)`。
- 分发器 `0x0600f85e`：写 `0x600f932`（setter ABI）/读 `0x600f89c`-`0x600f8b0`（getter）/
  响应包装 `0x600f946`；恢复默认全表遍历 `0x600f836`。
- 0x1d：init `0x06025e2c` / getter `0x06025e68` / setter `0x06025e82` / 恢复默认 `0x06025eb0`；
  状态 `b[0x124f3e]`、配置 key17（1B）、音量槽 2219=`h[0x127696]`；音量引擎消费者 `0x0601b10`/
  `0x0601c40` 族（槽族 2217/2218/2219）。
- 0x17：setter `0x06025ca4` / getter `0x06025c84` / 恢复默认 `0x06025d32`；参数存
  `b[0x1265EB..ED]`。
- 既有：docs/app-protocol.md §2.3/§2.4（路 CS 原始记录）、docs/eq-cmd-0x22-layout.md（路 H
  方法与 0x22 ABI）、docs/protocol.md（实测帧格式/查询响应）。
- 自验：`bash tools/android-query/test.sh` 5 套全过；`python3 tools/check_local.py --units` exit 0。

## 6. 未钉清单（如实标注）

| 项 | 状态 | 说明 |
|---|---|---|
| App 实发 data[1] 的原始值 | [未钉] | QCY App 反编译树不在本路手边；固件不读该字节，按对称读回发 00 |
| data_len>2 的实发形状 | [未钉] | 固件接受 ≥2 且只读 data[0]；白名单从严只放行 ==2 |
| 非档位值（如 5）的实际播放行为 | [未钉] | setter 不拒，音量引擎如何消费未跟（需真机，本路不连） |
| 0x17 前置「返回值==5」检查的确切语义 | [推断] | 按 scene 取值域 1..5 推测为降噪场景数 |
