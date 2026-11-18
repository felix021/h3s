import cn.felix021.h3squery.BleDiscoveryPolicy;

/**
 * BleDiscoveryPolicy 纯策略单测（路 BLEDISC）：名字匹配规则（前缀/全匹配/大小写/null 安全）、
 * 注入地址优先（offer 立即选定、大写归一）、多名字候选累积与最强选取、reset 语义。
 * transport 层的集成路径（超时文案/settle 时序/回退直连）在 TransportContractTest。
 */
public class BleDiscoveryTest {
    static void expect(boolean b, String msg) { if (!b) throw new AssertionError(msg); }

    public static void main(String[] args) {
        nameMatching();
        preferredAddress();
        candidateSelection();
        defaults();
        System.out.println("BleDiscoveryTest passed: 名字匹配（前缀/全匹配/大小写/null）+ 注入地址优先"
            + "（立即选定/大写归一）+ 多候选取最强 + reset 语义。");
    }

    /** 名字匹配规则：前缀模式容忍尾缀、忽略大小写；全匹配模式严格；null/空永不匹配。 */
    static void nameMatching() {
        expect(BleDiscoveryPolicy.nameMatches("QCY H3S", "QCY H3S", false), "前缀：全同名应匹配");
        expect(BleDiscoveryPolicy.nameMatches("QCY H3S 阿尔法", "QCY H3S", false), "前缀：带尾缀应匹配");
        expect(BleDiscoveryPolicy.nameMatches("qcy h3s", "QCY H3S", false), "前缀：大小写不敏感");
        expect(BleDiscoveryPolicy.nameMatches("QCY H3S", "qcy h3s", false), "前缀：目标小写也应匹配");
        expect(!BleDiscoveryPolicy.nameMatches("QCY", "QCY H3S", false), "前缀：目标更长不应匹配");
        expect(!BleDiscoveryPolicy.nameMatches("QCY H4S", "QCY H3S", false), "前缀：同名前缀不同型号不应匹配");
        expect(!BleDiscoveryPolicy.nameMatches("XCY H3S", "QCY H3S", false), "前缀：首字不同不应匹配");
        expect(!BleDiscoveryPolicy.nameMatches(null, "QCY H3S", false), "null 广播名不应匹配");
        expect(!BleDiscoveryPolicy.nameMatches("", "QCY H3S", false), "空广播名不应匹配");
        expect(BleDiscoveryPolicy.nameMatches("QCY H3S", "QCY H3S", true), "全匹配：同名应匹配");
        expect(BleDiscoveryPolicy.nameMatches("qcy h3s", "QCY H3S", true), "全匹配：大小写不敏感");
        expect(!BleDiscoveryPolicy.nameMatches("QCY H3S 阿尔法", "QCY H3S", true), "全匹配：带尾缀不应匹配");
        // 实例口径
        BleDiscoveryPolicy p = new BleDiscoveryPolicy(null, "QCY H3S", false);
        expect(p.nameMatches("QCY H3S Pro"), "实例默认应为前缀匹配");
        expect(p.exactMatch() == false, "默认非全匹配");
    }

    /** 注入地址：offer 立即选定（返回大写归一形），与名字无关；isPreferred 大小写不敏感。 */
    static void preferredAddress() {
        BleDiscoveryPolicy p = new BleDiscoveryPolicy("aa:bb:cc:dd:ee:ff", "QCY H3S", false);
        expect("AA:BB:CC:DD:EE:FF".equals(p.preferredAddress()), "注入地址应大写归一");
        expect(p.isPreferred("AA:BB:CC:DD:EE:FF") && p.isPreferred("aa:bb:cc:dd:ee:ff"),
            "isPreferred 应大小写不敏感");
        expect(p.offer("完全无关的名字", "AA:BB:CC:DD:EE:FF", -80) != null,
            "注入地址候选应立即选定（名字无关）");
        expect(p.offer(null, "aa:bb:cc:dd:ee:ff", -60).equals("AA:BB:CC:DD:EE:FF"),
            "小写地址候选应返回归一大写形");
        BleDiscoveryPolicy none = new BleDiscoveryPolicy(null, "QCY H3S", false);
        expect(none.preferredAddress() == null, "未注入地址应为 null");
        expect(!none.isPreferred("AA:BB:CC:DD:EE:FF"), "无注入地址时 isPreferred 恒 false");
        expect(none.offer("QCY H3S", "AA:BB:CC:DD:EE:FF", -50) == null,
            "名字匹配候选不应立即选定（等 settle）");
    }

    /** 多名字候选：同址保留最强 RSSI；strongest 取信号最强；非匹配名不进候选。 */
    static void candidateSelection() {
        BleDiscoveryPolicy p = new BleDiscoveryPolicy(null, "QCY H3S", false);
        expect(p.offer("QCY H3S", "A1", -70) == null && p.candidateCount() == 1, "首个名字候选入池");
        p.offer("QCY H3S", "A1", -60);                                  // 同址更强 → 刷新
        p.offer("QCY H3S", "A1", -90);                                  // 同址更弱 → 不降
        p.offer("QCY H3S", "A2", -50);                                  // 第二台同名设备
        p.offer("QCY H4S", "A3", -10);                                  // 名字不匹配 → 不入池
        p.offer(null, "A4", -10);                                       // 无名 → 不入池
        expect(p.candidateCount() == 2, "同址去重后应为 2 个候选: " + p.candidateCount());
        expect("A2".equals(p.strongest()), "应选信号最强者（A2/-50）");
        expect(p.offer("QCY H3S", "A5", -127) == null && "A2".equals(p.strongest()),
            "弱候选不应抢走最强");
        p.reset();
        expect(p.candidateCount() == 0 && p.strongest() == null, "reset 后无候选");
        p.offer("QCY H3S", "A6", -40);                                  // reset 后配置仍有效
        expect("A6".equals(p.strongest()), "reset 只清候选不清配置");
    }

    /** 构造缺省：null/空名字回落 DEFAULT_NAME；空串地址视为未注入。 */
    static void defaults() {
        BleDiscoveryPolicy p = new BleDiscoveryPolicy("", null, false);
        expect(BleDiscoveryPolicy.DEFAULT_NAME.equals(p.name()), "null 名字应回落默认广播名");
        expect(p.preferredAddress() == null, "空串地址=未注入");
        expect(BleDiscoveryPolicy.DEFAULT_NAME.equals("QCY H3S"), "默认广播名应为 QCY H3S");
    }
}
