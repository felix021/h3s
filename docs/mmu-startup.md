# MMU 初始化及 P11 域访问握手边界

2026-11-10，在[GPIO／真实ISR探索](gpio-irq.md)之后，连续暖路径推进到83,050个模型step，冷路径84,088个；都停在`PC0x060688f8`向`0xf2af10`写`0xff`。真实代码已初始化物理堆描述符、分配位图、10 KiB MMU页表并开启MMU。尚未进入RTOS，没有验证真实虚拟分配或整机启动。全程本机离线，没有操作耳机。

工具：[probe_mmu.py](../tools/probe_mmu.py)、[br28_model.py](../tools/br28_model.py)、[analyze_memory_reference.py](../tools/analyze_memory_reference.py)。结果：[mmu-execution.json](../evidence/mmu-execution.json)、[memory-reference.json](../evidence/memory-reference.json)。

## 从实际代码和固定 SDK 得到的契约

MMU寄存器位于`0xff0400`：CON、TLB1_BEG、TLB1_END。固定`asm/csfr.h`定义页表项为15位page和1位vld，共2B；参考`vmalloc.c.o/vmm_init_`写入首地址和**末字节的包含式地址**，置`BIT(cnum+4)`、memset清表、清该位，再置CON bit0。

QCY实际执行得到：

| 项目 | 结果 |
| --- | --- |
| 页表 | `0x120000..0x122800`，末地址`0x1227ff`，共10,240B |
| 有效位 | tag bit15；清表后所有页均无效 |
| 页大小 | 128B；5,120个页表项覆盖640 KiB |
| 虚拟堆描述符 | 页表`0x120000`、起点`0x400000`、终点`0x4a0000`、大小`0xa0000`、页数5,120 |
| 物理堆描述符 | 起点`0x134500`、终点`0x19fb80`、大小`0x6b680`；位图首／末索引 `0x68a / 0x13f7` |
| 页表清零摘要 | SHA256 `84ff92691f909a05b224e1c56abb4864f01b4f8e3c854e4bb4c7baf1d3f6d652` |

**后续真实分配执行纠正了早期解释。** `pmalloc_init` 的算式 `((physical_start + 0x700000) >> 7) & 0xffff` 得到的是位图索引，这里等价于`(physical_start-0x100000)>>7`，并非直接写入页表的硬件页号。实际QCY在RAM `0x12af74`将选中的索引加`0x2000`；参考`pmalloc`也做同样转换，`pfree`反向减去`0x2000`。

当前模型的CPU RAM backing地址为`((tag&0x7fff)<<7) + 页内偏移`。此前实现额外加`0x100000`有误：实际页表`0x1227fe`中的`0xb3f5`对应虚拟`0x49fff4`，正确目标是`0x19faf4`，旧公式会错误访问`0x29faf4`。另一实际页表项`0x120600=0xb3f6`对应`0x19fb00`页。`__pmalloc_continue`直接返回编码页号左移7位后的指针，释放时检查该指针位于物理堆，提供了独立交叉核对。

`get_physic_address`／`vmem_get_phy_adr`另有转换，其具体调用语境尚未确认；不能据此推断另一总线别名或宣称SDK有缺陷。本模型只确定当前执行路径的CPU RAM backing，不宣称已验证硅片总线地址语义。

功能投影在虚拟地址`0x400000..0x800000`内按当前表界限检查页号，读取2B tag并要求有效位。超表、无效页、未知寄存器和暂未拆分的跨页访问都停止，目标仍经过RAM映射／初始化检查。没有缓存、TLB失效时序或周期精度；CON初值0是显式fixture，非测得初态。

本页最初的83,050步报告只覆盖清表，未触发翻译，因此旧公式错误没有在该报告中暴露。[后续启动回放](clock-rtos-startup.md)已运行实际分配、写表及虚拟内存访问，并保留不映射物理堆时停在`0x19faf4`的严格反例。物理堆初始内容仍未知，没有整片补零。

## 并行完整 word 存储

上游scratch RAM模型可能先读旧word合并存储，并省略未变化的写。新选项`exact_parallel_stores`只适配明确的纯ALU＋完整word store配对及完整word store；ALU由原指令模型求值，存储地址和值均从**配对前**寄存器计算，始终发出写入，不伪造旧MMIO值。未适配形式继续严格停止。

独立执行真实ROM `0xa30c`配对验证：旧R0=37时，实际PRD得到37，而R0更新为`0x1011`。不得把新R0错误地写入PRD。旧报告没有开启该选项，保持逐字节复现。

## 新停止点不是普通可读写寄存器

参考`cpu.a/power_driver.c.o/p11_init`与QCY `0x060688ec`函数吻合：

```text
向0xf2af10依次写 ff ee ab 0e 55
读 JL_PMU.PMU_STA = 0xfd3e04
若bit0没有置位，则重复上述序列；否则返回
```

固定SDK的`p11_sfr.h`确认`0xf2af10=P11_UART0.CON2`，不是普通邮箱。`low_power_schedule`还会向`P11_WDT.DUMMY=0xf2a808`发送同样序列并轮询同一bit，提示这可能是跨域访问握手，尚非已证实的UART软件协议。完整事实、条件探索与P11固件解析见[P11记录](p11-domain.md)。默认模型仍在首次写入前停止；不假定写完五次就自动ready。

## 本机复现

```bash
# 先取得已固定的参考归档；不需要再次操作设备
python3 tools/fetch_boot_reference.py --hardware
# 需要本机g++和llvm-config-16开发文件
python3 tools/analyze_memory_reference.py
python3 tools/test_br28_model.py
python3 tools/probe_mmu.py --rom /private/path/rom.bin \
  --report analysis/mmu-execution.json
python3 tools/check_local.py --rom /private/path/rom.bin
```

LLVM16直接反编译部分归档会碰到其他函数的无效调试元数据。仓库的[单函数读取器](../tools/llvm_ir_function.cpp)按需载入选定函数并去掉调试信息，输出诊断IR片段；不补写指令、不执行厂商代码。目标函数的对象和片段摘要在结果中，完整SDK／对象／IR留在忽略目录。仍无法读取的`memory_init`参考函数不作为行为模型依据；其真实QCY指令继续由探针执行。
