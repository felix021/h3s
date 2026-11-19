# H3S USB recovery tools

完整实测流程、协议、依赖、命令和验证边界见 [docs/usb-recovery.md](../../docs/usb-recovery.md)。

- `h3s_usb.py`：固定BR28 RAM loader下载、USB进入、双备份、离线计划、限定6扇区恢复。
- `usbfs.py`：root Linux/Android usbfs，固定QCY/BR28身份。
- `protocol.py`：MSC BOT、CDB与响应校验。
- `image.py`：已知r2→b79镜像/布局/CRC/VM保留约束。
- `test_recovery.py`：脱机安全失败路径测试；不打开USB。
- `accept_profile.py` / `accept_verify.py` / `accept_usb.py` / `test_accept_recovery.py`：
  助听真机验证计划（H5-PRE-01）的双向 profile，按候选参数化（`--candidate`）：
  hal5=b79→HAL5 刷写与 HAL5→b79 回退（原始方向，回归钉死）；f1a/f1b=HAL5→F1x
  刷写与 F1x→HAL5 同层回退（退到 b79 需再接既有 hal5 回退，即 F1x→HAL5→b79 两跳，
  无直连 F1x→b79 profile）；f2/f1ac1=F1a→F2/F1aC1 刷写与同层回退到 F1a
  （F1aC1 为单变量对照候选；再退=接既有 f1a 回退再接 hal5 回退，
  即 F2→F1a→HAL5→b79，无直连跳层 profile）。具名、摘要钉死、精确变化扇区
  （全差分推导+独立校验器复算，绝不预填 r2 六扇区）、boot-info 扇区最后提交、
  VM 保留最新、一次性 nonce（固定状态目录 O_EXCL 占用）、意图日志
  （intent→result 逐条 fsync）、全读回校验，以及中断分析器（只接受日志可解释的
  执行前缀状态，输出限定恢复方案或拒绝）。旧 r2→b79 门一律不动。
  纯离线实现+假设备故障注入测试；未在真机执行。
  F1 验收文档见 docs/hearing-aid-f1-acceptance.md；F2 见
  docs/hearing-aid-f2-acceptance.md。

```bash
python3 tools/usb-recovery/h3s_usb.py --help
python3 tools/usb-recovery/test_recovery.py
python3 tools/usb-recovery/test_accept_recovery.py
python3 tools/usb-recovery/accept_usb.py --help   # plan-flash/flash/plan-rollback/rollback/analyze/recover
```

设备完整备份含私有身份、配对和校准数据，保存在忽略目录，勿提交。当前仅验证一台H3S这一故障场景；公开CLI整理后没有再次刷写，不能把它当成通用厂商全片烧录器。
