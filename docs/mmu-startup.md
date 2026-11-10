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
| 物理堆描述符 | 起点`0x134500`、终点`0x19fb80`、大小`0x6b680`；首／末tag `0x68a / 0x13f7` |
| 页表清零摘要 | SHA256 `84ff92691f909a05b224e1c56abb4864f01b4f8e3c854e4bb4c7baf1d3f6d652` |

物理tag来源由实际QCY算式和参考`pmalloc_init`交叉核对：`((physical_start + 0x700000) >> 7) & 0xffff`，这里等价于`(physical_start-0x100000)>>7`。参考`get_physic_address`对tag减首tag、乘128、加堆起址并加4；最后4B是分配器页内元数据偏移，不应错误地加到每次硬件地址翻译上。

功能投影在虚拟地址`0x400000..0x800000`内按当前表界限检查页号，读2B tag，要求有效位，再以`0x100000 + ((tag&0x7fff)<<7) + 页内偏移`翻译。超表、无效页、未知寄存器和暂未拆分的跨页访问都停止，物理目标仍经过原有RAM映射／初始化检查。没有缓存、TLB失效时序或周期精度；CON初值0是显式fixture，非测得初态。

**连续启动本轮没有发生虚拟页访问**：页表仍全无效。有效页和别名翻译由独立构造页表的单位测试覆盖，不冒充真实分配器已经运行。后续还需运行实际malloc／页表更新和使用这些地址的代码。

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

LLVM16直接反编译部分归档会碰到其他函数的无效调试元数据。仓库的[单函数读取器](../tools/llvm_ir_function.cpp)按需载入选定函数并去掉调试信息，输出诊断IR片段；不补写指令、不执行厂商代码。六个目标函数的对象和片段摘要在结果中，完整SDK／对象／IR留在忽略目录。仍无法读取的`memory_init`参考函数不作为行为模型依据；其真实QCY指令继续由探针执行。
