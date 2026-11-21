import cn.felix021.h3squery.EqWritePolicy;
import cn.felix021.h3squery.H3SBridge;
import cn.felix021.h3squery.H3sTransport;
import cn.felix021.h3squery.KeyStoreFrame;
import cn.felix021.h3squery.LoopbackTransport;
import java.util.ArrayList;
import java.util.List;
import org.json.JSONObject;

/**
 * R4 key-store（cmdid 0x30 / VM key 30）纯 JVM 单测 + Loopback 端到端
 * （docs/r4-command-hook.md，路 AQ；照 KeyRemapFrameTest/H3sTransportTest 风格）：
 *
 *  - 组帧/反解析往返（写 [30,8B] / 读 [30]）与文档字节对照；
 *  - 边界：值长度错、key 字节错、帧头/cmdid/长度/尾破坏 → 拒绝；
 *  - 回执载荷解析（写 `30 01 status` / 读 `30 08 v0..v7`）；
 *  - EqWritePolicy：合法形状放行（armed），畸形与非 0x30 cmdid 拒绝，未武装拒绝；
 *  - 端到端（LoopbackTransport）：setHearingCfg → 设备桩收帧逐字节对照 →
 *    `30 01 00` 回执 → SN 匹配 response 事件；getHearingCfg → `30 08 桩值` 回执 →
 *    payloadHex 解出 8 字节；未武装两方法都拒绝。
 *
 * 全部离线；真机下发未验证、固件候选 UNTESTED 不刷机（红线）。
 */
public class KeyStoreFrameTest {
    static void expect(boolean b, String msg) { if (!b) throw new AssertionError(msg); }
    static void bytes(String name, byte[] a, byte[] b) {
        if (!java.util.Arrays.equals(a, b))
            throw new AssertionError(name + ":\n  got " + hex(a) + "\n  exp " + hex(b));
    }
    static String hex(byte[] b) {
        StringBuilder s = new StringBuilder();
        for (byte x : b) s.append(String.format("%02x", x & 0xff));
        return s.toString();
    }
    static byte[] unhex(String h) {
        byte[] out = new byte[h.length() / 2];
        for (int i = 0; i < out.length; i++) out[i] = (byte) Integer.parseInt(h.substring(i * 2, i * 2 + 2), 16);
        return out;
    }
    static void rejected(Runnable r, String msg) {
        try { r.run(); } catch (IllegalArgumentException e) { return; }
        throw new AssertionError("未按预期报错: " + msg);
    }

    public static void main(String[] args) throws Exception {
        roundTrip();
        docBytes();
        boundary();
        ackParsing();
        policy();
        policyKey31();
        endToEnd();
        endToEndKey31();
        softRollback();
        mpoCodec();
        eqCodec();
        System.out.println("KeyStoreFrameTest passed: 写/读往返、文档字节、边界拒绝、回执解析、EqWritePolicy 放行（key30+key31）、Loopback 端到端读写（双 key）、软回退受限入口、L3 MPO 值协议、L4 EQ 十段值协议");
    }

    // L3 MPO 值协议（docs/hearing-aid-l3-mpo.md；与固件 apply_mpo.S 位型门对齐）
    static void mpoCodec() {
        // 正常值往返 + 字节形状（'H','A',01,00,float32 LE）
        byte[] v100 = KeyStoreFrame.encodeMpoValue(100.0f);
        bytes("MPO=100 编码", v100, unhex("48410100" + "0000c842"));
        Float back = KeyStoreFrame.decodeMpoValue(v100);
        expect(back != null && Math.abs(back - 100.0f) < 1e-4, "MPO=100 解码");
        // 位型门：与固件一致地拒绝 <=0.3 / >=120 / NaN / Inf / 负数
        rejected(() -> KeyStoreFrame.encodeMpoValue(0.3f), "MPO<=0.3 应拒绝");
        rejected(() -> KeyStoreFrame.encodeMpoValue(120.0f), "MPO>=120 应拒绝");
        rejected(() -> KeyStoreFrame.encodeMpoValue(Float.NaN), "NaN 应拒绝");
        rejected(() -> KeyStoreFrame.encodeMpoValue(Float.POSITIVE_INFINITY), "Inf 应拒绝");
        rejected(() -> KeyStoreFrame.encodeMpoValue(-90.0f), "负数应拒绝");
        // 边界对齐：固件 0.3 < M < 120.0 独占；此前 App 用 119.9 误拒 [119.9, 120)（缺口第 4 项）
        expect(KeyStoreFrame.decodeMpoValue(KeyStoreFrame.encodeMpoValue(119.95f)) != null,
                "MPO=119.95 应可编码（固件门 <120.0）");
        expect(KeyStoreFrame.decodeMpoValue(KeyStoreFrame.encodeMpoValue(119.9f)) != null,
                "MPO=119.9 应可编码");
        // 旧格式/任意测试字节不被误读（历史 key30 值 5730dead… 解码为 null）
        expect(KeyStoreFrame.decodeMpoValue(unhex("5730deadbeef2100")) == null,
                "非协议值必须解不出 MPO");
        // 协议头破损拒绝
        byte[] bad = v100.clone(); bad[0] = 0x49;
        expect(KeyStoreFrame.decodeMpoValue(bad) == null, "魔数错拒绝");
        bad = v100.clone(); bad[2] = 2;
        expect(KeyStoreFrame.decodeMpoValue(bad) == null, "版本错拒绝");
        bad = v100.clone(); bad[3] = 1;
        expect(KeyStoreFrame.decodeMpoValue(bad) == null, "保留字节非 0 拒绝");
        // 8B 协议值可进普通 write 通道（帧形状与既有写完全一致）
        byte[] w = KeyStoreFrame.buildWriteCommand(3, v100);
        bytes("MPO 写帧", KeyStoreFrame.parseWriteCommand(w), v100);
    }

    // L4 EQ 十段值协议（docs/hearing-aid-l4-eq.md；与固件 apply_eq.S 整包先验对齐）
    static void eqCodec() {
        // 官方 Eq1Eff blob 的十个频点 + 一组非零增益（含负增益）往返
        int[] freqs = {25, 53, 112, 238, 505, 1070, 2268, 4807, 10190, 21599};
        float[] gains = {12.5f, -6.25f, 0f, 39f, -39.9f, 3f, 0.5f, -1f, 20f, 40f - 0.01f};
        KeyStoreFrame.EqSeg[] segs = new KeyStoreFrame.EqSeg[10];
        for (int i = 0; i < 10; i++) segs[i] = new KeyStoreFrame.EqSeg(freqs[i], gains[i]);
        byte[] v = KeyStoreFrame.encodeEqValue(segs);
        expect(v.length == 88, "EQ 值应为 88B: " + v.length);
        // 头部字节形状：'H','A',02,00,0a,00 00 00
        bytes("EQ 头", java.util.Arrays.copyOfRange(v, 0, 8), unhex("484102000a000000"));
        // 段 0 字节形状：freq u16 LE + pad + float32 LE（25=0x0019, 12.5f=0x41480000）
        bytes("EQ seg0", java.util.Arrays.copyOfRange(v, 8, 16), unhex("1900" + "0000" + "00004841"));
        KeyStoreFrame.EqSeg[] back = KeyStoreFrame.decodeEqValue(v);
        expect(back != null, "EQ 值解码失败");
        for (int i = 0; i < 10; i++) {
            expect(back[i].freqHz == freqs[i], "freq 往返[" + i + "]: " + back[i].freqHz);
            expect(Math.abs(back[i].gainDb - gains[i]) < 1e-4, "gain 往返[" + i + "]: " + back[i].gainDb);
        }
        // 门：段数错 / freq=0 / freq>21599 / |gain|>40 / NaN / Inf（边界 ±40.0 含——固件 |bits|<=40.0f 同界）
        KeyStoreFrame.EqSeg[] nine = java.util.Arrays.copyOf(segs, 9);
        rejected(() -> KeyStoreFrame.encodeEqValue(nine), "9 段应拒绝");
        rejected(() -> KeyStoreFrame.encodeEqValue(mut(segs, 0, 0, 0f)), "freq=0 应拒绝");
        rejected(() -> KeyStoreFrame.encodeEqValue(mut(segs, 9, 21600, 0f)), "freq>21599 应拒绝");
        rejected(() -> KeyStoreFrame.encodeEqValue(mut(segs, 5, 1000, 40.01f)), "gain>40 应拒绝");
        rejected(() -> KeyStoreFrame.encodeEqValue(mut(segs, 5, 1000, -40.01f)), "gain<-40 应拒绝");
        rejected(() -> KeyStoreFrame.encodeEqValue(mut(segs, 5, 1000, Float.NaN)), "NaN 应拒绝");
        rejected(() -> KeyStoreFrame.encodeEqValue(mut(segs, 5, 1000, Float.POSITIVE_INFINITY)), "Inf 应拒绝");
        // 边界 ±40.0 含：此前 <40 误拒 40.0（缺口第 4 项），现已对齐固件 <=40.0f 放行
        expect(KeyStoreFrame.encodeEqValue(mut(segs, 5, 1000, 40.0f)).length == 88, "gain=+40 应放行");
        expect(KeyStoreFrame.encodeEqValue(mut(segs, 5, 1000, -40.0f)).length == 88, "gain=-40 应放行");
        // 头破损 / 段数错 / 段值越界 → 解码 null；key30 的 8B MPO 值不被误读成 EQ
        expect(KeyStoreFrame.decodeEqValue(unhex("4841010000000000" + "00".repeat(80))) == null, "ver=1 不解为 EQ");
        byte[] bad = v.clone(); bad[4] = 9;
        expect(KeyStoreFrame.decodeEqValue(bad) == null, "段数 9 拒绝");
        bad = v.clone();
        int nanBits = Float.floatToIntBits(Float.NaN);
        for (int k = 0; k < 4; k++) bad[8 + 4 + 4 + k] = (byte) (nanBits >>> (8 * k)); // seg0 gain -> NaN 位型
        expect(KeyStoreFrame.decodeEqValue(bad) == null, "段值 NaN 拒绝");
        bad = v.clone();
        int infBits = Float.floatToIntBits(Float.POSITIVE_INFINITY);
        for (int k = 0; k < 4; k++) bad[8 + 5 * 8 + 4 + k] = (byte) (infBits >>> (8 * k)); // seg5 gain -> Inf
        expect(KeyStoreFrame.decodeEqValue(bad) == null, "段值 Inf 拒绝");
        // 两种值互不误读：长度门在 validate 层直接报错（8B 值进 EQ 解码 / 88B 值进 MPO 解码都拒）
        rejected(() -> KeyStoreFrame.decodeEqValue(KeyStoreFrame.encodeMpoValue(100.0f)), "8B 值不是 EQ 值");
        rejected(() -> KeyStoreFrame.decodeMpoValue(v), "88B 值不是 MPO 值");
        // EQ 写命令帧形状：data = [31, 88B]，帧头/cmdid/data_len 与 key30 写同族
        byte[] w = KeyStoreFrame.buildEqWriteCommand(0, segs);
        expect(w.length == 7 + 3 + 89 + 1, "EQ 写帧长度: " + w.length);
        expect((w[8] & 0xff) == 0x30 && (w[9] & 0xff) == 89 && (w[10] & 0xff) == 31,
                "EQ 写帧 cmdid/data_len/key 字节");
        // EQ 读回载荷解析 `30 58 <88B>`
        byte[] payload = new byte[2 + 88];
        payload[0] = 0x30; payload[1] = 88;
        System.arraycopy(v, 0, payload, 2, 88);
        bytes("EQ 读回载荷值", KeyStoreFrame.parseEqReadPayload(payload), v);
    }

    static KeyStoreFrame.EqSeg[] mut(KeyStoreFrame.EqSeg[] segs, int i, int f, float g) {
        KeyStoreFrame.EqSeg[] out = segs.clone();
        out[i] = new KeyStoreFrame.EqSeg(f, g);
        return out;
    }

    static void roundTrip() {
        byte[] value = unhex("5730deadbeef2100");
        byte[] w = KeyStoreFrame.buildWriteCommand(7, value);
        bytes("写命令往返", KeyStoreFrame.parseWriteCommand(w), value);
        byte[] r = KeyStoreFrame.buildReadCommand(9);
        eqInt(KeyStoreFrame.parseReadCommand(r), KeyStoreFrame.KEY_ID);
        expect(w[7] == 7 && r[7] == 9, "SN 位置不对");
    }

    static void eqInt(int a, int b) { if (a != b) throw new AssertionError(a + " != " + b); }

    static void docBytes() {
        // 写命令整帧字节对照（SN=0）：FE DC BA C0 FF 00 0C | 00 | 30 | 09 | 1E | 8B 值 | EF
        byte[] w = KeyStoreFrame.buildWriteCommand(0, unhex("0102030405060708"));
        bytes("写命令文档字节", w,
              unhex("fedcbac0ff000c" + "00" + "30" + "09" + "1e" + "0102030405060708" + "ef"));
        // 读命令整帧字节对照（SN=0）：body = SN + cmdid + data_len + key = 4B
        byte[] r = KeyStoreFrame.buildReadCommand(0);
        bytes("读命令文档字节", r, unhex("fedcbac0ff0004" + "00" + "30" + "01" + "1e" + "ef"));
    }

    static void boundary() {
        rejected(() -> KeyStoreFrame.buildWriteCommand(0, unhex("0102")), "值 2 字节应拒绝");
        rejected(() -> KeyStoreFrame.buildWriteCommand(0, new byte[9]), "值 9 字节应拒绝");
        rejected(() -> KeyStoreFrame.buildWriteCommand(0, null), "null 值应拒绝");
        byte[] badKey = KeyStoreFrame.buildWriteCommand(0, new byte[8]);
        badKey[10] = 0x1f;                                   // data[0] != 30
        rejected(() -> KeyStoreFrame.parseWriteCommand(badKey), "key 字节错应拒绝");
        byte[] badLen = KeyStoreFrame.buildWriteCommand(0, new byte[8]);
        badLen[9] = 0x08;                                    // data_len 声明 8 与实长 9 不符
        rejected(() -> KeyStoreFrame.parseWriteCommand(badLen), "data_len 声明错应拒绝");
        byte[] badTail = KeyStoreFrame.buildReadCommand(0);
        badTail[badTail.length - 1] = 0x00;
        rejected(() -> KeyStoreFrame.parseReadCommand(badTail), "帧尾破坏应拒绝");
        byte[] badCmd = KeyStoreFrame.buildReadCommand(0);
        badCmd[8] = 0x2b;
        rejected(() -> KeyStoreFrame.parseReadCommand(badCmd), "cmdid 破坏应拒绝");
    }

    static void ackParsing() {
        eqInt(KeyStoreFrame.parseWriteAck(unhex("300100")), 0);
        eqInt(KeyStoreFrame.parseWriteAck(unhex("300101")), 1);
        rejected(() -> KeyStoreFrame.parseWriteAck(unhex("300200")), "写回执 n=2 应拒绝");
        bytes("读回载荷", KeyStoreFrame.parseReadPayload(unhex("3008" + "5730deadbeef2100")),
              unhex("5730deadbeef2100"));
        rejected(() -> KeyStoreFrame.parseReadPayload(unhex("3007" + "0102030405060708")), "读回 n=7 应拒绝");
    }

    // key31 白名单（缺口表第 1 项）：89B 写 + 1B 读精确放行；错长度/错 key/混合形状拒绝，
    // 不开任意帧写（0x30 只认 30/31 两个 key 的四个精确形状）。
    static void policyKey31() {
        KeyStoreFrame.EqSeg[] segs = flatSegs();
        byte[] w = KeyStoreFrame.buildEqWriteCommand(1, segs);
        byte[] r = KeyStoreFrame.buildEqReadCommand(1);
        expect(w.length == 8 + 3 + 89 && (w[9] & 0xff) == 89 && (w[10] & 0xff) == 31,
                "EQ 写帧形状: len=" + w.length);
        bytes("EQ 读命令文档字节", KeyStoreFrame.buildEqReadCommand(0),
              unhex("fedcbac0ff0004" + "00" + "30" + "01" + "1f" + "ef"));
        eqInt(KeyStoreFrame.parseEqReadCommand(r), KeyStoreFrame.EQ_KEY_ID);
        bytes("EQ 写命令往返", KeyStoreFrame.parseEqWriteCommand(w), KeyStoreFrame.encodeEqValue(segs));
        expect(EqWritePolicy.permitted(w, true), "armed 应放行 key31 89B 写形状");
        expect(EqWritePolicy.permitted(r, true), "armed 应放行 key31 1B 读形状");
        expect(!EqWritePolicy.permitted(w, false), "未武装应拒绝 key31 写");
        expect(!EqWritePolicy.permitted(r, false), "未武装应拒绝 key31 读");
        // key31 帧 key 字节/长度破坏：89B 但 key=30 / 9B 但 key=31 / 88B / 90B 全拒
        byte[] badKey = w.clone(); badKey[10] = 0x1e;
        expect(!EqWritePolicy.permitted(badKey, true), "89B 但 key=30 应拒绝");
        byte[] nine = KeyStoreFrame.buildWriteCommand(1, new byte[8]); nine[10] = 0x1f;
        expect(!EqWritePolicy.permitted(nine, true), "9B 但 key=31 应拒绝");
        byte[] badLen = w.clone(); badLen[9] = 88;
        expect(!EqWritePolicy.permitted(badLen, true), "data_len=88 应拒绝");
        byte[] overLen = w.clone(); overLen[9] = 90;   // 声明 90 与实长不符（len_BE16 也不符）
        expect(!EqWritePolicy.permitted(overLen, true), "data_len=90 应拒绝");
        byte[] otherKey = r.clone(); otherKey[10] = 0x20;
        expect(!EqWritePolicy.permitted(otherKey, true), "key=32 应拒绝");
        // key30 白名单不回归
        expect(EqWritePolicy.permitted(KeyStoreFrame.buildWriteCommand(1, new byte[8]), true),
                "key30 写形状回归");
    }

    static KeyStoreFrame.EqSeg[] flatSegs() {
        int[] freqs = {25, 53, 112, 238, 505, 1070, 2268, 4807, 10190, 21599};
        KeyStoreFrame.EqSeg[] segs = new KeyStoreFrame.EqSeg[10];
        for (int i = 0; i < 10; i++) segs[i] = new KeyStoreFrame.EqSeg(freqs[i], 0f);
        return segs;
    }

    static void policy() {
        byte[] w = KeyStoreFrame.buildWriteCommand(1, new byte[8]);
        byte[] r = KeyStoreFrame.buildReadCommand(1);
        expect(EqWritePolicy.permitted(w, true), "armed 应放行 0x30 写形状");
        expect(EqWritePolicy.permitted(r, true), "armed 应放行 0x30 读形状");
        expect(!EqWritePolicy.permitted(w, false), "未武装应拒绝");
        byte[] badKey = w.clone(); badKey[10] = 0x31;
        expect(!EqWritePolicy.permitted(badKey, true), "key 字节错应拒绝");
        byte[] badLen = w.clone(); badLen[9] = 0x08;
        expect(!EqWritePolicy.permitted(badLen, true), "data_len 声明错应拒绝");
        byte[] badCmd = w.clone(); badCmd[8] = 0x2f;
        expect(!EqWritePolicy.permitted(badCmd, true), "非 0x30 cmdid 应拒绝");
        // 既有白名单不回归：0x17/0x1d/0x2b/0x22 形状照旧
        expect(EqWritePolicy.permitted(
                H3SBridge.buildCustomCommand(1, EqWritePolicy.CMD_ANC, new byte[]{1, 1, 2}), true),
                "0x17 白名单回归");
    }

    static final class Events implements H3SBridge.ResultSink {
        final List<String> list = new ArrayList<String>();
        @Override public void emit(String json) { list.add(json); }
        JSONObject bySeq(int seq) throws org.json.JSONException {
            for (String s : list) {
                JSONObject o = new JSONObject(s);
                if (o.has("event") && "response".equals(o.getString("event"))
                        && o.getInt("seq") == seq) return o;
            }
            throw new AssertionError("未收到 seq=" + seq + " 的 response: " + list);
        }
    }

    static void endToEnd() throws Exception {
        LoopbackTransport loop = new LoopbackTransport();
        Events sink = new Events();
        H3SBridge bridge = new H3SBridge(loop, sink);
        loop.connect();
        bridge.armWrite(true);

        // 写：SN 匹配回执 `30 01 00`
        byte[] value = unhex("5730deadbeef2100");
        JSONObject wres = new JSONObject(bridge.setHearingCfg(hex(value)));
        expect(wres.getBoolean("ok") && wres.getBoolean("sent"), "写发送应成功: " + wres);
        int wseq = wres.getInt("seq");
        JSONObject wack = sink.bySeq(wseq);
        expect(wack.getBoolean("ok"), "写回执 status 应为 0: " + wack);
        eqInt(KeyStoreFrame.parseWriteAck(unhex(wack.getString("payloadHex"))), 0);
        expect(loop.receivedFrames.size() == 1, "设备桩应收 1 帧: " + loop.receivedFrames.size());
        bytes("设备桩收到的写帧", loop.receivedFrames.get(0), KeyStoreFrame.buildWriteCommand(wres.getInt("sn"), value));

        // 读：桩值 8 字节回 `30 08 v…`
        loop.keyStoreValue = value.clone();
        JSONObject rres = new JSONObject(bridge.getHearingCfg());
        expect(rres.getBoolean("ok") && rres.getBoolean("sent"), "读发送应成功: " + rres);
        JSONObject rval = sink.bySeq(rres.getInt("seq"));
        bytes("读回 8 字节", KeyStoreFrame.parseReadPayload(unhex(rval.getString("payloadHex"))), value);

        // 未武装拒绝（新 bridge 实例，默认未武装）
        H3SBridge unarmed = new H3SBridge(new LoopbackTransport(), new Events());
        expect(new JSONObject(unarmed.setHearingCfg(hex(value))).getBoolean("ok") == false, "未武装写应拒绝");
        expect(new JSONObject(unarmed.getHearingCfg()).getBoolean("ok") == false, "未武装读应拒绝");
        // 组帧层校验：非 hex / 长度错
        expect(new JSONObject(bridge.setHearingCfg("xyz")).getBoolean("ok") == false, "非 hex 应拒绝");
        expect(new JSONObject(bridge.setHearingCfg("0102")).getBoolean("ok") == false, "长度错应拒绝");
    }

    // key31 端到端（Loopback 桩按 keystore_hook.S HAL4 形状应答）：写→30 01 00；
    // 读→30 58+桩值 88B → parseEqReadPayload → decodeEqValue 十段往返。
    static void endToEndKey31() throws Exception {
        LoopbackTransport loop = new LoopbackTransport();
        Events sink = new Events();
        H3SBridge bridge = new H3SBridge(loop, sink);
        loop.connect();
        bridge.armWrite(true);

        KeyStoreFrame.EqSeg[] segs = flatSegs();
        JSONObject wres = new JSONObject(bridge.setHearingEq(segJson(segs)));
        expect(wres.getBoolean("ok") && wres.getBoolean("sent"), "EQ 写发送应成功: " + wres);
        JSONObject wack = sink.bySeq(wres.getInt("seq"));
        expect(wack.getBoolean("ok"), "EQ 写回执 status 应为 0: " + wack);
        eqInt(KeyStoreFrame.parseWriteAck(unhex(wack.getString("payloadHex"))), 0);
        bytes("设备桩收到的 EQ 写帧", loop.receivedFrames.get(0),
              KeyStoreFrame.buildEqWriteCommand(wres.getInt("sn"), segs));

        byte[] stub = KeyStoreFrame.encodeEqValue(segs);
        loop.eqKeyStoreValue = stub.clone();
        JSONObject rres = new JSONObject(bridge.getHearingEq());
        expect(rres.getBoolean("ok") && rres.getBoolean("sent"), "EQ 读发送应成功: " + rres);
        JSONObject rval = sink.bySeq(rres.getInt("seq"));
        byte[] value = KeyStoreFrame.parseEqReadPayload(unhex(rval.getString("payloadHex")));
        bytes("读回 88B", value, stub);
        KeyStoreFrame.EqSeg[] back = KeyStoreFrame.decodeEqValue(value);
        expect(back != null, "读回值应可解码");
        for (int i = 0; i < 10; i++) expect(back[i].freqHz == segs[i].freqHz, "读回 freq[" + i + "]");

        // 未武装拒绝 + 非协议桩值读回后软回退应拒（endToEnd 的 key30 未武装已覆盖）
        H3SBridge unarmed = new H3SBridge(new LoopbackTransport(), new Events());
        expect(new JSONObject(unarmed.getHearingEq()).getBoolean("ok") == false, "未武装 key31 读应拒绝");
        expect(new JSONObject(unarmed.setHearingEq(segJson(segs))).getBoolean("ok") == false, "未武装 key31 写应拒绝");
    }

    // 软回退受限入口（H5-DEF-01）：只接受可解码协议原值，Java 侧自行置魔数首字节 0，
    // 设备桩收到的帧 = 原值且仅 byte0 差异；任意/坏形状原值拒绝；未武装拒绝。
    static void softRollback() throws Exception {
        LoopbackTransport loop = new LoopbackTransport();
        Events sink = new Events();
        H3SBridge bridge = new H3SBridge(loop, sink);
        loop.connect();
        bridge.armWrite(true);

        // key30：原值 = 真实 MPO 协议值
        byte[] mpo = KeyStoreFrame.encodeMpoValue(95.0f);
        JSONObject r30 = new JSONObject(bridge.invalidateHearingCfg(hex(mpo)));
        expect(r30.getBoolean("ok") && r30.getBoolean("sent"), "key30 软回退应可发送: " + r30);
        byte[] sent30 = KeyStoreFrame.parseWriteCommand(loop.receivedFrames.get(0));
        expect(sent30[0] == 0 && java.util.Arrays.equals(sent30, 1, 8, mpo, 1, 8),
                "key30 软回退帧应只改魔数首字节");
        eqInt(KeyStoreFrame.parseWriteAck(unhex(sink.bySeq(r30.getInt("seq")).getString("payloadHex"))), 0);

        // key31：原值 = 真实 EQ 协议值（88B）
        byte[] eq = KeyStoreFrame.encodeEqValue(flatSegs());
        JSONObject r31 = new JSONObject(bridge.invalidateHearingEq(hex(eq)));
        expect(r31.getBoolean("ok") && r31.getBoolean("sent"), "key31 软回退应可发送: " + r31);
        byte[] sent31 = KeyStoreFrame.parseEqWriteCommand(loop.receivedFrames.get(1));
        expect(sent31[0] == 0 && java.util.Arrays.equals(sent31, 1, 88, eq, 1, 88),
                "key31 软回退帧应只改魔数首字节");
        eqInt(KeyStoreFrame.parseWriteAck(unhex(sink.bySeq(r31.getInt("seq")).getString("payloadHex"))), 0);

        // 负例：非协议原值（坏魔数/坏版本/长度错/任意字节）与已失效值都不接受
        for (String bad : new String[]{"00" + hex(mpo).substring(2),          // 已是坏魔数
                                       "48410200" + "00".repeat(72),          // ver=2 冒充 8B？长度即错
                                       "0102030405060708",                    // 任意字节
                                       hex(eq).replaceFirst("4841", "4842")}) { // 坏魔数第二字节
            expect(new JSONObject(bridge.invalidateHearingCfg(bad)).getBoolean("ok") == false,
                    "key30 非协议原值应拒绝: " + bad.substring(0, 8));
            expect(new JSONObject(bridge.invalidateHearingEq(bad)).getBoolean("ok") == false,
                    "key31 非协议原值应拒绝: " + bad.substring(0, 8));
        }
        // 未武装拒绝
        H3SBridge unarmed = new H3SBridge(new LoopbackTransport(), new Events());
        expect(new JSONObject(unarmed.invalidateHearingCfg(hex(mpo))).getBoolean("ok") == false, "未武装软回退应拒绝");
        expect(new JSONObject(unarmed.invalidateHearingEq(hex(eq))).getBoolean("ok") == false, "未武装软回退应拒绝");
    }

    static String segJson(KeyStoreFrame.EqSeg[] segs) {
        StringBuilder sb = new StringBuilder("[");
        for (int i = 0; i < segs.length; i++) {
            if (i > 0) sb.append(',');
            sb.append("{\"f\":").append(segs[i].freqHz).append(",\"g\":").append(segs[i].gainDb).append('}');
        }
        return sb.append(']').toString();
    }
}
