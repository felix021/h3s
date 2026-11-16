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

    /** 桩 BR28 ROM 设备：按地址合成确定性数据（双读天然一致）。
     *  M3 扩展：2MiB flash 阵列后端（FD05 读真内容；FB01/FB04 可执行——计划守卫在 app 侧，
     *  桩设备只模拟「无守卫的世界」里设备本体的行为）；breakErase=true 模拟擦除失效。 */
    static class FakeRomDevice implements UsbScsi.Channel {
        int fb06Chunks = 0;
        boolean fb08 = false;
        final byte[] flash = new byte[H3sUsbBridge.FLASH_SIZE];
        int eraseCount = 0, programCount = 0;
        boolean breakErase = false;
        int pendingOpcode; int pendingSize; byte[] pendingCdb; int pendingTag;
        boolean pendingDirIn; boolean dataServed;
        FakeRomDevice() {
            for (int base = 0; base < flash.length; base += 512)
                for (int j = 0; j < 512; j++)
                    flash[base + j] = (byte) ((base + j * 13 + 7) & 0xff);
        }
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
            if (pendingOpcode == UsbScsi.OP_SECTOR_PROGRAM && data.length == 512) {
                System.arraycopy(data, 0, flash, addrOf(pendingCdb), 512); // FB04 编程落盘
                programCount++;
            }
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
        static int addrOf(byte[] cbw) {
            return ((cbw[17] & 0xff) << 24) | ((cbw[18] & 0xff) << 16) | ((cbw[19] & 0xff) << 8) | (cbw[20] & 0xff);
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
            if (opcode == UsbScsi.OP_SECTOR_ERASE) {                       // FB01：置 4KiB 为 ff
                int addr = addrOf(cbw);
                if (!breakErase) java.util.Arrays.fill(flash, addr, addr + 4096, (byte) 0xff);
                eraseCount++;
                return echo(c0, c1, 16);
            }
            if (opcode == UsbScsi.OP_FLASH_READ)                           // FD05：真读 flash 后端
                return java.util.Arrays.copyOfRange(flash, addrOf(cbw), addrOf(cbw) + size);
            int addr = addrOf(cbw);
            byte[] r = new byte[size];
            for (int i = 0; i < size; i++) r[i] = (byte) ((addr + i * 13 + 7) & 0xff); // FD07 地址确定性数据
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

    /** 桩 Link：一台 ROM 设备 + 一台普通 QCY 设备。M3 扩展：armFlash/rebootDevice 可配置。 */
    static class FakeLink implements H3sUsbBridge.Link {
        final FakeRomDevice rom = new FakeRomDevice();
        H3sUsbBridge.Callbacks cb;
        boolean opened;
        int enterRequests;
        byte[] capability = new byte[]{(byte)0xff,(byte)0xff,(byte)0xff,(byte)0xff,(byte)0xff,(byte)0xff,(byte)0xff,(byte)0xff};
        String armJson; int armCalls; boolean armOk = true;                // M3 armed 协商记录
        int rebootCalls; boolean rebootOk = true;
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
        public boolean armFlash(String planJson) { armCalls++; armJson = planJson; return armOk; }
        public boolean rebootDevice() { rebootCalls++; return rebootOk; }
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

    /** 目标镜像读入桩（M3 planRestore）。 */
    static class Load implements H3sUsbBridge.FileLoader {
        final java.util.Map<String, byte[]> files = new java.util.HashMap<String, byte[]>();
        public byte[] load(String name) { return files.get(name); }
    }

    /** 时钟偏移桥（nonce 过期负例）。 */
    static final class Shifted extends H3sUsbBridge {
        long shift = 0;
        Shifted(H3sUsbBridge.Link link, H3sUsbBridge.ResultSink sink, H3sUsbBridge.FileSaver saver,
                byte[] loader, String sha, FileLoader fl) {
            super(link, sink, null, saver, loader, sha, true, fl);
        }
        @Override protected long now() { return super.now() + shift; }
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

        // 9. M3 恢复全序（flashWritable=true）：计划→确认 nonce→armed→擦写→reboot 收尾
        Events ev9 = new Events();
        Load load9 = new Load();
        FakeLink link9 = new FakeLink();
        H3sUsbBridge w = new H3sUsbBridge(link9, ev9, null, save, fakeLoader, fakeSha, true, load9);
        new JSONObject(w.open("/dev/rom"));
        check("loadLoader 接受(M3)", new JSONObject(w.loadLoader()).getBoolean("ok"));
        byte[] target = link9.rom.flash.clone();
        target[0x1F8000] ^= 0x5a;                                    // 扇区 0x1F8000 变更
        target[0x200] ^= 0x33;                                       // 扇区 0x0 变更
        load9.files.put("target.bin", target);
        check("计划生成接受", new JSONObject(w.planRestore("target.bin")).getBoolean("ok"));
        JSONObject plan9 = ev9.last("usb_restore_plan");
        check("计划事件 ok", plan9 != null && plan9.getBoolean("ok"));
        JSONArray sectors9 = plan9.optJSONArray("sectors");
        check("计划扇区=2", sectors9 != null && sectors9.length() == 2);
        String nonce9 = plan9.getString("nonce");
        check("计划带目标 SHA", plan9.getString("targetSha256").equals(H3sUsbBridge.sha256(target)));
        check("错误 nonce 拒绝", !new JSONObject(w.confirmRestore("wrong")).getBoolean("ok"));
        check("确认接受", new JSONObject(w.confirmRestore(nonce9)).getBoolean("ok"));
        check("armFlash 恰一次", 1 == link9.armCalls);
        JSONObject sent9 = new JSONObject(link9.armJson);
        check("arm 消息 sectors", sent9.optJSONArray("sectors") != null && sent9.optJSONArray("sectors").length() == 2);
        check("arm 消息 target_sha256", sent9.getString("target_sha256").equals(plan9.getString("targetSha256")));
        check("arm 消息 nonce", nonce9.equals(sent9.getString("nonce")));
        check("armed 事件", ev9.last("usb_restore_armed") != null && ev9.last("usb_restore_armed").getBoolean("ok"));
        check("nonce 复用拒绝", !new JSONObject(w.confirmRestore(nonce9)).getBoolean("ok"));
        check("擦写接受", new JSONObject(w.restoreFlash()).getBoolean("ok"));
        JSONObject done9 = ev9.last("usb_restore");
        check("擦写完成事件", done9 != null && done9.getBoolean("ok"));
        check("设备 flash==目标", java.util.Arrays.equals(link9.rom.flash, target));
        check("擦除恰 2 扇区", 2 == link9.rom.eraseCount);
        check("编程恰 16 块", 16 == link9.rom.programCount);
        check("SHA 互证", H3sUsbBridge.sha256(target).equals(done9.getString("sha256")));
        check("reboot 收尾接受", new JSONObject(w.rebootDevice()).getBoolean("ok"));
        check("reboot 事件 ok", ev9.last("usb_restore_reboot").getBoolean("ok"));
        check("reboot 后会话关闭", !link9.opened && 1 == link9.rebootCalls);
        check("收尾后再 reboot 拒绝", !new JSONObject(w.rebootDevice()).getBoolean("ok"));
        check("收尾后恢复态清空", !new JSONObject(w.restoreFlash()).getBoolean("ok"));

        // 10. 诚实失败与红线负例
        Events ev10 = new Events();
        Load load10 = new Load();
        FakeLink link10 = new FakeLink();
        link10.armOk = false;                                        // armFlash()==false（真机 Link default 同形）
        H3sUsbBridge w2 = new H3sUsbBridge(link10, ev10, null, save, fakeLoader, fakeSha, true, load10);
        new JSONObject(w2.open("/dev/rom"));
        new JSONObject(w2.loadLoader());
        byte[] target10 = link10.rom.flash.clone();
        target10[0x1000] ^= 0x11;
        load10.files.put("target.bin", target10);
        new JSONObject(w2.planRestore("target.bin"));
        String nonce10 = ev10.last("usb_restore_plan").getString("nonce");
        check("arm 失败路径确认接受（异步事件判）", new JSONObject(w2.confirmRestore(nonce10)).getBoolean("ok"));
        JSONObject fail10 = ev10.last("usb_restore");
        check("armFlash false 诚实失败", fail10 != null && !fail10.getBoolean("ok")
            && fail10.getString("error").contains("官方恢复工具"));
        check("失败后计划作废可重计划", new JSONObject(w2.planRestore("target.bin")).getBoolean("ok"));
        check("目标缺名同步拒绝", !new JSONObject(w2.planRestore("a/b")).getBoolean("ok"));
        load10.files.remove("target.bin");
        new JSONObject(w2.planRestore("target.bin"));
        // 目标缺失 → 事件失败（文件不存在）
        JSONObject miss10 = ev10.last("usb_restore");
        check("目标读入失败事件", miss10 != null && !miss10.getBoolean("ok"));

        // 11. 只读形态（生产红线）：默认构造 flashWritable=false
        Events ev11 = new Events();
        FakeLink link11 = new FakeLink();
        H3sUsbBridge ro = new H3sUsbBridge(link11, ev11, null, save, fakeLoader, fakeSha);
        new JSONObject(ro.open("/dev/rom"));
        new JSONObject(ro.loadLoader());
        check("只读形态 planRestore 拒绝", !new JSONObject(ro.planRestore("target.bin")).getBoolean("ok"));
        check("只读形态 restoreFlash 拒绝", !new JSONObject(ro.restoreFlash()).getBoolean("ok"));
        check("只读形态 reboot 拒绝", !new JSONObject(ro.rebootDevice()).getBoolean("ok"));

        // 12. 错误中止保持 armed（擦除失效 → 擦除验证失败 → 不 disarm）
        Events ev12 = new Events();
        Load load12 = new Load();
        FakeLink link12 = new FakeLink();
        link12.rom.breakErase = true;
        H3sUsbBridge w3 = new H3sUsbBridge(link12, ev12, null, save, fakeLoader, fakeSha, true, load12);
        new JSONObject(w3.open("/dev/rom"));
        new JSONObject(w3.loadLoader());
        byte[] target12 = link12.rom.flash.clone();
        target12[0x1E8000] ^= 0x77;
        load12.files.put("target.bin", target12);
        new JSONObject(w3.planRestore("target.bin"));
        check("armed 接受(负例场景)", new JSONObject(w3.confirmRestore(ev12.last("usb_restore_plan").getString("nonce"))).getBoolean("ok"));
        check("擦写接受(负例场景)", new JSONObject(w3.restoreFlash()).getBoolean("ok"));
        JSONObject fail12 = ev12.last("usb_restore");
        check("擦除验证失败可见", fail12 != null && !fail12.getBoolean("ok")
            && fail12.getString("error").contains("擦除验证失败"));
        check("保持 armed（取消被拒）", !new JSONObject(w3.cancelRestore()).getBoolean("ok"));
        check("零编程发生", 0 == link12.rom.programCount);
        check("无会话 cancelRestore 拒绝", !new JSONObject(ro.cancelRestore()).getBoolean("ok"));

        // 13. nonce 过期（2min 窗口，时钟注入）
        Events ev13 = new Events();
        Load load13 = new Load();
        FakeLink link13 = new FakeLink();
        Shifted w4 = new Shifted(link13, ev13, save, fakeLoader, fakeSha, load13);
        new JSONObject(w4.open("/dev/rom"));
        new JSONObject(w4.loadLoader());
        byte[] target13 = link13.rom.flash.clone();
        target13[0x180000] ^= 0x99;
        load13.files.put("target.bin", target13);
        new JSONObject(w4.planRestore("target.bin"));
        w4.shift = H3sUsbBridge.RESTORE_MAX_AGE_MS + 1000;           // 计划后拨快 2min+1s
        String nonce13 = ev13.last("usb_restore_plan").getString("nonce");
        check("过期确认拒绝", !new JSONObject(w4.confirmRestore(nonce13)).getBoolean("ok"));
        check("过期事件作废", ev13.last("usb_restore") != null && !ev13.last("usb_restore").getBoolean("ok"));
        w4.shift = 0;
        check("作废后可重计划", new JSONObject(w4.planRestore("target.bin")).getBoolean("ok"));
        check("PREPARED 可取消", new JSONObject(w4.cancelRestore()).getBoolean("ok"));

        if (failures > 0) { System.out.println("FAILED: " + failures); System.exit(1); }
        System.out.println("H3sUsbBridgeTest passed: bare 形态、open/INQUIRY 身份、loader 相位门+摘要门（46×FB06+FB08）、"
            + "窗读/备份双读落盘、enterRom 一次性+能力门、ROM 身份短路、拔出复位、M3 恢复全序（计划→nonce 确认→"
            + "armed→逐扇区擦写→SHA 互证→reboot 收尾）、armFlash 诚实失败、只读红线、错误中止保持 armed、nonce 过期。桩设备=协议形状，链路未验证。");
    }
}
