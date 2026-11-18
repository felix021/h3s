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
        badSnapshot(mutation, UpgradeGate.SHA);
    }
    static void badSnapshot(Consumer<UpgradeGate.Snapshot> mutation, String sha) {
        UpgradeGate.Snapshot s = snapshot(); mutation.accept(s);
        rejected(() -> UpgradeGate.validate(s, sha, UpgradeGate.FILE_BYTES));
        UpgradeGate g=new UpgradeGate(); g.preparing(); g.prepared(snapshot(), sha, UpgradeGate.FILE_BYTES, "nonce", 1000);
        g.authorize("nonce", 2000);
        rejected(() -> g.begin(s, sha, UpgradeGate.FILE_BYTES));
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
        badSnapshot(s -> s.version=281);
        // 地址核对（路 BLEDISC）：未注入地址（默认发现模式）跳过；注入了则必须精确匹配。
        if (!UpgradeGate.addressMatches("", null) || !UpgradeGate.addressMatches("", "AA:BB:CC:DD:EE:FF")
                || !UpgradeGate.addressMatches(null, "AA:BB:CC:DD:EE:FF"))
            throw new AssertionError("empty config should skip address check");
        if (!UpgradeGate.addressMatches("AA:BB:CC:DD:EE:FF", "AA:BB:CC:DD:EE:FF"))
            throw new AssertionError("matching address should pass");
        if (UpgradeGate.addressMatches("AA:BB:CC:DD:EE:FF", "BB:BB:BB:BB:BB:BB")
                || UpgradeGate.addressMatches("AA:BB:CC:DD:EE:FF", null))
            throw new AssertionError("mismatched/null address must be rejected");
        rejected(() -> UpgradeGate.validate(snapshot(), "d1f7a067b169285d23457f8c182a8626bc75c01d679f2846a1f7c5e84b9b752d", UpgradeGate.FILE_BYTES));
        rejected(() -> UpgradeGate.validate(snapshot(), "c8dae5233678102aac152f0ff94952edea83c6eeca6afaf216db43226d0b6696", UpgradeGate.FILE_BYTES));
        rejected(() -> UpgradeGate.validate(snapshot(), "516aa05559690be1e1e266f03108ba0ddeb35a4e8b041be336f325573f1ac44d", UpgradeGate.FILE_BYTES));
        badSnapshot(s -> s.doubleBackup=false); badSnapshot(s -> s.needBootloader=true); badSnapshot(s -> s.mandatory=true);
        badSnapshot(s -> s.battery=0); badSnapshot(s -> s.battery=101); badSnapshot(s -> s.offset=1);
        badSnapshot(s -> s.length=1); badSnapshot(s -> s.eligibility=1);
        rejected(() -> UpgradeGate.validate(snapshot(), "wrong", UpgradeGate.FILE_BYTES));
        rejected(() -> UpgradeGate.validate(snapshot(), UpgradeGate.SHA, 1));
        // —— 固定候选注册表（0.7.3 固件 tab）：sha 钉死门从单镜像扩为清单，语义不放宽 ——
        final String hal5 = "9ad0f0881a7996cb4dea81477d8731aa9b4db69a6f352603118663d68eda5465";
        final String official = "65cafc7f9a7545c5c30701d141c77db334e37065589e42c51306132c94c879af";
        UpgradeGate.Candidate b79 = UpgradeGate.candidateFor(UpgradeGate.SHA, UpgradeGate.FILE_BYTES);
        UpgradeGate.Candidate full = UpgradeGate.candidateFor(hal5, UpgradeGate.FILE_BYTES);
        if (b79 == null || !"anc_restore_tested".equals(b79.kind)) throw new AssertionError("b79 应命中注册表");
        if (full == null || !"hearing_aid_full_mpo95".equals(full.kind)) throw new AssertionError("HAL5 应命中注册表");
        if (full.version != 280 || full.bytes != 1326848) throw new AssertionError("HAL5 候选元数据错误");
        // HAL5 走完整准备→授权→复查→刷写链（合法候选过门）
        UpgradeGate hal5Gate = new UpgradeGate(); hal5Gate.preparing();
        hal5Gate.prepared(snapshot(), hal5, UpgradeGate.FILE_BYTES, "nonce", 1000);
        hal5Gate.authorize("nonce", 2000);
        hal5Gate.begin(snapshot(), hal5, UpgradeGate.FILE_BYTES);
        hal5Gate.reconnecting(); hal5Gate.reconnected(); hal5Gate.succeed();
        UpgradeGate.validate(snapshot(), hal5, UpgradeGate.FILE_BYTES);
        // 非法拒绝：官方原版 / r2 / 未知 sha / null / sha 对但长度错 / 长度对但 sha 错
        if (UpgradeGate.candidateFor(official, UpgradeGate.FILE_BYTES) != null) throw new AssertionError("官方原版不得进候选门");
        if (UpgradeGate.candidateFor(hal5, UpgradeGate.FILE_BYTES - 1) != null) throw new AssertionError("sha 命中但长度不符必须拒绝（文件选择路径）");
        if (UpgradeGate.candidateFor(UpgradeGate.SHA, UpgradeGate.FILE_BYTES + 1) != null) throw new AssertionError("b79 长度不符必须拒绝");
        if (UpgradeGate.candidateFor(null, UpgradeGate.FILE_BYTES) != null) throw new AssertionError("null sha 拒绝");
        if (UpgradeGate.candidateFor("wrong", 1326848) != null) throw new AssertionError("未知 sha 拒绝");
        rejected(() -> UpgradeGate.validate(snapshot(), official, UpgradeGate.FILE_BYTES));
        rejected(() -> UpgradeGate.validate(snapshot(), hal5, UpgradeGate.FILE_BYTES - 1));
        rejected(() -> UpgradeGate.validate(snapshot(), UpgradeGate.SHA, UpgradeGate.FILE_BYTES - 1));
        // 坏快照对 HAL5 同样拒（身份/版本/电量/升级条件不因候选而放宽）
        badSnapshot(s -> s.version=281, hal5);
        badSnapshot(s -> s.battery=0, hal5);
        // 允许清单文案：两条 sha 都在（页面拒绝提示与门同源）
        if (!UpgradeGate.allowedShaList().contains(UpgradeGate.SHA)
                || !UpgradeGate.allowedShaList().contains(hal5))
            throw new AssertionError("允许清单必须包含全部钉死候选");
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
        System.out.println("UpgradeGateTest passed: identity, file, expiry, replay, fresh recheck, terminal states, armed transport and pinned-candidate registry (b79+HAL5)");
    }
}
