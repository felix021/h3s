import cn.felix021.h3squery.QueryPolicy;
import java.util.Arrays;

public class QueryPolicyTest {
    static byte[] frame(int op, byte... payload) {
        int size = payload.length + 1;
        byte[] f = new byte[size + 8];
        f[0] = (byte)0xfe; f[1] = (byte)0xdc; f[2] = (byte)0xba; f[3] = (byte)0xc0;
        f[4] = (byte)op; f[5] = (byte)(size >> 8); f[6] = (byte)size; f[7] = 42;
        System.arraycopy(payload, 0, f, 8, payload.length); f[f.length-1] = (byte)0xef; return f;
    }
    static void expect(boolean yes, byte[] data) {
        if (QueryPolicy.permitted(data) != yes) throw new AssertionError(Arrays.toString(data));
    }
    public static void main(String[] args) {
        for (int op = 0; op < 256; op++) expect(op == 212 || op == 225, frame(op));
        byte[][] good = {frame(3, (byte)-1, (byte)-1, (byte)-1, (byte)-1, (byte)0), frame(212), frame(225), frame(226, (byte)0), frame(255, (byte)0xfe, (byte)1, (byte)47), frame(255, (byte)0xfe, (byte)1, (byte)20), frame(255, (byte)0xfe, (byte)1, (byte)16)};
        for (byte[] f : good) {
            expect(true, f);
            for (int n = 0; n < f.length; n++) expect(false, Arrays.copyOf(f, n));
            byte[] bad = f.clone(); bad[bad.length-1] = 0; expect(false, bad);
            bad = f.clone(); bad[3] = 0; expect(false, bad);
            bad = Arrays.copyOf(f, f.length+1); expect(false, bad);
            byte[] mixed = Arrays.copyOf(f, f.length+9); System.arraycopy(frame(227), 0, mixed, f.length, 9); expect(false, mixed);
        }
        expect(false, null); expect(false, new byte[0]);
        expect(false, frame(255, (byte)47, (byte)1, (byte)100)); // QCY setter-shaped command
        expect(false, frame(255, (byte)20, (byte)4, (byte)0, (byte)0, (byte)0, (byte)0)); // power setting remains blocked
        expect(false, frame(255, (byte)16, (byte)1, (byte)0)); // sleep setting remains blocked
        expect(true, frame(193, (byte)0, (byte)0, (byte)4, (byte)0)); // only ANC mode-list bit 10
        expect(false, frame(193, (byte)-1, (byte)-1, (byte)-1, (byte)-1));
        expect(false, frame(192, (byte)5, (byte)10, (byte)0, (byte)0, (byte)0, (byte)6)); // setting remains blocked
        expect(true, frame(255, (byte)0xfe, (byte)1, (byte)23)); // current QCY ANC mode, read only
        expect(false, frame(255, (byte)0xfe, (byte)1, (byte)48)); // other custom queries remain blocked
        expect(false, frame(226, (byte)1)); // SPP switch not permitted
        byte[] joined = new byte[good[0].length+good[4].length];
        System.arraycopy(good[0], 0, joined, 0, good[0].length); System.arraycopy(good[4], 0, joined, good[0].length, good[4].length); expect(true, joined);
        System.out.println("QueryPolicyTest passed: exhaustive opcodes, truncation, malformed and mixed frames, restricted payloads");
    }
}
