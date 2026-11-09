# QCY App「声音」设置如何与固件配合

## 分析范围和材料

原调查使用QCY Android App4.2.1 /versionCode701及已修复DEX的jadx输出。部分代码经过加固，不能把直接解压APK或未修复方法的空壳当作完成反编译。

仓库保存的是分析结果、方法定位、模式参数表与型号控制面板，不重新发布完整QCY APK或整棵第三方反编译源码。若自行复跑，需要从自己的合法APK提取对应DEX，并用jadx查看。历史源码头可能出现旧调查工程名，这不是H3S固件的源工程。

## 型号控制面板

[data/controlpanel_qcyh3s_16764.json](../data/controlpanel_qcyh3s_16764.json) 是型号16764的控制面板。ANC布局给出：

| UI项目 | defaultcmd十进制 | 拆成mode/scene/strength |
| --- | ---: | --- |
| 降噪 | 65794 | 01 01 02 |
| 通透 | 196868 | 03 01 04 |
| 正常 | 131072 | 02 00 00 |

降噪还列出自适应、嘈杂、通勤、室内、抗风噪子项。控制面板描述“可显示和下发什么”，不能单凭一项存在就断言每个固件版本都实现了它。面板使用的图片名也不是硬件识别依据。

## App调用链

关键类位于 `com.qcymall.earphonesetup`：

```text
v2ui.fragment.EQInfoFragment
    选中模式／子场景／强度
    ↓
manager.QCYConnectManager.setNoiseMode(bleMac, packedCommand)
    ↓
manager.JLDeviceImpl.setANCSenseMode(mode, scene, strength)
    ↓
JLDeviceImpl.sendCustomCMD([0x17, 3, mode, scene, strength])
    ↓
CustomCmd + CustomParam → JL RCSP SDK → QCY A002/1 BLE写入
```

`QCYConnectManager.setNoiseMode()` 对JL设备且命令大于255的情况拆字节：

```text
mode     = (command & 0xFF0000) >>16
scene    = (command & 0x00FF00) >>8
strength = command &0xFF
```

另有特殊兼容：`mode=3, scene=1, strength=0` 会转成 `3,2,0`。H3S面板默认通透是 `3,1,4`，不触发此分支。不能把特殊兼容写成所有通透命令的编码。

`JLDeviceImpl.setANCSenseMode()` 构造十进制数组 `[23,3,mode,scene,strength]`，即十六进制 `17 03 ...`。`sendCustomCMD()` 交给SDK的CustomCmd，不是直接向任意蓝牙UUID写一个孤立的0x17。

## 查询与回调

`getANCSenseLeval()` 调用 `getCustomCMDInfo(23)`，构造 `FE 01 17`。返回的数据如：

```text
17 03 01 01 02
│  │  └─────── 三字节mode/scene/strength
│  └────────── 数据长度3
└───────────── 自定义功能0x17
```

`JLDeviceImpl.checkCustomCMDInfo()` 的case23去掉前两字节，交给 `com.qcymall.qcylibrary.dataBean.ANCSettingDataBean`，按顺序解出mode、subSence、noiseValue，再通过HeadsetListener通知UI。代码有距上次设置时间的过滤，避免立即返回造成反复UI更新；它不等于设备没有应用设置。

本机只读查询实际返回 `17 03 01 01 02`。仓库中的查询App保留 `anc-info` 入口，仅发送读取命令，不公开任意ANC设置接口。

## 对应到H3S固件

从原app.bin机器码和常量记录确认：

| 项目 | 位置／作用 |
| --- | --- |
| 0x17注册记录 | app+0xA9AE8，一组7个32位word，以0x17结束 |
| 记录内容 | `0600DDDC,06025C84,06025CA4,0,0,06025D32,00000017` |
| Getter0x06025C84 | 容量至少3时返回状态地址0x0012666B的3字节 |
| Setter0x06025CA4 | 检查长度和设备状态，复制3字节，向app_core调度0x06025D6A |
| Callback0x06025D6A | 调用0x06012BE2保存状态，然后调用0x0600DCEE应用 |
| 0x06012BE2 | 以key16、len3调用配置写函数；与启动读取路径对应，函数名是按行为解释而非恢复符号 |
| 0x0600DCEE | QCY mode1→内部ON2，mode2→内部OFF1，mode3→内部TRANS3 |

0x0600DDDC的初始化路径读取同一三字节保存项，并处理缺失／特殊场景默认值。App直接模式设置由事件任务应用，不能假设BLE回调里立刻执行了所有DSP切换。

实际片段见 [qcy-command17.asm](../evidence/anc-cycle/qcy-command17.asm)、[直接设置与保存附近代码](../evidence/anc-cycle/0600dcee-0600de80.asm)、[任务回调](../evidence/anc-cycle/06025d20-06025dc0.asm)。

## 为什么App能选择三种模式，按键却能改成两种

直接设置回答“进入指定模式”；next例程回答“从当前模式去下一档”。它们在下层模式应用处汇合，但选择目标的逻辑不同。

```text
App选择正常 ─→ 命令17明确携带OFF ─→ 直接设置路径 ─→ ANC内部OFF1

按模式键 ─→ anc_next ─→ 查循环表 ─→ 选择下一目标 ─→ 同类模式应用逻辑
                       ↑
               本次只改这一张表
```

把App界面隐藏“正常”、修改手机端页面或由手机收到OFF后再立刻发ON，都不能替代耳机本身的按键行为。当前补丁在耳机固件中生效，不要求手机后台守护程序。

## 通用ANC列表接口的调查

杰理通用文档提供ADV type10的ANC模式列表，用32位mask描述关闭／降噪／通透。我们只读发送C1、mask `00000400` 查询该字段。H3S回复status0但没有返回该属性，因此没有依据把通用type10设置命令直接写进去。

这次没有通过该接口改mask，也不能把“空属性”写成完整证明固件永远不支持它。最终采用的是已经定位并验证的实际固件循环表。

参考：[杰理ANC设置](https://doc.zh-jieli.com/Apps/Android/jielihome/zh-cn/master/development/function/anc_ctrl.html)、[ADV设置数据格式](https://doc.zh-jieli.com/Apps/Android/jielihome/zh-cn/master/development/function/tws_func.html)。
