import cn.felix021.h3squery.H3sUsbBridge;
import cn.felix021.h3squery.SimulationUsbLink;
import cn.felix021.h3squery.UsbScsi;
import java.io.BufferedReader;
import java.io.InputStream;
import java.io.InputStreamReader;
import java.io.OutputStream;
import java.net.ServerSocket;
import java.net.Socket;
import java.nio.charset.StandardCharsets;
import java.util.Base64;
import java.util.List;
import java.util.concurrent.CopyOnWriteArrayList;
import java.util.concurrent.CountDownLatch;
import java.util.concurrent.Executor;
import java.util.concurrent.TimeUnit;
import java.util.concurrent.atomic.AtomicInteger;
import org.json.JSONArray;
import org.json.JSONObject;

/**
 * SimulationUsbLink 契约测试（路 SIMB2A / M2）：真 socket 回环——测试内起最小 USB bridge
 * server 桩（说 tools/sim_bridge_server.py 同款 JSON-lines 协议的 Java 版：hello(sim-usb)/
 * usb_scan/usb_open/usb_bulk_out/usb_ctl），验证 Link 面的枚举（短连接+会话内复用）、授权
 * （即时授予）、open/channel 的 BOT 传输（bulkOut→usb_bulk_out、usb_bulk_in→bulkIn 精确
 * 长度、CSW tag 回显——用真 UsbScsi 跑 INQUIRY/FD05 对拍）、EP0 控制往返（8×ff/len0 断连
 * →onDetached 模拟拔出）、close 幂等与服务端断开的可见失败路径。H3sUsbBridgeTest 已覆盖
 * 业务桥配桩 Link；本测试补「仿真 USB Link 对真 socket」这一半。全程 localhost（红线：
 * 只 AVD/本地，不触 android.hardware.usb）。
 */
public class SimulationUsbLinkTest {
    static void expect(boolean b, String msg) { if (!b) throw new AssertionError(msg); }

    /** 直跑调度器（回调在读线程上同步执行）。 */
    static final Executor DIRECT = new Executor() {
        @Override public void execute(Runnable r) { r.run(); }
    };

    /** 回调记录器。 */
    static final class Recorder implements H3sUsbBridge.Callbacks {
        final CountDownLatch permission = new CountDownLatch(1);
        final CountDownLatch detached = new CountDownLatch(1);
        volatile String permissionKey;
        volatile boolean granted;
        volatile String detachedKey;
        public void onPermission(String key, boolean granted) {
            permissionKey = key; this.granted = granted; permission.countDown();
        }
        public void onDetached(String key) { detachedKey = key; detached.countDown(); }
    }

    /** 最小 USB bridge server 桩：
     *  - hello(sim-usb) → hello_ok；usb_scan → 一台 rom 身份设备；usb_open → ok。
     *  - usb_bulk_out 收 CBW（31B 'USBC'）→ 按应答策略回 usb_bulk_in：
     *      INQUIRY → 真 36B BR28/UBOOT1.00；FD05 → 512B 确定性数据；其后各回 13B CSW（tag 回显）。
     *  - usb_ctl len8 → 8×ff；len0 → 回空结果后 close（重枚举）。
     *  - M3：usb_arm → ok（armReject=true 时 ok=false）；usb_reboot → ok 后 close（重枚举拔出）。 */
    static final class FakeUsbServer {
        final ServerSocket server;
        final List<String> log = new CopyOnWriteArrayList<String>();
        final List<JSONObject> armRequests = new CopyOnWriteArrayList<JSONObject>();
        volatile boolean armReject = false;
        final AtomicInteger connections = new AtomicInteger();
        final CountDownLatch dropped = new CountDownLatch(1);
        private volatile Socket client;

        FakeUsbServer() throws Exception { server = new ServerSocket(0); }
        int port() { return server.getLocalPort(); }

        void start() {
            Thread t = new Thread(new Runnable() { public void run() {
                while (true) {
                    try {
                        final Socket s = server.accept();
                        connections.incrementAndGet();
                        Thread h = new Thread(new Runnable() { public void run() { serve(s); } },
                            "fake-usb-conn");
                        h.setDaemon(true);
                        h.start();
                    } catch (Exception ignored) { return; }
                }
            } }, "fake-usb-server");
            t.setDaemon(true);
            t.start();
        }

        private void serve(Socket s) {
            try {
                client = s;
                InputStream in = s.getInputStream();
                OutputStream out = s.getOutputStream();
                BufferedReader r = new BufferedReader(new InputStreamReader(in, StandardCharsets.UTF_8));
                String line;
                int tag = 0;
                while ((line = r.readLine()) != null) {
                    JSONObject m = new JSONObject(line);
                    String type = m.optString("t", "");
                    log.add(type);
                    if ("hello".equals(type)) {
                        send(out, new JSONObject().put("t", "hello_ok")
                            .put("world", new JSONObject().put("mode", "sim-usb-msc")));
                    } else if ("usb_scan".equals(type)) {
                        JSONArray devices = new JSONArray();
                        devices.put(new JSONObject().put("key", "sim:usb:4c4a:3442")
                            .put("vid", 0x4c4a).put("pid", 0x3442).put("name", "BR28 UBOOT (sim)"));
                        send(out, new JSONObject().put("t", "usb_scan_result").put("devices", devices));
                    } else if ("usb_open".equals(type)) {
                        send(out, new JSONObject().put("t", "usb_open_result").put("ok", true).put("detail", ""));
                    } else if ("usb_ctl".equals(type)) {
                        int len = m.getInt("len");
                        if (len == 8) {
                            byte[] ff = new byte[8];
                            java.util.Arrays.fill(ff, (byte) 0xff);
                            send(out, new JSONObject().put("t", "usb_ctl_result")
                                .put("data", Base64.getEncoder().encodeToString(ff)));
                        } else {
                            send(out, new JSONObject().put("t", "usb_ctl_result").put("data", ""));
                            s.close();                        // 重枚举：进入 ROM 后断开
                            dropped.countDown();
                            return;
                        }
                    } else if ("usb_arm".equals(type)) {                // M3 armed 协商（ok/false 两态）
                        armRequests.add(m);
                        send(out, new JSONObject().put("t", "usb_arm_result")
                            .put("ok", !armReject).put("detail", ""));
                    } else if ("usb_reboot".equals(type)) {             // M3 重启：回执后断连重枚举
                        send(out, new JSONObject().put("t", "usb_reboot_result")
                            .put("ok", true).put("detail", ""));
                        s.close();
                        dropped.countDown();
                        return;
                    } else if ("usb_bulk_out".equals(type)) {
                        byte[] data = Base64.getDecoder().decode(m.getString("data"));
                        if (data.length == 31 && data[0] == 'U' && data[1] == 'S' && data[2] == 'B' && data[3] == 'C') {
                            tag = (data[4] & 0xff) | ((data[5] & 0xff) << 8) | ((data[6] & 0xff) << 16) | ((data[7] & 0xff) << 24);
                            int size = (data[8] & 0xff) | ((data[9] & 0xff) << 8) | ((data[10] & 0xff) << 16) | ((data[11] & 0xff) << 24);
                            int op = (data[15] & 0xff) == 0x12 ? 0x12 : (((data[15] & 0xff) << 8) | (data[16] & 0xff));
                            if (size > 0) send(out, bulkIn(bulk(op, data, size)));
                            send(out, bulkIn(csw(tag)));       // CSW 独立一条（BOT 契约）
                        }                                       // OUT 数据相位：桩不回（CSW 已随 CBW 后回）
                    }
                }
            } catch (Exception ignored) { }
        }

        private static byte[] bulk(int op, byte[] cbw, int size) {
            if (op == 0x12) {                                  // INQUIRY：真形状 36B
                byte[] r = new byte[36];
                put(r, 8, "BR28    ");
                put(r, 16, "UBOOT1.00");
                put(r, 32, "1.00");
                return r;
            }
            if (op == UsbScsi.OP_FLASH_READ) {                 // FD05：512B 确定性数据
                int addr = ((cbw[17] & 0xff) << 24) | ((cbw[18] & 0xff) << 16) | ((cbw[19] & 0xff) << 8) | (cbw[20] & 0xff);
                byte[] r = new byte[size];
                for (int i = 0; i < size; i++) r[i] = (byte) ((addr + i * 7 + 5) & 0xff);
                return r;
            }
            byte[] r = new byte[size];                         // 其余（FC0A/FD07）：0x55 填充
            java.util.Arrays.fill(r, (byte) 0x55);
            if (size >= 2 && op != UsbScsi.OP_FLASH_READ && op != UsbScsi.OP_MEM_READ) {
                r[0] = (byte) (op >> 8); r[1] = (byte) op;     // 16B 响应回显 opcode
            }
            return r;
        }

        private static void put(byte[] r, int off, String s) {
            byte[] b = s.getBytes(StandardCharsets.US_ASCII);
            System.arraycopy(b, 0, r, off, Math.min(b.length, r.length - off));
        }

        private static byte[] csw(int tag) {
            return new byte[]{'U','S','B','S', (byte)tag, (byte)(tag>>8), (byte)(tag>>16), (byte)(tag>>24), 0,0,0,0, 0};
        }

        private static JSONObject bulkIn(byte[] data) {
            JSONObject o = new JSONObject();
            try {
                o.put("t", "usb_bulk_in");
                o.put("data", Base64.getEncoder().encodeToString(data));
            } catch (org.json.JSONException impossible) { throw new RuntimeException(impossible); }
            return o;
        }

        private static void send(OutputStream out, JSONObject o) {
            try {
                out.write((o.toString() + "\n").getBytes(StandardCharsets.UTF_8));
                out.flush();
            } catch (Exception ignored) { }
        }

        void dropClient() { Socket s = client; if (s != null) try { s.close(); } catch (Exception ignored) { } }
        void close() { dropClient(); try { server.close(); } catch (Exception ignored) { } }
    }

    static void await(CountDownLatch l, String what) throws Exception {
        expect(l.await(5, TimeUnit.SECONDS), what + " 超时");
    }

    public static void main(String[] args) throws Exception {
        scanAndPermission();
        openExchangeInquiryAndRead();
        scanReusesDataSession();
        recoveryAndEnterRomDetached();
        serverDropFailsVisibly();
        closeIdempotent();
        armFlashAndReboot();
        System.out.println("SimulationUsbLinkTest passed: 枚举（短连接/会话内复用）+ 授权即时授予 + "
            + "open/channel 的 BOT 精确长度往返（真 UsbScsi INQUIRY/FD05）+ EP0 控制往返与重枚举拔出 + "
            + "断连可见失败 + close 幂等 + M3 usb_arm/usb_reboot 往返（ok/false 两态、回执后断连拔出）。"
            + "未触 android.hardware.usb。");
    }

    /** M3：armFlash roundtrip（计划 JSON 透传+回执分发）与 rebootDevice（ok 回执后断连拔出）。 */
    static void armFlashAndReboot() throws Exception {
        FakeUsbServer server = new FakeUsbServer();
        server.start();
        try {
            SimulationUsbLink link = new SimulationUsbLink("127.0.0.1", server.port(), DIRECT);
            Recorder rec = new Recorder();
            link.setCallbacks(rec);
            expect(link.open("sim:usb:4c4a:3442"), "open");
            String sha = new String(new char[64]).replace('\0', 'a');   // 64 位 hex 占位
            String planJson = "{\"sectors\":[4096," + (0x200000 - 0x1000) + "],\"target_sha256\":\"" + sha
                + "\",\"nonce\":\"n-1\"}";
            expect(link.armFlash(planJson), "armFlash 应成功");
            expect(server.armRequests.size() == 1, "server 应收到一次 usb_arm");
            JSONObject sent = server.armRequests.get(0);
            expect(sent.optJSONArray("sectors") != null && sent.optJSONArray("sectors").length() == 2
                && sent.optJSONArray("sectors").toString().equals("[4096," + (0x200000 - 0x1000) + "]"),
                "计划 sectors 透传");
            expect(sha.equals(sent.optString("target_sha256")), "target_sha256 透传");
            expect("n-1".equals(sent.optString("nonce")), "nonce 透传");
            server.armReject = true;                                    // 拒绝路径：ok=false → false
            expect(!link.armFlash(planJson), "server 拒绝时 armFlash 应 false");
            server.armReject = false;
            expect(link.rebootDevice(), "reboot 应送达");
            await(server.dropped, "server 断开");
            await(rec.detached, "重枚举拔出回调");
            expect("sim:usb:4c4a:3442".equals(rec.detachedKey), "onDetached(key)");
            expect(!link.socketConnected(), "会话应清理");
            link.close();
        } finally { server.close(); }
    }

    /** 短连接枚举 + 授权即时授予（无设备会话时 scanJson 自建短连接问一次）。 */
    static void scanAndPermission() throws Exception {
        FakeUsbServer server = new FakeUsbServer();
        server.start();
        try {
            SimulationUsbLink link = new SimulationUsbLink("127.0.0.1", server.port(), DIRECT);
            Recorder rec = new Recorder();
            link.setCallbacks(rec);
            JSONArray devices = new JSONArray(link.scanJson());
            expect(devices.length() == 1, "应枚举到一台设备");
            expect(devices.getJSONObject(0).getInt("vid") == 0x4c4a, "rom 身份 vid");
            expect(link.hasPermission("sim:usb:4c4a:3442"), "仿真设备无授权面");
            link.requestPermission("sim:usb:4c4a:3442");
            await(rec.permission, "授权回调");
            expect(rec.granted && "sim:usb:4c4a:3442".equals(rec.permissionKey), "即时授予");
            link.close();
        } finally { server.close(); }
    }

    /** open → 真 UsbScsi 跑 INQUIRY/FD05：usb_bulk_out 到达 server、usb_bulk_in 精确长度回流。 */
    static void openExchangeInquiryAndRead() throws Exception {
        FakeUsbServer server = new FakeUsbServer();
        server.start();
        try {
            SimulationUsbLink link = new SimulationUsbLink("127.0.0.1", server.port(), DIRECT);
            link.setCallbacks(new Recorder());
            expect(link.open("sim:usb:4c4a:3442"), "open 应成功");
            expect(link.socketConnected(), "数据会话应建立");
            UsbScsi scsi = new UsbScsi(link.channel());
            byte[] inquiry = scsi.inquiry();
            expect(inquiry.length == 36, "INQUIRY 36B");
            expect(new String(inquiry, 8, 4, StandardCharsets.US_ASCII).equals("BR28"), "vendor BR28");
            expect(new String(inquiry, 16, 9, StandardCharsets.US_ASCII).equals("UBOOT1.00"), "product UBOOT1.00");
            byte[] sector = scsi.readFlashSector(0x1000);
            expect(sector.length == 512, "FD05 512B");
            for (int i = 0; i < 512; i++)
                expect(sector[i] == (byte) ((0x1000 + i * 7 + 5) & 0xff), "FD05 字节 " + i);
            link.close();
        } finally { server.close(); }
    }

    /** 会话内 scanJson 复用数据会话 socket（server 连接数不增）。 */
    static void scanReusesDataSession() throws Exception {
        FakeUsbServer server = new FakeUsbServer();
        server.start();
        try {
            SimulationUsbLink link = new SimulationUsbLink("127.0.0.1", server.port(), DIRECT);
            link.setCallbacks(new Recorder());
            expect(link.open("sim:usb:4c4a:3442"), "open");
            int conns = server.connections.get();
            JSONArray devices = new JSONArray(link.scanJson());
            expect(devices.length() == 1, "会话内 scan 应复用 socket 枚举");
            Thread.sleep(150);
            expect(server.connections.get() == conns, "不应新开连接（单活动连接纪律）");
            link.close();
        } finally { server.close(); }
    }

    /** EP0：能力读 8×ff → 进入请求 len0 → server 断开 → onDetached（重枚举拔出 UX）。 */
    static void recoveryAndEnterRomDetached() throws Exception {
        FakeUsbServer server = new FakeUsbServer();
        server.start();
        try {
            SimulationUsbLink link = new SimulationUsbLink("127.0.0.1", server.port(), DIRECT);
            Recorder rec = new Recorder();
            link.setCallbacks(rec);
            expect(link.open("sim:usb:4c4a:3442"), "open");
            byte[] cap = link.recoveryCapability();
            expect(cap != null && cap.length == 8, "能力读 8B");
            for (byte b : cap) expect(b == (byte) 0xff, "能力读 8×ff");
            expect(link.enterRomRequest(), "进入请求应送达");
            await(server.dropped, "server 断开");
            await(rec.detached, "重枚举拔出回调");
            expect("sim:usb:4c4a:3442".equals(rec.detachedKey), "onDetached(key)");
            expect(!link.socketConnected(), "会话应清理");
            link.close();
        } finally { server.close(); }
    }

    /** 服务端中途断连（备份中断链负例的 Link 半边）：bulkIn 空读（可见失败），不挂死。 */
    static void serverDropFailsVisibly() throws Exception {
        FakeUsbServer server = new FakeUsbServer();
        server.start();
        SimulationUsbLink link = new SimulationUsbLink("127.0.0.1", server.port(), DIRECT);
        Recorder rec = new Recorder();
        link.setCallbacks(rec);
        expect(link.open("sim:usb:4c4a:3442"), "open");
        UsbScsi scsi = new UsbScsi(link.channel());
        UsbScsi.Channel ch = link.channel();
        expect(scsi.inquiry().length == 36, "断前正常");
        server.dropClient();
        await(rec.detached, "断连回调");
        long t0 = System.currentTimeMillis();
        byte[] got = ch.bulkIn(512);
        expect(got.length == 0, "断连后 bulkIn 应空读（快速失败，不挂 5s）");
        expect(System.currentTimeMillis() - t0 < 3000, "断连后不应吃满超时");
        boolean threw = false;
        try { scsi.readFlashSector(0); } catch (IllegalStateException expected) { threw = true; }
        expect(threw, "断连后 exchange 应抛可见错误");
        link.close();
        server.close();
    }

    /** close 幂等；关闭后 channel 为空。 */
    static void closeIdempotent() throws Exception {
        FakeUsbServer server = new FakeUsbServer();
        server.start();
        try {
            SimulationUsbLink link = new SimulationUsbLink("127.0.0.1", server.port(), DIRECT);
            Recorder rec = new Recorder();
            link.setCallbacks(rec);
            expect(link.open("sim:usb:4c4a:3442"), "open");
            link.close();
            link.close();                                       // 幂等
            expect(!link.socketConnected(), "关闭后无会话");
            expect(link.channel() == null, "关闭后 channel 为空");
            Thread.sleep(150);
            expect(rec.detached.getCount() == 1, "手动 close 不触发 onDetached");
        } finally { server.close(); }
    }
}
