# pi32v2 汇编器可行性调研（路 D / sim-assembler，只读预研）

**基线**：main 4a13f78；**范围**：只读调研，未改任何 tools/ 代码，未跑改报告 probe；所有验证脚本在 `/home/felix021/tmp/`（不入库）。**证据分级**：[静态]=读源码/文档钉死；[实测]=本路在本地实际运行验证；[SDK]=参考 SDK 头/IR；[推断]=推理未正向穷举。

## 0. 先纠正一个前提（这是本报告最重要的结论）

R1 条目的原始表述是「pi32v2 目前只有解码器（pi32v2-isa codec），写入方向的汇编器是真缺口」。**这个前提不成立。** [实测][静态]

`tools/reference-cache/pi32v2-isa/`（github.com/jvitkauskas/pi32v2-isa，本项目已 pin 在 commit `3608ed1`，probe_pi32v2.py 第 23 行 `PIN`，data/dependencies.json 两处引用）不是「只有解码器」，而是一个**完整的 pi32v2 工具链**：

| 文件 | 作用 |
| --- | --- |
| `isa/pi32v2.toml` | 指令表（1.8MB，1165 条指令形态），唯一事实源 |
| `pi32v2isa/codec.py` | `decode()` **和** `encode()`，字节↔字段双向 |
| `pi32v2isa/asm.py` | **完整汇编器**（文本→ELF 目标文件） |
| `pi32v2isa/ld.py`、`elf.py`、`ldscript.py` | 链接器 + ELF 读写 |
| `pi32v2isa/objdump.py` | 反汇编器 |
| `pi32v2isa/oracle.py` | 对 JieLi 官方工具链做差分验证的驱动器 |
| `tools/llvm/` | LLVM 后端生成器（TableGen/汇编表）+ 指令模拟器 `sim.py` |

README 原话：「This repository's assembler, linker and objdump write and read the files JieLi's do, byte for byte」。汇编器已经存在，且**已经和 JieLi 官方 clang/汇编器做过度字节级差分测试**（ASSEMBLER.md §8：307 个目标文件逐字节一致）。

更关键的是：**本项目已经在用这个子模块**。probe_pi32v2.py 的指令模拟器就是 `importlib` 加载 `reference-cache/pi32v2-isa/tools/llvm/sim.py`，而 sim.py 内部 `from pi32v2isa import codec, table`。也就是说，同一份双向 codec 和指令表已经随仿真器一起下载到位，只是汇编方向（`codec.encode` + `asm.py`）没被项目代码调起来用。

**所以「写一个 pi32v2 汇编器」这件事本身不用做——它已经写好了，且经过官方工具链差分验证。剩下的唯一真实工作是把现有的 `pi32v2isa as` / `codec.encode` 接入项目流程（薄封装 + 文档），量级是半天以内。**

## 1. 现有 codec 的编码信息可复用度：已经是双向的，不是单向

### 1.1 指令表怎么建模编码

每条指令形态（`Insn`，table.py）有四个核心字段：

- `encoding`：每 16 位字一条 16 字符的字符串（1/2/3 条 = 16/32/48 位指令）。字符语义：`0`/`1`=固定位，`-`=解码忽略、汇编写 0，小写字母=操作数字段位。同名字母跨字按出现顺序、高位在前拼接成一个字段。
- `fields`：操作数名 → 类型（`gpr`/`uimm`/`simm`/`modimm`/`bit`/`invbit`/`rmask`/`srmask`/`pair`/`pair_regs`/`rlist4`/`sr`/`enum`），带 `scale`（打印值=原始值×scale+add）、`add`、`zero_as`（原始 0 代表的特殊数）、`asm_range`（汇编器可接受的收窄范围）。
- `syntax`：objdump 风格的文本模板（`{x}`=字段，`{+x}`=值为 0 时不打印）。
- 计算属性 `mask`/`value`/`field_bits`：直接从 encoding 字符串算出，用于匹配和置位。

这套建模**天然双向**：[静态]
- 解码：`codec.decode(table, data, offset)` 用 `value & mask == value` 匹配，再按 `field_bits` 抽出各字段原始值，`format()` 反向生成文本。
- 编码：`codec.encode(insn, values)` 从 `insn.value` 出发，按 `field_bits` 把各字段原始值填进对应位，返回字节（超出位宽会 `ValueError`）。`__init__.py` 里 `encode` 与 `decode` 并列导出。

也就是说，「给定语义就能反推字节」不是需要新写的逻辑，是 codec.py 第 49–60 行已经存在的 12 行代码。指令定义的 `encoding`+`fields` 就是声明式的「可反向生成」定义，不需要 Sleigh 也能反向。

### 1.2 代表性指令在 codec 里的表示（[静态]，编码串来自 isa/pi32v2.toml，[实测] 往返验证）

**`li.u32 r0, imm`（48 位，加载任意 32 位常量）**
```
encoding = ["111111111100rrrr", "iiiiiiiiiiiiiiii", "jjjjjjjjjjjjjjjj"]
fields   = { r = "gpr", n = { kind = "uimm", letters = "ji" } }
```
低两字是 32 位立即数，**低半字在前**（letters="ji"，j=低半）。[实测] `encode(li.u32, {r:0, n:0x12345678})` = `c0 ff 78 56 34 12`，`decode` 回读 `r0 = 305419896`，完全一致。

**`st.w.sp.l [sp+8], r9`（32 位，任意源寄存器的栈字存储）**
```
encoding = ["1110100111010100", "rrrrnnnnnnnnnn01"]
fields   = { n = { kind = "uimm", scale = 4 }, r = "gpr" }
```
`scale=4` 表示打印的偏移 = 原始字段 × 4。[实测] `encode(st.w.sp.l, {n: 2, r: 9})` = `d4 e9 09 90`，`decode` 回读 `[sp+8] = r9`。

**`bne.and.modimm.ri.l`（48 位，寄存器掩码后与零比较并条件分支）**
```
encoding = ["1111111101100001", "rrrriiiiiiiiiiii", "nnnnnnnnnnnnnnnn"]
fields   = { r = "gpr", i = "modimm", n = { kind = "simm", scale = 2 } }
syntax   = "if (({r} & {i}) != 0) goto {n}"
```
`i` 是 12 位 modified immediate（ARM Thumb-2 风格的 8 位重复/右旋编码，见 codec.modimm）；`n` 是有符号分支偏移 ×2。注意两条复杂点：modified immediate 的 0 有 4 种原始编码（汇编器会择一），且条目里 `asm_wrong` 列了官方汇编器对这种零掩码形态的已知误汇编——这些都已经被 asm.py 处理。

**`mov.rr.p`（16 位，并行发射对的 A 半）**
```
encoding = ["11010110yyyyxxxx"]
fields   = { x = "gpr", y = "gpr" }
syntax   = "{x} = {y}  #"
```
`mov.rr` 的并行孪生（低 13 位相同、高 3 位改 110），尾部打印 `#`，与下一条 16 位 load/store 一起发射。

### 1.3 指令集形状：不是 32 位定长（纠正任务里的一个假设）

[静态] ISA.md §4：pi32v2 指令字 16 位、小端、逐字取；**变长 16/32/48 位（2/4/6 字节），首字决定长度**（`0000..DFFF`=16 位，`E000..FFFF`=32 位，`FF00..FFEF` 里 99 个字开头的是 48 位）。所以任务描述里「pi32v2 是 32 位定长指令集（应该）」也不成立。

会让汇编器复杂的特性（asm.py 已全部处理）：
- **伪指令/最短形式选择**：`r1 = 5` 同时匹配 li.u8/li.s16/li.modimm/li.u32，规则是「最短者胜」+ 一组并列偏好；分支从最短形式开始「grow」直到放得下（ASSEMBLER.md §3）。
- **并行发射对**（`A #` + 16 位 load/store）：不是延迟槽。asm.py §6 校验 A 必须有 `.p` 孪生、B 必须是简单地址的 16 位 load/store。
- **块**（`rep N {` / `if (...) {` 的 body 长度字段由汇编器从 body 自动算出）。
- **modified immediate** 的双向转换。
- **寄存器列表/掩码**（`{r3-r0}`、`{rets, r3-r0}`）。

**关于「延迟槽」**：[静态][推断] pi32v2 没有 MIPS 式延迟槽——它是 Blackfin 派生的并行发射核心（ISA.md §1）。eq-toggle/hooks.S 里 `goto` 后跟 `nop` 是补 6 字节补丁站点的填充（goto 占 4 字节 + nop 2 字节 = 6 字节，对齐原 6 字节指令），不是延迟槽。真正的复杂性是并行对 `#`，不是延迟槽。

## 2. 实现方案对比

先给结论，再展开。**推荐：直接用现有 pi32v2isa（方案 a 的已完成形态），不要另写任何汇编器。**

| 方案 | 现状 | 结论 |
| --- | --- | --- |
| (a) 在 pi32v2-isa 里加编码器 | **已存在**：codec.encode + asm.py，且经官方工具链差分验证 | 无需新做，只需接入 |
| (b) 从 codec 程序化生成编码器 | 编码器已与解码器同源同表（同一个 toml + 同一份 field_bits） | 多余 |
| (c) 独立手写汇编器 | 要自己重写文本解析、伪指令选择、并行对、块、reloc | 重复造轮子，负收益 |

### 方案 (a)：在 pi32v2-isa 里加编码器

**这不只是「天然支持双向」，而是双向已经写完并验证过。** [静态] codec.py 有 `encode()`；asm.py（86KB）是完整的文本汇编器，处理了表达式（64 位、LLVM 优先级）、符号/重定位、宏（`.macro/.rept/.irp/.if*`）、伪指令（`.section/.org/.align/.byte/.space/.incbin/.ascii`）、最短形式选择、分支 grow、modified immediate、并行对、块、`.S` 走 cpp。ASSEMBLER.md §8 记录了与 JieLi 官方汇编器的差分测试：11 个 FM-1 源 + 279 个 clang -S 输出 + 30 个片段，**307 个对象逐字节一致**；链接后 loaded sections 也一致。

唯一不做的事：patch.c 这类 **C 源码的编译**（那需要编译器前端 + LLVM 后端，见 §4）。

### 方案 (b)：程序化生成编码器

没有意义。[静态] 编码和解码本来就共用同一个 `Insn`（encoding 字符串 + fields + field_bits），`encode` 就是「把解码信息的 field_bits 反过来用」。不存在「需要从解码信息生成」的缺口。

### 方案 (c)：独立手写汇编器（类似现有 hooks.S + 外部 clang 的路径）

负收益。[静态] 要自己重写的东西正是 asm.py 已经解决、并且是「和官方工具链逐字节对齐」的那一大堆边角：伪指令选择顺序（li/分支 grow 的精确规则）、modified immediate 的 4 种 0 编码、并行对 B 半的合法性校验、块长度字段回填、符号/重定位。手写版本要么在这些边角上和官方不一致（给后续「字节级 diff 验证」埋坑），要么投入远大于「直接用现成的」。**更实际的对比对象是：现状（外部 clang 汇编 .S）vs 换成 pi32v2isa 汇编 .S**——两者产物可互验，而 pi32v2isa 免掉外部 toolchain 依赖。

## 3. 最小可用闭环（MVP）建议

目标：把一段新补丁以汇编文本写出来 → 编码成字节 → 喂给现有 codec 解码验证往返一致，最终替换/对齐现有外部 clang 的 `.S→.o` 一步。

**步骤 0：把 pi32v2isa 依赖就位（确定性机械工作，~半小时）**
- 现状：`tools/reference-cache/` 在 .gitignore 里，不入库；worktree 需按「路 BX 先例」软链复用主仓已钉死的缓存（probe_pi32v2.py 的 `upstream()` 会在缺失时 `--fetch-simulator` clone pin 3608ed1）。本路在 worktree 里没建软链，直接读主仓 `/home/felix021/code/headsets/h3s/tools/reference-cache/pi32v2-isa` 完成验证。
- 产出：确认 worktree 内 `tools/reference-cache/pi32v2-isa` 软链指向 pin 3608ed1 的缓存。
- 验收：`python3 -m pi32v2isa check` 在软链目录内 exit 0（1165 entries, 0 problems）。

**步骤 1：用现有汇编器汇编现有补丁（已实测通过）**
- 直接 `python3 -m pi32v2isa as tools/eq-toggle/hooks.S -o hooks.o`，或 Python 侧 `asm.assemble_source(...)`。
- [实测] 本路已把 hooks.S 汇编成功（1672 字节 ELF 目标文件），objdump 回读逐条一致（`goto`/`call`/`nop`/`r5 = b[r0+0] (u)`/`if (r2 != 7) goto`/`r1 = r1 & 0x7F`/`r0 = 0`/`r0 = [r6+4]` 等）。说明项目现有的补丁汇编源就是 pi32v2isa 能吃的语法。
- 产出：hooks.o。
- 验收：与外部 clang 汇编的 hooks.o 逐节字节一致（或 reloc 等价）。

**步骤 2：往返闭环验证（已实测通过）**
- 用 `codec.decode` 把产物的 .text 字节逐条解码，与手写源逐条比对；再用 `codec.encode` 反向编码比对字节。
- [实测] 本路已对 li.u32 / st.w.sp.l / add.rrr / mov.rr.p / ld.w 等做了 encode→decode 往返，零 diff。
- 可选更强校验：`python3 -m pi32v2isa oracle asmcompare hooks.S`（需下载官方 toolchain 作 oracle，见 §4 红线）。
- 产出：一份往返一致验证记录。
- 验收：汇编→字节→解码零 diff；与官方汇编器产物一致（若跑 oracle）。

**步骤 3（可选，ROI 最高的一步）：替换 build.sh 里的外部 clang .S→.o**
- 现状 build.sh 用 `"$toolchain/clang" -integrated-as -c hooks.S`。可改为 `python3 -m pi32v2isa as hooks.S -o hooks.o`。
- 注意：patch.c 的 `.c→.o` 仍是 C 编译，pi32v2isa 不覆盖（除非接 LLVM 后端）。所以这一步只替换汇编源那条腿；若要彻底摆脱外部 toolchain，还差 C 前端。

**步骤 4（远期，不在本 R1 范围）：C 前端**
- 编译 C（patch.c、新功能代码）需要 pi32v2 的 LLVM 后端（clang + ld.lld fork：jvitkauskas/pi32v2-llvm，其表由 pi32v2-isa 的 tools/llvm 生成）。这是独立的大工程（另一个仓库 + 编译工具链），不是「写汇编器」的延续。
- 在「EQ 载荷扩展」这类当前需求里，载荷本身就是汇编写的（hooks.S + patch.c 里 patch.c 是生成载荷字节的辅助 C），汇编器这一环已经能闭环；C 前端属于「C 子集编译器」这一更远目标。

## 4. 工作量与风险

**确定性机械工作（低风险，半天以内）**
- 接入 + 薄封装 + 文档 + 往返验证脚本。所有编码信息已存在且经官方差分验证，没有「猜 opcode」的成分。[静态][实测]

**需要 ISA 规范核对 / 可能踩坑的地方（诚实标注）**
1. **BR28 专属指令是否在表内——未正向穷举。** [推断] 子模块的 oracle 覆盖声明（GAPS.md "Encodings: done"，全部可解码首字都在表内）是针对 AC791N（`-mcpu=r3`）做的；行为级硬件验证也只在 FM-1（AC791N）上做，data/dependencies.json 里明写 "not BR28 hardware certification"。但：H3S 的 app.bin 本项目各路线（BR/BT/BU 等）都用同一 codec @3608ed1 反汇编过，未报告过 BR28 专属不可解码指令；disassemble_app.py 和 build.sh 都用 `-mcpu=r3`，与子模块主目标同核。纯**编码**层面是 ISA 级、芯片无关的，风险很低；「BR28 行为是否与 AC791N 完全一致」与汇编器无关，不影响本 R1 结论。
2. **asm_no_syntax 的少数形态不可汇编。** [静态] GAPS.md：objdump 打印 `??` 条件的形式和 4 个浮点条件（`!=`/`u==`/`uno`/`ord`）没有汇编语法。这些在 FM-1 上会复位芯片、正常代码不会用到。若补丁需要，只能手写 `.short 0x....` 字节——现有 asm.py 已支持 `.short`。风险可忽略。
3. **pi32v2isa 与 JieLi 汇编器的「故意不同」**（ASSEMBLER.md §7）：拒绝 `[r4-11]`、odd 分支偏移报错、修了 late-`.equ` 半字交换 bug 等。这些「不同」恰恰是为了修官方 bug，对「字节级正确」是加分项，但若要和官方产物逐字节比对，需要知道这些差异点（asmcompare 已覆盖）。
4. **子模块是外部仓库 + 参考缓存不入库。** 需要 pin（已是 3608ed1）+ 软链复用，且绝不把私有 ROM 路径/字节写进子模块或任何提交（红线）。这是流程纪律，不是技术风险。

**覆盖度总结**：1165 条形态全部 `status=confirmed`（编码+语法都对照 JieLi oracle 验证过）；一个 FM-1 工厂固件 97% 可解码、其余是数据；oracle 覆盖五个编码区全部完成。对「仿真用到子集」而言，**codec 覆盖的是全 ISA，不是子集**。[静态]

## 5. 一句话结论

**pi32v2 汇编器不需要新写——reference-cache/pi32v2-isa @3608ed1 里已经有 codec.encode（双向）+ 完整汇编器 asm.py + 链接器，且与 JieLi 官方工具链做过字节级差分验证；R1 的真实工作量从「写汇编器」降为「把现成汇编器接入项目流程」，半天以内，唯一未覆盖的是 C 源码编译（那是 LLVM 后端，另一个量级）。**
