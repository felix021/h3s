# O3 Memory.read 访存路径优化设计（路 O3D / sim-o3d，2026-11-15）

实施级设计文档（第二波 CPU 优化预研，纯文档零 tools/ 改动）。上游依据：
[perf-budget-analysis.md](perf-budget-analysis.md) §5 O3 行与 §6 codex 定调第 4 条
（「O3 拆两次：先查找路径（保字节 dict），再存储形态（字节数组+位图）」）。
第一波 OPT1（3791020，`tools/br28_exec_memo.py`）已落地 takes_state/operands/format
三项 memoization（1.482x，31/31 报告逐字节一致）；本文档为第二波的两阶段设计。

- 基线：main `2f5df87`（worktree `sim-o3d`，本路零代码改动）。
- 对象：条件前沿场景 `p11_heap_experimental`（与 PERF/OPT1 同一调用链
  `analyze_image_trace.frontier_run`，最重配置）。
- 红线：**不改变任何可观察行为**——Memory 的别名/MMU/未初始化语义以实现为准，
  全部优化只动「同一结果的计算方式」；验收=报告逐字节相等（§7）。

## 1. 基线复测（OPT1 后，本路实测）

py-spy 200Hz×30s 无扰动采样（4M 预算 run 的 ~600k 步活跃窗）+ 计数 wrapper
200k 步（开销 <2%，与 PERF §2.3 同方法）：

| 指标 | PERF 时点（OPT1 前） | 本路复测（OPT1 后） |
|---|---|---|
| 干净速率 | 15.5k 步/s | **19.1k 步/s**（300k 预算，15.6s） |
| A 类（主循环+CPU 壳+Memory 读写）自耗时 | 37.8% | **~36%**（仍为最大项） |
| 其中 Memory.read 路径直接可见 | — | **~13.8%**（行级见 §2.2） |
| inspect 反射 | 13.8% | ~0（OPT1 消除） |
| trace 签名族（_register_signature+after_step） | 12.8% | ~18.3%（占比被动升高） |
| advance 仲裁 | 9.7% | ~8.6% |

步进段行级自耗时（py-spy，本路实测，仅列访存路径相关行）：

| 帧:行 | 自耗时 | 内容（probe_boot.py） |
|---|---|---|
| read:184 + genexpr:184 | **3.7%** | `next((a for a in range(...) if a not in self.ram))` 逐字节未初始化检查 |
| read:187 + genexpr:187 | **3.3%** | `bytes(self.ram[a] for a in range(...))` 逐字节 dict 装配 |
| region:153/154 | **2.1%** | regions 元组线性扫（~13-17 项） |
| read:159 | 1.0% | `require()` 入口校验 |
| read:171 | 0.9% | `detail` dict 构造（在 MMIO/PLL/Blocked 消费它之前无条件构造） |
| read:162 | 0.8% | app 范围判定+切片 |
| read:168 | 0.9% | `handoff['readonly']` 线性扫+切片 |
| decode:441/442/438 | ~1.6% | RAM/虚拟路径 fetch 的字节校验读（自修改守卫） |
| handles（br28_model） | 0.8% | MMIO 归属判定 or 链（§2.3） |
| check_flash_access | 0.4% | SFC flash 直访区判定 |

计数 wrapper（200k 步，条件前沿）每步调用次数：

| 调用 | 次/步 | 说明 |
|---|---|---|
| `sfc.check_flash_access` | **5.05** | 每次 read/write + 每次 decode fetch（probe_boot.py:432）+ 并行对二次 decode（:1909） |
| `mmu.translate` | **2.28** | mmu_model 开启时每次 read/write 全过（窗外一行直通，br28_model.py:3949） |
| `hw.handles` | 0.72 | 数据读未命中只读区时全链判定 |
| `hw.read_bytes` | 0.49 | handles 之后的数据读 fixture 探测（probe_boot.py:177） |
| `machine.after_step` / `advance` | 1.0 | 每步各一次 |
| RAM write（note_memory_write） | **0.13** | 写频率远低于读 |
| `BR28Model.read`（MMIO 命中） | 0.06 | — |

由此推得每步 `Memory.read` 总调用 ≈2.2（fetch 校验读+数据读+诊断读各占其一）。

## 2. Memory 层现状精读

### 2.1 类层次与装配链

前沿场景的唯一 Memory 实现是 `probe_boot.explore()` 闭包内的 `Memory` 类
（tools/probe_boot.py:140），**完全覆盖**上游 `sim.Memory`（pages/bytearray 形态，
tools/reference-cache/.../llvm/sim.py:32——probe_boot.Memory 的 read/write 不调
super()，上游基类在本路径不参与）。装配链：

```
analyze_image_trace.frontier_run (:383)
  → probe_irq_entry.irq_case (:1099, flag 组装)
    → probe_dualcore.dual_case (:23, machine_options)
      → probe_platform_startup.platform_case (:24)
        → probe_arch_startup.architecture_case (:27)
          → probe_clock_startup.startup (:22, handoff regions 拼装)
            → probe_boot.explore (:97, Memory/CPU 定义+主循环)
```

Memory 实例属性（即 SNAP M2 的三 dict 契约，§4）：`ram`（address→byte 逐字节
dict）、`unknown_origins`（未知字节→物理来源）、`icfg_spills`（字地址→部分已知
IcfgValue）+类属性 `regions`（闭包拼装的 (lo,hi,kind) 元组）与 `operation`
（'read'/'fetch' 瞬态标记）。

### 2.2 Memory.read 的「8+ 函数调用」分解表

probe_boot.py:158-192，条件前沿场景（hardware/mmu/sfc 全开）每次数据读的完整
调用链——PERF §2.2「每次读 8+ 函数调用」的逐项展开：

| # | 调用 | 锚点 | 每次成本 | 备注 |
|---|---|---|---|---|
| 1 | `require()` 入口校验 | :159 | 1 次调用 | size/范围检查，恒真 |
| 2 | `hardware.mmu.translate()` | :161-162 | 1 次调用 | 窗外（br28_model.py:3949）一行 return；**窗内回调 `read_physical=Memory.read 自身`读页表（递归）** |
| 3 | `hardware.sfc.check_flash_access()` | :163 | 1 次调用 | flash 直访区（0x6000000-0x8000000）外立即返回 |
| 4 | app 范围判定+切片 | :164-165 | 内联+切片分配 | `app[address-ENTRY:...]` 每次分配新 bytes |
| 5 | rom 范围判定+切片 | :166-167 | 内联+切片 | 同上 |
| 6 | `handoff['readonly']` 线性扫 | :168-170 | N 项迭代+切片 | N≈1-2 |
| 7 | `detail` dict 构造 | :171 | 1 次分配 | **在消费者（PLL event :180 / Blocked :186-192）之前无条件构造** |
| 8 | `hardware.handles()` | :172 | 1 次调用 | 内部 40+ 成员 or 链（br28_model.py:5194-5249，最坏全扫） |
| 9 | `hardware.read_bytes()` | :177 | 1 次调用 | 字节 fixture 探测（br28_model.py:6015，P2M heap 窗外快速 None） |
| 10 | PLL 判定 | :179 | 内联 | — |
| 11 | `self.region()` | :182 | 1 次调用 | regions 元组线性扫（13-17 项，§2.4） |
| 12 | missing 检查 genexpr | :184 | genexpr 构造+逐字节 `__contains__`×size | 4 字节读=4 次 dict 查询 |
| 13 | 装配 genexpr | :187 | genexpr 构造+逐字节 `__getitem__`×size+bytes 装配 | 4 字节读=4 次 |

即：每次 4 字节数据读 ≈ 8 个直接函数调用 + 2 个 genexpr 对象构造 + 8 次 dict
操作；fetch 读额外经过 decode 的 operation 设置/恢复（probe_boot.py:433-458
try/finally）与 RAM 路径的缓存字节校验读（:441-445，自修改守卫，每次 fetch
一次 read(pc,size)+bytes 比较）。

### 2.3 调用方语义清单（谁依赖什么）

| 调用方 | 锚点 | 粒度 | 依赖的语义 |
|---|---|---|---|
| fetch（CPU.decode） | probe_boot.py:429-458；上游 sim.py:87-90 | 2/4/6 字节 | operation='fetch' 标记：**不走 MMIO/fixture/PLL 分支**（:172/:174/:179 三处 `!='fetch'` 门）；RAM/虚拟映射每次 fetch 读实际字节校验解码缓存（自修改守卫，:440-445）；ROM 尾部截断特判（:447-455） |
| 数据读（上游语义模型） | sim.py:57-60 State.load → mem.read | 1-4 字节 | MMIO 读在此命中（:172 handles→hardware.read）；**逐字节未初始化≠零**（:184-186 Blocked）；PLL 假设读（:179） |
| 字读（pop/rti 帧恢复） | sim.py:224/:288 mem_word；probe_boot.py:745/:831/:839 | 4/12 字节 | 同数据读；rti 帧三字读是控制流恢复路径 |
| 主循环诊断行 | probe_boot.py:1911-1912 | size 字节 | 每步一次 `bytes=c.mem.read(c.pc,size).hex()`（只留 64 行、即刻丢弃——O2 域，本文档不重复设计） |
| MMU 页表走查 | br28_model.py:3956 `read_physical(entry,2)` | 2 字节 | translate 窗内**递归调 Memory.read 读页表项**（operation 继承当前值）；页表在 mmu_page_table region（RAM 存储）——别名语义：VA 与 PA 走同一 ram dict |
| abstract_word（部分已知字） | probe_boot.py:227-246 | 4 字节 | 翻译后落 readonly/MMIO 边界判定；未知字节运输的读侧 |
| write 路径 | probe_boot.py:194-225 | 1-8 字节 | 判定序 translate→rom 只读拒→handles→字节写钩子→region→icfg_spills overlay→ram.update→unknown_origins 清除→note_memory_write→event；**unchanged-value RMW 仍发 bus write**（AGENTS 技术不变量） |
| 字节写钩子 | probe_boot.py:209-211 handles_byte_write | 1 字节 | P33 窗单字节写直达 write-only 记录，**不经读回** |
| write_abstract_byte（unknown 运输） | probe_boot.py:250-263 | 1 字节 | 直改 ram/unknown_origins（**绕过 write 的记账**——memo 失效义务点，§4.1 A3b） |
| SNAP M2 capture/restore | world_snapshot.py:904-928/:412-418 | 全量 | **不走 read/write，直读直写三 dict**（§3） |
| memory_snapshots 报告 | probe_boot.py:2008-2016 | 快照窗 | `c.mem.ram.get(a)` 逐字节直读（已知性=报告语义） |
| watch_ram_after_pc 诊断 | probe_boot.py:485-493 | 有界窗 | 同上直读 |
| 装载期 seeds | probe_boot.py:1755-1758 | — | `c.mem.ram.update(...)` 直写（主循环前，任何 read 之前） |
| pair 双镜像 | analyze_image_trace.py:442-459 | — | 两次 frontier_run 顺序跑、各自新建 Memory——无跨 world 状态 |

**绕过 Memory.write 直改 ram 的全部点位**（实施时 memo 失效义务的完备清单）：
write_abstract_byte（probe_boot.py:262-263/:276/:284）、装载 seeds（:1758，首读
前）、apply_ram_section（world_snapshot.py:412-418，restore，主循环前）。测试
文件（test_world_snapshot.py/test_task_unknown.py）不在运行路径。

### 2.4 regions 表的实际形状

条件前沿场景 Memory.regions 装配（13-17 项）：bootstrap_stack(0x123000-0x123840)
+ hypothesized_boot_argument_object(0x19fc28+24) + handoff 5 项
（typed_boot_device_info 0x19fd00+24 / qcy_boot_info 0x122800+40 / qcy_bss
0x125320+0x530c / qcy_initialized_data 0x124840+0xae0 / qcy_ram_code
0x12a640+0x48d4）+ startup 3 项（retained_update_parameters / rom_mask_context
0x19fd68-0x19fda4 / mmu_page_table 0x120000-0x122800）+ arch 2 项
（irq_vector_table 0x19fe00-0x1a0000 / physical_heap 0x134500-0x19fb80）+
CORE1_STACK(0x123840-0x124840) + 条件 fixture 窗（erased_bankb 四窗、p11 窗等，
按 flag 增减）。**实测两两不重叠**（边界相接：bootstrap_stack 顶=CORE1_STACK
底=0x123840；DATA 尾=BSS 底=0x125320；physical_heap 尾 0x19fb80<BOOT_ARGS 底）。
region 匹配语义=「元组序第一个 lo<=addr 且 addr+size<=hi 者赢」（probe_boot.py:152-156）。

## 3. 快照 M2 兼容约束（SNAP 守卫清单）

world_snapshot 对 Memory 的契约，全部是 O3 实施不可破坏的硬守卫：

| 守卫 | 锚点 | 内容 |
|---|---|---|
| 属性集守卫 | world_snapshot.py:893-901 `_check_m2_shape` | `vars(mem)` 必须⊆{ram,unknown_origins,icfg_spills}+M2_TRANSIENT('operation')——**Memory 实例挂任何新属性（缓存/索引）都会在 capture/restore 双侧拒载** |
| 干净停点守卫 | :898-901 | operation!='read' 时拒载（快照只在停点捕获） |
| M1 identity 守卫 | :792-798 | 每个 CPU 的 `mem` 必须 `is` world.mem（双核共享单实例——**不可按核分片缓存**） |
| manifest 计数守卫 | :924-928 | restore 后 len(ram)/len(unknown_origins)/len(icfg_spills) 必须与捕获计数相等 |
| RAM 段编码契约 | :328-373 encode/decode_ram_runs | 洞=未知字节（缺键≠0 值）；runs 排序、重叠拒绝 |
| code fingerprint | :668-684 + :58-62 FINGERPRINT_FILES | 哈希 probe_boot.py/br28_model.py 等全部仿真源——**任何 O3 改动自动使旧快照拒载**（设计内：重新生成是唯一迁移路径，design §2.3） |
| M2 字段表 | :1269-1272 | `fields={'Memory':('ram','unknown_origins','icfg_spills')}`——存储形态改造（方案 D）必须同步改这里 |

推论：**读缓存挂在 explore 闭包局部变量**（Memory.read/write 经闭包引用）而非
实例属性，可整体绕开属性集守卫且 world_snapshot 一行不改；restore 后的失效
由 explore 的 restore 分支显式清空承担（§4.1 A3b 约束 3——不依赖时序推断）。
存储形态改造（方案 D）则必须改 M2 adapter（wire format 可保兼容，§4.2）。

## 4. 优化方案设计

两案对比 + codex PERF 定调的两阶段路线。共同前提：判定序
（require→translate→SFC→app→rom→readonly→handles→read_bytes→PLL→region→missing→装配）
与 region 先声明先赢、未初始化≠零、fetch 不走 MMIO/fixture/PLL、MMU 递归走查、
unchanged-RMW 仍发 bus write——**全部语义原样保留，只改同一结果的计算路径**。

### 4.1 方案 A：查找路径+读结果 memo（推荐，阶段一）

四个正交子项，各自独立提交可单独回退：

**A1 判定序微重排**。`detail` dict 构造（probe_boot.py:171）从 handles 之前
移到首个消费者之前（:179 PLL 分支与 :186/:189/:191 Blocked 分支）。dict 字面量
与 `self.operation`/`hex()` 读取均无副作用，构造点后移=纯等价。
收益 ~0.7%（read:171 行 0.9% 的大部分）；工作量 ~5 行；风险≈0。

**A2 region 快路径**。Memory.__init__（或首次 region() 调用）时：
(a) 装载期检测 regions 两两是否重叠（O(n²)，n≤~20 一次性）；
(b) 无重叠→构建排序数组，region() 用 bisect（lo 数组二分+hi 校验）；
有重叠→保持现行线性扫（**回退而非拒绝**——断言不拒绝任何原本可运行的配置，
零行为变化；重叠时排序会改「先声明先赢」的匹配结果，故回退）。另加 last-hit
单项快指针（上次命中的 region 先试——局部性：数据读地址聚集在 heap/栈/页表
少数区域；hint 命中即真命中，未命中走 bisect，结果恒等；hint 无需无重叠
前提——它只是先行试一次完整判定）。regions 是类属性元组，索引结构与快指针
挂闭包/类外（**不进 vars(mem)**，§3）。codex 提醒采纳：~15 项的表上二分收益
有限，last-hit 指针可能已拿走大头——实施时 last-hit 可单独先上，bisect 以
分项计时决定去留。
收益 ~1.6%（region 2.1%→~0.5%）；工作量 ~25 行；风险低（回退保底+hint 恒等）。

**A3a 合并双重遍历（A3 的无缓存前置子项）**。missing 检查（:184）与装配
（:187）是同一区间上的两次逐字节遍历——合并为一次：直接装配
（`bytes(...)` 逐字节取，遇 `None` 即缺失字节），遇缺失时按原语义定位
first_missing 并抛原 Blocked。**前提核对**：现实现 missing 检查先抛（缺第一个
字节就停，后续字节不查）；合并版在装配循环里首次遇 None 时抛同一
Blocked（first_missing=该地址）——「检查的字节集合」「异常内容与先后顺序」
与原实现恒等（缺失字的装配产物本来就被丢弃）。省掉一次完整遍历+一个
genexpr 构造，**无任何失效义务**。收益 ~1.5-2%；工作量 ~10 行；风险≈0
（纯循环合并，对照测试钉 first_missing 语义）。

**A3b RAM 读结果 memo（本方案主体，A3a 之上）**。explore 闭包局部 dict
`ram_read_memo`；Memory.read 的 **region 分支（:182-187）**改为：
- 未命中：A3a 路径——缺失（Blocked）不缓存→装配 bytes→
  `ram_read_memo[(address,size)]＝bytes` 返回；
- 命中：直接返回缓存 bytes。
约束（规范式失效契约，实施与 review 按此核查，不按点位枚举）：
1. memo **只替代 region 分支的缺失检查+装配**；判定链（require/translate/
   sfc/app/rom/readonly/handles/read_bytes/PLL）每次照走，其副作用（MMIO 读
   计数、PLL event）零变化；**不得缓存这些操作的任何效果**。
2. 缓存值类型恒为 `bytes`（不可变，跨调用共享安全——OPT1 format memo 同款
   论证）；键=(translate 后的最终物理地址, size)——MMU 页表更新后翻译本身
   照走，不缓存翻译结果；页表递归读（br28_model.py:3956）同走 region 分支
   可命中，但 PTE 写经 Memory.write 必触发失效。
3. **失效不变量**：RAM 已知字节的「内容或存在性」的任何变更（赋值/删除/
   批量/容器替换、含 known→unknown）都必须发生在下一次可能命中之前——实现
   为写路径全清：Memory.write（ram.update :222 处，含 icfg_spills overlay 与
   unknown_origins 清除之前）、write_abstract_byte（:262-263 三处）、
   apply_ram_section（world_snapshot.py:415）三入口 `ram_read_memo.clear()`。
   全清策略下「部分修改后抛异常」不成问题（清空先于/伴随变更发生，保守永
   不过期）。**restore 后显式清空**（explore 的 restore 分支 :1816-1818 之后
   加一行 clear）——不依赖「主循环前 memo 必空」的时序论证，虽然该论证当前
   成立（构造链/seeds/restore 均不调 read，本路已核）；apply_ram_section 的
   clear 由 world_snapshot 侧无法触达闭包，故 restore 分支的显式清空是
   apply_ram_section 入口的等价实现点。
4. **直改点核查记录**（本路已核，实施 PR 复核）：绕过 Memory.write 直改
   ram 的全部点位=write_abstract_byte（:262-263/:276/:284）、装载 seeds
   （:1758，任何 read 之前）、apply_ram_section（restore）；post_stop 直调段
   （key_chain S/AJ 两路）的 fixture 安装与写**全部经 cpu.mem.write**
   （probe_irq_entry.py:931-932/:946 与 S 路同构）；br28_machine.py 与
   br28_model.py **零 ram 直写**（grep 核实）；测试文件不在运行路径。
5. **容量上限**：memo 条目数上限 1<<16，超限全清（扫描型负载下地址多样性
   无界，确定性容量策略替代无界增长；不引入 LRU）。
6. fetch 读同走 memo（RAM code 自修改校验读 :441-445 命中同一 (pc,size) 键；
   指令写经 write 全清→校验读必然重建）；双核共享单 Memory（M1 identity
   守卫）——memo 归属该实例对应的唯一闭包，双核读同一缓存正确（同一存储）。

**收益为待测假设**（codex 修正采纳）：请求命中率≠时间加权命中率（长读短读
成本不同），写全清的期望损失取决于「失效间隔内的 RAM 请求数与 (地址,长度)
多样性」，不能由 2.2/0.13≈17 的比值推出。实施前补三项诊断计数（不进正式
报告）：失效间隔 RAM 请求数分布、唯一 (地址,size) 键数、分阶段（启动/调度/
稳定循环）实测命中率。按 70-85% 请求命中率×时间加权折算的**上界估计**
~5.5%（missing 3.7%+装配 3.3%→memo 查询 ~0.5%）；A3a 的 ~1.5-2% 无条件。
工作量 ~40 行+对照+诊断计数；风险中（失效不变量有规范式契约+核查记录）。

**A4 只读切片 memo（可选，收益小）**。app/rom/handoff-readonly 的切片结果
（:164-170）按 (address,size) memo——内容装载后不可变，无需失效。收益
~0.4-0.8%（read:162/:168 行的大半）；工作量 ~15 行；风险≈0。**与 O2 的交叠**：
O2（诊断行材料化）落地后主循环 :1911 的 fetch 读消失，A4 的 fetch 侧收益随之
转移——两案兼容（O2 删调用点，A4 加速剩余调用），不重复计入收益。

**方案 A 合计（上界估计，分项实测为准——codex 修正采纳：请求命中率≠时间
加权命中率，写全清损失不能由读写比推出，实施前补 §7.4 分项诊断计数）**：
A1 ~0.7% + A2 ~1.6% + A3a ~1.5-2%（无条件）+ A3b ~3-5%（按命中率折算上界）
+ A4 ~0.4-0.8% → 步进段自耗时 **~7-10%** → 全局 **~1.07-1.09x**；叠加 §5 的
Insn getter 缓存（~4-5%）→ 第二波合计上界 **~1.13-1.15x**。工作量 2-3 个 PR
（A1+A2 一个、A3a+A3b 一个、A4 视证据并入或放弃）；全部不动 world_snapshot、
不动 M2 契约、不动存储形态。

### 4.2 方案 D：存储形态改造（bytearray 段+已知性位图，阶段二）

RAM 区按实际 region 分配 bytearray 段+逐字节（或逐字掩码）已知性位图；
missing 检查=位图区间扫描，装配=切片。write=切片写入+位图置位。

| 维度 | 方案 A（查找路径+memo） | 方案 D（存储形态） |
|---|---|---|
| CPU 收益 | **~7-8.5%**（读侧 missing+装配+region） | 读侧增量 ~2-3%（A3b 已吃掉读大头）；write 侧装配 ~0.5% |
| 内存收益 | 0（memo 峰值有界：键多样性=访问局部性，估 <1MB） | ram dict 3.9MB/200k 步世界（54,899 项×~71B，PERF §3.3）→段+位图 ~几十 KB；**但这是 40MB 世界里的小头**，50M 外推影响 <0.1GB |
| 快照 M2 | **零改动**（memo 在闭包，三 dict 契约原样） | adapter 必改（encode/apply_ram_section+M2 字段表 world_snapshot.py:1269-1272）；**wire format 可与内部存储分离**（bytearray+位图仍可导出原「已知段 runs+洞=未知」编码，codex 修正采纳——格式失效不是必然，可保 byte 兼容）；code_fingerprint 变化使旧快照拒载则不可避免（probe_boot.py 在 FINGERPRINT_FILES 清单内，:58-62） |
| 语义风险 | 规范式失效契约（§4.1 A3b 约束 1-6：判定链照走/bytes 类型/失效不变量/核查记录/容量上限） | 形态等价证明面大：逐字节已知性↔位图一一对应、洞语义、部分字写、icfg_spills 与位图交互、别名（VA/PA 同存储）、自修改（mutable_decode_bytes 比较 read 结果——形态透明） |
| 工作量 | 小-中（~80 行+对照） | 中-大（Memory 读写+world_snapshot 两段+全部直读方 memory_snapshots/watch_ram/encode_ram_runs+测试重钉） |

**推荐：A 先行（阶段一），D 延后至 py-spy 证明 A 后访存仍是最大项且有内存
压力时再立项**（codex PERF 定调原文即此顺序；D 的 CPU 增量在 A 落地后不足以
独立成案，其真实价值在内存形态与快照体积——那是 O4/O5 的邻域）。

### 4.3 明确不做（本轮排除）

- **按字粒度重排 ram dict 键**（address→word）：与 D 同类证明面，收益被 A3b
  覆盖，排除。
- **跨 MMIO/fixture 的读缓存**：MMIO 读有副作用（计数器/状态推进），缓存=改
  可观察行为，红线排除。
- **fetch 专用 read_fetch 旁路**：省 operation 三次判断+try/finally，但复制
  判定序引入漂移面；decode 的 operation 机制是行为契约（fetch 不走 MMIO），
  旁路收益 ~0.5% 不值得证明责任。
- **handles or 链预计算成集合**：handles 依赖 40+ 投影开关态（br28_model.py:5194），
  开关在构造时固定但 address 判定含范围/对齐逻辑，预计算=复制全部判定逻辑；
  实测 0.8% 收益不抵漂移面，排除（若后续 py-spy 证明显著再议）。

## 5. Insn.mask/value 属性重建残余（同波第二案）

### 5.1 成因与实测

上游 `Insn.mask`/`value` 是 property（table.py:104-111）：每次访问重做
`"".join(self.words)`+逐字符扫描 genexpr+`int(,2)`。运行时唯一消费者是
`codec.decode` 的扫表循环（codec.py:40-47：`for insn in table.insns: value &
insn.mask == insn.value`——每候选 2 次重建；encode 不在运行路径）。field_bits
（operands/format 内）已被 OPT1 memo 化，残余 miss 0.020/0.001/步可忽略。

实测（200k 步计数 wrapper + 微基准，本路）：
- codec.decode 真实 miss **0.064/步**（12,840 次；CPU.cache 命中 ~94-97%）
- 表大小 1165 条；每次 miss 平均扫 ~89 候选 → mask/value property 访问
  **各 5.78 次/步**（1,144,359 对）
- property 重建微基准 ~1.0-1.6µs/对（2-word 指令为主）→ **~5-6% 墙钟**
  （20M 长程口径 decode 0.032/步则 ~3-4%；早期窗口/新代码段更重 ~10%）
- py-spy 30s 窗口未捕到 table.py 帧=decode miss 集中在特定段（RAM code/
  自修改/首访），长程摊薄。

「属性无 setter 无法实例影子缓存」的准确机理：property 是 data descriptor，
实例 `__dict__` 注入无法遮蔽（类属性优先）——OPT1 的 operands memo 能挂
`insn.__dict__[_OPERANDS_MEMO]` 是因为那是**新键**而非遮蔽既有 property。

### 5.2 方案：Insn mask/value getter 首调缓存（装载层，不动 submodule；codex 修正后首选）

照 br28_exec_memo.install 先例（模块/属性替换+首调缓存），**首选 getter 缓存
而非复制 decode 扫表循环**（codex 论证采纳：平行数组复制了上游循环的语义面
——`_words` 的 None 处理、短路顺序、多字指令匹配宽度、zip 截断——证明责任
大于 getter 缓存；getter 版只需证明「property 返回值恒定」）：
- install() 增加第四项：装载后把 `Insn.mask`/`Insn.value` 两个类属性 property
  替换为首调缓存版——首次访问调原 property 计算并 memo 到实例 `__dict__`
  **私有新键**（`_h3s_mask_cached`/`_h3s_value_cached`，非遮蔽——property 是
  data descriptor、实例注入本就遮不住原 property，我们替换的是类属性本身），
  后续访问直接返回缓存整数；
- 等价性论证：mask/value 是 `self.words` 的纯函数（table.py:104-111）；
  `words` 装载后不被运行时突变（本路 grep 核实：pi32v2isa 包内对 Insn.words
  仅读——table.py:240/:328 校验、asm.py:1276 读；hwtest/check.py:93 与
  semantics/m2.py:31 的 `self.words` 是 hwtest 框架自有对象非 Insn）；表由
  PIN 钉死（probe_pi32v2.upstream 校验 git 状态 :51-53），数据固定→纯函数
  值恒定→缓存恒等；
- **保留上游 decode 循环原样**（codec.py:40-47 不动）：每次 miss 扫表仍调
  getter，但 getter 变为 ~百 ns 的缓存读（vs ~1-1.6µs 重算）——扫表语义面
  零复制；首调语义与 OPT1 契约一致（首次访问在原执行位点、错误不缓存）；
- test_exec_memo 增加：getter 缓存值与原 property 全表逐指令等价向量
  （1165 条 × mask/value）+「words 装载后不变」断言（防上游 PIN 更新时静默
  漂移——PIN 变化时 upstream() 的 revision 校验先拒）。

备选（getter 版实测不足时）：平行数组扫表 memo——须附 codex 不变量表
（表序/重复项/首中规则、_words None 与 0 分离、多字匹配宽度、数组与表严格
等长防 zip 截断、不提前求值原循环不访问的候选）+ 原/新 decoder 差分向量
（全字长截断输入、无匹配、重叠候选）。**默认不走此路**。

收益 ~4-5% 墙钟（miss 0.064/步×~89 候选对×~1.2µs 重算→~0.1µs 缓存读）；
工作量 ~40 行+测试；风险低（纯函数+输入不变+PIN 三重护栏）。

## 6. 风险清单与对策

| 风险 | 归属 | 对策 |
|---|---|---|
| memo 失效漏点（绕过 write 的直改） | A3b | 规范式失效契约（§4.1 A3b 约束 3）+三入口全清+restore 显式清空；核查记录（约束 4）实施 PR 复核 |
| region 重叠场景语义改变 | A2 | 装载期检测→回退线性扫（先声明先赢原样；断言不拒绝任何配置） |
| post_stop/键链直调段写内存 | A3b | 已核实现有场景（key_chain S/AJ）写全走 cpu.mem.write→全清覆盖；新增 post_stop 场景的检查项：ram 变更必须经 Memory.write |
| 双镜像/多 world 交叉污染 | A3b/Insn | memo 生命周期=单次 explore 闭包；Insn getter 缓存=纯函数值恒定（与实例生命周期无关）；pair 顺序跑各自新建（验收 7.2 正反序） |
| 快照格式意外失效 | A/D | A 全程不碰 world_snapshot；D 的 adapter 必改但 wire format 可保兼容（§4.2），SNAP 全回归重钉（阶段二单独 PR） |
| 上游 decode 语义漂移 | §5 | PIN 校验+getter 缓存纯函数论证+全表等价测试向量（words 不变断言） |
| 自修改代码 | A3b | fetch 字节校验（:440-445）照走；写路径全清 memo |
| MMU 递归走查 | A3b | 页表读同走 region 分支+memo；页表写走 write 全清；walks 计数验收对照（§7.3） |
| memo 无界增长 | A3b | 条目上限 1<<16 超限全清+无界扫描负例（§7.5） |

## 7. 验收规格（照 OPT1 先例+codex 补强，实施路一案一 PR 逐条过）

1. **报告逐字节相等（全量双侧）**：全部 check_local 单测（含 test_world_snapshot
   全套 SNAP capture/restore 回归）+ `check_local.py --rom "$PRIVATE_ROM"` 的
   31 份报告（legacy 场景+硬件/MMU/双核/平台族）与改动前 stash 对照逐字节
   相等；`check_local.py --units` exit 0（含 git diff --check）。
   **预期差异字段预登记**（codex 补强）：O3 改 probe_boot.py →
   code_fingerprint 必变（FINGERPRINT_FILES :58-62）——快照场景报告的
   `snapshot_capture.code_fingerprint` 与快照 header fingerprint 字段为
   **唯一**预期差异，其余字段（含 RAM 段正文与 digest）必须逐字节相同；
   对照时逐字段白名单化处理，不做整文件 diff 掩盖。优先做法：同版本内
   memo off/on 开关对照（若实现为可开关），把 fingerprint 隔离在对照之外。
2. **条件前沿逐位一致**：`analyze_image_trace` 主/条件两场景 completed_steps/
   stop_pc/stop kind/per-core/adapter 计数与基线逐位相等（OPT1 先例的
   197,845@200k 同款对照）；**双镜像隔离**：同进程顺序跑 official/b79 与
   反序 b79/official 各一次，报告互不影响（memo 生命周期=单次 explore）。
3. **专门负例与失效边界矩阵**（单测，test_exec_memo 模式改前改后恒等）：
   - 未初始化读（洞≠0，含首缺失字节定位 first_missing 语义——A3a 合并
     遍历后必须同值）、跨 region 边界读、零长度/越界（require 路径）；
   - 失效边界：known→known 覆盖写、known→unknown（write_abstract_byte 删
     字节置来源）→再 known、部分字写邻字节保持、同一 (addr,size) 跨长度
     重叠读、批量装载（seeds/apply_ram_section）、写后立即读；
   - MMU：两 VA 映射同一 PA 经一侧写另一侧读（别名经同一 ram 存储）、
     改 PTE 后读（PTE 写全清→重新走查）、页表递归读命中 memo+
     `mmu.walks` 计数与基线一致；
   - 自修改代码：RAM fetch memo 命中后改指令字节→校验读重建→重新 decode
     或原严格停；unchanged-RMW 仍发 bus write（AGENTS 不变量）；
   - region 组合：各 flag 组合下（含 erased_bankb/p11 窗）region() 结果与
     线性扫基线逐一相等（重叠回退路径用合成重叠 fixture 单测）；
   - post_stop：key_chain S/AJ 场景 memo 开启前后报告逐字节相等。
4. **py-spy 复测**：200Hz×30s 采样，A 类占比与 read 族行级自耗时对照 §1 表
   下降符合预期；**分项计时**（无 profiler 重复墙钟为主，py-spy 定位为辅）：
   A1/A2/A3a/A3b/Insn 各子项单独启用测量——收益记账到项；干净速率合计
   目标 ≥1.10x（方案 A+Insn memo 合计 ~1.13x 为上界估计，实测为准）。
5. **RSS 有界+容量策略**：memo 条目上限 1<<16 超限全清（A3b 设计内）；
   200k/2M 步 run 的 VmRSS 曲线与基线同形（memo 增量 <1MB）；**无界扫描
   负例**：构造持续读新地址的诊断场景验证上限触发全清后 RSS 回落。
6. **SNAP 快照回归**：capture→restore→续跑与完整重放报告逐字节相等
   （world_snapshot 既有自动化对照，O8 的同款标准）；**热缓存恢复负例**
   （codex 补强）：填热 memo→capture→改 RAM→restore→读——restore 分支
   显式清空后结果与冷缓存一致；恢复含洞 RAM、重复恢复两次各一例；
   RAM 段正文编码逐字节不变（§7.1 的 fingerprint 白名单外零差异）。

## 8. codex 参谋记录（gpt-6-astra，2026-11-15，~/tmp/codex-o3.log 全文照录归档）

本轮 codex 沙箱读文件被环境挡住（bwrap loopback 权限，未能读设计稿与源码，
其输出自声明「预审、未核实代码」），但基于 prompt 事实的意见多数有效，逐条
裁决如下：

**采纳**（已并入正文）：
1. 收益估算改「待测假设」口径：请求命中率≠时间加权命中率；写全清损失不能
   由 2.2/0.13≈17 推出；实施前补三项诊断计数（失效间隔请求数/唯一键数/分阶
   段命中率）（§4.1 A3b、§7.4）。
2. 失效义务改规范式契约（「内容或存在性的任何变更必须在下次命中前失效」，
   覆盖赋值/删除/批量/容器替换/known→unknown/异常中断），点位枚举降级为
   核查记录（§4.1 A3b 约束 3/4）。
3. restore 后**显式清空**替代时序推断（虽然「restore 前 memo 必空」本路已
   核成立，显式清空消除对构造链演化的隐式依赖）（§4.1 A3b 约束 3）。
4. Insn 方案首选 **getter 首调缓存**（保留上游 decode 循环，证明面=纯函数
   +words 不变+PIN 三重护栏），平行数组降为备选并附其不变量表（§5.2）。
5. 方案 D 表述修正：内部存储与 wire format 可分离，bytearray+位图可继续导出
   原 RAM 段编码——「快照格式必然失效」改为「fingerprint 拒载不可避免、
   格式失效非必然」（§4.2 表）。
6. 验收补强：预期差异字段白名单（fingerprint）、同版本 off/on 开关对照、
   失效边界矩阵、MMU 别名/改 PTE/页表命中+walks 计数、双镜像正反序隔离、
   post_stop 开关对照、分项计时记账、RSS 容量上限+无界扫描负例、热缓存恢复
   负例（§7）。
7. A2 补充：~15 项表上二分收益有限，last-hit 指针可单独先上、bisect 以分项
   计时决定去留；hint 无需无重叠前提（§4.1 A2）。
8. 新增更简子项 **A3a 合并双重遍历**（无缓存、无失效义务，codex「合并两次
   RAM 遍历」建议的落地——保持 first_missing 语义恒等）（§4.1 A3a）。

**不采纳/降级**：
- 「region 断言拒绝原配置=行为变化」——本设计断言行为是**回退线性扫**而非
  拒绝（§4.1 A2 已明确），该风险不存在；但保留其「先穷尽支持配置的区间
  关系」要求为实施 PR 的核查项。

**codex 未核实、本路代核**（其声明读码失败的部分）：
- post_stop（key_chain S/AJ）fixture 安装与写**全部经 cpu.mem.write**
  （probe_irq_entry.py:931-932/:946 及 S 路同构 :694+）；
- br28_machine.py / br28_model.py 零 ram 直写（grep 全文）；
- 构造链（CPU/BR28Model 构造、seeds、machine.attach/launch）在主循环前
  不调 mem.read；capture_world 不调 mem.read（encode 直读三 dict）；
- Insn.words 运行时零突变（pi32v2isa 包内仅读；hwtest 的 self.words 是框架
  自有对象）。

## 9. 实施顺序建议

1. PR-1：A1+A2（判定序微重排+region last-hit/索引，~30 行）+§7.1-7.3；
2. PR-2：A3a（合并遍历，~10 行）+A3b（RAM 读 memo+诊断计数+容量上限，
   ~40 行）+§7 全项（含 7.6 SNAP+热缓存恢复负例）；
3. PR-3：Insn getter 缓存（~40 行）+test_exec_memo 全表等价向量；
4. A4 视 O2 落地后的 py-spy 证据决定并入或放弃；
5. 方案 D（存储形态）不排期——留待 A 波后复测仍显示访存为最大项、且出现
   内存压力（O6/O4 未覆盖的残余）时另立设计。

本路（O3D）到此为止：测量与设计，零 tools/ 改动；实施归后续实施路。
