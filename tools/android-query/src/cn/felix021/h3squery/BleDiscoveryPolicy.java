package cn.felix021.h3squery;

import java.util.HashMap;
import java.util.Map;

/**
 * BLE 扫描发现策略（路 BLEDISC，2026-11-18）：目标地址不再要求构建期人工注入——按广播名
 * 匹配自动发现（照 QCY 官方 App 的做法：扫描 "QCY H3S" 广播 → 取地址连接，见
 * docs/android-ble-connection-diagnosis-20261118.md 的根因与官方对照）。构建注入的
 * DeviceConfig.ADDRESS 降级为可选优先目标：配置了且扫到该地址时优先直用（向后兼容注入
 * 地址的构建/测试），扫不到则照常按名字发现。
 *
 * 本类是纯 JVM 的配置 + 最小候选累积，transport 与 AndroidGattLink 共用同一实例：
 *   - AndroidGattLink 在平台扫描回调里调 {@link #nameMatches(String)}/{@link #isPreferred(String)}
 *     做前置过滤（不匹配的广播不上报主线程）；
 *   - AndroidBleTransport 在 SCAN 相位调 {@link #offer} 喂候选——返回非 null=优先地址命中
 *     应立即选定；settle 观察窗到点调 {@link #strongest()} 取名字匹配候选中信号最强者。
 *
 * 厂商 adv 数据识别 [未钉]：现有日志/逆向没有 QCY 厂商 ID 线索，按任务约束只按名字匹配——
 * 名字匹配不到=发现失败（明确报错），不猜厂商特征字节。
 */
public final class BleDiscoveryPolicy {
    /** H3S 的 BLE 广播名（2026-11-18 用户手机日志：官方 App 扫描发现的名字）。 */
    public static final String DEFAULT_NAME = "QCY H3S";

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

    /**
     * 喂一个广播候选。返回非 null=应立即选定的地址（仅优先地址命中时——它被显式配置，
     * 无需再比信号）；返回 null=继续扫描（名字匹配的候选先记录，settle 时取最强）。
     * 同址重复广播只保留最强 RSSI。
     */
    public String offer(String advertisedName, String address, int rssi) {
        if (address == null || address.isEmpty()) return null;
        if (isPreferred(address)) return preferredAddress;   // 返回大写归一形
        if (nameMatches(advertisedName)) {
            Integer old = candidates.get(address);
            if (old == null || rssi > old.intValue()) candidates.put(address, Integer.valueOf(rssi));
        }
        return null;
    }

    /** settle：名字匹配候选中 RSSI 最强的地址（无候选=null）。 */
    public String strongest() {
        String best = null;
        int bestRssi = Integer.MIN_VALUE;
        for (Map.Entry<String, Integer> e : candidates.entrySet()) {
            if (e.getValue().intValue() > bestRssi) { bestRssi = e.getValue().intValue(); best = e.getKey(); }
        }
        return best;
    }

    /** 当前名字匹配候选数（transport 判断是否进入 settle 观察）。 */
    public int candidateCount() { return candidates.size(); }

    /** 新一次连接会话前清空候选（配置项保留）。 */
    public void reset() { candidates.clear(); }
}
