package cn.felix021.h3squery;

import java.util.ArrayList;
import java.util.Arrays;
import java.util.List;

/**
 * RCSP 自定义命令 0x22（EQ 写）的组帧与反解析，字节布局照 docs/eq-cmd-0x22-layout.md（路 H 钉死）。
 *
 * 帧结构（路 CS docs/app-protocol.md §2.1）：
 *   FE DC BA | flags(0xC0) | opcode(0xFF) | len_BE16 | body | EF
 *   body = SN(1B) | cmdid(0x22) | data_len(1B) | data[data_len]
 *   data = 3B 头 + 每段 7B，data_len = 3 + 7*N
 *
 * 3B 头：[0]=EQ 类型(u8)、[1..2]=master gain(i16 小端，÷100 得 dB)；
 * 每段 7B：[0..1]=freq(u16 小端，Hz 直存)、[2..3]=gain(i16 小端，÷100 得 dB)、
 *          [4..5]=q(i16 小端，÷100)、[6]=iir_type(u8，255=未指定→固件兜底 band_pass=2)。
 *
 * 段内 gain/Q 字段顺序（2026-11-19 eqproto 字节级修正）：QCY 原生组帧在 DEX 0x5755EE 调
 * getGain() 写段 +2/+3、0x575632 调 getQ() 写段 +4/+5；固件 0x60251E6/0x60251DC 把两个数
 * 分别写运行时段结构 +8/+12（SDK 定义 float gain / float q）互证。0.9.1 曾把 +2/+3 当 Q、
 * +4/+5 当 gain 发反（「流行」预设 170Hz 段：固件读到 gain=+0.4dB、Q=−17），本版已换正。
 *
 * EQ 类型字节语义（路 EQFIX 重解 apply 0x06025118 钉死，docs/eq-cmd-0x22-layout.md §2.3；
 * 2026-11-19 codex 补钉官方预设/自定义实发取值，见 assets/index.html 预设表）：
 *   type==1  → 固件调 0x6024fe6("MusicEqBt",0) 装载自定义槽后直接返回，**载荷段被忽略**；
 *   type==10 → 空间音效开关路径（0x602262e），同样不进载荷解码循环；
 *   其余值   → 解码 3B 头+各段并应用到 DSP（0x602503a）——要下发曲线必用这类值。
 * 官方预设 type=面板 id（默认/流行/重低音/摇滚/柔和/古典=01..06、空间=0A）；自定义曲线
 * type=255(FF, 无历史) 或 128(80, 有历史)——两者都落在「解码载荷」分支。本类只定义
 * 自定义/槽/空间三个与编码无关的语义常量，官方预设 id 常量由页面预设表维护。
 *
 * 所有数值用整数表示：master gain/每段 gain 用 centidB(=dB×100)、q 用 ×100、freq 用 Hz，
 * 编码为小端 i16/u16——对齐固件「i16÷100 得 dB」「u16 直存 Hz」，不碰浮点、无舍入误差。
 *
 * 纯离线组帧，已单测；真机下发未验证（红线：不连真机、不刷机）。
 */
public final class EqFrame {
    public static final int CMD_ID = 0x22;         // 自定义命令 cmdid
    public static final int OPCODE_CUSTOM = 0xFF;  // RCSP opcode：自定义命令
    public static final int FLAGS_CMD = 0xC0;      // flags：命令需响应（EQ 写属 setter）
    public static final int MAX_BANDS = 10;        // 固件 apply 循环只应用前 10 段
    public static final int IIR_DEFAULT = 255;     // 255=未指定→固件兜底 band_pass(2)
    /** type=255（FF）：自定义 EQ 下发、无历史记录（原生 saveIndex∈{0,255} 且 lastUsedEQIndex==null）。 */
    public static final int TYPE_CUSTOM_NO_HISTORY = 255;
    /** type=128（80）：自定义 EQ 下发、已有历史记录（原生 saveIndex∈{0,255} 且 lastUsedEQIndex!=null）。 */
    public static final int TYPE_CUSTOM_HISTORY = 128;
    /** type=1：固件装载 MusicEqBt[0]（自定义槽）并忽略载荷段——不是「下发自定义曲线」的取值。 */
    public static final int TYPE_RECALL_SLOT = 1;
    /** type=10：空间音效路径。 */
    public static final int TYPE_SPATIAL = 10;

    /** EQ 段：freq/q/gain/iirType，全部整数。 */
    public static final class EqBand {
        public final int freq;     // 段中心频率，Hz，u16 范围 0..65535
        public final int q;        // Q 值 ×100，i16 范围
        public final int gain;     // 段增益 ×100（centidB），i16 范围
        public final int iirType;  // 0=high_pass/1=low_pass/2=band_pass/3=high_shelf/4=low_shelf，255=默认
        public EqBand(int freq, int q, int gain, int iirType) {
            this.freq = freq; this.q = q; this.gain = gain; this.iirType = iirType;
        }
    }

    /** 反解析结果：EQ 类型 + master gain + 段列表。 */
    public static final class Parsed {
        public final int eqType;
        public final int masterGainCentidB;
        public final List<EqBand> bands;
        Parsed(int eqType, int masterGainCentidB, List<EqBand> bands) {
            this.eqType = eqType; this.masterGainCentidB = masterGainCentidB; this.bands = bands;
        }
    }

    private EqFrame() {}

    private static void check(boolean ok, String msg) {
        if (!ok) throw new IllegalArgumentException(msg);
    }

    private static void checkI16(int v, String field) {
        check(v >= -32768 && v <= 32767, field + " 超出 i16 范围: " + v);
    }

    private static void checkBands(List<EqBand> bands) {
        check(bands != null, "段列表不能为 null");
        check(bands.size() <= MAX_BANDS, "段数超上限 " + MAX_BANDS + ": " + bands.size());
        for (EqBand b : bands) {
            check(b != null, "段不能为 null");
            check(b.freq >= 0 && b.freq <= 65535, "freq 超出 u16 范围: " + b.freq);
            checkI16(b.q, "q");
            checkI16(b.gain, "gain");
            check((b.iirType >= 0 && b.iirType <= 4) || b.iirType == IIR_DEFAULT,
                  "iir_type 非法（0..4 或 255）: " + b.iirType);
        }
    }

    /** 组 data：3B 头 + 每段 7B，不做 RCSP 包装。 */
    public static byte[] buildEqData(int eqType, int masterGainCentidB, List<EqBand> bands) {
        check(eqType >= 0 && eqType <= 255, "EQ 类型超出 u8 范围: " + eqType);
        checkI16(masterGainCentidB, "master gain");
        checkBands(bands);
        int n = bands.size();
        byte[] data = new byte[3 + 7 * n];
        data[0] = (byte) eqType;
        data[1] = (byte) masterGainCentidB;         // i16 低字节
        data[2] = (byte) (masterGainCentidB >> 8);  // i16 高字节（有符号）
        int p = 3;
        for (EqBand b : bands) {
            data[p + 0] = (byte) b.freq;
            data[p + 1] = (byte) (b.freq >> 8);
            data[p + 2] = (byte) b.gain;            // +2/+3=gain（QCY 原生 DEX 0x5755EE 先写 gain）
            data[p + 3] = (byte) (b.gain >> 8);
            data[p + 4] = (byte) b.q;               // +4/+5=Q（DEX 0x575632 后写 Q）
            data[p + 5] = (byte) (b.q >> 8);
            data[p + 6] = (byte) b.iirType;
            p += 7;
        }
        return data;
    }

    /** 组完整 RCSP 命令帧，SN 取 0。 */
    public static byte[] buildEqCommand(int eqType, int masterGainCentidB, List<EqBand> bands) {
        return buildEqCommand(0, eqType, masterGainCentidB, bands);
    }

    /** 组完整 RCSP 命令帧，显式 SN。 */
    public static byte[] buildEqCommand(int sn, int eqType, int masterGainCentidB, List<EqBand> bands) {
        byte[] data = buildEqData(eqType, masterGainCentidB, bands);
        int dataLen = data.length;          // 3 + 7N
        int bodyLen = 3 + dataLen;          // SN + cmdid + data_len + data
        byte[] frame = new byte[8 + bodyLen];
        frame[0] = (byte) 0xfe; frame[1] = (byte) 0xdc; frame[2] = (byte) 0xba;
        frame[3] = (byte) FLAGS_CMD;        // flags：命令需响应
        frame[4] = (byte) OPCODE_CUSTOM;    // opcode 0xFF
        frame[5] = (byte) (bodyLen >> 8);   // len_BE16 高字节
        frame[6] = (byte) bodyLen;          // len_BE16 低字节
        frame[7] = (byte) sn;               // body[0] = SN
        frame[8] = (byte) CMD_ID;           // body[1] = cmdid 0x22
        frame[9] = (byte) dataLen;          // body[2] = data_len
        System.arraycopy(data, 0, frame, 10, dataLen);
        frame[frame.length - 1] = (byte) 0xef;
        return frame;
    }

    private static int u16(byte lo, byte hi) { return (lo & 0xff) | ((hi & 0xff) << 8); }
    private static int i16(byte lo, byte hi) { return (short) ((lo & 0xff) | (hi << 8)); }

    /** 反解析 data（3B 头 + 每段 7B）回字段，往返自检用。 */
    public static Parsed parseEqData(byte[] data) {
        check(data != null && data.length >= 3, "data 太短");
        check((data.length - 3) % 7 == 0, "data 长度非法（需 3+7N）: " + data.length);
        int eqType = data[0] & 0xff;
        int masterGain = i16(data[1], data[2]);
        int n = (data.length - 3) / 7;
        List<EqBand> bands = new ArrayList<EqBand>(n);
        int p = 3;
        for (int i = 0; i < n; i++) {
            bands.add(new EqBand(u16(data[p], data[p + 1]), i16(data[p + 4], data[p + 5]),
                                 i16(data[p + 2], data[p + 3]), data[p + 6] & 0xff));
            p += 7;
        }
        return new Parsed(eqType, masterGain, bands);
    }

    /** 反解析完整 RCSP 命令帧，严格校验帧格式后抽出 data。 */
    public static Parsed parseEqCommand(byte[] frame) {
        check(frame != null && frame.length >= 10, "帧太短");
        check((frame[0] & 0xff) == 0xfe && (frame[1] & 0xff) == 0xdc && (frame[2] & 0xff) == 0xba,
              "帧头不是 FE DC BA");
        check((frame[4] & 0xff) == OPCODE_CUSTOM, "opcode 不是 0xFF");
        int bodyLen = ((frame[5] & 0xff) << 8) | (frame[6] & 0xff);
        check(frame.length == 8 + bodyLen, "len_BE16 与帧长度不符");
        check((frame[frame.length - 1] & 0xff) == 0xef, "帧尾不是 EF");
        check((frame[8] & 0xff) == CMD_ID, "cmdid 不是 0x22");
        int dataLen = frame[9] & 0xff;
        check(dataLen == bodyLen - 3, "data_len 与 len_BE16 不符");
        return parseEqData(Arrays.copyOfRange(frame, 10, 10 + dataLen));
    }
}
