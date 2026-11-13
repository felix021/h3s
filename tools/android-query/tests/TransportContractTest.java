import cn.felix021.h3squery.AndroidBleTransport;
import cn.felix021.h3squery.BlePacketizer;
import cn.felix021.h3squery.EqFrame;
import cn.felix021.h3squery.EqFrame.EqBand;
import cn.felix021.h3squery.H3SBridge;
import cn.felix021.h3squery.H3sTransport;
import cn.felix021.h3squery.LoopbackTransport;
import cn.felix021.h3squery.SerialBleTransport;
import java.util.ArrayDeque;
import java.util.ArrayList;
import java.util.Arrays;
import java.util.List;
import org.json.JSONObject;

/**
 * 传输契约测试（路 AU / sim-bletransport）：同一套接口级断言同时跑
 *   - LoopbackTransport（Z 路通用串行实现的回环桩），与
 *   - AndroidBleTransport + FakeLink（真机适配层状态机 + 假 GATT——把 BluetoothGatt/
     * BluetoothLeScanner 收敛成 AndroidBleTransport.Link 的最小 mock，设备行为复用
 *   LoopbackTransport.buildSuccessResponse 的桩响应形状），
 * 验证两者在 H3sTransport 接口语义上一致：分片序列 = BlePacketizer.split、通知重组走
 * RcspDeframer、帧级/片级串行顺序、错误传播、写类型选择。不连真机（红线）。
 *
 * AndroidBleTransport 专属部分（连接状态机/重试/看门狗/MTU 双标志/断线重连）单独覆盖：
 * 默认手动调度器（scheduler=null，与 Loopback 同步驱动）+ FakeScheduler（断言定时值：
 * 扫描 10s/连接 30s/片写看门狗 5s/片间隔 10ms/响应超时 5s）。
 */
public class TransportContractTest {
    static void expect(boolean b, String msg) { if (!b) throw new AssertionError(msg); }
    static void eq(String name, int a, int b) { if (a != b) throw new AssertionError(name + ": " + a + " != " + b); }
    static void bytes(String name, byte[] a, byte[] b) {
        if (!java.util.Arrays.equals(a, b))
            throw new AssertionError(name + ":\n  got " + H3SBridge.hex(a) + "\n  exp " + H3SBridge.hex(b));
    }

    static byte[] unhex(String hex) {
        byte[] out = new byte[hex.length() / 2];
        for (int i = 0; i < out.length; i++) out[i] = (byte) Integer.parseInt(hex.substring(i * 2, i * 2 + 2), 16);
        return out;
    }

    static List<EqBand> oneBand() { return Arrays.asList(new EqBand(1000, 100, -800, 255)); }
    static List<EqBand> tenBands() {
        int[] freq = {32, 64, 125, 250, 500, 1000, 2000, 4000, 8000, 16000};
        int[] q = {40, 120, 80, 300, 250, 100, 500, 1000, 2000, 5};
        int[] gain = {-1200, -250, 0, 300, -400, 800, -1200, 1500, -3000, 32767};
        int[] iir = {0, 255, 3, 255, 4, 255, 255, 1, 255, 255};
        List<EqBand> bands = new ArrayList<EqBand>();
        for (int i = 0; i < 10; i++) bands.add(new EqBand(freq[i], q[i], gain[i], iir[i]));
        return bands;
    }

    // ================================ 桩 ================================

    /** 连接/帧事件 + bridge 事件收集器。 */
    static final class Events implements H3sTransport.Listener, H3SBridge.ResultSink {
        final List<byte[]> frames = new ArrayList<byte[]>();
        final List<Integer> mtus = new ArrayList<Integer>();
        final List<String> disconnects = new ArrayList<String>();
        final List<String> jsons = new ArrayList<String>();
        @Override public void onFrame(byte[] frame) { frames.add(frame); }
        @Override public void onMtuChanged(int mtu) { mtus.add(mtu); }
        @Override public void onDisconnected(String reason) { disconnects.add(reason); }
        @Override public void emit(String json) { jsons.add(json); }
        List<JSONObject> byEvent(String event) throws org.json.JSONException {
            List<JSONObject> out = new ArrayList<JSONObject>();
            for (String s : jsons) { JSONObject o = new JSONObject(s); if (event.equals(o.getString("event"))) out.add(o); }
            return out;
        }
    }

    /** 帧终态回调收集器。 */
    static final class Result {
        Boolean ok; String error;
        final H3sTransport.FrameCallback cb = new H3sTransport.FrameCallback() {
            @Override public void onDone(boolean done, String err) { ok = done; error = err; }
        };
        void expectOk(String what) {
            expect(ok != null && ok, what + " 应成功: " + error);
        }
        void expectFail(String what, String contains) {
            expect(ok != null && !ok, what + " 应失败");
            expect(error != null && error.contains(contains), what + " 错误应含「" + contains + "」: " + error);
        }
    }

    /**
     * 假 GATT 链路：实现 AndroidBleTransport.Link，同步回调（JVM 单线程）。
     * 设备侧行为 = 收片 RcspDeframer 重组 + LoopbackTransport.buildSuccessResponse 生成
     * 响应 + 按协商 MTU 分片走 notify 送回（与 LoopbackTransport 同一桩形状）。
     */
    static final class FakeLink implements AndroidBleTransport.Link {
        AndroidBleTransport.Link.Callbacks cb;
        // 配置
        int negotiatedMtu = 247;
        int writeProps = AndroidBleTransport.PROPERTY_WRITE_NO_RESPONSE;   // H3S 实测属性 4
        boolean charsPresent = true;
        boolean scanFindsDevice = true;
        boolean scanFails;
        String notReady;                       // 非 null=前置检查失败原因
        int failConnects;                      // 前 N 次 connectGatt 立即报断开(133)
        boolean rejectMtu;                     // requestMtu 返回 false
        boolean rejectWrite;                   // writeChunk 返回 false
        int writeStatus = 0;                   // onCharacteristicWrite 的 status
        boolean silentWrite;                   // 不回写回调（看门狗测试）
        boolean paused;                        // 挂起设备响应（串行测试）
        boolean reportMtuEarly;                // discoverServices 时主动报 MTU（Android 先例）
        // 探针
        final List<byte[]> writtenChunks = new ArrayList<byte[]>();
        final List<Integer> writeTypes = new ArrayList<Integer>();
        final List<byte[]> deviceFrames = new ArrayList<byte[]>();
        int scanStarts, scanStops, gattConnects, gattCloses, discovers, cccdWrites;
        final List<Integer> mtuRequests = new ArrayList<Integer>();
        private final SerialBleTransport.RcspDeframer deviceRx = new SerialBleTransport.RcspDeframer();
        private final ArrayDeque<byte[]> pausedFrames = new ArrayDeque<byte[]>();

        @Override public void setCallbacks(AndroidBleTransport.Link.Callbacks callbacks) { cb = callbacks; }
        @Override public String notReadyReason() { return notReady; }
        @Override public void startScan() {
            scanStarts++;
            if (scanFails) cb.onScanFailed(1);
            else if (scanFindsDevice) cb.onScanResult();
        }
        @Override public void stopScan() { scanStops++; }
        @Override public void connectGatt() {
            gattConnects++;
            if (failConnects > 0) { failConnects--; cb.onConnectionStateChange(133, AndroidBleTransport.STATE_DISCONNECTED); return; }
            cb.onConnectionStateChange(0, AndroidBleTransport.STATE_CONNECTED);
        }
        @Override public boolean discoverServices() {
            discovers++;
            if (reportMtuEarly) cb.onMtuChanged(negotiatedMtu, 0);         // Android 主动回报 MTU
            cb.onServicesDiscovered(0);
            return true;
        }
        @Override public boolean characteristicsPresent() { return charsPresent; }
        @Override public int writeProperties() { return writeProps; }
        @Override public boolean enableNotifications() { cccdWrites++; cb.onDescriptorWrite(0); return true; }
        @Override public boolean requestMtu(int mtu) {
            mtuRequests.add(mtu);
            if (rejectMtu) return false;
            cb.onMtuChanged(negotiatedMtu, 0);
            return true;
        }
        @Override public boolean writeChunk(byte[] chunk, int writeType) {
            writtenChunks.add(chunk.clone());
            writeTypes.add(writeType);
            if (rejectWrite) return false;
            if (!silentWrite) cb.onCharacteristicWrite(writeStatus);        // 写回调先行（真机时序）
            for (byte[] frame : deviceRx.feed(chunk)) {                     // 设备侧重组→响应
                deviceFrames.add(frame);
                if (paused) pausedFrames.add(frame);
                else respond(frame);
            }
            return true;
        }
        void respond(byte[] commandFrame) {
            byte[] response = LoopbackTransport.buildSuccessResponse(commandFrame);
            if (response == null) return;
            for (byte[] piece : BlePacketizer.split(response, negotiatedMtu)) cb.onCharacteristicChanged(piece);
        }
        void releaseResponses() {
            paused = false;
            while (!pausedFrames.isEmpty()) respond(pausedFrames.poll());
        }
        @Override public void closeGatt() { gattCloses++; }
        void dropConnection(int status) { cb.onConnectionStateChange(status, AndroidBleTransport.STATE_DISCONNECTED); }
    }

    /** 假调度器：记录 (delay, task) 供测试显式触发与断言定时值。 */
    static final class FakeScheduler implements AndroidBleTransport.Scheduler {
        final List<Long> delays = new ArrayList<Long>();
        final ArrayDeque<Runnable> queue = new ArrayDeque<Runnable>();
        @Override public void postDelayed(long delayMs, Runnable r) { delays.add(delayMs); queue.add(r); }
        void runNext() { if (!queue.isEmpty()) queue.poll().run(); }
        void runAll() { while (!queue.isEmpty()) queue.poll().run(); }      // 新入队任务一并消费（无重调度循环）
    }

    /** 契约测试的传输适配器：统一 Loopback 与 Android+FakeLink 的设备侧探针。 */
    interface Harness {
        String name();
        H3sTransport transport();
        /** 连接并驱动到稳定（手动调度器下 connect 触发的片任务要显式排空才等价真机时钟）。 */
        void connect();
        List<byte[]> deviceChunks();
        List<byte[]> deviceFrames();
        void pauseResponses();
        void releaseResponses();
    }

    static final class LoopbackHarness implements Harness {
        final LoopbackTransport loopback;
        LoopbackHarness(int mtu) { loopback = new LoopbackTransport(mtu); }
        @Override public String name() { return "loopback"; }
        @Override public H3sTransport transport() { return loopback; }
        @Override public void connect() { loopback.connect(); loopback.runScheduler(); }
        @Override public List<byte[]> deviceChunks() { return loopback.writtenChunks; }
        @Override public List<byte[]> deviceFrames() { return loopback.receivedFrames; }
        @Override public void pauseResponses() { loopback.pauseResponses(); }
        @Override public void releaseResponses() { loopback.releaseResponses(); }
    }

    static final class AndroidHarness implements Harness {
        final FakeLink link = new FakeLink();
        final AndroidBleTransport transport;                                // scheduler=null=手动队列
        AndroidHarness(int mtu) { link.negotiatedMtu = mtu; transport = new AndroidBleTransport(link); }
        @Override public String name() { return "android"; }
        @Override public H3sTransport transport() { return transport; }
        @Override public void connect() { transport.connect(); transport.runScheduler(); }
        @Override public List<byte[]> deviceChunks() { return link.writtenChunks; }
        @Override public List<byte[]> deviceFrames() { return link.deviceFrames; }
        @Override public void pauseResponses() { link.paused = true; }
        @Override public void releaseResponses() { link.releaseResponses(); transport.runScheduler(); }
    }

    /** 每次调用建全新 harness（契约方法之间不共享设备侧探针状态）。 */
    interface HarnessFactory { Harness create(); }

    static final HarnessFactory LOOPBACK_247 = new HarnessFactory() { public Harness create() { return new LoopbackHarness(247); } };
    static final HarnessFactory ANDROID_247 = new HarnessFactory() { public Harness create() { return new AndroidHarness(247); } };
    static final HarnessFactory LOOPBACK_27 = new HarnessFactory() { public Harness create() { return new LoopbackHarness(27); } };
    static final HarnessFactory ANDROID_27 = new HarnessFactory() { public Harness create() { return new AndroidHarness(27); } };

    // ================================ 契约（两种实现同断言） ================================

    public static void main(String[] args) throws Exception {
        HarnessFactory[] mtu247 = {LOOPBACK_247, ANDROID_247};
        HarnessFactory[] mtu27 = {LOOPBACK_27, ANDROID_27};
        for (HarnessFactory f : mtu247) {
            contractEqWrite(f.create());
            contractSerialTwoFrames(f.create());
            contractNotReadyRejects(f.create());
            contractReconnectListenerSurvives(f.create());
        }
        for (HarnessFactory f : mtu27) contractFragmentation(f.create());
        androidWriteTypes();
        androidConnectHappyPath();
        androidMtuVariants();
        androidScanVariants();
        androidConnectRetries();
        androidConnectFailures();
        androidUnexpectedDisconnect();
        androidWriteWatchdog();
        androidSchedulerDelays();
        bridgeConnectionControl();
        System.out.println("TransportContractTest passed: 契约（EQ/串行/跨片/未连接拒绝/断线重连，"
            + "Loopback 与 AndroidBleTransport+FakeLink 同断言）+ Android 状态机（连接五段式/MTU 三变体/"
            + "扫描三变体/重试/失败路径/意外断线/写看门狗）+ 调度定时值（10s/30s/5s/10ms/5s）"
            + "+ bridge 连接控制。未连真机。");
    }

    /** 端到端契约：EQ 帧→分片序列=BlePacketizer.split→设备逐字节收到→响应经 notify 重组回 listener。 */
    static void contractEqWrite(Harness h) {
        Events events = new Events();
        H3sTransport t = h.transport();
        t.setListener(events);
        h.connect();
        expect(t.isReady(), h.name() + ": connect 后应就绪");
        eq(h.name() + " mtu", 247, t.mtu());
        eq(h.name() + " mtu 事件数", 1, events.mtus.size());

        byte[] frame = EqFrame.buildEqCommand(7, 1, -400, oneBand());
        Result r = new Result();
        t.write(frame, r.cb);
        r.expectOk(h.name() + " EQ 写");
        eq(h.name() + " 设备收帧数", 1, h.deviceFrames().size());
        bytes(h.name() + " EQ 帧逐字节", h.deviceFrames().get(0), frame);
        // 分片序列与 BlePacketizer 一致（244B 有效载荷，13+7B 帧单片）
        List<byte[]> expect = BlePacketizer.split(frame, t.mtu());
        eq(h.name() + " 片数", expect.size(), h.deviceChunks().size());
        for (int i = 0; i < expect.size(); i++) bytes(h.name() + " 片[" + i + "]", h.deviceChunks().get(i), expect.get(i));
        // 响应经 notify 重组回 listener，形状=status+SN 起（Loopback 桩，setter 载荷[未钉]）
        eq(h.name() + " listener 响应帧数", 1, events.frames.size());
        bytes(h.name() + " 响应帧", events.frames.get(0), LoopbackTransport.buildSuccessResponse(frame));
        expect(events.disconnects.isEmpty(), h.name() + ": 不应断线");
    }

    /** 跨片契约：MTU=27（载荷 24B）→ 81B 帧 4 片，设备侧与响应路径都重组正确。 */
    static void contractFragmentation(Harness h) {
        Events events = new Events();
        H3sTransport t = h.transport();
        t.setListener(events);
        h.connect();
        eq(h.name() + " 压小 MTU", 27, t.mtu());
        byte[] frame = EqFrame.buildEqCommand(3, 2, -400, tenBands());      // 8+3+70=81B
        Result r = new Result();
        t.write(frame, r.cb);
        r.expectOk(h.name() + " 跨片 EQ 写");
        eq(h.name() + " 81B/24B 片数", 4, h.deviceChunks().size());
        eq(h.name() + " 片0 长", 24, h.deviceChunks().get(0).length);
        bytes(h.name() + " 跨片重组帧", h.deviceFrames().get(0), frame);
        eq(h.name() + " 跨片响应到达", 1, events.frames.size());
    }

    /** 帧级串行契约：第二条帧在第一条响应前零发送；释放后按序完成。 */
    static void contractSerialTwoFrames(Harness h) {
        Events events = new Events();
        H3sTransport t = h.transport();
        t.setListener(events);
        h.connect();
        h.pauseResponses();
        byte[] f1 = H3SBridge.buildCustomCommand(11, H3SBridge.CMD_ANC, new byte[]{1, 1, 2});
        byte[] f2 = H3SBridge.buildCustomCommand(12, H3SBridge.CMD_PROMPT_VOLUME, new byte[]{6, 0});
        Result r1 = new Result(), r2 = new Result();
        t.write(f1, r1.cb);
        int chunksAfterF1 = h.deviceChunks().size();
        t.write(f2, r2.cb);
        eq(h.name() + " 帧2 未发出（片数不变）", chunksAfterF1, h.deviceChunks().size());
        expect(r1.ok == null && r2.ok == null, h.name() + ": 释放前都无终态");
        h.releaseResponses();
        r1.expectOk(h.name() + " 帧1");
        r2.expectOk(h.name() + " 帧2");
        eq(h.name() + " 释放后设备收帧数", 2, h.deviceFrames().size());
        bytes(h.name() + " 帧1 逐字节", h.deviceFrames().get(0), f1);
        bytes(h.name() + " 帧2 逐字节", h.deviceFrames().get(1), f2);
    }

    /** 未连接拒绝契约：connect 前写→排队不发；connect 后开闸发出（Z 路行为对齐）。 */
    static void contractNotReadyRejects(Harness h) {
        Events events = new Events();
        H3sTransport t = h.transport();
        t.setListener(events);
        Result r = new Result();
        byte[] frame = H3SBridge.buildCustomCommand(21, H3SBridge.CMD_ANC, new byte[]{3, 1, 4});
        t.write(frame, r.cb);                                               // 未连接：只排队
        expect(!t.isReady(), h.name() + ": 未连接");
        eq(h.name() + " 未连接零发送", 0, h.deviceChunks().size());
        h.connect();                                                        // 就绪即开闸（排空 connect 触发的片任务）
        r.expectOk(h.name() + " 连接后积压帧");
        eq(h.name() + " 积压帧发出", 1, h.deviceFrames().size());
        bytes(h.name() + " 积压帧逐字节", h.deviceFrames().get(0), frame);
    }

    /** 断线→重连契约：listener 存活（不因断线被清掉），重连后 mtu 事件与响应照常。 */
    static void contractReconnectListenerSurvives(Harness h) {
        Events events = new Events();
        H3sTransport t = h.transport();
        t.setListener(events);
        h.connect();
        byte[] f1 = H3SBridge.buildCustomCommand(31, H3SBridge.CMD_PROMPT_VOLUME, new byte[]{3, 0});
        Result r1 = new Result();
        t.write(f1, r1.cb);
        r1.expectOk(h.name() + " 断线前写");
        t.disconnect();
        eq(h.name() + " 断线事件", 1, events.disconnects.size());
        Result r2 = new Result();
        byte[] f2 = H3SBridge.buildCustomCommand(32, H3SBridge.CMD_ANC, new byte[]{2, 0, 0});
        t.write(f2, r2.cb);                                                 // 断开状态：排队等重连
        h.connect();                                                        // 重连
        r2.expectOk(h.name() + " 重连后积压帧");
        eq(h.name() + " 重连后 mtu 事件再次上报", 2, events.mtus.size());
        eq(h.name() + " 重连后设备收帧数", 2, h.deviceFrames().size());
        bytes(h.name() + " 重连后帧", h.deviceFrames().get(1), f2);
        expect(events.disconnects.size() == 1, h.name() + ": 重连成功不应再报断线");
    }

    // ================================ AndroidBleTransport 专属 ================================

    /** 写类型选择：属性 4（H3S 实测）→NO_RESPONSE；属性 8（WRITE）→DEFAULT；无写属性→连接失败。 */
    static void androidWriteTypes() {
        FakeLink props4 = new FakeLink();
        AndroidBleTransport t4 = new AndroidBleTransport(props4);
        t4.connect();
        expect(t4.isReady(), "属性 4 应就绪");
        eq("属性 4 → NO_RESPONSE", AndroidBleTransport.WRITE_TYPE_NO_RESPONSE, t4.writeType());
        t4.write(H3SBridge.buildCustomCommand(1, H3SBridge.CMD_ANC, new byte[]{1, 1, 2}), new Result().cb);
        for (int type : props4.writeTypes) eq("写出类型", AndroidBleTransport.WRITE_TYPE_NO_RESPONSE, type);

        FakeLink props8 = new FakeLink();
        props8.writeProps = AndroidBleTransport.PROPERTY_WRITE;
        AndroidBleTransport t8 = new AndroidBleTransport(props8);
        t8.connect();
        eq("属性 8 → DEFAULT", AndroidBleTransport.WRITE_TYPE_DEFAULT, t8.writeType());

        FakeLink none = new FakeLink();
        none.writeProps = 0;                                                // GATT 正常、特征无写属性→连接失败重试
        AndroidBleTransport t0 = new AndroidBleTransport(none);
        Events e0 = new Events();
        t0.setListener(e0);
        t0.connect();
        t0.runScheduler(); t0.runScheduler(); t0.runScheduler();            // 驱动退避+第二次尝试+其退避
        expect(!t0.isReady(), "无写属性不应就绪");
        expect(e0.disconnects.get(0).contains("WRITE"), "错误应提到 WRITE 属性: " + e0.disconnects.get(0));
        eq("无写属性尝试 2 次", 2, none.gattConnects);
    }

    /** 连接五段式（扫描→GATT→发现→CCCD→MTU）+ 计数探针。 */
    static void androidConnectHappyPath() {
        FakeLink link = new FakeLink();
        AndroidBleTransport t = new AndroidBleTransport(link);
        Events events = new Events();
        t.setListener(events);
        t.connect();
        expect(t.isReady(), "五段式后应就绪");
        expect("READY".equals(t.phase()), "就绪后相位应为 READY: " + t.phase());
        eq("mtu=实测 247", 247, t.mtu());
        eq("扫描启动 1 次", 1, link.scanStarts);
        eq("扫描停止 1 次", 1, link.scanStops);
        eq("GATT 连接 1 次", 1, link.gattConnects);
        eq("服务发现 1 次", 1, link.discovers);
        eq("CCCD 写 1 次", 1, link.cccdWrites);
        eq("MTU 请求 1 次（值 517）", 1, link.mtuRequests.size());
        eq("MTU 请求值", 517, link.mtuRequests.get(0));
        eq("mtu 事件", 1, events.mtus.size());
        // 幂等：连接中/已连接重复 connect 是 no-op
        t.connect();
        eq("重复 connect no-op", 1, link.gattConnects);
        // 主动断开→释放
        t.disconnect();
        expect(!t.isReady(), "断开后未就绪");
        eq("断线事件", 1, events.disconnects.size());
        eq("GATT 关闭 1 次", 1, link.gattCloses);
    }

    /** MTU 三变体：主动早报（不重复 requestMtu）、协商被拒（按 23 就绪分 20B 片）、协商失败 status!=0。 */
    static void androidMtuVariants() {
        FakeLink early = new FakeLink();
        early.reportMtuEarly = true;
        AndroidBleTransport tEarly = new AndroidBleTransport(early);
        tEarly.connect();
        expect(tEarly.isReady(), "早报 MTU 应就绪");
        eq("早报时不再 requestMtu", 0, early.mtuRequests.size());
        eq("早报就绪 MTU", 247, tEarly.mtu());

        FakeLink reject = new FakeLink();
        reject.rejectMtu = true;
        AndroidBleTransport tReject = new AndroidBleTransport(reject);
        tReject.connect();
        expect(tReject.isReady(), "MTU 请求被拒仍应按默认 23 就绪");
        eq("被拒后 MTU", 23, tReject.mtu());
        Result r = new Result();
        byte[] frame = EqFrame.buildEqCommand(2, 1, -400, oneBand());       // 21B（8 头+3+10 data）
        tReject.write(frame, r.cb);
        r.expectOk("小 MTU 写");
        eq("21B/20B 片数", 2, reject.writtenChunks.size());                 // 20+1 两片
        byte[] big = EqFrame.buildEqCommand(3, 2, -400, tenBands());        // 81B
        Result r2 = new Result();
        int before = reject.writtenChunks.size();
        tReject.write(big, r2.cb);
        r2.expectOk("小 MTU 跨片写");
        eq("81B/20B 片数", 5, reject.writtenChunks.size() - before);        // 同链路两次写，取增量
    }

    /** 扫描三变体：命中即连、超时转直连、启动失败转直连。 */
    static void androidScanVariants() {
        FakeLink hit = new FakeLink();
        hit.scanFindsDevice = true;
        AndroidBleTransport t1 = new AndroidBleTransport(hit);
        t1.connect();
        expect(t1.isReady(), "扫描命中应连接");
        eq("命中后连接 1 次", 1, hit.gattConnects);

        FakeLink none = new FakeLink();
        none.scanFindsDevice = false;
        AndroidBleTransport t2 = new AndroidBleTransport(none);
        t2.connect();
        expect(!t2.isReady(), "扫描未命中时应还在 SCAN 相位等超时");
        eq("扫描超时前未连接", 0, none.gattConnects);
        expect("SCAN".equals(t2.phase()), "等待期相位应为 SCAN: " + t2.phase());
        t2.runScheduler();                                                  // 推进扫描超时任务→转直连
        expect(t2.isReady(), "超时转直连应就绪");
        eq("超时后连接 1 次", 1, none.gattConnects);
        eq("超时后停扫", 1, none.scanStops);

        FakeLink fails = new FakeLink();
        fails.scanFails = true;
        AndroidBleTransport t3 = new AndroidBleTransport(fails);
        t3.connect();
        expect(t3.isReady(), "扫描启动失败转直连应就绪");
        eq("失败后连接 1 次", 1, fails.gattConnects);

        FakeLink l4 = new FakeLink();
        AndroidBleTransport t5 = new AndroidBleTransport(l4);
        t5.setScanEnabled(false);
        t5.connect();
        expect(t5.isReady(), "关扫描直连应就绪");
        eq("关扫描零扫描", 0, l4.scanStarts);
    }

    /** 连接重试：首次 GATT 断开(133)→退避 1s→第二次成功；重试期间旧帧仍开闸。 */
    static void androidConnectRetries() {
        FakeLink link = new FakeLink();
        link.failConnects = 1;
        AndroidBleTransport t = new AndroidBleTransport(link);
        Events events = new Events();
        t.setListener(events);
        t.connect();
        expect(!t.isReady(), "首次失败后应退避中");
        eq("第一次尝试已发生", 1, link.gattConnects);
        expect("IDLE".equals(t.phase()), "退避期相位应为 IDLE: " + t.phase());
        t.runScheduler();                                                   // 退避任务→第二次尝试
        expect(t.isReady(), "重试后应就绪");
        eq("重试后连接数", 2, link.gattConnects);
        expect(events.disconnects.isEmpty(), "重试成功不应报断线");
        Result r = new Result();
        t.write(H3SBridge.buildCustomCommand(41, H3SBridge.CMD_ANC, new byte[]{1, 1, 2}), r.cb);
        r.expectOk("重试成功后写");
    }

    /** 连接失败路径：前置检查失败、重试耗尽、特征缺失——都收敛为一次 onDisconnected(reason)。 */
    static void androidConnectFailures() {
        FakeLink notReady = new FakeLink();
        notReady.notReady = "蓝牙未开启";
        AndroidBleTransport t1 = new AndroidBleTransport(notReady);
        Events e1 = new Events();
        t1.setListener(e1);
        t1.connect();
        expect(!t1.isReady(), "前置失败不应就绪");
        eq("前置失败零连接", 0, notReady.gattConnects);
        eq("前置失败事件", 1, e1.disconnects.size());
        expect(e1.disconnects.get(0).contains("蓝牙未开启"), "原因应透传: " + e1.disconnects.get(0));

        FakeLink always = new FakeLink();
        always.failConnects = 99;
        AndroidBleTransport t2 = new AndroidBleTransport(always);
        Events e2 = new Events();
        t2.setListener(e2);
        t2.connect();
        t2.runScheduler();                                                  // 第一次退避
        t2.runScheduler();                                                  // 第二次尝试失败→第二次退避前已耗尽
        expect(!t2.isReady(), "重试耗尽不应就绪");
        eq("重试耗尽连接数", 2, always.gattConnects);
        expect(!e2.disconnects.isEmpty(), "重试耗尽应报断线");
        expect(e2.disconnects.get(0).contains("已尝试 2 次"), "错误应含尝试次数: " + e2.disconnects.get(0));

        FakeLink noChars = new FakeLink();
        noChars.charsPresent = false;                                       // GATT 正常、A002/1/2 缺失→连接失败重试
        AndroidBleTransport t3 = new AndroidBleTransport(noChars);
        Events e3 = new Events();
        t3.setListener(e3);
        t3.connect();
        t3.runScheduler(); t3.runScheduler(); t3.runScheduler();
        expect(!t3.isReady(), "特征缺失不应就绪");
        expect(e3.disconnects.get(0).contains("A002"), "错误应提到 A002: " + e3.disconnects.get(0));
    }

    /** READY 后意外断线：在途帧失败回调 + 一次 onDisconnected；重连后链路完整（listener 存活）。 */
    static void androidUnexpectedDisconnect() {
        FakeLink link = new FakeLink();
        AndroidBleTransport t = new AndroidBleTransport(link);
        Events events = new Events();
        t.setListener(events);
        t.connect();
        link.paused = true;
        Result r1 = new Result(), r2 = new Result();
        t.write(H3SBridge.buildCustomCommand(51, H3SBridge.CMD_ANC, new byte[]{1, 1, 2}), r1.cb);
        t.write(H3SBridge.buildCustomCommand(52, H3SBridge.CMD_ANC, new byte[]{2, 0, 0}), r2.cb);
        link.dropConnection(0x8e);                                          // 真机历史错误码形状
        r1.expectFail("在途帧1", "断开");
        r2.expectFail("排队帧2", "断开");
        eq("意外断线事件", 1, events.disconnects.size());
        expect(events.disconnects.get(0).contains("status=142"), "原因应含 status: " + events.disconnects.get(0));
        eq("断线后关闭 GATT", 1, link.gattCloses);
        // 重连：listener 仍活着（SerialBleTransport 不再清 listener），响应链恢复
        link.paused = false;                                               // 设备恢复出响应
        Result r3 = new Result();
        t.write(H3SBridge.buildCustomCommand(53, H3SBridge.CMD_ANC, new byte[]{3, 1, 4}), r3.cb);
        t.connect();
        r3.expectOk("重连后积压帧");
        eq("重连后 mtu 事件", 2, events.mtus.size());
        eq("重连不重复报断线", 1, events.disconnects.size());
    }

    /** 写看门狗：写回调 5s 缺失→断链（在途/排队帧失败），之后写请求不再触达 GATT。 */
    static void androidWriteWatchdog() {
        FakeLink link = new FakeLink();
        link.silentWrite = true;
        FakeScheduler scheduler = new FakeScheduler();
        AndroidBleTransport t = new AndroidBleTransport(link, scheduler, null);
        Events events = new Events();
        t.setListener(events);
        t.connect();
        scheduler.runAll();                                                 // 跳转任务→连接五段式→过期定时任务全部 no-op
        Result r = new Result();
        t.write(H3SBridge.buildCustomCommand(61, H3SBridge.CMD_ANC, new byte[]{1, 1, 2}), r.cb);
        scheduler.runNext();                                                // 跳转任务→排队片任务（delay 10）
        expect(r.ok == null, "片任务未执行");
        scheduler.runNext();                                                // 片任务→doWriteChunk→排片写看门狗（delay 5000）→静默写无回调
        expect(r.ok == null, "静默写时帧应仍在途");
        scheduler.runNext();                                                // 片写看门狗 → 断链
        r.expectFail("看门狗后帧", "片写回调超时");
        expect(!t.isReady(), "看门狗后应断链");
        eq("看门狗断链事件", 1, events.disconnects.size());
        eq("看门狗关闭 GATT", 1, link.gattCloses);
        int chunks = link.writtenChunks.size();
        Result r2 = new Result();
        t.write(H3SBridge.buildCustomCommand(62, H3SBridge.CMD_ANC, new byte[]{2, 0, 0}), r2.cb);
        scheduler.runAll();
        eq("断链后写不触达 GATT", chunks, link.writtenChunks.size());
        expect(r2.ok == null, "断链后的写停在排队（等重连）");
    }

    /** 调度定时值：跳转 0 / 片间隔 10ms / 片写看门狗 5s / 响应超时 5s / 扫描超时 10s / 连接看门狗 30s / 退避 1s。 */
    static void androidSchedulerDelays() {
        FakeLink link = new FakeLink();
        FakeScheduler scheduler = new FakeScheduler();
        AndroidBleTransport t = new AndroidBleTransport(link, scheduler, null);
        t.connect();
        scheduler.runNext();                                                // 跳转任务（delay 0）→connectInternal
        // 连接期：扫描超时 10s + 连接看门狗 30s（假链路同步完成五段式）
        expect(containsOnce(scheduler, 10000), "应有扫描超时 10s: " + scheduler.delays);
        expect(containsOnce(scheduler, 30000), "应有连接看门狗 30s: " + scheduler.delays);
        expect(t.isReady(), "假链路同步就绪");
        scheduler.runAll();                                                 // 过期定时任务全部 no-op
        expect(t.isReady(), "过期任务不应误伤连接");
        t.write(H3SBridge.buildCustomCommand(71, H3SBridge.CMD_ANC, new byte[]{1, 1, 2}), new Result().cb);
        scheduler.runAll();                                                 // 跳转→片任务→写回调→响应
        expect(containsOnce(scheduler, 10), "应有片间隔 10ms: " + scheduler.delays);
        expect(containsOnce(scheduler, 5000), "应有 5s 看门狗/超时: " + scheduler.delays);
        eq("5s 任务数（写看门狗+响应超时）", 2, count(scheduler, 5000));
        // 退避 1s：一次失败连接后
        FakeScheduler s2 = new FakeScheduler();
        FakeLink l2 = new FakeLink();
        l2.failConnects = 1;
        AndroidBleTransport t2 = new AndroidBleTransport(l2, s2, null);
        t2.connect();
        s2.runAll();
        expect(containsOnce(s2, 1000), "应有退避 1s: " + s2.delays);
        expect(t2.isReady(), "退避重试后应就绪");
    }

    static boolean containsOnce(FakeScheduler s, long delay) { return count(s, delay) >= 1; }
    static int count(FakeScheduler s, long delay) {
        int n = 0;
        for (long d : s.delays) if (d == delay) n++;
        return n;
    }

    /** bridge 连接控制（页面入口）：connect/disconnect/isConnected 三个方法的 JSON/事件契约。 */
    static void bridgeConnectionControl() throws Exception {
        // 传输形态（Android+FakeLink）
        FakeLink link = new FakeLink();
        AndroidBleTransport t = new AndroidBleTransport(link);
        Events events = new Events();
        H3SBridge b = new H3SBridge(t, events);
        expect(!b.isConnected(), "初始未连接");
        JSONObject r = new JSONObject(b.connect());
        expect(r.getBoolean("ok") && r.getBoolean("connecting"), "connect 应返回 connecting: " + r);
        expect(b.isConnected(), "connect 后就绪");
        b.armWrite(true);
        JSONObject w = new JSONObject(b.setAnc(1, 1, 2));
        expect(w.getBoolean("ok") && w.getBoolean("sent"), "连接后写应发出");
        JSONObject resp = events.byEvent("response").get(0);
        eq("响应 seq", w.getInt("seq"), resp.getInt("seq"));
        JSONObject d = new JSONObject(b.disconnect());
        expect(d.getBoolean("ok"), "disconnect 返回 ok");
        expect(!b.isConnected(), "断开后未连接");
        expect(!events.byEvent("disconnected").isEmpty(), "应有断线事件");
        // 传输形态（Loopback）——同方法在 Z 路实现上同样成立
        LoopbackTransport loopback = new LoopbackTransport();
        H3SBridge bl = new H3SBridge(loopback, new Events());
        expect(new JSONObject(bl.connect()).getBoolean("ok"), "Loopback connect ok");
        expect(bl.isConnected(), "Loopback 连接");
        expect(new JSONObject(bl.disconnect()).getBoolean("ok"), "Loopback disconnect ok");
        // 纯组帧形态：无传输
        H3SBridge pure = new H3SBridge();
        expect(new JSONObject(pure.connect()).getString("error").contains("未注入"), "纯组帧 connect 应报未注入");
        expect(new JSONObject(pure.disconnect()).getString("error").contains("未注入"), "纯组帧 disconnect 应报未注入");
        expect(!pure.isConnected(), "纯组帧永远未连接");
    }
}
