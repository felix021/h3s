import cn.felix021.h3squery.EqFrame;
import cn.felix021.h3squery.EqFrame.EqBand;
import cn.felix021.h3squery.EqFrame.Parsed;
import cn.felix021.h3squery.EqWritePolicy;
import cn.felix021.h3squery.H3SBridge;
import cn.felix021.h3squery.KeyRemapFrame;
import java.util.ArrayList;
import java.util.List;
import org.json.JSONObject;

/**
 * H3SBridge 纯 JVM 单测（照 EqFrameTest 风格）。bridge 方法只做「校验+组帧+JSON 返回」，
 * 全部可脱离 Android 测；WebView 壳（WebShellActivity）依赖 android.jar/WebView 运行时，
 * 纯 JVM 无法实例化，且其逻辑只有「加载 asset + 注入 bridge」，无业务分支，故不在此测——
 * 组帧/门控语义由本测试覆盖。org.json 与 @JavascriptInterface 用 tests/stubs/ 桩编译。
 */
public class H3SBridgeTest {
    static void expect(boolean b, String msg) { if (!b) throw new AssertionError(msg); }
    static void eq(String name, int a, int b) { if (a != b) throw new AssertionError(name + ": " + a + " != " + b); }
    static void bytes(String name, byte[] a, byte[] b) {
        if (!java.util.Arrays.equals(a, b))
            throw new AssertionError(name + ":\n  got " + H3SBridge.hex(a) + "\n  exp " + H3SBridge.hex(b));
    }

    static JSONObject result(String json) throws org.json.JSONException { return new JSONObject(json); }

    static String tenBandsJson() {
        return "[{\"freq\":32,\"q\":40,\"gain\":-1200,\"iirType\":0},{\"freq\":64,\"q\":120,\"gain\":-250},"
            + "{\"freq\":125,\"q\":80,\"gain\":0,\"iirType\":3},{\"freq\":250,\"q\":300,\"gain\":300},"
            + "{\"freq\":500,\"q\":250,\"gain\":-400,\"iirType\":4},{\"freq\":1000,\"q\":100,\"gain\":800},"
            + "{\"freq\":2000,\"q\":500,\"gain\":-1200},{\"freq\":4000,\"q\":1000,\"gain\":1500,\"iirType\":1},"
            + "{\"freq\":8000,\"q\":2000,\"gain\":-3000},{\"freq\":16000,\"q\":5,\"gain\":32767,\"iirType\":255}]";
    }

    public static void main(String[] args) throws Exception {
        armGate();
        writeEqOk();
        writeEqBadInput();
        bandsFromCompact();
        setAncBytes();
        setPromptVolumeBytes();
        keyRemapBytes();
        keyRemapGet();
        policyConsistency();
        audioModeAndReadStatus();
        readInfoAndParse();
        deviceMessageEvent();
        System.out.println("H3SBridgeTest passed: 门控、writeEq JSON 解析/组帧对拍/错误路径、DRIVE bands 紧凑串好坏负例（SIMB1G 崩溃回归）、"
            + "setAnc/setPromptVolume/setKeyRemap 字节对照、"
            + "getKeyRemap 默认/更新、EqWritePolicy 一致性、setAudioMode(0x2E)/readStatus 字节对照、"
            + "readInfo(03)/parseInfoPayload LTV 解析（固件 tab 版本展示）、device_message 事件");
    }

    /** readInfo（opcode 03 设备信息，只读）与 parseInfoPayload LTV 解析（固件 tab 版本展示）。 */
    static void readInfoAndParse() throws Exception {
        H3SBridge b = new H3SBridge();
        JSONObject r = result(b.readInfo());
        expect(r.getBoolean("ok"), "readInfo 应成功（只读不需武装）: " + r);
        bytes("readInfo 帧", H3SBridge.unhex(r.getString("bytesHex")),
            new byte[]{(byte)0xfe,(byte)0xdc,(byte)0xba,(byte)0xc0,0x03,0,6,0,
                (byte)0xff,(byte)0xff,(byte)0xff,(byte)0xff,0x00,(byte)0xef});
        expect(cn.felix021.h3squery.QueryPolicy.permitted(H3SBridge.unhex(r.getString("bytesHex"))),
            "readInfo 帧应过 QueryPolicy（03 全属性 mask 白名单）");
        expect(cn.felix021.h3squery.QueryPolicy.permitted(H3SBridge.buildReadInfoFrame(7)),
            "任意 SN 的 readInfo 帧都应过 QueryPolicy");
        // 03 回包载荷形状 [未钉]：属性表按 docs/protocol.md「设备信息和身份」构造
        // （05 版本码小端 0x0118=280、08 双备份 01 00 01、0A VID/PID 1C52 417C）。
        // 样例 A：带 `03 len` 前缀（0x17 回包 cmdid+len 先例的对称形状）。
        JSONObject info = result(H3SBridge.parseInfoPayload(
            H3SBridge.unhex("030f0502180108030100010a041c52417c")));
        expect(info.getBoolean("ok"), "带前缀样例应解析: " + info);
        expect(info.getInt("version") == 280, "版本码 280: " + info);
        expect("0.1.1.8".equals(info.getString("versionText")), "版本文本换算 0.1.1.8: " + info);
        expect(info.getBoolean("doubleBackup"), "双备份能力位");
        expect(info.getInt("vid") == 7250 && info.getInt("pid") == 16764, "VID/PID 大端识别: " + info);
        expect(info.getBoolean("identityKnown"), "已知 H3S 身份");
        // 样例 B：裸 LTV（无前缀变体也兼容）。
        JSONObject bare = result(H3SBridge.parseInfoPayload(
            H3SBridge.unhex("0502180108030100010a041c52417c")));
        expect(bare.getBoolean("ok") && bare.getInt("version") == 280, "裸 LTV 样例应解析: " + bare);
        // 0A 字节序未留档：小端镜像 52 1C 7C 41 同样识别为 H3S 身份。
        JSONObject le = result(H3SBridge.parseInfoPayload(
            H3SBridge.unhex("030f0502180108030100010a04521c7c41")));
        expect(le.getBoolean("ok") && le.getBoolean("identityKnown")
            && le.getInt("vid") == 7250 && le.getInt("pid") == 16764, "0A 小端镜像识别: " + le);
        // 版本 281=0x0119 先例（换算 0.1.1.9）。
        JSONObject v281 = result(H3SBridge.parseInfoPayload(H3SBridge.unhex("030405021901")));
        expect(v281.getBoolean("ok") && v281.getInt("version") == 281
            && "0.1.1.9".equals(v281.getString("versionText")), "0x0119 换算: " + v281);
        // 负例：空/过短/垃圾流/电量载荷（无 05 属性）都不得猜出版本。
        expect(!result(H3SBridge.parseInfoPayload(null)).getBoolean("ok"), "null 载荷拒绝");
        expect(!result(H3SBridge.parseInfoPayload(new byte[0])).getBoolean("ok"), "空载荷拒绝");
        expect(!result(H3SBridge.parseInfoPayload(H3SBridge.unhex("ffff"))).getBoolean("ok"), "垃圾拒绝");
        expect(!result(H3SBridge.parseInfoPayload(H3SBridge.unhex("2f03006400"))).getBoolean("ok"),
            "电量载荷（无版本属性）不得解析为设备信息");
        // 页面入口 parseInfo(String)：hex 校验 + 大小写。
        expect(result(b.parseInfo("030f0502180108030100010a041c52417c")).getBoolean("ok"), "hex 入口解析");
        expect(result(b.parseInfo("030F0502180108030100010A041C52417C")).getBoolean("ok"), "大写 hex 解析");
        expect(!result(b.parseInfo("030f0")).getBoolean("ok"), "奇数长度 hex 拒绝");
        expect(!result(b.parseInfo("zz")).getBoolean("ok"), "非 hex 拒绝");
    }

    /** setAudioMode（FF/0x2E，音乐 1/游戏 3/电影 4）与 readStatus（只读四点）字节对照。 */
    static void audioModeAndReadStatus() throws Exception {
        H3SBridge b = new H3SBridge();
        expect(!result(b.setAudioMode(3)).getBoolean("ok"), "未武装 setAudioMode 应失败");
        b.armWrite(true);
        expect(!result(b.setAudioMode(2)).getBoolean("ok"), "mode=2（电影/游戏之间无此值）应拒绝");
        bytes("setAudioMode(3) 帧", H3SBridge.unhex(result(b.setAudioMode(3)).getString("bytesHex")),
            H3SBridge.buildCustomCommand(0, 0x2e, new byte[]{3}));
        bytes("setAudioMode(1) 帧", H3SBridge.unhex(result(b.setAudioMode(1)).getString("bytesHex")),
            H3SBridge.buildCustomCommand(0, 0x2e, new byte[]{1}));
        expect(EqWritePolicy.permitted(H3SBridge.buildCustomCommand(0, 0x2e, new byte[]{4}), true),
            "0x2E 帧（模式 4）应过写策略");
        expect(!EqWritePolicy.permitted(H3SBridge.buildCustomCommand(0, 0x2e, new byte[]{2}), true),
            "0x2E 帧（模式 2）应被写策略拒绝");
        expect(!EqWritePolicy.permitted(H3SBridge.buildCustomCommand(0, 0x2e, new byte[]{3, 0}), true),
            "0x2E 帧（len 2 假设外）应被写策略拒绝");
        // readStatus：只读、不要求武装；帧形状=QueryPolicy §battery 白名单（body=SN+FE 01 cmd）
        expect(!result(b.readStatus(5)).getBoolean("ok"), "readStatus 非法 cmd 应拒绝");
        JSONObject r = result(b.readStatus(0x2f));
        expect(r.getBoolean("ok"), "readStatus(0x2f) 应成功（只读不需武装）: " + r);
        bytes("readStatus(0x2f) 帧", H3SBridge.unhex(r.getString("bytesHex")),
            new byte[]{(byte)0xfe,(byte)0xdc,(byte)0xba,(byte)0xc0,(byte)0xff,0,4,0,(byte)0xfe,0x01,0x2f,(byte)0xef});
        expect(cn.felix021.h3squery.QueryPolicy.permitted(H3SBridge.unhex(r.getString("bytesHex"))),
            "readStatus 帧应过 QueryPolicy");
        bytes("readStatus(0x17) 帧", H3SBridge.unhex(result(b.readStatus(0x17)).getString("bytesHex")),
            H3SBridge.buildReadStatusFrame(0, 0x17));
        // SIMB1E 读环 E2E：0x22 EQ 查询加入白名单（回包形状 22 n …，n=3+7×段数）
        JSONObject r22 = result(b.readStatus(0x22));
        expect(r22.getBoolean("ok"), "readStatus(0x22) 应成功: " + r22);
        expect(cn.felix021.h3squery.QueryPolicy.permitted(H3SBridge.unhex(r22.getString("bytesHex"))),
            "readStatus(0x22) 帧应过 QueryPolicy");
    }

    /** device_message 事件：未登记 SN 的响应帧透出给页面（只读查询回包的收口）。 */
    static void deviceMessageEvent() throws Exception {
        final List<String> events = new ArrayList<String>();
        final List<H3sTransportFake.Listener> listeners = new ArrayList<H3sTransportFake.Listener>();
        H3sTransportFake t = new H3sTransportFake(listeners);
        H3SBridge b = new H3SBridge(t, new H3SBridge.ResultSink() {
            public void emit(String json) { events.add(json); }
        });
        expect(t.isReady(), "假传输就绪");
        JSONObject r = result(b.readStatus(0x2f));
        expect(r.getBoolean("ok") && r.getBoolean("sent"), "readStatus 经 transport 排队: " + r);
        // 设备回包（SN 未登记）：fe dc ba | 00 | 00 | 00 07 | status=00 sn=00 | 2f 03 50 c0 00 | ef
        byte[] resp = H3SBridge.unhex("fedcba" + "00" + "00" + "0007" + "00" + "00" + "2f0350c000" + "ef");
        listeners.get(0).onFrame(resp);
        expect(events.size() == 1, "应恰好一条事件: " + events);
        JSONObject e = new JSONObject(events.get(0));
        expect("device_message".equals(e.getString("event")), "事件名 device_message: " + events);
        expect("2f0350c000".equals(e.getString("payloadHex")), "payload 透传: " + e);
        expect(e.getInt("status") == 0 && e.getInt("sn") == 0, "status/sn 解析");
    }

    /** 最小假传输：就绪、write 即回调成功、listener 可被测试捕获。 */
    static class H3sTransportFake implements cn.felix021.h3squery.H3sTransport {
        interface Listener { void onFrame(byte[] frame); }
        final List<Listener> captured;
        cn.felix021.h3squery.H3sTransport.Listener real;
        H3sTransportFake(List<Listener> captured) { this.captured = captured; }
        public void setListener(cn.felix021.h3squery.H3sTransport.Listener l) {
            real = l;
            final List<Listener> cap = captured;
            cap.add(new Listener() { public void onFrame(byte[] frame) { l.onFrame(frame); } });
        }
        public void connect() { }
        public void disconnect() { }
        public boolean isReady() { return true; }
        public int mtu() { return 247; }
        public void write(byte[] frame, FrameCallback cb) { cb.onDone(true, null); }
    }

    /** 武装门控：未武装一切写方法拒绝，armWrite 翻转后放行。 */
    static void armGate() throws Exception {
        H3SBridge b = new H3SBridge();
        expect(!b.isArmed(), "初始应未武装");
        String r = b.writeEq(1, 0, "[]");
        expect(!result(r).getBoolean("ok"), "未武装 writeEq 应失败");
        expect(result(r).getString("error").contains("武装"), "错误信息应提到武装: " + r);
        expect(!result(b.setAnc(1, 1, 2)).getBoolean("ok"), "未武装 setAnc 应失败");
        expect(!result(b.setPromptVolume(3)).getBoolean("ok"), "未武装 setPromptVolume 应失败");
        expect(!result(b.setKeyRemap(1793, 1)).getBoolean("ok"), "未武装 setKeyRemap 应失败");
        expect(result(b.setKeyRemap(1793, 1)).getString("error").contains("武装"), "未武装错误信息应提到武装");
        expect(!result(b.setKeyRemapCustom(1793, 1)).getBoolean("ok"), "未武装 setKeyRemapCustom 应失败");
        b.armWrite(true);
        expect(b.isArmed(), "armWrite(true) 后应武装");
        expect(result(b.writeEq(1, 0, "[]")).getBoolean("ok"), "武装后 writeEq 应成功");
        b.armWrite(false);
        expect(!b.isArmed(), "armWrite(false) 后应解除");
        expect(!result(b.setAnc(2, 0, 0)).getBoolean("ok"), "解除后 setAnc 应再次失败");
    }

    /** writeEq 成功路径：与 EqFrame.buildEqCommand 逐字节对拍 + parseEqCommand 往返。 */
    static void writeEqOk() throws Exception {
        H3SBridge b = new H3SBridge();
        b.armWrite(true);
        List<EqBand> bands = new ArrayList<EqBand>();
        int[] freq = {32, 64, 125, 250, 500, 1000, 2000, 4000, 8000, 16000};
        int[] q = {40, 120, 80, 300, 250, 100, 500, 1000, 2000, 5};
        int[] gain = {-1200, -250, 0, 300, -400, 800, -1200, 1500, -3000, 32767};
        int[] iir = {0, 255, 3, 255, 4, 255, 255, 1, 255, 255}; // JSON 里省略的段走默认 255
        for (int i = 0; i < 10; i++) bands.add(new EqBand(freq[i], q[i], gain[i], iir[i]));
        String r = b.writeEq(2, -400, tenBandsJson());
        JSONObject o = result(r);
        expect(o.getBoolean("ok"), "writeEq 应成功: " + r);
        byte[] frame = unhex(o.getString("bytesHex"));
        bytes("与 EqFrame 对拍", frame, EqFrame.buildEqCommand(2, -400, bands));
        Parsed p = EqFrame.parseEqCommand(frame);
        eq("往返 eqType", 2, p.eqType);
        eq("往返 masterGain", -400, p.masterGainCentidB);
        eq("往返段数", 10, p.bands.size());
        for (int i = 0; i < 10; i++) {
            EqBand x = p.bands.get(i);
            eq("往返 freq[" + i + "]", freq[i], x.freq);
            eq("往返 q[" + i + "]", q[i], x.q);
            eq("往返 gain[" + i + "]", gain[i], x.gain);
            eq("往返 iir[" + i + "]", iir[i], x.iirType);
        }
        // 空段列表：仅 3B 头
        JSONObject empty = result(b.writeEq(1, -400, "[]"));
        expect(empty.getBoolean("ok"), "空段应成功");
        eq("空段帧长", 14, unhex(empty.getString("bytesHex")).length);
    }

    /** writeEq 错误路径：坏 JSON、字段问题、越界。全部返回 {ok:false}，不抛异常。 */
    static void writeEqBadInput() throws Exception {
        H3SBridge b = new H3SBridge();
        b.armWrite(true);
        String[][] cases = {
            {"not json", "非 JSON 文本"},
            {"{\"a\":1}", "非数组"},
            {"[{\"gain\":0}]", "缺 freq"},
            {"[{\"freq\":1000}]", "缺 gain"},
            {"[{\"freq\":\"x\",\"gain\":0}]", "freq 非数值"},
            {"[{}]", "空对象"},
            {"[{\"freq\":1000,\"gain\":0,\"q\":32768}]", "q 超 i16"},
            {"[{\"freq\":1000,\"gain\":32768}]", "gain 超 i16"},
            {"[{\"freq\":1000,\"gain\":0,\"iirType\":5}]", "iir=5 非法"},
            {"[{\"freq\":1000,\"gain\":0},{\"freq\":1000,\"gain\":0},{\"freq\":1000,\"gain\":0},{\"freq\":1000,\"gain\":0},{\"freq\":1000,\"gain\":0},{\"freq\":1000,\"gain\":0},{\"freq\":1000,\"gain\":0},{\"freq\":1000,\"gain\":0},{\"freq\":1000,\"gain\":0},{\"freq\":1000,\"gain\":0},{\"freq\":1000,\"gain\":0}]", "11 段超限"},
            {null, "null 输入"},
        };
        for (String[] c : cases) {
            JSONObject o = result(b.writeEq(1, 0, c[0]));
            expect(!o.getBoolean("ok"), "应失败: " + c[1]);
            expect(o.has("error") && !o.getString("error").isEmpty(), "错误信息非空: " + c[1] + " -> " + o);
        }
        // masterGain 越界由 EqFrame 校验拦截
        expect(!result(b.writeEq(1, 32768, "[]")).getBoolean("ok"), "masterGain 超 i16 应失败");
        // 小数容错：字段单位已是 ×100 整数（centidB），round 只容错小数尾差——页面负责 dB×100。
        // Math.round(250.4)=250、Math.round(-250.4)=-250（半值向上，取无歧义值）。
        JSONObject dec = result(b.writeEq(1, 0, "[{\"freq\":1000,\"q\":70.4,\"gain\":250.4}]"));
        expect(dec.getBoolean("ok"), "小数输入应成功: " + dec);
        Parsed pd = EqFrame.parseEqCommand(unhex(dec.getString("bytesHex")));
        eq("小数 gain", 250, pd.bands.get(0).gain);
        eq("小数 q", 70, pd.bands.get(0).q);
        JSONObject neg = result(b.writeEq(1, 0, "[{\"freq\":1000,\"gain\":-250.4}]"));
        expect(neg.getBoolean("ok"), "负小数应成功");
        eq("负小数 gain", -250, EqFrame.parseEqCommand(unhex(neg.getString("bytesHex"))).bands.get(0).gain);
    }

    /** DRIVE bands 紧凑串解析（SIMB1G 崩溃回归）：坏 bands 负例必须返回 null 而非抛
     *  NumberFormatException——WebShellActivity 收到 null 即记日志忽略该次驱动，
     *  不调 writeEq（无组帧/传输副作用），app 进程存活。 */
    static void bandsFromCompact() {
        // 好串：与页面 eqSend 字段一致的紧凑形状
        expect(H3SBridge.bandsJsonFromCompact("1000,100,-100,0;2000,150,-50,1").equals(
                   "[{\"freq\":1000,\"q\":100,\"gain\":-100,\"iirType\":0},{\"freq\":2000,\"q\":150,\"gain\":-50,\"iirType\":1}]"),
               "好串解析");
        // 空白容错（原行为保留）
        expect(H3SBridge.bandsJsonFromCompact(" 1000 , 100 , -100 , 0 ").equals(
                   "[{\"freq\":1000,\"q\":100,\"gain\":-100,\"iirType\":0}]"),
               "字段空白容错");
        // 段形状不符（逗号数≠3）：跳过该段、不视为坏串（原行为保留）
        expect(H3SBridge.bandsJsonFromCompact("garbage;1000,100,-100,0").equals(
                   "[{\"freq\":1000,\"q\":100,\"gain\":-100,\"iirType\":0}]"),
               "坏形状段跳过");
        // null 输入 → null（WebShellActivity 照旧把 null 交给 writeEq 走既有的 err 路径）
        expect(H3SBridge.bandsJsonFromCompact(null) == null, "null 输入");
        // 转义残留负例（SIMB1G 现场）：反斜杠/引号/非数字混入字段 → 整串放弃返回 null，
        // 不抛异常（进程存活）、不产出半成品 JSON（无副作用）。任一段脏即整体忽略。
        String[] bad = {
            "1000\\,100,-100,0",                       // 分隔符转义残留
            "\"1000\",100,-100,0",                     // 引号残留
            "1000,100,-100,0;2000,1o0,-50,1",          // 段内非数字（一坏全弃）
            "1000,100,-100,0x",                        // 16 进制残留（parseInt 十进制拒绝）
            "-,100,-100,0",
        };
        for (String c : bad) {
            String got = H3SBridge.bandsJsonFromCompact(c);
            expect(got == null, "坏串应返回 null: " + c + " -> " + got);
        }
    }

    /** setAnc 三态：逐字节对照 docs/app-protocol.md §2.3 钉死载荷；mode 越界拒绝。 */
    static void setAncBytes() throws Exception {
        H3SBridge b = new H3SBridge();
        b.armWrite(true);
        // 默认降噪 17 03 01 01 02（完整帧：FE DC BA C0 FF 00 06 | 00 | 17 | 03 | 01 01 02 | EF）
        JSONObject anc = result(b.setAnc(1, 1, 2));
        expect(anc.getBoolean("ok"), "setAnc 应成功: " + anc);
        bytes("默认降噪", unhex(anc.getString("bytesHex")),
            unhex("fedcbac0ff0006" + "00" + "1703" + "010102" + "ef"));
        // 默认通透 17 03 03 01 04
        bytes("默认通透", unhex(result(b.setAnc(3, 1, 4)).getString("bytesHex")),
            unhex("fedcbac0ff0006" + "00" + "1703" + "030104" + "ef"));
        // 正常模式 17 03 02 00 00
        bytes("正常模式", unhex(result(b.setAnc(2, 0, 0)).getString("bytesHex")),
            unhex("fedcbac0ff0006" + "00" + "1703" + "020000" + "ef"));
        // mode 越界（0/4）、scene/strength 超 u8
        expect(!result(b.setAnc(0, 1, 2)).getBoolean("ok"), "mode=0 应拒绝");
        expect(!result(b.setAnc(4, 1, 2)).getBoolean("ok"), "mode=4 应拒绝");
        expect(!result(b.setAnc(1, -1, 2)).getBoolean("ok"), "scene=-1 应拒绝");
        expect(!result(b.setAnc(1, 1, 256)).getBoolean("ok"), "strength=256 应拒绝");
    }

    /** setPromptVolume：3/6/8 三档字节（路 P 钉死 data=`value 00`，固件只读 data[0]）；其他值拒绝。 */
    static void setPromptVolumeBytes() throws Exception {
        H3SBridge b = new H3SBridge();
        b.armWrite(true);
        int[] levels = {3, 6, 8};
        for (int v : levels) {
            JSONObject o = result(b.setPromptVolume(v));
            expect(o.getBoolean("ok"), "档位 " + v + " 应成功: " + o);
            bytes("音量 " + v, unhex(o.getString("bytesHex")),
                unhex("fedcbac0ff0005" + "00" + "1d02" + String.format("%02x00", v) + "ef"));
        }
        expect(!result(b.setPromptVolume(5)).getBoolean("ok"), "档位 5 应拒绝");
        expect(!result(b.setPromptVolume(0)).getBoolean("ok"), "档位 0 应拒绝");
        expect(!result(b.setPromptVolume(9)).getBoolean("ok"), "档位 9 应拒绝");
    }

    /** setKeyRemap/setKeyRemapAdv/setKeyRemapCustom 字节对照：默认 wire=批量同步 0xC0 item
     *  cmdid 2（MOREFUNC 钉死）、候选 A=ADV type2（C2+mask+三元组）、候选 B=FF/0x2b 三元组；
     *  eventID 面板全集+长按假设、SN=0（未注入 transport）；非法 eventID/action 拒绝。 */
    static void keyRemapBytes() throws Exception {
        H3SBridge b = new H3SBridge();
        b.armWrite(true);
        // 默认 wire（批量同步）：多功能双击→模式切换（eventID 1794=0x0702, action 7）
        JSONObject batch = result(b.setKeyRemap(1794, 7));
        expect(batch.getBoolean("ok"), "setKeyRemap 应成功: " + batch);
        bytes("默认批量同步帧", unhex(batch.getString("bytesHex")),
            unhex("fedcbac0c00006" + "00" + "0402" + "070207" + "ef"));
        bytes("与 KeyRemapFrame 直组对拍", unhex(batch.getString("bytesHex")),
            KeyRemapFrame.buildBatchSyncCommand(0, 7, 2, 7));
        // 候选 A wire（ADV type2）
        JSONObject adv = result(b.setKeyRemapAdv(1794, 7));
        expect(adv.getBoolean("ok"), "setKeyRemapAdv 应成功: " + adv);
        bytes("ADV type2 帧", unhex(adv.getString("bytesHex")),
            unhex("fedcbac0c20008" + "00" + "00000004" + "070207" + "ef"));
        // 候选 B wire（FF/0x2b，固件注册表无对应——实发必无回执，仅对照）：音量- 单击→音量-
        JSONObject custom = result(b.setKeyRemapCustom(769, 6));
        expect(custom.getBoolean("ok"), "setKeyRemapCustom 应成功: " + custom);
        bytes("0x2b 帧", unhex(custom.getString("bytesHex")),
            unhex("fedcbac0ff0006" + "00" + "2b03" + "030106" + "ef"));
        // 长按假设 eventID（低字节 5）+ 固件侧 action 11（ANC）——默认批量同步 wire
        JSONObject hold = result(b.setKeyRemap(0x0705, 11));
        expect(hold.getBoolean("ok"), "长按 eventID 应成功: " + hold);
        bytes("长按 ANC 帧（批量同步）", unhex(hold.getString("bytesHex")),
            KeyRemapFrame.buildBatchSyncCommand(0, 7, 5, 11));
        // 全表写帧（45B）与 C1 读帧
        bytes("全表写帧", KeyRemapFrame.buildBatchSyncTableCommand(0, new byte[45]),
            unhex("fedcbac0c0" + "0030" + "00" + "2e02" + "00".repeat(45) + "ef"));
        bytes("C1 读帧", KeyRemapFrame.buildBatchReadCommand(0),
            unhex("fedcbac0c10005" + "00" + "00000004" + "ef"));
        // 非法输入：key/gesture 越界的 eventID、action 8/12/-1
        int[] badEvents = {0x0901, 0x0300, 0x0306, 0x0311, -1, 0x10000};
        for (int ev : badEvents)
            expect(!result(b.setKeyRemap(ev, 1)).getBoolean("ok"), "eventID " + ev + " 应拒绝");
        int[] badActions = {8, 9, 12, -1};
        for (int a : badActions)
            expect(!result(b.setKeyRemap(1793, a)).getBoolean("ok"), "action " + a + " 应拒绝");
        expect(result(b.setKeyRemap(1793, 8)).getString("error").contains("action"), "action 错误信息应说明取值域");
    }

    /** getKeyRemap：初始=面板 defaultFun（长按 -1 未知），setKeyRemap 成功后更新、失败不更新。 */
    static void keyRemapGet() throws Exception {
        H3SBridge b = new H3SBridge();
        JSONObject init = result(b.getKeyRemap());
        expect(init.getBoolean("ok"), "getKeyRemap 应成功");
        JSONObject map = init.getJSONObject("map");
        eq("默认 音量- 单击", 6, map.getInt("769"));
        eq("默认 音量+ 单击", 5, map.getInt("1281"));
        eq("默认 多功能 单击", 1, map.getInt("1793"));
        eq("默认 多功能 双击", 7, map.getInt("1794"));
        eq("默认 多功能 三击", 4, map.getInt("1795"));
        eq("默认 多功能 四击", 0, map.getInt("1796"));
        // map 键=eventID 十进制字符串；长按条目 -1=未知（App 面板无长按条目）
        eq("长按 音量- 未知（0x0305=773）", -1, map.getInt("773"));
        eq("长按 音量+ 未知（0x0505=1285）", -1, map.getInt("1285"));
        eq("长按 多功能 未知（0x0705=1797）", -1, map.getInt("1797"));
        b.armWrite(true);
        expect(result(b.setKeyRemap(0x0305, 2)).getBoolean("ok"), "长按改上一曲应成功");
        eq("set 后更新", 2, result(b.getKeyRemap()).getJSONObject("map").getInt("773"));
        expect(!result(b.setKeyRemap(0x0305, 9)).getBoolean("ok"), "非法 action 应失败");
        eq("失败不更新", 2, result(b.getKeyRemap()).getJSONObject("map").getInt("773"));
    }

    /** bridge 产物与写策略一致性：EQ/ANC/音量/按键重映射帧武装后被 EqWritePolicy 放行；未武装一律拒绝。 */
    static void policyConsistency() throws Exception {
        H3SBridge b = new H3SBridge();
        b.armWrite(true);
        byte[] eq = unhex(result(b.writeEq(1, 0, "[{\"freq\":1000,\"gain\":-800}]")).getString("bytesHex"));
        expect(!EqWritePolicy.permitted(eq, false), "EQ 帧未武装应被策略拒绝");
        expect(EqWritePolicy.permitted(eq, true), "EQ 帧武装后应被 EqWritePolicy 放行");
        byte[] anc = unhex(result(b.setAnc(1, 1, 2)).getString("bytesHex"));
        expect(!EqWritePolicy.permitted(anc, false), "ANC 帧未武装应被策略拒绝");
        expect(EqWritePolicy.permitted(anc, true), "ANC 帧武装后应被 EqWritePolicy 放行（路 P 扩白名单）");
        byte[] vol = unhex(result(b.setPromptVolume(6)).getString("bytesHex"));
        expect(!EqWritePolicy.permitted(vol, false), "提示音帧未武装应被策略拒绝");
        expect(EqWritePolicy.permitted(vol, true), "提示音帧武装后应被 EqWritePolicy 放行（路 P 扩白名单）");
        byte[] remapAdv = unhex(result(b.setKeyRemap(1794, 7)).getString("bytesHex"));
        expect(!EqWritePolicy.permitted(remapAdv, false), "按键重映射批量同步帧未武装应被策略拒绝");
        expect(EqWritePolicy.permitted(remapAdv, true), "按键重映射批量同步帧武装后应被 EqWritePolicy 放行");
        byte[] remapCustom = unhex(result(b.setKeyRemapCustom(769, 6)).getString("bytesHex"));
        expect(!EqWritePolicy.permitted(remapCustom, false), "按键重映射 0x2b 帧未武装应被策略拒绝");
        expect(EqWritePolicy.permitted(remapCustom, true), "按键重映射 0x2b 帧武装后应被 EqWritePolicy 放行");
    }

    static byte[] unhex(String hex) {
        byte[] out = new byte[hex.length() / 2];
        for (int i = 0; i < out.length; i++) out[i] = (byte) Integer.parseInt(hex.substring(i * 2, i * 2 + 2), 16);
        return out;
    }
}
