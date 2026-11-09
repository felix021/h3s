package cn.felix021.h3squery;

/** Allow complete read/query commands only, before fragmenting them into GATT writes. */
public final class QueryPolicy {
    public static boolean permitted(byte[] data) {
        if (data == null) return false;
        int pos = 0;
        while (pos < data.length) {
            if (data.length - pos < 9 || data[pos] != (byte)0xfe || data[pos+1] != (byte)0xdc || data[pos+2] != (byte)0xba || data[pos+3] != (byte)0xc0) return false;
            int op = data[pos+4] & 255, size = ((data[pos+5]&255)<<8) | (data[pos+6]&255);
            if (size < 1 || pos + size + 8 > data.length || data[pos+size+7] != (byte)0xef) return false;
            boolean info = op == 3 && size == 6 && data[pos+8] == -1 && data[pos+9] == -1 && data[pos+10] == -1 && data[pos+11] == -1 && data[pos+12] == 0;
            boolean md5 = op == 212 && size == 1;
            boolean offset = op == 225 && size == 1;
            boolean battery = op == 255 && size == 4 && data[pos+8] == (byte)0xfe && data[pos+9] == 1 && (data[pos+10] == 47 || data[pos+10] == 20 || data[pos+10] == 16 || data[pos+10] == 23);
            boolean ancCycle = op == 193 && size == 5 && data[pos+8] == 0 && data[pos+9] == 0 && data[pos+10] == 4 && data[pos+11] == 0;
            boolean eligibility = op == 226 && size == 2 && data[pos+8] == 0;
            if (!info && !md5 && !offset && !battery && !eligibility && !ancCycle) return false;
            pos += size + 8;
        }
        return pos > 0;
    }
}
