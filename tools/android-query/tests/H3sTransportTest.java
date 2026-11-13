import cn.felix021.h3squery.EqFrame;
import cn.felix021.h3squery.EqFrame.EqBand;
import cn.felix021.h3squery.H3SBridge;
import cn.felix021.h3squery.H3sTransport;
import cn.felix021.h3squery.LoopbackTransport;
import cn.felix021.h3squery.SerialBleTransport;
import java.util.ArrayList;
import java.util.List;
import org.json.JSONObject;

/**
 * 传输接线端到端单测（路 Z）：JS 层参数 → H3SBridge 组帧 → EqWritePolicy →
 * SerialBleTransport 串行队列/分片 → LoopbackTransport 收片重组（逐字节对照）→
 * 设备桩响应 → notify 重组 → SN 匹配 → ResultSink 回调收到 {ok}。
 *
 * Loopback 走的就是真机路径（同一条串行队列代码，真机适配层只换 doWriteChunk/连接两个点），
 * 所以这里钉住的行为 = 真机联调时的行为。不连真机、不刷机（红线）。
 */
public class H3sTransportTest {
    static void expect(boolean b, String msg) { if (!b) throw new AssertionError(msg); }
    static void eq(String name, int a, int b) { if (a != b) throw new AssertionError(name + ": " + a + " != " + b); }
    static void bytes(String name, byte[] a, byte[] b) {
        if (!java.util.Arrays.equals(a, b))
            throw new AssertionError(name + ":\n  got " + H3SBridge.hex(a) + "\n  exp " + H3SBridge.hex(b));
    }

    /** 收集 sink 事件的测试桩。 */
    static final class Events implements H3SBridge.ResultSink {
        final List<String> list = new ArrayList<String>();
        @Override public void emit(String json) { list.add(json); }
        List<JSONObject> byEvent(String event) throws org.json.JSONException {
            List<JSONObject> out = new ArrayList<JSONObject>();
            for (String s : list) { JSONObject o = new JSONObject(s); if (event.equals(o.getString("event"))) out.add(o); }
            return out;
        }
        JSONObject first(String event) throws org.json.JSONException {
            List<JSONObject> all = byEvent(event);
            expect(!all.isEmpty(), "应收到 " + event + " 事件: " + list);
            return all.get(0);
        }
    }

    static byte[] unhex(String hex) {
        byte[] out = new byte[hex.length() / 2];
        for (int i = 0; i < out.length; i++) out[i] = (byte) Integer.parseInt(hex.substring(i * 2, i * 2 + 2), 16);
        return out;
    }

    static String tenBandsJson() {
        return "[{\"freq\":32,\"q\":40,\"gain\":-1200,\"iirType\":0},{\"freq\":64,\"q\":120,\"gain\":-250},"
            + "{\"freq\":125,\"q\":80,\"gain\":0,\"iirType\":3},{\"freq\":250,\"q\":300,\"gain\":300},"
            + "{\"freq\":500,\"q\":250,\"gain\":-400,\"iirType\":4},{\"freq\":1000,\"q\":100,\"gain\":800},"
            + "{\"freq\":2000,\"q\":500,\"gain\":-1200},{\"freq\":4000,\"q\":1000,\"gain\":1500,\"iirType\":1},"
            + "{\"freq\":8000,\"q\":2000,\"gain\":-3000},{\"freq\":16000,\"q\":5,\"gain\":32767,\"iirType\":255}]";
    }

    static List<EqBand> tenBands() {
        int[] freq = {32, 64, 125, 250, 500, 1000, 2000, 4000, 8000, 16000};
        int[] q = {40, 120, 80, 300, 250, 100, 500, 1000, 2000, 5};
        int[] gain = {-1200, -250, 0, 300, -400, 800, -1200, 1500, -3000, 32767};
        int[] iir = {0, 255, 3, 255, 4, 255, 255, 1, 255, 255};
        List<EqBand> bands = new ArrayList<EqBand>();
        for (int i = 0; i < 10; i++) bands.add(new EqBand(freq[i], q[i], gain[i], iir[i]));
        return bands;
    }

    public static void main(String[] args) throws Exception {
        endToEndEqWrite();
        fragmentationSmallMtu();
        serialTwoFrames();
        notArmedRejected();
        notConnectedRejected();
        snWraparound();
        responseTimeout();
        loopbackNoTransportUnchanged();
        deframerUnits();
        System.out.println("H3sTransportTest passed: 端到端组帧→队列→Loopback 逐字节→SN 匹配回调、"
            + "跨片重组、串行（第二条等第一条）、未武装/未连接拒绝、SN 回绕、响应超时、"
            + "未注入 transport 兼容、deframer 单元");
    }

    /** 端到端：writeEq 全链路——Loopback 收到的帧与 EqFrame 直组逐字节一致，响应按 SN 回到 sink。 */
    static void endToEndEqWrite() throws Exception {
        LoopbackTransport loopback = new LoopbackTransport();          // MTU 247（实测）
        Events events = new Events();
        H3SBridge b = new H3SBridge(loopback, events);
        loopback.connect();
        expect(loopback.isReady(), "connect 后应就绪");
        eq("实测 MTU", 247, loopback.mtu());
        JSONObject mtu = events.first("mtu");
        eq("mtu 事件", 247, mtu.getInt("mtu"));
        b.armWrite(true);

        JSONObject r = new JSONObject(b.writeEq(2, -400, tenBandsJson()));
        expect(r.getBoolean("ok"), "writeEq 应成功: " + r);
        expect(r.getBoolean("sent"), "注入 transport 后应 sent=true");
        eq("首条 SN", 1, r.getInt("sn"));
        eq("首条 seq", 1, r.getInt("seq"));
        // Loopback（=真机路径）收到的完整帧与 EqFrame 直组逐字节对照（data 73B，帧 81B）
        eq("设备收帧数", 1, loopback.receivedFrames.size());
        bytes("EQ 帧逐字节", loopback.receivedFrames.get(0), EqFrame.buildEqCommand(1, 2, -400, tenBands()));
        eq("单帧片数（81B<244B）", 1, loopback.writtenChunks.size());
        // 响应按 SN 匹配回到 sink
        JSONObject resp = events.first("response");
        expect(resp.getBoolean("ok"), "响应 status 应为 0: " + resp);
        eq("响应 seq", 1, resp.getInt("seq"));
        eq("响应 sn", 1, resp.getInt("sn"));
        eq("响应 status", 0, resp.getInt("status"));
        // 载荷回显 = cmdid|data_len|data（Loopback 桩形状，setter 响应载荷[未钉]见类注释）
        bytes("响应载荷回显", unhex(resp.getString("payloadHex")),
            unhex("2249" + H3SBridge.hex(EqFrame.buildEqData(2, -400, tenBands()))));
    }

    /** 跨片路径：MTU 压到 27（payload 24B），81B 帧 4 片——设备侧重组后仍逐字节正确。 */
    static void fragmentationSmallMtu() throws Exception {
        LoopbackTransport loopback = new LoopbackTransport(27);        // 压小只为覆盖跨片路径
        Events events = new Events();
        H3SBridge b = new H3SBridge(loopback, events);
        loopback.connect();
        b.armWrite(true);
        JSONObject r = new JSONObject(b.writeEq(1, 0, tenBandsJson()));
        expect(r.getBoolean("ok"), "跨片 writeEq 应成功: " + r);
        eq("81B/24B 片数", 4, loopback.writtenChunks.size());
        bytes("跨片重组帧", loopback.receivedFrames.get(0), EqFrame.buildEqCommand(r.getInt("sn"), 1, 0, tenBands()));
        // 片间隔全部 >=10ms（AGENTS 不变量 6；值记录于调度器）
        for (long d : loopback.scheduledDelays) expect(d >= SerialBleTransport.INTER_CHUNK_DELAY_MS,
            "片间隔应 >= " + SerialBleTransport.INTER_CHUNK_DELAY_MS + "ms: " + d);
        expect(events.first("response").getBoolean("ok"), "跨片响应应成功");
    }

    /** 帧级串行：连发两条，第二条要等第一条的响应后才写出（AGENTS「等前一个回调再发下一个」）。 */
    static void serialTwoFrames() throws Exception {
        LoopbackTransport loopback = new LoopbackTransport();
        Events events = new Events();
        H3SBridge b = new H3SBridge(loopback, events);
        loopback.connect();
        b.armWrite(true);
        loopback.pauseResponses();

        JSONObject r1 = new JSONObject(b.writeEq(1, 0, "[{\"freq\":1000,\"gain\":-800}]"));
        expect(r1.getBoolean("sent"), "帧1 应已排队写出");
        int chunksAfterFrame1 = loopback.writtenChunks.size();
        expect(events.byEvent("response").isEmpty(), "帧1 未响应前不应有响应事件");
        // 帧2：帧1 还在等响应 → 帧2 只入队，不应写出任何片
        JSONObject r2 = new JSONObject(b.setPromptVolume(6));
        expect(r2.getBoolean("sent"), "帧2 应返回排队成功");
        eq("帧2 未发出（片数不变）", chunksAfterFrame1, loopback.writtenChunks.size());
        expect(events.byEvent("response").isEmpty(), "释放前不应有任何响应事件");

        loopback.releaseResponses();                                    // 设备开始回响应
        eq("释放后设备收帧数", 2, loopback.receivedFrames.size());
        bytes("帧1 仍逐字节正确", loopback.receivedFrames.get(0),
            EqFrame.buildEqCommand(r1.getInt("sn"), 1, 0, java.util.Arrays.asList(new EqBand(1000, 100, -800, 255))));
        bytes("帧2 逐字节正确", loopback.receivedFrames.get(1),
            H3SBridge.buildCustomCommand(r2.getInt("sn"), H3SBridge.CMD_PROMPT_VOLUME, new byte[]{6, 0}));
        List<JSONObject> responses = events.byEvent("response");
        eq("两条响应", 2, responses.size());
        eq("帧1 响应 seq", r1.getInt("seq"), responses.get(0).getInt("seq"));
        eq("帧2 响应 seq", r2.getInt("seq"), responses.get(1).getInt("seq"));
        expect(responses.get(0).getInt("sn") != responses.get(1).getInt("sn"), "两条帧 SN 应不同");
    }

    /** 未武装拒绝：transport 已连接也不发一字节。 */
    static void notArmedRejected() throws Exception {
        LoopbackTransport loopback = new LoopbackTransport();
        Events events = new Events();
        H3SBridge b = new H3SBridge(loopback, events);
        loopback.connect();                                             // 注意：未 armWrite
        JSONObject r = new JSONObject(b.writeEq(1, 0, "[]"));
        expect(!r.getBoolean("ok"), "未武装应拒绝: " + r);
        expect(r.getString("error").contains("武装"), "错误信息应提到武装: " + r);
        eq("未武装零发送", 0, loopback.writtenChunks.size());
        eq("未武装零帧", 0, loopback.receivedFrames.size());
        expect(events.byEvent("response").isEmpty(), "不应有响应事件");
    }

    /** 未连接拒绝：注入了 transport 但没 connect。 */
    static void notConnectedRejected() throws Exception {
        LoopbackTransport loopback = new LoopbackTransport();
        Events events = new Events();
        H3SBridge b = new H3SBridge(loopback, events);
        b.armWrite(true);
        JSONObject r = new JSONObject(b.setAnc(1, 1, 2));
        expect(!r.getBoolean("ok"), "未连接应拒绝: " + r);
        expect(r.getString("error").contains("连接"), "错误信息应提到连接: " + r);
        eq("未连接零发送", 0, loopback.writtenChunks.size());
        // connect 前积压：connect 后队列开闸，这一条应正常发出（markReady→pump）
        loopback.connect();
        JSONObject r2 = new JSONObject(b.setAnc(1, 1, 2));
        expect(r2.getBoolean("sent"), "连接后应可发送: " + r2);
        expect(events.first("response").getBoolean("ok"), "连接后响应应成功");
    }

    /** SN 回绕：u8 自增 254→255→0，响应各自匹配正确 seq。 */
    static void snWraparound() throws Exception {
        LoopbackTransport loopback = new LoopbackTransport();
        Events events = new Events();
        H3SBridge b = new H3SBridge(loopback, events);
        loopback.connect();
        b.armWrite(true);
        b.setNextSnForTest(254);
        int[] expectedSn = {254, 255, 0};
        int[] seqs = new int[3];
        String[] rs = {b.setAnc(1, 1, 2), b.setPromptVolume(8), b.setAnc(3, 1, 4)};
        for (int i = 0; i < 3; i++) {
            JSONObject r = new JSONObject(rs[i]);
            expect(r.getBoolean("ok"), "第 " + (i + 1) + " 条应成功: " + r);
            eq("SN 序列[" + i + "]", expectedSn[i], r.getInt("sn"));
            seqs[i] = r.getInt("seq");
        }
        List<JSONObject> responses = events.byEvent("response");
        eq("回绕三条响应", 3, responses.size());
        for (int i = 0; i < 3; i++) {
            eq("响应[" + i + "] sn", expectedSn[i], responses.get(i).getInt("sn"));
            eq("响应[" + i + "] seq", seqs[i], responses.get(i).getInt("seq"));
            expect(responses.get(i).getBoolean("ok"), "响应[" + i + "] 应 status=0");
        }
    }

    /** 响应超时：设备不回（pause），显式触发超时任务 → 帧 send_failed 事件。 */
    static void responseTimeout() throws Exception {
        LoopbackTransport loopback = new LoopbackTransport();
        Events events = new Events();
        H3SBridge b = new H3SBridge(loopback, events);
        loopback.connect();
        b.armWrite(true);
        loopback.pauseResponses();
        JSONObject r = new JSONObject(b.setPromptVolume(3));
        expect(r.getBoolean("sent"), "帧应已写出（写完成≠响应）");
        expect(events.byEvent("response").isEmpty(), "无响应");
        loopback.runTimeouts();                                         // 真机由 postDelayed 5s 触发
        JSONObject fail = events.first("send_failed");
        eq("失败 seq", r.getInt("seq"), fail.getInt("seq"));
        expect(fail.getString("error").contains("超时"), "错误应说明超时: " + fail);
    }

    /** 兼容性：未注入 transport（无参构造）行为不变——组帧返回 hex，无 sent 字段、零发送对象。 */
    static void loopbackNoTransportUnchanged() throws Exception {
        H3SBridge b = new H3SBridge();
        b.armWrite(true);
        JSONObject r = new JSONObject(b.writeEq(1, 0, "[{\"freq\":1000,\"gain\":-800}]"));
        expect(r.getBoolean("ok"), "未注入应组帧成功: " + r);
        expect(!r.has("sent"), "未注入返回应保持旧形状（无 sent 字段）");
        bytes("未注入 SN=0 逐字节（旧字节不变）", unhex(r.getString("bytesHex")),
            EqFrame.buildEqCommand(0, 1, 0, java.util.Arrays.asList(new EqBand(1000, 100, -800, 255))));
        JSONObject anc = new JSONObject(b.setAnc(1, 1, 2));
        bytes("未注入 ANC 逐字节", unhex(anc.getString("bytesHex")),
            unhex("fedcbac0ff0006" + "00" + "1703" + "010102" + "ef"));
    }

    /** deframer 单元：跨片、一次多帧、垃圾前缀、不完整帧保留。 */
    static void deframerUnits() {
        SerialBleTransport.RcspDeframer d = new SerialBleTransport.RcspDeframer();
        byte[] f1 = H3SBridge.buildCustomCommand(7, H3SBridge.CMD_ANC, new byte[]{1, 1, 2});
        byte[] f2 = H3SBridge.buildCustomCommand(8, H3SBridge.CMD_PROMPT_VOLUME, new byte[]{6, 0});
        // 跨片：f1 切两半
        int mid = f1.length / 2;
        expect(d.feed(java.util.Arrays.copyOfRange(f1, 0, mid)).isEmpty(), "半帧不出");
        List<byte[]> out = d.feed(java.util.Arrays.copyOfRange(f1, mid, f1.length));
        eq("跨片出一帧", 1, out.size());
        bytes("跨片重组", out.get(0), f1);
        // 一次多帧（两条帧粘在一个 notify 里）
        byte[] glued = new byte[f1.length + f2.length];
        System.arraycopy(f1, 0, glued, 0, f1.length);
        System.arraycopy(f2, 0, glued, f1.length, f2.length);
        d = new SerialBleTransport.RcspDeframer();
        out = d.feed(glued);
        eq("粘连出两帧", 2, out.size());
        bytes("粘连帧1", out.get(0), f1);
        bytes("粘连帧2", out.get(1), f2);
        // 垃圾前缀 + 假头（FE DC BA 后长度不对、尾非 EF）后跟真帧
        d = new SerialBleTransport.RcspDeframer();
        byte[] junk = unhex("001122fe" + "fedcba000100" + "00");       // 垃圾 + 头对但不成帧的字节
        expect(d.feed(junk).isEmpty(), "垃圾+半帧不出");
        out = d.feed(f2);
        eq("垃圾后真帧", 1, out.size());
        bytes("垃圾后重组", out.get(0), f2);
        // Loopback 响应帧形状（status+SN 起，cmdid|n|data 对齐读路径实测包装）直接验证
        byte[] cmd = H3SBridge.buildCustomCommand(0x6f, H3SBridge.CMD_ANC, new byte[]{1, 1, 2});
        byte[] resp = LoopbackTransport.buildSuccessResponse(cmd);
        bytes("响应帧形状（status+SN）", resp,
            unhex("fedcba00ff0007" + "006f" + "1703" + "010102" + "ef"));
        expect(SerialBleTransport.isResponseFrame(resp), "应为响应帧");
        expect(!SerialBleTransport.needsResponse(resp), "响应帧不需再响应");
    }
}
