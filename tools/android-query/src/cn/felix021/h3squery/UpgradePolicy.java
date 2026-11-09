package cn.felix021.h3squery;

/** Only the selected SDK's BLE OTA commands may leave an explicitly armed session. */
public final class UpgradePolicy {
    public static boolean permitted(byte[] data, boolean armed) {
        if (!armed) return QueryPolicy.permitted(data);
        if (data == null || data.length == 0) return false;
        int p = 0;
        while (p < data.length) {
            if (data.length-p < 9 || data[p] != (byte)0xfe || data[p+1] != (byte)0xdc || data[p+2] != (byte)0xba) return false;
            int flags = data[p+3]&255, op = data[p+4]&255, n = ((data[p+5]&255)<<8) | (data[p+6]&255);
            if (n < 1 || p+n+8 > data.length || data[p+n+7] != (byte)0xef) return false;
            byte[] frame = java.util.Arrays.copyOfRange(data, p, p+n+8);
            boolean read = QueryPolicy.permitted(frame);
            boolean mode = flags == 0xc0 && (op == 227 || op == 228 || op == 230) && n == 1;
            boolean block = flags == 0 && op == 229 && n >= 2 && n <= 542 && data[p+7] == 0;
            boolean reboot = flags == 0xc0 && op == 231 && n == 2 && data[p+8] == 0;
            boolean size = flags == 0xc0 && op == 232 && (n == 5 || n == 9);
            boolean sizeReply = flags == 0 && op == 232 && n == 2 && data[p+7] == 0;
            boolean mtuReply = flags == 0 && op == 209 && n == 4 && data[p+7] == 0;
            boolean stopAdv = flags == 0xc0 && op == 195 && n == 2 && data[p+8] == 0;
            if (!read && !mode && !block && !reboot && !size && !sizeReply && !mtuReply && !stopAdv) return false;
            p += n+8;
        }
        return true;
    }
}
