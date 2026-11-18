package cn.felix021.h3squery;

/** In-memory, single-use approval. A process restart never restores authorization. */
public final class UpgradeGate {
    public static final String SHA = "b79cb3a6f8702eb9e1d12eaf181a968f6b5848a66c051b2c792c39aa8b42fa1b";
    public static final int EXPECTED_VERSION = 280;
    public static final String DESCRIPTION = "恢复已实测的 ANC 降噪/通透两态版本；移除 EQ 实验补丁";
    public static final int FILE_BYTES = 1326848;
    public static final long MAX_AGE_MS = 120000;

    /**
     * 固定候选注册表（0.7.3 固件 tab）：仍是不放宽的 sha 钉死审批门——只允许推送
     * 逐字节摘要钉死的镜像，文件选择器命中不了其中任何一个就明确拒绝（AGENTS 红线：
     * 不开任意固件推送）。两条候选都是已在真机刷过/验收过的版本：
     *   - b79：已实测的 ANC 两态恢复镜像（2026-11-09 设备验收，此前 OTA 链路实测载体）；
     *   - HAL5 full MPO95：助听全功能合一候选（docs/hearing-aid-l5-full.md §6，2026-11-18
     *     已刷入真机，docs/hearing-aid-lnoise-diagnosis.md 复核 sha 一致）。
     * 两条都是 0.1.1.8（版本码 280）/1326848 字节：同底镜像、改动全在原 UFW 结构内。
     */
    public static final class Candidate {
        public final String sha, kind, description;
        public final int bytes, version;
        Candidate(String sha, int bytes, int version, String kind, String description) {
            this.sha = sha; this.bytes = bytes; this.version = version;
            this.kind = kind; this.description = description;
        }
    }
    public static final Candidate[] REGISTRY = {
        new Candidate(SHA, FILE_BYTES, EXPECTED_VERSION, "anc_restore_tested",
            DESCRIPTION + "（0.1.1.8，2026-11-09 设备验收）"),
        new Candidate("9ad0f0881a7996cb4dea81477d8731aa9b4db69a6f352603118663d68eda5465",
            FILE_BYTES, EXPECTED_VERSION, "hearing_aid_full_mpo95",
            "助听全功能合一候选（L1 双击开关 + L2 兜底 MPO=95 + L3/L4 key30/31 热更；"
                + "2026-11-18 已刷入真机，docs/hearing-aid-l5-full.md）"),
    };
    /** sha+长度同时命中才放行（固定候选门查询口；OtaWebBridge 文件检查/服务侧复核共用）。 */
    public static Candidate candidateFor(String sha, int bytes) {
        if (sha == null) return null;
        for (Candidate c : REGISTRY) if (c.sha.equals(sha) && c.bytes == bytes) return c;
        return null;
    }
    /** 拒绝文案用的允许清单（明确列出，不模糊提示）。 */
    public static String allowedShaList() {
        StringBuilder list = new StringBuilder();
        for (Candidate c : REGISTRY) {
            if (list.length() > 0) list.append("、");
            list.append(c.sha);
        }
        return list.toString();
    }
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

    /**
     * 地址核对：构建注入了地址（H3S_BLE_ADDRESS）才比对（路 BLEDISC 起地址默认空=扫描发现模式）。
     * 空配置时跳过不会打开「刷错设备」的口子——升级链路在 UpgradeService 侧另有
     * 「仅连注入地址」的 fail-closed 门（未注入地址时该门恒拒绝）。
     */
    public static boolean addressMatches(String expected, String actual) {
        return expected == null || expected.isEmpty() || expected.equals(actual);
    }
    public void preparing() {
        if (armed() || state == State.RECHECKING) throw new IllegalStateException("升级进行中");
        nonce = null; state = State.PREPARING;
    }
    public static void validate(Snapshot s, String sha, int bytes) {
        Candidate plan = candidateFor(sha, bytes);
        if (plan == null)
            throw new IllegalStateException("文件不是固定候选清单里的镜像（允许：" + allowedShaList() + "）");
        if (s == null || !addressMatches(DeviceConfig.ADDRESS, s.address) || s.pid != 16764 || s.vid != 7250 || s.uid != 7250) throw new IllegalStateException("设备身份不匹配");
        if (s.version != plan.version) throw new IllegalStateException("设备版本不是已查询的 0.1.1.8");
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
