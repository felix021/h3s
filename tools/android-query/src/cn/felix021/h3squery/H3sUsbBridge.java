package cn.felix021.h3squery;

import android.webkit.JavascriptInterface;
import java.io.ByteArrayOutputStream;
import java.security.MessageDigest;
import java.util.Locale;
import java.util.concurrent.Executor;
import org.json.JSONArray;
import org.json.JSONException;
import org.json.JSONObject;

/**
 * WebView JS USB bridge（R3 USB tab，注入名 "h3susb"）：BR28 ROM 模式的只读操作面。
 *
 * 分层与 H3SBridge 同构：
 *   - 未注入 Link（无参形态）：纯协议演示——命令编码返回 hex，不做任何设备 IO；
 *   - 注入 Link（Android=AndroidUsbLink）：枚举 → 授权 → claim → INQUIRY →（可选）
 *     装载 RAM loader → FD07 内存窗读 / FD05 Flash 读。
 *
 * 只读边界（照 tools/usb-recovery 恢复读取器口径）：
 *   - 命令编码层（UsbScsi）根本不存在擦除/编程/复位命令——刷机路径在代码层就不存在，
 *     不是 UI 层挡一挡（红线：候选镜像 UNTESTED 不刷机）；
 *   - FD07/FD05 只在 loader 就绪（FB06×46+FB08+FC0A 全过）之后放行（read_rom.py 相位门）；
 *   - 读 ROM 窗（128KiB）与全片备份（2MiB）都是双读一致才返回数据（backup()/read_rom.py 口径）。
 *
 * 进入 ROM 模式（enterRom）：对已打开的普通 QCY USB（3654:4a55）先读恢复能力
 * （vendor GET_STATUS wLength=8，须返回 8×0xff），确认后只发一次 wLength=0 的
 * 进入请求（docs/usb-recovery.md：重启可能让请求本身报协议错误，以重新扫描到的
 * ROM 身份为准，不重发）。
 *
 * 物理链路（手机 OTG 连耳机进 ROM 模式）**未验证**——info() 恒报 verified=false，
 * UI 需把「未验证」如实展示；本桥不臆造设备响应形状。
 *
 * 线程：设备 IO 跑在注入的 Executor（Android=单线程后台，避免阻塞主线程/JS 桥线程）；
 * 事件（授权结果/插拔/进度）经 ResultSink 用 evaluateJavascript 推 window.onH3sUsbEvent。
 *
 * JVM 单测（H3sUsbBridgeTest/UsbScsiTest）用桩 Link/Channel；Android 构建走
 * android.jar + 内置 org.json。
 */
public class H3sUsbBridge {
    /** 固定 BR28 RAM loader（tools/usb-recovery/h3s_usb.py 钉死；构建时按摘要打包）。 */
    public static final String LOADER_SHA256 = "ab1b77eb73385aaa78ab769253b2af7ef63d0ade46f391bd7424cc598a40267c";
    public static final int LOADER_SIZE = 23552;
    /** ROM 窗大小（CPU ROM 0x0..0x20000，read_rom.py 固定窗口）。 */
    public static final int ROM_WINDOW = 0x20000;
    /** Flash 全片大小（2MiB，usb-recovery 固定口径）。 */
    public static final int FLASH_SIZE = 0x200000;
    /** 普通 QCY USB 身份 / BR28 ROM 身份（usbfs.py）。 */
    public static final int VID_QCY = 0x3654, PID_QCY = 0x4a55, VID_ROM = 0x4c4a, PID_ROM = 0x3442;

    /** USB 层抽象（Android=UsbManager 粘合；JVM 测试=桩）。bulk 通道即 UsbScsi.Channel。 */
    public interface Link {
        /** 枚举 USB 设备：[{key,vid,pid,name,rom}]（rom=true 为 4c4a:3442 身份）。 */
        String scanJson();
        /** 该设备是否已授权。 */
        boolean hasPermission(String key);
        /** 请求授权（异步，结果经 Callbacks.onPermission）。 */
        void requestPermission(String key);
        /** 打开并 claim（bulk/control 通道就绪）；失败返回 false。 */
        boolean open(String key);
        void close();
        /** 已打开设备的 bulk 通道（open 成功后非空）。 */
        UsbScsi.Channel channel();
        /** 厂商恢复能力读（GET_STATUS 83/00/fc/fe len8）；返回 8 字节或 null。 */
        byte[] recoveryCapability();
        /** 发一次进入 ROM 请求（len=0）；返回是否已发出（结果以重枚举身份为准）。 */
        boolean enterRomRequest();
        void setCallbacks(Callbacks cb);
    }

    public interface Callbacks {
        void onPermission(String key, boolean granted);
        void onDetached(String key);
    }

    /** JS 事件出口（与 H3SBridge.ResultSink 同用法：壳 evaluateJavascript 推页面全局处理器）。 */
    public interface ResultSink { void emit(String json); }

    /** 备份落盘（壳实现：app 私有目录 usb/<name>；测试=桩）。 */
    public interface FileSaver { String save(String name, byte[] data); }

    private final Link link;            // 可空：null=纯协议演示形态
    private final ResultSink sink;      // 可空：null 时事件丢弃
    private final Executor io;          // 可空：null=调用线程直接执行（JVM 测试）
    private final FileSaver saver;      // 可空：null=读盘类操作不可用
    private final byte[] loader;        // 可空：null=未打包 loader
    private final String expectedLoaderSha;  // 生产=LOADER_SHA256；测试可注入桩摘要
    private UsbScsi scsi;               // open 成功后非空
    private String openedKey;
    private boolean loaderReady;        // FB06×46+FB08+FC0A 全过
    private String openedIsRom;

    public H3sUsbBridge() { this(null, null, null, null, null); }

    public H3sUsbBridge(Link link, ResultSink sink, Executor io, FileSaver saver, byte[] loader) {
        this(link, sink, io, saver, loader, LOADER_SHA256);
    }

    /** 测试形态：允许注入桩 loader 摘要（生产构造固定 LOADER_SHA256）。 */
    public H3sUsbBridge(Link link, ResultSink sink, Executor io, FileSaver saver, byte[] loader, String expectedLoaderSha) {
        this.link = link;
        this.sink = sink;
        this.io = io;
        this.saver = saver;
        this.loader = loader;
        this.expectedLoaderSha = expectedLoaderSha;
        if (link != null) link.setCallbacks(new Callbacks() {
            @Override public void onPermission(final String key, final boolean granted) {
                emitEvent(put(put(eventJson("usb_permission"), "key", key), "granted", granted));
            }
            @Override public void onDetached(final String key) { notifyDetached(key); }
        });
    }

    /** USB 设备拔出通知（壳的 ACTION_USB_DEVICE_DETACHED 广播转发）：作废打开态并推事件。 */
    public void notifyDetached(String key) {
        if (key != null && key.equals(openedKey)) { openedKey = null; scsi = null; loaderReady = false; openedIsRom = null; }
        emitEvent(put(eventJson("usb_detached"), "key", key == null ? "" : key));
    }

    private void run(Runnable r) {
        if (io != null) io.execute(r); else r.run();
    }

    private JSONObject eventJson(String event) { return put(new JSONObject(), "event", event); }

    private void emitEvent(JSONObject event) {
        if (sink != null) sink.emit(event.toString());
    }

    private static JSONObject put(JSONObject o, String key, int value) {
        try { return o.put(key, value); } catch (JSONException impossible) { throw new RuntimeException(impossible); }
    }

    private static JSONObject put(JSONObject o, String key, boolean value) {
        try { return o.put(key, value); } catch (JSONException impossible) { throw new RuntimeException(impossible); }
    }

    private static JSONObject put(JSONObject o, String key, String value) {
        try { return o.put(key, value); } catch (JSONException impossible) { throw new RuntimeException(impossible); }
    }

    private static String ok() { return put(new JSONObject(), "ok", true).toString(); }

    private static String err(String message) {
        try { return new JSONObject().put("ok", false).put("error", message).toString(); }
        catch (JSONException impossible) { return "{\"ok\":false}"; }
    }

    /** 设备 IO 前置检查：须注入 Link 且已打开。 */
    private String requireOpen() {
        if (link == null) return err("未注入 USB 层（纯协议演示形态无设备 IO）");
        if (scsi == null) return err("未打开设备（先 scan → 授权 → open）");
        return null;
    }

    // —— 查询面（同步，无 IO 或轻量枚举）——

    /** 层能力与状态：物理链路未验证是硬编码事实，UI 必须展示。 */
    @JavascriptInterface public String info() {
        JSONObject o = put(new JSONObject(), "ok", true);
        put(o, "link", link != null);
        put(o, "opened", scsi != null);
        if (openedKey != null) put(o, "openedKey", openedKey);
        put(o, "romDevice", "rom".equals(openedIsRom));
        put(o, "loaderReady", loaderReady);
        put(o, "loaderBundled", loader != null && loader.length == LOADER_SIZE);
        put(o, "loaderSha256", LOADER_SHA256);
        put(o, "physicalLinkVerified", false);          // 手机 OTG 链路未验证（诚实标注）
        return o.toString();
    }

    /** 枚举 USB 设备（标记 QCY 普通身份 / BR28 ROM 身份）。 */
    @JavascriptInterface public String scan() {
        if (link == null) return err("未注入 USB 层（纯协议演示形态无设备 IO）");
        try {
            JSONArray devices = new JSONArray(link.scanJson());
            for (int i = 0; i < devices.length(); i++) {
                JSONObject d = devices.getJSONObject(i);
                int vid = d.getInt("vid"), pid = d.getInt("pid");
                boolean rom = vid == VID_ROM && pid == PID_ROM;
                boolean qcy = vid == VID_QCY && pid == PID_QCY;
                d.put("rom", rom);
                d.put("qcy", qcy);
            }
            JSONObject o = put(new JSONObject(), "ok", true);
            o.put("devices", devices);
            return o.toString();
        } catch (JSONException e) {
            return err("scan 结果解析失败: " + e.getMessage());
        }
    }

    @JavascriptInterface public boolean hasPermission(String key) {
        return link != null && key != null && link.hasPermission(key);
    }

    /** 请求授权（异步：结果经 usb_permission 事件）。 */
    @JavascriptInterface public String requestPermission(String key) {
        if (link == null) return err("未注入 USB 层（纯协议演示形态无设备 IO）");
        if (key == null) return err("key 不能为空");
        link.requestPermission(key);
        return ok();
    }

    // —— 设备操作面（IO，经 Executor）——

    /** 打开并 claim 设备。key 来自 scan()。 */
    @JavascriptInterface public String open(final String key) {
        if (link == null) return err("未注入 USB 层（纯协议演示形态无设备 IO）");
        if (key == null) return err("key 不能为空");
        run(new Runnable() { @Override public void run() {
            String fail = null;
            try {
                if (!link.hasPermission(key)) fail = "未授权（先 requestPermission）";
                else if (!link.open(key)) fail = "打开/claim 失败（OTG 或占用？）";
            } catch (Exception e) { fail = "打开异常: " + e; }
            if (fail != null) { openedKey = null; scsi = null; loaderReady = false; openedIsRom = null; emitEvent(put(put(put(eventJson("usb_open"), "ok", false), "key", key), "error", fail)); return; }
            openedKey = key;
            scsi = new UsbScsi(link.channel());
            loaderReady = false;
            try {
                JSONArray devices = new JSONArray(link.scanJson());
                for (int i = 0; i < devices.length(); i++) {
                    JSONObject d = devices.getJSONObject(i);
                    if (key.equals(d.optString("key", ""))) {
                        int vid = d.getInt("vid"), pid = d.getInt("pid");
                        openedIsRom = (vid == VID_ROM && pid == PID_ROM) ? "rom" : "other";
                    }
                }
            } catch (JSONException ignore) { openedIsRom = "other"; }
            emitEvent(put(put(put(eventJson("usb_open"), "ok", true), "key", key), "rom", "rom".equals(openedIsRom)));
        } });
        return ok();
    }

    /** 关闭当前设备。 */
    @JavascriptInterface public String close() {
        if (link == null) return err("未注入 USB 层（纯协议演示形态无设备 IO）");
        run(new Runnable() { @Override public void run() {
            link.close();
            openedKey = null; scsi = null; loaderReady = false; openedIsRom = null;
            emitEvent(eventJson("usb_closed"));
        } });
        return ok();
    }

    /** INQUIRY：确认 BR28/UBOOT1.00 身份。 */
    @JavascriptInterface public String inquiry() {
        String gate = requireOpen();
        if (gate != null) return gate;
        run(new Runnable() { @Override public void run() {
            try {
                byte[] raw = scsi.inquiry();
                String vendor = ascii(raw, 8, 16), product = ascii(raw, 16, 32), revision = ascii(raw, 32, 36);
                JSONObject o = put(put(eventJson("usb_inquiry"), "ok", true), "rawHex", UsbScsi.hex(raw));
                put(o, "vendor", vendor); put(o, "product", product); put(o, "revision", revision);
                put(o, "br28", "BR28".equals(vendor) && "UBOOT1.00".equals(product));
                emitEvent(o);
            } catch (Exception e) {
                emitEvent(put(put(eventJson("usb_inquiry"), "ok", false), "error", String.valueOf(e)));
            }
        } });
        return ok();
    }

    /** 进入 ROM 模式：能力读（8×ff）→ 只发一次进入请求；结果以重枚举身份为准。 */
    @JavascriptInterface public String enterRom() {
        String gate = requireOpen();
        if (gate != null) return gate;
        if ("rom".equals(openedIsRom)) return err("当前设备已是 BR28 ROM 身份，无需进入");
        run(new Runnable() { @Override public void run() {
            try {
                byte[] cap = link.recoveryCapability();
                if (cap == null || cap.length != 8) { emitEvent(put(put(eventJson("usb_enter_rom"), "ok", false), "error", "能力读失败（非普通 QCY USB 身份？）")); return; }
                boolean all = true;
                for (byte b : cap) if (b != (byte) 0xff) all = false;
                if (!all) { emitEvent(put(put(eventJson("usb_enter_rom"), "ok", false), "error", "恢复能力不符: " + UsbScsi.hex(cap))); return; }
                boolean sent = link.enterRomRequest();   // 一次性；请求后设备重启，连接作废
                emitEvent(put(put(put(eventJson("usb_enter_rom"), "ok", true), "sent", sent),
                    "note", "已发送一次性进入请求；设备会重启重枚举——请重新扫描，以出现 4c4a:3442 / BR28 UBOOT 身份为准，不重发"));
            } catch (Exception e) {
                emitEvent(put(put(eventJson("usb_enter_rom"), "ok", false), "error", String.valueOf(e)
                    + "（重启可能使请求本身报错——以重扫身份为准，不重发）"));
            } finally {
                link.close(); openedKey = null; scsi = null; loaderReady = false; openedIsRom = null;
            }
        } });
        return ok();
    }

    /** 装载 RAM loader：FB06×46（512B 分块）→ FB08 跳转 → FC0A 校验 SPI NOR。 */
    @JavascriptInterface public String loadLoader() {
        String gate = requireOpen();
        if (gate != null) return gate;
        if (!"rom".equals(openedIsRom)) return err("只对 BR28 ROM 身份装载 loader（当前: " + openedIsRom + "）");
        if (loader == null || loader.length != LOADER_SIZE) return err("loader 未打包或长度不符（构建需内置 br28loader.bin）");
        run(new Runnable() { @Override public void run() {
            try {
                String sha = sha256(loader);
                if (!expectedLoaderSha.equals(sha)) { emitEvent(put(put(eventJson("usb_loader"), "ok", false), "error", "loader 摘要不符: " + sha)); return; }
                for (int p = 0; p < loader.length; p += UsbScsi.SECTOR) {
                    scsi.ramWrite(UsbScsi.LOADER_ADDRESS + p, java.util.Arrays.copyOfRange(loader, p, p + UsbScsi.SECTOR));
                }
                scsi.loaderStart();
                byte[] q = scsi.onlineQuery();
                if (q.length < 3 || (q[0] & 0xff) != 0xfc || (q[1] & 0xff) != 0x0a || (q[2] & 0xff) != 0x03) {
                    loaderReady = false;
                    emitEvent(put(put(eventJson("usb_loader"), "ok", false), "error", "loader 在线查询不符: " + UsbScsi.hex(q)));
                    return;
                }
                loaderReady = true;
                emitEvent(put(put(eventJson("usb_loader"), "ok", true), "spiNor", true));
            } catch (Exception e) {
                loaderReady = false;
                emitEvent(put(put(eventJson("usb_loader"), "ok", false), "error", String.valueOf(e)));
            }
        } });
        return ok();
    }

    /** 读 ROM 窗（128KiB FD07×1024，双读一致；结果存 app 私有目录）。 */
    @JavascriptInterface public String readRomWindow() {
        String gate = requireOpen();
        if (gate != null) return gate;
        if (!loaderReady) return err("loader 未就绪（先 loadLoader；FD07 只在 loader 就绪后放行）");
        if (saver == null) return err("未注入落盘器（无法保存结果）");
        run(new Runnable() { @Override public void run() {
            try {
                byte[] first = readWindow(0, ROM_WINDOW, "rom_window");
                byte[] second = readWindow(0, ROM_WINDOW, "rom_window");
                if (!java.util.Arrays.equals(first, second)) { emitEvent(put(put(eventJson("usb_rom_window"), "ok", false), "error", "双读不一致（放弃，不重试）")); return; }
                String path = saver.save("rom-window-" + System.currentTimeMillis() + ".bin", first);
                JSONObject o = put(put(eventJson("usb_rom_window"), "ok", true), "bytes", first.length);
                put(o, "sha256", sha256(first));
                put(o, "headHex", UsbScsi.hex(java.util.Arrays.copyOf(first, Math.min(64, first.length))));
                put(o, "path", path);
                emitEvent(o);
            } catch (Exception e) {
                emitEvent(put(put(eventJson("usb_rom_window"), "ok", false), "error", String.valueOf(e)));
            }
        } });
        return ok();
    }

    /** 全片备份（2MiB FD05×4096，双读一致；只读，不写 Flash）。 */
    @JavascriptInterface public String backupFlash() {
        String gate = requireOpen();
        if (gate != null) return gate;
        if (!loaderReady) return err("loader 未就绪（先 loadLoader；FD05 只在 loader 就绪后放行）");
        if (saver == null) return err("未注入落盘器（无法保存结果）");
        run(new Runnable() { @Override public void run() {
            try {
                byte[] first = readFlash(FLASH_SIZE, "backup_pass1");
                byte[] second = readFlash(FLASH_SIZE, "backup_pass2");
                if (!java.util.Arrays.equals(first, second)) { emitEvent(put(put(eventJson("usb_backup"), "ok", false), "error", "双读不一致（放弃，不重试）")); return; }
                String path = saver.save("flash-backup-" + System.currentTimeMillis() + ".bin", first);
                JSONObject o = put(put(eventJson("usb_backup"), "ok", true), "bytes", first.length);
                put(o, "sha256", sha256(first));
                put(o, "path", path);
                emitEvent(o);
            } catch (Exception e) {
                emitEvent(put(put(eventJson("usb_backup"), "ok", false), "error", String.valueOf(e)));
            }
        } });
        return ok();
    }

    /** FD07 分块读整窗（块 128B；进度事件 usb_progress）。 */
    private byte[] readWindow(int base, int size, String op) {
        ByteArrayOutputStream out = new ByteArrayOutputStream(size);
        int block = UsbScsi.MEM_READ_MAX, done = 0;
        while (done < size) {
            out.write(scsi.readMem(base + done, Math.min(block, size - done)), 0, Math.min(block, size - done));
            done += block;
            if (done % 4096 == 0 || done >= size) progress(op, done, size);
        }
        return out.toByteArray();
    }

    /** FD05 扇区读整片（512B/扇区；进度事件 usb_progress）。 */
    private byte[] readFlash(int size, String op) {
        ByteArrayOutputStream out = new ByteArrayOutputStream(size);
        int done = 0;
        while (done < size) {
            byte[] sector = scsi.readFlashSector(done);
            out.write(sector, 0, sector.length);
            done += UsbScsi.SECTOR;
            if (done % (UsbScsi.SECTOR * 128) == 0 || done >= size) progress(op, done, size);
        }
        return out.toByteArray();
    }

    private void progress(String op, int done, int total) {
        emitEvent(put(put(put(eventJson("usb_progress"), "op", op), "done", done), "total", total));
    }

    public static String sha256(byte[] data) {
        try {
            MessageDigest d = MessageDigest.getInstance("SHA-256");
            StringBuilder s = new StringBuilder(64);
            for (byte b : d.digest(data)) s.append(String.format(Locale.ROOT, "%02x", b));
            return s.toString();
        } catch (java.security.NoSuchAlgorithmException impossible) {
            throw new IllegalStateException(impossible);
        }
    }

    private static String ascii(byte[] raw, int from, int to) {
        if (raw == null || raw.length < to) return "";
        StringBuilder s = new StringBuilder(to - from);
        for (int i = from; i < to; i++) { char c = (char) (raw[i] & 0xff); if (c >= 0x20 && c < 0x7f) s.append(c); }
        return s.toString().trim();
    }
}
