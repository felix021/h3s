import cn.felix021.h3squery.BleDiscoveryPolicy;

/**
 * BleDiscoveryPolicy 纯策略单测（路 BLEDISC2，2026-11-19）：厂商 AD 解析（正例/字节序两种记法/
 * 截断载荷/异公司/异型号/AD 结构遍历/服务列表辅助 0xFEE8）+ 名字匹配规则（前缀/全匹配/大小写/
 * null 安全）+ 注入地址优先（offer 立即选定、大写归一）+ 多候选（名字/厂商）累积与最强选取 +
 * reset 语义。transport 层的集成路径（无名厂商候选连接/超时文案/settle 时序/连续失败终态）在
 * TransportContractTest。
 */
public class BleDiscoveryTest {
    static void expect(boolean b, String msg) { if (!b) throw new AssertionError(msg); }

    public static void main(String[] args) {
        vendorAdParsing();
        serviceListAuxiliary();
        vendorCandidates();
        nameMatching();
        preferredAddress();
        candidateSelection();
        defaults();
        System.out.println("BleDiscoveryTest passed: 厂商 AD 解析（正例/双字节序/截断/异公司/异型号）"
            + "+ 服务列表 0xFEE8 辅助 + 厂商候选累积 + 名字匹配（前缀/全匹配/大小写/null）"
            + "+ 注入地址优先（立即选定/大写归一）+ 多候选取最强 + reset 语义。");
    }

    // ================================ 广播记录构造工具 ================================

    /** 一个 AD 结构 [len][type][data…]（len 计 type 不计自身）。 */
    static byte[] ad(int type, byte[] data) {
        byte[] out = new byte[2 + data.length];
        out[0] = (byte) (1 + data.length);
        out[1] = (byte) type;
        System.arraycopy(data, 0, out, 2, data.length);
        return out;
    }

    static byte[] concat(byte[]... parts) {
        int n = 0;
        for (byte[] p : parts) n += p.length;
        byte[] out = new byte[n];
        int i = 0;
        for (byte[] p : parts) { System.arraycopy(p, 0, out, i, p.length); i += p.length; }
        return out;
    }

    /** 厂商 AD（0xFF）：公司 ID 两字节 + 厂商载荷 payloadLen 字节（头两字节=型号大端，len>=2 时）。 */
    static byte[] vendorAd(byte[] company, int model, int payloadLen) {
        byte[] payload = new byte[payloadLen];
        int from = 0;
        if (payloadLen >= 2) {
            payload[0] = (byte) (model >> 8);
            payload[1] = (byte) model;
            from = 2;
        }
        for (int i = from; i < payloadLen; i++) payload[i] = (byte) (0x30 + i);   // 填充字节内容无关
        return ad(0xFF, concat(company, payload));
    }

    /** 实测形状的 H3S scan record（诊断报告 2026-11-19）：服务列表 0x03(FEE8) + 厂商 AD(24B 载荷)。 */
    static byte[] h3sRecord(byte[] company) {
        return concat(
            ad(0x03, new byte[]{(byte) 0xE8, (byte) 0xFE}),        // 0xFEE8 小端
            vendorAd(company, 0x417C, 24),
            new byte[]{0, 0, 0});                                  // 尾部 0 填充
    }

    /** 诊断报告公司 ID 0x521C 的两种可能字节序（报告未钉原始序；VID 0x1C52 互为镜像，都认）。 */
    static final byte[] COMPANY_LE = {(byte) 0x1C, (byte) 0x52};
    static final byte[] COMPANY_BE = {(byte) 0x52, (byte) 0x1C};

    // ================================ 厂商 AD 解析 ================================

    /** 厂商 AD：正例（两种公司 ID 字节序 + 实测 24B 载荷 + 型号大端 0x417C）。 */
    static void vendorAdParsing() {
        expect(BleDiscoveryPolicy.vendorMatches(h3sRecord(COMPANY_LE)), "小端公司 ID 字节序应命中");
        expect(BleDiscoveryPolicy.vendorMatches(h3sRecord(COMPANY_BE)), "大端镜像字节序也应命中（报告未钉字节序）");
        expect(BleDiscoveryPolicy.vendorMatches(vendorAd(COMPANY_LE, 0x417C, 24)), "单个厂商 AD 结构应命中");
        // 截断载荷：实测载荷 24B，不足按截断拒绝（边界 23 拒绝、24 放行）
        expect(!BleDiscoveryPolicy.vendorMatches(vendorAd(COMPANY_LE, 0x417C, 23)), "23B 载荷=截断应拒绝");
        expect(!BleDiscoveryPolicy.vendorMatches(vendorAd(COMPANY_LE, 0x417C, 4)), "4B 载荷应拒绝");
        expect(!BleDiscoveryPolicy.vendorMatches(vendorAd(COMPANY_LE, 0x417C, 1)), "1B 载荷（无型号位）应拒绝");
        // 更长载荷放行（固件扩展不拒收）
        expect(BleDiscoveryPolicy.vendorMatches(vendorAd(COMPANY_BE, 0x417C, 28)), "28B 载荷（未来扩展）应命中");
        // 异公司（两种序都不是 0x521C 的镜像对）
        expect(!BleDiscoveryPolicy.vendorMatches(vendorAd(new byte[]{(byte) 0x11, (byte) 0x22}, 0x417C, 24)), "异公司应拒绝");
        expect(!BleDiscoveryPolicy.vendorMatches(vendorAd(new byte[]{(byte) 0x21, (byte) 0x5C}, 0x417C, 24)), "近邻公司 ID 应拒绝");
        // 异型号（同公司不同型号靠 0x417C 区分）
        expect(!BleDiscoveryPolicy.vendorMatches(vendorAd(COMPANY_LE, 0x417D, 24)), "异型号（低字节差）应拒绝");
        expect(!BleDiscoveryPolicy.vendorMatches(vendorAd(COMPANY_LE, 0x407C, 24)), "异型号（高字节差）应拒绝");
        // 无 0xFF / 空记录 / null
        expect(!BleDiscoveryPolicy.vendorMatches(ad(0x03, new byte[]{(byte) 0xE8, (byte) 0xFE})), "只有服务列表无厂商 AD 应不命中");
        expect(!BleDiscoveryPolicy.vendorMatches(new byte[0]), "空记录应不命中");
        expect(!BleDiscoveryPolicy.vendorMatches(null), "null 记录应不命中");
        // AD 结构遍历：厂商 AD 前有别的结构照样找到；尾 0 填充截止
        byte[] prefixed = concat(ad(0x01, new byte[]{0x06}), h3sRecord(COMPANY_LE));
        expect(BleDiscoveryPolicy.vendorMatches(prefixed), "前置其他 AD 结构不影响命中");
        // 畸形：len 声明越过记录尾 → 放弃解析不命中
        byte[] malformed = {(byte) 0x05, (byte) 0xFF, 0x1C, 0x52, 0x41};   // len=5 但只剩 3 字节
        expect(!BleDiscoveryPolicy.vendorMatches(malformed), "len 越界=畸形应不命中");
        // findAd 基本行为：取第一个匹配 type 的结构
        byte[] two = concat(ad(0x03, new byte[]{1, 2}), ad(0x03, new byte[]{3, 4}));
        expect(BleDiscoveryPolicy.findAd(two, 0x03).length == 2 && BleDiscoveryPolicy.findAd(two, 0x03)[0] == 1,
            "findAd 应取首个匹配结构");
        expect(BleDiscoveryPolicy.findAd(two, 0x09) == null, "findAd 无匹配应 null");
    }

    /** 服务列表 0x03 辅助信号（0xFEE8）：解析正确；只加分不否决（无 0xFEE8 的厂商命中仍可、只有服务列表不命中）。 */
    static void serviceListAuxiliary() {
        expect(BleDiscoveryPolicy.serviceUuidListed(h3sRecord(COMPANY_LE), 0xFEE8), "实测记录应含 0xFEE8");
        expect(BleDiscoveryPolicy.serviceUuidListed(ad(0x03, new byte[]{(byte) 0xE8, (byte) 0xFE, 0x11, 0x22}), 0xFEE8),
            "多 UUID 列表应逐个识别");
        expect(!BleDiscoveryPolicy.serviceUuidListed(ad(0x03, new byte[]{(byte) 0xE7, (byte) 0xFE}), 0xFEE8), "异 UUID 不含");
        expect(!BleDiscoveryPolicy.serviceUuidListed(ad(0x03, new byte[]{(byte) 0xE8}), 0xFEE8), "奇数长畸形列表视为不含");
        expect(!BleDiscoveryPolicy.serviceUuidListed(vendorAd(COMPANY_LE, 0x417C, 24), 0xFEE8), "无服务 AD 应不含");
        expect(!BleDiscoveryPolicy.serviceUuidListed(null, 0xFEE8), "null 记录不含");
        // 加分不否决：缺 0xFEE8 的厂商 AD 命中照样是候选；只有 0xFEE8 无厂商 AD 不构成候选
        byte[] noService = vendorAd(COMPANY_LE, 0x417C, 24);
        expect(!BleDiscoveryPolicy.serviceUuidListed(noService, 0xFEE8) && BleDiscoveryPolicy.vendorMatches(noService),
            "缺 0xFEE8 不否决厂商命中");
        byte[] serviceOnly = ad(0x03, new byte[]{(byte) 0xE8, (byte) 0xFE});
        expect(BleDiscoveryPolicy.serviceUuidListed(serviceOnly, 0xFEE8) && !BleDiscoveryPolicy.vendorMatches(serviceOnly),
            "只有 0xFEE8 不构成候选（面向整个厂商家族，太宽）");
    }

    /** 厂商候选累积：无名广播靠 vendorAd=true 入池（主路径）；错误名+厂商命中也入池；settle 取最强。 */
    static void vendorCandidates() {
        BleDiscoveryPolicy p = new BleDiscoveryPolicy(null, "QCY H3S", false);
        expect(p.offer(null, "V1", -60, true) == null && p.candidateCount() == 1, "无名厂商候选应入池");
        expect(p.offer(null, "V1", -55, true) == null, "同址厂商候选刷新 RSSI");
        p.offer("完全无关的名字", "V2", -40, true);                       // 名字不匹配但厂商命中 → 入池
        p.offer(null, "V3", -10, false);                                   // 无名且非厂商 → 不入池
        p.offer("QCY H4S", "V4", -5, false);                               // 名字不匹配非厂商 → 不入池
        expect(p.candidateCount() == 2, "应只有厂商候选 V1/V2: " + p.candidateCount());
        expect("V2".equals(p.strongest()), "厂商候选取最强（V2/-40）");
        p.reset();
        expect(p.candidateCount() == 0, "reset 清候选");
        expect(p.offer(null, "V5", -30, true) == null && "V5".equals(p.strongest()), "reset 后厂商路径仍有效");
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
