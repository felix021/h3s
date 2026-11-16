import cn.felix021.h3squery.AndroidBleTransport;
import cn.felix021.h3squery.H3SBridge;
import cn.felix021.h3squery.SimulationGattLink;
import java.io.BufferedReader;
import java.io.InputStream;
import java.io.InputStreamReader;
import java.io.OutputStream;
import java.net.ServerSocket;
import java.net.Socket;
import java.nio.charset.StandardCharsets;
import java.util.ArrayList;
import java.util.Base64;
import java.util.List;
import java.util.concurrent.CountDownLatch;
import java.util.concurrent.TimeUnit;
import java.util.concurrent.atomic.AtomicInteger;
import org.json.JSONObject;

/**
 * SimulationGattLink 契约测试（路 SIMB1A / M1-a）：真 socket 回环——测试内起最小
 * bridge server 桩（说 tools/sim_bridge_server.py 同款 JSON-lines 协议的 Java 版），
 * 验证 Link 面的连接相位（本地合成）、数据面往返（ble_write 原样到达 server、
 * chunk_ack→onCharacteristicWrite、ble_notify→onCharacteristicChanged 字节级一致）、
 * 拨号失败/closeGatt/服务端断开的可见错误路径。TransportContractTest 已覆盖
 * AndroidBleTransport 状态机配假 Link；本测试补「仿真 Link 对真 socket」这一半。
 * 全程 localhost，不触 android.bluetooth（红线：只 AVD/本地）。
 */
public class SimulationGattLinkTest {
    static void expect(boolean b, String msg) { if (!b) throw new AssertionError(msg); }

    /** 直跑调度器（回调在读/IO 线程上同步执行；延迟被忽略）。 */
    static final AndroidBleTransport.Scheduler DIRECT = new AndroidBleTransport.Scheduler() {
        @Override public void postDelayed(long delayMs, Runnable r) { r.run(); }
    };

    /** 回调记录器（线程安全：回调可能来自 socket 读线程）。 */
    static final class Recorder implements AndroidBleTransport.Link.Callbacks {
        final CountDownLatch connected = new CountDownLatch(1);
        final CountDownLatch services = new CountDownLatch(1);
        final CountDownLatch descriptor = new CountDownLatch(1);
        final CountDownLatch mtu = new CountDownLatch(1);
        final CountDownLatch writeAck = new CountDownLatch(1);
        final CountDownLatch notify = new CountDownLatch(1);
        final CountDownLatch disconnected = new CountDownLatch(1);
        final CountDownLatch scanHit = new CountDownLatch(1);
        final List<Integer> states = new ArrayList<Integer>();
        final List<byte[]> notifies = new ArrayList<byte[]>();
        int reportedMtu = -1, reportedMtuStatus = -1;
        public synchronized void onScanResult() { scanHit.countDown(); }
        public void onScanFailed(int error) { }
        public synchronized void onConnectionStateChange(int status, int state) {
            states.add((status << 8) | state);
            if (status == 0 && state == AndroidBleTransport.STATE_CONNECTED) connected.countDown();
            else disconnected.countDown();
        }
        public void onServicesDiscovered(int status) { services.countDown(); }
        public void onDescriptorWrite(int status) { descriptor.countDown(); }
        public synchronized void onMtuChanged(int mtu, int status) { reportedMtu = mtu; reportedMtuStatus = status; this.mtu.countDown(); }
        public void onCharacteristicWrite(int status) { writeAck.countDown(); }
        public synchronized void onCharacteristicChanged(byte[] value) { notifies.add(value); notify.countDown(); }
    }

    /** 最小 bridge server 桩：hello→hello_ok；ble_write→记录+chunk_ack+（可配）ble_notify 回帧。 */
    static final class FakeBridge {
        final ServerSocket server;
        final List<byte[]> writes = new ArrayList<byte[]>();
        final CountDownLatch helloSeen = new CountDownLatch(1);
        volatile byte[] notifyPayload;                       // 非 null=收到 ble_write 后回推
        private volatile Socket client;
        private volatile OutputStream out;
        FakeBridge() throws Exception { server = new ServerSocket(0); }
        int port() { return server.getLocalPort(); }
        void start() {
            Thread t = new Thread(new Runnable() { public void run() {
                try {
                    final Socket s = server.accept();
                    client = s;
                    out = s.getOutputStream();
                    InputStream in = s.getInputStream();
                    BufferedReader r = new BufferedReader(new InputStreamReader(in, StandardCharsets.UTF_8));
                    String line;
                    while ((line = r.readLine()) != null) {
                        JSONObject msg = new JSONObject(line);
                        String type = msg.optString("t", "");
                        if ("hello".equals(type)) {
                            JSONObject ok = new JSONObject();
                            ok.put("t", "hello_ok").put("world", new JSONObject().put("mode", "test"));
                            send(ok);
                            helloSeen.countDown();
                        } else if ("ble_write".equals(type)) {
                            byte[] chunk = Base64.getDecoder().decode(msg.getString("data"));
                            synchronized (writes) { writes.add(chunk); }
                            send(new JSONObject().put("t", "chunk_ack"));
                            byte[] payload = notifyPayload;
                            if (payload != null) send(new JSONObject().put("t", "ble_notify")
                                .put("data", Base64.getEncoder().encodeToString(payload)));
                        }
                    }
                } catch (Exception ignored) { }
            } }, "fake-bridge");
            t.setDaemon(true);
            t.start();
        }
        void send(JSONObject o) {
            try {
                OutputStream o2 = out;
                if (o2 != null) { o2.write((o.toString() + "\n").getBytes(StandardCharsets.UTF_8)); o2.flush(); }
            } catch (Exception ignored) { }
        }
        void dropClient() { Socket s = client; if (s != null) try { s.close(); } catch (Exception ignored) { } }
        void close() { dropClient(); try { server.close(); } catch (Exception ignored) { } }
    }

    static void await(CountDownLatch l, String what) throws Exception {
        expect(l.await(5, TimeUnit.SECONDS), what + " 超时");
    }

    public static void main(String[] args) throws Exception {
        fullRoundTrip();
        writeBeforeConnectRejected();
        dialFailureVisible();
        closeGattIdempotent();
        serverDropVisible();
        System.out.println("SimulationGattLinkTest passed: 五段式相位（本地合成）+ ble_write/chunk_ack/ble_notify "
            + "字节级往返 + 拨号失败/closeGatt/服务端断开的可见错误路径。未触 android.bluetooth。");
    }

    /** 端到端：hello→CONNECTED→发现→CCCD→MTU(247)→写片原样到 server→ack+notify 字节级回流。 */
    static void fullRoundTrip() throws Exception {
        FakeBridge bridge = new FakeBridge();
        byte[] query = H3SBridge.buildReadStatusFrame(5, 0x17);          // FE DC BA C0 FF 00 04 05 FE 01 17 EF
        byte[] response = new byte[]{(byte) 0xfe, (byte) 0xdc, (byte) 0xba, 0x00, (byte) 0xff, 0x00, 0x07,
            0x00, 0x05, 0x17, 0x03, 0x01, 0x01, 0x02, (byte) 0xef};      // protocol.md:40 响应形（SN=5）
        bridge.notifyPayload = response;
        bridge.start();
        try {
            SimulationGattLink link = new SimulationGattLink("127.0.0.1", bridge.port(), DIRECT);
            Recorder rec = new Recorder();
            link.setCallbacks(rec);

            link.startScan();
            await(rec.scanHit, "扫描命中");
            link.connectGatt();
            await(rec.connected, "CONNECTED 回调");
            expect(link.discoverServices(), "discoverServices 应被接受");
            await(rec.services, "服务发现回调");
            expect(link.characteristicsPresent(), "A002/1/2 合成在场");
            expect(link.writeProperties() == AndroidBleTransport.PROPERTY_WRITE_NO_RESPONSE, "属性应为 4（H3S 实测）");
            expect(link.enableNotifications(), "订阅应被接受");
            await(rec.descriptor, "CCCD 回调");
            expect(link.requestMtu(517), "MTU 请求应被接受");
            await(rec.mtu, "MTU 回调");
            expect(rec.reportedMtu == 247 && rec.reportedMtuStatus == 0,
                "MTU 应回报 247/0，got " + rec.reportedMtu + "/" + rec.reportedMtuStatus);

            expect(link.writeChunk(query, AndroidBleTransport.WRITE_TYPE_NO_RESPONSE), "写片应入队");
            await(rec.writeAck, "chunk_ack→写回调");
            await(rec.notify, "ble_notify→onCharacteristicChanged");
            await(bridge.helloSeen, "server 收到 hello");
            synchronized (bridge.writes) {
                expect(bridge.writes.size() == 1, "server 应恰收一片: " + bridge.writes.size());
                expect(java.util.Arrays.equals(bridge.writes.get(0), query), "片字节应与帧一致");
            }
            expect(rec.notifies.size() == 1 && java.util.Arrays.equals(rec.notifies.get(0), response),
                "notify 字节应与 server 帧一致");
            link.closeGatt();
        } finally { bridge.close(); }
    }

    /** 未连接写片：同步拒绝（false），不产生任何回调。 */
    static void writeBeforeConnectRejected() {
        SimulationGattLink link = new SimulationGattLink("127.0.0.1", 1, DIRECT);
        Recorder rec = new Recorder();
        link.setCallbacks(rec);
        expect(!link.writeChunk(new byte[]{1}, AndroidBleTransport.WRITE_TYPE_NO_RESPONSE), "未连接应拒绝写片");
        link.closeGatt();
    }

    /** server 不在：拨号失败必须可见（133/DISCONNECTED），不能静默挂到 transport 30s 看门狗。 */
    static void dialFailureVisible() throws Exception {
        ServerSocket swizzle = new ServerSocket(0);
        int deadPort = swizzle.getLocalPort();
        swizzle.close();                                                  // 占住再释放=端口基本无人听
        SimulationGattLink link = new SimulationGattLink("127.0.0.1", deadPort, DIRECT);
        Recorder rec = new Recorder();
        link.setCallbacks(rec);
        link.connectGatt();
        await(rec.disconnected, "拨号失败回调");
        expect(!link.socketConnected(), "失败后不应标记连接");
        link.closeGatt();
    }

    /** closeGatt 幂等；关闭后写片拒绝、无迟到回调。 */
    static void closeGattIdempotent() throws Exception {
        FakeBridge bridge = new FakeBridge();
        bridge.start();
        try {
            SimulationGattLink link = new SimulationGattLink("127.0.0.1", bridge.port(), DIRECT);
            Recorder rec = new Recorder();
            link.setCallbacks(rec);
            link.connectGatt();
            await(rec.connected, "CONNECTED");
            link.closeGatt();
            link.closeGatt();                                             // 幂等
            expect(!link.writeChunk(new byte[]{1}, AndroidBleTransport.WRITE_TYPE_NO_RESPONSE), "关闭后应拒绝写片");
            int states = rec.states.size();
            Thread.sleep(150);                                           // 残留线程不应再报事件
            expect(rec.states.size() == states, "closeGatt 后不应有迟到状态回调");
        } finally { bridge.close(); }
    }

    /** 服务端断连（EOF）：可见断线（129/DISCONNECTED），映射 transport 的意外掉线路径。 */
    static void serverDropVisible() throws Exception {
        FakeBridge bridge = new FakeBridge();
        bridge.start();
        SimulationGattLink link = new SimulationGattLink("127.0.0.1", bridge.port(), DIRECT);
        Recorder rec = new Recorder();
        link.setCallbacks(rec);
        link.connectGatt();
        await(rec.connected, "CONNECTED");
        bridge.dropClient();
        await(rec.disconnected, "服务端断连回调");
        expect(!link.socketConnected(), "断连后连接标记应清除");
        link.closeGatt();
        bridge.close();
    }
}
