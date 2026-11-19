package cn.felix021.h3squery;

/**
 * 「更多功能」命令组帧（docs/morefunc-protocol.md，路 MOREFUNC / sim-morefunc）。
 *
 * 覆盖官方控制面板（data/controlpanel_qcyh3s_16764.json）里除 EQ（0x22，eqfix 路）/
 * ANC（0x17）/按键自定义（批量同步，KeyRemapFrame）外的全部可下发功能，帧形状全部来自
 * 固件注册表反解（表 A=[0x060a9b78,0x060a9d1c) 15×28B、表 C=[0x060a9b24,0x060a9b78)
 * 3×28B，docs/r4-command-hook.md §1 + 本路逐 handler 反汇编）：
 *
 * FF 自定义命令（opcode 0xFF，body=SN|cmdid|data_len|data，setter ABI 只读 data）：
 *   - 0x14 定时关机：data=[分钟 LE16, 00, 00]（len 4；FFFF=不关机、0=立即关机——本类
 *     拒绝 0，立即关机不当普通设置发）。getter 回 4B=[设定分钟 LE16, 剩余分钟 LE16]。
 *     setter 返回 0=静默无回执，成功确认走回读（docs/protocol.md「电源命令的陷阱」+反汇编）。
 *   - 0x1f 断连关机：data=[分钟 LE16]（len 2；FFFF=不关机）。getter 回 2B。默认 5 分钟
 *     （恢复默认 handler 写 0x0005）。同 0x14：静默 setter，回读确认。
 *   - 0x19 提示音语言：data=4B key，面板两档「JL00」(4a4c3030)=英文 /「JL01」(4a4c3031)=
 *     中文（固件表 0x609e284 两项 BE u32 键匹配）。getter 回 4B 当前 key。
 *   - 0x23 LDAC：data=[01/00]（len 1，面板 cmd 串 "230101"/"230100"）。开=会重启并
 *     关闭双设备（清 QCY+34/config8）与空间音效（清 QCY+33/config14）。getter 回 1B。
 *   - 0x24 双设备连接：data=[01/00]（len 1，面板 "240101"/"240100"）。开=会重启并关 LDAC。
 *     getter 回 1B。连接数上限固件侧编译死 2（0x1281fa 2×40B 链路记录，docs/morefunc-protocol.md §5）。
 *   - （已删 0x03 清除配对：旧 [推断] 归属错误——eqproto 字节对照证明 QCY 原生清配对走
 *     0xC5/02，FF/03 不再构造/放行。）
 *
 * 批量同步（opcode 0xC0 写 / 0xC1 读，body=SN 后接 item 序列，item={u8 len(含 cmdid),
 * u8 cmdid, data[len-1]}，docs/morefunc-protocol.md §3）：
 *   - 表 C cmdid 1 设备名：data=名字字节（≤32B，固件 NUL 补齐；面板 maxLength 20）。
 *   - 表 C cmdid 2 按键映射：见 KeyRemapFrame.buildBatchSyncCommand。
 *
 * 0xC5 恢复/清除族（body=SN|子命令）：
 *   - 0xC5 01 恢复出厂：跑两表全部恢复默认 handler（0x600f856）——危险操作，UI 必须 confirm。
 *   - 0xC5 02 清除配对记录（2026-11-19 eqproto 钉死：QCY 原生面板「清除配对记录」经 SDK
 *     clearPairedRecord() 组 opcode C5/参数 02，与 0.9.1 误当「找耳机响声」发的字节完全相同）。
 *     危险操作：清除后连接断开、需重新配对，UI 必须 confirm。原生「响铃找耳机」不发任何
 *     RCSP 帧（手机侧媒体播放，见 H3SBridge.findBuds 注释）。
 *
 * 纯组帧/解析，无传输副作用；真机未验证（红线：不连真机），[未钉] 处如实标注。
 */
public final class MoreFuncFrame {
    /** 定时关机 cmdid（面板 cmdid 20；getter 回 4B，setter 静默）。 */
    public static final int CMD_SLEEP_TIMER = 0x14;
    /** 断连关机 cmdid（面板 cmdid 31；getter 回 2B，默认 5 分钟）。 */
    public static final int CMD_AUTO_OFF = 0x1f;
    /** 提示音语言 cmdid（面板 cmdid 25；4B key "JL00"/"JL01"）。 */
    public static final int CMD_TONE_LANGUAGE = 0x19;
    /** LDAC 开关 cmdid（面板 cmdid 35）。 */
    public static final int CMD_LDAC = 0x23;
    /** 双设备连接 cmdid（面板 cmdid 36）。 */
    public static final int CMD_DUAL_DEVICE = 0x24;
    /** 批量同步写 opcode（固件分发器 0x0600fae0 case 0，docs/morefunc-protocol.md §3）。 */
    public static final int OPCODE_BATCH_WRITE = 0xc0;
    /** 批量同步读 opcode（SDK CMD_ADV_NOTIFY_SETTINGS=0xC3 之外的 C1 读，C1 读实测形状见 QueryPolicy §ancCycle）。 */
    public static final int OPCODE_BATCH_READ = 0xc1;
    /** 恢复/清除族 opcode（RX 分发 0x0600fd78：body[0]=1 恢复出厂 / 2 清除配对记录）。 */
    public static final int OPCODE_RESET_FAMILY = 0xc5;
    /** 0xC5 子命令：恢复出厂（跑两表全部恢复默认回调）。 */
    public static final int RESET_SUBCMD_FACTORY = 0x01;
    /** 0xC5 子命令：清除配对记录（eqproto 字节对照钉死：QCY 原生清配对命令；清后断连需重新配对）。 */
    public static final int RESET_SUBCMD_CLEAR_PAIRING = 0x02;
    /** 批量同步表 C：设备名 cmdid（32B 字串，读 0x0602557a / 写 0x060255e4）。 */
    public static final int BATCH_CMDID_NAME = 0x01;
    /** 设备名固件上限（32B；面板 maxLength=20，本类从严取 20）。 */
    public static final int NAME_MAX_LEN = 20;
    public static final int NAME_FW_MAX_LEN = 32;
    /** 提示音语言 key：英文 "JL00"。 */
    public static final byte[] TONE_KEY_EN = {(byte) 0x4a, (byte) 0x4c, (byte) 0x30, (byte) 0x30};
    /** 提示音语言 key：中文 "JL01"。 */
    public static final byte[] TONE_KEY_ZH = {(byte) 0x4a, (byte) 0x4c, (byte) 0x30, (byte) 0x31};
    /** 定时/断连关机「不关机」哨兵值（FFFF）。 */
    public static final int MINUTES_DISABLED = 0xffff;

    private MoreFuncFrame() {}

    private static void check(boolean ok, String msg) {
        if (!ok) throw new IllegalArgumentException(msg);
    }

    // ---------- FF 自定义命令（data 侧） ----------

    /** 定时/断连关机分钟数合法性：面板档位 {5,10,30,60} 或 FFFF=不关机；0=立即关机拒绝。 */
    public static boolean isLegalMinutes(int minutes) {
        return minutes == MINUTES_DISABLED
            || minutes == 5 || minutes == 10 || minutes == 30 || minutes == 60;
    }

    /** 0x14 定时关机 data=[lo, hi, 00, 00]（len 4；setter 忽略第 3/4 字节，protocol.md 实测形状）。 */
    public static byte[] buildSleepTimerData(int minutes) {
        check(isLegalMinutes(minutes), "定时关机分钟数非法（5/10/30/60 或 0xFFFF=不关机）: " + minutes);
        return new byte[]{(byte) minutes, (byte) (minutes >> 8), 0x00, 0x00};
    }

    /** 0x1f 断连关机 data=[lo, hi]（len 2，LE u16）。 */
    public static byte[] buildAutoOffData(int minutes) {
        check(isLegalMinutes(minutes), "断连关机分钟数非法（5/10/30/60 或 0xFFFF=不关机）: " + minutes);
        return new byte[]{(byte) minutes, (byte) (minutes >> 8)};
    }

    /** 0x19 提示音语言 data=4B key（chinese=false→"JL00" 英文 / true→"JL01" 中文）。 */
    public static byte[] buildToneLanguageData(boolean chinese) {
        return (chinese ? TONE_KEY_ZH : TONE_KEY_EN).clone();
    }

    /** 0x23 LDAC / 0x24 双设备 data=[01/00]（len 1）。 */
    public static byte[] buildBoolData(boolean on) {
        return new byte[]{(byte) (on ? 1 : 0)};
    }

    /** 组 FF 自定义命令帧（复用 H3SBridge.buildCustomCommand）。 */
    public static byte[] buildCustomCommand(int sn, int cmdId, byte[] data) {
        return H3SBridge.buildCustomCommand(sn, cmdId, data);
    }

    // ---------- 批量同步（opcode 0xC0 写 / 0xC1 读） ----------

    /** 批量同步 item：{u8 itemLen=1+data.length, u8 cmdid, data…}。 */
    public static byte[] buildBatchItem(int cmdId, byte[] data) {
        check(cmdId >= 0 && cmdId <= 255, "批量同步 cmdid 超出 u8: " + cmdId);
        check(data != null, "批量同步 data 不能为 null");
        check(data.length >= 1 && data.length <= 254, "批量同步 item data 长度非法（1..254）: " + data.length);
        byte[] item = new byte[2 + data.length];
        item[0] = (byte) (1 + data.length);
        item[1] = (byte) cmdId;
        System.arraycopy(data, 0, item, 2, data.length);
        return item;
    }

    /** 批量同步写帧：FE DC BA | C0 | C0 | len | SN | item | EF（单 item；固件分发器支持多 item 序列）。 */
    public static byte[] buildBatchWriteCommand(int sn, int cmdId, byte[] data) {
        byte[] item = buildBatchItem(cmdId, data);
        return buildRawFrame(EqFrame.FLAGS_CMD, OPCODE_BATCH_WRITE, sn, item);
    }

    /** 设备名写帧：0xC0 批量 item cmdid 1，data=名字 UTF-8 字节（面板 maxLength 20 从严）。 */
    public static byte[] buildNameWriteCommand(int sn, String name) {
        check(name != null, "设备名不能为 null");
        byte[] bytes = name.getBytes(java.nio.charset.StandardCharsets.UTF_8);
        check(bytes.length >= 1 && bytes.length <= NAME_MAX_LEN,
            "设备名字节数非法（1.." + NAME_MAX_LEN + "，面板 maxLength=20；固件上限 32）: " + bytes.length);
        return buildBatchWriteCommand(sn, BATCH_CMDID_NAME, bytes);
    }

    /** 批量同步读帧：FE DC BA | C0 | C1 | len | SN | mask(4B 大端) | EF（bit n=1 读表 C cmdid n）。 */
    public static byte[] buildBatchReadCommand(int sn, int mask) {
        check(mask >= 0 && mask <= 0x7fff_ffff, "mask 超出 u32: " + mask);
        byte[] body = {(byte) (mask >> 24), (byte) (mask >> 16), (byte) (mask >> 8), (byte) mask};
        return buildRawFrame(EqFrame.FLAGS_CMD, OPCODE_BATCH_READ, sn, body);
    }

    // ---------- 0xC5 恢复/清除族 ----------

    /** 0xC5 帧：FE DC BA | C0 | C5 | len | SN | 子命令 | EF（子命令∈{01 恢复出厂, 02 清除配对}）。 */
    public static byte[] buildResetFamilyCommand(int sn, int subcmd) {
        check(subcmd == RESET_SUBCMD_FACTORY || subcmd == RESET_SUBCMD_CLEAR_PAIRING,
            "0xC5 子命令非法（01=恢复出厂, 02=清除配对）: " + subcmd);
        return buildRawFrame(EqFrame.FLAGS_CMD, OPCODE_RESET_FAMILY, sn, new byte[]{(byte) subcmd});
    }

    // ---------- 帧组装/解析公共 ----------

    /** 组「opcode + SN + payload」帧（payload 不含 SN；批量同步/0xC5 共用）。 */
    static byte[] buildRawFrame(int flags, int opcode, int sn, byte[] payload) {
        check(payload != null, "payload 不能为 null");
        int bodyLen = 1 + payload.length;
        check(bodyLen <= 0xffff, "body 超长: " + bodyLen);
        byte[] frame = new byte[8 + bodyLen];
        frame[0] = (byte) 0xfe; frame[1] = (byte) 0xdc; frame[2] = (byte) 0xba;
        frame[3] = (byte) flags;
        frame[4] = (byte) opcode;
        frame[5] = (byte) (bodyLen >> 8); frame[6] = (byte) bodyLen;
        frame[7] = (byte) sn;
        System.arraycopy(payload, 0, frame, 8, payload.length);
        frame[frame.length - 1] = (byte) 0xef;
        return frame;
    }

    /** 通用严格反解析：校验头/flags/opcode/长度/尾，返回 SN 后的 payload（不含尾 EF）。 */
    public static byte[] parseFrame(byte[] frame, int expectedFlags, int expectedOpcode) {
        check(frame != null && frame.length >= 10, "帧太短");
        check((frame[0] & 0xff) == 0xfe && (frame[1] & 0xff) == 0xdc && (frame[2] & 0xff) == 0xba, "帧头不是 FE DC BA");
        check((frame[3] & 0xff) == expectedFlags, "flags 不符: " + Integer.toHexString(frame[3] & 0xff));
        check((frame[4] & 0xff) == expectedOpcode, "opcode 不符: " + Integer.toHexString(frame[4] & 0xff));
        int bodyLen = ((frame[5] & 0xff) << 8) | (frame[6] & 0xff);
        check(frame.length == 8 + bodyLen, "len_BE16 与帧长度不符");
        check((frame[frame.length - 1] & 0xff) == 0xef, "帧尾不是 EF");
        return java.util.Arrays.copyOfRange(frame, 8, frame.length - 1);
    }

    /** 批量同步读回执解析：在 payload 里找 item{len, cmdid, value}，返回指定 cmdid 的 value
     *  （找不到返回 null）。item 形状与写同族 {u8 len(含 cmdid), u8 cmdid, value[len-1]}（分发器
     *  读路径组装，前面可能有 [未钉] 的前缀字节——这里对前缀宽容，按「前一字节=len 且
     *  长度自洽」匹配提取，不做整帧严格校验）。 */
    public static byte[] parseBatchReadPayload(byte[] payload, int cmdId) {
        if (payload == null) return null;
        for (int i = 1; i < payload.length; i++) {
            if ((payload[i] & 0xff) != cmdId) continue;
            int itemLen = payload[i - 1] & 0xff;
            int dataLen = itemLen - 1;
            if (dataLen < 0 || i + 1 + dataLen > payload.length) continue;
            byte[] value = new byte[dataLen];
            System.arraycopy(payload, i + 1, value, 0, dataLen);
            return value;
        }
        return null;
    }

    /** 定时关机 getter 4B 解析：[设定分钟 LE16, 剩余分钟 LE16]；长度不符抛异常。 */
    public static int[] parseSleepTimerPayload(byte[] payload) {
        check(payload != null && payload.length == 4, "定时关机回读载荷应为 4B: " + (payload == null ? 0 : payload.length));
        int set = (payload[0] & 0xff) | ((payload[1] & 0xff) << 8);
        int left = (payload[2] & 0xff) | ((payload[3] & 0xff) << 8);
        return new int[]{set, left};
    }

    /** 断连关机 getter 2B 解析：分钟 LE16。 */
    public static int parseAutoOffPayload(byte[] payload) {
        check(payload != null && payload.length == 2, "断连关机回读载荷应为 2B: " + (payload == null ? 0 : payload.length));
        return (payload[0] & 0xff) | ((payload[1] & 0xff) << 8);
    }

    /** 提示音语言 getter 4B key 解析：true=中文(JL01)/false=英文(JL00)；其它值抛异常（不猜语义）。 */
    public static boolean parseToneLanguagePayload(byte[] payload) {
        check(payload != null && payload.length == 4, "提示音语言回读载荷应为 4B: " + (payload == null ? 0 : payload.length));
        if (java.util.Arrays.equals(payload, TONE_KEY_ZH)) return true;
        if (java.util.Arrays.equals(payload, TONE_KEY_EN)) return false;
        throw new IllegalArgumentException("未知提示音语言 key（非 JL00/JL01）: " + H3SBridge.hex(payload));
    }
}
