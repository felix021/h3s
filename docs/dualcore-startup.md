# 双核条件启动：保留未知GPR，严格停在首任务RTI

2026-11-11，连续离线执行已越过[CPU1释放前边界](platform-startup.md)。新增的显式UnknownWord profile保留首任务未初始化GPR，执行真实寄存器恢复、`lockclr`和`sti`后，两核累计完成 **147,268 条模型指令**，严格停在CPU1 `0x06066130` 的任务RTI前。CPU0完成136,405步，CPU1完成10,863步，另有2,130次共享锁等待尝试；这些数值不是CPU周期或物理时间。

原有strict profile完整保留：147,263步时，CPU1在 `0x06066128` 的 `pop.rmask` 读取backing `0x19f640`而停止；其CPU0／CPU1步数为136,403／10,860，锁等待2,129次。两个profile回答不同问题，新增抽象值并没有把原有未初始化读取变成普通已知数据。

CPU1已经执行真实ROM、QCY trampoline、缓存初始化等待和两个ready通知写入；调度代码实际选中了任务TCB并恢复12B特殊寄存器帧。**首任务尚未进入，完整耳机启动和EQ修复仍未验证。** 未连接设备、未刷机、未运行GitHub CI。新增profile只允许受限的未知值搬运；任务RTI仍是独立的严格边界，不借用bootstrap RTI投影。

工具是 [probe_dualcore.py](../tools/probe_dualcore.py)，共享执行器是 [br28_machine.py](../tools/br28_machine.py)，精简结果见 [dualcore-startup.json](../evidence/dualcore-startup.json)。工具现包含10组严格／条件场景，可选两个精确候选各运行strict与abstract两条前缀，共4条候选结果。本轮10组场景与4条候选前缀的汇总检查，以及包含私有ROM的完整本机回归均通过。退出成功表示回归条件匹配，不表示启动完成。

## CPU1实际经过的路径

CPU0先写向量 `0x19fef8=0x12caf4`、清ready，再执行释放寄存器操作。共享机器模型只在显式选择reset PC=0后创建CPU1，分配独立寄存器、ICFG、IRQ bank和解码缓存，共用RAM／MMIO；它不写ready，也不直接跳进trampoline或任务入口。

CPU1实际执行25条ROM指令：从PC0进入 `0xa120`，在 `0xa144` 读到核号1，`0xc5fe` 读取CPU0写入的向量，`0xc602` 执行CALL到 `0x12caf4`，RETS=`0xc604`。随后三条真实trampoline指令设置USP、SP=`0x124840`及RETI=`0x12cad4`。strict场景在第28条CPU1指令之后、`0x12cb06` 的RTI前停止。

只有显式开启conditional bootstrap RTI时才继续。这个一次性投影要求真实路径锚点、核号、向量、CALL返回地址及SP／USP／RETI都匹配，条件失败不能修改启动状态。它假设该次启动异常返回进入thread上下文；不提供通用RTI，也不能复用于任务或中断返回。

新增 [analyze_cpu1_rom.py](../tools/analyze_cpu1_rom.py) 对固定128KiB ROM中的8个窗口校验摘要，并核对46个bootstrap／cache指令锚点及相关控制地址，[精简静态结果](../evidence/cpu1-rom-static.json)保留这些锚点。该检查已接入本机回归，只输出有限元数据；它确认所执行字节与控制流来源，不认证reset、缓存时序或RTI模式。

## 必须保留的未验证条件

| 条件 | 当前显式策略 | 尚未证明的部分 |
| --- | --- | --- |
| release与reset | CPU1从ROM PC0开始；CPU1初始ICFG完整字选择0，两核初始EMU guard均显式关闭 | 实际release到reset向量的因果、耳机真实初始模式与寄存器值 |
| bootstrap RTI | 一次性进入RETI；SP保留已写出的USP值，返回后SSP与ICFG gates8/9未知 | 通用RTI、异常入口、栈bank选择和SSP回写 |
| ICFG后续 | 其他位沿用显式初始fixture；实际read／AND／write三条指令清除gates8/9，才恢复已知ICFG | 其他位跨RTI保持的硅片行为 |
| SP／USP／SSP | 显式写入可观察；可能存在别名或回写且数值分歧时保留unknown，last-explicit值仅供追溯 | 不能用旧bank值冒充当前值，不能据固定AC791N端点测量推导BR28通用模式 |
| ICU command4 | `0xffec10=0xca000010` 后busy随64次全局模型advance完成；bit31保持独立外部状态fixture | 64不是周期或测量延迟；缓存内容、一致性及command4完整硬件语义未实现 |
| 软件IRQ WO命令 | 对已限定RMW形式仅解释被选中的SET／CLR位，不提供伪读回字；其他pending位保持未知 | WO总线读回、异常入口及IRQ投递未认证 |

前阶段的PMU／OTP／时钟／端口／复位历史／MPU写门假设仍然适用。PC guard跟随两核各自的实际范围寄存器，检查实际执行及完整parallel长度；半开区间、整条指令覆盖仍是软件策略。MPU仅模拟配置，不执行CPU／DMA权限检查。

ICU正例实际经历14次busy读取，命令完成后才写enable，CPU1 `CON=0x80000001`。未知进度在命令写入处停止；显式进度0保持busy，在150,000条已完成指令的预算结束时ready仍为0。这个反例防止等待循环被当作无条件成功。

## ready由固件写出，pending没有自动清除

| 执行核与PC | 真实写入 |
| --- | --- |
| CPU1 `0x0606602a` | `0x1265f5 = 1` |
| CPU1 `0x06066034` | `0x19ff54 = 1` |

共享锁按核记录所有者，竞争会让当前核等待并让另一核继续；原strict边界末尾锁由CPU1持有，CPU0在 `0x12b5aa` 等待；新增profile执行CPU1的真实 `lockclr` 后，CPU0实际取得锁，末尾owner为0。该固定轮转顺序是确定性测试调度，不是时钟精确仿真，也不覆盖所有并发交错。

实际软件IRQ命令只建立CPU0 pending mask `0x8`、value `0x8`，对应已核对的IRQ `124+3`。其余位没有被初始化成0；模型不自动ACK，也未投递中断。关闭WO命令投影的strict场景仍停在 `0x060644a6` 对 `0xffe1a0` 的读取。

## 首任务恢复到哪里

实际调度数据中CPU0／CPU1当前TCB分别是 `0x127db8`／`0x127e10`。CPU1通过真实TCB读取选到栈VA `0x401bb0`，按实际页表对应backing `0x19f630`，并执行 `0x06066126` 的12B特殊帧恢复：

| 槽位 | 已知内容 |
| --- | --- |
| RETI / RETS / PSR | `0x12c57e / 0 / 0` |
| 随后的R0参数槽 | `0`，已由任务构造代码写入 |
| 后续R1–R15槽 | 共60B，从 `0x19f640` 开始，全部未初始化 |

特殊帧恢复后SP=`0x401bbc`。下一条 `pop.rmask {r15-r0}` 读取未知GPR槽而停止，不能把这些槽补零。此时USP和SSP仍报告unknown，`OSRunning`仍为0，缓存锁字节为0，任务RTI尚未执行。创建的app_core帧、选中的CPU1首任务帧和Timer ISR的28B保存结构是不同对象，不能互相替代。

## 显式搬运未知值，不猜任务初值

任务构造代码只写RETI、RETS、PSR和R0参数，没有初始化R1–R15。固定ISA的完整寄存器pop依据和QCY真实指令共同确定：`0x06066128` 依次恢复16个GPR，SP前进64B，flags不变。新增profile先验证实际TCB／特殊帧路径及整个源范围，再一次性提交寄存器和SP；这不是跳过pop，也不把整块任务栈填零。

[br28_unknown.py](../tools/br28_unknown.py) 的UnknownWord保存四个字节各自的已知状态和物理来源。此次R0为真实pdata=0，R1–R15继续未知，来源覆盖 `0x19f640..0x19f67b`。只有已映射的实际任务帧允许首次引入这类未知值；普通未写内存读取仍停止，后续抽象栈reload必须有明确保存来源。

显式MOV与寄存器列表push/pop可以搬运未知字，不计算其数值。保存未知字会撤销目的地址旧的已知字节，已知覆盖会更新或清除相应来源；ROM／Flash／只读映射／MMIO继续遵守访问限制，已解码指令和取指保护不能绕过这些限制。算术、比较、条件、地址、间接跳转、返回目标或特殊寄存器写入若消费未知值，必须在相关状态提交前停止；未知GPR仅仅存在不应阻止无关指令。

真实后续两条指令不读取这些GPR：`0x0606612c lockclr`释放共享锁，`0x0606612e sti`将ICFG bit9置位，在该初始fixture下模型可见值为 `0x200`。这不是耳机真实ICFG完整状态的测量。新增边界SP=`0x401bfc`、RETI=`0x12c57e`，USP／SSP仍未知，CPU0 pending mask/value仍为 `0x8`。`0x06066130 rti`继续报 `unsupported_runtime_instruction`；没有跳入idle任务，没有清pending或伪造任务运行。

## 严格反例与候选边界

| 场景 | 停止条件 |
| --- | --- |
| reset向量未知 | 127,239步，CPU1尚未创建，release控制仍为0 |
| CPU1初始guard未知 | CPU1第0步、PC0停止，不继承CPU0控制状态 |
| bootstrap RTI关闭／ICFG未知 | CPU1完成28步后停在 `0x12cb06` |
| CPU1栈未映射 | 首次写 `0x124838` 停止，不隐式映射或清零 |
| ICU进度未知／停止 | 分别在命令写入处停止／保持busy至预算结束 |
| WO命令严格模式 | 读取 `0xffe1a0` 停止 |
| 原strict条件profile | 147,263步，CPU1读 `0x19f640` 停止 |
| 显式UnknownWord profile | 147,268步，CPU1在 `0x06066130` 的任务RTI前停止 |

官方镜像、历史失败r2和离线repair候选均已到达各自相同的147,263步strict边界及147,268步任务RTI前边界，两条路径分别检查。候选按完整UFW摘要和载荷摘要绑定；两核实际Flash上限分别核对为原版／r2的 `0x060c3338` 或repair的 `0x060e31c5`。**共同到达此前缀不区分整机启动健康，也不证明repair解决了历史故障。** 旧r2继续禁止重刷；repair仍未刷入，不能仅据本页批准刷机。

## 本机复现

```bash
python3 tools/analyze_cpu1_rom.py --rom /private/path/rom.bin \
  --report analysis/cpu1-rom.json
python3 tools/probe_dualcore.py --rom /private/path/rom.bin \
  --report analysis/dualcore/results.json

# 可选精确候选，仅做离线回放
python3 tools/probe_dualcore.py --rom /private/path/rom.bin \
  --repaired-candidate /private/path/repaired.ufw --failed-r2 /private/path/r2.ufw \
  --report analysis/dualcore/candidates.json
```

本机测试包含10项未知值／任务栈、11项共享机器、18项总线／指令和41项外设测试；`check_local.py`已接入这些测试及私有ROM下的静态锚点、10组双核场景。本轮 `python3 tools/check_local.py --rom /private/path/rom.bin` 完成并退出0；完整回归中的10组双核结果与候选回放对应场景完全一致，旧16组平台场景与前一检查点完全一致。随后增加的只读区边界补强再次通过10项定向测试。

ROM必须匹配固定128KiB摘要。公共证据仅保留输入摘要、停止点、少量路径锚点、通知事件及帧字段，不发布原始ROM、完整指令轨迹、整块RAM或私有输入路径。P11执行、架构级IRQ／任务RTI、完整任务运行、蓝牙／音频／EQ行为仍是未完成部分。
