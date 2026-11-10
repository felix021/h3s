# EQ 实验的执行范围修复：仅离线候选

2026-11-10，针对[r2失败中的执行范围遗漏](eq-toggle.md)，生成了**另一个**候选。它保留原337B编译载荷、五个钩子、ANC两态与提示音资源，仅另外修改`debug_init`的Flash PC上限立即数。独立完整性／差分审计、五个受限入口和15个实际编译函数用例通过；**完整启动验证未完成，没有刷入，不能称为已修复实机故障**。

| 项目 | 值 |
| --- | --- |
| 文件 | `H3S_0.1.1.8_tones_minus10dB_ANC_EQ_TOGGLE_FENCE_REPAIR_OFFLINE_UNTESTED.ufw` |
| 大小 | 1,326,848B |
| SHA256 | `67ba97efb83a15ec9df14de574667c6a77126d43ac6d8e5f5e645cb91126f222` |
| 新修改点 | `0x06034e98`，`R1=0x060c3338`改为`R1=0x060e31c5`，只变32位立即数 |
| 保护 | 下限、RAM范围、设置函数调用及保护使能指令保持原字节 |
| 载荷 | `0x060e3074`起337B，SHA256 `f4e9501e515a7f74e7114633ba9bc165d1da9b399481530a78be8663dbe0e49a` |
| 整包相对已验证b79 | 428字节变化，包含补丁、资源扩展和必需CRC元数据 |

原r2的SHA仍为`c8dae5…`，继续禁止刷入。当前OTA客户端的固定包门槛没有放开，原`firmware` tag／Release没有替换，候选文件只留在忽略的本机分析目录。

## 验证检查了什么

[check_eq_repair.py](../tools/check_eq_repair.py)从固定官方原包、既有编译ELF重新构建，再由不导入builder的[verify_eq_candidate.py](../tools/verify_eq_candidate.py)审计：

- UFW／JLFS／双备份明文、密文及头部CRC全部通过；旧资源、ANC表、外层资源保留。
- 修改范围限制为原五处钩子、精确PC上限立即数、追加文件及必需元数据；boot-info的reserved及其他无关字节不允许变化。
- 新策略只接受精确337B载荷摘要和五处既有钩子字节；实际候选payload／hook必须与回放ELF逐段一致，避免“审计一个包、执行另一个ELF”。
- 新工具仍检查默认builder拒绝旧布局、默认auditor不悄悄接受新范围、旧r2在修复策略下仍被拒绝。
- 两个**全部CRC有效**的负向样本：修改boot reserved或把节点名`Eq0Media`改成`Xq0Media`，独立审计都拒绝。

[probe_pi32v2.py](../tools/probe_pi32v2.py)从候选实际两条`li.u32`执行读取上下限，五个入口在该范围下进入真实补丁；另外8组source状态、3组toggle、4组node-open函数检查通过，寄存器／栈保持。该轮没有权限覆盖，但DSP／RTOS调用依然是明确测试桩，未验证真实音效链。历史r2的旧范围拒绝测试保持`REJECTED`。

公开脱敏报告：[eq-fence-repair.json](../evidence/eq-fence-repair.json)。

## 端点和连续范围的限制

`0x060e31c5`是载荷末端，属于**写给LIM_PC0_H的值**；目前没有证据完全确定芯片对上下限端点的包含关系或低位处理。探针采用半开区间并检查整条指令不跨界，这是模型策略，不是已测硅片规范。

载荷最后一条指令结束于`0x060e31b2`，后19B为字符串；仅凭奇数上界不能推断候选必然失败，也不能据此消除未知。实际RAM setter不做软件对齐或掩码。参考IR的寄存器访问`align4/8`不等于要求写入值按4／8对齐。

扩大一个连续Flash执行范围，也会把原代码末端到新载荷之间的资源区域纳入范围。此处修的是已知范围错误，不是额外的安全强化，也没有证明它是历史故障的唯一原因。

## 本机复现

先按[原实验构建记录](eq-toggle.md)生成精确编译ELF／反汇编，现有归档可直接复用，无需再次重编译：

```bash
python -m venv .venv
.venv/bin/pip install -r requirements.txt
.venv/bin/python tools/build_quiet_candidate.py \
  --attenuation-db 10 --anc-cycle-two-state \
  --eq-toggle-elf /path/to/patch.elf --eq-code-fence-repair \
  --output-dir analysis/eq-repair-new

.venv/bin/python tools/check_eq_repair.py \
  --patch-elf /path/to/patch.elf --patch-asm /path/to/patch.asm \
  --report analysis/eq-repair-check.json
```

可加`--rejected-r2 /path/to/historical-r2.ufw`运行精确旧包负向检查。检查在临时目录重建，不写耳机；默认本机检查不依赖私有归档ELF。已验证的三份基线仍通过原包／提示音中间包／ANC包精确复现。

后续[条件启动回放](clock-rtos-startup.md)已让精确修复候选和旧r2分别执行真实前缀，均到110,818步后读取SDTAP.CON的边界。实际OS初始化及分配器运行，但尚未到debug_init执行范围配置，不能据此区分两个镜像的完整启动结果。

下一步仍需端口初态、双核、调度与中断硬件语义，再让候选走完整连续启动和实际EQ链。函数用例和启动前缀通过不能替代这些工作。
