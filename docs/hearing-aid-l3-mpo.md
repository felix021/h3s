# 助听 L3「BLE 参数通道固件侧消费链」：全带 MPO 运行时热更（路 HAL3 / sim-hal3）

2026-11-17。任务：让 App 下发的助听参数（本轮=全带 MPO 曲线）经 BLE 到固件后
真正生效到 mic_effect pipeline 的 Drc1Eff——补上 r4-keyhook 留下的「配置存进去
与音频参数生效之间缺消费链」。本路产出**离线镜像 + 静态/字节层验证**，不刷机、
不连真机；候选 UNTESTED。前置：main 87e36e1（L2 MPO 曲线 + L1KEY 双击 toggle +
r4-keyhook cmdid 0x30）。

## 0. 一句话结论

codex 参谋（gpt-6-astra，`~/tmp/codex/202611172140-hal3/final.md`）三选一判：
**采纳 a（复用框架热更通道）的修正版——wdrc 76B 整包经 jlstream_set_node_param
热更，消费时机 a2（open 成功后应用）**。codex 同时纠正了主会话调研的三个关键错
读（§1），并揪出 r4-keyhook 既有回执 bug（§5）。本路落地：

1. **固件侧**：新载荷 `apply_mpo.S`（读 VM key 30 → 版本化协议校验 → L2 曲线写
   官方 Drc1Eff 76B 模板 → `jlstream_set_node_param(0xdefe,"Drc1Eff",包,76)`）
   + key7 toggle 桩扩展（open 成功后调用）；构建器开关 `--hearing-aid-l3-mpo`。
2. **App 侧**：KeyStoreFrame 8B 值协议编码（`{HA,01,00,float32 M}`）+ 助听 tab
   「保存全带 MPO」按钮接 bridge.setHearingCfg。
3. **修复**：r4-keyhook 写回执字节偏移 bug（失败也回 `30 01 00` 假成功）。
4. **验证**：载荷/镜像两层单测（10+11 断言）+ android-query 全套 + 独立校验器。

## 1. 链路核实结论（调研阶段钉死，codex 复核修正）

**全链**：App `setHearingCfg(hex16)` → BLE RCSP 自定义命令 cmdid 0x30（r4-keyhook
已接驳注册表空 setter 槽）→ keystore_hook 载荷 `set_config(30, 8B)` 存 VM →
**本路新增**：双击多功能键进辅听 → open 成功 → `apply_mpo` 读 key 30 → 构造
76B → `0x0607538a(uuid=0xdefe, name16, param, 76)` → 框架消息（事件 0x2000F）→
wdrc ioctl `0x06078d0a` 0x2000F 分支 → `rep 4 19` 整包复制 76B → 运行句柄非空时
`0x607c7c6`（memcpy 76B + `b[r4+19]=1` updata 标志，与 SDK IR
`audio_wdrc_update_parm` 逐 call 同形）→ **下一音频块按新曲线压缩**。

### 1.1 三个被 codex 纠正的错读（字节级证据，主会话已复核）

| 主会话原判 | codex 纠正（核实成立） |
|---|---|
| 0x0607898e 是 wdrc 的控制 handler，其 tbh 表 subcmd 7 = 40B wdrc 参数组 | **0x607898e 是 eq(0x737b) 的 ioctl**；wdrc(0xdefe) 的 ioctl 是 **0x06078d0a**。注册表 [0x060a8ed0,0x060a9170) 28×24B 记录：eq @0x060a8f84 `{name="eq",uuid=0x737b,bind=0x0607876a,ioctl=0x0607898e}`、wdrc @0x060a8f9c `{name="wdrc",uuid=0xdefe,bind=0x06078cee,ioctl=0x06078d0a}`（walker 0x0607452e 从记录+4 读 uuid、节点分发读+16）。**subcmd 7 是 EQ_SEG**（44B=16B 段+24B 淡入参数+priv/callback，调用形 0x60250da..0x60250f8 实证），与 wdrc 无关 |
| 0x0607538a 是「发完即走」的异步 post | **投递后同步等待**：`request(cmd=11, msg, event=0x2000F, sem=msg+24)`，0x06074192 内部 0x060741bc 投递 + 0x060741e2 等待；返回后 producer 读 msg+20 结果再 free。**禁止在流服务任务自身上下文调用（会等自己）**——本路挂在按键 dispatch 链，与固件 0x22 setter 同域 |
| wdrc 消息需要 subcommand 头（type=7 之类） | **wdrc 的 param 就是 76B tool_set 原样**（param[0]=is_bypass 字段）：0x06078d8a 读 [param+0]，bypass==0 且运行句柄非空 → 直接 0x0607c7c6 热更；否则 76B 复制到 node+28 参数缓存（下次 open 生效）。与 SDK `drc_update_parm` 的 `jlstream_set_node_param(NODE_UUID_WDRC, name, &cfg, 76)` 完全一致 |

### 1.2 通道通用性（固件内 13 处活调用，全 app 扫描）

`0x0607538a` 是全效果通用运行时热更总线：eq(0x737b, 44B 段消息 ×6 处)、
spk_eq(0x3be6, 20B/8B ×4)、volume(0x74e3, 16B ×2——VolEff 同 uuid)、0x72b3(16B)
——QCY App 日常改 EQ 走的就是它。wdrc 无现成调用者，但 handler 分支与 SDK 设计
路径都证明通道开放。

### 1.3 EQPIPE 深链墙的边界（沿用既有结论）

0x22 setter 的 apply 深链在冻结单上下文仿真撞 0x60642ec（EQPIPE 根因=OS 同步对象
+任务调度，冻结世界无真实调度）——那是**仿真方法论边界**，非固件缺陷。本路的
`0x0607538a` 同样经 0x6074192 同步等待，在真机标准调度下是常规 RTOS 操作；冻结
仿真里直调它也会撞同族墙（诚实边界 §7）。真机联调未做（红线）。

## 2. key 30 值协议（App ↔ 固件对齐）

8B = `{魔数 "HA"(0x48,0x41), 版本 1, 保留 0, float32 LE(MPO dB 位型)}`。

- **范围门**：固件按 IEEE754 位型比较 `0x3E99999A < bits < 0x42F00000`（即
  0.3 < M < 120.0 独占）——有符号位型比较同时排除负数（位型高位置 1 → 比较为
  负 → 拒）、NaN/±Inf（位型 ≥ 0x7F800000 → 拒）。App 侧同门先挡一道。
- **版本头必留**：历史 key 30 的任意测试字节（如路 AB 的 `5730dead…`）解不出
  魔数即静默跳过、保留镜像默认曲线，不会被误当音频参数。
- 未配置/格式拒/范围拒 → `apply_mpo` 返回 1（跳过），不改任何音频状态。

## 3. 固件载荷（tools/hearing-aid/apply_mpo.S，43 insns / 128B 代码 + 92B rodata）

```
apply_mpo():
    [--sp] = {rets, r6-r4}; sp += -88; r4 = sp      # 帧 = 8B key buf + 76B 包
    get_config(30, sp, 8) @0x06000f88               # r0 != 8 → 跳过（未配置）
    逐字节校验 {0x48, 0x41, 0x01, 0x00}
    r5 = [sp+4]（M 位型）; 位型双界门（§2）
    模板 76B（段内 rodata）→ 栈包（19 字循环拷贝）
    [包+16]=r5; [包+20]=r5; [包+28]=r5              # th[1].in/out、th[2].out = M
    jlstream_set_node_param(0xdefe, "Drc1Eff"16B, 包, 76) @0x0607538a
    r0 透传框架结果（0=已应用/负=消费者错误）
```

**模板 = 官方 Drc1Eff 76B**（stream.bin sha 19c9deeb §5 @0x1038 逐字节：attack=100
release=200 拐点 (0.3,0.3)/(90.3,90.3)/(110.3,110.3)/(90.3,90.3)×3 inputgain=
outputgain=0 rms_time=25.0 tnum=3 PEAK/TWOPOINT），**唯一差异 th[2].in(+24)
110.3f→120.0f**。曲线 = (0.3,0.3)/(M,M)/(120,M)：段0 单位增益到 M，段1 斜率 0、
截距 M——任意过驱动输入映射 M（L2 §1 语义）。th[2].in 固定 120.0f 而非 L2 的
M+10.3：**末段斜率 0 下 in 值不参与输出**，只需严格递增（M < 120 由范围门保证），
载荷因此零浮点运算。构建器与单测双重断言模板字节（对官方 stream.bin 交叉）。

**接驳**（tools/hearing-aid/mic_effect_toggle_mpo_hook.S，15 insns / 46B）：
key7 toggle 桩（L1KEY 同源）open 分支扩两条——`if (r0 != 0) goto done`（open 失败
不应用，节点未起消息无消费者）+ `call apply_mpo`。段布局：open(156B) + close(64B)
+ apply_mpo(128B) + 模板(76B) + 名字(16B) + 桩(46B) + keyhook(78B) = 564B；
MPO_*_ADDR .equ 两遍定址（占位与实值同宽 li.u32，布局跨趟稳定——与 MIC_EFX_SLOT
同契约）；fence 扩到段末 0x060E32A8。

## 4. 构建与验证

- 开关 `--hearing-aid-l3-mpo`（隐含 --r4-keyhook 全链；与 --hearing-aid-l1-key7
  互斥——同一 KEY7 接驳窗 0x06025404）。规范构建 sha256
  `88483a67ce4351bfe15aeeef4a34bcfd13a11d2cba7ef2ed07a742c69cdbe182`，
  与官方逐字节差异 630B（keyhook 候选 554 + apply_mpo/桩/rodata 增量 + CRC 链）。
  独立校验器 verify_ufw_integrity.py 全过。
- 单测 `test_hearing_aid_l3_mpo_payload.py`（10）：往返零 diff、API 锚点恰一次
  装载+call 配对、常量集、协议门全部先于首个存储、三个拐点 store 精确形状、
  模板对官方字节（含活 stream.bin 交叉）、16B 名字域、.equ 双值布局不敏感、
  栈帧平衡、帧内 key/包布局。
- 单测 `test_hearing_aid_l3_mpo_image.py`（11）：段几何、open/close 前缀==独立
  汇编、decode 三旗、桩调用序 [open→apply_mpo→close]、keyhook 回执修复
  （§5）、段内模板/名字、开关误用拒绝、legacy 四变体零回归、候选构建 + 差异
  计数区间 + 注册表/fence 窗口。
- App：KeyStoreFrameTest 增 mpoCodec（编码字节形状 `48410100 0000c842`、位型门
  六拒、非协议值不误读、协议头破损拒、协议值可进普通写通道）；test.sh 8 套全过。

## 5. r4-keyhook 回执 bug 修复（codex 揪出，既有候选的勘误）

keystore_hook.S 写分支原 ` [r4+0] = r5`（word store）——status 实际落在 resp[0]，
resp[2] 恒 0：**set_config 失败也回 `30 01 00`，App 解析成假成功**（分发器
0x0600f94a/0x0600f94e 只覆写 b[resp+0]/b[resp+1]）。修复为 `b[r4+2] = r5` 字节
直写；test_r4_keyhook_payload 的错误断言（要求写 resp+0）同步翻转。旧候选
（sha 8e9a47d0…）的字节就此作废，r4-keyhook 家族字节以本路为准。

## 6. 消费时机的边界（codex a2 分析，如实收口）

- **本轮语义 =「已保存，下次开启辅听时应用」**：App 写 key 30 即时生效不做
  （需把应用请求串行化到管理 open/close 的同一任务并核实其可阻塞且非流服务
  任务，下一阶段）。
- open 成功 ≠ 首个音频块已用新曲线（start 返回与第一块消费之间的窗口未证），
  a2 可能短暂用镜像默认曲线。
- close 进行中槽仍非零的竞态窗口对 a2 无影响（应用只挂 open 路径）。
- 框架不为下次 open 缓存失败请求：应用失败（节点查找 -22 等）不重试，key 30
  的值仍在，下次 open 再试。

## 7. 诚实边界（必须随候选一起读）

1. **离线候选 UNTESTED，不刷机**。交付=载荷字节与 §1 证据一致 + 四层 CRC/
   JLFS 结构自洽 + 逐字节差异精确可控 + 单测/独立校验全绿。
2. **消费链零执行证据**：apply_mpo → 0x0607538a → 框架队列 → 0x06078d0a →
   0x0607c7c6 → 音频块重算的完整路径未在任何仿真/真机跑过（主仿真前沿未到
   RCSP/任务派发相位；0x0607538a 的同步等待在冻结单上下文会撞 EQPIPE 同族墙）。
3. **逐频增益（Eq1Eff 十段）热更未实现**：通道证据已钉（EQ_SEG subcmd 7 的
   44B 段消息 + 0x22 应用循环先例），但 8B key 30 放不下十段——留下一阶段
   （版本化 EQ 配置 + 多帧/大帧协议）。本路只声明「全带 MPO 运行时热更链落地」。
4. M 是调音工具数字域 dB 刻度（原值 110.3 的同一标尺），非 dB SPL；attack=100/
   TWOPOINT 平滑下稳态上限成立≠瞬时峰值不越限（L2 §6 口径不变）。
5. cmdid 0x30 占用残余风险沿用 r4-command-hook §5.4（QCY App 未来若启用 0x30
   写会冲突）。
6. key 30 的 VM 记录跨重启仍在（flash 持久）——重启后首次开辅听即应用；如需
   「恢复出厂曲线」删 key 30 的通道未做（get_config 读回魔数不符即默认曲线，
   行为安全）。

## 8. 复跑命令

```sh
python3 tools/build_quiet_candidate.py --attenuation-db 0 --hearing-aid-l3-mpo \
    --output-dir <不存在的目录>
python3 tools/verify_ufw_integrity.py <产物>/H3S_0.1.1.8_tones_minus0dB_HEARING_AID_L3_MPO_OFFLINE_UNTESTED.ufw
python3 tools/test_hearing_aid_l3_mpo_payload.py
python3 tools/test_hearing_aid_l3_mpo_image.py
bash tools/android-query/test.sh
python3 tools/check_local.py --units
```

## 9. 证据锚点

- codex 参谋全文：`~/tmp/codex/202611172140-hal3/final.md`（不入库）；prompt 同
  目录 prompt.txt，log.md。
- 框架链：注册表 eq@0x060a8f84 / wdrc@0x060a8f9c（walker 0x0607452e +16 读 ioctl）；
  set_node_param 0x0607538a（zalloc 1068 / msg+2 uuid / +4..20 name / +44 param /
  len<1025 post / 同步等待 0x06074192→0x060741e2）；wdrc ioctl 0x06078d0a 的
  0x2000F 分支（0x06078d8a is_bypass、0x06078d92 rep 4 19 整包、0x06078e1a 句柄、
  0x0607c7c6 memcpy76+updata）；调用者 13 处清单（§1.2）。
- 载荷：tools/hearing-aid/apply_mpo.S + mic_effect_toggle_mpo_hook.S；单测
  tools/test_hearing_aid_l3_mpo_payload.py / test_hearing_aid_l3_mpo_image.py；
  构建 tools/hearing_aid_l1_image.py（MPO_* 常量与 _verify_mpo/_verify_mpo_toggle_stub）
  + tools/build_quiet_candidate.py（--hearing-aid-l3-mpo）。
- App：KeyStoreFrame.encodeMpoValue/decodeMpoValue + tests/KeyStoreFrameTest.mpoCodec
  + assets/index.html（haEncodeMpoValue / haSaveMpo / haChanged 文案）。
- 既有文档勘误：docs/hearing-aid-eq-feasibility.md §1.1 表 wdrc 行 op1 0x607898e
  → **0x06078d0a**；docs/eq-payload-semantics.md §2.3 'wdrc' 记录 ops 三元组
  {0x607876a,0x607898e,0x6078bb0} 实为 **eq 记录**的 ops（wdrc ops=
  {0x06078cee,0x06078d0a,0x06078e32}）；eq-toggle.md「EQ 节点控制处理函数
  0x0607898e」表述成立（0x737b 消息路由到 0x607898e，eq_toggle 实测），但它是
  ops 表 eq 记录的 ioctl，非 wdrc。
