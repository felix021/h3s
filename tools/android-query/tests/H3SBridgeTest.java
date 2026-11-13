import cn.felix021.h3squery.EqFrame;
import cn.felix021.h3squery.EqFrame.EqBand;
import cn.felix021.h3squery.EqFrame.Parsed;
import cn.felix021.h3squery.EqWritePolicy;
import cn.felix021.h3squery.H3SBridge;
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
        setAncBytes();
        setPromptVolumeBytes();
        policyConsistency();
        System.out.println("H3SBridgeTest passed: 门控、writeEq JSON 解析/组帧对拍/错误路径、setAnc/setPromptVolume 字节对照、EqWritePolicy 一致性");
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

    /** bridge 产物与写策略一致性：EQ/ANC/音量帧武装后被 EqWritePolicy 放行（路 P 扩白名单后）；未武装一律拒绝。 */
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
    }

    static byte[] unhex(String hex) {
        byte[] out = new byte[hex.length() / 2];
        for (int i = 0; i < out.length; i++) out[i] = (byte) Integer.parseInt(hex.substring(i * 2, i * 2 + 2), 16);
        return out;
    }
}
