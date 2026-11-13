package cn.felix021.h3squery;

import java.util.Arrays;

/**
 * RCSP 自定义命令（opcode 0xFF）setter 放行策略，类比 UpgradePolicy 的 armed 状态。
 *
 * 只在 armed 时放行「形状完全匹配」的 setter 帧（docs/cmd-0x17-0x1d-layout.md 路 P 钉死）：
 *   - 0x22 EQ：data_len%7==3（3B 头 + 每段 7B，docs/eq-cmd-0x22-layout.md 路 H）；
 *   - 0x17 ANC：data_len==3，data=`mode scene strength`，mode 1..3（QCY 线协议降噪/正常/通透）；
 *   - 0x1d 提示音音量：data_len==2，data=`value 占位`，value∈{3,6,8}（固件只读 data[0]，
 *     第二字节忽略；档位 3/6/8 是控制面板层约束，固件接收层不钳）。
 * 不开任意字节写总开关（AGENTS 红线）。未 armed 时退回 QueryPolicy（只读帧），所有写一律拒绝。
 *
 * 纯逻辑判定，不接真机传输层——传输接线需真机联调，本路不做。
 */
public final class EqWritePolicy {
    public static final int CMD_ANC = 0x17;            // ANC 模式切换 cmdid
    public static final int CMD_PROMPT_VOLUME = 0x1d;  // 提示音音量 cmdid
    /** 提示音音量合法档位（控制面板 3/6/8=最小/中等/最大；固件接收层不钳，白名单从严）。 */
    public static final int[] PROMPT_VOLUME_LEVELS = {3, 6, 8};

    private EqWritePolicy() {}

    public static boolean permitted(byte[] data, boolean armed) {
        if (!armed) return QueryPolicy.permitted(data);
        if (data == null || data.length == 0) return false;
        int p = 0;
        while (p < data.length) {
            if (data.length - p < 9 || data[p] != (byte) 0xfe || data[p + 1] != (byte) 0xdc || data[p + 2] != (byte) 0xba) return false;
            int flags = data[p + 3] & 255, op = data[p + 4] & 255, n = ((data[p + 5] & 255) << 8) | (data[p + 6] & 255);
            if (n < 1 || p + n + 8 > data.length || data[p + n + 7] != (byte) 0xef) return false;
            byte[] frame = Arrays.copyOfRange(data, p, p + n + 8);
            boolean read = QueryPolicy.permitted(frame);
            boolean setter = isSetter(frame, flags, op, n);
            if (!read && !setter) return false;
            p += n + 8;
        }
        return true;
    }

    /** 形状完全匹配的 setter：flags=C0、opcode=0xFF、cmdid∈{0x17,0x1d,0x22} 且各自载荷形状匹配。 */
    private static boolean isSetter(byte[] frame, int flags, int op, int n) {
        if (flags != EqFrame.FLAGS_CMD || op != EqFrame.OPCODE_CUSTOM || n < 5) return false; // body 至少 SN+cmdid+data_len+2B data（0x1d 最小形状）
        int cmdId = frame[8] & 255;                              // body[1] = cmdid
        int dataLen = frame[9] & 255;                            // body[2] = data_len
        if (dataLen != n - 3) return false;
        switch (cmdId) {
            case EqFrame.CMD_ID:            // 0x22 EQ：3B 头 + 每段 7B
                return dataLen % 7 == 3;
            case CMD_ANC:                   // 0x17 ANC：data=`mode scene strength`，mode 1..3
                return dataLen == 3 && (frame[10] & 255) >= 1 && (frame[10] & 255) <= 3;
            case CMD_PROMPT_VOLUME:         // 0x1d：data=`value 占位`（固件只读 data[0]），值∈{3,6,8}
                if (dataLen != 2) return false;
                int v = frame[10] & 255;
                for (int level : PROMPT_VOLUME_LEVELS) if (level == v) return true;
                return false;
            default:
                return false;
        }
    }
}
