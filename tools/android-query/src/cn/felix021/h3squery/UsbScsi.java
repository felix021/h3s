package cn.felix021.h3squery;

/**
 * BR28 USB MSC 命令层（纯 JVM）：CBW/CSW 交换 + 最小命令集，移植自
 * tools/usb-recovery/protocol.py（JL uboot USB 协议，上游 MIT）的字节级形状：
 *
 *   CBW = "USBC" | tag(u32 LE) | size(u32 LE) | flags | lun=0 | cdb_len | cdb(16B)
 *   数据相位 = OUT(写 data) 或 IN(读 size 字节)
 *   CSW = "USBS" | tag(u32 LE) | residue=0(u32) | status=0
 *
 * 命令白名单（照 protocol.py / read_rom.py 的恢复读取器口径，**只含只读/装载**）：
 *   - 0x12 INQUIRY（36B，厂商 BR28 / 产品 UBOOT1.00）
 *   - 0xFB06 ROM 写 RAM（loader 512B 分块：addr4 BE + len2 BE + 00 + crc16LE）
 *   - 0xFB08 跳转 loader（固定实参 0x120000/0001）
 *   - 0xFC0A 在线查询（响应须以 fc0a03 起 = SPI NOR）
 *   - 0xFD05 读 Flash 扇区（512B，仅已装载 loader 后）
 *   - 0xFD07 读内存窗（≤128B，仅已装载 loader 后；512B 实测会断链，docs/rom-read.md）
 * 擦除/编程/复位命令（FB00/FB01/FB02/FB04/FC0C/FC12…）一律不存在编码入口——
 * 本类不提供任何 Flash 写路径（红线：候选镜像 UNTESTED 不刷机）。
 *
 * CRC = CRC-16/XMODEM（binascii.crc_hqx，poly 0x1021、初值 0、无反射）。
 *
 * 物理链路（手机 OTG 连耳机进 ROM 模式）未验证——本类只保证与 Python 工具
 * 逐字节同形（UsbScsiTest 对拍），真机行为待联调。
 */
public class UsbScsi {

    /** 通道抽象：OUT/IN 两个 bulk 端点（Android=UsbDeviceConnection.bulkTransfer，测试=桩）。 */
    public interface Channel {
        /** OUT 端点写整段数据；返回实际写出字节数。 */
        int bulkOut(byte[] data);

        /** IN 端点最多读 max 字节；返回实际读到的（允许 < max）。 */
        byte[] bulkIn(int max);
    }

    public static final int OP_INQUIRY = 0x12;
    public static final int OP_RAM_WRITE = 0xfb06;
    public static final int OP_LOADER_START = 0xfb08;
    public static final int OP_ONLINE_QUERY = 0xfc0a;
    public static final int OP_FLASH_READ = 0xfd05;
    public static final int OP_MEM_READ = 0xfd07;
    /** FD07 单次最大 128B（实测 512B 读取会把 USB 读挂掉，docs/rom-read.md）。 */
    public static final int MEM_READ_MAX = 128;
    /** Flash 扇区大小（FD05 固定按 512B 扇区读）。 */
    public static final int SECTOR = 512;
    /** RAM loader 装载地址（br28loader.bin，tools/usb-recovery 钉死）。 */
    public static final int LOADER_ADDRESS = 0x120000;

    private final Channel ch;
    private int tag = 0;

    public UsbScsi(Channel ch) {
        if (ch == null) throw new IllegalArgumentException("channel 不能为 null");
        this.ch = ch;
    }

    /** 测试钩子：当前 CBW tag（每个 exchange 自增，从 1 起）。 */
    public int tag() { return tag; }

    /** CRC-16/XMODEM（crc_hqx(data,0) 的 Java 移植）。 */
    public static int crc16(byte[] data) {
        int crc = 0;
        for (byte b : data) {
            crc ^= (b & 0xff) << 8;
            for (int i = 0; i < 8; i++)
                crc = ((crc & 0x8000) != 0) ? ((crc << 1) ^ 0x1021) : (crc << 1);
            crc &= 0xffff;
        }
        return crc;
    }

    /** 组 16B vendor CDB：opcode(2B BE) + args + 0xff 填充（protocol.py cmd() 同形）。
     *  编码入口即查白名单——擦除/编程/复位命令在这里就不存在。 */
    public static byte[] vendorCdb(int opcode, byte[] args) {
        if (!opcodeAllowed(opcode)) throw new IllegalArgumentException("opcode 不在只读/装载白名单: " + Integer.toHexString(opcode));
        if (args == null || args.length > 14) throw new IllegalArgumentException("args 长度非法: " + (args == null ? -1 : args.length));
        byte[] cdb = new byte[16];
        cdb[0] = (byte) (opcode >> 8);
        cdb[1] = (byte) opcode;
        System.arraycopy(args, 0, cdb, 2, args.length);
        java.util.Arrays.fill(cdb, 2 + args.length, 16, (byte) 0xff);
        return cdb;
    }

    /** 组 31B CBW（data==null 为 IN 方向 flags=0x80，否则 OUT 方向 flags=0x00）。 */
    public static byte[] cbw(int tag, int size, boolean dataOut, byte[] cdb) {
        if (cdb == null || cdb.length < 1 || cdb.length > 16) throw new IllegalArgumentException("cdb 长度非法: " + (cdb == null ? -1 : cdb.length));
        byte[] out = new byte[31];
        out[0] = 'U'; out[1] = 'S'; out[2] = 'B'; out[3] = 'C';
        out[4] = (byte) tag; out[5] = (byte) (tag >> 8); out[6] = (byte) (tag >> 16); out[7] = (byte) (tag >> 24);
        out[8] = (byte) size; out[9] = (byte) (size >> 8); out[10] = (byte) (size >> 16); out[11] = (byte) (size >> 24);
        out[12] = (byte) (dataOut ? 0x00 : 0x80);
        out[13] = 0;                                            // lun
        out[14] = (byte) cdb.length;
        System.arraycopy(cdb, 0, out, 15, cdb.length);          // 尾部 0 填充（不足 16B 时）
        return out;
    }

    /** opcode 白名单（只读/装载；Flash 擦写/复位不在内，见类注）。 */
    static boolean opcodeAllowed(int opcode) {
        switch (opcode) {
            case OP_INQUIRY: case OP_RAM_WRITE: case OP_LOADER_START:
            case OP_ONLINE_QUERY: case OP_FLASH_READ: case OP_MEM_READ:
                return true;
            default:
                return false;
        }
    }

    /** 一次命令交换：CBW →（数据相位）→ CSW 校验；返回数据相位字节（无数据相位返回空）。 */
    public byte[] exchange(byte[] cdb, int size, byte[] data) {
        if (cdb == null || cdb.length < 1 || cdb.length > 16) throw new IllegalArgumentException("CDB 长度非法");
        int opcode = ((cdb[0] & 0xff) << 8) | (cdb[1] & 0xff);
        if (cdb[0] != OP_INQUIRY && !opcodeAllowed(opcode))
            throw new IllegalArgumentException("opcode 不在只读/装载白名单: " + Integer.toHexString(opcode));
        if (data != null && size != 0 && size != data.length) throw new IllegalArgumentException("OUT 数据长度不一致");
        int dataSize = data != null ? data.length : size;
        tag += 1;
        byte[] cmd = cbw(tag, dataSize, data != null, cdb);
        if (ch.bulkOut(cmd) != 31) throw new IllegalStateException("CBW 写出不足 31B");
        byte[] result = new byte[0];
        if (dataSize > 0) {
            if (data != null) {
                if (ch.bulkOut(data) != data.length) throw new IllegalStateException("数据相位写出不足");
            } else {
                result = ch.bulkIn(size);
                if (result.length != size) throw new IllegalStateException("数据相位短读: " + result.length + "/" + size);
            }
        }
        byte[] csw = ch.bulkIn(13);
        if (csw.length != 13) throw new IllegalStateException("CSW 长度非法: " + hex(csw));
        if (csw[0] != 'U' || csw[1] != 'S' || csw[2] != 'B' || csw[3] != 'S'
            || csw[4] != (byte) tag || csw[5] != (byte) (tag >> 8) || csw[6] != (byte) (tag >> 16) || csw[7] != (byte) (tag >> 24)
            || csw[8] != 0 || csw[9] != 0 || csw[10] != 0 || csw[11] != 0 || csw[12] != 0)
            throw new IllegalStateException("CSW 校验失败: " + hex(csw));
        // 普通 16B 响应回显 opcode 双字节（FD05/FD07 返回原始数据，protocol.py cmd() 同规则）
        if (data == null && size == 16 && opcode != OP_FLASH_READ && opcode != OP_MEM_READ) {
            if (result.length < 2 || result[0] != cdb[0] || result[1] != cdb[1])
                throw new IllegalStateException("响应 opcode 回显不符: " + hex(result));
        }
        return result;
    }

    /** vendor 命令：opcode(2B BE)+args → 16B CDB（0xff 填充）→ exchange。 */
    public byte[] cmd(int opcode, byte[] args, int size, byte[] data) {
        return exchange(vendorCdb(opcode, args), size, data);
    }

    private static byte[] addrLen(int address, int length) {
        return new byte[]{(byte) (address >> 24), (byte) (address >> 16), (byte) (address >> 8), (byte) address,
            (byte) (length >> 8), (byte) length};
    }

    /** 标准 SCSI INQUIRY（36B）。CBW 记 cdb_len=6、CDB 区 0 填充到 16（protocol.py 同形）。 */
    public byte[] inquiry() {
        byte[] cdb = new byte[6];
        cdb[0] = OP_INQUIRY; cdb[4] = 36;
        return exchange(cdb, 36, null);
    }

    /** FB06：向 ROM 写一段 RAM（loader 512B 分块；args=addr4+len2+00+crc16LE）。 */
    public byte[] ramWrite(int address, byte[] chunk) {
        if (chunk == null || chunk.length < 1 || chunk.length > SECTOR) throw new IllegalArgumentException("RAM 块长度非法: " + (chunk == null ? -1 : chunk.length));
        int crc = crc16(chunk);
        byte[] args = new byte[]{(byte) (address >> 24), (byte) (address >> 16), (byte) (address >> 8), (byte) address,
            (byte) (chunk.length >> 8), (byte) chunk.length, 0, (byte) crc, (byte) (crc >> 8)};
        return cmd(OP_RAM_WRITE, args, 0, chunk);
    }

    /** FB08：跳转已装载的 loader（固定实参：入口 0x120000、参数 1=SPI NOR）。 */
    public byte[] loaderStart() {
        byte[] args = new byte[]{(byte) (LOADER_ADDRESS >> 24), (byte) (LOADER_ADDRESS >> 16), (byte) (LOADER_ADDRESS >> 8), (byte) LOADER_ADDRESS, 0, 1};
        return cmd(OP_LOADER_START, args, 16, null);
    }

    /** FC0A：loader 在线查询（响应须以 fc0a03 起 = SPI NOR，调用方校验）。 */
    public byte[] onlineQuery() {
        return cmd(OP_ONLINE_QUERY, new byte[0], 16, null);
    }

    /** FD07：读内存窗（仅 loader 就绪后调用；≤128B，512B 实测断链）。 */
    public byte[] readMem(int address, int length) {
        if (length < 1 || length > MEM_READ_MAX) throw new IllegalArgumentException("FD07 长度须 1..128: " + length);
        if (address < 0 || address + length < 0) throw new IllegalArgumentException("FD07 地址非法: " + address);
        return cmd(OP_MEM_READ, addrLen(address, length), length, null);
    }

    /** FD05：读 Flash 扇区（仅 loader 就绪后调用；固定 512B）。 */
    public byte[] readFlashSector(int address) {
        if (address < 0 || address % SECTOR != 0) throw new IllegalArgumentException("FD05 地址须 512 对齐: " + address);
        return cmd(OP_FLASH_READ, addrLen(address, SECTOR), SECTOR, null);
    }

    public static String hex(byte[] data) {
        StringBuilder s = new StringBuilder(data.length * 2);
        for (byte b : data) s.append(String.format("%02x", b & 0xff));
        return s.toString();
    }
}
