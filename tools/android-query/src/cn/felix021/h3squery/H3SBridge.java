package cn.felix021.h3squery;

import android.webkit.JavascriptInterface;
import java.util.ArrayList;
import java.util.List;
import java.util.Locale;
import org.json.JSONArray;
import org.json.JSONException;
import org.json.JSONObject;

/**
 * WebView JS bridge（R3 独立 app 壳层，注入名 "h3s"）：把网页调用变成 RCSP 命令帧。
 *
 * 纯组帧层：只做「校验 + 组帧 + hex 回显」，不接真蓝牙/USB（真机传输留待用户授权联调，
 * 见 README「诚实标注」）。写方法全部走武装门控（对应 EqWritePolicy 的 armed 语义）：
 * 未武装一律返回 {ok:false}，不开任意字节写。
 *
 * 返回统一 JSON：成功 {ok:true, bytesHex:"fedcba..."}（小写连续 hex）；失败 {ok:false, error:"..."}。
 *
 * 帧格式照 docs/app-protocol.md / EqFrame：FE DC BA | C0 | FF | len_BE16 | SN | cmdid | data_len | data | EF。
 * ANC=FF/0x17 data=`mode scene strength`（docs/app-protocol.md §2.3 钉死）；
 * EQ=FF/0x22 走 EqFrame.buildEqCommand（docs/eq-cmd-0x22-layout.md 钉死）；
 * 提示音音量=FF/0x1d 值 3/6/8——帧形状 [未钉]，本类按 data=`01 value` 假设组帧，
 * 联调时需对照真机抓包修正（README 已标注）。
 *
 * JVM 单测（H3SBridgeTest）用 tests/stubs/ 下的 android.webkit.JavascriptInterface 与
 * org.json 最小桩编译；Android 构建走 android.jar + 内置 org.json。
 */
public class H3SBridge {
    public static final int CMD_ANC = 0x17;        // ANC 模式切换 cmdid
    public static final int CMD_PROMPT_VOLUME = 0x1d; // 提示音音量 cmdid
    /** 提示音音量合法档位（docs/app-protocol.md §2.4：3/6/8=最小/中等/最大）。 */
    public static final int[] PROMPT_VOLUME_LEVELS = {3, 6, 8};

    private boolean armed = false;

    /** 武装/解除写门控（对应 EqWritePolicy 的 armed 参数；页面开关调用）。 */
    @JavascriptInterface public void armWrite(boolean on) { armed = on; }

    /** 当前是否武装。 */
    @JavascriptInterface public boolean isArmed() { return armed; }

    /**
     * 组 EQ 写命令（FF/0x22）。bandsJson 是段数组，每段 {freq:Hz, q:x100, gain:centidB, iirType}，
     * q 缺省 100（Q=1.0）、iirType 缺省 255（未指定→固件兜底 band_pass）；数值允许小数，×100 后四舍五入。
     * 单位与 writeEq 的 masterGainCentidB 一致（centidB= dB×100），避免浮点歧义。
     */
    @JavascriptInterface
    public String writeEq(int eqType, int masterGainCentidB, String bandsJson) {
        if (!armed) return err("未武装：先调 h3s.armWrite(true)（写门控，未武装拒绝一切写命令）");
        List<EqFrame.EqBand> bands;
        try {
            bands = parseBands(bandsJson);
        } catch (JSONException e) {
            return err("bandsJson 不是合法 JSON 数组: " + e.getMessage());
        } catch (IllegalArgumentException e) {
            return err(e.getMessage());
        }
        try {
            return ok(EqFrame.buildEqCommand(eqType, masterGainCentidB, bands));
        } catch (IllegalArgumentException e) {
            return err(e.getMessage()); // 段数/取值越界由 EqFrame 统一校验
        }
    }

    /** 组 ANC 切换命令（FF/0x17，data=`mode scene strength`）。mode 1/2/3=降噪/正常/通透（QCY 线协议）。 */
    @JavascriptInterface
    public String setAnc(int mode, int scene, int strength) {
        if (!armed) return err("未武装：先调 h3s.armWrite(true)（写门控，未武装拒绝一切写命令）");
        if (mode < 1 || mode > 3) return err("mode 非法（QCY 线协议 1=降噪/2=正常/3=通透）: " + mode);
        if (scene < 0 || scene > 255) return err("scene 超出 u8 范围: " + scene);
        if (strength < 0 || strength > 255) return err("strength 超出 u8 范围: " + strength);
        return ok(buildCustomCommand(CMD_ANC, new byte[]{(byte) mode, (byte) scene, (byte) strength}));
    }

    /** 组提示音音量命令（FF/0x1d，值 3/6/8）。帧形状未钉：按 data=`01 value` 假设组帧，联调需对照。 */
    @JavascriptInterface
    public String setPromptVolume(int level) {
        if (!armed) return err("未武装：先调 h3s.armWrite(true)（写门控，未武装拒绝一切写命令）");
        boolean legal = false;
        for (int v : PROMPT_VOLUME_LEVELS) if (v == level) { legal = true; break; }
        if (!legal) return err("提示音音量档位非法（仅 3/6/8=最小/中等/最大）: " + level);
        return ok(buildCustomCommand(CMD_PROMPT_VOLUME, new byte[]{0x01, (byte) level}));
    }

    /** 解析段数组 JSON → EqBand 列表；JSON 语法错误抛 JSONException，字段问题抛 IllegalArgumentException。 */
    static List<EqFrame.EqBand> parseBands(String bandsJson) throws JSONException {
        if (bandsJson == null) throw new IllegalArgumentException("bandsJson 不能为 null");
        JSONArray arr = new JSONArray(bandsJson);
        List<EqFrame.EqBand> bands = new ArrayList<EqFrame.EqBand>();
        for (int i = 0; i < arr.length(); i++) {
            JSONObject o = arr.getJSONObject(i);
            int freq = reqInt(o, "freq", i);
            int gain = reqInt(o, "gain", i);
            int q = optInt(o, "q", i, 100);                        // 缺省 Q=1.0
            int iir = optInt(o, "iirType", i, EqFrame.IIR_DEFAULT); // 缺省未指定
            bands.add(new EqFrame.EqBand(freq, q, gain, iir));
        }
        return bands;
    }

    private static int reqInt(JSONObject o, String field, int index) {
        if (!o.has(field)) throw new IllegalArgumentException("第 " + index + " 段缺字段 " + field);
        return roundField(o, field, index, true, 0);
    }

    private static int optInt(JSONObject o, String field, int index, int fallback) {
        if (!o.has(field) || o.isNull(field)) return fallback;
        return roundField(o, field, index, false, fallback);
    }

    /** 字段值 round 成整数（单位已是 ×100 整数，round 只容错小数尾差）；非数值（NaN）明确报错，不静默取 0。 */
    private static int roundField(JSONObject o, String field, int index, boolean required, int fallback) {
        double d = o.optDouble(field, Double.NaN);
        if (Double.isNaN(d)) {
            if (!required) return fallback;
            throw new IllegalArgumentException("第 " + index + " 段字段 " + field + " 不是数值");
        }
        return (int) Math.round(d);
    }

    /**
     * 组通用 RCSP 自定义命令帧（SN=0）：FE DC BA | C0 | FF | len_BE16 | 00 | cmdid | data_len | data | EF。
     * 复用 EqFrame 的帧形状常量，不动 EqFrame 本身。
     */
    static byte[] buildCustomCommand(int cmdId, byte[] data) {
        return buildCustomCommand(0, cmdId, data);
    }

    static byte[] buildCustomCommand(int sn, int cmdId, byte[] data) {
        if (data == null) throw new IllegalArgumentException("data 不能为 null");
        if (cmdId < 0 || cmdId > 255) throw new IllegalArgumentException("cmdid 超出 u8 范围: " + cmdId);
        int dataLen = data.length;                       // u8
        int bodyLen = 3 + dataLen;
        byte[] frame = new byte[8 + bodyLen];
        frame[0] = (byte) 0xfe; frame[1] = (byte) 0xdc; frame[2] = (byte) 0xba;
        frame[3] = (byte) EqFrame.FLAGS_CMD;             // C0：命令需响应
        frame[4] = (byte) EqFrame.OPCODE_CUSTOM;         // FF：自定义命令
        frame[5] = (byte) (bodyLen >> 8); frame[6] = (byte) bodyLen;
        frame[7] = (byte) sn;
        frame[8] = (byte) cmdId;
        frame[9] = (byte) dataLen;
        System.arraycopy(data, 0, frame, 10, dataLen);
        frame[frame.length - 1] = (byte) 0xef;
        return frame;
    }

    public static String hex(byte[] data) {
        StringBuilder s = new StringBuilder(data.length * 2);
        for (byte b : data) s.append(String.format(Locale.ROOT, "%02x", b & 0xff));
        return s.toString();
    }

    /** Android org.json 的 put 声明受检 JSONException，但 put(String, int/boolean/String) 实际不会抛；兜底手拼保证总返回 JSON。 */
    private static String ok(byte[] frame) {
        try { return new JSONObject().put("ok", true).put("bytesHex", hex(frame)).toString(); }
        catch (JSONException impossible) { return "{\"ok\":true,\"bytesHex\":\"" + hex(frame) + "\"}"; }
    }

    private static String err(String message) {
        try { return new JSONObject().put("ok", false).put("error", message).toString(); }
        catch (JSONException impossible) { return "{\"ok\":false}"; }
    }
}
