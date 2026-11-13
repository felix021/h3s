package cn.felix021.h3squery;

import java.util.Arrays;

/**
 * EQ 写命令（0x22 setter）放行策略骨架，类比 UpgradePolicy 的 armed 状态。
 *
 * 只在 armed 时放行「形状完全匹配」的 0x22 setter 帧：RCSP 自定义命令（opcode 0xFF）
 * + cmdid 0x22 + data_len%7==3（即 data=3B 头+每段 7B）。不开任意字节写总开关（AGENTS 红线）。
 * 未 armed 时退回 QueryPolicy（只读帧），所有写一律拒绝。
 *
 * 纯逻辑判定，不接真机传输层——传输接线需真机联调，本路不做。
 */
public final class EqWritePolicy {
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
            boolean eq = isEqSetter(frame, flags, op, n);
            if (!read && !eq) return false;
            p += n + 8;
        }
        return true;
    }

    /** 形状完全匹配的 0x22 setter：flags=C0、opcode=0xFF、cmdid=0x22、data_len=n-3 且 %7==3。 */
    private static boolean isEqSetter(byte[] frame, int flags, int op, int n) {
        if (flags != EqFrame.FLAGS_CMD || op != EqFrame.OPCODE_CUSTOM || n < 6) return false; // body 至少 SN+cmdid+data_len+3B 头
        if ((frame[8] & 255) != EqFrame.CMD_ID) return false;   // body[1] = cmdid
        int dataLen = frame[9] & 255;                           // body[2] = data_len
        if (dataLen != n - 3) return false;
        return dataLen % 7 == 3;
    }
}
