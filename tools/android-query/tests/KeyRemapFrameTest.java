import cn.felix021.h3squery.EqWritePolicy;
import cn.felix021.h3squery.KeyRemapFrame;
import cn.felix021.h3squery.KeyRemapFrame.Triple;

/**
 * KeyRemapFrame 纯 JVM 单测（照 EqFrameTest 风格）：两条 wire 候选（ADV type2 默认 /
 * 自定义 0x2b）的往返、eventID 拆解/组回、字段域边界、文档字节对照、EqWritePolicy 放行。
 * 全部离线组帧层验证；真机下发未验证（红线：不连真机）。
 */
public class KeyRemapFrameTest {
    static void expect(boolean b, String msg) { if (!b) throw new AssertionError(msg); }
    static void eq(String name, int a, int b) { if (a != b) throw new AssertionError(name + ": " + a + " != " + b); }
    static void bytes(String name, byte[] a, byte[] b) {
        if (!java.util.Arrays.equals(a, b))
            throw new AssertionError(name + ":\n  got " + hex(a) + "\n  exp " + hex(b));
    }
    static String hex(byte[] b) {
        StringBuilder s = new StringBuilder();
        for (byte x : b) s.append(String.format("%02x", x & 0xff));
        return s.toString();
    }
    static byte[] unhex(String h) {
        byte[] out = new byte[h.length() / 2];
        for (int i = 0; i < out.length; i++) out[i] = (byte) Integer.parseInt(h.substring(i * 2, i * 2 + 2), 16);
        return out;
    }
    static void rejected(Runnable r, String msg) {
        try { r.run(); } catch (IllegalArgumentException e) { return; }
        throw new AssertionError("未按预期报错: " + msg);
    }

    public static void main(String[] args) {
        eventIdSplit();
        roundTripBothWires();
        docBytes();
        boundary();
        policy();
        System.out.println("KeyRemapFrameTest passed: eventID 拆解/组回、两 wire 往返、文档字节对照、字段域边界、EqWritePolicy 放行/拒绝");
    }

    /** eventID ↔ (key,gesture) 拆解/组回：面板全集 6 条 + 长按假设 3 条。 */
    static void eventIdSplit() {
        int[][] cases = {
            {769, 3, 1},    // 音量- 单击（控制面板）
            {1281, 5, 1},   // 音量+ 单击
            {1793, 7, 1},   // 多功能 单击
            {1794, 7, 2},   // 多功能 双击（固件默认表 [7,2,7] 的 gesture=2 与此低字节互证）
            {1795, 7, 3},   // 多功能 三击
            {1796, 7, 4},   // 多功能 四击
            {0x0305, 3, 5}, // 长按 [假设]：低字节按 1..4 顺延为 5
            {0x0505, 5, 5},
            {0x0705, 7, 5},
        };
        for (int[] c : cases) {
            eq("keyOf(" + c[0] + ")", c[1], KeyRemapFrame.keyOf(c[0]));
            eq("gestureOf(" + c[0] + ")", c[2], KeyRemapFrame.gestureOf(c[0]));
            eq("eventIdOf(" + c[1] + "," + c[2] + ")", c[0], KeyRemapFrame.eventIdOf(c[1], c[2]));
        }
        // 长按 eventID 五条全部合法（gesture=5 放行，假设域）
        KeyRemapFrame.validateEventId(0x0705);
    }

    /** 两 wire 往返：全部 3 键×5 手势×动作代表值（0/1/7/11）经 build→parse 字段一致。 */
    static void roundTripBothWires() {
        int[] actions = {0, 1, 7, 11};
        for (int key : KeyRemapFrame.KEYS) {
            for (int gesture = KeyRemapFrame.GESTURE_MIN; gesture <= KeyRemapFrame.GESTURE_MAX; gesture++) {
                for (int action : actions) {
                    Triple a = KeyRemapFrame.parseAdvType2Command(KeyRemapFrame.buildAdvType2Command(0x2a, key, gesture, action));
                    eq("ADV key", key, a.key);
                    eq("ADV gesture", gesture, a.gesture);
                    eq("ADV action", action, a.action);
                    Triple b = KeyRemapFrame.parseCustomCommand(KeyRemapFrame.buildCustomCommand(0x2a, key, gesture, action));
                    eq("0x2b key", key, b.key);
                    eq("0x2b gesture", gesture, b.gesture);
                    eq("0x2b action", action, b.action);
                }
            }
        }
        // SN 透传（两 wire 的 SN 位置不同：ADV body[0] 与 FF body[0]）
        expect((KeyRemapFrame.buildAdvType2Command(0x33, 7, 2, 7)[7] & 0xff) == 0x33, "ADV SN 位置");
        expect((KeyRemapFrame.buildCustomCommand(0x34, 7, 2, 7)[7] & 0xff) == 0x34, "0x2b SN 位置");
    }

    /** 文档字节对照：多功能双击→模式切换（eventID 0x0702, action 7）两条 wire 的完整帧逐字节。 */
    static void docBytes() {
        // 候选 A：FE DC BA C0 C2 0008 | SN=00 | 00 00 00 04 | 07 02 07 | EF
        bytes("ADV type2 帧", KeyRemapFrame.buildAdvType2Command(0, 7, 2, 7),
            unhex("fedcbac0c20008" + "00" + "00000004" + "070207" + "ef"));
        // 候选 B：FE DC BA C0 FF 0006 | SN=00 | 2b | 03 | 07 02 07 | EF
        bytes("0x2b 帧", KeyRemapFrame.buildCustomCommand(0, 7, 2, 7),
            unhex("fedcbac0ff0006" + "00" + "2b03" + "070207" + "ef"));
        // 音量- 单击→无作用（769, 0）
        bytes("音量- 单击 ADV", KeyRemapFrame.buildAdvType2Command(0, 3, 1, 0),
            unhex("fedcbac0c20008" + "00" + "00000004" + "030100" + "ef"));
        // data 与帧解耦可对拍
        bytes("ADV data", KeyRemapFrame.buildAdvType2Data(3, 1, 0), unhex("00000004030100"));
        bytes("0x2b data", KeyRemapFrame.buildCustomData(3, 1, 0), unhex("030100"));
    }

    /** 字段域边界：非法 key/gesture/action/eventID 一律抛 IllegalArgumentException。 */
    static void boundary() {
        // key：非三键逻辑键
        rejected(() -> KeyRemapFrame.triple(0, 1, 1), "key=0");
        rejected(() -> KeyRemapFrame.triple(9, 1, 1), "key=9（BW 掩码里的逻辑键 9 无面板条目）");
        rejected(() -> KeyRemapFrame.triple(255, 1, 1), "key=255");
        rejected(() -> KeyRemapFrame.triple(-3, 1, 1), "key=-3");
        // gesture：0/6/越界
        rejected(() -> KeyRemapFrame.triple(7, 0, 1), "gesture=0");
        rejected(() -> KeyRemapFrame.triple(7, 6, 1), "gesture=6");
        rejected(() -> KeyRemapFrame.triple(7, -1, 1), "gesture=-1");
        rejected(() -> KeyRemapFrame.triple(7, 256, 1), "gesture=256");
        // action：8/9/10/12/负值（0..7 与 11 之外的都拒）
        int[] badActions = {-1, 8, 9, 10, 12, 255};
        for (int a : badActions) rejected(() -> KeyRemapFrame.triple(7, 1, a), "action=" + a);
        // eventID：越界/错键/中间 nibble 非零
        rejected(() -> KeyRemapFrame.validateEventId(-1), "eventID=-1");
        rejected(() -> KeyRemapFrame.validateEventId(0x10000), "eventID=0x10000");
        rejected(() -> KeyRemapFrame.validateEventId(0x0901), "eventID=0x0901（逻辑键 9）");
        rejected(() -> KeyRemapFrame.validateEventId(0x0300), "eventID=0x0300（gesture=0）");
        rejected(() -> KeyRemapFrame.validateEventId(0x0306), "eventID=0x0306（gesture=6）");
        rejected(() -> KeyRemapFrame.validateEventId(0x0311), "eventID=0x0311（中间 nibble 非 0）");
        // 帧反解析的形状防御：坏头/坏 opcode/坏 mask/坏 cmdid/坏长度
        rejected(() -> KeyRemapFrame.parseAdvType2Command(unhex("fedcbac0c2000800" + "00" + "00000008" + "070207" + "ef")), "mask 非 type2");
        rejected(() -> KeyRemapFrame.parseAdvType2Command(unhex("aa000000000000000000000000000000")), "坏帧头");
        rejected(() -> KeyRemapFrame.parseAdvType2Command(KeyRemapFrame.buildCustomCommand(0, 7, 2, 7)), "拿 0x2b 帧喂 ADV 解析");
        rejected(() -> KeyRemapFrame.parseCustomCommand(KeyRemapFrame.buildAdvType2Command(0, 7, 2, 7)), "拿 ADV 帧喂 0x2b 解析");
        byte[] badTriple = unhex("fedcbac0c20008" + "00" + "00000004" + "090207" + "ef"); // key=9
        rejected(() -> KeyRemapFrame.parseAdvType2Command(badTriple), "帧内 key=9");
        byte[] badAction = unhex("fedcbac0ff0006" + "00" + "2b03" + "070209" + "ef");     // action=9
        rejected(() -> KeyRemapFrame.parseCustomCommand(badAction), "帧内 action=9");
    }

    /** EqWritePolicy 一致性：两 wire 帧武装后放行、未武装拒绝；形状变体（mask/字段）拒绝。 */
    static void policy() {
        byte[] adv = KeyRemapFrame.buildAdvType2Command(0, 7, 2, 7);
        byte[] custom = KeyRemapFrame.buildCustomCommand(0, 3, 1, 6);
        expect(!EqWritePolicy.permitted(adv, false), "ADV type2 帧未武装应拒绝");
        expect(EqWritePolicy.permitted(adv, true), "ADV type2 帧武装后应放行");
        expect(!EqWritePolicy.permitted(custom, false), "0x2b 帧未武装应拒绝");
        expect(EqWritePolicy.permitted(custom, true), "0x2b 帧武装后应放行");
        // 白名单从严：mask 换 type10、字段越界、长度不符——武装后也拒
        expect(!EqWritePolicy.permitted(unhex("fedcbac0c20008" + "00" + "00000400" + "070207" + "ef"), true), "mask=type10 应拒");
        expect(!EqWritePolicy.permitted(unhex("fedcbac0c20008" + "00" + "00000004" + "090207" + "ef"), true), "帧内 key=9 应拒");
        expect(!EqWritePolicy.permitted(unhex("fedcbac0c20008" + "00" + "00000004" + "070209" + "ef"), true), "帧内 action=9 应拒");
        expect(!EqWritePolicy.permitted(unhex("fedcbac0c2000900" + "00" + "00000004" + "070207" + "00" + "ef"), true), "body 长 9 应拒");
        expect(EqWritePolicy.permitted(unhex("fedcbac0ff0006" + "00" + "2b03" + "070206" + "ef"), true), "0x2b 合法帧应放行");
        expect(!EqWritePolicy.permitted(unhex("fedcbac0ff0006" + "00" + "2b03" + "07020c" + "ef"), true), "0x2b action=12 应拒");
        expect(!EqWritePolicy.permitted(unhex("fedcbac0ff0004" + "00" + "2b01" + "0702" + "ef"), true), "0x2b data_len=1 应拒");
        // 长 press gesture=5（假设域）在白名单内
        expect(EqWritePolicy.permitted(KeyRemapFrame.buildAdvType2Command(0, 3, 5, 11), true), "长按 gesture=5 应放行");
    }
}
