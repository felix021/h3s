import cn.felix021.h3squery.EqFrame;
import cn.felix021.h3squery.EqFrame.EqBand;
import cn.felix021.h3squery.EqFrame.Parsed;
import cn.felix021.h3squery.EqWritePolicy;
import java.util.ArrayList;
import java.util.Arrays;
import java.util.List;

public class EqFrameTest {
    static void expect(boolean b, String msg) { if (!b) throw new AssertionError(msg); }
    static void eq(String name, int a, int b) { if (a != b) throw new AssertionError(name + ": " + a + " != " + b); }
    static String hex(byte[] b) {
        StringBuilder s = new StringBuilder();
        for (byte x : b) s.append(String.format("%02X ", x & 0xff));
        return s.toString().trim();
    }
    static void bytes(String name, byte[] a, byte[] b) {
        if (!Arrays.equals(a, b)) throw new AssertionError(name + ":\n  got " + hex(a) + "\n  exp " + hex(b));
    }
    static void rejected(Runnable r, String msg) {
        try { r.run(); } catch (IllegalArgumentException e) { return; }
        throw new AssertionError("未按预期报错: " + msg);
    }
    static byte[] band(int freq, int q, int gain, int iir) { return EqFrame.buildEqData(1, 0, Arrays.asList(new EqBand(freq, q, gain, iir))); }

    public static void main(String[] args) {
        roundTrip();
        boundary();
        docBytes();
        popPresetBytes();
        typeSemantics();
        overLimit();
        policy();
        System.out.println("EqFrameTest passed: 往返一致、边界、修正后字节对照（gain 在前）、「流行」预设原生对照、type 语义常量、段数超限、EqWritePolicy 放行/拒绝");
    }

    /** type 字节语义（路 EQFIX 重解 apply 0x06025118 钉死，docs/eq-cmd-0x22-layout.md §2.3；
     *  2026-11-19 codex 补钉官方预设/自定义实发取值）：1=装载 MusicEqBt[0] 槽（载荷被忽略）、
     *  10=空间音效、其余=解码载荷应用——自定义曲线无历史发 255(FF)、有历史发 128(80)。 */
    static void typeSemantics() {
        eq("TYPE_RECALL_SLOT=1", 1, EqFrame.TYPE_RECALL_SLOT);
        eq("TYPE_SPATIAL=10", 10, EqFrame.TYPE_SPATIAL);
        eq("TYPE_CUSTOM_NO_HISTORY=255", 255, EqFrame.TYPE_CUSTOM_NO_HISTORY);
        eq("TYPE_CUSTOM_HISTORY=128", 128, EqFrame.TYPE_CUSTOM_HISTORY);
        // 自定义无历史下发头：type=255 + mastergain -4dB → FF 70 FE
        bytes("type=255 头", EqFrame.buildEqData(EqFrame.TYPE_CUSTOM_NO_HISTORY, -400, new ArrayList<EqBand>()),
              hexBytes("FF 70 FE"));
        // type=255 曲线帧形状过写策略；type=1 帧形状同样是 0x22 setter（策略只钉形状，语义由调用方选择）
        byte[] apply = EqFrame.buildEqCommand(EqFrame.TYPE_CUSTOM_NO_HISTORY, -400,
            Arrays.asList(new EqBand(30, 80, 0, 0), new EqBand(170, 60, -1700, 2)));
        expect(EqWritePolicy.permitted(apply, true), "type=255 曲线下发帧应放行");
        expect(!EqWritePolicy.permitted(apply, false), "未 armed 应拒绝 type=255 帧");
        expect(EqWritePolicy.permitted(EqFrame.buildEqCommand(EqFrame.TYPE_RECALL_SLOT, -400,
            Arrays.asList(new EqBand(30, 80, 0, 0))), true), "type=1 帧形状合法（策略按形状放行）");
    }

    static void roundTrip() {
        // 10 段满配：覆盖正负 gain、q 大小值、iir 全枚举（0..4 与 255）
        List<EqBand> bands = new ArrayList<EqBand>();
        int[] freq = {20, 32, 64, 125, 250, 500, 1000, 2000, 8000, 16000};
        int[] q = {40, 120, 80, 300, 250, 100, 500, 1000, 2000, 5};
        int[] gain = {-1700, -250, 300, 0, -400, 800, -1200, 1500, -3000, 32767};
        int[] iir = {0, 1, 2, 3, 4, 255, 0, 2, 3, 255};
        for (int i = 0; i < 10; i++) bands.add(new EqBand(freq[i], q[i], gain[i], iir[i]));
        byte[] data = EqFrame.buildEqData(2, -400, bands);
        Parsed p = EqFrame.parseEqData(data);
        eq("roundTrip eqType", 2, p.eqType);
        eq("roundTrip masterGain", -400, p.masterGainCentidB);
        eq("roundTrip bandCount", 10, p.bands.size());
        for (int i = 0; i < 10; i++) {
            EqBand b = p.bands.get(i);
            eq("roundTrip freq[" + i + "]", freq[i], b.freq);
            eq("roundTrip q[" + i + "]", q[i], b.q);
            eq("roundTrip gain[" + i + "]", gain[i], b.gain);
            eq("roundTrip iir[" + i + "]", iir[i], b.iirType);
        }
        // 完整帧也走一遍 parseEqCommand
        byte[] frame = EqFrame.buildEqCommand(2, -400, bands);
        Parsed pf = EqFrame.parseEqCommand(frame);
        eq("frameRoundTrip bandCount", 10, pf.bands.size());
        eq("frameRoundTrip gain[0]", -1700, pf.bands.get(0).gain);
    }

    static void boundary() {
        // 0 段：data 只有 3B 头
        byte[] zero = EqFrame.buildEqData(1, 0, new ArrayList<EqBand>());
        bytes("0 段 data", zero, new byte[]{1, 0, 0});
        Parsed pz = EqFrame.parseEqData(zero);
        eq("0 段 eqType", 1, pz.eqType);
        eq("0 段 bandCount", 0, pz.bands.size());
        byte[] zf = EqFrame.buildEqCommand(1, 0, new ArrayList<EqBand>());
        expect(zf.length == 14, "0 段帧长应为 14");
        expect(EqFrame.parseEqCommand(zf).bands.size() == 0, "0 段帧往返");

        // 1 段
        List<EqBand> one = Arrays.asList(new EqBand(1000, 80, -400, 255));
        byte[] d1 = EqFrame.buildEqData(10, 200, one);
        eq("1 段 data 长", 10, d1.length);
        Parsed p1 = EqFrame.parseEqData(d1);
        eq("1 段 eqType", 10, p1.eqType);
        eq("1 段 masterGain", 200, p1.masterGainCentidB);
        eq("1 段 freq", 1000, p1.bands.get(0).freq);
        eq("1 段 q", 80, p1.bands.get(0).q);
        eq("1 段 gain", -400, p1.bands.get(0).gain);
        eq("1 段 iir", 255, p1.bands.get(0).iirType);
    }

    static void docBytes() {
        // 逐字节对照：字段顺序按 eqproto 修正——每段 +2/+3=gain、+4/+5=Q（QCY 原生 DEX
        // 0x5755EE 先 getGain 写 +2/+3、0x575632 后 getQ 写 +4/+5；docs 同步待补）。
        // 头：type 1 + mastergain -4 → 01 70 FE；段0 freq30/q80/gain0/iir0；段1 freq170/q60/gain-17/iir2
        List<EqBand> two = Arrays.asList(new EqBand(30, 80, 0, 0), new EqBand(170, 60, -1700, 2));
        byte[] d = EqFrame.buildEqData(1, -400, two);
        bytes("头+段0/段1", d, hexBytes("01 70 FE 1E 00 00 00 50 00 00 AA 00 5C F9 3C 00 02"));

        // master gain -4dB = 0xFE70（i16 小端）
        bytes("master -4dB", EqFrame.buildEqData(1, -400, new ArrayList<EqBand>()), hexBytes("01 70 FE"));

        // freq 1000Hz = 0x03E8 → E8 03；gain -17dB=-1700 → 5C F9；q 0.8=80 → 50 00
        bytes("freq1000/gain-17/q80", band(1000, 80, -1700, 2), hexBytes("01 00 00 E8 03 5C F9 50 00 02"));

        // 段 8 示例：freq 11000 / gain 2(200) / q 2(200) / iir 3 → F8 2A C8 00 C8 00 03（gain==q 对称，顺序无关）
        bytes("段8（对称段）", band(11000, 200, 200, 3), hexBytes("01 00 00 F8 2A C8 00 C8 00 03"));

        // 完整 RCSP 帧：1 段（master -4、freq30/gain0/q80/iir0）
        // FE DC BA C0 FF 00 0D 00 22 0A 01 70 FE 1E 00 00 00 50 00 00 EF
        byte[] frame = EqFrame.buildEqCommand(1, -400, Arrays.asList(new EqBand(30, 80, 0, 0)));
        bytes("完整 RCSP 帧", frame, hexBytes("FE DC BA C0 FF 00 0D 00 22 0A 01 70 FE 1E 00 00 00 50 00 00 EF"));
    }

    /** 「流行」预设对照（eqproto 钉死的 QCY 原生字节）：官方预设「流行」面板 id=02（type=02）、
     *  总增益 −4dB，段字节 gain 在前、Q 在后——0.9.1 发反时固件在 170Hz 段读到 gain=+0.4dB、Q=−17。 */
    static void popPresetBytes() {
        // 30 Hz，0 dB，Q 0.5 → 1E 00 00 00 32 00 00（gain 0 占 +2/+3，Q 50=0x32 占 +4/+5）
        bytes("流行段0（30Hz）", band(30, 50, 0, 0), hexBytes("01 00 00 1E 00 00 00 32 00 00"));
        // 170 Hz，−17 dB，Q 0.4 → AA 00 5C F9 28 00 02（gain −1700=0xF95C 在前，Q 40=0x28 在后）
        bytes("流行段1（170Hz）", band(170, 40, -1700, 2), hexBytes("01 00 00 AA 00 5C F9 28 00 02"));

        // 完整「流行」帧头+前两段（SN=00，原生帧 `FE DC BA C0 FF 00 45 00 22 42 02 70 FE`）：
        // 9 段 → len=0x0045、data_len=0x42，type=02、master −4dB=70 FE
        List<EqBand> pop = new ArrayList<EqBand>();
        int[] freq = {30, 170, 300, 700, 1200, 2800, 5000, 10000, 11000};
        int[] q = {50, 40, 200, 100, 200, 100, 200, 300, 200};
        int[] gain = {0, -1700, -200, 200, -250, 400, 0, -600, 200};
        int[] iir = {0, 2, 2, 2, 2, 2, 2, 2, 3};
        for (int i = 0; i < 9; i++) pop.add(new EqBand(freq[i], q[i], gain[i], iir[i]));
        byte[] popFrame = EqFrame.buildEqCommand(0, 2, -400, pop);
        eq("流行帧长", 8 + 3 + 3 + 7 * 9, popFrame.length);
        bytes("流行帧头+段0/段1", Arrays.copyOfRange(popFrame, 0, 13 + 14),
            hexBytes("FE DC BA C0 FF 00 45 00 22 42 02 70 FE 1E 00 00 00 32 00 00 AA 00 5C F9 28 00 02"));
        // 反解析与「流行」预设参数一致（gain/Q 各归各位）
        Parsed pp = EqFrame.parseEqCommand(popFrame);
        eq("流行 eqType", 2, pp.eqType);
        eq("流行 masterGain", -400, pp.masterGainCentidB);
        for (int i = 0; i < 9; i++) {
            eq("流行 freq[" + i + "]", freq[i], pp.bands.get(i).freq);
            eq("流行 q[" + i + "]", q[i], pp.bands.get(i).q);
            eq("流行 gain[" + i + "]", gain[i], pp.bands.get(i).gain);
            eq("流行 iir[" + i + "]", iir[i], pp.bands.get(i).iirType);
        }
    }

    static void overLimit() {
        List<EqBand> eleven = new ArrayList<EqBand>();
        for (int i = 0; i < 11; i++) eleven.add(new EqBand(1000, 80, 0, 2));
        rejected(() -> EqFrame.buildEqData(1, 0, eleven), "11 段应超限");
        rejected(() -> EqFrame.buildEqCommand(1, 0, eleven), "11 段命令应超限");
        // 各字段取值越界
        rejected(() -> EqFrame.buildEqData(256, 0, new ArrayList<EqBand>()), "eqType>255 应报错");
        rejected(() -> EqFrame.buildEqData(1, 32768, new ArrayList<EqBand>()), "master gain 超 i16 应报错");
        rejected(() -> EqFrame.buildEqData(1, -32769, new ArrayList<EqBand>()), "master gain 负超 i16 应报错");
        rejected(() -> EqFrame.buildEqData(1, 0, Arrays.asList(new EqBand(65536, 80, 0, 2))), "freq 超 u16 应报错");
        rejected(() -> EqFrame.buildEqData(1, 0, Arrays.asList(new EqBand(1000, 32768, 0, 2))), "q 超 i16 应报错");
        rejected(() -> EqFrame.buildEqData(1, 0, Arrays.asList(new EqBand(1000, 80, 32768, 2))), "gain 超 i16 应报错");
        rejected(() -> EqFrame.buildEqData(1, 0, Arrays.asList(new EqBand(1000, 80, 0, 5))), "iir=5 应报错");
        rejected(() -> EqFrame.buildEqData(1, 0, Arrays.asList(new EqBand(1000, 80, 0, -1))), "iir=-1 应报错");
        // 越界值在合法范围内应通过（i16 上下界、freq 0/65535、iir 0/4/255）
        EqFrame.buildEqData(255, -32768, Arrays.asList(new EqBand(0, -32768, 32767, 4)));
        EqFrame.buildEqData(0, 32767, Arrays.asList(new EqBand(65535, 32767, -32768, 255)));
    }

    static void policy() {
        byte[] eq = EqFrame.buildEqCommand(1, -400, Arrays.asList(new EqBand(30, 80, 0, 0)));
        expect(!EqWritePolicy.permitted(eq, false), "未 armed 应拒绝 EQ 写");
        expect(EqWritePolicy.permitted(eq, true), "armed 应放行形状匹配的 0x22 setter");

        // armed 仍放行只读帧（退回 QueryPolicy）
        byte[] info = QueryPolicyTest.frame(3, (byte) -1, (byte) -1, (byte) -1, (byte) -1, (byte) 0);
        expect(EqWritePolicy.permitted(info, true), "armed 应保留只读查询");
        expect(EqWritePolicy.permitted(info, false), "未 armed 应保留只读查询");

        // 0x17 ANC setter：形状匹配（data_len=3、mode 1..3）放行；畸形拒绝
        byte[] anc = QueryPolicyTest.frame(255, (byte) 0x17, (byte) 3, (byte) 1, (byte) 1, (byte) 2);
        expect(EqWritePolicy.permitted(anc, true), "armed 应放行形状匹配的 0x17 setter（data=mode scene strength）");
        expect(!EqWritePolicy.permitted(anc, false), "未 armed 应拒绝 0x17 setter");
        expect(!EqWritePolicy.permitted(QueryPolicyTest.frame(255, (byte) 0x17, (byte) 3, (byte) 0, (byte) 1, (byte) 2), true), "0x17 mode=0 应拒绝");
        expect(!EqWritePolicy.permitted(QueryPolicyTest.frame(255, (byte) 0x17, (byte) 3, (byte) 4, (byte) 1, (byte) 2), true), "0x17 mode=4 应拒绝");
        expect(!EqWritePolicy.permitted(QueryPolicyTest.frame(255, (byte) 0x17, (byte) 2, (byte) 1, (byte) 1), true), "0x17 data_len=2 应拒绝");
        expect(!EqWritePolicy.permitted(QueryPolicyTest.frame(255, (byte) 0x17, (byte) 4, (byte) 1, (byte) 1, (byte) 2, (byte) 0), true), "0x17 data_len=4 应拒绝");

        // 0x1d 提示音音量 setter：形状匹配（data_len=2、data[0]∈{3,6,8}）放行；畸形拒绝
        expect(EqWritePolicy.permitted(QueryPolicyTest.frame(255, (byte) 0x1d, (byte) 2, (byte) 6, (byte) 0), true), "armed 应放行形状匹配的 0x1d setter（data=value 占位）");
        expect(!EqWritePolicy.permitted(QueryPolicyTest.frame(255, (byte) 0x1d, (byte) 2, (byte) 6, (byte) 0), false), "未 armed 应拒绝 0x1d setter");
        expect(!EqWritePolicy.permitted(QueryPolicyTest.frame(255, (byte) 0x1d, (byte) 1, (byte) 6), true), "0x1d 单字节 data 应拒绝（固件要求 len>=2）");
        expect(!EqWritePolicy.permitted(QueryPolicyTest.frame(255, (byte) 0x1d, (byte) 2, (byte) 5, (byte) 0), true), "0x1d 值=5 应拒绝（白名单只收 3/6/8）");
        expect(!EqWritePolicy.permitted(QueryPolicyTest.frame(255, (byte) 0x1d, (byte) 3, (byte) 6, (byte) 0, (byte) 0), true), "0x1d data_len=3 应拒绝");
        // 0x22 但 data_len%7 != 3：body=SN,0x22,data_len=4,4 字节 data
        byte[] badLen = rcspFrame(0xC0, 0xFF, new byte[]{0, 0x22, 4, 1, 2, 3, 4});
        expect(!EqWritePolicy.permitted(badLen, true), "0x22 data_len%7!=3 应拒绝");
        // cmdid 不是 0x22（opcode 仍是 0xFF）
        byte[] badCmd = rcspFrame(0xC0, 0xFF, new byte[]{0, 0x21, 3, 1, 2, 3});
        expect(!EqWritePolicy.permitted(badCmd, true), "cmdid!=0x22 应拒绝");
        // opcode 不是 0xFF
        byte[] badOp = rcspFrame(0xC0, 0x22, new byte[]{0, 0x22, 3, 1, 2, 3});
        expect(!EqWritePolicy.permitted(badOp, true), "opcode!=0xFF 应拒绝");
        // flags 不是 C0
        byte[] badFlags = rcspFrame(0x80, 0xFF, new byte[]{0, 0x22, 3, 1, 2, 3});
        expect(!EqWritePolicy.permitted(badFlags, true), "flags!=C0 应拒绝");
        // 截断帧
        expect(!EqWritePolicy.permitted(Arrays.copyOf(eq, eq.length - 1), true), "截断帧应拒绝");
        expect(!EqWritePolicy.permitted(null, true), "null 应拒绝");
        expect(!EqWritePolicy.permitted(new byte[0], true), "空应拒绝");
    }

    static byte[] rcspFrame(int flags, int op, byte[] body) {
        int n = body.length;
        byte[] f = new byte[8 + n];
        f[0] = (byte) 0xfe; f[1] = (byte) 0xdc; f[2] = (byte) 0xba;
        f[3] = (byte) flags; f[4] = (byte) op;
        f[5] = (byte) (n >> 8); f[6] = (byte) n;
        System.arraycopy(body, 0, f, 7, n);
        f[f.length - 1] = (byte) 0xef;
        return f;
    }

    static byte[] hexBytes(String s) {
        String[] parts = s.trim().split("\\s+");
        byte[] out = new byte[parts.length];
        for (int i = 0; i < parts.length; i++) out[i] = (byte) Integer.parseInt(parts[i], 16);
        return out;
    }
}
