# H3S USB recovery tools

完整实测流程、协议、依赖、命令和验证边界见 [docs/usb-recovery.md](../../docs/usb-recovery.md)。

- `h3s_usb.py`：固定BR28 RAM loader下载、USB进入、双备份、离线计划、限定6扇区恢复。
- `usbfs.py`：root Linux/Android usbfs，固定QCY/BR28身份。
- `protocol.py`：MSC BOT、CDB与响应校验。
- `image.py`：已知r2→b79镜像/布局/CRC/VM保留约束。
- `test_recovery.py`：脱机安全失败路径测试；不打开USB。

```bash
python3 tools/usb-recovery/h3s_usb.py --help
python3 tools/usb-recovery/test_recovery.py
```

设备完整备份含私有身份、配对和校准数据，保存在忽略目录，勿提交。当前仅验证一台H3S这一故障场景；公开CLI整理后没有再次刷写，不能把它当成通用厂商全片烧录器。
