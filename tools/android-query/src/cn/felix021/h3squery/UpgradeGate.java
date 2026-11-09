package cn.felix021.h3squery;

/** In-memory, single-use approval. A process restart never restores authorization. */
public final class UpgradeGate {
    public static final String SHA = "b79cb3a6f8702eb9e1d12eaf181a968f6b5848a66c051b2c792c39aa8b42fa1b";
    public static final int EXPECTED_VERSION = 280;
    public static final String DESCRIPTION = "ANC 按键只轮换降噪/通透；保留提示音 -10 dB 参数";
    public static final int FILE_BYTES = 1326848;
    public static final long MAX_AGE_MS = 120000;
    public enum State { IDLE, PREPARING, PREPARED, RECHECKING, FLASHING, RECONNECTING, SUCCEEDED, FAILED }
    public static final class Snapshot {
        public int pid, vid, uid, version, battery = -1, offset = -1, length = -1, eligibility = -1;
        public boolean doubleBackup, needBootloader, mandatory;
        public String address;
    }
    private volatile State state = State.IDLE;
    private String nonce;
    private long preparedAt;
    public State state() { return state; }
    public boolean armed() { return state == State.FLASHING || state == State.RECONNECTING; }
    public void preparing() {
        if (armed() || state == State.RECHECKING) throw new IllegalStateException("升级进行中");
        nonce = null; state = State.PREPARING;
    }
    public static void validate(Snapshot s, String sha, int bytes) {
        if (!SHA.equals(sha) || bytes != FILE_BYTES) throw new IllegalStateException("文件不是已复核的 ANC 两态候选");
        if (s == null || !DeviceConfig.ADDRESS.equals(s.address) || s.pid != 16764 || s.vid != 7250 || s.uid != 7250) throw new IllegalStateException("设备身份不匹配");
        if (s.version != EXPECTED_VERSION) throw new IllegalStateException("设备版本不是已查询的 0.1.1.8");
        if (!s.doubleBackup || s.needBootloader || s.mandatory) throw new IllegalStateException("仅支持本次确认的双备份正常模式");
        if (s.battery <= 0 || s.battery > 100) throw new IllegalStateException("QCY 电量读值无效");
        if (s.offset != 0 || s.length != 0 || s.eligibility != 0) throw new IllegalStateException("设备升级条件未通过");
    }
    public void prepared(Snapshot s, String sha, int bytes, String token, long now) {
        if (state != State.PREPARING) throw new IllegalStateException("未处于准备阶段");
        validate(s, sha, bytes);
        if (token == null || token.isEmpty()) throw new IllegalArgumentException("缺少本次确认标识");
        nonce = token; preparedAt = now; state = State.PREPARED;
    }
    public void authorize(String token, long now) {
        if (state != State.PREPARED || nonce == null || !nonce.equals(token)) throw new IllegalStateException("升级计划或确认标识已失效");
        if (now < preparedAt || now - preparedAt > MAX_AGE_MS) { fail(); throw new IllegalStateException("计划超过 2 分钟，请重新准备"); }
        nonce = null; state = State.RECHECKING;
    }
    public void begin(Snapshot s, String sha, int bytes) {
        if (state != State.RECHECKING) throw new IllegalStateException("没有本次刷写确认");
        validate(s, sha, bytes); state = State.FLASHING;
    }
    public void reconnecting() {
        if (!armed()) throw new IllegalStateException("未在升级");
        state = State.RECONNECTING;
    }
    public void reconnected() {
        if (state != State.RECONNECTING) throw new IllegalStateException("未在回连");
        state = State.FLASHING;
    }
    public void succeed() {
        if (!armed()) throw new IllegalStateException("未在升级");
        nonce = null; state = State.SUCCEEDED;
    }
    public void fail() { nonce = null; state = State.FAILED; }
}
