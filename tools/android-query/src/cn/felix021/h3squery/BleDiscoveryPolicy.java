package cn.felix021.h3squery;

import java.util.HashMap;
import java.util.Map;

/**
 * BLE 扫描发现策略（路 BLEDISC2，2026-11-19）：目标地址由扫描广播自动发现，主识别=
 * 厂商特定 AD（H3S 广播实测**无 Local Name**——5 条历史目标广播 getName() 全 null、无
 * 0x08/0x09 AD，上一版按名发现的前提不成立，见 docs/android-ble-connection-diagnosis-20261118.md
 * 「2026-11-19 广播扫描版复测」）。广播名匹配保留为辅（有名字的广播也能认）。构建注入的
 * DeviceConfig.ADDRESS 仍为可选优先目标：扫到该地址立即选定（兼容注入构建/测试）。
 *
 * 厂商 AD 识别（照官方 QCY App 的做法）：
 *   - AD 结构 type 0xFF（Manufacturer Specific Data），公司 ID 16 位值 {@link #VENDOR_COMPANY_ID}
 *     （0x521C），去掉公司 ID 后载荷 >= {@link #VENDOR_PAYLOAD_MIN_LEN} 字节（实测 H3S 恒 24，
 *     截断广播按长度拒绝），载荷开头两字节大端 = 型号 ID {@link #VENDOR_MODEL_ID}（0x417C——
 *     docs/protocol.md USB PID 同值，同公司其他型号在此被拒）。
 *   - 公司 ID 字节序 [未钉]：诊断报告只记了 16 位值 0x521C 未记原始字节序，而 docs/protocol.md
 *     的 USB VID 0x1C52 与它互为镜像——两种序都认（识别力不受影响：型号+长度才是判别器）。
 *   - 16 位服务列表 AD（0x03）含 0xFEE8 是辅助信号（{@link #serviceUuidListed}）——加分不
 *     否决：只含服务列表不含厂商 AD 的广播不作为候选（0xFEE8 面向整个 QCY/杰理家族，太宽），
 *     厂商 AD 命中而缺 0xFEE8 照样是候选。
 *
 * 本类是纯 JVM 的配置 + 广播解析 + 最小候选累积，transport 与 AndroidGattLink 共用同一实例：
 *   - AndroidGattLink 在平台扫描回调里调 {@link #vendorMatches(byte[])}/
 *     {@link #nameMatches(String)}/{@link #isPreferred(String)} 做前置过滤（不匹配的广播不上报
 *     主线程），并把厂商命中位随候选传给 transport；
 *   - AndroidBleTransport 在 SCAN 相位调 {@link #offer} 喂候选——返回非 null=优先地址命中应立即
 *     选定；settle 观察窗到点调 {@link #strongest()} 取匹配候选中信号最强者（多台同型号时选信号
 *     最好者）。连错设备的最后一道防线在 transport：GATT 服务发现后核对 A002 服务/特征，缺=失败。
 */
public final class BleDiscoveryPolicy {
    /** H3S 的 BLE 广播名（辅助识别：广播带名时也能认；实测 H3S 广播无名）。 */
    public static final String DEFAULT_NAME = "QCY H3S";

    /** AD 结构类型：厂商特定数据（Manufacturer Specific Data）。 */
    public static final int AD_TYPE_MANUFACTURER = 0xFF;
    /** AD 结构类型：16 位服务 Class UUID 完整列表。 */
    public static final int AD_TYPE_SERVICE_UUIDS_16 = 0x03;
    /** H3S 厂商 AD 的公司 ID 16 位值（诊断报告 2026-11-19；字节序两种都认，见类注）。 */
    public static final int VENDOR_COMPANY_ID = 0x521C;
    /** H3S 厂商载荷（去掉公司 ID 后）的最小长度：5 条实测广播全部 24 字节；更短=截断拒绝。 */
    public static final int VENDOR_PAYLOAD_MIN_LEN = 24;
    /** H3S 型号 ID：厂商载荷开头两字节大端（docs/protocol.md USB PID 0x417C 同值）。 */
    public static final int VENDOR_MODEL_ID = 0x417C;
    /** 辅助信号：H3S 广播的 16 位服务列表含 0xFEE8（加分不否决，只作诊断/核对）。 */
    public static final int AUX_SERVICE_UUID16 = 0xFEE8;

    private final String preferredAddress;   // 可空：构建注入的优先目标（DeviceConfig.ADDRESS，大写归一）
    private final String name;               // 目标广播名
    private final boolean exact;             // true=全匹配；false=前缀匹配（默认，容忍 "QCY H3S xxx" 尾缀）
    private final HashMap<String, Integer> candidates = new HashMap<String, Integer>(); // 地址→最强 RSSI

    public BleDiscoveryPolicy(String preferredAddress, String name, boolean exact) {
        this.preferredAddress = preferredAddress == null || preferredAddress.isEmpty()
            ? null : preferredAddress.toUpperCase();
        this.name = name == null || name.isEmpty() ? DEFAULT_NAME : name;
        this.exact = exact;
    }

    public String preferredAddress() { return preferredAddress; }

    /** 目标广播名。 */
    public String name() { return name; }

    /** 是否全匹配模式（否则前缀）。 */
    public boolean exactMatch() { return exact; }

    /** 广播名是否匹配目标（实例配置；null/空安全）。 */
    public boolean nameMatches(String advertised) {
        return nameMatches(advertised, name, exact);
    }

    /**
     * 静态匹配规则（AndroidGattLink 前置过滤与本实例共用；纯函数便于单测）。
     * 忽略大小写——BLE 广播名经不同栈/缓存读出时大小写不保证一致。
     */
    public static boolean nameMatches(String advertised, String target, boolean exact) {
        if (advertised == null || advertised.isEmpty() || target == null || target.isEmpty()) return false;
        if (exact) return advertised.equalsIgnoreCase(target);
        return advertised.length() >= target.length()
            && advertised.substring(0, target.length()).equalsIgnoreCase(target);
    }

    /** 该地址是否构建注入的优先目标（大小写不敏感——地址写法各栈不一）。 */
    public boolean isPreferred(String address) {
        return preferredAddress != null && address != null && preferredAddress.equalsIgnoreCase(address);
    }

    // ================================ 广播记录解析（纯函数，单测覆盖） ================================

    /**
     * 遍历 scan record 的 AD 结构（[len][type][data…] 序列，len 计 type 不计自身），返回第一个
     * 匹配 type 的 data；无匹配/记录畸形=null。len=0 视为记录结束（Android 广播包尾 0 填充）。
     */
    public static byte[] findAd(byte[] scanRecord, int type) {
        if (scanRecord == null) return null;
        int i = 0;
        while (i + 1 < scanRecord.length) {
            int len = scanRecord[i] & 0xff;
            if (len == 0) break;                                        // 填充 0=记录结束
            int dataStart = i + 2;
            int dataLen = len - 1;                                      // len 含 1 字节 type
            if (dataLen <= 0 || dataStart + dataLen > scanRecord.length) break;  // 畸形截断：不再解析
            if ((scanRecord[i + 1] & 0xff) == type)
                return java.util.Arrays.copyOfRange(scanRecord, dataStart, dataStart + dataLen);
            i = dataStart + dataLen;
        }
        return null;
    }

    /**
     * 厂商 AD 识别（H3S 无名广播的主识别）：0xFF 载荷 = 公司 ID 2 字节 + 厂商载荷。
     * 命中条件：公司 ID（两种字节序都认，见类注）+ 载荷 >= {@link #VENDOR_PAYLOAD_MIN_LEN}
     * （截断拒绝）+ 载荷头两字节大端 = {@link #VENDOR_MODEL_ID}（同公司异型号拒绝）。
     */
    public static boolean vendorMatches(byte[] scanRecord) {
        byte[] ad = findAd(scanRecord, AD_TYPE_MANUFACTURER);
        if (ad == null || ad.length < 2 + VENDOR_PAYLOAD_MIN_LEN) return false;
        int b0 = ad[0] & 0xff, b1 = ad[1] & 0xff;
        int littleEndian = (b1 << 8) | b0;                              // 蓝牙规范：公司 ID 小端
        int bigEndian = (b0 << 8) | b1;                                 // 报告记法可能的大端镜像
        if (littleEndian != VENDOR_COMPANY_ID && bigEndian != VENDOR_COMPANY_ID) return false;
        int model = ((ad[2] & 0xff) << 8) | (ad[3] & 0xff);             // 型号大端（报告实测）
        return model == VENDOR_MODEL_ID;
    }

    /**
     * 16 位服务列表 AD（0x03）是否含指定 UUID（小端，蓝牙规范）。辅助信号——加分不否决，
     * 不参与候选判定（见类注）；诊断日志与真机核对用（H3S 广播实测含 0xFEE8）。
     */
    public static boolean serviceUuidListed(byte[] scanRecord, int uuid16) {
        byte[] ad = findAd(scanRecord, AD_TYPE_SERVICE_UUIDS_16);
        if (ad == null || ad.length % 2 != 0) return false;             // 奇数长=畸形，视为不含
        for (int i = 0; i + 1 < ad.length; i += 2) {
            int uuid = ((ad[i + 1] & 0xff) << 8) | (ad[i] & 0xff);
            if (uuid == uuid16) return true;
        }
        return false;
    }

    /**
     * 喂一个广播候选。返回非 null=应立即选定的地址（仅优先地址命中时——它被显式配置，
     * 无需再比信号）；返回 null=继续扫描（厂商 AD 命中或名字匹配的候选先记录，settle 时取
     * 最强）。vendorAd=AndroidGattLink 前置过滤算出的厂商 AD 命中位（无名广播的主路径）。
     * 同址重复广播只保留最强 RSSI。
     */
    public String offer(String advertisedName, String address, int rssi, boolean vendorAd) {
        if (address == null || address.isEmpty()) return null;
        if (isPreferred(address)) return preferredAddress;   // 返回大写归一形
        if (vendorAd || nameMatches(advertisedName)) {
            Integer old = candidates.get(address);
            if (old == null || rssi > old.intValue()) candidates.put(address, Integer.valueOf(rssi));
        }
        return null;
    }

    /** {@link #offer(String, String, int, boolean)} 的名字匹配快捷形（vendorAd=false）。 */
    public String offer(String advertisedName, String address, int rssi) {
        return offer(advertisedName, address, rssi, false);
    }

    /** settle：匹配候选（厂商 AD 或名字）中 RSSI 最强的地址（无候选=null）。 */
    public String strongest() {
        String best = null;
        int bestRssi = Integer.MIN_VALUE;
        for (Map.Entry<String, Integer> e : candidates.entrySet()) {
            if (e.getValue().intValue() > bestRssi) { bestRssi = e.getValue().intValue(); best = e.getKey(); }
        }
        return best;
    }

    /** 当前匹配候选数（transport 判断是否进入 settle 观察）。 */
    public int candidateCount() { return candidates.size(); }

    /** 新一次连接会话前清空候选（配置项保留）。 */
    public void reset() { candidates.clear(); }
}
