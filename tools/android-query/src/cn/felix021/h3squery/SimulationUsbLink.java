package cn.felix021.h3squery;

import java.io.BufferedReader;
import java.io.InputStream;
import java.io.InputStreamReader;
import java.io.OutputStream;
import java.net.InetSocketAddress;
import java.net.Socket;
import java.nio.charset.StandardCharsets;
import java.util.Base64;
import java.util.concurrent.ArrayBlockingQueue;
import java.util.concurrent.Executor;
import java.util.concurrent.TimeUnit;
import org.json.JSONArray;
import org.json.JSONObject;

/**
 * {@link H3sUsbBridge.Link} 的仿真设备实现（路 SIMB2A / M2 USB 只读通道，
 * docs/sim-bridge-design.md §4.4）：把 USB 物理 face（枚举/授权/claim/bulk/EP0 控制传输）
 * 搬到 adb reverse 的 localhost TCP——宿主机跑 tools/sim_bridge_server.py 的 MSC BOT 设备端
 * （2MiB raw fixture 后端）。不触 android.hardware.usb（AVD 无 OTG 也可用），纯 JVM 可测。
 *
 * socket 命令协议（JSON-lines，proto=1——与 SimulationGattLink/tools/sim_bridge_server.py 对齐）：
 *   app→server {"t":"hello","proto":1,"client":"sim-usb"}
 *   server→app {"t":"hello_ok","world":{"mode":"sim-usb-msc","identity":…}}（验收记录用）
 *   app→server {"t":"usb_scan"}                        枚举（server 进程级身份一台设备）
 *   server→app {"t":"usb_scan_result","devices":[…]}
 *   app→server {"t":"usb_open","key":…}                数据会话（key 过期→ok:false）
 *   server→app {"t":"usb_open_result","ok":bool}
 *   app→server {"t":"usb_bulk_out","data":b64}         CBW / OUT 数据相位（app 的 bulkOut）
 *   server→app {"t":"usb_bulk_in","data":b64}          device→host：数据相位与 13B CSW 各自
 *                                                     独立一条、长度精确（设计 §4.4 BOT 契约）
 *   app→server {"t":"usb_ctl","len":8|0}               EP0 厂商恢复请求（进入 ROM 流程）
 *   server→app {"t":"usb_ctl_result","data":b64}       len8→8×ff；len0→server 翻身份后断开
 *   app→server {"t":"usb_arm","sectors":[…],…}         M3 armed 协商（Link.armFlash）
 *   server→app {"t":"usb_arm_result","ok":bool,…}      校验失败 ok=false 不断链
 *   app→server {"t":"usb_reboot"}                      M3 整机重启换身份（Link.rebootDevice）
 *   server→app {"t":"usb_reboot_result","ok":bool,…}   ok=true 后断连重枚举（照 usb_ctl len0）
 *   （usb_disarm_result 同族回执：app 侧未发 disarm——PREPARED 可取消、armed 不可，规格 §4.3）
 *
 * 线程约定照 AndroidUsbLink：Link 动作（open/channel 传输/控制）由 H3sUsbBridge 跑在注入的
 * io executor（单线程）——阻塞 socket IO 可接受（AndroidUsbLink.open 同为阻塞）；回调
 * （onPermission/onDetached）经注入的 {@link Executor} 跳主线程。scanJson 由 JS 桥线程调用：
 * 数据会话已建立时**复用会话 socket**（writeLock 串行；server 按消息类型路由，usb_scan 与
 * bulk 互不污染），否则短连接问一次即断（server 单活动连接纪律下不占坑）。
 *
 * 会话代数（{@link #session}）防旧线程串扰：open/close 自增；server 主动断开（enterRom 重
 * 枚举/server 退出）且会话仍现行时→ onDetached（模拟拔出，走 app 的 usb_detached UX）；
 * 手动 close() 不触发。closeGatt 同款幂等。
 *
 * 诚实边界（设计 §4.6）：仿真设备为协议级 MSC——INQUIRY/装载/FD07/FD05 的字节形状照
 * tools/usb-recovery 钉死值应答，USB 物理层行为（重枚举抖动、-71、stall 时序）不仿真；
 * 仿真设备无授权面（hasPermission 恒 true、requestPermission 即时授予——真机要运行时弹窗）。
 */
public class SimulationUsbLink implements H3sUsbBridge.Link {
    /** socket 协议版本（与 tools/sim_bridge_server.py 的 PROTO 对齐；不匹配会被拒）。 */
    public static final int PROTO = 1;
    /** 默认端口（设计 §4.5 取偏门值避开常见占用；CLI 可换）。 */
    public static final int DEFAULT_PORT = 7788;
    /** socket 拨号超时（对齐 AndroidUsbLink 的快速失败语义）。 */
    static final int CONNECT_TIMEOUT_MS = 5000;
    /** bulk 传输超时（对齐 AndroidUsbLink.TIMEOUT_MS=5000）。 */
    static final int TRANSFER_TIMEOUT_MS = 5000;
    /** scan/open/ctl 请求-结果超时。 */
    static final int REPLY_TIMEOUT_MS = 5000;
    /** 接收队列上限（bulk 锁步：app 一次 exchange 至多 2 条在途；防失控堆积）。 */
    private static final int RX_LIMIT = 64;
    private static final int LINE_LIMIT = 64 * 1024;

    private final String host;
    private final int port;
    private final Executor poster;                    // 回调跳主线程（Android=runOnUiThread；测试=直跑）
    private H3sUsbBridge.Callbacks cb;

    /** 数据会话代数：open/close 自增；旧会话线程据此自检失效（SimulationGattLink 同款）。 */
    private volatile int session;
    private volatile Socket socket;                   // 数据会话 socket（open 成功后非空）
    private volatile OutputStream out;                // 会话写口（writeLock 内独占）
    private final Object writeLock = new Object();
    private final ArrayBlockingQueue<byte[]> rx = new ArrayBlockingQueue<byte[]>(RX_LIMIT);
    private byte[] leftover;                          // 上一条 usb_bulk_in 未消费完的字节
    private int leftoverOff;
    /** 单槽请求-结果（USB 通道锁步：同一时刻至多一个在途 scan/open/ctl）。 */
    private volatile ArrayBlockingQueue<Object> replySlot;
    private volatile String openedKey;

    public SimulationUsbLink(String host, int port, Executor poster) {
        this.host = host;
        this.port = port;
        this.poster = poster;
    }

    @Override public void setCallbacks(H3sUsbBridge.Callbacks callbacks) { this.cb = callbacks; }

    // ================================ 枚举 / 授权 ================================

    /** server usb_scan：会话 socket 在则复用（server 按消息类型路由），否则短连接问一次。
     *  失败返回 "[]"（=无设备；server 不在/占线同形——不臆造错误 JSON）。 */
    @Override public String scanJson() {
        if (socket != null && out != null) {
            JSONObject r = roundtrip(message("usb_scan"));
            if (r != null && r.has("devices")) return r.optJSONArray("devices").toString();
        }
        Socket s = null;
        try {
            s = new Socket();
            s.connect(new InetSocketAddress(host, port), CONNECT_TIMEOUT_MS);
            s.setSoTimeout(REPLY_TIMEOUT_MS);
            OutputStream o = s.getOutputStream();
            writeLine(o, helloMessage().toString());
            writeLine(o, message("usb_scan").toString());
            BufferedReader reader = new BufferedReader(
                new InputStreamReader(s.getInputStream(), StandardCharsets.UTF_8));
            JSONObject scan = null;
            String line;
            while ((line = reader.readLine()) != null) {       // hello_ok/busy 等杂行跳过
                JSONObject m;
                try { m = new JSONObject(line); } catch (Exception e) { continue; }
                if ("usb_scan_result".equals(m.optString("t", ""))) { scan = m; break; }
            }
            if (scan == null) return "[]";
            JSONArray devices = scan.optJSONArray("devices");
            return devices != null ? devices.toString() : "[]";
        } catch (Exception e) {
            return "[]";
        } finally {
            closeQuietly(s);
        }
    }

    /** 仿真设备无授权面：恒已授权（真机对照=UsbManager 弹窗；AVD 上无意义，设计 §4.3 同判）。 */
    @Override public boolean hasPermission(String key) { return true; }

    @Override public void requestPermission(final String key) {
        final H3sUsbBridge.Callbacks c = cb;
        if (c != null) poster.execute(new Runnable() { public void run() { c.onPermission(key, true); } });
    }

    // ================================ 数据会话 ================================

    /** 打开数据会话：connect → hello(sim-usb) → usb_open(key) → 起 读线程。key 过期 → false。 */
    @Override public boolean open(String key) {
        close();                                               // 幂等起点（旧会话自检失效）
        final int mySession = ++session;
        Socket s = new Socket();
        try {
            s.connect(new InetSocketAddress(host, port), CONNECT_TIMEOUT_MS);
        } catch (Exception e) {
            return false;
        }
        try {
            OutputStream o = s.getOutputStream();
            if (session != mySession) { closeQuietly(s); return false; }   // 拨号窗口内被 close
            socket = s;
            out = o;
            Thread reader = new Thread(new Runnable() { public void run() { readLoop(s, mySession); } },
                "sim-usb-read");
            reader.setDaemon(true);
            reader.start();
            if (roundtrip(helloMessage()) == null) { close(); return false; }   // hello_ok 等不到=失败
            JSONObject openReply = roundtrip(message("usb_open").put("key", key == null ? "" : key));
            if (openReply == null || !openReply.optBoolean("ok", false)) { close(); return false; }
            openedKey = key;
            return true;
        } catch (Exception e) {
            close();
            return false;
        }
    }

    @Override public void close() {
        session++;                                             // EOF 自检失效：不触发 onDetached
        Socket s = socket;
        socket = null;
        out = null;
        openedKey = null;
        rx.clear();
        leftover = null;
        closeQuietly(s);                                       // 连带关 socket 双向（读线程收 EOF）
    }

    /** 数据会话是否建立（测试探针）。 */
    public boolean socketConnected() { return socket != null; }

    /** bulk 通道（UsbScsi.Channel）：OUT=发 usb_bulk_out；IN=从接收队列取（超时 5s）。 */
    @Override public UsbScsi.Channel channel() {
        if (socket == null) return null;
        return new UsbScsi.Channel() {
            @Override public int bulkOut(byte[] data) { return SimulationUsbLink.this.bulkOut(data); }
            @Override public byte[] bulkIn(int max) { return SimulationUsbLink.this.bulkIn(max); }
        };
    }

    // ================================ EP0 厂商恢复请求 ================================

    /** 能力读（wLength=8 → 8×ff）；失败/非 8B → null（H3sUsbBridge.enterRom 的门同形）。 */
    @Override public byte[] recoveryCapability() {
        JSONObject r = roundtrip(ctlMessage(8));
        if (r == null) return null;
        try {
            byte[] data = Base64.getDecoder().decode(r.getString("data"));
            return data.length == 8 ? data : null;
        } catch (Exception e) {
            return null;
        }
    }

    /** 一次性进入请求（wLength=0）：server 翻身份 normal→rom 后断开（重枚举）。
     *  返回=请求已送达（对照 AndroidUsbLink：-71 也可能已进入，以重扫身份为准，不重发）。 */
    @Override public boolean enterRomRequest() {
        return roundtrip(ctlMessage(0)) != null;
    }

    private static JSONObject ctlMessage(int length) {
        JSONObject o = message("usb_ctl");
        try { o.put("len", length); } catch (org.json.JSONException impossible) { throw new RuntimeException(impossible); }
        return o;
    }

    // ================================ M3 armed 刷机控制面 ================================

    /** usb_arm：把计划（sectors/target_sha256/nonce，H3sUsbBridge.planJson 产物）送 server
     *  armed 协商。校验失败 server 回 ok=false（不断链）——返回 false=可见失败。 */
    @Override public boolean armFlash(String planJson) {
        if (socket == null || out == null || planJson == null) return false;
        JSONObject m;
        try { m = new JSONObject(planJson); } catch (Exception e) { return false; }
        try { m.put("t", "usb_arm"); } catch (org.json.JSONException impossible) { throw new RuntimeException(impossible); }
        JSONObject r = roundtrip(m);
        return r != null && r.optBoolean("ok", false);
    }

    /** usb_reboot：整机重启换身份。ok=true 后 server 断连（EOF=onDetached 模拟拔出）——
     *  与 enterRomRequest 返回语义对称：返回=结果已送达，以重扫身份为准。 */
    @Override public boolean rebootDevice() {
        if (socket == null || out == null) return false;
        JSONObject r = roundtrip(message("usb_reboot"));
        return r != null && r.optBoolean("ok", false);
    }

    // ================================ 内部 ================================

    private int bulkOut(byte[] data) {
        OutputStream o = out;
        if (o == null || socket == null) return 0;
        JSONObject m = new JSONObject();
        try { m.put("t", "usb_bulk_out").put("data", Base64.getEncoder().encodeToString(data)); }
        catch (org.json.JSONException impossible) { throw new RuntimeException(impossible); }
        synchronized (writeLock) {
            try {
                o.write((m.toString() + "\n").getBytes(StandardCharsets.UTF_8));
                o.flush();
                return data.length;
            } catch (Exception e) {
                linkLost();
                return 0;
            }
        }
    }

    /** 阻塞取至多 max 字节（切片消费 leftover；超时/链路断 → 空数组=可见失败，不抛）。 */
    private byte[] bulkIn(int max) {
        long deadline = System.currentTimeMillis() + TRANSFER_TIMEOUT_MS;
        while (true) {
            byte[] buf = leftover;
            if (buf == null || leftoverOff >= buf.length) {
                int wait = (int) Math.min(200, deadline - System.currentTimeMillis());
                if (wait <= 0) return new byte[0];
                try {
                    buf = rx.poll(wait, TimeUnit.MILLISECONDS);
                } catch (InterruptedException e) {
                    return new byte[0];
                }
                if (buf == null) {
                    if (socket == null) return new byte[0];    // 链路已断：提前失败
                    continue;
                }
                leftover = buf;
                leftoverOff = 0;
            }
            int n = Math.min(max, buf.length - leftoverOff);
            byte[] result = java.util.Arrays.copyOfRange(buf, leftoverOff, leftoverOff + n);
            leftoverOff += n;
            return result;
        }
    }

    /** 读线程：JSON 行分发——usb_bulk_in 入接收队列，其余结果入单槽；EOF=链路丢失。 */
    private void readLoop(Socket s, final int mySession) {
        try {
            BufferedReader reader = new BufferedReader(
                new InputStreamReader(s.getInputStream(), StandardCharsets.UTF_8));
            while (true) {
                String line = reader.readLine();
                if (line == null) break;                       // EOF=server 关闭（重枚举/退出）
                if (line.length() > LINE_LIMIT) break;
                handleLine(line);
            }
        } catch (Exception ignored) { /* 会话结束或 socket 断 */ }
        linkLost(mySession);
    }

    private void handleLine(String text) {
        JSONObject m;
        try { m = new JSONObject(text); } catch (Exception e) { return; }   // 杂散行丢弃
        String t = m.optString("t", "");
        if ("usb_bulk_in".equals(t)) {
            byte[] data;
            try { data = Base64.getDecoder().decode(m.getString("data")); }
            catch (Exception e) { return; }
            rx.offer(data);                                    // 锁步下不会满；满=上层失控，丢弃
        } else if ("usb_scan_result".equals(t) || "usb_open_result".equals(t)
                || "usb_ctl_result".equals(t) || "hello_ok".equals(t)
                || "usb_arm_result".equals(t) || "usb_disarm_result".equals(t)   // M3 控制面回执
                || "usb_reboot_result".equals(t)) {
            offerReply(m);
        } else if ("busy".equals(t) || "hello_err".equals(t) || "error".equals(t)) {
            // 请求级失败（占线/握手拒/设备端拒绝）——对等待方可见失败；
            // 连接级错误（server 已断开）走 EOF 路径。
            offerReply(FAILED);
        }
    }

    private JSONObject roundtrip(JSONObject request) {
        ArrayBlockingQueue<Object> slot = new ArrayBlockingQueue<Object>(1);
        replySlot = slot;
        OutputStream o = out;
        if (o == null || socket == null) return null;
        try {
            synchronized (writeLock) {
                o.write((request.toString() + "\n").getBytes(StandardCharsets.UTF_8));
                o.flush();
            }
        } catch (Exception e) {
            linkLost();
            return null;
        }
        Object reply;
        try {
            reply = slot.poll(REPLY_TIMEOUT_MS, TimeUnit.MILLISECONDS);
        } catch (InterruptedException e) {
            return null;
        }
        return reply instanceof JSONObject ? (JSONObject) reply : null;
    }

    private void offerReply(Object value) {
        ArrayBlockingQueue<Object> slot = replySlot;
        if (slot != null) slot.offer(value);
    }

    private static final Object FAILED = new Object();

    /** 链路丢失（现行会话）：可见拔出 onDetached(key) + 解除等待方阻塞。 */
    private void linkLost() {
        linkLost(session);
    }

    private void linkLost(int mySession) {
        if (session != mySession) return;                      // 手动 close/新会话：自检失效
        socket = null;
        out = null;
        offerReply(FAILED);
        final H3sUsbBridge.Callbacks c = cb;
        final String key = openedKey;
        openedKey = null;
        if (c != null && key != null)
            poster.execute(new Runnable() { public void run() { c.onDetached(key); } });
    }

    private static JSONObject helloMessage() {
        JSONObject o = new JSONObject();
        try { o.put("t", "hello").put("proto", PROTO).put("client", "sim-usb"); }
        catch (org.json.JSONException impossible) { throw new RuntimeException(impossible); }
        return o;
    }

    private static JSONObject message(String type) {
        JSONObject o = new JSONObject();
        try { o.put("t", type); } catch (org.json.JSONException impossible) { throw new RuntimeException(impossible); }
        return o;
    }

    private static void writeLine(OutputStream o, String json) throws java.io.IOException {
        o.write((json + "\n").getBytes(StandardCharsets.UTF_8));
        o.flush();
    }

    private static void closeQuietly(Socket s) { try { if (s != null) s.close(); } catch (Exception ignored) { } }
}
