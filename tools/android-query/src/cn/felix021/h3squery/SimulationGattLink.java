package cn.felix021.h3squery;

import java.io.InputStream;
import java.io.InputStreamReader;
import java.io.OutputStream;
import java.net.Socket;
import java.nio.charset.StandardCharsets;
import java.util.Base64;
import java.util.concurrent.ArrayBlockingQueue;
import java.util.concurrent.TimeUnit;
import org.json.JSONObject;

/**
 * {@link AndroidBleTransport.Link} 的仿真设备实现（路 SIMB1A / M1-a 通道骨架，
 * docs/sim-bridge-design.md §4.3）：把 GATT 数据面搬到 adb reverse 的 localhost TCP——
 * 宿主机跑 tools/sim_bridge_server.py（M1-a 为静态 fixture 状态源；直调半边对接后同协议
 * 换成停点世界驱动）。不触 android.bluetooth（AVD 无 BT 也可用），因此本文件是纯 JVM 代码，
 * 与 AndroidGattLink 不同处在于：连接期相位事件（扫描命中/服务发现/CCCD/MTU）本地合成——
 * transport 五段式状态机的覆盖由 TransportContractTest 假 Link 保证，server 只做数据面
 * （连接期故障注入留扩展：server 发 link_event 类消息即可，本版未实现）。
 *
 * 线程约定照 AndroidGattLink：socket 读线程收消息统一经注入的 {@link Scheduler} 跳到
 * 主线程（Android=AndroidGattLink.mainScheduler()；JVM 测试=直跑调度器）再调
 * Link.Callbacks；Link 动作方法由 transport 在主线程调用。写路径按设计 §4.3 纪律：
 * writeChunk 只入有界发送队列，由后台 IO 线程写 socket——主线程绝不直写（阻塞=ANR）；
 * 片写回执走 server 的 chunk_ack（收到即 ACK，不隐式延迟）。
 *
 * socket 命令协议（JSON-lines，UTF-8、\n 结尾，二进制 base64——设计 §4.2 契约）：
 *   app→server {"t":"hello","proto":1,"client":"sim-gatt"}
 *   server→app {"t":"hello_ok","world":{...}}（M1-a: {"mode":"static-fixture",...}）
 *   app→server {"t":"ble_write","data":b64}      一个 GATT 片
 *   server→app {"t":"chunk_ack"}                 片写回执 → onCharacteristicWrite(0)
 *   server→app {"t":"ble_notify","data":b64}     设备→app 帧 → onCharacteristicChanged
 *   server→app {"t":"error","code":…,"detail":…} 链路错误 → 可见断线（不静默）
 *
 * 会话模型：connectGatt 开新会话（代数 {@link #session} +1），closeGatt 结束当前会话；
 * 旧会话的 IO/读线程经代数自检 no-op（重试/重连路径上 transport 会先 closeGatt 再
 * connectGatt，AndroidBleTransport retryOrFail 先例）。closeGatt 幂等。
 *
 * 诚实边界：静态 fixture 模式下回值不是固件执行结果（synthetic-not-firmware）；
 * AVD 上「已连接」的语义 = socket 会话建立 + 五段式本地相位全部走通。
 */
public class SimulationGattLink implements AndroidBleTransport.Link {
    /** socket 协议版本（与 tools/sim_bridge_server.py 的 PROTO 对齐；不匹配会被拒）。 */
    public static final int PROTO = 1;
    /** 默认端口（设计 §4.5 取偏门值避开常见占用；CLI 可换）。 */
    public static final int DEFAULT_PORT = 7788;
    /** 本地合成扫描命中延迟（走真实扫描相位，不留 12s 发现超时窗口）。 */
    static final long SCAN_HIT_DELAY_MS = 50;
    /** 合成候选的广播名/地址/RSSI（名字与默认发现策略匹配；地址是占位，不触真蓝牙）。 */
    public static final String SIMULATED_NAME = BleDiscoveryPolicy.DEFAULT_NAME;
    public static final String SIMULATED_ADDRESS = "02:00:00:00:00:02";
    static final int SIMULATED_RSSI = -50;
    /** socket 拨号超时（对齐 GATT 快速失败语义，不吃 transport 的 30s 看门狗）。 */
    static final int CONNECT_TIMEOUT_MS = 5000;
    /** MTU 回报值（真机实测回调 247，设计 §4.3；transport 请求 517，以回调为准）。 */
    static final int SIMULATED_MTU = 247;
    /** 发送队列上限（设计 §4.2 执行契约①：超限=同步拒绝，不无限堆积）。 */
    private static final int TX_QUEUE_LIMIT = 64;
    /** 单行 JSON 上限（防资源失控；命令片最大 ~512B 的 base64 编码）。 */
    private static final int LINE_LIMIT = 64 * 1024;

    private final String host;
    private final int port;
    private final AndroidBleTransport.Scheduler poster;   // 回调统一跳主线程
    private Callbacks cb;

    /** 会话代数：connectGatt/closeGatt 都自增，旧会话线程据此自检失效。 */
    private volatile int session;

    private volatile boolean connected;                    // 当前会话 socket 已建立
    private volatile OutputStream out;                     // 当前会话写口（IO 线程独占）
    private final ArrayBlockingQueue<byte[]> txQueue = new ArrayBlockingQueue<byte[]>(TX_QUEUE_LIMIT);
    private boolean scanning;

    /** poster=回调调度面（Android 用 AndroidGattLink.mainScheduler()；测试注入直跑调度器）。 */
    public SimulationGattLink(String host, int port, AndroidBleTransport.Scheduler poster) {
        this.host = host;
        this.port = port;
        this.poster = poster;
    }

    @Override public void setCallbacks(Callbacks callbacks) { this.cb = callbacks; }

    // ================================ 连接期（本地合成相位） ================================

    @Override public String notReadyReason() { return null; }   // 仿真链路无 BT 前置条件

    @Override public void startScan() {
        scanning = true;
        postDelayed(SCAN_HIT_DELAY_MS, new Runnable() { public void run() {
            if (!scanning) return;                          // stopScan 后晚到任务 no-op
            post(new Runnable() { public void run() { if (cb != null) cb.onCandidate(SIMULATED_NAME, SIMULATED_ADDRESS, SIMULATED_RSSI); } });
        }});
    }

    @Override public void stopScan() { scanning = false; }

    /** 异步拨号：成功→hello→CONNECTED(0)；失败→断开(133)，transport 按尝试失败重试。地址参数忽略（socket 会话）。 */
    @Override public void connectGatt(String address) {
        final int mySession = ++session;
        connected = false;
        Thread io = new Thread(new Runnable() { public void run() { connectAndPump(mySession); } }, "sim-link-io");
        io.setDaemon(true);
        io.start();
    }

    /** IO 线程主体：拨号 → 发 hello → 起读线程 → CONNECTED → 阻塞写循环（队列 → socket）。 */
    private void connectAndPump(final int mySession) {
        Socket s;
        try {
            s = new Socket();
            s.connect(new java.net.InetSocketAddress(host, port), CONNECT_TIMEOUT_MS);
        } catch (Exception e) {
            if (session == mySession)
                post(new Runnable() { public void run() { if (cb != null) cb.onConnectionStateChange(133, AndroidBleTransport.STATE_DISCONNECTED); } });
            return;
        }
        final OutputStream myOut;
        try { myOut = s.getOutputStream(); }
        catch (Exception e) { closeQuietly(s); linkLost(mySession); return; }
        if (session != mySession) { closeQuietly(s); return; }   // 拨号窗口内被 closeGatt
        out = myOut;
        connected = true;
        Thread reader = new Thread(new Runnable() { public void run() { readLoop(s, mySession); } }, "sim-link-read");
        reader.setDaemon(true);
        reader.start();
        sendLine(myOut, helloMessage(), mySession);
        post(new Runnable() { public void run() { if (cb != null) cb.onConnectionStateChange(0, AndroidBleTransport.STATE_CONNECTED); } });
        try {
            while (session == mySession) {
                byte[] chunk = txQueue.poll(200, TimeUnit.MILLISECONDS);
                if (chunk == null) continue;
                JSONObject msg = new JSONObject();
                try { msg.put("t", "ble_write").put("data", Base64.getEncoder().encodeToString(chunk)); }
                catch (org.json.JSONException impossible) { throw new RuntimeException(impossible); }
                sendLine(myOut, msg.toString(), mySession);
            }
        } catch (InterruptedException expected) {
            // 会话结束（closeGatt/新会话）中断退出
        }
    }

    /** 读线程：JSON 行 → chunk_ack / ble_notify / error 分发（回调经 poster 跳主线程）。 */
    private void readLoop(Socket s, final int mySession) {
        try {
            InputStream in = s.getInputStream();
            InputStreamReader isr = new InputStreamReader(in, StandardCharsets.UTF_8);
            StringBuilder line = new StringBuilder();
            char[] buf = new char[4096];
            while (true) {
                int n = isr.read(buf);
                if (n < 0) break;                           // EOF=server 关闭=链路丢失
                for (int i = 0; i < n; i++) {
                    char c = buf[i];
                    if (c == '\n') { handleLine(line.toString()); line.setLength(0); }
                    else if (line.length() < LINE_LIMIT) line.append(c);
                    else break;                             // 超限行=资源失控，断开
                }
            }
        } catch (Exception ignored) { /* 会话结束或 socket 断 */ }
        linkLost(mySession);                                // 主动关闭时 session 已变，自检 no-op
    }

    private void handleLine(String text) {
        final JSONObject msg;
        try { msg = new JSONObject(text); } catch (Exception e) { return; }   // 杂散行丢弃
        String t = msg.optString("t", "");
        if ("chunk_ack".equals(t)) {
            post(new Runnable() { public void run() { if (cb != null) cb.onCharacteristicWrite(0); } });
        } else if ("ble_notify".equals(t)) {
            final byte[] data;
            try { data = Base64.getDecoder().decode(msg.getString("data")); }
            catch (Exception e) { return; }
            post(new Runnable() { public void run() { if (cb != null) cb.onCharacteristicChanged(data); } });
        }
        // hello_ok（世界横幅）本版不消费（M1 直调半边对接后作验收记录）；
        // error（blocked/budget/world_dead）按设计 §4.3 映射为可见断线（不静默）——
        // server 报错误后世界状态不可信，直接按链路丢失处理。
        else if ("error".equals(t)) {
            connected = false;
            post(new Runnable() { public void run() { if (cb != null) cb.onConnectionStateChange(129, AndroidBleTransport.STATE_DISCONNECTED); } });
        }
    }

    @Override public boolean discoverServices() {
        post(new Runnable() { public void run() { if (cb != null) cb.onServicesDiscovered(0); } });
        return true;
    }

    @Override public boolean characteristicsPresent() { return true; }   // A002/1/2 合成在场

    @Override public int writeProperties() { return AndroidBleTransport.PROPERTY_WRITE_NO_RESPONSE; } // H3S 实测属性 4

    @Override public boolean enableNotifications() {
        post(new Runnable() { public void run() { if (cb != null) cb.onDescriptorWrite(0); } });
        return true;
    }

    @Override public boolean requestMtu(int mtu) {
        post(new Runnable() { public void run() { if (cb != null) cb.onMtuChanged(SIMULATED_MTU, 0); } });
        return true;
    }

    // ================================ 数据面与关闭 ================================

    /** 入有界发送队列（主线程不直写 socket）；socket 未连/队列满 → false（按片失败，回调必不来）。 */
    @Override public boolean writeChunk(byte[] chunk, int writeType) {
        if (!connected) return false;
        return txQueue.offer(chunk.clone());
    }

    /** 断开并释放（幂等；触发的断开回调在 transport IDLE 相位被忽略）。 */
    @Override public void closeGatt() {
        session++;                                          // 当前会话全部线程自检失效
        connected = false;
        txQueue.clear();
        OutputStream o = out;
        out = null;
        if (o != null) try { o.close(); } catch (Exception ignored) { }   // 连带关 socket 双向
    }

    /** 当前 socket 会话是否建立（测试探针）。 */
    public boolean socketConnected() { return connected; }

    // ================================ 内部 ================================

    private static String helloMessage() {
        JSONObject o = new JSONObject();
        try { o.put("t", "hello").put("proto", PROTO).put("client", "sim-gatt"); }
        catch (org.json.JSONException impossible) { throw new RuntimeException(impossible); }
        return o.toString();
    }

    /** 只在 IO 线程调用；会话失效后的写失败不报断线（旧会话线程自检）。 */
    private void sendLine(OutputStream myOut, String json, int mySession) {
        try {
            myOut.write((json + "\n").getBytes(StandardCharsets.UTF_8));
            myOut.flush();
        } catch (Exception e) {
            linkLost(mySession);
        }
    }

    /** 链路丢失：可见断线（transport READY 后=意外掉线不自动重连；初始化中=按尝试失败重试）。 */
    private void linkLost(int mySession) {
        if (session != mySession) return;
        connected = false;
        post(new Runnable() { public void run() { if (cb != null) cb.onConnectionStateChange(129, AndroidBleTransport.STATE_DISCONNECTED); } });
    }

    private void post(Runnable r) { poster.postDelayed(0, r); }

    private void postDelayed(long delayMs, Runnable r) { poster.postDelayed(delayMs, r); }

    private static void closeQuietly(Socket s) { try { s.close(); } catch (Exception ignored) { } }
}
