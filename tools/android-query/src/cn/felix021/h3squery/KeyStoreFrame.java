package cn.felix021.h3squery;

/**
 * R4「App 写配置 + 固件读配置」命令组帧（docs/r4-command-hook.md，路 AQ）。
 *
 * 固件侧离线候选（--r4-keyhook，UNTESTED 不刷机）把 RCSP 自定义命令注册表 cmdid 0x30 项的
 * 空 setter 字（0x060a9c7c，原值 0——写路径今日为 no-op）填为 keystore_hook 载荷地址；载荷
 * 直调 VM get/set_config 分派器（0x06000f88/0x06001f12，路 AB 函数级仿真实证 key 30 读写
 * 全链）读写 key 30。两条命令形状（与载荷 .equ 常量一一对应）：
 *
 *   写：FF/0x30 data = [30, v0..v7]（data_len=9）→ set_config(30, data+1, 8)
 *       回执载荷 `30 01 status`（status 0=成功，1=set_config 返回非 0）
 *   读：FF/0x30 data = [30]（data_len=1）→ get_config(30, resp+2, 8)
 *       回执载荷 `30 08 v0..v7`；key 无记录时固件静默不回（n<1 分发器跳过响应）
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
