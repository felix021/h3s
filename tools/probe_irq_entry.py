#!/usr/bin/env python3
"""Minimal IRQ127 software-interrupt entry on the real startup prefix.

The scheduler IPI (pending 0x8 set by the actual QCY code at 0x60644a6)
reaches an opt-in CPU entry: RETI loads the interrupted PC and execution
continues at the vector slot written by the firmware. Entry banks, RETI
load and pending acknowledgement are explicit assumptions; generic IRQ
entry, IDLE wake and hardware frame writes stay unmodeled. The trailing
irq125_repeat_inert scenario pins that the opt-in repeat_irq_delivery
projection (handler return re-enables delivery) stays inert on this prefix
until the firmware actually pends a second time. No flash approval.

The trailing crc_bankb_erased_fixture scenario layers an explicit unverified
erased-bank fixture (four evidence-bounded windows of 0xFF: the 32-byte header
read of the probe-table entry 5, the 20-byte window-table structure the qcy
consumer enumerates at flash offset 0x1e0000, the full declared VM region
0x2000 bytes at flash offset 0x1da000, and the 8-byte first-record header at
window offset +0x200 the cfg-tool/btif storage backend init 0x6060d7c parses)
on top of crc_engine_roundtrip: the consumers really compute the header CRC /
parse the erased headers / blank-scan the VM halves / range-check the erased
record header and select their benign branches; crc_engine_roundtrip itself
keeps stopping honestly on the unmapped bank-B read. The VM window is declared
structurally (not incrementally) because its consumer is a data-dependent
byte-by-byte blank scan over each whole half — a partial window would just
move the unmapped stop one byte later. After the blank scan confirms an
all-erased VM, the chain meets the misaligned-source ROM memcpy variant
(insert.rrii.p + ld.bu.pin compose pairs), runs it through the opt-in
exact_parallel_insert_load projection, then the record-header init rejects
the erased 8-byte header (only its first 4 bytes are read) and the chain
stops honestly at the next independent boundary: an upstream pair-store
bookkeeping read of uninitialized stack RAM at 0x6011370 (mov.rr.p with a
halfword-store second half), not a bank-B data question.
"""
import argparse
import json
from pathlib import Path

from probe_dualcore import dual_case
from probe_task_returns import EMPTY_PRIORITIES
from probe_arch_startup import candidate_view, REPAIRED_SHA, FAILED_SHA
from probe_boot import application, flash_image, check_rom, ROM_SHA, OFFICIAL_SHA, APP_SHA
from probe_boot_handoff import package_header
from probe_pi32v2 import ROOT, PIN, upstream, require

HANDLER_PSR_FRAMES={0x12ad30:'psr_push',0x12ad60:'psr_pop',0x12adae:'psr_push',0x12adde:'psr_pop',
                    0x12ad6e:'frame12_push',0x12ada0:'frame12_pop',
                    0x60662c8:'frame12_push',0x606633e:'frame12_pop'}

# bank B 头 fixture 的显式范围:find_partition 探测表 0x060A0FA4 第 5 项
# (偏移 0x100000)的 32 字节头读取窗(CPU=0x06000000+偏移-0x2020 头)。
# 只覆盖消费者分析实测的读取范围;见 irq_case 的 erased_bankb 注释。
ERASED_BANKB_HEADER_RANGE=(0x60fdfe0,32)

# bank B 第二数据窗 fixture 的显式范围:qcy 消费函数 0x6060a4a 缓存进全局
# 0x126544 的 window(0x061ddfe0=0x06000000+0x1e0000-0x2020,VM 尾与 TON
# 0x1e1000 之间)上,u16 对表 walker(0x60609be,表 0x60a1e8a,app flash 公开
# 字节)枚举的全部条目结构范围:两项 {id=0x66,size=6}/{id=0x6e,size=6},
# 每项 on-flash 形状=4 字节头+size 字节数据,全长 (4+6)*2=20 字节——范围
# 由 app 自己的表声明,不是任意拍的;见 irq_case 的 erased_bankb 注释。
ERASED_BANKB_WINDOW_RANGE=(0x61ddfe0,20)

# bank B 第三数据窗 fixture 的显式范围:VM 区全长 0x2000 字节(CPU 可见
# 0x61d7fe0..0x61d9fe0=0x06000000+0x1da000-0x2020 起 0x2000)。范围是结构声明
# 的:qcy_boot_info 自己的 vm 字段(vm_saddr=0x1da000、vm_size=0x4000)、GET
# 消费者 0x6060f9a 自己的 clamp(>=8192→8192)与 4K 对齐切半算法([0x129070+0]=
# 0x1da000、[+0xC]=0x1db000、[+0x10]=0x1dc000,每半 0x1000)、空白检查自己传入
# 的 len(0x600a79e 每半 len=0x1000)——不是任意拍的。消费者分支图(反汇编+
# trace 钉死):0x6061094/0x606109e 先读两半 magic 各 4 字节,0xFFFFFFFF(擦除态)
# →0x6061106→word2≠0xFF5A5A5A→0x6061120 call 0x600a7b6:0x600a78a 逐字节扫
# 每半 len 字节检查全 0xFF(实测 0x600a78c 执行 8192 次、读 0x61d7fe0..0x61d9fdf,
# 遇非 0xFF 立即中止)——数据依赖的结构性全扫描,逐窗扩 fixture 只会把 unmapped
# 停点向后挪一个字节,故按结构声明范围一次性映射(同第二窗「表结构=擦除态」
# 语义)。全空白分支良性:两处格式化写 call(0x600a7da/0x600a7f6)只在半区部分
# 写入且头无效时执行、本前缀 trace 零执行;随后设置 VM 空状态、从 app flash 表
# 0x60a9344 重建位图、return 0。见 irq_case 的 erased_bankb 注释。
ERASED_BANKB_VM_RANGE=(0x61d7fe0,0x2000)

# bank B 第四数据窗 fixture 的显式范围:window(0x61ddfe0)+0x200 处的记录区
# 首条目头 8 字节(0x61de1e0..0x61de1e7=0x06000000+0x1e0200-0x2020 起 8 字节)。
# 消费者是 cfg_tool/btif 存储后端 ops 表(28 字节记录,0x60c32c8 起,成员含
# 0x6060a4a/0x6060c3c/0x6060f9a/0x6060d7c)的 init 成员 0x6060d7c——平台 init
# 尾部 0x601206c 表遍历的最后一项:thunk("flash/res/cfg_tool.bin") 与
# thunk("flash/app/btif") 各取描述符,把状态块写到全局 0x12655C(+0 有效=1、
# +4/+8 cfg_tool 内容界、+12 条目有效位清 0、+16 游标=window+0x200、+20 长度),
# 然后解析 window+0x200 的 8 字节首条目头 {u32 len;u32{b0 crc,bits20+ size}}:
# 0x6060e0e 对 len 做无符号范围检查((len-1)>2047 拒绝)、0x6060e2a 对
# size 做 >800 检查、都过了才 call 0x6000f0c 对数据区算 CRC 并与头 b0 比较,
# 匹配才置 +12 条目有效位=1 并推进游标。0xFF 擦除态下(len=0xffffffff,
# (len-1)=0xfffffffe 无符号 >2047)真实代码只读头 4 字节就在 0x6060e0e 拒绝、
# 返回 0(动态 trace 钉死:bytes 4-7 不读、CRC call 不执行、无 flash 写)——
# +12 有效位保持 0,把按 index 找条目的 walker 0x60112c8(入口判 b[0x126568]
# !=1 即跳过)挡在门外。fixture 覆盖消费者自己解析的完整 8 字节头结构
# (同第二窗「表结构=擦除态」语义:按结构整体声明,避免把一个头切成一半已知
# 一半未知),不扩 +0x208 之后的数据区/后续条目——擦除态分支到不了,无任何
# 全记录区扫描证据(记录区其余消费者:0x60112c8 被 +12=0 门控、0x6060c6a/
# 0x600a412 的 walk 基址=getter(103)+OTP 槽 827(本前缀 find_partition 失败
# 已清 0,0x600a412 入口判槽 828==0 直接返回 -250)、表区写路径 0x6060b80 与
# VM 窗口 0x6060f9a 各自独立)。见 irq_case 的 erased_bankb 注释。
ERASED_BANKB_RECORD_RANGE=(0x61de1e0,8)

# 0xfd1c00 在 SFC 挂起/恢复周期里按位的 RMW 记录；前三条来自挂起尾与恢复头，
# 中间的 0x4000 是 0x12ba38 轮询返回后的 PLL 状态位写入，末尾两条是恢复尾的清位。
SFC_RESUME_OPERATIONS=(
    {'kind':'or','operand':'0x1'},{'kind':'or','operand':'0x8'},
    {'kind':'and','operand':'0xffffefff'},{'kind':'or','operand':'0x4000'},
    {'kind':'and','operand':'0xfffffff7'},{'kind':'or','operand':'0x8'},
    {'kind':'and','operand':'0xffffefff'},{'kind':'or','operand':'0x4000'},
    {'kind':'and','operand':'0xfffffff7'},{'kind':'or','operand':'0x8'},
    {'kind':'and','operand':'0xffffefff'},{'kind':'or','operand':'0x4000'},
    {'kind':'and','operand':'0xfffffff7'},{'kind':'or','operand':'0x8'},
    {'kind':'and','operand':'0xffffefff'},{'kind':'or','operand':'0x4000'},
    {'kind':'and','operand':'0xfffffff7'},{'kind':'or','operand':'0x1000'},
    *([{'kind':'or','operand':'0x4000'}]*32),
    {'kind':'and','operand':'0xfffff3ff'},{'kind':'and','operand':'0xfffffffe'})


def irq_case(module,app,header,rom,name,*,entry=False,banks=False,idle_wait=False,rti=False,
             unmaskable=False,sfc_suspend=False,gpio_out_readback=False,kick_wake=False,
             repeat=False,hardware_crc=False,rep_block=False,rep_unknown_transport=False,
             icu_region=False,dcu_cmo=False,zero_rep_skip=False,erased_bankb=False,
             insert_load_pair=False,flash_image_bytes=None,readonly=()):
    machine_options={**EMPTY_PRIORITIES,'first_task_return_hypothesis':'open_gate8',
                     'defer_core1_first_idle':True}
    if entry:machine_options['software_irq_entry']=True
    if banks:machine_options['software_irq_entry_banks']=True
    if idle_wait:machine_options['idle_wait_projection']=True
    if rti:machine_options['software_irq_rti']=True
    if gpio_out_readback:machine_options['gpio_out_readback']=True
    if kick_wake:machine_options['cpu1_control_kick_wake']=True
    if repeat:machine_options['repeat_irq_delivery']=True
    options={'initial_software_irq_pending':(0,0)}
    if entry:options['software_irq_delivery_requests']=True
    if unmaskable:options['unmaskable_irq_projection']=True
    if hardware_crc:options['hardware_crc_projection']=True
    # icu_region:opt-in 的两核 ICU region 寄存器组(0xffe400/0xffec00 的
    # +0x14/+0x18/+0x10)。ROM 0xc726 把 VM flash 窗口配进两核 ICU:每核
    # [+0x14]=begin、[+0x18]=end-1(write-only 记录)、[+0x10]=命令字
    # 0xca000020/0x40(与 0xca000010 同族,复用 CON bit30 完成投影)。默认关。
    if icu_region:options['icu_region_projection']=True
    # dcu_cmo:opt-in 的 DCU CMO 逐地址 cache 维护口(0xff05c8/0xff05cc,
    # SDK csfr.h JL_L1P 的 CMO[18]/CMO[19],__WO 只写)。sfc_drop_cache 两核
    # region 命令完成后尾调 flushinv_dcache(0xc71a,r2=0xff05cc):0xc6f4 把
    # (beg,len) 折算成 32 字节行数,寄存器计数 `rep r1,4` 体=add.rrr.p(r0+=32)
    # +st.w [r2]=旧 r0 并行对,每行写一次口——write-only 记录,读回一律拒绝。
    # 默认关。
    if dcu_cmo:options['dcu_cmo_projection']=True
    # rep_block:opt-in 的多指令 rep.i 体执行(0x6010b14 rep 4 32 字节拷贝,
    # 及其 callee 0x6010a64 内的四个 rep 4 8)。默认关闭,legacy 报告不变。
    # rep_unknown_transport:opt-in 的 rep 拷贝体未知字节运输。字形式(0x600a352
    # rep 4 4 从 qcy_boot_info 0x122800 拷 16 字节,其中 bytes 1-3/8-11 是
    # boot.h 里 vm_info 的 C 对齐 padding 与 vm_res 保留字段,app/ROM 均无写
    # 入者,真机为残留 RAM 垃圾)与字节形式(0x600a302 rep 4 16 从设备结构
    # 0x1265C0+0x10E4 拷回同一 16 字节块——SET 路径 rep 4 4 拷入的缓存,同一
    # 批 padding/vm_res 未知字节;ld.bu.pin 零扩展构造、st.b.pin 只写低字节)
    # 都按字节来源运输未知,不臆造数值。默认关闭。
    extra={'software_irq_frames':dict(HANDLER_PSR_FRAMES)} if entry else {}
    if rep_block:extra['immediate_rep_multi_block']=True
    if rep_unknown_transport:extra['rep_copy_unknown_transport']=True
    # zero_rep_skip:opt-in 的零计数 rep 跳过假设(multi-block gate 的寄存器
    # 计数形式 count=0 → 体一次也不执行、PC 前进到体后、计数寄存器保持 0)。
    # memset 先例 rom_zero_rep_skip(0x4242/0x424c)的推广:FM-1 run3 只捕获非零
    # 计数「体执行次数=计数寄存器值,寄存器递减到 0」,count=0 本身 BR28 未测
    # 量(GAPS.md「Not tried: rep with a count of 0」),报告以
    # zero_count_skip_is_assumption 计数、不声称硅验证。默认关闭。
    if zero_rep_skip:extra['zero_count_rep_skip']=True
    # insert_load_pair:opt-in 的「装载+组装」并行对执行(ROM memcpy 0x417e
    # 不对齐源组装变体:rep r5,22 体=4 次 ld.bu.pin 逐字节取+insert.rrii.p
    # ∥ld.bu.pin 并行对组装字(bits 8/16/24)+st.w.pin 存整字)。寄存器交叉
    # 方向「A 半(insert)读 B 半(ld)写之前的旧寄存器」与 FM-1 run3 捕获的
    # exact_parallel_stores 方向(B 读 A 之前旧值)相反、无硬件捕获证据,按
    # 对称旧值假设执行,adapter 以 _is_assumption 后缀显式标注。默认关闭。
    if insert_load_pair:extra['exact_parallel_insert_load']=True
    # 资源表扫描从 flash 基址 0x06000000 读 app 映射(ENTRY 之下、app 结束
    # 之后)之外的已知真实字节:按需挂完整 sfc 明文只读区,同样不影响默认映射。
    if flash_image_bytes is not None:readonly=((0x06000000,flash_image_bytes),)+tuple(readonly)
    # erased_bankb:opt-in 的「未验证擦除态 bank B」显式 fixture,三个独立
    # 证据钉死的窗。窗 1(2026-11-12 反汇编+trace):app find_partition
    # (0x6010f32) 主循环从探测表 0x060A0FA4 第 5 项(flash 偏移 0x100000)
    # 读 32 字节头——CPU 可见 0x60fdfe0=0x06000000+0x100000-0x2020。
    # CRC16(头 bytes 2..31)与头前 2 字节期望值在 0x6010f80 比较,不匹配→
    # 0x6010f5a 良性跳到下一项;5 项全败→0x6010fe6 return 1;调用者只是
    # free 临时对象并清 OTP 全局槽 827/828,VM init 返回值被 0x601205a
    # 调用者忽略——失败分支良性。0xFF 擦除态下期望 CRC=0xffff、真实计算值
    # =0x75f8,必然不匹配,由真实代码自己选择分支。窗 2(本次反汇编+trace):
    # qcy 消费函数 0x6060a4a 把 window(flash 0x1e0000)缓存进全局 0x126544
    # 后,循环 0x6060ad0 从同一张 u16 对表 0x60a1e8a(id==0 终止)取 id、
    # call 0x60609ec:walker 0x60609be 命中项(offset=Σ前项(4+size))后读
    # window+offset 起 4 头字节,LE32==0xFFFFFFFF→返回 -248(擦除态),
    # 调用者 0x6060a9a 对 -248 直接跳下一项、零副作用(错误处理 call 只在
    # -245/-243/成功路径,trace 零执行)。本表两项 {0x66,6}/{0x6e,6},擦除
    # 态实际读取只有两段头各 4 字节(offset 0/0xa),fixture 覆盖表声明的
    # 完整结构范围 20 字节。窗 3(本次反汇编+trace):VM 配置 GET 消费者
    # 0x6060f9a 读两半 magic 各 4 字节后,0xFFFFFFFF→0x600a7b6 空白检查
    # 逐字节扫过声明 VM 区全长(两半各 0x1000,实测 0x600a78c 共 8192 次、
    # 范围 0x61d7fe0..0x61d9fdf,遇非 0xFF 即止)——数据依赖的结构性全扫描,
    # 逐窗扩 fixture 只会把 unmapped 停点后挪一字节,故按结构声明范围一次
    # 性映射 0x2000 字节(范围依据见 ERASED_BANKB_VM_RANGE 注释:boot_info
    # vm 字段+消费者切半算法+空白检查 len,均固件自己的声明);全空白分支
    # 良性(格式化写 call 零执行、空 VM 状态、位图重建自 app flash 表、
    # return 0),由真实代码自己扫描自己选分支。窗 4(本次反汇编+trace):
    # cfg_tool/btif 存储后端 init 0x6060d7c 解析 window+0x200 的 8 字节首
    # 条目头 {u32 len;u32{b0 crc,bits20+ size}},0xFF 擦除态下 (len-1)=
    # 0xfffffffe 无符号 >2047,真实代码只读头 4 字节就在 0x6060e0e 拒绝、
    # 返回 0(bytes 4-7 不读、CRC 不算、无 flash 写,trace 钉死),条目有效
    # 位 b[0x126568] 保持 0 把按 index 找条目的 walker 0x60112c8 挡在门外
    # ——fixture 覆盖消费者自己解析的完整 8 字节头结构(范围依据见
    # ERASED_BANKB_RECORD_RANGE 注释),不扩数据区/后续条目。四个窗都是
    # 显式环境假设(unverified erased-bank fixture),不是已知出厂状态;
    # 范围外 bank B(含 TON 0x1e1000)继续严格 unmapped。
    if erased_bankb:
        base,length=ERASED_BANKB_HEADER_RANGE
        wbase,wlength=ERASED_BANKB_WINDOW_RANGE
        vbase,vlength=ERASED_BANKB_VM_RANGE
        rbase,rlength=ERASED_BANKB_RECORD_RANGE
        readonly=tuple(readonly)+((base,b'\xff'*length),(wbase,b'\xff'*wlength),
                                  (vbase,b'\xff'*vlength),(rbase,b'\xff'*rlength))
    if sfc_suspend:
        # The SFC suspend tail sets 0xfd1c00|=1 (write-only RMW projection) and
        # then the wrapper reads the second SFC 0xfe0100 CON. The second SFC is
        # never referenced by the ROM (byte search) and the H3S boots from a
        # single 2MiB flash on 0xfe0200, so 0xfe0100 stays at its reset state;
        # CON=0 is an explicit, unmeasured reset assumption, not a default.
        options['sfc_clock_gate_projection']=True
        options['secondary_sfc_read_bus']=True
        options['initial_secondary_sfc_control']=0
    return dual_case(module,app,header,rom,name,abstract=True,readonly=readonly,
                     machine_options=machine_options,options=options,budget=250000,
                     extra_diagnostics=extra or None)


def check_delivery(r,*,before_rti=True):
    m=r['machine'];projection=m['software_irq_entry_projection']
    require(len(projection['deliveries'])==1,'scheduler IPI was not delivered exactly once')
    d=projection['deliveries'][0]
    require(d['core']==0 and d['irq']==127 and d['vector_address']=='0x19fffc' and
            d['handler']=='0x12ad2a' and d['reti_load_is_assumption'] and
            d['entry_sp_bank_or_icfg_effects_certified'] is False,'unexpected delivery record')
    if before_rti:
        require(m['cores']['0']['reti']==d['reti_from'],'RETI did not keep the interrupted PC')
    return d


def check(report):
    rows={r['name']:r for r in report['scenarios']}
    r=rows['irq127_request_strict']
    require(r['completed_steps']==149660 and r['stop_core']==0 and r['stop_pc']=='0x12b608' and
            r['stop']=={'kind':'software_irq_delivery_not_implemented','core':0,'irq':127},
            'strict path must still stop before the unimplemented entry')
    r=rows['irq127_entry_minimal']
    d=check_delivery(r)
    pending=r['peripherals']['software_irq_commands']['pending']['0']
    require(pending=={'known_mask':'0xf','known_value':'0x0'} and
            r['peripherals']['software_irq_commands']['acknowledgements']==1,
            'pending bit was not acknowledged exactly once by the delivery assumption')
    source=r['peripherals']['software_irq_commands']['source_enable']['0']
    require(source=={'known_mask':'0x8','known_value':'0x0'} and
            r['peripherals']['software_irq_commands']['source_commands']>0,
            'handler did not mask its own software-IRQ source at entry')
    require(r['peripherals']['ipmask']==7,'handler did not raise the real IPMASK to 7')
    require(r['stop_pc']=='0x12ad68' and
            r['stop']['kind']=='unknown_core_special_register' and r['stop']['register']=='usp',
            'handler without the bank assumption must stop at the unknown USP')
    adapters=r['instruction_adapters']
    require(adapters.get('software_irq_handler_psr_frame')==2 and
            adapters.get('software_irq_source_enable_rmw',0)>0,
            'entry small-frame push/pop or source masking was skipped')
    executed=[(row['pc'],row.get('instruction_id')) for row in r['last_instructions']]
    require(('0x12ad5c','st.w.modimm') in executed and ('0x12ad66','csync') in executed,
            'handler head instructions were not really executed')
    r=rows['irq127_scheduler_entry']
    d=check_delivery(r)
    require(r['machine']['software_irq_entry_projection']['entry_bank_assumption'] and
            d['entry_banks'] and not d['entry_banks']['silicon_bank_switch_certified'],
            'scheduler entry lacks the explicit bank assumption record')
    adapters=r['instruction_adapters']
    require(adapters.get('multicore_visible_sp_from_usp')==1 and
            adapters.get('software_irq_handler_context_frame')==1 and
            adapters.get('multicore_visible_sp_from_ssp',0)>=1,
            'handler bank switch or 76-byte context frame was skipped')
    require(r['completed_steps']>150000 and r['stop_core']==1 and r['stop_pc']=='0x12c5ca' and
            r['stop']['kind']=='unsupported_runtime_instruction' and
            r['stop']['instruction_id']=='idle',
            'the real scheduler must run and the stop must be the peer-core IDLE boundary')
    require(r['completed_steps']>=151000,'scheduler body 0x12adec did not execute')
    r=rows['irq127_appcore_roundtrip']
    d=check_delivery(r,before_rti=False)
    events=r['machine']['events']
    rti=[e for e in events if e['kind']=='software_irq_rti']
    require(len(rti)==1 and rti[0]['core']==0 and rti[0]['pc_after']=='0x6012028' and
            rti[0]['reti_from_frame']=='0x6012028' and rti[0]['bank_writeback_is_assumption'],
            'the restored task did not enter the real app_core entry through RTI')
    restored=[e for e in events if e['kind']=='scheduler_task_gprs_restored']
    require(restored and restored[0]['unknown_registers']==list(range(1,16)),
            'scheduler GPR restore lost the unknown task slots')
    require(any(e['kind']=='core_idle_wait_entered' for e in events),
            'peer core never reached its IDLE wait under the projection')
    require(r['stop']['kind']=='unmodeled_unmaskable_irq' and r['stop']['core']==1 and
            r['stop']['source']=='software_irq',
            'round trip must stop at the next real boundary: the CPU1 unmaskable software IRQ')
    require(r['completed_steps']>=153000,'app_core task did not execute after RTI')
    r=rows['irq125_sync_roundtrip']
    deliveries=r['machine']['software_irq_entry_projection']['deliveries']
    require([(d['core'],d['irq'],d['handler']) for d in deliveries]==
            [(0,127,'0x12ad2a'),(1,125,'0x60662c8')],
            'the peer sync IPI was not delivered to the real CPU1 handler')
    sync=deliveries[1]
    require(sync['vector_address']=='0x19fff4' and sync['entry_banks'] and
            not sync['entry_banks']['silicon_bank_switch_certified'],'CPU1 sync delivery record differs')
    require(r['peripherals']['software_irq_commands']['pending']['1']=={'known_mask':'0xf','known_value':'0x0'},
            'CPU1 sync pending was not acknowledged by its real handler')
    gpio=r['peripherals']['gpio'].get('0xfd5308')
    require(gpio=={'known_value':'0x60','known_mask':'0x68'},
            'the register-AND must clear the known bit3 and leave the masked-out bit0 unknown')
    out=r['peripherals']['gpio'].get('0xfd5300')
    require(out=={'known_value':'0x8','known_mask':'0x8'},
            'the SFC resume must re-set the known bit3 on GPIO OUT without fabricating its neighbours')
    adapters=r['instruction_adapters']
    require(adapters.get('gpio_out_readback_load')==12 and
            adapters.get('gpio_out_readback_bit_mask')==4 and
            adapters.get('gpio_out_readback_register_copy')==1 and
            adapters.get('gpio_out_readback_writeback')==2,
            'the SFC GPIO OUT read-back path was not exercised through its exact load/mask/copy/write-back shape')
    # The CPU1 handler tail pops its real {psr,rets,reti} frame (0x606633e,
    # app disassembly: pop.rmask r4-r0 / pop.srl m=41 / csync / rti) and the
    # symmetric RTI returns the core to its interrupted idle instruction.
    require(adapters.get('conditional_software_irq_rti')==2 and
            adapters.get('software_irq_handler_context_frame')==4,
            'the CPU1 handler tail frame pop and symmetric RTI were skipped')
    rti=[e for e in r['machine']['events'] if e['kind']=='software_irq_rti']
    require(len(rti)==2 and rti[1]['core']==1 and rti[1]['pc_after']=='0x12c5ca' and
            rti[1]['reti_from_frame']=='0x12c5ca' and rti[1]['sp_after']=='0x401bf8' and
            rti[1]['bank_writeback_is_assumption'],
            'the CPU1 sync handler did not return to its interrupted idle task through RTI')
    events=r['machine']['events']
    parks=[i for i,e in enumerate(events) if e['kind']=='core_idle_wait_entered' and
           e['core']==1 and e['pc']=='0x12c5ca']
    require(len(parks)==2 and parks[1]>events.index(rti[1]),
            'the returned CPU1 idle task did not re-park after its RTI (first park + re-park)')
    require(r['stop_core']==0 and r['stop_pc']=='0x6000f44' and
            r['stop']=={'kind':'unmapped_or_readonly_write','access':'write','address':'0xfd3504',
                        'size':4,'value_bytes':'00000000'},
            'after the peer return the stop must be CPU0 reaching the CRC engine control write')
    require(r['completed_steps']==155127,'the CPU1 handler return path changed its step count')
    held=r['peripherals']['cpu1_held_projection']
    require(held['current_control']==9 and held['kick_rmw_writes']==3,
            'the CPU1 PMU_CON0 kick bit was not recorded as exactly three |=8 retries')
    require(r['instruction_adapters'].get('cpu1_control_rmw')==4,
            'the release RMW plus three kick RMWs must be the only CPU1 control RMWs')
    wakes=[e for e in r['machine']['events'] if e.get('kind')=='core_woken_by_cpu1_control_kick']
    require(len(wakes)==1 and wakes[0]['pc_after']=='0x606631a' and
            wakes[0]['idle_completion_is_assumption'] and wakes[0]['wake_source_unverified'],
            'the kick wake of the parked peer core must stay an explicit assumption')
    require('cpu1_control_kick_bit3' in r['peripherals']['assumptions'],
            'the PMU_CON0 bit3 kick assumption must be recorded explicitly')
    gate=r['peripherals']['sfc_clock_gate_projection']
    require(gate['operations']==list(SFC_RESUME_OPERATIONS),
            'the SFC suspend/resume clock-gate writes changed')
    require(gate['divider']=='0xff' and gate['divider_writes']==36 and gate['reads']==36 and
            gate['readback_modeled'] is True and gate['known_value']=='0x5000' and
            gate['known_mask']=='0x5c09',
            'the SFC +8 divider write/read-back or the PLL-lock bit15 projection differs')
    require(r['peripherals']['sfc_read_bus_secondary']['con']==0 and
            r['peripherals']['sfc_read_bus_secondary']['writes']==0 and
            r['peripherals']['sfc_read_bus_secondary']['initial_control_fixture']==0,
            'the second SFC (0xfe0100) reset-state fixture or write count differs')
    r=rows['irq125_repeat_inert']
    # 暂停点1(repeat_irq_delivery)的实证边界:新投影打开后,前沿前缀必须完全
    # 不变(该场景不启用 hardware_crc,停在 155,127 的 CRC 引擎种子写)。前缀内
    # 固件对每核只 pend 过一次(commands=4:两核各 pend+clear,CPU1 handler 入口
    # 自清),第二次交付从未被请求;投影保持惰性,直到固件真实地再次 pend。
    require(r['completed_steps']==155127 and r['stop_core']==0 and r['stop_pc']=='0x6000f44' and
            r['stop']=={'kind':'unmapped_or_readonly_write','access':'write','address':'0xfd3504',
                        'size':4,'value_bytes':'00000000'},
            'the repeat delivery projection must not move the frontier stop')
    require(r['machine']['repeat_irq_delivery_projection']['enabled'] and
            not r['machine']['repeat_irq_delivery_projection']['nesting_implemented'] and
            r['machine']['repeat_irq_delivery_projection']['pending_rearmed_only_by_real_firmware_command'],
            'the repeat delivery projection is missing from the machine report')
    deliveries=r['machine']['software_irq_entry_projection']['deliveries']
    require([(d['core'],d['irq']) for d in deliveries]==[(0,127),(1,125)],
            'enabling repeat delivery changed the delivery set before the frontier')
    require(r['peripherals']['software_irq_commands']['commands']==4 and
            r['peripherals']['software_irq_commands']['acknowledgements']==2,
            'the firmware issued unexpected extra software IRQ commands before the frontier')
    r=rows['crc_engine_roundtrip']
    deliveries=r['machine']['software_irq_entry_projection']['deliveries']
    require([(d['core'],d['irq'],d['handler']) for d in deliveries[:2]]==
            [(0,127,'0x12ad2a'),(1,125,'0x60662c8')],
            'the CRC scenario must reuse the exact scheduler/sync deliveries')
    crc=r['peripherals']['hardware_crc_projection']
    # The wrapper (app 0x6000f2c) writes seed 0, feeds the 30 bytes at 0x400b22
    # one st.w each, csyncs and reads the result once. The expected value is
    # recomputed here from the app software CRC-16 parameters (poly 0x1021,
    # MSB-first, init=seed, no xorout) so the projection is checked against the
    # golden software algorithm, not against its own implementation.
    fed=bytes.fromhex('60e0e0c183070f3e5c983142a468f0e1c3872e7cd8b163e6ecd9b367cf9f')
    reference=0
    for byte in fed:
        reference^=byte<<8
        for _ in range(8):
            reference=((reference<<1)^0x1021)&0xffff if reference&0x8000 else (reference<<1)&0xffff
    require(crc['seed_writes'][0]=='0x0' and crc['result_reads'][0]==hex(reference) and
            crc['fed_bytes']>=len(fed),
            'the hardware CRC projection did not reproduce the software-table result for the actual wrapper input')
    # sfc_drop_cache 之后 VM init 链继续:同一 CRC 引擎再用 13 次(每次种子 0、
    # 喂 30 字节、读一次结果,共 14 次往返 420 字节),首轮黄金校验不变。
    require(len(crc['seed_writes'])==14 and crc['fed_bytes']==420 and len(crc['result_reads'])==14,
            'the extended VM-init prefix changed the hardware CRC usage shape')
    # 0x6010b14 rep 4 32(4 字节体 ld.bu.pin+st.b.pin × 32,flash 资源表头部
    # 32 字节拷贝)及其后整条资源表扫描(含 callee 0x6010a64 内四个 rep 4 8)
    # 全部按多指令体真实执行:70 个 rep.i 体、155,389→162,447 步;扫描完成后
    # 的一次性 SFC 时钟重配(0x12762c 标志守卫)写 0xfd1c04=3(+4 只写记录)。
    # 之后 0x600a352 rep 4 4 从 qcy_boot_info 0x122800 拷 16 字节头:bytes
    # 1-3/8-11 无任何写入者(boot.h vm_info 的 C padding 与 vm_res),按
    # rep_copy_unknown_transport 运输未知字节;消费函数 0x600a2aa 只用
    # vm_saddr(word 4-7,VM init 0x6011102 写入的 0x1da000),call 0x12bdc6
    # 算出 VM 区窗口地址 0x061d7fe0(=0x06000000+0x1da000-0x2020)。随后
    # 0x600a36e..0x600a37e 的 SFC 窗口组写入(r1=0xfe0218:[+12]=0x061d7fe0、
    # [+8]=0x7ffffff、[0]|=2)按 write-only 记录走完(sfc_read_bus 扩展),
    # call 0x12c030 后 CPU0 真实地第二次 pend irq125 到 CPU1(repeat 投影
    # +cpu1_control bit0 释放位修正),第二次交付把 CPU1 从停泊唤醒、
    # handler 处理完回停泊。之后 VM init 链 call ROM export sfc_drop_cache
    # (0x1fdcc→0xc726,实参 beg=0x061d7fe0/len=0x20):把 [0x061d7fe0,
    # 0x061d8000) 配进两核 ICU(每核 cli→testset 0x19fd78 抢锁→[+0x14]=begin、
    # [+0x18]=end-1(write-only 记录)→[+0x10]=0xca000040(0xc72c if.ge len>=
    # 0x1000 在 0xc730/0xc736 两条谓词 li 里二选一,实测 len=0x20 选 0x40)→
    # call 0xbac0 等 CON bit30 完成(与 0xca000010 使能路径同一条轮询指令)→
    # call 0xbad4 释放锁),r3+=0x800 换核重复 2 次。region 命令完成后尾调
    # flushinv_dcache(0x1fdb0→0xc71a→0xc6f4):0xff05cc 逐地址失效循环,体是
    # 寄存器计数 `rep r1,4`(add.rrr.p+st.w 并行对)。三件套建模后走通:
    # ①register_rep_multi_block(immediate_rep_multi_block 推广到寄存器计数
    # rep,计数来自真实寄存器值,计数寄存器按 FM-1 run3 证据递减到 0,体后
    # 编译器生成的 bnez r1 自然不跳);②并行对体按 exact_parallel_stores 的
    # 旧寄存器语义执行(存储半读 A 之前的旧 r0,首写 [0xff05cc]=0x061d7fe0
    # =行地址,r0 递增 32);③dcu_cmo_projection 把 0xff05cc 写入记为
    # write-only 序列(SDK csfr.h JL_L1P 的 CMO[19],__WO;flush_dcache 用的
    # 0xff05c8=CMO[18] 一并建模,本前缀未出现)。flushinv_dcache 尾部
    # csync+ssync 走通(ssync 按 run4「无可见效果」模型),返回 sfc_drop_cache、
    # VM init 链继续 12k 步后撞上 ROM 0x4176 字节拷贝尾循环 `rep r2,4`(该次
    # 拷贝 len&3==0,count=0):opt-in zero_count_rep_skip(multi-block gate 的
    # 零跳过假设,memset 先例 rom_zero_rep_skip/0x4242、0x424c 的推广——体一
    # 次也不执行、PC 前进到体后、计数寄存器保持 0,FM-1 run3「递减到 0」在
    # 0 次执行下的自然延伸;count=0 本身 BR28 未测量,报告以
    # zero_count_skip_is_assumption 计数、不声称硅验证)。跳过两次(0x4176 尾
    # ×2)后链继续 892 步(ROM 拷贝族再加 2 个非零寄存器计数 rep、CRC 引擎
    # 再用 2 次 12→14),停在 ROM 0x416c(0x1fd74→0x413c memcpy 字主体首条
    # ld.w.pin)读 0x60fdfe0——源指针由 app 经 ROM 解析,恰为 flash 偏移
    # 0x100000(1MiB,app flash 偏移探测表 0x060A0FA4 {0,0x1000,0x10000,
    # 0x80000,0x100000} 的第 5 项;CPU 可见地址=0x06000000+偏移-0x2020 头)。
    # 按实测物理布局(docs/usb-recovery.md),0x0ee000 起是 bank B 应用区,
    # 0x100000 落在 bank B 内;官方 UFW 的 flash.bin 只含引导区+bank A
    # (0..0xE6000),bank B 字节不在任何公开工件里——不臆造,诚实停。
    require(r['stop_core']==0 and r['stop_pc']=='0x416c' and
            r['stop']=={'kind':'unmapped_memory','access':'read','address':'0x60fdfe0',
                        'size':4,'reference_sdk_register':None},
            'after the two zero-count REP skips the stop must be the genuine bank-B flash read')
    require(r['completed_steps']==176288,'the zero-count REP skip changed its step count')
    require(r['instruction_adapters'].get('zero_count_skip_is_assumption')==2,
            'the zero-count REP skip assumption must fire exactly twice (both 0x4176 tails) '
            'and be reported as an explicit assumption')
    dcu=r['peripherals']['dcu_cmo_projection']
    require(dcu['ports']['0xff05cc']=={'values':['0x61d7fe0'],'writes':1} and
            dcu['ports']['0xff05c8']=={'values':[],'writes':0},
            'the DCU invalidate port did not record exactly the one observed line address')
    require(dcu['write_only'] and not dcu['readback_observed'] and
            not dcu['port_index_semantics_verified'] and
            not dcu['cache_contents_or_coherence_simulated'],
            'the DCU CMO projection report is missing its explicit assumption record')
    require(r['instruction_adapters'].get('register_rep_multi_block_projection')==7,
            'the register-count REP projection must cover exactly the DCU loop (0xc70c) plus the '
            'six nonzero ROM memcpy counters (0x416a word bodies and 0x4176 byte tails) in the extended prefix')
    gate=r['peripherals']['sfc_clock_gate_projection']
    require(gate['select']=='0x3' and gate['select_writes']==1 and
            gate['select_write_only'] is True and gate['select_readback_observed'] is False,
            'the SFC clock +4 select write-only record differs')
    # SFC 主块窗口组(0xfe0218/+0x20/+0x24)write-only 记录:初始配置序列
    # 各写一次,+0x18 只有一次 |=2(or 置位,不经读回)。
    window=r['peripherals']['sfc_read_bus']['window_registers']
    require(window['base']=='0x61d7fe0' and window['base_writes']==1 and
            window['limit']=='0x7ffffff' and window['limit_writes']==1 and
            window['control_operations']==[{'kind':'or','operand':'0x2'}] and
            window['control_known_mask']=='0x2' and
            window['write_only'] is True and window['readback_observed'] is False,
            'the SFC window register record differs')
    require(r['instruction_adapters'].get('sfc_window_enable_rmw')==1,
            'the SFC window control RMW was not executed exactly once')
    # 两核 ICU region 组(opt-in icu_region_projection):sfc_drop_cache 把
    # [0x061d7fe0,0x061d8000) 配进两核——+0x14/+0x18 各一写(write-only 记录),
    # +0x10 命令 0xca000040 与 0xca000010(bootstrap 使能)同走 CON bit30
    # 完成投影(64 步 fixture),两核各完成一次,busy 读 14→74。
    icu=r['peripherals']['icu_initialization_projection']
    region=icu['region_projection']
    require(icu['commands']==[{'address':'0xffec10','value':'0xca000010'},
                              {'address':'0xffe410','value':'0xca000040'},
                              {'address':'0xffec10','value':'0xca000040'}] and
            icu['completed_commands']==3 and icu['busy_reads']==74 and not icu['pending_cores'],
            'the two-core ICU region commands did not run through the shared completion projection')
    require(region['registers']['0xffe400']=={'0x14':{'value':'0x61d7fe0','writes':1},
                                              '0x18':{'value':'0x61d7fff','writes':1}} and
            region['registers']['0xffec00']=={'0x14':{'value':'0x61d7fe0','writes':1},
                                              '0x18':{'value':'0x61d7fff','writes':1}},
            'the ICU region begin/end records differ from the observed sfc_drop_cache arguments')
    require(region['write_only'] and not region['readback_observed'] and
            not region['command_low_byte_semantics_verified'] and
            region['same_busy_machinery_as_command4'],
            'the ICU region projection report is missing its explicit assumption record')
    # 窗口组放行后 CPU0 第二次 pend irq125(commands 4→6),第二次交付把
    # 停泊的 CPU1 再唤醒一次:暂停点1 的真实第二次交付在 162,898 前沿被观察
    # 到。DCU 失效循环走通后 VM init 链继续与 CPU1 同步:又 pend 4 次(合计
    # commands 14/acks 7,交付 1+6 次,CPU1 每次回同一停泊 0x12c5ca)。
    deliveries=r['machine']['software_irq_entry_projection']['deliveries']
    require([(d['core'],d['irq'],d['handler'],d['reti_from']) for d in deliveries]==
            [(0,127,'0x12ad2a','0x12b608')]+
            [(1,125,'0x60662c8','0x12c5ca')]*6,
            'the repeated cross-core deliveries to the parked CPU1 did not occur through the repeat projection')
    require(r['peripherals']['software_irq_commands']['commands']==14 and
            r['peripherals']['software_irq_commands']['acknowledgements']==7,
            'the VM-init sync requests changed the software IRQ command bookkeeping')
    # 70 个体来自资源表扫描,第 71 个是 0x600a352 rep 4 4(拷 16 字节 boot_info
    # 头);其中第 1/3 轮(word0 含 padding、word2 是 vm_res)走未知字节运输,
    # 第 2/4 轮(vm_saddr/vm_size 全已知)走常规严格读。DCU 失效循环走通后
    # VM init 链的 ROM 拷贝族再加 9 个 rep.i 体(71→80)。
    require(r['instruction_adapters'].get('immediate_rep_multi_block_projection')==80,
            'the flash resource-table scan must execute its rep.i blocks through the multi-block projection')
    require(r['instruction_adapters'].get('rep_copy_unknown_transport_load')==2 and
            r['instruction_adapters'].get('rep_copy_unknown_transport_store')==2,
            'the boot-info padding/reserved words were not transported exactly twice')
    r=rows['crc_bankb_erased_fixture']
    # bank B 头 0xFF fixture(显式环境假设,unverified erased-bank fixture,非已知
    # 出厂状态;范围=探测表第 5 项的 32 字节头读取窗,其余 bank B 严格 unmapped)。
    # 消费者分支图(app 反汇编+trace 钉死,报告/HANDOFF 有全文):find_partition
    # 0x6010f32 主循环 5 项逐项读 32B 头→crc16(头 bytes 2..31)→0x6010f80 与头前
    # 2 字节期望值比较,不匹配→0x6010f5a 良性跳下一项;0xFF 头期望 0xffff、真实
    # 计算值 0x75f8(下面对黄金软件算法重算),必然不匹配→r4=5→0x6010f5c ifs
    # (r4>4)→0x6010fe6 return 1;调用者 VM init(0x6011210)free 临时对象、清
    # OTP 全局槽 827/828 后与成功路径汇合,VM init 返回值被 0x601205a 忽略——
    # 失败分支良性,由真实代码自己计算 CRC 并选择分支(不强制任何结果)。
    crc_ff=r['peripherals']['hardware_crc_projection']
    reference_ff=0
    for byte in b'\xff'*30:
        reference_ff^=byte<<8
        for _ in range(8):
            reference_ff=((reference_ff<<1)^0x1021)&0xffff if reference_ff&0x8000 else (reference_ff<<1)&0xffff
    require(reference_ff==0x75f8 and reference_ff not in (0,0xffff),
            'the golden CRC of an erased 0xFF header must stay a non-matching value')
    # 前 14 次往返与 crc_engine_roundtrip 完全相同(fixture 不改变既有前缀),
    # 第 15 次(result_reads[14])是 0xFF 头的真实计算值,后 4 次是探测失败后
    # VM init 链继续运行时的真实 CRC 使用——失败分支确实继续执行的证据。
    crc_base=rows['crc_engine_roundtrip']['peripherals']['hardware_crc_projection']
    require(crc_ff['seed_writes'][:14]==crc_base['seed_writes'] and
            crc_ff['result_reads'][:14]==crc_base['result_reads'] and
            crc_ff['fed_bytes']==crc_base['fed_bytes']+360,
            'the erased fixture must not change the first fourteen CRC round trips')
    require(crc_ff['result_reads'][14]=='0x75f8' and len(crc_ff['seed_writes'])==26 and
            crc_ff['fed_bytes']==780 and len(crc_ff['result_reads'])==26,
            'the fifth probe entry must really CRC the 0xFF header bytes and the chain must continue')
    require(crc_ff['result_reads'][15:]==['0xc190','0xf271','0xb758','0x5ddd',
                                          '0xa1cd','0x9946','0xd1cf','0xc190','0xf271','0xb758','0x5ddd'],
            'the post-probe VM-init CRC usages changed after the fixture took the mismatch branch')
    # 簿记读边界已解决(st.d.sp.l 双字全 8 字节覆盖写,exact_doubleword_stores
    # 两半直写);第二数据窗的表 walker(上一路钉死:有限小窗两项、-248 跳过
    # 零副作用)与 qcy_bss 16 字节 VM 配置块(SET 路径已写入、7 未知字节与
    # boot-info padding/vm_res 同源,GET 消费者只读已知字段,字节形状 unknown
    # 运输放行)都走通后,旧前沿 184,113 停在 ROM 0x4178 字节尾读 bank B VM
    # 窗口基址 0x61d7fe0。本次(路 S):VM 区消费结构先反汇编+trace 钉死——
    # 0x6060f9a 读两半 magic 各 4 字节(0x6061094/0x606109e,flash 0x1da000/
    # 0x1db000)后 magic switch(0x60610a4..0x60610c2:0x5A5A5A5A/0xFFFF5A5A/
    # 0xFFFFFF56/0xFFFFFFFF/0xFF5A5A5A),擦除态 0xFFFFFFFF→0x6061106→
    # half-2 magic≠0xFF5A5A5A→0x6061120 call 0x600a7b6 空白检查:0x600a79e
    # 每半换算 CPU 地址后 0x600a78a 逐字节扫 len=0x1000 检查全 0xFF(实测
    # 0x600a78c 执行 8192 次、读 0x61d7fe0..0x61d9fdf,遇非 0xFF 立即中止)
    # ——数据依赖的结构性全扫描,逐窗扩 fixture 只会把 unmapped 停点后挪
    # 一字节,故按结构声明范围一次性映射 VM 区全长 0x2000 字节(范围=
    # boot_info vm 字段声明+消费者 4K 对齐切半算法+空白检查 len,全部是
    # 固件自己的结构声明;同第二窗「表结构=擦除态」语义)。全空白分支良性
    # (trace 钉死):两处格式化写 call(0x600a7da/0x600a7f6,只在半区部分
    # 写入且头无效时执行)零执行、清状态位、0x6061274 清理路径设 VM 空状态、
    # 读 sfc 头 2 字节(0x06000000,flash image 已知字节)与缓存版本不等→
    # 从 app flash 表 0x60a9344 重建位图、return 0——空白 VM 就是「全新
    # 芯片」的良性初始化路径,由真实代码自己逐字节扫描自己选分支。放行后
    # 链继续 44,137 步(寄存器计数 rep 11→16、双字存储 15→19、单存 rep
    # 13→16、irq_repeat_return 146→160;CRC 19 次往返 570 字节、IRQ commands
    # 16/acks 8、交付 1+7 全不变——空白扫描是纯字节比较,不经硬件 CRC、
    # 无跨核同步),旧前沿 228,250 停在 ROM 0x417e——memcpy 族的「不对齐源
    # 组装」变体。路 T(本次):opt-in exact_parallel_insert_load 按对称旧值
    # 假设执行 insert.rrii.p∥ld.bu.pin(A 半 insert 读 B 半 ld 写之前的旧
    # 寄存器——与 FM-1 run3 捕获的 exact_parallel_stores 方向相反,无硬件
    # 捕获证据;厂商 memcpy 的软件流水只有在旧值语义下才能把字节按序组装,
    # 未知源字节经 rep_copy_unknown_transport 零扩展运输、按字节来源组装
    # 传播)。放行后链继续 7,692 步(compose 对执行 4 次=2 轮 rep×2 对、
    # 寄存器计数 rep 16→21、rep.i 体 104→143、双字存储 19→21、
    # irq_repeat_return 160→202;CRC 19→26 次往返 780 字节、IRQ commands
    # 16/acks 8、交付 1+7 全不变——新增前缀是 app 侧重扫描与 memcpy,无新
    # 跨核同步),停在 app 0x6060df4 `ld.bu b[r0+3]` 读 0x61de1e3——
    # 0x6060dd6 call 0x60609ac(描述符 thunk,与 0x6060a4a 表 walker 同族)
    # 后 r0=[desc+8]+512=window(0x61ddfe0)+0x200=0x61de1e0,首个头字节读
    # 在 +3——bank B flash 偏移 0x1e0203,在第二窗 20 字节 fixture
    # (0x1e0000..0x1e0014)之外、仍在 0x1e0000 window 区内(VM 尾与 TON
    # 0x1e1000 之间)——旧决策点:window+0x200 结构区的消费者分支图
    # 与 fixture 范围声明(235,942)。路 U(本次):静态全图+动态 trace 钉死
    # window 区(0x1e0000..0x1e1000)不是被系统性枚举——消费者=cfg_tool/btif
    # 存储后端 ops 表(0x60c32c8 起 28 字节记录)的成员,init 0x6060d7c 是
    # 平台 init 表遍历(0x601206c)最后一项:双 thunk("flash/res/cfg_tool.bin"
    # +"flash/app/btif")后解析 window+0x200 的 8 字节首条目头
    # {u32 len;u32{b0 crc,bits20+ size}};0xFF 擦除态下 (len-1)=0xfffffffe
    # 无符号 >2047,真实代码只读头 4 字节就在 0x6060e0e 拒绝返回 0(bytes
    # 4-7 不读、CRC 不算、无 flash 写),条目有效位 b[0x126568] 保持 0 把
    # walker 0x60112c8(入口判 !=1 即跳)挡住;其余记录区消费者 0x6060c6a/
    # 0x600a412 的 walk 基址=getter(103)+OTP 槽 827(本前缀已清 0,槽 828
    # ==0 直接返回 -250)、表区写路径 0x6060b80 未调用——故 fixture 只按
    # 结构声明映射完整 8 字节头(第四窗,同 opt-in erased_bankb),不扩
    # 记录区其余 0xE00 字节。放行后链继续 473 步(仅双字存储 21→22;rep/
    # 运输/compose/CRC 26 次往返 780 字节/IRQ commands 16/acks 8/交付 1+7
    # 全不变——记录头校验是 4 字节读+分支,无新跨核同步),停在 app
    # 0x6011370 `mov.rr.p`(A 半 r6=r0 ∥ B 半 h[r4+0]=r5,0x6011366 记录
    # 解析 helper 入口把出参半字清零)写虚拟栈 0x400b96(物理 0x19f794,
    # 未初始化)——上游 pair 存储模型的簿记读边界(uninitialized_memory),
    # 与 183,486 的 st.d.sp.l 同类(半字部分写、邻字节真保持旧值,决策归
    # 下一路),非 bank B 数据可得性问题(236,415,新前沿)。
    require(r['stop_core']==0 and r['stop_pc']=='0x6011370' and
            r['stop']['kind']=='uninitialized_memory' and r['stop']['address']=='0x19f794',
            'the record-header fixture frontier must stop at the pair-store bookkeeping read')
    require(r['completed_steps']==236415,'the erased fixture scenario changed its step count')
    # compose 对(装载+组装)假设执行:2 轮 rep×2 对=4 次,adapter 名带
    # _is_assumption 显式标注旧值方向无硬件捕获证据。
    require(r['instruction_adapters'].get('parallel_insert_load_old_registers_is_assumption')==4,
            'the insert/load parallel pair must execute exactly four times (two REP rounds) '
            'through the old-register assumption adapter')
    require(r['instruction_adapters'].get('exact_doubleword_store')==22,
            'the doubleword stores of the extended prefix must run through the exact gate')
    require(r['peripherals']['software_irq_commands']['commands']==16 and
            r['peripherals']['software_irq_commands']['acknowledgements']==8,
            'the extended VM-init sync requests changed the software IRQ bookkeeping')
    deliveries=r['machine']['software_irq_entry_projection']['deliveries']
    require([(d['core'],d['irq'],d['handler'],d['reti_from']) for d in deliveries]==
            [(0,127,'0x12ad2a','0x12b608')]+[(1,125,'0x60662c8','0x12c5ca')]*7,
            'the extra cross-core delivery after the probe mismatch differs from the repeat projection')
    # ROM 拷贝族/CPU0-CPU1 同步在 fixture 放行后继续:rep.i 体 80→143(第 5 项
    # 头拷贝+后续链+walker 后链+compose 放行后的 app 侧链)、零计数跳过 2→3
    # 不变、寄存器计数 rep 7→21(walker 后链 GET 消费链 +1、VM 空白扫描后的
    # cfg 链 +5、compose rep 本体 +1 及其后再 +4)。unknown 运输 2/2→9/9:字
    # 形式 2 轮(boot_info padding/vm_res)不变,字节形式 +7(GET rep 4 16 的
    # 7 个未知字节,同一 16 字节块内的 padding/vm_res,来源链回 0x122800);
    # compose 对的源是 app flash 已知字节,不走运输。CRC 19→26 次往返 780
    # 字节(app 侧对既有窗口数据的重扫描);IRQ commands 16/acks 8 不变(新
    # 增前缀无新跨核同步)。
    require(r['instruction_adapters'].get('immediate_rep_multi_block_projection')==143 and
            r['instruction_adapters'].get('zero_count_skip_is_assumption')==3 and
            r['instruction_adapters'].get('register_rep_multi_block_projection')==21 and
            r['instruction_adapters'].get('rep_copy_unknown_transport_load')==9 and
            r['instruction_adapters'].get('rep_copy_unknown_transport_store')==9,
            'the fixture-scenario REP projections differ from the observed prefix')
    report['irq127_scheduler_loop_entered']=True
    report['app_core_entered_after_irq_rti']=True
    report['dual_core_sync_handshake_executed']=True
    report['hardware_crc_engine_modeled']=True
    report['erased_bankb_fixture_assumption']=(
        'unverified erased-bank fixture (NOT a known factory state), four evidence-bounded windows: '
        '(1) the 32-byte bank-B header read at 0x60fdfe0 (flash offset 0x100000, probe-table entry 5) '
        'is mapped read-only to 0xFF; the consumer find_partition 0x6010f32 really computes CRC-16 over '
        'the 30 header data bytes (golden value 0x75f8, recomputed in the assertion from the app software '
        'parameters) and compares it at 0x6010f80 against the header word 0xffff, so the benign mismatch '
        'branch (0x6010f5a next entry -> table exhausted -> return 1 -> caller frees the temp object and '
        'clears the OTP slots 827/828, the VM-init return value ignored at 0x601205a) is selected by real '
        'code, never forced. (2) The 20-byte window-table structure at 0x61ddfe0 (flash offset 0x1e0000, '
        'the window the qcy consumer 0x6060a4a caches into the global 0x126544) is mapped read-only to '
        '0xFF over the full on-flash extent declared by the app u16-pair table 0x60a1e8a (two entries '
        '{id=0x66,size=6}/{id=0x6e,size=6}, each 4-byte header + payload); with erased headers the '
        'header check 0x60609ec reads only the two 4-byte headers (LE32 0xffffffff -> return -248) and '
        'the caller loop 0x6060a9a skips to the next entry with zero side effects, again selected by '
        'real code. (3) The full declared VM region 0x2000 bytes at 0x61d7fe0 (flash offset 0x1da000, '
        'the two 0x1000 halves the VM-config consumer 0x6060f9a derives from the boot-info vm fields '
        'after the 8192 clamp) is mapped read-only to 0xFF over its whole structural extent: the '
        'erased magic words 0xffffffff route to the blank check 0x600a7b6 whose byte-by-byte scan '
        '(0x600a78a, len 0x1000 per half, observed 8192 loads over 0x61d7fe0..0x61d9fdf, aborting '
        'at the first non-FF byte) confirms an all-erased VM, skips both format-write calls, sets up '
        'the empty-VM state, rebuilds the bitmap from the app-flash table 0x60a9344 and returns 0 '
        'again selected by real code; the window is declared structurally, not incrementally, '
        'because the scan is data-dependent — a partial window would only move the unmapped stop '
        'one byte later. (4) The 8-byte first-record header at 0x61de1e0 (window+0x200, flash offset '
        '0x1e0200) of the record area the cfg-tool/btif storage backend initializes: its init member '
        '0x6060d7c (the last platform-init table call at 0x601206c) parses the header as '
        '{u32 len; u32{b0 crc, bits20+ size}}; with erased bytes len=0xffffffff and the unsigned '
        'range check (len-1)>2047 at 0x6060e0e rejects after reading only the first 4 header bytes '
        '(observed in the trace: bytes 4-7 unread, no CRC call, no flash write, return 0), leaving '
        'the entry-valid byte b[0x126568] at 0 which gates the find-by-index walker 0x60112c8 — '
        'the fixture covers the complete 8-byte header structure the consumer itself parses '
        '(same structure-unit semantics as window 2), not the remaining record area, for which no '
        'erased-state read exists (the other record-area consumers walk from getter(103)+OTP slot '
        '827 and the OTP slot 828==0 gate returns -250 immediately in this prefix). Every other '
        'bank-B address (including the TON window at flash 0x1e1000 and the record payload area '
        'beyond the 8-byte header at window+0x208) stays strictly unmapped; the '
        'crc_engine_roundtrip scenario without the fixture still stops honestly at 176,288 on the '
        'unmapped 0x60fdfe0 read')
    report['insert_load_parallel_pair_assumption']=(
        'opt-in exact_parallel_insert_load: the compose pair of the misaligned-source ROM memcpy '
        '(0x417e, rep r5,22: four ld.bu.pin byte loads + insert.rrii.p||ld.bu.pin pairs assembling '
        'bits 8/16/24 + one plain insert.rrii + st.w.pin) is executed with BOTH halves reading the '
        'pre-pair register file: the A half (insert) reads the register the B half (ld.bu.pin) '
        'writes in the same pair. FM-1 run3 hardware capture only established the opposite '
        'direction (exact_parallel_stores: the second half reads the register as it was before the '
        'first half); the A-reads-what-B-writes direction has NO hardware capture evidence and is '
        'a symmetry assumption — the only semantics under which the vendor compiler software '
        'pipeline assembles the bytes in order (a new-value read would skip/duplicate source '
        'bytes and memcpy would be wrong). Reported through the '
        'parallel_insert_load_old_registers_is_assumption adapter; unknown source bytes are '
        'transported (zero-extended UnknownWord, rep_copy_unknown_transport) and assembled '
        'byte-granularly with their physical origins; non-byte-aligned insert fields over '
        'unknown bytes still stop honestly')
    for r in report.get('candidate_prefixes',[]):
        d=r['machine']['software_irq_entry_projection']['deliveries']
        require(len(d)==1 and d[0]['vector_address']=='0x19fffc','candidate delivery differs')
        require(r['stop']['kind']=='unknown_core_special_register' and r['stop'].get('register')=='usp',
                'candidate handler left the modeled region unexpectedly')
    report['irq127_entry_checks_passed']=True
    report['generic_irq_entry_implemented']=False
    report['repeat_delivery_second_observation']=('observed at the 162898 frontier: after the SFC window '
        'writes are recorded, CPU0 really pends irq125 to CPU1 a second time (software IRQ commands '
        '4 to 6) and the repeat delivery projection wakes the parked CPU1 once more; the handler '
        'returns to the same idle park 0x12c5ca. Delivering it also required the cpu1_control '
        'release-bit (bit0) fix: the kick bit3 record (control word 9) must not revoke the released '
        'core delivery eligibility that the advance() pending checks gate on')


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--rom',type=Path,required=True)
    p.add_argument('--firmware',type=Path,default=ROOT/'firmware/QCY_H3S_7018F_1T2_release_note_V1.1.8_011.ufw')
    p.add_argument('--repaired-candidate',type=Path);p.add_argument('--failed-r2',type=Path)
    p.add_argument('--report',type=Path,required=True);args=p.parse_args()
    rom=args.rom.read_bytes();check_rom(rom);module=upstream();app=application(args.firmware);header=package_header(args.firmware)
    image=flash_image(args.firmware)
    report={'firmware_sha256':OFFICIAL_SHA,'application_sha256':APP_SHA,'rom_sha256':ROM_SHA,'simulator_commit':PIN,
        'device_accessed':False,'whole_h3s_boot_simulated':False,'candidate_whole_boot_validated':False,
        'suitable_for_flash_approval_alone':False,
        'vector_slot_formula_evidence':'app bytes [r1+r0<<2]=r2 at 0x6065d90 after li 0x19FE00; caller 0x6066100 passes irq=0x7f-cnum handler=0x12AD2A',
        'scenarios':[irq_case(module,app,header,rom,'irq127_request_strict'),
                     irq_case(module,app,header,rom,'irq127_entry_minimal',entry=True),
                     irq_case(module,app,header,rom,'irq127_scheduler_entry',entry=True,banks=True),
                     irq_case(module,app,header,rom,'irq127_appcore_roundtrip',
                              entry=True,banks=True,idle_wait=True,rti=True),
                     irq_case(module,app,header,rom,'irq125_sync_roundtrip',
                              entry=True,banks=True,idle_wait=True,rti=True,unmaskable=True,
                              sfc_suspend=True,gpio_out_readback=True,kick_wake=True),
                     irq_case(module,app,header,rom,'irq125_repeat_inert',
                              entry=True,banks=True,idle_wait=True,rti=True,unmaskable=True,
                              sfc_suspend=True,gpio_out_readback=True,kick_wake=True,repeat=True),
                     irq_case(module,app,header,rom,'crc_engine_roundtrip',
                              entry=True,banks=True,idle_wait=True,rti=True,unmaskable=True,
                              sfc_suspend=True,gpio_out_readback=True,kick_wake=True,hardware_crc=True,
                              rep_block=True,rep_unknown_transport=True,repeat=True,icu_region=True,
                              dcu_cmo=True,zero_rep_skip=True,flash_image_bytes=image),
                     irq_case(module,app,header,rom,'crc_bankb_erased_fixture',
                              entry=True,banks=True,idle_wait=True,rti=True,unmaskable=True,
                              sfc_suspend=True,gpio_out_readback=True,kick_wake=True,hardware_crc=True,
                              rep_block=True,rep_unknown_transport=True,repeat=True,icu_region=True,
                              dcu_cmo=True,zero_rep_skip=True,erased_bankb=True,insert_load_pair=True,
                              flash_image_bytes=image)]}
    for name,path,digest in [('repaired_candidate',args.repaired_candidate,REPAIRED_SHA),('archived_failed_r2',args.failed_r2,FAILED_SHA)]:
        if path is None:continue
        candidate,readonly=candidate_view(path,digest,args.firmware)
        r=irq_case(module,candidate,header,rom,name,entry=True,readonly=readonly);r['ufw_sha256']=digest
        report.setdefault('candidate_prefixes',[]).append(r)
    check(report);args.report.parent.mkdir(parents=True,exist_ok=True)
    args.report.write_text(json.dumps(report,indent=2)+'\n')


if __name__=='__main__':main()
