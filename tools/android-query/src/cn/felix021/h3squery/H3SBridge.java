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
 * 两种形态（向后兼容）：
 *   - 未注入 transport（无参构造）：纯组帧层，只做「校验 + 组帧 + hex 回显」，不发送——
 *     现有测试与离线演示页行为不变。
 *   - 注入 H3sTransport（路 Z 传输接线）：校验 + 组帧（SN 自管理）→ EqWritePolicy 放行检查
 *     → transport.write 排队串行发送 → notify 响应按 SN 匹配 → 结果经 ResultSink 推回 JS。
 *
 * 异步出口选「seq + 事件回调」而不是「JS 回调函数入参」：Android WebView 的
 * @JavascriptInterface 无法把 JS function 传进 Java（跨界后只剩不可调用的对象），
 * 页面侧唯一可靠的回推方式是壳 evaluateJavascript 调页面注册的全局处理器；
 * 方法立即返回 {ok,sent,seq,sn,bytesHex}，页面按 seq 匹配 ResultSink 推的
 * {"event":"response","seq":...} 事件——这就是 WebView 生态的 promise id 模式。
 *
 * 写方法全部走武装门控（对应 EqWritePolicy 的 armed 语义）：未武装一律返回 {ok:false}，
 * 不开任意字节写。SN 为 u8 自增回绕（255→0），发出的命令按 SN 登记 pending，响应
 * body 以 status+SN 起（docs/protocol.md 实测）据此匹配。
 *
 * 返回统一 JSON：成功（未注入）{ok:true, bytesHex:"fedcba..."}（小写连续 hex）；
 * 成功（已注入）{ok:true, sent:true, seq, sn, bytesHex}；失败 {ok:false, error:"..."}。
 *
 * 帧格式照 docs/app-protocol.md / EqFrame：FE DC BA | C0 | FF | len_BE16 | SN | cmdid | data_len | data | EF。
 * ANC=FF/0x17 data=`mode scene strength`（docs/app-protocol.md §2.3 + docs/cmd-0x17-0x1d-layout.md 核对）；
 * EQ=FF/0x22 走 EqFrame.buildEqCommand（docs/eq-cmd-0x22-layout.md 钉死）；
 * 提示音音量=FF/0x1d 值 3/6/8，data=`value 00`——路 P 反汇编钉死（docs/cmd-0x17-0x1d-layout.md）：
 * 固件 setter 只读 data[0] 当音量值、要求 len>=2、忽略第二字节，值不钳（3/6/8 是面板层档位）；
 * 按键重映射走 KeyRemapFrame（docs/app-protocol.md §2.5 两条 wire 候选：默认 ADV type2
 * 「C2+mask+三元组」、setKeyRemapCustom 走 FF/0x2b 三元组——真机抓包定案前并存放行）。
 *
 * 线程模型：transport 回调约定在主线程（Android BLE 适配层保证；Loopback 单线程同步），
 * 本类不加锁。真机 BLE 适配层未实现（红线：不连真机），粘合清单见 README「传输接线骨架」。
 *
 * JVM 单测（H3SBridgeTest/H3sTransportTest）用 tests/stubs/ 下的 android.webkit.JavascriptInterface
 * 与 org.json 最小桩编译；Android 构建走 android.jar + 内置 org.json。
 */
public class H3SBridge {
    public static final int CMD_ANC = EqWritePolicy.CMD_ANC;              // ANC 模式切换 cmdid
    public static final int CMD_PROMPT_VOLUME = EqWritePolicy.CMD_PROMPT_VOLUME; // 提示音音量 cmdid
    public static final int CMD_KEY_REMAP = EqWritePolicy.CMD_KEY_REMAP;  // 按键重映射 cmdid（0x2b，候选 B）
    public static final int CMD_KEY_STORE = EqWritePolicy.CMD_KEY_STORE;  // R4 key-store cmdid（0x30）
    /** 提示音音量合法档位（docs/app-protocol.md §2.4：3/6/8=最小/中等/最大；固件接收层不钳）。 */
    public static final int[] PROMPT_VOLUME_LEVELS = EqWritePolicy.PROMPT_VOLUME_LEVELS;

    /** JS 事件出口：传输接线后异步结果的唯一出口（壳用 evaluateJavascript 调页面全局处理器）。 */
    public interface ResultSink { void emit(String json); }

    private static final class Pending {
        final int seq;
        Pending(int seq) { this.seq = seq; }
    }

    private boolean armed = false;
    private final H3sTransport transport;          // 可空：null=纯组帧（现状）
    private final ResultSink sink;                 // 可空：null 时事件丢弃（transport 非 null 时建议非空）
    private final java.util.HashMap<Integer, Pending> pendingBySn = new java.util.HashMap<Integer, Pending>();
    private final java.util.HashMap<Integer, Integer> keyRemap = buildDefaultKeyRemap();
    private int nextSn = 1;                        // u8 自增回绕；首条 SN=1（未注入路径恒 0，与旧行为一致）
    private int seqCounter = 0;

    /**
     * 按键映射的本地工作副本（getKeyRemap 读、setKeyRemap 成功后写）。默认值=控制面板
     * defaultFun（data/controlpanel_qcyh3s_16764.json：音量- 单击 6/音量+ 单击 5/多功能
     * 单击 1、双击 7、三击 4、四击 0）；长按条目值 -1=未知（App 面板无长按条目，eventID 与
     * 默认动作均 [未钉]）。真机读回（ADV type2 查询响应形状）未钉，先不伪造设备值。
     */
    private static java.util.HashMap<Integer, Integer> buildDefaultKeyRemap() {
        java.util.HashMap<Integer, Integer> m = new java.util.HashMap<Integer, Integer>();
        m.put(0x0301, 6);                          // 音量- 单击：音量-
        m.put(0x0501, 5);                          // 音量+ 单击：音量+
        m.put(0x0701, 1);                          // 多功能 单击：播放/暂停
        m.put(0x0702, 7);                          // 多功能 双击：模式切换（固件默认表 [7,2,7] 互证）
        m.put(0x0703, 4);                          // 多功能 三击：语音助手
        m.put(0x0704, 0);                          // 多功能 四击：无作用
        m.put(0x0305, -1);                         // 长按 [未钉]
        m.put(0x0505, -1);
        m.put(0x0705, -1);
        return m;
    }

    /** 纯组帧形态（现状）：不发送，组帧后返回 hex。 */
    public H3SBridge() { this(null, null); }

    /** 传输接线形态：write 类方法组帧后经 transport 串行发送，响应/事件经 sink 推回。 */
    public H3SBridge(H3sTransport transport, ResultSink sink) {
        this.transport = transport;
        this.sink = sink;
        if (transport != null) transport.setListener(new H3sTransport.Listener() {
            @Override public void onFrame(byte[] frame) { handleFrame(frame); }
            @Override public void onMtuChanged(int mtu) { emitEvent(put(eventJson("mtu"), "mtu", mtu)); }
            @Override public void onDisconnected(String reason) { emitEvent(put(eventJson("disconnected"), "reason", reason)); }
        });
    }

    /** 设备响应帧处理：解析 status+SN，匹配 pending 后经 sink 推回；未知 SN 忽略（迟到/主动通知）。 */
    private void handleFrame(byte[] frame) {
        if (frame == null || frame.length < 10) return;
        if ((frame[3] & 0xff) != 0x00) return;                     // 只认响应帧（flags=00）
        int bodyLen = ((frame[5] & 0xff) << 8) | (frame[6] & 0xff);
        if (frame.length != 8 + bodyLen || (frame[frame.length - 1] & 0xff) != 0xef) return;
        int status = frame[7] & 0xff;
        int sn = frame[8] & 0xff;
        Pending p = pendingBySn.remove(sn);
        if (p == null) return;
        emitEvent(put(put(put(put(put(eventJson("response"), "seq", p.seq), "ok", status == 0),
            "status", status), "sn", sn),
            "payloadHex", hex(java.util.Arrays.copyOfRange(frame, 9, frame.length - 1))));
    }

    private org.json.JSONObject eventJson(String event) { return put(new org.json.JSONObject(), "event", event); }

    private void emitEvent(org.json.JSONObject event) {
        if (sink != null) sink.emit(event.toString());
    }

    /** put 助手：Android org.json 的 put 声明受检 JSONException，但基本类型实际不会抛；集中兜底。 */
    private static JSONObject put(JSONObject o, String key, int value) {
        try { return o.put(key, value); } catch (JSONException impossible) { throw new RuntimeException(impossible); }
    }

    private static JSONObject put(JSONObject o, String key, boolean value) {
        try { return o.put(key, value); } catch (JSONException impossible) { throw new RuntimeException(impossible); }
    }

    private static JSONObject put(JSONObject o, String key, String value) {
        try { return o.put(key, value); } catch (JSONException impossible) { throw new RuntimeException(impossible); }
    }

    /** 取下一个 SN（u8 回绕）。 */
    private int allocSn() {
        int sn = nextSn;
        nextSn = (nextSn + 1) & 0xff;
        return sn;
    }

    /** 测试钩子：直接设定下一条命令的 SN（SN 回绕测试用）。 */
    public void setNextSnForTest(int sn) { nextSn = sn & 0xff; }

    /**
     * 组帧后的分发：未注入 transport 保持现状（返回 hex 不发送）；已注入则
     * 「连接检查 → EqWritePolicy 放行 → SN/seq 登记 → transport 排队串行发送」。
     * policy 检查是纵深防御（组帧源头本身受控、合法帧必过），钉住「发送链路必经策略」。
     */
    private String dispatch(byte[] frame, int sn) {
        if (transport == null) return ok(frame);
        if (!transport.isReady()) return err("transport 未连接（先 connect，等 onMtuChanged 就绪）");
        if (!EqWritePolicy.permitted(frame, true)) return err("写策略拒绝（帧形状不在 0x22/0x17/0x1d/0x2b/C2 type2 白名单）");
        final int seq = ++seqCounter;
        pendingBySn.put(sn, new Pending(seq));
        final int fSn = sn;
        transport.write(frame, new H3sTransport.FrameCallback() {
            @Override public void onDone(boolean done, String error) {
                if (done) return;                                   // 成功结果经 onFrame(SN 匹配) 推送
                pendingBySn.remove(fSn);
                emitEvent(put(put(eventJson("send_failed"), "seq", seq), "error", error == null ? "" : error));
            }
        });
        return put(put(put(put(put(new JSONObject(), "ok", true), "sent", true),
            "seq", seq), "sn", sn), "bytesHex", hex(frame)).toString();
    }


    /** 武装/解除写门控（对应 EqWritePolicy 的 armed 参数；页面开关调用）。 */
    @JavascriptInterface public void armWrite(boolean on) { armed = on; }

    /** 当前是否武装。 */
    @JavascriptInterface public boolean isArmed() { return armed; }

    // —— 连接控制（路 AU：转发给注入的 transport；纯组帧形态无传输，返回错误）——
    // 连接是异步的：结果经事件推送（就绪={"event":"mtu"}，失败/断开={"event":"disconnected"}）。

    /** 触发传输连接（幂等）。成功返回 {"ok":true,"connecting":true}，终态看事件。 */
    @JavascriptInterface public String connect() {
        if (transport == null) return err("未注入传输（纯组帧形态无连接）");
        transport.connect();
        return put(put(new JSONObject(), "ok", true), "connecting", true).toString();
    }

    /** 断开传输（未完成帧按失败回调，页面会收到 send_failed/disconnected 事件）。 */
    @JavascriptInterface public String disconnect() {
        if (transport == null) return err("未注入传输（纯组帧形态无连接）");
        transport.disconnect();
        return put(new JSONObject(), "ok", true).toString();
    }

    /** 传输是否就绪（discover+CCCD+MTU 全到位；页面状态显示用）。 */
    @JavascriptInterface public boolean isConnected() { return transport != null && transport.isReady(); }

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
            int sn = transport == null ? 0 : allocSn();
            return dispatch(EqFrame.buildEqCommand(sn, eqType, masterGainCentidB, bands), sn);
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
        int sn = transport == null ? 0 : allocSn();
        return dispatch(buildCustomCommand(sn, CMD_ANC, new byte[]{(byte) mode, (byte) scene, (byte) strength}), sn);
    }

    /** 组提示音音量命令（FF/0x1d，值 3/6/8）。data=`value 00`（路 P 钉死：固件 setter 只读 data[0] 当值、
     *  要求 len>=2、忽略第二字节；值不钳，3/6/8 是面板层档位）。 */
    @JavascriptInterface
    public String setPromptVolume(int level) {
        if (!armed) return err("未武装：先调 h3s.armWrite(true)（写门控，未武装拒绝一切写命令）");
        boolean legal = false;
        for (int v : PROMPT_VOLUME_LEVELS) if (v == level) { legal = true; break; }
        if (!legal) return err("提示音音量档位非法（仅 3/6/8=最小/中等/最大）: " + level);
        int sn = transport == null ? 0 : allocSn();
        return dispatch(buildCustomCommand(sn, CMD_PROMPT_VOLUME, new byte[]{(byte) level, 0x00}), sn);
    }

    /**
     * 组按键重映射命令（默认 wire=候选 A「标准 ADV 类型 2」：opcode C2+mask+三元组，形状
     * [假设] 见 KeyRemapFrame——真机抓包定案前的默认选择）。eventId 布局 0x{逻辑键}0{连击数}：
     * 769=音量- 单击/1281=音量+ 单击/1793..1796=多功能 单击~四击；长按=低字节 5 [假设]。
     * action 0..7（面板动作）或 11（ANC，固件侧）。成功后更新本地映射缓存（getKeyRemap 可读）。
     */
    @JavascriptInterface
    public String setKeyRemap(int eventId, int action) {
        return setKeyRemapWire(eventId, action, false);
    }

    /** 组按键重映射命令（候选 B「自定义命令 0x2b」wire：FF/0x2b+3B 三元组，字段域同上）。 */
    @JavascriptInterface
    public String setKeyRemapCustom(int eventId, int action) {
        return setKeyRemapWire(eventId, action, true);
    }

    private String setKeyRemapWire(int eventId, int action, boolean customWire) {
        if (!armed) return err("未武装：先调 h3s.armWrite(true)（写门控，未武装拒绝一切写命令）");
        try {
            KeyRemapFrame.validateEventId(eventId);
        } catch (IllegalArgumentException e) {
            return err(e.getMessage());
        }
        if (!KeyRemapFrame.isLegalAction(action)) return err("action 非法（0..7 面板动作或 11=ANC）: " + action);
        int key = KeyRemapFrame.keyOf(eventId), gesture = KeyRemapFrame.gestureOf(eventId);
        try {
            int sn = transport == null ? 0 : allocSn();
            byte[] frame = customWire
                ? KeyRemapFrame.buildCustomCommand(sn, key, gesture, action)
                : KeyRemapFrame.buildAdvType2Command(sn, key, gesture, action);
            String result = dispatch(frame, sn);
            try { if (new JSONObject(result).getBoolean("ok")) keyRemap.put(eventId, action); }
            catch (JSONException impossible) { throw new RuntimeException(impossible); } // 工作副本随组帧/发送成功更新；设备真实态以响应为准
            return result;
        } catch (IllegalArgumentException e) {
            return err(e.getMessage()); // 组帧校验兜底（validateEventId 已拦大头）
        }
    }

    /**
     * 读当前按键映射（本地工作副本，非设备读回——ADV type2 查询响应形状未钉）：
     * {"ok":true,"map":{"769":6,...}}，键=eventID 十进制串、值=action（-1=长按未知）。
     * 初始值=控制面板 defaultFun；setKeyRemap/setKeyRemapCustom 成功后随之更新。
     */
    /**
     * 组 R4 key-store 写命令（FF/0x30，docs/r4-command-hook.md）：valueHex=16 个 hex 字符
     * （8 字节值，对本层不透明）。固件侧 keystore_hook 载荷把 data[1..9] 原样 set_config(30,…)
     * 持久化；回执经 response 事件返回，载荷 `30 01 status`（KeyStoreFrame.parseWriteAck 解析）。
     * 固件候选 UNTESTED 不刷机——真机生效前提 = --r4-keyhook 候选刷入（本 app 不做）。
     */
    @JavascriptInterface
    public String setHearingCfg(String valueHex) {
        if (!armed) return err("未武装：先调 h3s.armWrite(true)（写门控，未武装拒绝一切写命令）");
        byte[] value;
        try {
            value = unhex(valueHex);
        } catch (IllegalArgumentException e) {
            return err(e.getMessage());
        }
        try {
            KeyStoreFrame.validateValue(value);
        } catch (IllegalArgumentException e) {
            return err(e.getMessage());
        }
        int sn = transport == null ? 0 : allocSn();
        return dispatch(KeyStoreFrame.buildWriteCommand(sn, value), sn);
    }

    /**
     * 组 R4 key-store 读命令（FF/0x30，data=[30]）：固件 get_config(30, resp+2, 8) 把存的
     * 8 字节放进回执，response 事件 payloadHex=`3008`+值（KeyStoreFrame.parseReadPayload
     * 解析）；key 无记录时固件静默不回（无 response 事件=未配置）。
     */
    @JavascriptInterface
    public String getHearingCfg() {
        if (!armed) return err("未武装：先调 h3s.armWrite(true)（写门控，未武装拒绝一切写命令）");
        int sn = transport == null ? 0 : allocSn();
        return dispatch(KeyStoreFrame.buildReadCommand(sn), sn);
    }

    /** hex 串 → 字节数组（长度须为偶数；只认 0-9a-fA-F）。 */
    static byte[] unhex(String hex) {
        if (hex == null) throw new IllegalArgumentException("valueHex 不能为 null");
        if (hex.length() % 2 != 0) throw new IllegalArgumentException("hex 长度须为偶数: " + hex.length());
        byte[] out = new byte[hex.length() / 2];
        for (int i = 0; i < out.length; i++) {
            int hi = Character.digit(hex.charAt(i * 2), 16);
            int lo = Character.digit(hex.charAt(i * 2 + 1), 16);
            if (hi < 0 || lo < 0) throw new IllegalArgumentException("hex 字符非法: " + hex);
            out[i] = (byte) ((hi << 4) | lo);
        }
        return out;
    }

    @JavascriptInterface
    public String getKeyRemap() {
        JSONObject o = new JSONObject();
        for (java.util.Map.Entry<Integer, Integer> e : keyRemap.entrySet()) {
            try { o.put(String.valueOf(e.getKey()), e.getValue().intValue()); } catch (JSONException impossible) { throw new RuntimeException(impossible); }
        }
        try { return new JSONObject().put("ok", true).put("map", o).toString(); }
        catch (JSONException impossible) { return "{\"ok\":true}"; }
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

    public static byte[] buildCustomCommand(int sn, int cmdId, byte[] data) {
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
