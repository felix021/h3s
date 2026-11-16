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
 *   - 默认（真机）形态：命令编码层（UsbScsi）不存在擦除/编程/复位命令——写路径在代码层
 *     就不可达（红线：候选镜像 UNTESTED 不刷机）；
 *   - M3 仿真刷机（仅 flashWritable=true 的 sim 构造）：FB01/FB04 经「写能力位+计划感知守卫
 *     （P1-4：地址∈armed 计划扇区+当前活动扇区+数据==已确认目标）+server 侧 armed/计划门」
 *     三层闭合后才可发；nonce 单次+2min 过期（UpgradeGate 先例）；错误中止保持 armed；
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
        /** M3：把本次刷机计划（{"sectors":[…],"target_sha256":…,"nonce":…}）告知仿真设备
         *  （usb_arm 消息，armed 协商）。真机链路无此面：不覆写=false=不支持。 */
        default boolean armFlash(String planJson) { return false; }
        /** M3：仿真设备整机重启（rom→normal 重枚举）。真机=手动断电/拔线，链路层无此命令。 */
        default boolean rebootDevice() { return false; }
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

    /** 目标镜像读入（壳实现：app 外部/私有 files/usb/<name>；测试=桩）。
     *  name 由 JS 传入——业务层先做文件名白名单校验，实现侧仍不得解析路径分量。 */
    public interface FileLoader { byte[] load(String name); }

    private final Link link;            // 可空：null=纯协议演示形态
    private final ResultSink sink;      // 可空：null 时事件丢弃
    private final Executor io;          // 可空：null=调用线程直接执行（JVM 测试）
    private final FileSaver saver;      // 可空：null=读盘类操作不可用
    private final byte[] loader;        // 可空：null=未打包 loader
    private final String expectedLoaderSha;  // 生产=LOADER_SHA256；测试可注入桩摘要
    private final boolean flashWritable;     // M3 写能力：仅仿真刷机链路（壳 sim 模式）true
    private final FileLoader targetLoader;   // 可空：null=planRestore 不可用
    private UsbScsi scsi;               // open 成功后非空
    private String openedKey;
    private boolean loaderReady;        // FB06×46+FB08+FC0A 全过
    private String openedIsRom;
    /** M3 恢复会话（PREPARED/ARMED；null=空闲）。字段不可变整体替换，锁 this 保护流转。 */
    private static final class Restore {
        final String stage;             // "PREPARED" | "ARMED"
        final FlashPlan plan;
        final byte[] baseline;          // 计划时的全片基线（擦前核验对照，h3s_usb.py:163）
        final long preparedAt;
        Restore(String stage, FlashPlan plan, byte[] baseline, long preparedAt) {
            this.stage = stage; this.plan = plan; this.baseline = baseline; this.preparedAt = preparedAt;
        }
    }
    private volatile Restore restore;

    public H3sUsbBridge() { this(null, null, null, null, null); }

    public H3sUsbBridge(Link link, ResultSink sink, Executor io, FileSaver saver, byte[] loader) {
        this(link, sink, io, saver, loader, LOADER_SHA256);
    }

    /** 测试形态：允许注入桩 loader 摘要（生产构造固定 LOADER_SHA256）。 */
    public H3sUsbBridge(Link link, ResultSink sink, Executor io, FileSaver saver, byte[] loader, String expectedLoaderSha) {
        this(link, sink, io, saver, loader, expectedLoaderSha, false, null);
    }

    /** M3 仿真刷机构造：flashWritable 仅壳 sim 模式传 true（真机构建保持默认构造=只读红线）；
     *  targetLoader 提供目标镜像读入。 */
    public H3sUsbBridge(Link link, ResultSink sink, Executor io, FileSaver saver, byte[] loader,
                        boolean flashWritable, FileLoader targetLoader) {
        this(link, sink, io, saver, loader, LOADER_SHA256, flashWritable, targetLoader);
    }

    /** 全参形态（测试组合注入用；生产走上面两个便捷构造——flashWritable 默认 false）。 */
    public H3sUsbBridge(Link link, ResultSink sink, Executor io, FileSaver saver, byte[] loader,
                        String expectedLoaderSha, boolean flashWritable, FileLoader targetLoader) {
        this.link = link;
        this.sink = sink;
        this.io = io;
        this.saver = saver;
        this.loader = loader;
        this.expectedLoaderSha = expectedLoaderSha;
        this.flashWritable = flashWritable;
        this.targetLoader = targetLoader;
        if (link != null) link.setCallbacks(new Callbacks() {
            @Override public void onPermission(final String key, final boolean granted) {
                emitEvent(put(put(eventJson("usb_permission"), "key", key), "granted", granted));
            }
            @Override public void onDetached(final String key) { notifyDetached(key); }
        });
    }

    /** 恢复计划（不可变；扇区数组私有+进出防御性复制——P1-4：public final int[]
     *  内容可变不算不可变）。 */
    public static final class FlashPlan {
        private final int[] sectors;    // 4KiB 基址升序去重
        private final byte[] target;    // 2MiB 预期镜像
        public final String targetSha256, baseSha256, nonce;
        public FlashPlan(int[] sectors, byte[] target, String nonce, String baseSha256) {
            if (sectors == null || sectors.length == 0) throw new IllegalArgumentException("扇区计划不能为空");
            if (target == null || target.length != FLASH_SIZE) throw new IllegalArgumentException("目标镜像须恰 2MiB");
            if (nonce == null || nonce.isEmpty()) throw new IllegalArgumentException("nonce 不能为空");
            int[] copy = sectors.clone();
            java.util.Arrays.sort(copy);
            for (int i = 0; i < copy.length; i++) {
                if (copy[i] < 0 || copy[i] % UsbScsi.FLASH_SECTOR_4K != 0 || copy[i] >= FLASH_SIZE)
                    throw new IllegalArgumentException("扇区基址须 4KiB 对齐且在 2MiB 内: 0x" + Integer.toHexString(copy[i]));
                if (i > 0 && copy[i] == copy[i - 1]) throw new IllegalArgumentException("扇区基址重复: 0x" + Integer.toHexString(copy[i]));
            }
            this.sectors = copy;
            this.target = target.clone();
            this.targetSha256 = sha256(target);
            this.baseSha256 = baseSha256;
            this.nonce = nonce;
        }
        public int sectorCount() { return sectors.length; }
        public int sectorAt(int index) { return sectors[index]; }
        /** 防御性复制出口。 */
        public int[] sectors() { return sectors.clone(); }
        public boolean contains(int sector) { return java.util.Arrays.binarySearch(sectors, sector) >= 0; }
        /** 目标切片（防御性复制）。 */
        public byte[] slice(int address, int length) {
            if (address < 0 || length < 0 || address + length > target.length)
                throw new IllegalArgumentException("目标切片越界: 0x" + Integer.toHexString(address) + "+" + length);
            return java.util.Arrays.copyOfRange(target, address, address + length);
        }
    }

    /** 计划感知写守卫（UsbScsi.RestoreGuard 实现；h3s_usb.py:86-101 RestoreTransport 同款：
     *  活动扇区∈计划、擦=活动扇区、写=活动扇区内 512 对齐且数据==已确认目标）。 */
    static final class PlanGuard implements UsbScsi.RestoreGuard {
        private final FlashPlan plan;
        private int activeSector = -1;
        PlanGuard(FlashPlan plan) { this.plan = plan; }
        /** 执行器每扇区擦除前设置（h3s_usb.py:165 t.active_sector = address）。 */
        void beginSector(int address) { this.activeSector = address; }
        int activeSector() { return activeSector; }
        @Override public void checkErase(int address) {
            requireActive();
            if (address != activeSector)
                throw new IllegalStateException("FB01 计划外擦除: 0x" + Integer.toHexString(address)
                    + "（活动扇区 0x" + Integer.toHexString(activeSector) + "）");
        }
        @Override public void checkProgram(int address, byte[] data) {
            requireActive();
            int sector = address / UsbScsi.FLASH_SECTOR_4K * UsbScsi.FLASH_SECTOR_4K;
            if (sector != activeSector || address % UsbScsi.SECTOR != 0)
                throw new IllegalStateException("FB04 计划外编程: 0x" + Integer.toHexString(address)
                    + "（活动扇区 0x" + Integer.toHexString(activeSector) + "）");
            byte[] expect = plan.slice(address, UsbScsi.SECTOR);   // h3s_usb.py:98 数据==目标
            if (data == null || data.length != UsbScsi.SECTOR || !java.util.Arrays.equals(data, expect))
                throw new IllegalStateException("FB04 数据与已确认目标不符: 0x" + Integer.toHexString(address));
        }
        private void requireActive() {
            if (activeSector < 0 || !plan.contains(activeSector))
                throw new IllegalStateException("无计划内活动扇区（FB01 先行）");
        }
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

    private static JSONObject putValue(JSONObject o, String key, Object value) {
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
        put(o, "flashWritable", flashWritable);         // M3：仅仿真刷机链路 true（真机构建=只读红线）
        Restore r = restore;
        if (r != null) put(o, "restoreStage", r.stage);
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
            scsi = new UsbScsi(link.channel(), flashWritable);   // M3：写能力随 sim Link 路径闭合
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
        return readFlashRange(0, size, op);
    }

    /** FD05 从任意 512 对齐地址起读 size 字节（M3：扇区核验/全片复读不再只从 0 开始）。 */
    private byte[] readFlashRange(int address, int size, String op) {
        ByteArrayOutputStream out = new ByteArrayOutputStream(size);
        int done = 0;
        while (done < size) {
            byte[] sector = scsi.readFlashSector(address + done);
            out.write(sector, 0, sector.length);
            done += UsbScsi.SECTOR;
            if (done % (UsbScsi.SECTOR * 128) == 0 || done >= size) progress(op, address + done, address + size);
        }
        return out.toByteArray();
    }

    private void progress(String op, int done, int total) {
        emitEvent(put(put(put(eventJson("usb_progress"), "op", op), "done", done), "total", total));
    }

    // —— M3 恢复业务（docs/sim-m3-flash-spec.md §4.3；执行器对照 h3s_usb.py:150-184 逐句）——

    /** nonce 确认有效期（UpgradeGate.MAX_AGE_MS=120s 先例）。 */
    public static final long RESTORE_MAX_AGE_MS = 120000;
    /** 4KiB NOR 擦除态（readFlashRange 擦除核验对照，h3s_usb.py:168）。 */
    private static final byte[] FF_4096 = new byte[UsbScsi.FLASH_SECTOR_4K];
    static { java.util.Arrays.fill(FF_4096, (byte) 0xff); }

    /** 时钟注入点（nonce 过期测试跨包子类覆写）。 */
    protected long now() { return System.currentTimeMillis(); }

    /** 目标文件名白名单（JS 输入，防路径分量）。 */
    static boolean validTargetName(String name) {
        return name != null && name.matches("[A-Za-z0-9._-]{1,64}") && !name.startsWith(".");
    }

    /** 生成刷机计划：读入目标镜像（2MiB）→ 全片读基线 → diff 出 4KiB 扇区集合 →
     *  PREPARED 态发 usb_restore_plan（nonce 展示，等 confirmRestore）。 */
    @JavascriptInterface public String planRestore(final String targetName) {
        String gate = requireOpen();
        if (gate != null) return gate;
        if (!flashWritable) return err("本形态未启用刷机路径（仅仿真 armed 会话，真机走官方恢复工具）");
        if (!loaderReady) return err("loader 未就绪（先 loadLoader）");
        if (targetLoader == null) return err("未注入目标镜像读入器");
        if (!validTargetName(targetName)) return err("目标文件名非法（仅字母数字._-）: " + targetName);
        synchronized (this) {
            if (restore != null) return err("已有刷机会话（stage=" + restore.stage + "；先完成或取消）");
        }
        run(new Runnable() { @Override public void run() {
            try {
                byte[] target = targetLoader.load(targetName);
                if (target == null || target.length != FLASH_SIZE)
                    { emitEvent(restoreFail("目标镜像读入失败（须恰 2MiB）: " + targetName)); return; }
                byte[] baseline = readFlash(FLASH_SIZE, "plan_read");
                java.util.ArrayList<Integer> changed = new java.util.ArrayList<Integer>();
                for (int base = 0; base < FLASH_SIZE; base += UsbScsi.FLASH_SECTOR_4K) {
                    for (int i = 0; i < UsbScsi.FLASH_SECTOR_4K; i += UsbScsi.SECTOR) {
                        if (!java.util.Arrays.equals(
                                java.util.Arrays.copyOfRange(baseline, base + i, base + i + UsbScsi.SECTOR),
                                java.util.Arrays.copyOfRange(target, base + i, base + i + UsbScsi.SECTOR))) {
                            changed.add(base);
                            break;
                        }
                    }
                }
                if (changed.isEmpty()) { emitEvent(restoreFail("目标与当前芯片内容一致，无需刷机")); return; }
                int[] sectors = new int[changed.size()];
                for (int i = 0; i < sectors.length; i++) sectors[i] = changed.get(i);
                FlashPlan plan = new FlashPlan(sectors, target,
                    java.util.UUID.randomUUID().toString(), sha256(baseline));
                synchronized (H3sUsbBridge.this) { restore = new Restore("PREPARED", plan, baseline, now()); }
                JSONObject o = put(eventJson("usb_restore_plan"), "ok", true);
                JSONArray list = new JSONArray();
                for (int sector : sectors) list.put(sector);
                o = putValue(o, "sectors", list);
                put(o, "targetSha256", plan.targetSha256);
                put(o, "baseSha256", plan.baseSha256);
                put(o, "nonce", plan.nonce);
                put(o, "note", "计划已生成（未 armed、未写任何字节）；2 分钟内单次确认");
                emitEvent(o);
            } catch (Exception e) {
                emitEvent(restoreFail("计划生成失败: " + e));
            }
        } });
        return ok();
    }

    /** 确认计划（nonce 单次+2min 过期，UpgradeGate.authorize 同款）→ Link.armFlash → armed。
     *  真机 Link（default false）=诚实失败，不降级不绕过。 */
    @JavascriptInterface public String confirmRestore(final String nonce) {
        final Restore r = restore;
        if (r == null) return err("没有待确认的刷机计划");
        if (!"PREPARED".equals(r.stage)) return err("计划已确认过（nonce 单次有效）");
        if (nonce == null || !nonce.equals(r.plan.nonce)) return err("确认令牌不匹配");
        if (now() < r.preparedAt || now() - r.preparedAt > RESTORE_MAX_AGE_MS) {
            synchronized (this) { restore = null; }
            emitEvent(restoreFail("计划超过 2 分钟，已作废（请重新生成）"));
            return err("计划超过 2 分钟，已作废");
        }
        run(new Runnable() { @Override public void run() {
            String fail = null;
            try {
                if (!link.armFlash(planJson(r.plan)))
                    fail = "链路不支持刷机协商（真机链路需走官方恢复工具）";
            } catch (Exception e) { fail = "armFlash 异常: " + e; }
            if (fail != null) {
                synchronized (H3sUsbBridge.this) { restore = null; }   // 协商失败=计划作废（诚实失败）
                emitEvent(restoreFail(fail));
                return;
            }
            synchronized (H3sUsbBridge.this) { restore = new Restore("ARMED", r.plan, r.baseline, r.preparedAt); }
            JSONObject o = put(eventJson("usb_restore_armed"), "ok", true);
            put(o, "sectors", r.plan.sectorCount());
            put(o, "note", "设备侧已 armed（copy-on-armed）；可开始擦写");
            emitEvent(o);
        } });
        return ok();
    }

    /** 取消计划：仅 PREPARED 可取消（armed 后取消=危险操作，h3s_usb.py 恢复流无中途取消路径）。 */
    @JavascriptInterface public String cancelRestore() {
        synchronized (this) {
            if (restore == null) return err("没有刷机会话");
            if (!"PREPARED".equals(restore.stage))
                return err("已 armed 不可取消（擦写一旦开始没有安全中止点；完成走 reboot，失败保持 armed 由人工处置）");
            restore = null;
        }
        emitEvent(put(eventJson("usb_restore_cancelled"), "ok", true));
        return ok();
    }

    /** armed 计划的 usb_arm 消息体（server MscBotDevice.arm 契约：sectors/target_sha256/nonce）。 */
    static String planJson(FlashPlan plan) {
        JSONObject o = new JSONObject();
        JSONArray list = new JSONArray();
        for (int i = 0; i < plan.sectorCount(); i++) list.put(plan.sectorAt(i));
        try {
            o.put("sectors", list);
            o.put("target_sha256", plan.targetSha256);
            o.put("nonce", plan.nonce);
        } catch (JSONException impossible) { throw new RuntimeException(impossible); }
        return o.toString();
    }

    /** 执行恢复（对照 h3s_usb.py:162-179 逐句）：擦前核验→FB01→读 ff→8×FB04→读比对→进度→
     *  全片复读==目标。任何核验失败=中止+保持 armed（不自动 disarm；状态留给 UI/人工）。 */
    @JavascriptInterface public String restoreFlash() {
        String gate = requireOpen();
        if (gate != null) return gate;
        if (!flashWritable) return err("本形态未启用刷机路径（仅仿真 armed 会话）");
        if (!loaderReady) return err("loader 未就绪（先 loadLoader）");
        final Restore r = restore;
        if (r == null || !"ARMED".equals(r.stage)) return err("未 armed（先 planRestore → confirmRestore）");
        run(new Runnable() { @Override public void run() {
            final FlashPlan plan = r.plan;
            final UsbScsi s = scsi;
            if (s == null) { emitEvent(restoreFail("设备已断开（保持 armed；重开后可重试）")); return; }
            PlanGuard guard = new PlanGuard(plan);
            s.installRestoreGuard(guard);
            int done = 0;
            try {
                for (int i = 0; i < plan.sectorCount(); i++) {
                    int address = plan.sectorAt(i);
                    requireEquals(readFlashRange(address, UsbScsi.FLASH_SECTOR_4K, "restore_read"),
                        java.util.Arrays.copyOfRange(r.baseline, address, address + UsbScsi.FLASH_SECTOR_4K),
                        "扇区在擦前变化: 0x" + Integer.toHexString(address));                       // :163
                    guard.beginSector(address);                                                        // :165
                    s.eraseSector(address);                                                            // :167
                    requireEquals(readFlashRange(address, UsbScsi.FLASH_SECTOR_4K, "restore_read"),
                        FF_4096, "擦除验证失败: 0x" + Integer.toHexString(address));                   // :168
                    for (int p = address; p < address + UsbScsi.FLASH_SECTOR_4K; p += UsbScsi.SECTOR)
                        s.programSector(p, plan.slice(p, UsbScsi.SECTOR));                            // :171-172
                    requireEquals(readFlashRange(address, UsbScsi.FLASH_SECTOR_4K, "restore_read"),
                        plan.slice(address, UsbScsi.FLASH_SECTOR_4K),
                        "扇区编程验证失败: 0x" + Integer.toHexString(address));                        // :173
                    progress("restore", ++done, plan.sectorCount());
                }
                byte[] finel = readFlash(FLASH_SIZE, "verify");                                       // :177
                requireEquals(finel, plan.slice(0, FLASH_SIZE), "最终全片读回不符");                   // :179
                JSONObject o = put(eventJson("usb_restore"), "ok", true);
                put(o, "sectors", plan.sectorCount());
                put(o, "sha256", sha256(finel));
                put(o, "targetSha256", plan.targetSha256);
                put(o, "note", "擦写完成且全片复读==目标（armed 仍有效，rebootDevice 收尾）");
                emitEvent(o);
            } catch (Exception e) {
                JSONObject o = put(restoreFail("擦写中止: " + e + "（保持 armed；处置前先核对设备状态）"), "sector", done);
                emitEvent(o);
            } finally {
                s.installRestoreGuard(null);   // 守卫只在执行器持扇期间存在
            }
        } });
        return ok();
    }

    /** 收尾：设备整机重启（rom→normal 重枚举）。成功后 armed 作废、会话关闭，重扫见普通身份。 */
    @JavascriptInterface public String rebootDevice() {
        String gate = requireOpen();
        if (gate != null) return gate;
        Restore r = restore;
        if (r == null || !"ARMED".equals(r.stage)) return err("未处于 armed 刷机会话（无需重启收尾）");
        run(new Runnable() { @Override public void run() {
            try {
                if (!link.rebootDevice()) {
                    emitEvent(restoreFail("链路不支持设备重启（真机=手动断电重插，重扫为准）"));
                    return;
                }
                synchronized (H3sUsbBridge.this) { restore = null; }
                link.close(); openedKey = null; scsi = null; loaderReady = false; openedIsRom = null;
                JSONObject o = put(eventJson("usb_restore_reboot"), "ok", true);
                put(o, "note", "重启已送达（armed 作废，刷后内容固化）；请重新扫描，以 normal 身份为准");
                emitEvent(o);
            } catch (Exception e) {
                emitEvent(restoreFail("重启异常: " + e));
            }
        } });
        return ok();
    }

    private static JSONObject restoreFail(String message) {
        try { return new JSONObject().put("event", "usb_restore").put("ok", false).put("error", message); }
        catch (JSONException impossible) { throw new RuntimeException(impossible); }
    }

    private static void requireEquals(byte[] actual, byte[] expect, String what) {
        if (!java.util.Arrays.equals(actual, expect)) throw new IllegalStateException(what);
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
