import cn.felix021.h3squery.UsbScsi;
import java.util.ArrayList;
import java.util.List;

/**
 * UsbScsi 纯 JVM 单测：CBW/CDB/CSW 字节形状与 tools/usb-recovery/protocol.py 逐字节对拍
 * （期望值由 Python struct/binascii.crc_hqx 现算钉死），加白名单/边界/坏 CSW 错误路径。
 */
public class UsbScsiTest {
    static int failures = 0;

    static void eq(String what, Object expect, Object actual) {
        boolean ok = expect == null ? actual == null : expect.equals(actual);
        if (!ok) { failures++; System.out.println("FAIL " + what + ": expect=" + expect + " actual=" + actual); }
        else System.out.println("ok   " + what);
    }

    /** 桩通道：按序吐预置的 IN 数据；OUT 全收。 */
    static class FakeDev implements UsbScsi.Channel {
        final List<byte[]> in = new ArrayList<byte[]>();
        final List<byte[]> out = new ArrayList<byte[]>();
        void expect(byte[] data) { in.add(data); }
        public int bulkOut(byte[] data) { out.add(data); return data.length; }
        public byte[] bulkIn(int max) {
            if (in.isEmpty()) throw new IllegalStateException("意外的 bulkIn");
            byte[] d = in.remove(0);
            return java.util.Arrays.copyOf(d, Math.min(d.length, max));
        }
    }

    static byte[] csw(int tag) {
        return new byte[]{'U','S','B','S', (byte)tag, (byte)(tag>>8), (byte)(tag>>16), (byte)(tag>>24), 0,0,0,0, 0};
    }

    public static void main(String[] args) {
        // CRC-16/XMODEM：标准测试向量 + 与 crc_hqx 现算的 FB06 分块
        eq("crc16 向量 123456789", "31c3", String.format("%04x", UsbScsi.crc16("123456789".getBytes())));
        byte[] chunk = new byte[512];
        for (int i = 0; i < 512; i++) chunk[i] = (byte) ((i * 7 + 3) & 0xff);
        eq("crc16 512B 图案", "6b2f", String.format("%04x", UsbScsi.crc16(chunk)));

        // vendor CDB 形状（Python: opcode BE2 + args + 0xff 填充到 16）
        eq("FD07 cdb(0x80,128)", "fd07000000800080ffffffffffffffff",
            UsbScsi.hex(UsbScsi.vendorCdb(0xfd07, new byte[]{0,0,0,(byte)0x80, 0, (byte)128})));
        eq("FD05 cdb(0x1FE000,512)", "fd05001fe0000200ffffffffffffffff",
            UsbScsi.hex(UsbScsi.vendorCdb(0xfd05, new byte[]{0x00,0x1f,(byte)0xe0,0x00, 0x02, 0x00})));
        eq("FB06 cdb(0x120000,512,00,crc6b2fLE)", "fb06001200000200002f6bffffffffff",
            UsbScsi.hex(UsbScsi.vendorCdb(0xfb06, new byte[]{0x00,0x12,0x00,0x00, 0x02,0x00, 0x00, 0x2f, 0x6b})));
        eq("FB08 cdb(0x120000,0001)", "fb08001200000001ffffffffffffffff",
            UsbScsi.hex(UsbScsi.vendorCdb(0xfb08, new byte[]{0x00,0x12,0x00,0x00, 0x00,0x01})));

        // INQUIRY：CBW 逐字节（Python struct 现算）+ 数据相位 36B + CSW
        FakeDev dev = new FakeDev();
        byte[] raw = new byte[36];
        String inquiry = "BR28   UBOOT1.00        1.00";
        System.arraycopy(inquiry.getBytes(), 0, raw, 8, Math.min(28, inquiry.getBytes().length));
        dev.expect(raw); dev.expect(csw(1));
        UsbScsi scsi = new UsbScsi(dev);
        byte[] resp = scsi.inquiry();
        eq("INQUIRY 数据相位长度", 36, resp.length);
        eq("INQUIRY CBW", "55534243010000002400000080000612000000240000000000000000000000",
            UsbScsi.hex(dev.out.get(0)));
        eq("tag 自增", 1, scsi.tag());

        // FB06 OUT 数据相位：CBW flags=0、data=chunk 原样、CSW
        dev = new FakeDev();
        dev.expect(csw(1));
        scsi = new UsbScsi(dev);
        scsi.ramWrite(0x120000, chunk);
        byte[] cbw = dev.out.get(0);
        eq("FB06 CBW 长度", 31, cbw.length);
        eq("FB06 CBW 头", "55534243", UsbScsi.hex(java.util.Arrays.copyOf(cbw, 4)));
        eq("FB06 CBW size LE(512)", "00020000", UsbScsi.hex(new byte[]{cbw[8],cbw[9],cbw[10],cbw[11]}));
        eq("FB06 CBW flags OUT=0", 0, cbw[12] & 0xff);
        eq("FB06 CBW cdb_len", 16, cbw[14] & 0xff);
        eq("FB06 数据相位=chunk", java.util.Arrays.hashCode(chunk), java.util.Arrays.hashCode(dev.out.get(1)));

        // FC0A 响应 opcode 回显校验
        dev = new FakeDev();
        byte[] fc = new byte[16];
        fc[0] = (byte)0xfc; fc[1] = 0x0a; fc[2] = 0x03;
        dev.expect(fc); dev.expect(csw(1));
        scsi = new UsbScsi(dev);
        eq("FC0A 响应头", "fc0a03", UsbScsi.hex(java.util.Arrays.copyOf(scsi.onlineQuery(), 3)));

        // tag 连续自增
        dev = new FakeDev();
        byte[] echo1 = new byte[16]; echo1[0] = (byte)0xfb; echo1[1] = 0x08;
        byte[] echo2 = new byte[16]; echo2[0] = (byte)0xfc; echo2[1] = 0x0a; echo2[2] = 0x03;
        dev.expect(echo1); dev.expect(csw(1));
        dev.expect(echo2); dev.expect(csw(2));
        scsi = new UsbScsi(dev);
        scsi.loaderStart(); scsi.onlineQuery();
        eq("tag 连续", 2, scsi.tag());

        // 坏 CSW：tag 不匹配 → 异常
        dev = new FakeDev();
        dev.expect(csw(99));
        scsi = new UsbScsi(dev);
        try { scsi.loaderStart(); failures++; System.out.println("FAIL 坏 CSW 未拒绝"); }
        catch (IllegalStateException expected) { System.out.println("ok   坏 CSW 拒绝: " + expected.getMessage()); }

        // 数据相位短读 → 异常
        dev = new FakeDev();
        dev.expect(new byte[10]); dev.expect(csw(1));
        scsi = new UsbScsi(dev);
        try { scsi.readMem(0, 128); failures++; System.out.println("FAIL 短读未拒绝"); }
        catch (IllegalStateException expected) { System.out.println("ok   短读拒绝: " + expected.getMessage()); }

        // opcode 白名单：FB01（擦除）根本不放行
        try {
            UsbScsi.vendorCdb(0xfb01, new byte[4]);
            failures++; System.out.println("FAIL FB01 不该能组帧");
        } catch (IllegalArgumentException ok) { System.out.println("ok   FB01 组帧被拒"); }
        dev = new FakeDev(); dev.expect(csw(1));
        scsi = new UsbScsi(dev);
        try {
            scsi.exchange(new byte[16], 16, null); // opcode 0x0000
            failures++; System.out.println("FAIL 未知 opcode 未拒绝");
        } catch (IllegalArgumentException expected) { System.out.println("ok   未知 opcode 拒绝"); }

        // 边界：FD07 >128、FD05 未对齐
        try { new UsbScsi(new FakeDev()).readMem(0, 129); failures++; System.out.println("FAIL FD07 129 未拒"); }
        catch (IllegalArgumentException e) { System.out.println("ok   FD07 129 拒绝"); }
        try { new UsbScsi(new FakeDev()).readFlashSector(3); failures++; System.out.println("FAIL FD05 未对齐未拒"); }
        catch (IllegalArgumentException e) { System.out.println("ok   FD05 未对齐拒绝"); }

        if (failures > 0) { System.out.println("FAILED: " + failures); System.exit(1); }
        System.out.println("UsbScsiTest passed: CBW/CDB/CSW 与 Python 工具逐字节对拍、CRC16 向量、"
            + "FB06 OUT 相位、FC0A 回显、坏 CSW/短读/白名单（FB01 无编码入口）/边界拒绝。");
    }
}
