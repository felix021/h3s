package cn.felix021.h3squery;

/**
 * 按键重映射命令组帧（docs/app-protocol.md §2.5）：App setKeyFunction() 把 eventID 拆成
 * key+gesture 后有两种传输实现，H3S 实际走哪条 [未钉]（真机抓包定案），本类两条都实现：
 *
 *   候选 A（默认）「标准 ADV 类型 2」：opcode 0xC2（[假设] C1 读的对偶写 opcode——C1+4B 大端
 *     mask 的读形状已实测钉死（docs/protocol.md「通用ADV接口」），写对偶 opcode 值与「+值」
 *     扩展均未钉）；body = SN + mask(4B 大端，type2 → 1<<2 = 00 00 00 04) + [key,gesture,action]。
 *
 *   候选 B「自定义命令 0x2b」：opcode 0xFF cmdid 0x2b，data = [key,gesture,action]（反编译
 *     证据只到「gesture/action 组合」，字节序按 eventID 拆解结果直存；docs/eq-toggle.md）。
 *
 * 字段域（两端证据互证）：
 *   - key = 逻辑键 3=音量- / 5=音量+ / 7=多功能（App eventID 布局 0x{逻辑键}0{gesture+1}：
 *     769=0x0301、1281=0x0501、1793..1796=0x0701..0x0704；固件侧 BW handler 掩码 0x09050307
 *     把物理键 10..13 映射逻辑键 {7,3,5,9}，docs/d1-acceptance.md §6.1）。
 *   - gesture = eventID 低字节 = 连击数 1..4（单击~四击，控制面板钉死；固件映射表默认项
 *     [7,2,7] 的 gesture=2 与双击 eventID 0x0702 低字节一致，docs/eq-toggle.md）。5=长按
 *     [假设]：App 面板无长按条目，但固件配置 key 48 是 15 个 3B 三元组的映射表
 *     （docs/config-key-map.md）恰为 3 键×5 手势——按连击数 1..4 顺延假设长按=5，真机定案。
 *   - action = 动作编号 0..7（控制面板 cmd：0 无作用/1 播放暂停/2 上一曲/3 下一曲/4 语音
 *     助手/5 音量+/6 音量-/7 模式切换）+ 11（ANC 链 0x06018b60，固件侧动作、面板外）。
 *
 * eventID ↔ (key,gesture) 双向拆解/组回，纯离线组帧，已单测；真机下发未验证（红线：不连
 * 真机、不刷机）。
 */
public final class KeyRemapFrame {
    /** 自定义命令 cmdid 候选 B（docs/eq-toggle.md：新设备自定义命令 0x2b，gesture/action 组合）。 */
    public static final int CMD_ID = 0x2b;
    /** ADV 设备设置写 opcode 候选 A（[假设] C1=读 已钉，C2=写对偶 未钉，真机抓包定案）。 */
    public static final int ADV_WRITE_OPCODE = 0xc2;
    /** ADV 属性 type2（按键重映射）的 4B 大端 mask：1<<2，与 C1 读的 mask 族同构（type10=1<<10）。 */
    public static final int ADV_TYPE2_MASK = 0x00000004;
    /** H3S 三键布局的逻辑键（音量-/音量+/多功能），面板 eventID 与固件 BW 掩码互证。 */
    public static final int[] KEYS = {3, 5, 7};
    /** gesture 合法域：1..4=单击~四击（面板钉死）；5=长按 [假设]（key 48 表 3 键×5 手势顺延）。 */
    public static final int GESTURE_MIN = 1;
    public static final int GESTURE_MAX = 5;
    /** 面板外固件动作：11=ANC（0x06025382 动作跳表 → 0x06018b60 ANC 链）。 */
    public static final int ACTION_ANC = 11;
    /** 面板动作上限（0..7），11 单独放行。 */
    public static final int ACTION_PANEL_MAX = 7;

    private KeyRemapFrame() {}

    /** 反解析结果：一个 [key,gesture,action] 三元组。 */
    public static final class Triple {
        public final int key;
        public final int gesture;
        public final int action;
        Triple(int key, int gesture, int action) { this.key = key; this.gesture = gesture; this.action = action; }
    }

    private static void check(boolean ok, String msg) {
        if (!ok) throw new IllegalArgumentException(msg);
    }

    /** action 合法：0..7（面板全集）或 11（ANC，固件侧）。 */
    public static boolean isLegalAction(int action) {
        return (action >= 0 && action <= ACTION_PANEL_MAX) || action == ACTION_ANC;
    }

    /** key 合法：H3S 三键布局的逻辑键 {3,5,7}。 */
    public static boolean isLegalKey(int key) {
        for (int k : KEYS) if (k == key) return true;
        return false;
    }

    public static boolean isLegalGesture(int gesture) {
        return gesture >= GESTURE_MIN && gesture <= GESTURE_MAX;
    }

    /** 三元组整体合法性（非抛出版，EqWritePolicy 白名单形状判定用）。 */
    public static boolean isValidTriple(int key, int gesture, int action) {
        return isLegalKey(key) && isLegalGesture(gesture) && isLegalAction(action);
    }

    /** 三元组校验（抛出版，组帧入口用），错误信息带取值域说明。 */
    public static void validate(int key, int gesture, int action) {
        validateKeyGesture(key, gesture);
        check(isLegalAction(action), "action 非法（0..7 面板动作或 11=ANC）: " + action);
    }

    /** eventID 拆 key：0x{逻辑键}0{gesture+1} 布局，key=eventID>>8。 */
    public static int keyOf(int eventId) { return (eventId >> 8) & 0xff; }

    /** eventID 拆 gesture：低字节=连击数（1..4；5=长按假设）。 */
    public static int gestureOf(int eventId) { return eventId & 0xff; }

    /** (key,gesture) 组回 eventID。 */
    public static int eventIdOf(int key, int gesture) { return ((key & 0xff) << 8) | (gesture & 0xff); }

    /** eventID 校验：非负、key∈{3,5,7}、低字节=1..5（中间 nibble 恒 0 的布局由低字节域保证）。 */
    public static void validateEventId(int eventId) {
        check(eventId >= 0 && eventId <= 0xffff, "eventID 超出 u16 范围: " + eventId);
        validateKeyGesture(keyOf(eventId), gestureOf(eventId));
    }

    /** (key,gesture) 校验（eventID 拆解后的二元组）。 */
    public static void validateKeyGesture(int key, int gesture) {
        check(isLegalKey(key), "key 非法（H3S 三键逻辑键 {3=音量-,5=音量+,7=多功能}）: " + key);
        check(isLegalGesture(gesture), "gesture 非法（1..4=单击~四击，5=长按[假设]）: " + gesture);
    }

    /** 3B 三元组（两条 wire 共用的载荷域）。 */
    public static byte[] triple(int key, int gesture, int action) {
        validate(key, gesture, action);
        return new byte[]{(byte) key, (byte) gesture, (byte) action};
    }

    // ---------- 候选 A：标准 ADV 类型 2（默认） ----------

    /** 候选 A 的 data（SN 后的全部 body）：4B 大端 mask + 3B 三元组，共 7B。 */
    public static byte[] buildAdvType2Data(int key, int gesture, int action) {
        byte[] t = triple(key, gesture, action);
        return new byte[]{0x00, 0x00, 0x00, (byte) ADV_TYPE2_MASK, t[0], t[1], t[2]};
    }

    /**
     * 候选 A 完整帧：FE DC BA | C0 | C2 | len_BE16(8) | SN | mask(4B) | key | gesture | action | EF。
     * opcode C2 与「mask+值」的写形状是 [假设]（C1 读已钉，写对偶未钉）——真机抓包定案。
     */
    public static byte[] buildAdvType2Command(int sn, int key, int gesture, int action) {
        byte[] data = buildAdvType2Data(key, gesture, action);
        int bodyLen = 1 + data.length;                 // SN + mask + 三元组
        byte[] frame = new byte[8 + bodyLen];
        frame[0] = (byte) 0xfe; frame[1] = (byte) 0xdc; frame[2] = (byte) 0xba;
        frame[3] = (byte) EqFrame.FLAGS_CMD;           // C0：命令需响应
        frame[4] = (byte) ADV_WRITE_OPCODE;            // C2：ADV 设置写（候选）
        frame[5] = (byte) (bodyLen >> 8); frame[6] = (byte) bodyLen;
        frame[7] = (byte) sn;
        System.arraycopy(data, 0, frame, 8, data.length);
        frame[frame.length - 1] = (byte) 0xef;
        return frame;
    }

    /** 反解析候选 A 帧：严格校验头/opcode/mask/尾后抽出三元组，往返自检用。 */
    public static Triple parseAdvType2Command(byte[] frame) {
        check(frame != null && frame.length >= 16, "ADV type2 帧太短");
        check((frame[0] & 0xff) == 0xfe && (frame[1] & 0xff) == 0xdc && (frame[2] & 0xff) == 0xba, "帧头不是 FE DC BA");
        check((frame[3] & 0xff) == EqFrame.FLAGS_CMD, "flags 不是 C0");
        check((frame[4] & 0xff) == ADV_WRITE_OPCODE, "opcode 不是 C2");
        int bodyLen = ((frame[5] & 0xff) << 8) | (frame[6] & 0xff);
        check(frame.length == 8 + bodyLen, "len_BE16 与帧长度不符");
        check((frame[frame.length - 1] & 0xff) == 0xef, "帧尾不是 EF");
        check(bodyLen == 8, "ADV type2 body 长度应为 8（SN+mask+三元组）: " + bodyLen);
        int mask = ((frame[8] & 0xff) << 24) | ((frame[9] & 0xff) << 16) | ((frame[10] & 0xff) << 8) | (frame[11] & 0xff);
        check(mask == ADV_TYPE2_MASK, "mask 不是 type2（00 00 00 04）: " + Integer.toHexString(mask));
        int key = frame[12] & 0xff, gesture = frame[13] & 0xff, action = frame[14] & 0xff;
        validate(key, gesture, action);
        return new Triple(key, gesture, action);
    }

    // ---------- 候选 B：自定义命令 0x2b ----------

    /** 候选 B 的 data：3B 三元组（「gesture/action 组合」按 eventID 拆解结果直存，字节序 [未钉]）。 */
    public static byte[] buildCustomData(int key, int gesture, int action) {
        return triple(key, gesture, action);
    }

    /**
     * 候选 B 完整帧：FE DC BA | C0 | FF | len_BE16(6) | SN | 0x2b | 03 | key | gesture | action | EF，
     * 帧形状与 0x17/0x22/0x1d 同族（opcode 0xFF 自定义命令，docs/cmd-0x17-0x1d-layout.md）。
     */
    public static byte[] buildCustomCommand(int sn, int key, int gesture, int action) {
        byte[] data = buildCustomData(key, gesture, action);
        int dataLen = data.length;
        int bodyLen = 3 + dataLen;                     // SN + cmdid + data_len + data
        byte[] frame = new byte[8 + bodyLen];
        frame[0] = (byte) 0xfe; frame[1] = (byte) 0xdc; frame[2] = (byte) 0xba;
        frame[3] = (byte) EqFrame.FLAGS_CMD;
        frame[4] = (byte) EqFrame.OPCODE_CUSTOM;       // FF：自定义命令
        frame[5] = (byte) (bodyLen >> 8); frame[6] = (byte) bodyLen;
        frame[7] = (byte) sn;
        frame[8] = (byte) CMD_ID;                      // 0x2b
        frame[9] = (byte) dataLen;                     // 03
        System.arraycopy(data, 0, frame, 10, dataLen);
        frame[frame.length - 1] = (byte) 0xef;
        return frame;
    }

    /** 反解析候选 B 帧：严格校验头/opcode/cmdid/长度/尾后抽出三元组，往返自检用。 */
    public static Triple parseCustomCommand(byte[] frame) {
        check(frame != null && frame.length >= 12, "0x2b 帧太短");
        check((frame[0] & 0xff) == 0xfe && (frame[1] & 0xff) == 0xdc && (frame[2] & 0xff) == 0xba, "帧头不是 FE DC BA");
        check((frame[3] & 0xff) == EqFrame.FLAGS_CMD, "flags 不是 C0");
        check((frame[4] & 0xff) == EqFrame.OPCODE_CUSTOM, "opcode 不是 0xFF");
        int bodyLen = ((frame[5] & 0xff) << 8) | (frame[6] & 0xff);
        check(frame.length == 8 + bodyLen, "len_BE16 与帧长度不符");
        check((frame[frame.length - 1] & 0xff) == 0xef, "帧尾不是 EF");
        check((frame[8] & 0xff) == CMD_ID, "cmdid 不是 0x2b");
        int dataLen = frame[9] & 0xff;
        check(dataLen == 3 && bodyLen == 3 + dataLen, "0x2b data_len 应为 3（三元组）: " + dataLen);
        int key = frame[10] & 0xff, gesture = frame[11] & 0xff, action = frame[12] & 0xff;
        validate(key, gesture, action);
        return new Triple(key, gesture, action);
    }
}
