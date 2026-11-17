package cn.felix021.h3squery;

/**
 * R4「App 写配置 + 固件读配置」命令组帧（docs/r4-command-hook.md，路 AQ）。
 *
 * 固件侧离线候选（--r4-keyhook，UNTESTED 不刷机）把 RCSP 自定义命令注册表 cmdid 0x30 项的
 * 空 setter 字（0x060a9c7c，原值 0——写路径今日为 no-op）填为 keystore_hook 载荷地址；载荷
 * 直调 VM get/set_config 分派器（0x06000f88/0x06001f12，路 AB 函数级仿真实证 key 30 读写
 * 全链）读写 key 30。HAL4 候选（--hearing-aid-l4-eq）把载荷扩展为双 key。命令形状：
 *
 *   写：FF/0x30 data = [30, v0..v7]（data_len=9）→ set_config(30, data+1, 8)
 *       回执载荷 `30 01 status`（status 0=成功，1=set_config 返回非 0）
 *   读：FF/0x30 data = [30]（data_len=1）→ get_config(30, resp+2, 8)
 *       回执载荷 `30 08 v0..v7`；key 无记录时固件静默不回（n<1 分发器跳过响应）
 *   写：FF/0x30 data = [31, v0..v87]（data_len=89，路 HAL4）→ set_config(31, data+1, 88)
 *       回执同 `30 01 status`；key 31 = 助听 EQ 十段 88B 版本化协议（encodeEqValue）
 *   读：FF/0x30 data = [31]（data_len=1，路 HAL4）→ get_config(31, resp+2, 88)
 *       回执 `30 58 v0..v87`（90B，分发器 128B 响应缓冲装得下）
 *
 * key 30 属 1..99 的 54 个空 key（27..36 整段空，路 K），VM 后端无需注册；8 字节值对本层
 * 不透明（真机语义=写入的配置内容，R4 阶段只验证「写→读回往返一致」）。
 *
 * 帧形状与 0x17/0x22/0x1d/0x2b 同族（docs/cmd-0x17-0x1d-layout.md §1）：
 * FE DC BA | C0 | FF | len_BE16 | SN | cmdid | data_len | data | EF。
 * 纯离线组帧，往返+边界已单测；真机下发未验证（红线：不连真机、不刷机）。
 */
public final class KeyStoreFrame {
    /** 注册表命令号：cmdid 0x30 项（只读查询 getter 0x06026060 保持原样，写槽为 R4 载荷）。 */
    public static final int CMD_ID = 0x30;
    /** VM key 号：路 K/AB 钉死的空 key（无需固件注册）。 */
    public static final int KEY_ID = 30;
    /** 值长度：路 AB 读写往返实证的 8 字节。 */
    public static final int VALUE_LEN = 8;
    /** 写形状 data_len = key(1B) + value(8B)。 */
    public static final int WRITE_LEN = 1 + VALUE_LEN;
    /** 读形状 data_len = key(1B)。 */
    public static final int READ_LEN = 1;

    private KeyStoreFrame() {}

    private static void check(boolean ok, String msg) {
        if (!ok) throw new IllegalArgumentException(msg);
    }

    /** 8 字节值数组长度/非空校验。 */
    public static void validateValue(byte[] value) {
        check(value != null, "value 不能为 null");
        check(value.length == VALUE_LEN, "value 长度必须为 " + VALUE_LEN + " 字节: " + (value == null ? -1 : value.length));
    }

    // ---------- L3 MPO 值协议（docs/hearing-aid-l3-mpo.md，与固件 apply_mpo.S 对齐） ----------

    /** 协议魔数 "HA"。 */
    public static final byte MPO_MAGIC_H = 0x48, MPO_MAGIC_A = 0x41;
    /** 协议版本 1（保留字节必须为 0）。 */
    public static final byte MPO_VERSION = 1;
    /** MPO 取值范围（数字域 dB 刻度，与固件位型边界一致；UI 滑杆范围同源）。 */
    public static final float MPO_MIN_DB = 0.3f, MPO_MAX_DB = 119.9f;

    /** 把全带 MPO 值编成 8B key 30 值：{ 'H','A', ver=1, rsv=0, float32 LE }。
     *  非有限值或超出 (0.3, 119.9) 拒绝——固件侧 apply_mpo.S 的位型门
     *  （0x3E99999A < bits < 0x42F00000）在 App 侧先挡一道。 */
    public static byte[] encodeMpoValue(float mpoDb) {
        check(Float.isFinite(mpoDb), "MPO 必须是有限数: " + mpoDb);
        check(mpoDb > MPO_MIN_DB && mpoDb < MPO_MAX_DB,
                "MPO 超出范围 (" + MPO_MIN_DB + ", " + MPO_MAX_DB + "): " + mpoDb);
        byte[] value = new byte[VALUE_LEN];
        value[0] = MPO_MAGIC_H;
        value[1] = MPO_MAGIC_A;
        value[2] = MPO_VERSION;
        value[3] = 0;
        int bits = Float.floatToIntBits(mpoDb);
        value[4] = (byte) bits;
        value[5] = (byte) (bits >>> 8);
        value[6] = (byte) (bits >>> 16);
        value[7] = (byte) (bits >>> 24);
        return value;
    }

    /** 从 8B 值解出 MPO（魔数/版本/保留/范围不符返回 null）。 */
    public static Float decodeMpoValue(byte[] value) {
        validateValue(value);
        if (value[0] != MPO_MAGIC_H || value[1] != MPO_MAGIC_A
                || value[2] != MPO_VERSION || value[3] != 0) return null;
        int bits = ((value[4] & 0xff)) | ((value[5] & 0xff) << 8)
                | ((value[6] & 0xff) << 16) | ((value[7] & 0xff) << 24);
        float mpo = Float.intBitsToFloat(bits);
        if (!Float.isFinite(mpo) || mpo <= MPO_MIN_DB || mpo >= MPO_MAX_DB) return null;
        return mpo;
    }

    // ---------- L4 EQ 十段值协议（docs/hearing-aid-l4-eq.md，与固件 apply_eq.S 对齐） ----------

    /** EQ 用 VM key 号：路 K 空 key 段（27..36），与 key 30 同族。 */
    public static final int EQ_KEY_ID = 31;
    /** EQ 值长度：8B 头 + 10 段 × {u16 freq, u16 pad, float32 gain}。 */
    public static final int EQ_VALUE_LEN = 8 + 10 * 8;
    /** EQ 写形状 data_len = key(1B) + value(88B)。 */
    public static final int EQ_WRITE_LEN = 1 + EQ_VALUE_LEN;
    /** 协议版本 2（魔数同 "HA"，保留字节必须为 0）。 */
    public static final byte EQ_VERSION = 2;
    /** 段数固定十段（固件整包先验要求 == 10）。 */
    public static final int EQ_SEG_COUNT = 10;
    /** 段频点门（官方 Eq1Eff blob 观测域 [1, 21599]Hz，非算法验证上界）。 */
    public static final int EQ_FREQ_MIN_HZ = 1, EQ_FREQ_MAX_HZ = 21599;
    /** 段增益幅值门 ±40dB（固件按位型幅值 |bits|<=40.0f 同界排 NaN/Inf）。 */
    public static final float EQ_GAIN_MAX_DB = 40.0f;

    /** 单段参数（freq Hz + gain dB；iir=band_pass/q=0.7 由固件模板烘定）。 */
    public static final class EqSeg {
        public final int freqHz;
        public final float gainDb;

        public EqSeg(int freqHz, float gainDb) {
            this.freqHz = freqHz;
            this.gainDb = gainDb;
        }
    }

    private static void putFloat(byte[] buf, int off, float v) {
        int bits = Float.floatToIntBits(v);
        buf[off] = (byte) bits;
        buf[off + 1] = (byte) (bits >>> 8);
        buf[off + 2] = (byte) (bits >>> 16);
        buf[off + 3] = (byte) (bits >>> 24);
    }

    private static float getFloat(byte[] buf, int off) {
        return Float.intBitsToFloat(((buf[off] & 0xff)) | ((buf[off + 1] & 0xff) << 8)
                | ((buf[off + 2] & 0xff) << 16) | ((buf[off + 3] & 0xff) << 24));
    }

    /**
     * 把十段曲线编成 88B key 31 值：{'H','A', ver=2, rsv=0, seg_count=10, pad[3],
     * 10 × {u16 freq LE, u16 pad=0, float32 gain LE}}。任一段频点出
     * [1, 21599]Hz、增益非有限或 |gain|>=40dB、段数≠10 都拒绝——固件侧
     * apply_eq.S 整包先验同门（App 侧先挡一道）。
     */
    public static byte[] encodeEqValue(EqSeg[] segs) {
        check(segs != null, "segs 不能为 null");
        check(segs.length == EQ_SEG_COUNT, "段数必须为 " + EQ_SEG_COUNT + ": " + (segs == null ? -1 : segs.length));
        for (EqSeg s : segs) {
            check(s != null, "段不能为 null");
            check(s.freqHz >= EQ_FREQ_MIN_HZ && s.freqHz <= EQ_FREQ_MAX_HZ,
                    "freq 超出 [" + EQ_FREQ_MIN_HZ + ", " + EQ_FREQ_MAX_HZ + "]Hz: " + s.freqHz);
            check(Float.isFinite(s.gainDb) && Math.abs(s.gainDb) < EQ_GAIN_MAX_DB,
                    "gain 超出 (-" + EQ_GAIN_MAX_DB + ", " + EQ_GAIN_MAX_DB + ")dB: " + s.gainDb);
        }
        byte[] value = new byte[EQ_VALUE_LEN];
        value[0] = MPO_MAGIC_H;
        value[1] = MPO_MAGIC_A;
        value[2] = EQ_VERSION;
        value[3] = 0;
        value[4] = (byte) EQ_SEG_COUNT;
        for (int i = 0; i < EQ_SEG_COUNT; i++) {
            int off = 8 + i * 8;
            value[off] = (byte) segs[i].freqHz;
            value[off + 1] = (byte) (segs[i].freqHz >>> 8);
            value[off + 2] = 0;                     // pad（固件忽略）
            value[off + 3] = 0;
            putFloat(value, off + 4, segs[i].gainDb);
        }
        return value;
    }

    /** 从 88B 值解出十段（头/段数/每段门任一不符返回 null；pad 字节不校验）。 */
    public static EqSeg[] decodeEqValue(byte[] value) {
        check(value != null, "value 不能为 null");
        check(value.length == EQ_VALUE_LEN,
                "EQ 值长度必须为 " + EQ_VALUE_LEN + " 字节: " + (value == null ? -1 : value.length));
        if (value[0] != MPO_MAGIC_H || value[1] != MPO_MAGIC_A
                || value[2] != EQ_VERSION || value[3] != 0
                || (value[4] & 0xff) != EQ_SEG_COUNT) return null;
        EqSeg[] segs = new EqSeg[EQ_SEG_COUNT];
        for (int i = 0; i < EQ_SEG_COUNT; i++) {
            int off = 8 + i * 8;
            int freq = (value[off] & 0xff) | ((value[off + 1] & 0xff) << 8);
            float gain = getFloat(value, off + 4);
            if (freq < EQ_FREQ_MIN_HZ || freq > EQ_FREQ_MAX_HZ
                    || !Float.isFinite(gain) || Math.abs(gain) >= EQ_GAIN_MAX_DB) return null;
            segs[i] = new EqSeg(freq, gain);
        }
        return segs;
    }

    /** EQ 写命令 data：[31, v0..v87] 共 89B（与固件 keyhook key-31 分支对齐）。 */
    public static byte[] buildEqWriteData(EqSeg[] segs) {
        byte[] value = encodeEqValue(segs);
        byte[] data = new byte[EQ_WRITE_LEN];
        data[0] = (byte) EQ_KEY_ID;
        System.arraycopy(value, 0, data, 1, EQ_VALUE_LEN);
        return data;
    }

    /** EQ 写命令完整帧：FE DC BA | C0 | FF | len_BE16 | SN | 30 | 59 | key31+88B 值 | EF。 */
    public static byte[] buildEqWriteCommand(int sn, EqSeg[] segs) {
        return H3SBridge.buildCustomCommand(sn, CMD_ID, buildEqWriteData(segs));
    }

    /** 解析 EQ 读回响应载荷：期望 `30 58 v0..v87`，返回 88B 值。 */
    public static byte[] parseEqReadPayload(byte[] payload) {
        check(payload != null && payload.length == 2 + EQ_VALUE_LEN,
                "0x30 EQ 读回载荷长度应为 " + (2 + EQ_VALUE_LEN) + ": " + (payload == null ? -1 : payload.length));
        check((payload[0] & 0xff) == CMD_ID, "EQ 读回载荷 cmdid 不是 0x30");
        check((payload[1] & 0xff) == EQ_VALUE_LEN, "EQ 读回载荷 n 不是 88");
        byte[] value = new byte[EQ_VALUE_LEN];
        System.arraycopy(payload, 2, value, 0, EQ_VALUE_LEN);
        return value;
    }

    // ---------- 命令组帧 ----------

    /** 写命令 data：[key, v0..v7] 共 9B。 */
    public static byte[] buildWriteData(byte[] value) {
        validateValue(value);
        byte[] data = new byte[1 + VALUE_LEN];
        data[0] = (byte) KEY_ID;
        System.arraycopy(value, 0, data, 1, VALUE_LEN);
        return data;
    }

    /** 写命令完整帧：FE DC BA | C0 | FF | len_BE16 | SN | 30 | 09 | key+8B 值 | EF。 */
    public static byte[] buildWriteCommand(int sn, byte[] value) {
        return H3SBridge.buildCustomCommand(sn, CMD_ID, buildWriteData(value));
    }

    /** 读命令 data：[key] 共 1B（读也走写形态帧——载荷按 data_len 分路，见类注释）。 */
    public static byte[] buildReadData() {
        return new byte[]{(byte) KEY_ID};
    }

    /**
     * 读命令完整帧：与写命令同族同 opcode/cmdid，仅 data_len=1：
     * FE DC BA | C0 | FF | len_BE16(5) | SN | 30 | 01 | 1E | EF。固件载荷按 data_len==1
     * 分路到 get_config(30, resp+2, 8)，回执载荷 `30 08 v0..v7`。
     * 注意：不走 `FE 01 30` 只读查询族——那条路是分发器 getter 路径（cmdid 0x30 的
     * getter 0x06026060 保持原样），不经过 R4 载荷。
     */
    public static byte[] buildReadCommand(int sn) {
        return H3SBridge.buildCustomCommand(sn, CMD_ID, buildReadData());
    }

    // ---------- 反解析（往返自检 + 白名单形状判定用） ----------

    /** 严格校验头/opcode/cmdid/长度/尾后抽出写命令的 8 字节值。 */
    public static byte[] parseWriteCommand(byte[] frame) {
        byte[] head = parseFrameHead(frame, WRITE_LEN);
        byte[] value = new byte[VALUE_LEN];
        System.arraycopy(head, 1, value, 0, VALUE_LEN);
        return value;
    }

    /** 严格校验头/opcode/cmdid/长度/尾后确认读命令形状（返回 key 字节）。 */
    public static int parseReadCommand(byte[] frame) {
        byte[] head = parseFrameHead(frame, READ_LEN);
        return head[0] & 0xff;
    }

    /** 帧头/cmdid/data_len 通用校验：返回 data（长度 dataLen）。 */
    private static byte[] parseFrameHead(byte[] frame, int expectLen) {
        check(frame != null && frame.length >= 12, "0x30 帧太短");
        check((frame[0] & 0xff) == 0xfe && (frame[1] & 0xff) == 0xdc && (frame[2] & 0xff) == 0xba, "帧头不是 FE DC BA");
        check((frame[3] & 0xff) == EqFrame.FLAGS_CMD, "flags 不是 C0");
        check((frame[4] & 0xff) == EqFrame.OPCODE_CUSTOM, "opcode 不是 0xFF");
        int bodyLen = ((frame[5] & 0xff) << 8) | (frame[6] & 0xff);
        check(frame.length == 8 + bodyLen, "len_BE16 与帧长度不符");
        check((frame[frame.length - 1] & 0xff) == 0xef, "帧尾不是 EF");
        check((frame[8] & 0xff) == CMD_ID, "cmdid 不是 0x30");
        int dataLen = frame[9] & 0xff;
        check(dataLen == expectLen && bodyLen == 3 + dataLen,
                "0x30 data_len 应为 " + expectLen + ": " + dataLen);
        byte[] data = new byte[dataLen];
        System.arraycopy(frame, 10, data, 0, dataLen);
        check((data[0] & 0xff) == KEY_ID, "data[0] 不是 key 30");
        return data;
    }

    // ---------- 读回响应解析 ----------

    /**
     * 解析设备读回响应的载荷（分发器包装格式 cmdid|n|data，即响应帧 status+SN 之后的
     * payloadHex 字节）：期望 `30 08 v0..v7`，返回 8 字节值。
     */
    public static byte[] parseReadPayload(byte[] payload) {
        check(payload != null && payload.length == 2 + VALUE_LEN,
                "0x30 读回载荷长度应为 " + (2 + VALUE_LEN) + ": " + (payload == null ? -1 : payload.length));
        check((payload[0] & 0xff) == CMD_ID, "读回载荷 cmdid 不是 0x30");
        check((payload[1] & 0xff) == VALUE_LEN, "读回载荷 n 不是 8");
        byte[] value = new byte[VALUE_LEN];
        System.arraycopy(payload, 2, value, 0, VALUE_LEN);
        return value;
    }

    /**
     * 解析写回执载荷：期望 `30 01 status`（status 0=成功）。返回 status。
     * 形状即固件 keystore_hook 载荷的设计回执（docs/r4-command-hook.md），真机帧以实机为准。
     */
    public static int parseWriteAck(byte[] payload) {
        check(payload != null && payload.length == 3, "0x30 写回执载荷长度应为 3: " + (payload == null ? -1 : payload.length));
        check((payload[0] & 0xff) == CMD_ID, "写回执载荷 cmdid 不是 0x30");
        check((payload[1] & 0xff) == 1, "写回执载荷 n 不是 1");
        return payload[2] & 0xff;
    }
}
