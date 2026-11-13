# pi32v2 汇编器薄封装（路 G / sim-asmwrap，R1 落地实施）

**基线**：main d5c4175；**范围**：新增 `tools/pi32v2_asm.py` + `tools/test_pi32v2_asm.py`，不改任何既有 probe/tools 行为、不跑改报告 probe；`tools/reference-cache/pi32v2-isa` 软链复用主仓钉死缓存（路 BX 先例，不入库）。**证据分级**：延续 [assembler-feasibility.md](assembler-feasibility.md)（路 D）的 [静态]/[实测]/[推断] 约定。

## 0. 一句话

把路 D 调研确认「已经存在、且与 JieLi 官方工具链做过字节级差分验证」的 pi32v2-isa 汇编器接进项目，做成可复跑的薄封装 + 往返验证闭环。**接入已打通；这是「汇编器接入」，不是「C 编译器」——C 源码编译仍需 pi32v2 的 LLVM 后端（另一个仓库，不在本路）。**

## 1. 薄封装是什么

`tools/pi32v2_asm.py` 只做一件事：从 `tools/reference-cache/pi32v2-isa`（github.com/jvitkauskas/pi32v2-isa，pin `3608ed19bc21324f8ecfe79cc0018d05e43d9563`，与 probe_pi32v2.py 第 23 行同一 pin）导入 `pi32v2isa.{codec,table,asm,elf,ld,objdump}` 六个子模块，包一层项目可直接用的 Python 函数 + 极简 CLI。

**自包含性结论（[实测]）**：pi32v2-isa 的 `pyproject.toml` 里 `dependencies = []`，没有任何 pip 依赖；整个 `pi32v2isa` 包只 import Python 标准库（`re`/`struct`/`tomllib`/`subprocess`/`pathlib` 等）。汇编这条腿（`asm.py` → `codec.encode`）**不碰 JieLi toolchain、不碰网络**——`subprocess` 只在可选的 `.S` 预处理步骤里调 `/usr/bin/cpp`（见 §3），而项目现有 hooks.S 没有任何 cpp 指令，本封装默认按纯文本直接汇编，连 `/usr/bin/cpp` 都不走。唯一一次网络访问是 `--fetch` 时把 pin 好的 pi32v2-isa clone 进 reference-cache（probe_pi32v2 的 `--fetch-simulator` 同款），之后完全离线。

## 2. 用法

### 2.1 命令行

```bash
# 首次：把 pin 死的 pi32v2-isa clone 进 reference-cache（gitignored）
python3 tools/pi32v2_asm.py --fetch

# 校验指令表完整（1165 entries, 0 problems）
python3 tools/pi32v2_asm.py --check

# 汇编文本 -> ELF 目标（纯文本，不走 cpp）
python3 tools/pi32v2_asm.py asm tools/eq-toggle/hooks.S -o hooks.o   # -> 1672 bytes

# 反汇编 ELF 目标（codec.decode + format 逐条）
python3 tools/pi32v2_asm.py objdump hooks.o
```

### 2.2 Python 函数

```python
import pi32v2_asm as W

W.load()              # 校验 pin + 树干净 + 从钉死位置 import，返回模块 dict
W.load_table()        # 1165 条指令形态（缓存）
W.assemble_text(text) # 文本 -> ELF 目标 bytes
W.assemble_file(path) # 文件（纯文本）-> ELF 目标 bytes
W.read_object(data)   # ELF bytes -> elf.Obj
W.code_sections(obj)  # 可执行节 [(name, bytes, relocs)]
W.disassemble(data)   # 逐条 (addr, insn_id, 文本, 字节)
W.encode(insn_id, **raw)   # 单条编码 -> bytes
W.decode(data, offset)     # 单条解码 -> (insn, raw字段, 长度)
W.roundtrip_one(insn_id, raw)  # encode->decode 断言同形态同字段，返回 bytes
```

## 3. 往返验证：汇编器可信的闭环

这是本路的核心验收。两条闭环都做成 `tools/test_pi32v2_asm.py` 里可复跑的 unittest（`python3 tools/test_pi32v2_asm.py`）：

1. **codec 双向闭环（[实测]，6 条全零 diff）**：`codec.encode(insn, raw字段)` → 字节 → `codec.decode` → 断言回到**同一指令形态 + 同一组 raw 字段**。因为 encode/decode 共用同一份指令表（`Insn.encoding` + `field_bits`），这等价于「写入方向和读出方向是同一条映射的正反两半」，是「汇编器写出的字节语义正确」的最强判据。

   | 指令 id | raw 字段 | 字节 | 解码文本 |
   | --- | --- | --- | --- |
   | `li.u32` | r=0, n=0x12345678 | `c0 ff 78 56 34 12` | `r0 = 305419896` |
   | `st.w.sp.l` | n=2, r=9 | `d4 e9 09 90` | `[sp+8] = r9` |
   | `st.w.sp` | n=2, r=9 | `89 22` | `[sp+8] = r9` |
   | `bne.and.modimm.ri.l` | r=0, i=0xFF, n=0x10 | `61 ff ff 00 10 00` | `if ((r0 & 0xFF) != 0) goto 32` |
   | `beq.and.modimm.ri.l` | r=0, i=0xFF, n=0xFFFC | `60 ff ff 00 fc ff` | `if ((r0 & 0xFF) == 0) goto -8` |
   | `mov.rr.p` | x=0, y=1 | `10 d6` | `r0 = r1  #` |

   `st.w.sp.l` 的 `d4 e9 09 90` 就是条件前沿 0x6065ccc 那条真实指令的字节（HANDOFF 路 CQ 反汇编钉死），往返零 diff 意味着「真固件在用的这条指令，汇编器能原样写出来、解码器能原样读回去」。

2. **文本汇编闭环（[实测]）**：`assemble_text`（asm.py 走最短形式选择）→ ELF 目标的 `.text` 字节 → `codec.decode` → 断言语义字段与源文本一致。钉死了 asm.py 的最短形式选择：`[sp+8] = r9` 选 `st.w.sp`（16 位）、`[sp+256] = r9` 选 `st.w.sp.l`（32 位）、`r0 = 0x12345678` 选 `li.u32`（48 位）；`if ((r0 & 0xFF) != 0) goto 8` 直接落 `bne.and.modimm.ri.l`（modimm 需要 12 位字段，只有这个形式能装）；并行对 `r6 = r0 #` 后跟 16 位 store `[r4+0] = r5` 正确配对成 `mov.rr.p ∥ st.w`。

3. **真实补丁源全量往返（[实测]）**：把 `tools/eq-toggle/hooks.S` 整个汇编 → **1672 字节目标**（与路 D 实测一致）→ 六个节逐节 objdump → **80 字节代码 12 条重定位逐条零 `.short` 空洞**，且每条指令 `encode(decode(字节))` 与原始字节**逐字节相同**（对带重定位的 `goto.l`/`call.l`，`.o` 里偏移字段是 0、加数在重定位表里，指令字节本身不受影响）。真实用到的指令形态 `goto.l`/`call.l`/`nop`/`ld.bu`/`bne.ri`/`ld.bu.l`/`and.modimm.rri`/`li.u8`/`mov.rr`/`ld.w` 全部覆盖。

## 4. 与旧「外部 clang」路径的对比

| | 旧路径（build.sh） | 本路薄封装 |
| --- | --- | --- |
| .S → .o | `"$toolchain/clang" -integrated-as -c hooks.S` | `python3 -m pi32v2isa as hooks.S -o hooks.o`（或 `pi32v2_asm.py asm`） |
| 依赖 | JieLi toolchain（clang/ld/objdump 二进制） | 纯 Python + 标准库，pin 死到 reference-cache |
| 产物 | hooks.o（ELF 目标） | 同一布局的 hooks.o（ELF 目标，1672B） |
| 可信度 | 官方汇编器（但二进制不可读） | 与官方汇编器做过字节级差分验证的社区实现 + 本路往返闭环 |
| .c → .o | clang 编译 patch.c | **不覆盖**——这是 C 编译，需 LLVM 后端（见 §5） |

两者产物可互验：路 D 已把 hooks.S 用 pi32v2isa 汇编成功，与外部 clang 的 hooks.o 走同一 ELF 布局。本路只替换「.S → .o 汇编」这一条腿，不替换 `.c → .o`。

## 5. 诚实标注：这不是 C 编译器

- 本路是**汇编器接入**：文本汇编（`.s`/`.S`/内联文本）→ ELF 目标 → objdump 反汇编，加上 codec 的双向往返验证，全部打通且可复跑。
- **C 源码编译（patch.c、未来新功能代码）不在本路**：那需要 pi32v2 的 LLVM 后端（jvitkauskas/pi32v2-llvm，其表由 pi32v2-isa 的 tools/llvm 生成），是另一个仓库 + 一套编译工具链的量级。因此 eq-toggle 的完整 build.sh（clang 编 patch.c + ld 链接）目前**无法仅靠本封装替代**——要彻底摆脱外部 toolchain，还差 C 前端这一环。
- 可选的 `.S` 预处理（`asm.py` 的 `preprocess`）走 `/usr/bin/cpp`；本封装默认**不做 cpp**，直接按纯文本汇编。若未来补丁源用到 `#include`/`#define`，要么改用 cpp（回到系统预处理器依赖），要么避免这些指令。
- 与路 D 一致的红线：不改 reference-cache/pi32v2-isa 子模块内容（只能 import）；不连真机、不刷机；EQ 候选 UNTESTED 状态不变；私有 ROM 路径不进任何文件。

## 6. 验证

- `python3 tools/test_pi32v2_asm.py` **exit 0**（5 条测试：表完整 / encode→decode 零 diff / 文本汇编往返 / hooks.o 尺寸与节布局 / hooks 全指令逐字节重汇编一致）。
- 已接入 `tools/check_local.py --units`（`tools/test_pi32v2_asm.py` 一行，与既有 `test_*.py` 单测族并列）；默认 legacy 报告不受影响（本路是新增工具，不碰 probe/boot 系列）。
