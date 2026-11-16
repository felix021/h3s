# 快照续跑：验证轮的断点起跑入口（路 SNAPX/sim-snapx，2026-11-15）

## 解决什么问题

前沿推进后，同一个新停点要被**全量重放 N 遍**：parity 三臂（official/tested_b79/候选）、
`--rom` 条件场景、断言重钉迭代——历史峰值是路 LPV 烧 4 轮 × 35 分钟。重放的
前缀（0 → 停点）在这些轮次之间**逐字节不变**，重复计算是纯浪费。快照系统
（[snapshot-design.md](snapshot-design.md)：BD 设计 / BJ 框架 / BL M4 适配器 / BN M1 /
SNAP 接线+等价性）已经能把停点处的整个世界落盘并恢复；本路把它接进
`tools/analyze_image_trace.py` 的执行层，让验证轮用 restore 起跑替代从 0 重放。

成本对照（21.8M 条件前沿，单臂全量 ≈16 分钟；实测：crc_bankb 双镜像捕获轮
29.6s→验证轮 **2.6s**；条件三镜像捕获轮 68m33s（含背景负载）→验证轮（--jobs 3
并行 restore）**2.2s**）：

| 轮次 | 无快照 | 有快照 |
| --- | --- | --- |
| 捕获轮（一代码态一次） | — | 每镜像一次全量 + 捕获（~16 分钟/镜像，`--jobs 3` 可三臂并行 ≈22 分钟） |
| 验证轮（parity/停点复核） | 每臂 ~16 分钟 | 三臂合计 **~2s**（restore+重试停点指令） |

## 用法

### 1. 捕获轮（前沿推进后做一次）

```bash
python3 tools/analyze_image_trace.py --rom <ROM> \
    --scenario p11_heap_experimental \
    --image official=... --image tested_b79=... \
    --capture-snapshots analysis/snapshots \
    --report analysis/local-checks/capture-round.json
```

每个镜像照常全量重放到自然停点（freshness 钉子照常把关），并在停点边界把该镜像的
世界（CPU×2/内存/外设/机器/账本全量，M1–M7）捕获到
`analysis/snapshots/<scenario>-<label>.bin`。镜像世界从 step 155,390 起分歧，所以
**每个镜像一份快照**，互不可替换（restore 侧 expected_context 按 run 名拒错配）。

### 2. 验证轮（重复 N 次的部分）

```bash
python3 tools/analyze_image_trace.py --rom <ROM> \
    --scenario p11_heap_experimental \
    --image official=... --image tested_b79=... \
    --resume-from analysis/snapshots \
    --report analysis/local-checks/verify-round.json
```

每臂从自己的快照 restore 起跑：restore 世界重现捕获停点，主循环从停点指令重新
decode（B-1：预算停点不补调 advance）。前沿停点快照（世界停在 unmapped Blocked）下，
第一次重试即重现同一停点——停点 parity、final report 差异、每核步数 parity、
freshness 钉子全部照常核验，报告顶层多一个 `snapshot_round` 溯源键。

`--resume-from` 要求目录里有**每一个** `--image` 标签的快照（全或无，拒混合轮）；
与 `--self-check`/`--candidate-self-check` 互斥（原因见下）。resumed 轮核验通过的
面（与全量重放完全同值，crc_bankb 双镜像实测逐字节对照）：freshness 停点钉子、
每对的 `runs`/`parity`（步数/每核/完整停点记录）、`final_report_diffs`（镜像差异
面）；resumed 报告里 `trace_lengths`/`first_divergence` 等轨迹面字段只反映
restore 之后的步（前沿停点快照=0 步），报告顶层 `snapshot_round` 键即轮次溯源，
每个 pair 对象另带 `resumed: true` 就近标记（全量重放/捕获轮不含该键）——读到
轨迹面字段为 0/None 时先看该标记，属 resume 轮预期而非异常。

## 适用边界（判据，先读这个再用）

1. **前提：捕获轮到验证轮之间，前缀行为零变化。** 这不是靠自觉——机械保证：
   - 快照双闸在 restore 前重算代码指纹（probe_boot/br28_model/br28_machine/
     br28_icfg/…/world_snapshot 源哈希 + 适配器形状），任何仿真源或适配器改动
     → 拒载并提示重新捕获（重新捕获=一次全量重放，恰好就是「行为变化类改动必须
     全量重放」的执行点）；
   - 场景开关由恢复侧静态守卫逐对象比对（M3/M4 每对象构造开关、M5/M7 静态面），
     换 flag 集直接拒；
   - 捕获上下文闸（scenario 名）拒错镜像/错场景的快照。
   **判据一句话：合并了任何改模型行为/改场景开关的路 → 必须重新捕获（=全量重放）；
   纯文档/工具/测试改动不动指纹文件 → 快照继续可用。** frontier_registry 的
   前沿钉子（freshness）在验证轮照常生效，旧前沿快照会因停点不符被拒。
2. **验证轮核验的是停点面，不是逐步签名面。** resumed 轨迹只含 restore 之后的步
   （前沿停点快照 ≈1 步），所以 per-step 比较只覆盖后缀；已钉死的全前缀轨迹事实
   （首个分歧 step 155,390、reconvergence 形状等）**不在 resumed 轮里重新执行**——
   它们由上一轮全量重放钉死、由指纹闸保证仍适用。需要重新验证轨迹面（例如怀疑
   前缀行为有变）→ 跑全量。这也是 `--self-check`/`--candidate-self-check`
   拒绝 `--resume-from` 的原因：那些断言钉的是全前缀逐步事实。
3. **快照文件含 RAM 镜像，红线不进 git**（放 `analysis/`（gitignored）或本地目录；
   仓库里只进摘要行，见 `evidence/snapshot-artifact.json` 先例）。

## 等价性证据（21.8M 时代重验，本路验收）

方法同 SNAP 先例（capture 于停点 → restore+advance 三档预算 vs 全量重放到同停点，
报告逐字节相等；另加 0 步自反检查）。当前代码态的验收记录见
`evidence/snapshot-artifact.json` 的 canonical 行（21,800,455 @ 0x606e044，
JL_ASS+4 读停点）与 N=21,700,455 预算停点（实际完成 21,696,721——idle 等待步耗
预算不计数）三档行；测试入口
`tools/test_world_snapshot.py` ArtifactTierFrontierWorld（env 门控 `H3S_SNAPSHOT_ROM`，
`H3S_SNAPSHOT_CAPTURE_N`/`H3S_SNAPSHOT_WORK` 可调）。

## 本路适配器补齐清单（19,139,239 canonical 之后新增的模型状态）

用 missing-state guard 自查（低预算捕获让守卫显形拒载，逐项补齐后捕获过闸）：

| 新增状态（路） | M4 槽位 | 状态字段 | 静态 fixture |
| --- | --- | --- | --- |
| JL_AUDIO DAC_TM0/TM1 半字容器（DT） | `jl_audio_dac_tm` | stores/word_writes/reads_after_write/synthesized_initial_reads | tm0_probe_initial |
| JL_ADDA ADA 九字容器（ADA） | `jl_adda_ada` | 同上四字段 | ada_probe_initial |
| P11_LPVAD VAD_ACON0 单字容器（LPV） | `p11_lpvad` | 同上四字段 | — |
| JL_AUDIO ADC DMA 五字 write-only（JLA） | `jl_audio_adc_dma` | stores/word_writes | — |
| ADC 启动配置块四字合成读回（ASS） | `jl_adc_start` | 同 DT 四字段 | probe_initial（MIC_CTL0 探针） |
| P2M 邮箱 ack 请求-响应投影（B2G 提层） | `p2m_mailbox` | pnd_armed/echo_armed/kicks/other_kick_values/clr_writes/clear_slot_writes/pnd_reads/echo_reads/message_mirror/stored_slot/transactions/current（12 字段，无构造 fixture） | — |

机器侧（M5，路 M3FIX）：`cfg523_installed_at`（B2G cfg523 解析器入口一次性
安装位，运行期 None→{'pc','object_bytes'}）按**状态**捕获恢复——误落 static
段时 29,215,313 canonical resume 轮显形拒载（捕获时 dict vs 重建时 None）；
构造侧 fixture `cfg523_object_bytes` 仍在 static 段双向把关。

另：`analyze_image_trace.TraceMachine` 的观察层属性（O6 签名链 spool `trace` 与
`pending_writes`）是本 comparator run 的记录、非仿真世界状态，M5 按 observation-skip
跳过（恢复世界起新 spool；其它未登记机器属性仍拒载）。O3/OPT1 的 memo 缓存在
decode 缓存对象上（M1 §M9 跳过域）/闭包局部（M2 守卫绕开），无需适配器改动。
