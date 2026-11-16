# M3 刷机流实施规格（SIMB3T）：FB01/FB04 擦写 + armed 门控 + 全环 SHA 验收

2026-11-17 01:20-。基线 worktree `c0f3140`（分支 sim-simb3t）。本文是 [sim-bridge-design.md](sim-bridge-design.md)
§5.3（M3 刷机流）的**实施级展开**，照 [sim-m1-direct-spec.md](sim-m1-direct-spec.md)（SIMB1T）的模式写：
每条给**接口 + 代码块形状 + 验收断言 + 风险**，目标=实施路「拿到即可写代码」。纯文档路：零 tools/
改动、probe 五文件零触碰、HANDOFF 不动（条目草稿随报告交主协调）。

合成关系（三份已齐输入）：

| 来源 | 本文消费的部分 |
| --- | --- |
| [sim-bridge-design.md](sim-bridge-design.md) | §4.4 USB 设备端/BOT 契约/进程级状态纪律；§4.6 协议级诚实边界；§5.3 M3 改动面（codex 展开版）与验收四条；R13 保真边界 |
| SIMB2A 落地（tools/sim_bridge_server.py + SimulationUsbLink.java + test_sim_usb_device.py） | MscBotDevice 相位机/身份机/CBW 流重组/CRC16-XMODEM；M2 只读红线现状（**M3 要解除的唯一一道门**）；测试先例（BotHost 红绿双向） |
| tools/usb-recovery（h3s_usb.py + image.py + protocol.py + docs/usb-recovery.md） | **FB01/FB04 字节级唯一实证**（2026-11-10 实机恢复六扇区全流程）；恢复业务守卫形状（active_sector/512 对齐/CRC/保留范围） |

证据强度沿用总设计：**[实测]**=实机过线（usb-recovery.md 2026-11-10 恢复）；**[静态]**=反汇编钉死（引文档）；
**[文档]**=既有 docs/HANDOFF 条目；**[设计]**=本文新设计决策；**[未实证]**=仓内无证据，如实标注不发明。

## 0. 一句话结论

M3 = 在 M2 的 `MscBotDevice` 上加**两道门内的两个 opcode**：FB01（擦 4KiB 扇区）/FB04（512B 编程）
字节级照实机恢复工具钉死形状（[实测]），门=①loader-ready 相位（与 FD05 同门）②**armed 状态**
（`usb_arm` 消息进入：一次性 nonce + 扇区计划 + 目标 SHA；进入瞬间 fixture **copy-on-armed**——原始
2MiB 资产 bytes 永不写，写全部落在进程内副本上）。app 侧**远非零改动**：UsbScsi 白名单按「写能力位」
扩展 FB01/FB04 编码入口、H3sUsbBridge 新建恢复业务本体（计划/armed/执行器/进度/中止/范围守卫，
host 侧 h3s_usb.py:86-101 RestoreTransport 同款守卫形状）、Link 接口加两个 default 方法
（armFlash/rebootDevice——真机 Link 不覆写即不支持，诚实失败）。「重启换身份」=相位机扩
`usb_reboot`：identity 回 normal + loader 丢弃 + armed 作废，fixture 副本内容保持——新固件可见性
**限定在 USB 面内断言**（再进 ROM→FD05 全片读==预期镜像），跨面（BLE 直调世界用新固件起世界）不在
M3 范围。验收=全环 SHA 链（S0 原始→擦写后 S_target→reboot 后仍 S_target→磁盘 S0 全程不变）+
中断负例（半擦断链：状态可见/原始完好/可重 arm 重刷）+误擦保护（未 armed 写=拒绝+记录，行为与
M2 逐字节一致）。

## 1. 数据流总览：app 真实刷机路径打到仿真耳机的命令序列

全环 11 步（①-⑦=M2 已落地零改动复用；⑧-⑪=M3 新增）：

```
app（AVD，WebShell USB tab → h3susb JS 桥）
 ① scan → normal 3654:4a55 → open → recoveryCapability(EP0 len8 → 8×ff)
 ② enterRomRequest(EP0 len0) → server 翻身份+断连（重枚举） → rescan → rom 4c4a:3442 → open
 ③ INQUIRY（36B，BR28/UBOOT1.00）
 ④ FB06×46（loader 23552B，CRC16-XMODEM/块）→ FB08（0x120000/1）→ FC0A（fc0a03=SPI NOR）→ loader-ready
 ⑤ FD05 全片双读备份（2×4096 次 512B，双读一致落盘）——计划输入=备份副本
 ⑥ 宿主离线工具产出「目标镜像 + 扇区计划」（boot-info CRC/长度在此层更新，§3.4）
 ⑦ planRestore（app 侧 armed 链：nonce 单次确认，UpgradeGate 先例）→ Link.armFlash(planJson)
    → server {"t":"usb_arm"} → copy-on-armed → armed                                          [M3]
 ⑧ 每计划扇区：FD05 核验(==副本基线) → FB01 擦 → FD05×8 读==ff → FB04×8 写 → FD05×8 比对   [M3]
 ⑨ 全片 FD05 复读 SHA==S_target（预期镜像由独立脚本离线算，server 不生成期望）              [M3]
 ⑩ Link.rebootDevice() → server {"t":"usb_reboot"} → 断连重枚举 → rescan=normal              [M3]
 ⑪ （验证环）再走 ①-④ 进 ROM 装载 loader → FD05 全片读 SHA 仍==S_target                     [M3]
```

读路径后端切换：FD05 在未 armed 时读原始 `flash`（M2 行为），armed 后读副本 `flash_work`——擦写中途
读回即见 ff/新数据（实机同款：每擦立读 ff、每写读回比对，usb-recovery.md:106 / h3s_usb.py:168/173）。

## 2. 协议面：FB01/FB04 字节级语义（实证钉死）

### 2.1 共同外壳（M2 已实现，零新增）

- CBW/CSW：31B CBW（`USBC`|tag|size|flags|lun=0|cdb_len|cdb[16]）→ 数据相位 → 13B CSW
  （`USBS`|tag|residue=0|status）。厂商 CDB = **大端 2B opcode + 参数 + 0xff 补齐 16B；地址/长度
  大端；数据 CRC16 小端**，算法 `binascii.crc_hqx(data,0)`（CRC-16/XMODEM）[实测：
  usb-recovery.md:66；protocol.py:39-45；UsbScsi.java:73-84 同形]。
- BOT 流重组/签名重同步/tag 校验/数据相位与 CSW 各自独立精确长度：MscBotDevice 现有
  `feed_out/_take_cbw/_execute/_csw` 全部复用（sim_bridge_server.py:275-354）。

### 2.2 FB01 擦除（4KiB 扇区）

| 项 | 规格 | 出处 |
| --- | --- | --- |
| CDB | `fb 01 \| addr4(BE) \| ff×10`（args=4B） | [实测] h3s_usb.py:167 `t.cmd(0xFB01,address.to_bytes(4,'big'))`；cmd() ljust 0xff（protocol.py:40） |
| 数据相位 | 无（OUT/IN 均无；CBW size=16 是**响应**长度） | [实测] h3s_usb.py:167 data=None → cmd 默认 size=16（protocol.py:39） |
| 响应 | 16B IN，前 2B 回显 `fb 01`；**尾段 14B 无实证**（协议只校验回显双字节） | [实测] protocol.py:42-44 回显校验规则（FD05/FD07 例外表不含 FB01）；对照 FB08 同状（sim_bridge_server.py:406-408 已标「尾段未实测钉死，0 填充」） |
| 字节语义 | 目标 4KiB 扇区置 `ff`（NOR 擦除态） | [实测] usb-recovery.md:74「擦除单个4KiB扇区」+:99「NOR Flash 按4KiB扇区擦除」+ :106「每次擦除都立即读回全 ff」 |
| 地址约束 | 4KiB 对齐 + 2MiB 内 | [实测·间接] h3s_usb.py:93 `require(address == self.active_sector)`，active_sector∈ORDER（image.py:14，全部 4KiB 对齐）；非对齐地址真机行为**[未实证]**——server 侧按对齐约束拒绝（§2.4 错误路径） |
| 擦除耗时 | 真机 NOR 有擦写延时；server 即时应答 | [设计] 协议级仿真不含时序（设计 §4.6/R13 同款边界），不影响字节正确性 |

### 2.3 FB04 编程（512B 块写）

| 项 | 规格 | 出处 |
| --- | --- | --- |
| CDB | `fb 04 \| addr4(BE) \| len2(BE) \| 00 \| crc16(LE)`（args=11B，与 FB06 同构） | [实测] h3s_usb.py:171-172 `cargs = p.to_bytes(4,'big')+(512).to_bytes(2,'big')+b'\0'+crc(data).to_bytes(2,'little')`；usb-recovery.md:75 |
| 数据相位 | OUT，len 字节精确（实战恒 512） | [实测] h3s_usb.py:97/99 `len(data)==512`；UsbScsi.ramWrite 同形先例（UsbScsi.java:162-169） |
| 长度约束 | len==len(data)==512；addr 512 对齐 | [实测] h3s_usb.py:95-97（`address%512==0`、`len(data)==512`）；len≠512 真机行为**[未实证]**——server 只接受 512，其余拒绝并记录 |
| CRC | `crc16(chunk)`（XMODEM，LE 放 CDB[9:11]）须符 | [实测] h3s_usb.py:99-100 |
| 响应 | 无数据相位响应（OUT 后直接 CSW） | [实测] protocol.py:17-37 exchange：data≠None 时 result=b''、无 IN |
| 字节语义 | `flash_work[addr:addr+512] = data`（编程后读回==写入值） | [实测·间接] h3s_usb.py:173 擦后写读回比对通过；NOR 编程位语义（未擦先写的行为）**[未实证]**——server 按「先擦后写」纪律用计划门约束（§3.2），不模拟位冲突 |
| 响应回显校验 | app 侧 exchange 的 16B 回显校验不触发（FB04 无 16B IN） | [文档] UsbScsi.java:138 条件 `data==null && size==16` 不满足 |

### 2.4 错误路径（server 侧统一 `_Reject` → CSW status=1 + 记录）

M2 的 `_Reject` 通道（sim_bridge_server.py:184-185/343-348）直接复用。FB01/FB04 的拒绝条件
（任一即拒，`rejected` 记录 opcode+reason）：

1. 身份/相位门：非 rom 身份或 phase≠loader-ready（与 FD05 同门，sim_bridge_server.py:424-426）。
2. **armed 门：未 armed → 一律拒绝**（消息=`FB01/FB04 需要 armed（M2 只读红线：未确认的刷机会话
   不开写路径）`）——**未 armed 时 M3 行为与 M2 逐字节一致**（M2 把 FB01/FB04 当白名单外 opcode
   拒绝；M3 拒绝理由换成 armed 门，CSW 形状相同，M2 回归测试仍绿）。
3. 计划门：addr 不在 armed 计划的扇区集合内（FB01 须 addr==某计划扇区基址；FB04 须
   addr//4096*4096 ∈ 计划扇区）。
4. 形状门：FB01 非 4KiB 对齐/越界；FB04 非 512 对齐/len≠512/CRC 不符/数据相位长度不齐。

真机被拒时的 CSW 形状（status/residue/stall 时序）**[未实证]**（实机工具从未被拒）——沿用 M2 的
status=1 合成应答（协议级标注，R13）。app 侧 UsbScsi.exchange 对 CSW status≠0 抛
IllegalStateException（UsbScsi.java:133-136）=可见失败，不静默。

### 2.5 未实证清单（本规格不发明，汇总）

| # | 项 | 处置 |
| --- | --- | --- |
| U1 | FB01 响应 16B 的尾段 14B 内容 | server 0 填充+回显 opcode；app 只校验前 2B（双向都只依赖已实证面） |
| U2 | FB01 非对齐地址、FB04 len≠512 的真机行为 | server 保守拒绝+记录；如需钉死列真机抓包任务单（§6） |
| U3 | FB04 未擦先写的 NOR 位冲突行为 | server 不模拟（计划门+「先擦后写」纪律规避）；验收不含此负例 |
| U4 | 拒绝路径的 CSW/stall 时序细节 | status=1 合成（M2 先例）；R13 边界 |
| U5 | 「重启」的协议 opcode（FB00/FB02 疑似复位/跳转族，read_rom.py:65 禁止清单有名，语义无钉） | **不发明 opcode**：reboot 走控制面消息 `usb_reboot`（§3.3） |
| U6 | bank 启动选择逻辑（boot-info 序号 2/3 谁启动，firmware-layout.md:100 只记观测值） | M3 不模拟 bank 切换的固件侧行为；目标镜像保持序号不变（同实机恢复纪律 usb-recovery.md:106「没有切换序号」） |

## 3. server 侧设计：MscBotDevice 扩展（tools/sim_bridge_server.py，实施路改）

### 3.1 armed 状态机与 copy-on-armed

```python
class MscBotDevice:
    # M3 新增构造参数与状态（其余不动）：
    def __init__(self, ..., plan=None):          # plan=None → 未 armed（M2 行为）
        self.flash_original = flash              # bytes：原始 2MiB 资产，永不写（M2 的 self.flash 语义改名保留）
        self.flash_work = None                   # bytearray | None：armed 副本（copy-on-armed）
        self.plan = None                         # {'sectors':[int], 'target_sha256':str, 'nonce':str(已作废)}
        self.journal = []                        # [{'op':'erase'|'program'|'reboot', ...}]（验收证据）
        self.erased = set()                      # 已擦扇区基址集合（FB04 计划门的「已擦」判定）
```

- **armed 进入**（唯一入口 `arm(plan)`，两个触发面）：
  1. 控制面消息 `{"t":"usb_arm","sectors":[…],"target_sha256":"…","nonce":"…"}`（app 侧进入刷机
     模式的动作经 Link.armFlash 发出，§4.3）；
  2. CLI `--arm-plan <json>`（宿主测试/无 app 冒烟——内容同构）。
  进入动作=**一次性原子三步**：校验（sectors 非空、全 4KiB 对齐、在 2MiB 内、互不重复；nonce 非空）
  → `flash_work = bytearray(flash_original)`（copy-on-armed：此后 FD05/FB01/FB04 全部走副本）→
  nonce 立即作废（`plan.nonce = None`，回执 ok）。重复 usb_arm（无论同异 nonce）在 armed 期间=拒绝
  （`busy` 语义：先 disarm）。
- **armed 退出**（三条路径，退出后 `flash_work=None`、`plan=None`、`erased` 清空）：
  1. `usb_reboot`（§3.3，正常收尾）；
  2. `usb_disarm`（app 侧中止/错误路径主动收兵——丢弃副本回到原始，等于「本次刷机作废」；
     半擦状态不保留，见 §3.5 取舍）；
  3. server 进程退出（进程级状态语义同 M2 身份机：退出即复位）。
- **断链不退出 armed**：socket 断开只结束会话，armed 与副本保留（进程级）——重连后 describe 可见
  半擦状态、可继续 FB04 或 disarm（真机对照：擦了就是擦了，链路断了芯片上已经 ff）。验收负例 N2
  消费此语义。
- nonce 语义 [设计，照 UpgradeGate.java:33-47 先例]：单次有效、armed 即作废、server 重启后 armed
  必然清零（进程级）→「进程重启后 nonce 不得复用」由构造保证（重启后根本没有 armed 态）。

### 3.2 FB01/FB04 handler（_execute 白名单扩两项）

```python
handler = {0xfb06: self._ram_write, 0xfb08: self._loader_start,
           0xfc0a: self._online_query, 0xfd05: self._flash_read,
           0xfd07: self._mem_read,
           0xfb01: self._sector_erase,            # M3
           0xfb04: self._sector_program}.get(opcode)   # M3（armed 门内才注册——见下）

def _sector_erase(self, w):                        # FB01：门链 §2.4-1..4 → 副本置 ff
    self._rom_gate('FB01')                         # _rom_gate=M2 现有（:356-359）；相位门照 _flash_read
    if self.phase != 'loader-ready':               # 的内联形状（:424-426），非新抽象
        raise _Reject('FB01 仅 loader-ready 相位（当前 %s）' % self.phase)
    if self.plan is None:
        raise _Reject('FB01 需要 armed（未确认的刷机会话不开写路径）')
    address = int.from_bytes(w['cdb'][2:6], 'big')
    if address % 0x1000 != 0 or not 0 <= address < USB_FLASH_SIZE:
        raise _Reject('FB01 地址须 4KiB 对齐且在 2MiB 内: 0x%x' % address)
    if address not in self.plan['sectors']:
        raise _Reject('FB01 计划外扇区 0x%x（计划=%s）' % (address, self.plan['sectors']))
    if w['size'] != 16 or w['data_out']:
        raise _Reject('FB01 须 16B IN（got %d）' % w['size'])
    self.flash_work[address:address + 0x1000] = b'\xff' * 0x1000
    self.erased.add(address)
    self.journal.append({'op': 'erase', 'sector': address})
    r = bytearray(16); r[0], r[1] = 0xfb, 0x01     # 回显 opcode；尾段 0 填充 [未实证 U1]
    return [bytes(r), self._csw(w['tag'])]

def _sector_program(self, w):                      # FB04：形状门 → 计划+已擦门 → 副本赋值
    self._rom_gate('FB04')
    if self.phase != 'loader-ready':
        raise _Reject('FB04 仅 loader-ready 相位（当前 %s）' % self.phase)
    if self.plan is None:
        raise _Reject('FB04 需要 armed（未确认的刷机会话不开写路径）')
    address = int.from_bytes(w['cdb'][2:6], 'big')
    length = int.from_bytes(w['cdb'][6:8], 'big')
    cdb, data = w['cdb'], w['buf']
    if not w['data_out'] or length != len(data) or length != USB_SECTOR:
        raise _Reject('FB04 须 512B OUT（got %d/%d）' % (length, len(data)))
    if address % USB_SECTOR != 0:
        raise _Reject('FB04 地址须 512 对齐: 0x%x' % address)
    sector = address // 0x1000 * 0x1000
    if sector not in self.plan['sectors']:
        raise _Reject('FB04 计划外扇区 0x%x' % sector)
    if sector not in self.erased:
        raise _Reject('FB04 目标扇区未擦除（先 FB01）: 0x%x' % sector)   # 宿主工具同款纪律（h3s_usb.py 擦→写顺序）
    if cdb[8] != 0 or int.from_bytes(cdb[9:11], 'little') != binascii.crc_hqx(data, 0):
        raise _Reject('FB04 CRC16-XMODEM 不符')
    self.flash_work[address:address + length] = data
    self.journal.append({'op': 'program', 'addr': address, 'sha256': hashlib.sha256(data).hexdigest()})
    return [self._csw(w['tag'])]                   # 无 IN 响应（§2.3）
```

`_flash_read` 一行改动：后端 `self.flash if self.flash_work is None else self.flash_work`（§1 读路径
切换）。`describe()` 增 `armed`/`plan_sectors`/`journal_len`/`work_sha256`（副本当前 SHA——验收
断言与「中途状态可见」用）。

**实现取舍注**：白名单表扩在 `handler` dict 里、armed 判定放 handler 体内（而非按 armed 动态增删
dict 项）——后者会让「未 armed 时 FB01 的拒绝消息」变成「opcode 不存在」，与 M2 的记录形状分叉；
前者保持 `counts['fb01']`/`rejected` 流水连续（M2 回归测试对 rejected 记录形状有断言）。

### 3.3 重启换身份：usb_reboot 与相位机扩展

```python
# 身份机（M2: normal ⇄ rom，进程级）扩一支：
#   rom + loader-ready/started + (armed|not) → usb_reboot → normal（phase='n/a'，loader 丢弃，armed 作废）
def reboot(self):
    with self.lock:
        if self.identity != 'rom':
            return '已是 normal 身份'
        self.identity = 'normal'; self.phase = 'n/a'
        self.loaded = 0; self.loader = bytearray(USB_LOADER_SIZE)
        self.plan = None; self.flash_work = None; self.erased = set()   # armed 作废（§3.1）
        self.journal.append({'op': 'reboot'})
        return None
```

- **真机对照依据**：①loader 不无限留驻、离开工具后设备曾自行回应用模式（usb-recovery.md:79）；
  ②实机恢复的收尾=拔 USB **手动重启**后再验收（usb-recovery.md:145「手动重启耳机一次」列为明确
  收尾步骤）。仿真把「重启」做成显式动作（app 侧 rebootDevice 触发），语义=设备整机重启。
- **断连重枚举**：与会话层 `usb_ctl len0` 同款（sim_bridge_server.py:584-586 先例）——reboot 成功
  后 server 断开连接，app rescan 见 normal 身份（SimulationUsbLink 的 EOF→onDetached 模拟拔出
  UX 已就绪，SimulationUsbLink.java:331-341）。
- **新固件可见性的边界（如实）**：reboot 后 normal 身份无 MSC 面（M2 `_rom_gate` 语义：
  normal=普通 QCY 复合设备），「新固件已启动」在 M3 只能以 **USB 面内容断言**代理（⑪ 再进 ROM
  复读 SHA==S_target）。跨面断言（BLE 直调世界用刷后镜像起世界）需要世界构造链换 flash 输入，
  属 M4+/另行立项——本规格不声称「重启后新固件在跑」。
- **bank 切换不模拟**（U6）：目标镜像由离线计划器保持 boot-info 序号不变（§3.4）；「序号切换触发
  启动 bank 换边」的固件行为无实证，不发明。

### 3.4 目标镜像与扇区计划：宿主离线工具（实施路新文件）

```
tools/sim_restore_planner.py（新）：
  输入：--base <2MiB fixture>（=server --flash-image 同源）--target <2MiB 目标镜像>
  输出：plan.json {sectors:[基址…], target_sha256, base_sha256,
                   preserved:[[0,S_min],[S_max,2MiB]]}   # 计划外全部保留
  规则：diff(base,target) → 变化字节所属 4KiB 扇区集合；空计划拒绝；扇区集合必须使
        diff(target, base') 仅落在计划扇区内（base'=写完的 base）。
```

- 目标镜像的 boot-info/密文 CRC 层：M3 验收用**合成目标**（改若干字节+更新无关紧要区）即可全环
  机检；「真实 UFW 级 bank 恢复」（boot-info 序号/CRC/长度更新，image.py:74-102 prepare 语义）
  列为 planner 的可选 `--ufw` 模式（复用 tools/usb-recovery/image.py 的 encode/sfc/crc 纯函数，
  import 不改）——不阻塞 M3 验收（设计 §5.3-1 的「6 扇区一类」口径由合成目标覆盖同构性）。
- **预期镜像独立性**（设计 §5.3-3 codex 纪律）：验收的期望值由测试脚本离线算（planner 或脚本内
  diff 构造），server 只执行不生成期望——`usb_arm` 的 target_sha256 是**断言输入**（reboot 时
  server 对比副本 SHA 并记进 journal，供验收读），不是 server 自己给自己打分。

### 3.5 写后状态与磁盘持久化（取舍）

| 方案 | 行为 | 取舍 |
| --- | --- | --- |
| **A（默认）**：不落盘 | 副本只活在进程内；server 退出=回滚原始 fixture | 原始资产零风险（`--flash-image` 文件只 `open('rb')` 读一次，sim_bridge_server.py:692 现状保持）；测试可无限重复；与真机「擦写不可逆」语义不同——如实标注，验收用「重连后副本状态可见」代理不可逆性 |
| B（可选 `--persist <dir>`） | journal+副本定期落盘，重启 server 恢复 armed 前状态 | 贴近真机；引入磁盘态泄漏（上轮半擦影响下轮）+额外失败面。**第一版不做**，留 CLI 占位 |

选 A：M3 的价值在「app 刷机路径全环可机检」，不在仿真掉电语义；A 下验收判据 4（armed 过期/进程
重启 nonce 作废）由「进程级状态」天然成立。disarm 丢弃副本（§3.1）与 A 同理——半擦状态的「可见」
窗口=armed 会话存续期间（断链不断 armed，N2 负例覆盖）。

### 3.6 会话层与 CLI 增量

- `SimBridgeSession.handle` 加两个消息分支（app/宿主客户端均可发；形状照 `usb_ctl`
  sim_bridge_server.py:574-587）：

| 消息 | 语义 | 回 |
| --- | --- | --- |
| `{"t":"usb_arm","sectors":[…],"target_sha256":…,"nonce":…}` | §3.1 armed 进入（校验失败不断链） | `{"t":"usb_arm_result","ok":bool,"detail":…}` |
| `{"t":"usb_disarm"}` | 主动作废（副本丢弃） | `{"t":"usb_disarm_result","ok":bool}` |
| `{"t":"usb_reboot"}` | §3.3 重启换身份（成功即断连） | 断连前 `{"t":"usb_reboot_result","ok":true}` |

- CLI：`--arm-plan <json文件>`（冒烟：启动即 armed，跳过 app 确认链——仅测试构图用，验收走
  usb_arm 消息面）+ `--persist`（占位，§3.5-B）。
- 单活动客户端/busy/资源上限：全部不动（M2 纪律，usb_arm 是普通请求消息不入例外）。

## 4. app 侧改动面（tools/android-query）

### 4.1 总账：SimulationUsbLink 已就绪≠app 零改动

SimulationUsbLink 的 bulk/ctl/scan 通道对 FB01/FB04 **透明可用**（CBW 流重组在 server，OUT 数据
相位走既有 bulkOut，SimulationUsbLink.java:217-233）；缺的是**业务与编码入口**（SIMBRIDGE 设计
§5.3 codex 展开结论，本规格落形状）：

| 文件 | 改动 | 量级 |
| --- | --- | --- |
| UsbScsi.java | 写能力位 + eraseSector/programSector 两方法（§4.2） | 小 |
| H3sUsbBridge.java | 恢复业务本体：FlashPlan/armed 链/restoreFlash 执行器/事件（§4.3） | 大头 |
| H3sUsbBridge.Link | +两个 default 方法 armFlash/rebootDevice（§4.4） | 小（不破坏 M2 八方法契约） |
| SimulationUsbLink.java | 覆写 armFlash/rebootDevice（usb_arm/usb_reboot 消息） | 小 |
| WebShellActivity/index.html | USB tab 恢复卡（计划/确认/进度 UI） | 中 |
| AndroidUsbLink.java | **零改动**（default 方法不覆写=false=不支持，恢复业务报错退出） | 0 |

### 4.2 UsbScsi：写能力位 + 两方法

```java
public class UsbScsi {
    public static final int OP_SECTOR_ERASE = 0xfb01;     // [实测] usb-recovery.md:74
    public static final int OP_SECTOR_PROGRAM = 0xfb04;   // [实测] usb-recovery.md:75
    public static final int FLASH_SECTOR_4K = 4096;       // NOR 擦除块（image.py:15 SECTOR=4096）
    private final boolean writable;                       // 写能力位（默认 false=M2 行为）

    public UsbScsi(Channel ch) { this(ch, false); }
    public UsbScsi(Channel ch, boolean writable) { … }    // 仿真构建/测试才 true

    static boolean opcodeAllowed(int opcode, boolean writable) {
        …M2 六白名单… || (writable && (opcode == OP_SECTOR_ERASE || opcode == OP_SECTOR_PROGRAM));
    }
    /** FB01：擦 4KiB 扇区（addr 4KiB 对齐；16B IN 回显 opcode）。 */
    public byte[] eraseSector(int address) {
        if (!writable) throw new IllegalStateException("写能力未开（只读形态）");
        if (address < 0 || address % FLASH_SECTOR_4K != 0) throw new IllegalArgumentException("FB01 地址须 4KiB 对齐");
        return cmd(OP_SECTOR_ERASE, addr4(address), 16, null);
    }
    /** FB04：512B 编程（args=addr4+len2+00+crc16LE，照 ramWrite 形状 UsbScsi.java:166-168）。 */
    public void programSector(int address, byte[] chunk) {
        if (!writable || chunk == null || chunk.length != SECTOR
            || address % SECTOR != 0) throw …;
        int crc = crc16(chunk);
        byte[] args = {addr4…, len2…, 0, (byte)crc, (byte)(crc >> 8)};
        cmd(OP_SECTOR_PROGRAM, args, 0, chunk);           // OUT 数据相位，无 IN 响应
    }
}
```

**能力位而非全局白名单**（设计 §5.3 codex 纪律「不只依赖 server 拒绝越界」）：生产（真机）构建
`writable=false`，FB01/FB04 **在编码入口就不存在**——M2 红线「UsbScsi 不提供任何 Flash 写路径」
在生产形态逐字保持（UsbScsi.java:18-19 类注更新为「写路径仅仿真构建经显式能力位编入」）。
vendorCdb 的白名单查询带上 writable 位（调用点 :75-84 同步加参）。

### 4.3 H3sUsbBridge：恢复业务本体（恢复执行器核心形状）

```java
// 状态与计划（照 UpgradeGate 先例：单次确认+nonce+过期，UpgradeGate.java:33-47）
public final class FlashPlan {
    public final int[] sectors;           // 4KiB 基址升序
    public final byte[] target;           // 2MiB 预期镜像（宿主 planner 产物经 app 私有目录/文件选择进入）
    public final String targetSha256, baseSha256, nonce;   // nonce=UUID（UpgradeService.java:278 同款）
}
// JS 接口（@JavascriptInterface，事件走既有 ResultSink emitEvent）：
//   planRestore(targetPath)   读目标+SHA 校验 → 读当前备份（⑤ 产物）diff 得扇区计划 →
//                             PREPARED 态发 usb_restore_event（计划+nonce 展示，等待确认）
//   confirmRestore(nonce)     nonce 单次+2min 过期（MAX_AGE_MS 照 UpgradeGate.java:9）→
//                             Link.armFlash(planJson) → armed
//   cancelRestore()           PREPARED 可取消；armed 后不可（真机纪律：擦写中取消=危险操作，
//                             h3s_usb.py 恢复流无中途取消路径）
//   restoreFlash()            执行器（下） + rebootDevice() 收尾
// 执行器（对照 h3s_usb.py:150-184 真实工具逐句）：
for (int sector : plan.sectors) {
    require(readFlashSlice(sector, 4096) == backupSlice(sector), "扇区在擦前变化");   // :163
    scsi.eraseSector(sector);                                                        // :167
    require(readFlashSlice(sector, 4096) == FF_4096, "擦除验证失败");                 // :168
    for (int p = sector; p < sector + 4096; p += 512) {
        scsi.programSector(p, Arrays.copyOfRange(target, p, p + 512));               // :171-172
    }
    require(readFlashSlice(sector, 4096) == targetSlice(sector), "扇区编程验证失败");// :173
    progress("restore", done++, plan.sectors.length);                                 // usb_progress 事件
}
require(readFlash(FLASH_SIZE) 双读 == target, "最终全片读回不符");                    // :177-179
```

- **范围守卫双层**（设计 §5.3-2）：app 层=执行器只发计划内地址（FlashPlan 不可变+循环边界）；若
  UI/并发异常试图发计划外地址，构造参数校验（eraseSector/programSector 的对齐+范围断言）先抛。
  server 层=§2.4-3 计划门（绕过 app 直发也拒，验收负例 N4）。
- 错误中止：任何 require 失败→emitEvent(usb_restore ok=false)+**保持 armed**（不自动 disarm——
  状态留给 UI 决定重试或 cancelDisarm；重试=同计划重跑，server 副本上已写的扇区重擦重写幂等）。
- readFlashSlice/readFlash 复用 M2 私有方法（H3sUsbBridge.java:381-392 readFlash）。

### 4.4 Link 接口扩展：两个 default 方法

```java
public interface Link {
    …M2 八方法不动（H3sUsbBridge.java:52-69）…
    /** 告知仿真设备本次刷机计划（armed 协商）。真机链路无此面：默认不支持。 */
    default boolean armFlash(String planJson) { return false; }
    /** 仿真设备整机重启（rom→normal 重枚举）。真机=手动断电/拔线，链路层无此命令。 */
    default boolean rebootDevice() { return false; }
}
```

- Java 8 default 方法不破坏 AndroidUsbLink/测试桩的既有实现（M2 契约面零扰动）。
- SimulationUsbLink 覆写：`armFlash` → roundtrip(usb_arm 消息)（roundtrip 基建现成，
  SimulationUsbLink.java:296-317；handleLine 的 reply 分发表加 usb_arm_result/usb_disarm_result/
  usb_reboot_result 三项，SimulationUsbLink.java:286-288）；`rebootDevice` → roundtrip(usb_reboot)
  （server 断连=EOF=onDetached 模拟拔出，与 enterRomRequest 的返回语义对称：返回=请求已送达，
  以重扫身份为准）。
- H3sUsbBridge 恢复业务对 `armFlash()==false` 的处置=报错「链路不支持刷机协商（真机链路需走
  官方恢复工具）」，不降级不绕过（诚实失败，同 M2 physicalLinkVerified=false 展示纪律）。

### 4.5 AVD 端到端验收剧本（SIMB2A 绕法沿用）

```bash
# 宿主：构造 fixture + 目标镜像 + 计划，起 server（监听 127.0.0.1 即可——slirp 10.0.2.2 映射宿主 loopback）
python3 tools/sim_flash_fixture.py --output analysis/sim-usb/flash-2mib.bin
python3 tools/sim_restore_planner.py --base … --target … --plan analysis/sim-usb/plan.json   # §3.4
python3 tools/sim_bridge_server.py --listen 127.0.0.1:7788 \
    --flash-image analysis/sim-usb/flash-2mib.bin --rom analysis/sim-usb/rom-window.bin
# AVD（SIMB2A 实测地基：h3s-app headless；adbd 偶发重启清 reverse → sim_host 直连 10.0.2.2 绕开）
emulator -avd h3s-app -no-window -no-audio -gpu swiftshader_indirect &
adb install -r …app.apk
adb shell am start -n cn.felix021.h3squery/.WebShellActivity --ez sim true --es sim_host 10.0.2.2
# app 内（WebView USB tab，经 webview_devtools_remote CDP 或截图取证）：
#   扫描→进入 ROM→装载 loader→全片备份→选目标镜像→planRestore→确认（nonce）→restoreFlash
#   →进度事件→rebootDevice→重扫=normal 身份→（验证环）再进 ROM 复读
```

## 5. 验收判据（机检）

验收执行器=宿主侧新文件 `tools/test_sim_m3_verdicts.py`（照 test_sim_usb_device.py 的 BotHost
先例走 socket/设备直调，退出码即 verdict）+ AVD 端到端剧本（§4.5）双轨。设备级红绿单测并入
test_sim_usb_device.py 扩展段。

### 5.1 正例：全环 SHA 链（P1-P7）

| # | 步骤 | 断言 |
| --- | --- | --- |
| P1 | fixture 构造 + planner 产出 | S0=sha256(flash-2mib.bin)==构造器报告 SHA；plan.sectors 非空全对齐；S_target=sha256(target) 独立复算（脚本内 hashlib，不用 server 回报值） |
| P2 | M2 回归前置 | 未 armed 全序列（enterRom→loader→FD05 双读备份）SHA==S0（M2 行为零漂移；M2 既有测试不动仍绿） |
| P3 | usb_arm（nonce=N1） | ①usb_arm_result ok；②describe：armed=true、work_sha256==S0（副本=原始拷贝）；③再 usb_arm（同 N1/异 N2）→ 拒绝 |
| P4 | 逐扇区擦写（§1-⑧序列，BotHost 驱动或 app） | 每扇区：FB01 后 FD05×8==ff；FB04×8 后 FD05×8==target 切片；server journal 按序 erase×1+program×8/扇区 |
| P5 | 计划外只读区抽查 | 未在计划内的保留扇区 FD05 读==S0 对应切片（擦写零外溢） |
| P6 | 全片复读 | FD05 全片（4096×512B）SHA==S_target；server describe.work_sha256==S_target（两面互证） |
| P7 | reboot+验证环 | ①usb_reboot ok 后断连；②rescan=normal（3654:4a55）+open_key(旧 rom key)=过期；③再进 ROM→装载→FD05 全片 SHA 仍==S_target；④journal 尾部有 reboot 记录、armed=false；⑤磁盘 flash-2mib.bin SHA==S0（**原始资产全程未被写**，copy-on-armed 铁证） |

verdict=**刷机全环通过**。P4/P6 任一失败→设备端 journal+副本 SHA 落盘（验收记录），不重跑遮掩。

### 5.2 负例（N1-N5）

| # | 场景 | 断言 |
| --- | --- | --- |
| N1 误擦保护 | 未 armed 直发 FB01/FB04（绕过 app，BotHost 裸发） | ①CSW status=1；②rejected 记录含 opcode+armed 理由；③副本未建立（work_sha256 无）、FD05 仍读原始；④describe.read_only 语义标注更新后 armed=false 时 M2 断言不变 |
| N2 中断（擦一半断链） | armed→擦 3/6 扇区→断 socket（不 disarm） | ①server 进程存活、armed 保持；②重连 describe：journal 3×erase、work_sha256≠S0≠S_target（半态可见）；③FD05 读已擦扇区==ff、未擦==原始（状态如实）；④磁盘 SHA==S0；⑤重新 arm（新 nonce N2）→副本重置为 S0→完整重刷→P6 通过（**可恢复**） |
| N3 armed 过期/作废 | ①armed 后同 nonce 复用；②server kill -9 重启后发 usb_arm(N1) | ①拒绝；②重启后 armed=false、FD05==S0（回滚，§3.5-A）、旧 nonce 自然无效（进程级状态） |
| N4 计划外写 | armed(plan=P) 后 FB01/FB04 打非 P 扇区（含对齐但计划外、非对齐、越界三种） | 双层：BotHost 裸发→server 计划门 CSW status=1+记录；（app 层负例进 JVM 单测：eraseSector 构造参数抛） |
| N5 形状负例 | FB04 CRC 错/len≠512/未擦先写；FB01 非 16B IN | 全部 _Reject+记录；副本不变（用 FD05 抽查） |

### 5.3 app 侧单测（JVM，进 check_local --units）

1. UsbScsiTest 扩段：writable=true 时 eraseSector/programSector 的 CBW 字节级断言（对照
   h3s_usb.py:167/171-172 的 args 十六进制）+writable=false 抛；回显校验（FB01 响应前 2B）。
2. H3sUsbBridgeTest 恢复业务桩测：假 Link+假 Channel 驱动 planRestore→confirm→restoreFlash 全序
   （事件序+进度+错误中止路径+nonce 过期/复用拒绝——UpgradeGate 同款断言面）；armFlash()==false
   的诚实失败路径。
3. M2 既有 UsbScsiTest/H3sUsbBridgeTest 零改动仍绿（只读默认形态回归）。

### 5.4 验收记录落档

{P1-P7/N1-N5 消息序列、journal、各阶段 SHA（S0/半态/S_target）、耗时}落 JSON 进
analysis/local-checks/ 惯例区；AVD 端到端另存截图/CDP 记录（照 SIMB2A ~/tmp 交付先例，不进仓）。

## 6. 风险与依赖

| # | 风险/依赖 | 影响 | 缓解（落点） |
| --- | --- | --- | --- |
| D1 | **SIMB1C 在途改 sim_bridge_server.py**（SIMB1T 规格 §3.4：handle 消息分支重构、--static/--world 模式分叉、会话线程模型改单线程）——与 M3 的 handle 加分支/main() 加 CLI 同文件同函数 | 合并冲突；M3 基于旧线程模型写的消息分支被单线程改造推翻 | 切分建议：M3 改动按「类内新增优先」排序——①MscBotDevice 扩展（armed/两 handler/reboot，类内新增冲突面小）②测试文件（新文件零冲突）③handle 三消息分支+CLI（**等 SIMB1C 落 main 后 rebase 再做**，行级冲突集中此处）。M3 各分支独立可测（①②不依赖③——设备级直调 feed_out 即可全验） |
| D2 | 原始资产被写（不可逆） | 测试资产损坏、fixture 供应链污染 | 三层：open('rb') 只读打开（现状）；copy-on-armed（写只落进程内副本）；不落盘默认（§3.5-A）。P7-⑤/N2-④ 机检兜底 |
| D3 | FB01/FB04 尾段/非 512 长度等 [未实证 U1-U4] | 仿真形状与真机有偏差 | 双向只依赖已实证面（回显 2B/512 约束）；如需钉死→真机抓包任务单（对 armed 真机发受控 FB01 非对齐地址观察 CSW——**需用户明确授权**，本规格不排期） |
| D4 | app 恢复业务本体的工作量被低估（SIMBRIDGE §5.3 codex 已警示过一次） | M3 实施路拖尾 | §4.3 已把执行器核心形状落到位（对照 h3s_usb.py 逐句）；UI 层可裁（第一版 JS 恢复卡只做 计划→确认→进度→结果 四态，不做日志面板） |
| D5 | 中断负例与真机语义分歧（A 方案回滚 vs 真机不可逆） | 把仿真结论误推真机 | §3.5 如实标注；验收记录写明「断链可见性=armed 会话存续期」；B 方案留位不实施 |
| D6 | 双客户端并发（BLE sim-gatt 与 USB sim-usb 同时 armed 操作） | machine 无关但 USB 面状态串扰（M2 单活动连接纪律已限一对一） | 维持单活动客户端（busy 断开）；usb_arm/reboot 是会话消息不吃例外 |
| D7 | FC03 stall 教训（usb-recovery.md:77：参考库有≠loader 支持） | 把上游 jl-uboot-tool 文档当本 loader 语义 | 本规格 FB01/FB04 全部锚实机恢复工具实证（h3s_usb.py），上游文档仅作 CDB 布局旁证；不采纳未实测命令 |
| D8 | BLE OTA（E1-E8）混入 M3 | 范围蔓延 | M3 只做 USB 面；E1-E8 维持设计 §5.3-5 的 M4 可选定位（UpgradeService 现成代码不动） |

## 7. 证据锚点索引（复核用）

| 结论 | 锚点 |
| --- | --- |
| FB01=addr4/擦 4KiB/16B 响应；FB04=addr4+len2+00+crc16LE/写+DATA OUT | docs/usb-recovery.md:74-75（实测表）；tools/usb-recovery/h3s_usb.py:167、:171-172（调用形状） |
| 厂商 CDB 大端 opcode+参数+ff 补齐；地址长度大端 CRC 小端 crc_hqx | usb-recovery.md:66；protocol.py:39-45（cmd/回显规则）；UsbScsi.java:73-84（Java 同形） |
| 擦后读 ff、每扇区 8×512 写读回、全片复读、保留区 | usb-recovery.md:99-106（实测）；h3s_usb.py:163-179（工具逐句）；image.py:66-71（validate_delta 保留范围） |
| 恢复守卫形状（writing 门/active_sector/512 对齐/CRC/计划外拒） | h3s_usb.py:86-101（RestoreTransport.execute）；test_recovery.py:52-66（红路径单测先例） |
| FB01/FB04 属 loader-ready 后命令（擦写发生在装载后）；FC03 stall 教训 | usb-recovery.md:60/77/99；read_rom.py:65-66（FORBIDDEN 清单含 FB00/FB01/FB02/FB04/FC0C/FC12） |
| loader 不留驻/自行回应用模式；手动重启=收尾步骤 | usb-recovery.md:79、:145 |
| ROM/loader hook 分派（FB06 清 hook/FB08 保留；命令处理者由 hook 决定） | usb-rom-dispatch.md:26-38（静态） |
| boot-info 字段（CRC@0/明文@2/密文@4/序号@6/长度@8；两 bank 序号 2/3） | firmware-layout.md:87-105；image.py:54-63（bank() 解码）；序号保持纪律 usb-recovery.md:97/:106 |
| 4KiB 扇区/2MiB/六扇区 ORDER/VM 保留 | image.py:12-15、:105-116；usb-recovery.md:101-104 |
| M2 落地（相位机/身份机/白名单拒绝/bytes 只读/BOT 契约/单活动） | sim_bridge_server.py:201-238（构造/describe）、:240-271（scan/control）、:328-354（_execute/白名单）、:423-436（FD05 门）、:574-587（usb_ctl 分支先例）；test_sim_usb_device.py:14-16 |
| M2 红线原文（USB 通道无 Flash 写路径） | sim_bridge_server.py:19-20（docstring）、:198（类注）；UsbScsi.java:18-19（编码入口不存在） |
| app Link 八方法/loaderReady/backup 双读/readFlash | H3sUsbBridge.java:52-69、:90、:292-320、:346-392 |
| armed 先例（nonce 单次/2min/状态机/进程重启不恢复） | UpgradeGate.java:5-10/33-47；UpgradeService.java:278-283（UUID nonce）；ota.md「升级门槛与单次确认」节 |
| socket 契约 proto=1/roundtrip/reply 分发/onDetached | SimulationUsbLink.java:51-63、:286-288、:296-317、:331-341；sim_bridge_server.py:22-37（协议 docstring） |
| AVD 基地+adbd 绕法（sim_host 10.0.2.2） | WebShellActivity.java:66-89；HANDOFF SIMB2A 条目（遗留⑤） |
| SIMB1C 冲突面（handle 改造/模式分叉/单线程） | sim-m1-direct-spec.md §3.4-§3.6（SIMB1T 规划的 server 改造点） |
| fixture 构造（flash.bin 逐字节+0xff 合成区） | sim_flash_fixture.py:47-58（NAMED_REGIONS）、:76-100（assemble） |

## 8. 红线自查（本路）

- 零 tools/ 改动、probe 五文件零触碰（git status 可查）；本文纯新增 docs/。
- HANDOFF 未动（条目草稿随报告交主协调）。
- 不发明：FB01/FB04 全部字段锚 h3s_usb.py/usb-recovery.md 实证；U1-U6 未实证项集中列出并给处置；
  bank 启动选择/bank 切换固件行为明确不做；「重启」不用未钉 opcode（FB00/FB02）。
- 验收全部机检（P1-P7/N1-N5 消息序列+断言+退出码）；预期镜像独立构造（server 不自评）；未 armed
  行为与 M2 逐字节一致（回归保障）。
- 提交不加 Co-Authored-By；文档内主机/端口示例一律 127.0.0.1。
