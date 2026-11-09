import cn.felix021.h3squery.UpgradeGate;
import cn.felix021.h3squery.DeviceConfig;
import cn.felix021.h3squery.UpgradePolicy;
import java.util.function.Consumer;

public class UpgradeGateTest {
    static UpgradeGate.Snapshot snapshot() {
        UpgradeGate.Snapshot s = new UpgradeGate.Snapshot();
        s.address=DeviceConfig.ADDRESS; s.pid=16764; s.vid=7250; s.uid=7250; s.version=280;
        s.battery=100; s.doubleBackup=true; s.offset=0; s.length=0; s.eligibility=0; return s;
    }
    static UpgradeGate prepared() {
        UpgradeGate g = new UpgradeGate(); g.preparing(); g.prepared(snapshot(), UpgradeGate.SHA, UpgradeGate.FILE_BYTES, "nonce", 1000); return g;
    }
    static void rejected(Runnable r) { try { r.run(); } catch (IllegalStateException e) { return; } throw new AssertionError("not rejected"); }
    static void badSnapshot(Consumer<UpgradeGate.Snapshot> mutation) {
        UpgradeGate.Snapshot s = snapshot(); mutation.accept(s);
        rejected(() -> UpgradeGate.validate(s, UpgradeGate.SHA, UpgradeGate.FILE_BYTES));
        UpgradeGate g=prepared(); g.authorize("nonce", 2000);
        rejected(() -> g.begin(s, UpgradeGate.SHA, UpgradeGate.FILE_BYTES));
        if (g.armed()) throw new AssertionError("invalid snapshot armed");
    }
    public static void main(String[] args) {
        rejected(() -> new UpgradeGate().begin(snapshot(), UpgradeGate.SHA, UpgradeGate.FILE_BYTES));
        UpgradeGate g=prepared(); rejected(() -> g.authorize("wrong", 2000));
        g.authorize("nonce", 2000); rejected(() -> g.authorize("nonce", 2000));
        if (g.armed()) throw new AssertionError("authorization skipped recheck");
        g.begin(snapshot(), UpgradeGate.SHA, UpgradeGate.FILE_BYTES);
        g.reconnecting(); g.reconnected(); g.succeed();
        if (g.armed()) throw new AssertionError("terminal state remained armed");
        rejected(() -> g.authorize("nonce", 3000));
        UpgradeGate stale=prepared(); rejected(() -> stale.authorize("nonce", 121001));
        if (stale.armed()) throw new AssertionError("expired approval armed");
        badSnapshot(s -> s.pid=1); badSnapshot(s -> s.vid=1); badSnapshot(s -> s.uid=1);
        badSnapshot(s -> s.address=null); badSnapshot(s -> s.version=281);
        rejected(() -> UpgradeGate.validate(snapshot(), "d1f7a067b169285d23457f8c182a8626bc75c01d679f2846a1f7c5e84b9b752d", UpgradeGate.FILE_BYTES));
        badSnapshot(s -> s.doubleBackup=false); badSnapshot(s -> s.needBootloader=true); badSnapshot(s -> s.mandatory=true);
        badSnapshot(s -> s.battery=0); badSnapshot(s -> s.battery=101); badSnapshot(s -> s.offset=1);
        badSnapshot(s -> s.length=1); badSnapshot(s -> s.eligibility=1);
        rejected(() -> UpgradeGate.validate(snapshot(), "wrong", UpgradeGate.FILE_BYTES));
        rejected(() -> UpgradeGate.validate(snapshot(), UpgradeGate.SHA, 1));
        for (int op=0; op<256; op++) {
            byte[] f=QueryPolicyTest.frame(op);
            boolean query=op==212 || op==225;
            if (UpgradePolicy.permitted(f, false)!=query) throw new AssertionError("unarmed op="+op);
            if (UpgradePolicy.permitted(f, true)!=(query || op==227 || op==228 || op==230)) throw new AssertionError("armed op="+op);
        }
        byte[] block=QueryPolicyTest.frame(229, (byte)0, (byte)1); block[3]=0; block[7]=0;
        if (UpgradePolicy.permitted(block,false) || !UpgradePolicy.permitted(block,true)) throw new AssertionError("firmware block gating");
        byte[] malformed=java.util.Arrays.copyOf(block,block.length-1);
        if (UpgradePolicy.permitted(malformed,true)) throw new AssertionError("malformed block");
        System.out.println("UpgradeGateTest passed: identity, file, expiry, replay, fresh recheck, terminal states and armed transport");
    }
}
