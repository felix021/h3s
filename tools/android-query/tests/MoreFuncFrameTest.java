import cn.felix021.h3squery.EqWritePolicy;
import cn.felix021.h3squery.H3SBridge;
import cn.felix021.h3squery.KeyRemapFrame;
import cn.felix021.h3squery.LoopbackTransport;
import cn.felix021.h3squery.MoreFuncFrame;
import org.json.JSONObject;

/**
 * 「更多功能」纯 JVM 单测（docs/morefunc-protocol.md，路 MOREFUNC / sim-morefunc + eqproto 勘误）：
 * 新帧形状正例/边界/负例（0x14/0x1f/0x19/0x23/0x24/0xC0/0xC1/0xC5；FF/0x03 旧清配对已删→拒绝）、
 * 回读载荷解析、EqWritePolicy 白名单放行/拒绝、H3SBridge 方法字节对照（findBuds 不发帧、
 * clearPairing=C5/02），以及 LoopbackTransport 端到端（真固件行为桩：FF 静默 setter→回读确认、
 * C0 写改表→C1 读回 45B、C5 恢复出厂重置桩态、找耳机 0 次 BLE 写/清配对才发 C5/02）。
 */
public class MoreFuncFrameTest {
    static void expect(boolean b, String msg) { if (!b) throw new AssertionError(msg); }
    static void eq(String name, int a, int b) { if (a != b) throw new AssertionError(name + ": " + a + " != " + b); }

    static void bytes(String name, byte[] a, byte[] b) {
        if (!java.util.Arrays.equals(a, b))
            throw new AssertionError(name + ":\n  got " + H3SBridge.hex(a) + "\n  exp " + H3SBridge.hex(b));
    }

    static JSONObject result(String json) throws org.json.JSONException { return new JSONObject(json); }

    public static void main(String[] args) throws Exception {
        frameShapes();
        policyGates();
        bridgeMethods();
        parsePayloads();
        loopbackEndToEnd();
        System.out.println("MoreFuncFrameTest passed: 0x14/0x1f/0x19/0x23/0x24/0xC0/0xC1/0xC5 帧"
            + "正例+边界+负例（FF/0x03 已删→拒绝）、白名单放行/拒绝、bridge 字节对照（findBuds 0 写、"
            + "clearPairing=C5/02）、回读解析、Loopback 端到端（静默 setter 回读确认+C1 读表+C5 恢复+找耳机 0 次 BLE 写）");
    }

    /** 帧形状正例/边界/负例。 */
    static void frameShapes() throws Exception {
        // 0x14 定时关机：data=[lo,hi,00,00]；30 分钟=0x001E→LE 1E 00
        bytes("定时关机 30 分钟", MoreFuncFrame.buildCustomCommand(0, 0x14, MoreFuncFrame.buildSleepTimerData(30)),
            unhex("fedcbac0ff0007" + "00" + "1404" + "1e000000" + "ef"));
        bytes("定时关机 不关机(FFFF)", MoreFuncFrame.buildCustomCommand(0, 0x14, MoreFuncFrame.buildSleepTimerData(0xffff)),
            unhex("fedcbac0ff0007" + "00" + "1404" + "ffff0000" + "ef"));
        // 0x1f 断连关机：data=[lo,hi]
        bytes("断连关机 10 分钟", MoreFuncFrame.buildCustomCommand(0, 0x1f, MoreFuncFrame.buildAutoOffData(10)),
            unhex("fedcbac0ff0005" + "00" + "1f02" + "0a00" + "ef"));
        // 0x19 提示音语言：JL00/JL01
        bytes("提示音语言 英文", MoreFuncFrame.buildCustomCommand(0, 0x19, MoreFuncFrame.buildToneLanguageData(false)),
            unhex("fedcbac0ff0007" + "00" + "1904" + "4a4c3030" + "ef"));
        bytes("提示音语言 中文", MoreFuncFrame.buildCustomCommand(0, 0x19, MoreFuncFrame.buildToneLanguageData(true)),
            unhex("fedcbac0ff0007" + "00" + "1904" + "4a4c3031" + "ef"));
        // 0x23/0x24：面板 cmd 串 "230101"/"240100" 的 data 部分（len=01）
        bytes("LDAC 开", MoreFuncFrame.buildCustomCommand(0, 0x23, MoreFuncFrame.buildBoolData(true)),
            unhex("fedcbac0ff0004" + "00" + "2301" + "01" + "ef"));
        bytes("双设备 关", MoreFuncFrame.buildCustomCommand(0, 0x24, MoreFuncFrame.buildBoolData(false)),
            unhex("fedcbac0ff0004" + "00" + "2401" + "00" + "ef"));
        // 0xC5 恢复出厂 / 清除配对（02 原「找耳机」[推断] 已勘误：eqproto 证明是原生清配对命令）
        bytes("恢复出厂", MoreFuncFrame.buildResetFamilyCommand(0, MoreFuncFrame.RESET_SUBCMD_FACTORY),
            unhex("fedcbac0c50002" + "00" + "01" + "ef"));
        bytes("清除配对（QCY 原生字节）", MoreFuncFrame.buildResetFamilyCommand(0, MoreFuncFrame.RESET_SUBCMD_CLEAR_PAIRING),
            unhex("fedcbac0c50002" + "00" + "02" + "ef"));
        // 0xC0 设备名：item={len=1+nameLen, cmdid=01, name}
        bytes("设备名写", MoreFuncFrame.buildNameWriteCommand(0, "H3S"),
            unhex("fedcbac0c00006" + "00" + "0401" + "483353" + "ef"));
        // 边界：分钟 0（立即关机）拒绝、60/FFFF 放行、61 拒绝
        boolean threw = false;
        try { MoreFuncFrame.buildSleepTimerData(0); } catch (IllegalArgumentException e) { threw = true; }
        expect(threw, "定时关机 0 分钟（=立即关机）应拒绝");
        threw = false;
        try { MoreFuncFrame.buildAutoOffData(61); } catch (IllegalArgumentException e) { threw = true; }
        expect(threw, "断连关机 61 分钟（非面板档位）应拒绝");
        MoreFuncFrame.buildSleepTimerData(60);              // 60 合法
        MoreFuncFrame.buildAutoOffData(5);                  // 5 合法
        // 设备名边界：空/21B 拒绝，20B 放行
        threw = false;
        try { MoreFuncFrame.buildNameWriteCommand(0, ""); } catch (IllegalArgumentException e) { threw = true; }
        expect(threw, "空设备名应拒绝");
        threw = false;
        try { MoreFuncFrame.buildNameWriteCommand(0, "x".repeat(21)); } catch (IllegalArgumentException e) { threw = true; }
        expect(threw, "21 字节设备名（面板 maxLength=20）应拒绝");
        MoreFuncFrame.buildNameWriteCommand(0, "x".repeat(20));   // 20 合法
        // 0xC5 子命令边界：03 拒绝
        threw = false;
        try { MoreFuncFrame.buildResetFamilyCommand(0, 3); } catch (IllegalArgumentException e) { threw = true; }
        expect(threw, "0xC5 子命令 03 应拒绝");
        // 严格反解析往返
        byte[] f = MoreFuncFrame.buildBatchReadCommand(7, KeyRemapFrame.ADV_TYPE2_MASK);
        bytes("parseFrame 往返（payload 不含 SN）", MoreFuncFrame.parseFrame(f, 0xc0, 0xc1), unhex("00000004"));
    }

    /** EqWritePolicy 白名单：新形状放行、同族畸形拒绝、未武装拒绝。 */
    static void policyGates() {
        expect(EqWritePolicy.permitted(MoreFuncFrame.buildCustomCommand(0, 0x14, MoreFuncFrame.buildSleepTimerData(30)), true),
            "0x14 定时关机帧应放行");
        expect(!EqWritePolicy.permitted(MoreFuncFrame.buildCustomCommand(0, 0x14, new byte[]{0x00, 0, 0, 0}), true),
            "0x14 分钟 0（立即关机）应拒绝");
        expect(!EqWritePolicy.permitted(MoreFuncFrame.buildCustomCommand(0, 0x14, new byte[]{0x0f, 0, 0, 0}), true),
            "0x14 分钟 15（非档位）应拒绝");
        expect(EqWritePolicy.permitted(MoreFuncFrame.buildCustomCommand(0, 0x14, new byte[]{0x3c, 0, 0, 0}), true),
            "0x14 分钟 60（档位）应放行");
        expect(EqWritePolicy.permitted(MoreFuncFrame.buildCustomCommand(0, 0x1f, MoreFuncFrame.buildAutoOffData(5)), true),
            "0x1f 断连关机帧应放行");
        expect(!EqWritePolicy.permitted(MoreFuncFrame.buildCustomCommand(0, 0x1f, new byte[]{2, 1}), true),
            "0x1f 长度 258 越界（非档位）应拒绝");
        expect(EqWritePolicy.permitted(MoreFuncFrame.buildCustomCommand(0, 0x19, MoreFuncFrame.buildToneLanguageData(true)), true),
            "0x19 提示音语言帧应放行");
        expect(!EqWritePolicy.permitted(MoreFuncFrame.buildCustomCommand(0, 0x19, new byte[]{0x4a, 0x4c, 0x30, 0x32}), true),
            "0x19 未知 key JL02 应拒绝");
        expect(EqWritePolicy.permitted(MoreFuncFrame.buildCustomCommand(0, 0x23, MoreFuncFrame.buildBoolData(true)), true)
            && EqWritePolicy.permitted(MoreFuncFrame.buildCustomCommand(0, 0x24, MoreFuncFrame.buildBoolData(false)), true),
            "0x23/0x24 布尔帧应放行");
        expect(!EqWritePolicy.permitted(MoreFuncFrame.buildCustomCommand(0, 0x23, new byte[]{2}), true),
            "0x23 data=2 应拒绝");
        // 0.9.1 误推断的 FF/03 清配对命令：eqproto 勘误后不再放行（任何 data 值都拒）
        expect(!EqWritePolicy.permitted(MoreFuncFrame.buildCustomCommand(0, 0x03, new byte[]{0x02}), true),
            "FF/0x03 data=02（旧清配对误推断）应拒绝");
        expect(!EqWritePolicy.permitted(MoreFuncFrame.buildCustomCommand(0, 0x03, new byte[]{1}), true),
            "FF/0x03 data=01 应拒绝");
        expect(EqWritePolicy.permitted(KeyRemapFrame.buildBatchSyncCommand(0, 7, 2, 7), true),
            "C0 按键三元组帧应放行");
        expect(EqWritePolicy.permitted(KeyRemapFrame.buildBatchSyncTableCommand(0, new byte[45]), true),
            "C0 按键全表帧应放行");
        expect(EqWritePolicy.permitted(MoreFuncFrame.buildNameWriteCommand(0, "abc"), true),
            "C0 设备名帧应放行");
        expect(!EqWritePolicy.permitted(MoreFuncFrame.buildBatchWriteCommand(0, 12, new byte[]{1}), true),
            "C0 未知 cmdid 12（表 C 语义未钉）应拒绝");
        expect(EqWritePolicy.permitted(KeyRemapFrame.buildBatchReadCommand(0), true),
            "C1 读帧（mask 00000004）应放行");
        expect(!EqWritePolicy.permitted(MoreFuncFrame.buildBatchReadCommand(0, 0x00000008), true),
            "C1 读帧（mask bit3 未钉）应拒绝");
        expect(EqWritePolicy.permitted(MoreFuncFrame.buildResetFamilyCommand(0, MoreFuncFrame.RESET_SUBCMD_FACTORY), true)
            && EqWritePolicy.permitted(MoreFuncFrame.buildResetFamilyCommand(0, MoreFuncFrame.RESET_SUBCMD_CLEAR_PAIRING), true),
            "0xC5 子命令 01 恢复出厂/02 清除配对应放行（调用方负责 UI confirm）");
        byte[] c5bad = unhex("fedcbac0c50002" + "00" + "03" + "ef");   // 手组子命令 03（builder 拒绝）
        expect(!EqWritePolicy.permitted(c5bad, true), "0xC5 子命令 03 武装后也应拒绝");
        // 未武装一律拒绝（退回 QueryPolicy 只读）
        expect(!EqWritePolicy.permitted(MoreFuncFrame.buildCustomCommand(0, 0x14, MoreFuncFrame.buildSleepTimerData(30)), false),
            "0x14 未武装应拒绝");
        expect(!EqWritePolicy.permitted(KeyRemapFrame.buildBatchSyncCommand(0, 7, 2, 7), false),
            "C0 未武装应拒绝");
    }

    /** H3SBridge 新方法字节对照与门控。 */
    static void bridgeMethods() throws Exception {
        H3SBridge b = new H3SBridge();
        expect(!result(b.setSleepTimer(30)).getBoolean("ok"), "未武装 setSleepTimer 应失败");
        b.armWrite(true);
        bytes("setSleepTimer(30) 帧", unhex(result(b.setSleepTimer(30)).getString("bytesHex")),
            unhex("fedcbac0ff0007" + "00" + "1404" + "1e000000" + "ef"));
        bytes("setAutoOff(FFFF) 帧", unhex(result(b.setAutoOff(0xffff)).getString("bytesHex")),
            unhex("fedcbac0ff0005" + "00" + "1f02" + "ffff" + "ef"));
        bytes("setToneLanguage(中文) 帧", unhex(result(b.setToneLanguage(true)).getString("bytesHex")),
            unhex("fedcbac0ff0007" + "00" + "1904" + "4a4c3031" + "ef"));
        bytes("setLdac(true) 帧", unhex(result(b.setLdac(true)).getString("bytesHex")),
            unhex("fedcbac0ff0004" + "00" + "2301" + "01" + "ef"));
        bytes("setDualDevice(false) 帧", unhex(result(b.setDualDevice(false)).getString("bytesHex")),
            unhex("fedcbac0ff0004" + "00" + "2401" + "00" + "ef"));
        bytes("setDeviceName 帧", unhex(result(b.setDeviceName("H3S")).getString("bytesHex")),
            unhex("fedcbac0c00006" + "00" + "0401" + "483353" + "ef"));
        bytes("restoreDefaults 帧", unhex(result(b.restoreDefaults()).getString("bytesHex")),
            unhex("fedcbac0c50002" + "00" + "01" + "ef"));
        // findBuds：eqproto 后不发任何命令（原生=手机侧媒体播放），返回明确待定错误
        JSONObject find = result(b.findBuds());
        expect(!find.getBoolean("ok"), "findBuds 应返回待定错误（不发 BLE 命令）");
        expect(!find.has("bytesHex"), "findBuds 不应产出命令帧");
        // clearPairing：C5/02（QCY 原生清配对字节；0.9.1 的 FF/03 误推断已移除）
        bytes("clearPairing 帧（C5/02）", unhex(result(b.clearPairing()).getString("bytesHex")),
            unhex("fedcbac0c50002" + "00" + "02" + "ef"));
        bytes("readKeyRemapTable 帧", unhex(result(b.readKeyRemapTable()).getString("bytesHex")),
            unhex("fedcbac0c10005" + "00" + "00000004" + "ef"));
        // readStatus 扩白名单
        for (int c : new int[]{0x1f, 0x19, 0x23, 0x24, 0x2e})
            expect(result(b.readStatus(c)).getBoolean("ok"), "readStatus(0x" + Integer.toHexString(c) + ") 应放行");
        expect(!result(b.readStatus(0x2d)).getBoolean("ok"), "readStatus(0x2d 空间音效) 白名单外应拒绝");
    }

    /** 回读载荷解析。 */
    static void parsePayloads() throws Exception {
        int[] st = MoreFuncFrame.parseSleepTimerPayload(new byte[]{0x1e, 0, 0x05, 0});
        eq("定时关机设定", 30, st[0]);
        eq("定时关机剩余", 5, st[1]);
        eq("断连关机", 5, MoreFuncFrame.parseAutoOffPayload(new byte[]{5, 0}));
        expect(MoreFuncFrame.parseToneLanguagePayload(new byte[]{0x4a, 0x4c, 0x30, 0x30}) == false, "JL00=英文");
        expect(MoreFuncFrame.parseToneLanguagePayload(new byte[]{0x4a, 0x4c, 0x30, 0x31}) == true, "JL01=中文");
        boolean threw = false;
        try { MoreFuncFrame.parseToneLanguagePayload(new byte[]{0x4a, 0x4c, 0x30, 0x32}); }
        catch (IllegalArgumentException e) { threw = true; }
        expect(threw, "未知语言 key 应拒绝（不猜语义）");
        // 批量读回执提取：前缀宽容（[未钉] 前缀字节）+ cmdid+len 匹配
        byte[] payload = unhex("0000" + "2e02" + "070101070207070304070400070500030106"
            + "030200030300030400030502050105050200050300050400050503");
        byte[] value = MoreFuncFrame.parseBatchReadPayload(payload, 2);
        expect(value != null && value.length == 45, "应提取出 45B 按键表");
        KeyRemapFrame.Triple[] table = KeyRemapFrame.parseBatchTable(value);
        eq("表项数", 15, table.length);
        eq("多功能双击动作", 7, table[1].action);
        eq("音量- 单击动作", 6, table[5].action);
        expect(MoreFuncFrame.parseBatchReadPayload(unhex("0000"), 2) == null, "无匹配 cmdid 应返回 null");
    }

    /** LoopbackTransport 端到端：真固件行为（静默 setter→回读确认、C0 写改表→C1 读回、C5 恢复）。 */
    static void loopbackEndToEnd() throws Exception {
        LoopbackTransport t = new LoopbackTransport();
        final java.util.List<String> events = new java.util.ArrayList<String>();
        H3SBridge b = new H3SBridge(t, new H3SBridge.ResultSink() {
            public void emit(String json) { events.add(json); }
        });
        b.armWrite(true);
        t.connect();
        // 1) 定时关机写：真固件静默（setter 返回 0 不回帧）——串行队列等响应超时才放行下一条
        //    （Android 适配层 5s 真时钟；测试显式 runTimeouts 推进），随后回读确认。
        JSONObject w = result(b.setSleepTimer(30));
        expect(w.getBoolean("sent"), "写命令应排队: " + w);
        t.runTimeouts();                                   // 触发响应超时 → 队列放行
        expect(countEvents(events, "send_failed") == 1, "静默 setter 应产生 send_failed(响应超时): " + events);
        expect(t.moreFuncState.get(0x14) != null, "桩态应记录 0x14 写值");
        JSONObject q = result(b.readStatus(0x14));
        expect(q.getBoolean("sent"), "回读应排队: " + q);
        JSONObject read = lastEvent(events, "response");
        expect(read != null, "回读应有 response: " + events);
        expect("14041e001e00".equals(read.getString("payloadHex")),
            "回读载荷应为 14 04 1e 00 1e 00（cmdid n 设定 剩余）: " + read);
        // 2) 提示音语言写+回读（静默+回读确认同链）
        result(b.setToneLanguage(true));
        t.runTimeouts();
        result(b.readStatus(0x19));
        JSONObject lang = lastEvent(events, "response");
        expect("19044a4c3031".equals(lang.getString("payloadHex")), "语言回读 JL01: " + lang);
        // 3) 按键批量同步写（有 0xC0 回执）→ C1 读回全表 → 表已改
        JSONObject remap = result(b.setKeyRemap(0x0702, 1));   // 多功能双击→播放/暂停
        expect(remap.getBoolean("sent"), "按键写应排队: " + remap);
        JSONObject remapAck = lastEvent(events, "response");
        expect(remapAck != null && remapAck.getBoolean("ok"), "C0 写应有 status=0 回执: " + remapAck);
        eq("桩表多功能双击动作已改", 1, t.keyRemapTable[5] & 0xff);
        result(b.readKeyRemapTable());
        JSONObject tableEv = lastEvent(events, "response");
        String hex = tableEv.getString("payloadHex");
        byte[] value = MoreFuncFrame.parseBatchReadPayload(H3SBridge.unhex(hex), 2);
        expect(value != null && value.length == 45, "C1 读回应含 45B 表: " + hex);
        KeyRemapFrame.Triple[] table = KeyRemapFrame.parseBatchTable(value);
        eq("读回多功能双击动作", 1, table[1].action);
        eq("读回音量- 单击动作（默认未动）", 6, table[5].action);
        // 4) 0xC5 恢复出厂：桩态清空 + 回执
        result(b.restoreDefaults());
        JSONObject resetAck = lastEvent(events, "response");
        expect(resetAck != null && resetAck.getBoolean("ok"), "C5 恢复应有回执: " + resetAck);
        expect(t.moreFuncState.isEmpty(), "恢复出厂应清空桩态");
        eq("恢复出厂按键表回默认", 7, t.keyRemapTable[5] & 0xff);
        // 4b) eqproto 回归：找耳机 0 次 BLE 写（原生=手机侧媒体播放）；清配对才发 C5/02
        int framesBefore = t.receivedFrames.size();
        JSONObject findRes = result(b.findBuds());
        expect(!findRes.getBoolean("ok"), "findBuds 应返回待定错误（不发送）: " + findRes);
        eq("找耳机 BLE 写次数", framesBefore, t.receivedFrames.size());
        JSONObject clearRes = result(b.clearPairing());
        expect(clearRes.getBoolean("sent"), "清配对应排队发送: " + clearRes);
        bytes("清配对写出帧=C5/02", t.receivedFrames.get(t.receivedFrames.size() - 1),
            unhex("fedcbac0c50002" + String.format("%02x", clearRes.getInt("sn")) + "02" + "ef"));
        // 5) 通知帧（flags=0x80）→ device_message 透出（固件 0x06025642 广播形状：cmdid+len+data，无 SN）
        byte[] notify = unhex("fedcba" + "80" + "ff" + "000a" + "1408" + "1e00000000000000" + "ef");
        t.injectDeviceNotify(notify);
        JSONObject dev = lastEvent(events, "device_message");
        expect(dev != null && dev.getBoolean("notify"), "flags=80 通知应以 device_message(notify) 透出: " + events);
        expect("14081e00000000000000".equals(dev.getString("payloadHex")), "通知载荷透传: " + dev);
    }

    static int countEvents(java.util.List<String> events, String name) throws org.json.JSONException {
        int n = 0;
        for (String s : events) if (new JSONObject(s).getString("event").equals(name)) n++;
        return n;
    }

    static JSONObject lastEvent(java.util.List<String> events, String name) throws org.json.JSONException {
        for (int i = events.size() - 1; i >= 0; i--) {
            JSONObject o = new JSONObject(events.get(i));
            if (o.getString("event").equals(name)) return o;
        }
        return null;
    }

    static byte[] unhex(String hex) {
        byte[] out = new byte[hex.length() / 2];
        for (int i = 0; i < out.length; i++) out[i] = (byte) Integer.parseInt(hex.substring(i * 2, i * 2 + 2), 16);
        return out;
    }
}
