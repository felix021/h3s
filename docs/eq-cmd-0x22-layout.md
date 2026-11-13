# FF/0x22 EQ 写命令逐字节布局（路 H / sim-eqcmd）

2026-11-13。本页钉死 QCY App 经 BLE 下发的 EQ 写命令（RCSP opcode `0xFF` 自定义命令
`0x22`）的**载荷逐字节布局**，服务 R3「独立 app 直接发 >8 增益」的前置。**只读**：未改
tools/ 代码、未跑改机器状态 probe、未连真机。反汇编用仓库钉死 codec（pi32v2-isa
@3608ed1，reference-cache）+ 官方 UFW（sha `65cafc7f…`）解出的 app.bin（sha
`18f3d573…`，`disassemble_app.py` 同源 ELF 走 pi32v2isa objdump）；控制面板字节/曲线
来自 `data/controlpanel_qcyh3s_16764.json`。私有 ROM/路径不进本文。

证据强度：**[静态]** = 反汇编指令锚定（本路）；**[SDK]** = 参考 SDK（eq_config.h 注释）；
**[面板]** = 控制面板 JSON 值；**[推断]** = 上述链推出；**[未钉]** = 仓库无证据、如实标注。

## 0. 一句话结论

FF/0x22 的 data（App 发来的 EQ 参数体）就是 **3 字节头 + 每段 7 字节**，全部字段逐字节
钉死（解/编两个方向反汇编互证）：头 = `type(u8) + master_gain(i16, ÷100 得 dB)`；每段 =
`freq(u16, Hz) + q(i16, ÷100) + gain(i16, ÷100 得 dB) + iir_type(u8, 255=默认 2)`。
**固件命令接收层无任何 ±8 钳位**（setter 只做长度校验 + memcpy，apply 直接
`int16÷100 → float`）。独立 app 已具备完整字节格式，直接按 §5 伪代码组帧即可。

## 1. 命令在哪一层（完整字节序列）

```
RCSP 帧:  FE DC BA | flags | opcode | len_BE16 | body | EF
  body  =  SN(1B) | cmdid(1B=0x22) | data_len(1B) | data[data_len]
  data  =  3B 头 + 每段 7B × N          ← 本页钉死的就是这里
```

- `flags`：`C0`=命令需响应；EQ 写属 setter，一般 `C0`。
- `body` 的 SN、cmdid、data_len、以及帧头/尾 `FE DC BA`/`EF` 的写法照
  docs/protocol.md / docs/app-protocol.md（路 CS）抄，本页不重复。
- **data_len = 3 + 7×N**（N=段数）。setter `0x0602583c` 的校验就是
  `data_len % 7 == 3`（`r3 = r3 - (r3/7)*7; if (r3 != 3) 报错`）[静态]，段数
  `N = data_len / 7`。

## 2. 逐字节布局表（钉死）

### 2.1 头（3 字节，data[0..2]）

| 偏移 | 长度 | 字段 | 编码 | 取值/换算 |
|---|---|---|---|---|
| 0 | 1 | EQ 类型 | u8 | 1=自定义、10=空间音效、其它(2=音乐预设)直接应用；见 §6 命名 |
| 1 | 1 | master gain 低字节 | u8 | `int16 = lo \| hi<<8`（hi 有符号） |
| 2 | 1 | master gain 高字节 | i8 | **master_gain(dB) = int16 / 100.0** |

[静态] 解码点：apply `0x06025118` `0x6025152..0x6025176`（`r2 = b[+1]`、`r1 = b[+2](s)`、
`insert(p:8,l:24)` 拼成符号扩展 int16 → `itof` → `÷ 1120403456(=100.0f)`）；编码点
`0x060256be` `0x6025708..0x602571a`（`float×100.0 → ftoi(trunc) → 低字节/`>>8` 高字节`）。

### 2.2 每段（7 字节，第 i 段在 data[3+7i .. 3+7i+6]）

| 段内偏移 | 长度 | 字段 | 编码 | 取值/换算 |
|---|---|---|---|---|
| 0 | 1 | freq 低字节 | u8 | `uint16 = b0 \| b1<<8` |
| 1 | 1 | freq 高字节 | u8 | **freq = uint16，单位 Hz**（直存，非索引/对数） |
| 2 | 1 | q 低字节 | u8 | `int16 = b2 \| b3<<8`（b3 有符号） |
| 3 | 1 | q 高字节 | i8 | **q = int16 / 100.0**（Q 值） |
| 4 | 1 | gain 低字节 | u8 | `int16 = b4 \| b5<<8`（b5 有符号） |
| 5 | 1 | gain 高字节 | i8 | **gain = int16 / 100.0，单位 dB** |
| 6 | 1 | iir_type | u8 | 0=high_pass/1=low_pass/2=band_pass/3=high_shelf/4=low_shelf；**255=未指定→默认 2** |

[静态] 解码点：apply 段循环 `0x6025192..0x60251f0`（段指针 r2 实为「段尾」=data[9+7i]，
用负偏移读：`b[r2-6]=freq_lo`、`b[r2-5]=freq_hi`、`b[r2-4]=q_lo`、`b[r2-3]=q_hi(s)`、
`b[r2-2]=gain_lo`、`b[r2-1]=gain_hi(s)`、`b[r2+0]=iir_type`）；编码点 `0x060256be`
`0x602572a..0x6025756`（同一布局反向写出）。两个方向逐字段一致，字段顺序/字节序**钉死**。

关键编码细节：

- **freq 是 uint16 Hz 直存**，不除以任何系数、不做表查找。apply 里 `r1 = b0 | b1<<8` 直接
  存 32 位 [静态]；面板 freq 值 32..16000 Hz 都在 u16 范围 [面板]。
- **gain 是 int16 ÷100 得 dB**（符号扩展）。面板 `-17/-14/-2.5/3` 等值 [面板] 分别存
  `-1700/-1400/-250/300`（int16）。
- **q 是 int16 ÷100**。面板 `0.4/1.2/0.8/3/2.5` [面板] 分别存 `40/120/80/300/250`。
- **master_gain 同 gain，int16 ÷100 得 dB**。面板 `-4` [面板] 存 `-400 = 0xFE70`。
- **iir_type 255 表示「没给」**，apply 兜底默认 2=band_pass（`r14=2; if (r5!=255) r14=r5`
  [静态]）。面板 iitType 只出现 0/2/3 [面板]，与 [SDK] eq_config.h 枚举一致。

## 3. 取值范围与上限

| 字段 | 表示范围 | 说明 |
|---|---|---|
| freq | 0..65535 Hz (u16) | 无固件钳位；典型 20..20000 |
| gain | int16 ÷100 = **−327.68..+327.67 dB** | **命令接收层无钳**（见 §4）；App UI 限 ±8（面板 mindb/maxdb） |
| q | int16 ÷100 = −327.68..+327.67 | 无钳；典型 0.1..20 |
| master_gain | int16 ÷100 = −327.68..+327.67 dB | 无钳 |
| iir_type | 0..4（255=默认） | 枚举 |

**段数 N**：setter 无显式上界（只有 `len%7==3` 校验），但 apply 段循环**只跑 10 次**
（`r4 = 0; …; r4 += 16; if (r4 != 160) 回跳`，160=10×16 [静态]），即运行时只应用前 10 段；
控制面板 `count=10` [面板]，预设曲线 9 段、默认/空间音效 10 段。**结论：N 有效范围 1..10
（App 实际发 9 或 10），独立 app 发 10 段最稳。** 配置存储缓冲 143 字节（key42 读长
[静态：0x6025782 `r2=143`]），能装下 20 段，但第 11 段起 apply 会忽略。

## 4. 无 ±8 钳位的反汇编证据（回应 R3 遗留问题）

路 CS 遗留「QCY 命令接收器 0x0602583c 是否钳 gain」——本路反汇编直接钉死**不钳** [静态]：

- setter `0x0602583c` 全程只有：`len%7==3` 校验 → `b[QCY+5499]=段数` → `memcpy` 到配置
  缓冲 → （一次落空桩的 key34 保存，见 §6）→ `call 0x06025118`。无任何与 8/±8 的比较。
- apply `0x06025118` 把 `int16` 直接 `itof ÷100.0` 转 float，全程无 min/max/clamp。
  唯一特殊分支是 type==10（空间音效）与 type==1（先装载预设 0），与增益值无关。

所以「±8」确如路 CS 结论，是 (a) App UI/控制面板层（`mindb:8`/`maxdb:8`）的滑块界，不是
协议/固件接收层限制。真正的约束在输出端限幅（路 CN：WDRC out_threshold + data_saturation
+ DAC_VL0），发大增益要防削顶。

## 5. App 转命令的最小正确路径（伪代码）

```python
def eq0x22_body(eq_type: int, master_gain_db: float, segs: list) -> bytes:
    # segs: [(freq_hz:int, q:float, gain_db:float, iir_type:int), ...]  1..10 段
    def i16(x):           # 把十进制值 ×100 转 int16，越界要钳到 [-32768,32767]
        v = round(x * 100)
        return v & 0xFFFF
    body = bytes([eq_type & 0xFF])
    body += i16(master_gain_db).to_bytes(2, 'little')
    for freq, q, gain, iir in segs:
        body += (freq & 0xFFFF).to_bytes(2, 'little')   # u16 Hz
        body += i16(q).to_bytes(2, 'little')            # i16, /100
        body += i16(gain).to_bytes(2, 'little')         # i16, /100 dB
        body += bytes([iir & 0xFF])                     # u8, 255=默认 band_pass
    return body

def rcsp_0x22_frame(sn: int, body: bytes) -> bytes:
    # 帧: FE DC BA | C0 | FF | len_BE16 | SN | 22 | data_len | data | EF
    payload = bytes([sn, 0x22, len(body)]) + body
    return bytes([0xFE,0xDC,0xBA, 0xC0, 0xFF]) + len(payload).to_bytes(2,'big') + payload + bytes([0xEF])
```

要点：段内字节序一律小端；gain/q/master_gain 都 ×100 取整；freq 是整数 Hz 不乘系数；
iir_type 未知时发 255（固件兜底 band_pass）。帧头 `C0`/`SN`/`EF` 与分片规则照
docs/protocol.md（MTU247→GATT244、单写串行、≥10ms 片间隔）。

## 6. 证据与命名（钉死 vs 推断 vs 未钉）

反汇编锚点 [静态]（本路全 codec 验证）：

- 注册记录 `app.bin+0xa9a78` = `{init 0x6025774, getter 0x602580a, setter 0x602583c,
  0x6025882, 0x60258b6, 回调 0x60258ea, cmdid 0x22}`（对照 ANC `0xa9ae8` 同构记录）
  [静态]。
- setter `0x602583c`（r2=data 指针, r3=data_len）：长度校验 + memcpy + apply。
- apply `0x6025118`：字节 → float 解码 + 写运行时段数组 + 调 `0x602503a` 应用。
- **编码方向 `0x60256be`**：float → 字节（getter/回调复用），逐字段与 apply 互证。
- 配置持久化：key 42（init `0x6025774` 读 143B、回调 `0x60258ea` 写）；setter 里的
  key34 保存经 `0x6025642 → 0x6024e68`，而 `0x6024e68 = rts`（空桩）[静态]——本路实测
  该保存**实际不落盘**，真正持久化走回调的 key42。

命名状态：

| 项 | 状态 | 说明 |
|---|---|---|
| 字节布局/字节序/字段顺序 | **[静态] 钉死** | 解/编双向互证 |
| freq=u16 Hz、gain=i16÷100dB、q=i16÷100、master=i16÷100dB、iir 255→2 | **[静态] 钉死** | 反汇编 + 面板值交叉 |
| iir_type 枚举 0..4 | **[静态+SDK]** | eq_config.h 注释 + 面板只现 0/2/3 |
| 段数上限 10 | **[静态] 钉死** | apply 循环 160/16=10；面板 count=10 |
| 无 ±8 钳 | **[静态] 钉死** | setter/apply 全链无 clamp |
| type 语义（1=自定义/10=空间/其它=预设） | **[推断]** 命名 | 反汇编钉死「type==10 走空间音效、type==1 先装预设 0、其余直接应用」三个分支；「1 叫自定义、2 叫音乐预设」是按面板结构 + getter 编码器硬编码 type=1 的命名，未真机对照 |
| key34 落空桩的具体意图 | **[未钉]** | 是历史遗留还是有意 no-op，无证据 |
| N>10 段被忽略、config 缓冲能存 20 段的用途 | **[未钉]** | 存储 143B 与运行时 10 段上限不一致的根因未追 |

## 7. 交叉验证（面板曲线 → 字节示例）

面板「默认」预设（id 1）前 2 段 + 头，按 §2 编码 [面板→本路公式]：

- 头：type 取 1（自定义）、mastergain `-4` → `01 70 FE`。
- 段 0：freq 30Hz、q 0.8、gain 0、iir 0 → `1E 00 50 00 00 00 00`。
- 段 1：freq 170Hz、q 0.6、gain −17、iir 2 → `AA 00 3C 00 5C F9 02`（−17×100=−1700=0xF95C，低字节 0x5C）。
- 段 8：freq 11000、q 2、gain 2、iir 3 → `F8 2A C8 00 C8 00 03`（11000=0x2AF8、200=0xC8、3=high_shelf）。

[推断] 头 type 字节未从任何抓包字节确认（仓库无 App 实发字节样本），上述 type=1 是「独立
app 要发自定义 EQ 时的推荐值」，非实测样本；若日后有真机抓包可换实测值回填。

## 8. 证据锚点

- 反汇编：app.bin sha `18f3d573…`（官方 UFW `65cafc7f…` 解出，`disassemble_app.py` 同源
  ELF）+ pi32v2-isa @3608ed1 objdump（`python3 -m pi32v2isa objdump -d -mcpu=r3`）。
- 关键函数：setter `0x0602583c`、getter `0x0602580a`、apply `0x06025118`、编码
  `0x060256be`、init `0x06025774`、回调 `0x060258ea`、memcpy 包装 `0x060257e6`、RCSP
  分发器 `0x0600f85e`（钉 setter ABI：r2=data/r3=len）。
- 面板：`data/controlpanel_qcyh3s_16764.json`（type2/count10/mindb8/maxdb8、sys_eq 6 预设
  +空间音效、freq/eqs/q/iitType/mastergain 字段）。
- 既有：docs/app-protocol.md（路 CS）、docs/eq-toggle.md、docs/runtime-persistence.md
  （key42=EQ、`0x06000f88`/`0x06001f12` 配置读写分派器）、docs/hearing-aid-eq-feasibility.md
  §1.2（stream.bin eq_tool 浮点格式，与本线上格式对比见 §2）。
- 自验：`python3 tools/check_local.py --units` exit 0。
