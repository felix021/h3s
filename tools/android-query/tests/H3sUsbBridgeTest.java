import cn.felix021.h3squery.H3sUsbBridge;
import cn.felix021.h3squery.UsbScsi;
import java.util.ArrayList;
import java.util.List;
import org.json.JSONArray;
import org.json.JSONObject;

/**
 * H3sUsbBridge 纯 JVM 单测：桩 Link + 桩 BR28 ROM 设备（说最小协议：INQUIRY/FB06/FB08/
 * FC0A/FD07/FD05），钉住开箱流程、loader 摘要门、FD07/FD05 的 loader 相位门、双读一致、
 * 进入 ROM 的一次性控制序列与拔出复位。物理链路未验证——桩设备只保证协议形状。
 */
public class H3sUsbBridgeTest {
    static int failures = 0;

    static void ok(String what) { System.out.println("ok   " + what); }
    static void fail(String what) { failures++; System.out.println("FAIL " + what); }
    static void check(String what, boolean cond) { if (cond) ok(what); else fail(what); }

    /** 桩 BR28 ROM 设备：按地址合成确定性数据（双读天然一致）。 */
    static class FakeRomDevice implements UsbScsi.Channel {
        int fb06Chunks = 0;
        boolean fb08 = false;
        int pendingOpcode; int pendingSize; byte[] pendingCdb; int pendingTag;
        boolean pendingDirIn; boolean dataServed;
        public int bulkOut(byte[] data) {
            if (data.length == 31 && data[0] == 'U') {                     // CBW：cdb 在偏移 15 起
                pendingOpcode = data[15] == 0x12 ? 0x12 : (((data[15] & 0xff) << 8) | (data[16] & 0xff));
                pendingSize = (data[8] & 0xff) | ((data[9] & 0xff) << 8) | ((data[10] & 0xff) << 16) | ((data[11] & 0xff) << 24);
                pendingTag = (data[4] & 0xff) | ((data[5] & 0xff) << 8) | ((data[6] & 0xff) << 16) | ((data[7] & 0xff) << 24);
                pendingCdb = data;
                pendingDirIn = (data[12] & 0x80) != 0;
                dataServed = false;
                return data.length;
            }
            if (pendingOpcode == UsbScsi.OP_RAM_WRITE) fb06Chunks++;       // OUT 数据相位
            return data.length;
        }
        public byte[] bulkIn(int max) {
            if (pendingDirIn && pendingSize > 0 && !dataServed) {          // IN 数据相位（先于 CSW）
                dataServed = true;
                return serve(pendingOpcode, pendingCdb, pendingSize);
            }
            return csw(pendingTag);                                        // CSW（回显 tag、residue/status 0）
        }
        static byte[] csw(int tag) {
            return new byte[]{'U','S','B','S', (byte)tag, (byte)(tag>>8), (byte)(tag>>16), (byte)(tag>>24), 0,0,0,0, 0};
        }
        byte[] serve(int opcode, byte[] cbw, int size) {
            byte c0 = cbw[15], c1 = cbw[16];                               // CDB 从 CBW 偏移 15 起
            if (opcode == 0x12) {
                byte[] r = new byte[36];
                put(r, 8, "BR28    ");            // vendor 8B
                put(r, 16, "UBOOT1.00        ");  // product 16B
                put(r, 32, "1.00");              // revision 4B
                return r;
            }
            if (opcode == UsbScsi.OP_RAM_WRITE) return new byte[0];
            if (opcode == UsbScsi.OP_LOADER_START) { fb08 = true; return echo(c0, c1, 16); }
            if (opcode == UsbScsi.OP_ONLINE_QUERY) {
                byte[] r = new byte[16];
                r[0] = (byte)0xfc; r[1] = 0x0a; r[2] = 0x03;
                return r;
            }
            int addr = ((cbw[17] & 0xff) << 24) | ((cbw[18] & 0xff) << 16) | ((cbw[19] & 0xff) << 8) | (cbw[20] & 0xff);
            byte[] r = new byte[size];
            for (int i = 0; i < size; i++) r[i] = (byte) ((addr + i * 13 + 7) & 0xff); // 地址确定性数据
            return r;
        }
        static void put(byte[] r, int off, String s) {
            byte[] b = s.getBytes();
            System.arraycopy(b, 0, r, off, Math.min(b.length, r.length - off));
        }
        static byte[] echo(byte c0, byte c1, int n) {
            byte[] r = new byte[n];
            r[0] = c0; r[1] = c1;
            for (int i = 2; i < n; i++) r[i] = 0x55;
            return r;
        }
    }

    /** 桩 Link：一台 ROM 设备 + 一台普通 QCY 设备。 */
    static class FakeLink implements H3sUsbBridge.Link {
        final FakeRomDevice rom = new FakeRomDevice();
        H3sUsbBridge.Callbacks cb;
        boolean opened;
        int enterRequests;
        byte[] capability = new byte[]{(byte)0xff,(byte)0xff,(byte)0xff,(byte)0xff,(byte)0xff,(byte)0xff,(byte)0xff,(byte)0xff};
        public String scanJson() {
            JSONArray a = new JSONArray();
            try {
                a.put(new JSONObject().put("key", "/dev/rom").put("vid", 0x4c4a).put("pid", 0x3442).put("name", "BR28"));
                a.put(new JSONObject().put("key", "/dev/qcy").put("vid", 0x3654).put("pid", 0x4a55).put("name", "QCY H3S"));
            } catch (org.json.JSONException impossible) { throw new RuntimeException(impossible); }
            return a.toString();
        }
        public boolean hasPermission(String key) { return true; }
        public void requestPermission(String key) { if (cb != null) cb.onPermission(key, true); }
        public boolean open(String key) { opened = true; return true; }
        public void close() { opened = false; }
        public UsbScsi.Channel channel() { return opened ? rom : null; }
        public byte[] recoveryCapability() { return capability; }
        public boolean enterRomRequest() { enterRequests++; return true; }
        public void setCallbacks(H3sUsbBridge.Callbacks cb) { this.cb = cb; }
    }

    /** 事件收集 sink。 */
    static class Events implements H3sUsbBridge.ResultSink {
        final List<JSONObject> list = new ArrayList<JSONObject>();
        public void emit(String json) {
            try { list.add(new JSONObject(json)); } catch (org.json.JSONException e) { throw new RuntimeException(e); }
        }
        JSONObject last(String event) {
            for (int i = list.size() - 1; i >= 0; i--) if (event.equals(list.get(i).optString("event", ""))) return list.get(i);
            return null;
        }
    }

    static class Save implements H3sUsbBridge.FileSaver {
        byte[] saved; String name;
        public String save(String name, byte[] data) { this.name = name; this.saved = data; return "/data/usb/" + name; }
    }

    public static void main(String[] args) throws Exception {
        // 1. 无 Link 形态：info 可用、设备 IO 报错
        H3sUsbBridge bare = new H3sUsbBridge();
        JSONObject i = new JSONObject(bare.info());
        check("bare info link=false", !i.getBoolean("link"));
        check("bare info verified=false", !i.getBoolean("physicalLinkVerified"));
        check("bare inquiry 拒绝", !new JSONObject(bare.inquiry()).getBoolean("ok"));

        // 2. 开 ROM 设备 → INQUIRY 确认 BR28
        FakeLink link = new FakeLink();
        Events ev = new Events();
        Save save = new Save();
        byte[] fakeLoader = new byte[H3sUsbBridge.LOADER_SIZE];
        for (int j = 0; j < fakeLoader.length; j++) fakeLoader[j] = (byte) (j & 0xff);
        String fakeSha = H3sUsbBridge.sha256(fakeLoader);
        H3sUsbBridge b = new H3sUsbBridge(link, ev, null, save, fakeLoader, fakeSha);
        check("open 接受", new JSONObject(b.open("/dev/rom")).getBoolean("ok"));
        JSONObject open = ev.last("usb_open");
        check("usb_open ok+rom", open != null && open.getBoolean("ok") && open.getBoolean("rom"));
        check("inquiry 接受", new JSONObject(b.inquiry()).getBoolean("ok"));
        JSONObject inq = ev.last("usb_inquiry");
        check("INQUIRY BR28/UBOOT", inq.getBoolean("ok") && inq.getBoolean("br28") && "BR28".equals(inq.getString("vendor")));

        // 3. loader 相位门：未装载就窗读 → 拒绝；loader 摘要不对 → 拒绝（未发任何 USB 命令）
        check("未装载窗读拒绝", !new JSONObject(b.readRomWindow()).getBoolean("ok"));
        Events evBad = new Events();
        H3sUsbBridge badSha = new H3sUsbBridge(link, evBad, null, save, fakeLoader, "deadbeef");
        new JSONObject(badSha.open("/dev/rom"));
        badSha.loadLoader();
        JSONObject bad = evBad.last("usb_loader");
        check("loader 摘要不符拒绝", bad != null && !bad.getBoolean("ok"));
        check("摘要不符未发 FB06", 0 == link.rom.fb06Chunks);
        Events ev2 = new Events();
        H3sUsbBridge good = new H3sUsbBridge(link, ev2, null, save, fakeLoader, fakeSha);
        new JSONObject(good.open("/dev/rom"));
        check("loadLoader 接受", new JSONObject(good.loadLoader()).getBoolean("ok"));
        JSONObject ld = ev2.last("usb_loader");
        check("loader 就绪事件", ld != null && ld.getBoolean("ok"));
        check("FB06 分块=46", 46 == link.rom.fb06Chunks);
        check("FB08 已跳转", link.rom.fb08);

        // 4. 窗读：双读一致 → 落盘 + sha
        check("窗读接受", new JSONObject(good.readRomWindow()).getBoolean("ok"));
        JSONObject win = ev2.last("usb_rom_window");
        check("窗读 ok/131072B", win.getBoolean("ok") && win.getInt("bytes") == 0x20000);
        check("窗读落盘 sha 一致", H3sUsbBridge.sha256(save.saved).equals(win.getString("sha256")));
        check("窗读无备份串扰", save.name.startsWith("rom-window-"));

        // 5. 全片备份（桩设备 FD05 确定性数据）
        check("备份接受", new JSONObject(good.backupFlash()).getBoolean("ok"));
        JSONObject bk = ev2.last("usb_backup");
        check("备份 ok/2MiB", bk.getBoolean("ok") && bk.getInt("bytes") == 0x200000);

        // 6. 进入 ROM：普通设备能力读 ff×8 → 一次性请求 → 关闭
        Events ev3 = new Events();
        H3sUsbBridge q = new H3sUsbBridge(link, ev3, null, save, fakeLoader, fakeSha);
        new JSONObject(q.open("/dev/qcy"));
        check("enterRom 接受", new JSONObject(q.enterRom()).getBoolean("ok"));
        JSONObject er = ev3.last("usb_enter_rom");
        check("enterRom 事件 ok", er != null && er.getBoolean("ok") && er.getBoolean("sent"));
        check("进入请求恰一次", 1 == link.enterRequests);
        check("enterRom 后关闭", !link.opened);
        // 能力不符 → 拒绝且不发请求
        link.capability = new byte[8];
        Events ev4 = new Events();
        H3sUsbBridge q2 = new H3sUsbBridge(link, ev4, null, save, fakeLoader, fakeSha);
        new JSONObject(q2.open("/dev/qcy"));
        new JSONObject(q2.enterRom());
        JSONObject er2 = ev4.last("usb_enter_rom");
        check("能力不符拒绝", er2 != null && !er2.getBoolean("ok"));
        check("能力不符不发请求", 1 == link.enterRequests);
        link.capability = new byte[]{(byte)0xff,(byte)0xff,(byte)0xff,(byte)0xff,(byte)0xff,(byte)0xff,(byte)0xff,(byte)0xff};

        // 7. ROM 身份直接 enterRom → 拒绝（返回值）
        Events ev5 = new Events();
        H3sUsbBridge r = new H3sUsbBridge(link, ev5, null, save, fakeLoader, fakeSha);
        new JSONObject(r.open("/dev/rom"));
        check("ROM 身份无需进入", !new JSONObject(r.enterRom()).getBoolean("ok"));

        // 8. 拔出复位
        r.notifyDetached("/dev/rom");
        JSONObject det = ev5.last("usb_detached");
        check("usb_detached 事件", det != null && "/dev/rom".equals(det.getString("key")));
        check("拔出后窗读拒绝", !new JSONObject(r.readRomWindow()).getBoolean("ok"));

        if (failures > 0) { System.out.println("FAILED: " + failures); System.exit(1); }
        System.out.println("H3sUsbBridgeTest passed: bare 形态、open/INQUIRY 身份、loader 相位门+摘要门（46×FB06+FB08）、"
            + "窗读/备份双读落盘、enterRom 一次性+能力门、ROM 身份短路、拔出复位。桩设备=协议形状，链路未验证。");
    }
}
