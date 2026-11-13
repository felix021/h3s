package cn.felix021.h3squery;

/**
 * H3sTransport 的 Android BLE 适配层（路 AU / sim-bletransport）：真机传输的连接状态机
 * + GATT 粘合，串行/分片/重组全部复用 SerialBleTransport（接口与 LoopbackTransport 对齐，
 * H3SBridge/EqWritePolicy 零改动可切换）。
 *
 * 与 android.bluetooth 的全部接触面收敛在一个可注入的 {@link Link} 接口后面——本类因此是
 * 纯 JVM 代码，可在不连真机（红线）的前提下用假 Link 单测整个连接/写入/断线状态机；
 * 真实现是 AndroidGattLink（android.bluetooth 薄粘合，照 MainActivity/UpgradeService 先例，
 * 只在 build.sh 按 android.jar 编译）。离线契约测试见 TransportContractTest：同一套断言
 * 同时跑 LoopbackTransport（Z 路）与本类+假 Link。
 *
 * 连接状态机（照 tools/android-query/README.md「真机适配清单」与 docs/app-protocol.md §1）：
 *
 *   IDLE → SCAN(可选，按目标地址过滤，超时/失败也继续) → CONNECT(connectGatt)
 *        → DISCOVER(discoverServices) → SUBSCRIBE(setCharacteristicNotification+CCCD)
 *        → MTU(requestMtu(517)，以 onMtuChanged 回调实际值为准，不假定 247/517)
 *        → READY(markReady(实际MTU)，connect 前积压的帧开闸)
 *
 * 就绪条件 = CCCD 写成功 && 收到过 onMtuChanged 实际值（两者先后任意——Android 可能主动
 * 回报 MTU，照 UpgradeService 的 cccdEnabled/attMtu 双标志先例）。MTU 协商失败不致命：
 * 命令帧按当前 MTU（默认 23）分片照样可发，只是片数变多。
 *
 * 重试策略：连接初始化阶段（CONNECT..MTU 任一环节）失败重试最多 {@link #CONNECT_ATTEMPTS}
 * 次（默认 2 次总尝试），退避 1s（照 UpgradeService reconnect 的 1s 先例）；30s 整体看门狗
 * 超时或重试耗尽则报 onDisconnected(reason)。READY 后意外断线不自动重连（清单第 6 条只要求
 * markNotReady；页面可再次 connect）。
 *
 * 写路径（AGENTS 不变量 6 全在 SerialBleTransport）：doWriteChunk 一次一片经 Link 写出；
 * onCharacteristicWrite(status==0) → chunkWritten(true)，status!=0 → chunkWritten(false)
 * （只终结当前帧，串行队列按 10ms 间隔试下一条——GATT 写回调 5s 缺失才是致命路径，
 * 直接断链不静默丢片，照 UpgradeService writeTimeout 先例）。写请求被同步拒绝
 * （writeCharacteristic 返回 false）同样按片失败回调（回调必不来，不能等 5s）。
 *
 * 调度：本类的定时器（扫描超时/连接看门狗/写看门狗）与 SerialBleTransport 的片间隔/
 * 响应超时统一走 {@link Scheduler}（Android 注入 main Handler.postDelayed；测试注入假
 * 调度器，或 null=默认手动队列）。所有定时任务都带相位/世代自检，晚到的过期任务自动 no-op，
 * 不需要 cancel 句柄。
 *
 * 线程模型：Link 回调约定在单线程上调用（AndroidGattLink 统一 main.post），本类不加锁。
 *
 * 诚实标注：**未连真机**（红线）——本路只写适配层 + 假 Link 单测；真机联调顺序与对照项
 * 见 README 清单第 9/10 条（setter 响应载荷形状、EQ 是否需预设装载等仍 [未钉]）。
 */
public class AndroidBleTransport extends SerialBleTransport {
    // —— android.bluetooth 常量镜像（公开 API 值稳定；平台侧 AndroidGattLink 直接用原常量）——
    /** BluetoothGattCharacteristic.PROPERTY_WRITE（写带响应）。 */
    public static final int PROPERTY_WRITE = 0x08;
    /** BluetoothGattCharacteristic.PROPERTY_WRITE_NO_RESPONSE（写无响应，H3S 实测属性 4）。 */
    public static final int PROPERTY_WRITE_NO_RESPONSE = 0x04;
    /** BluetoothGattCharacteristic.WRITE_TYPE_DEFAULT（写带响应）。 */
    public static final int WRITE_TYPE_DEFAULT = 0x02;
    /** BluetoothGattCharacteristic.WRITE_TYPE_NO_RESPONSE（写无响应，H3S 实测走这个）。 */
    public static final int WRITE_TYPE_NO_RESPONSE = 0x01;
    /** BluetoothProfile.STATE_CONNECTED。 */
    public static final int STATE_CONNECTED = 2;
    /** BluetoothProfile.STATE_DISCONNECTED。 */
    public static final int STATE_DISCONNECTED = 0;

    /** 扫描超时（超时/失败不阻断——转直连；Android 允许不扫描直接 connectGatt）。 */
    public static final long SCAN_TIMEOUT_MS = 10000;
    /** 连接+初始化整体看门狗（照 MainActivity 30s 先例；超时直接失败，不占重试额度）。 */
    public static final long CONNECT_TIMEOUT_MS = 30000;
    /** 单片 GATT 写回调看门狗（照 UpgradeService 5s 先例；超时=断链）。 */
    public static final long CHUNK_WRITE_TIMEOUT_MS = 5000;
    /** MTU 协商请求值（实测回调 247；以回调为准，不假定请求值生效）。 */
    public static final int MTU_REQUEST = 517;
    /** 连接初始化失败的总尝试次数（含首次）。 */
    public static final int CONNECT_ATTEMPTS = 2;
    /** 两次尝试间的退避。 */
    public static final long RETRY_BACKOFF_MS = 1000;

    /**
     * Android GATT/扫描的最小缝合面：transport 状态机只看这些动作 + 回调，
     * 把 BluetoothGatt/BluetoothLeScanner 的平台形状隔离在实现侧（真实现=AndroidGattLink，
     * 测试=TransportContractTest 的假 Link）。实现方保证回调在单线程上调用。
     */
    public interface Link {
        /** 连接前置检查：蓝牙权限/开关/目标地址合法性；就绪返回 null，否则返回原因（直接进日志与断线事件）。 */
        String notReadyReason();
        /** 按「目标地址」过滤开始扫描（不扫任意设备——地址门槛在实现侧钉死，照清单第 8 条）。 */
        void startScan();
        void stopScan();
        /** 发起 GATT 连接（autoConnect=false、TRANSPORT_LE，异步）。 */
        void connectGatt();
        /** 触发服务发现（异步，回调 onServicesDiscovered）。返回 false=请求被同步拒绝。 */
        boolean discoverServices();
        /** 服务发现完成后：QCY A002 服务与写/通知特征是否齐（校验留在 transport，失败按连接失败重试）。 */
        boolean characteristicsPresent();
        /** 写特征的属性位（服务发现完成后有效；transport 据此选 WRITE_TYPE_*）。 */
        int writeProperties();
        /** 订阅通知：setCharacteristicNotification + CCCD 写 ENABLE_NOTIFICATION_VALUE（异步，回调 onDescriptorWrite）。 */
        boolean enableNotifications();
        /** 请求 MTU 协商（异步，回调 onMtuChanged）。返回 false=请求被同步拒绝（用当前 MTU 直接就绪）。 */
        boolean requestMtu(int mtu);
        /** 写一片（一次只有一个在途——串行由 SerialBleTransport 保证）。返回 false=请求被同步拒绝。 */
        boolean writeChunk(byte[] chunk, int writeType);
        /** 断开并释放 GATT 资源（disconnect+close）。 */
        void closeGatt();
        /** 注入回调（transport 构造时调用一次）。 */
        void setCallbacks(Callbacks callbacks);

        /** 平台回调（真实现把 BluetoothGattCallback 全部翻译到这里并统一过主线程）。 */
        interface Callbacks {
            /** 目标地址的广播出现（扫描命中）。 */
            void onScanResult();
            /** 扫描启动失败（transport 直接转直连，不阻断）。 */
            void onScanFailed(int error);
            /** status==0 && state==STATE_CONNECTED=链路建立；断开/错误见 state/status。 */
            void onConnectionStateChange(int status, int state);
            void onServicesDiscovered(int status);
            void onDescriptorWrite(int status);
            /** MTU 协商结果（可能在任意阶段到达——Android 会主动回报）。 */
            void onMtuChanged(int mtu, int status);
            /** 单片写完成回调。 */
            void onCharacteristicWrite(int status);
            /** 通知分片（transport 喂 RcspDeframer 重组，可能跨片/一次多帧）。 */
            void onCharacteristicChanged(byte[] value);
        }
    }

    /** 延时任务调度面（Android=Handler.postDelayed；测试=假调度器；null=SerialBleTransport 默认手动队列）。 */
    public interface Scheduler {
        void postDelayed(long delayMs, Runnable r);
    }

    /** 状态日志出口（连接过程/断线原因；Android 侧接 android.util.Log，测试可收集断言）。 */
    public interface Logger {
        void log(String message);
    }

    private final Link link;
    private final Scheduler scheduler;              // 可空：null=用 SerialBleTransport 默认手动调度器
    private final Logger logger;                    // 可空
    private final Link.Callbacks callbacks = new Link.Callbacks() {
        @Override public void onScanResult() { handleScanResult(); }
        @Override public void onScanFailed(int error) { handleScanFailed(error); }
        @Override public void onConnectionStateChange(int status, int state) { handleConnectionState(status, state); }
        @Override public void onServicesDiscovered(int status) { handleServicesDiscovered(status); }
        @Override public void onDescriptorWrite(int status) { handleDescriptorWrite(status); }
        @Override public void onMtuChanged(int mtu, int status) { handleMtuChanged(mtu, status); }
        @Override public void onCharacteristicWrite(int status) { handleCharacteristicWrite(status); }
        @Override public void onCharacteristicChanged(byte[] value) { deviceNotify(value.clone()); }
    };

    private enum Phase { IDLE, SCAN, CONNECT, DISCOVER, SUBSCRIBE, MTU_WAIT, READY }

    private Phase phase = Phase.IDLE;
    private int attempt;                            // 已用掉的连接尝试次数
    private boolean scanEnabled = true;
    private int lastMtu = 23;                       // 最近一次 onMtuChanged 的有效值（默认 ATT 23）
    private boolean mtuArrived;                     // 收到过 MTU 结果（成功或失败兜底）
    private boolean cccdOk;
    private int writeType = WRITE_TYPE_NO_RESPONSE; // discover 后按特征属性选定
    private long connectEpoch = 0;                  // 每次连接尝试递增；看门狗/退避任务自检
    private long writeEpoch = 0;                    // 每次片写动作递增；写看门狗自检

    public AndroidBleTransport(Link link) { this(link, null, null); }

    public AndroidBleTransport(Link link, Scheduler scheduler, Logger logger) {
        this.link = link;
        this.scheduler = scheduler;
        this.logger = logger;
        if (link != null) link.setCallbacks(callbacks);
    }

    /** 连接前是否先扫描（默认 true；关掉=直接 connectGatt）。 */
    public void setScanEnabled(boolean enabled) { this.scanEnabled = enabled; }

    // ================================ 线程入口（主线程跳转） ================================

    /**
     * 线程约定：注入 Scheduler（Android=主线程 Handler）时，本类全部可变入口
     * （connect/disconnect/write）先跳主线程再执行——@JavascriptInterface 的 bridge 方法
     * 跑在 WebView JS 线程，而 Link 回调在主线程（AndroidGattLink 统一 post），不跳线程
     * 会让 SerialBleTransport 的队列被两个线程并发改。Scheduler 为 null（JVM 单测）时
     * 直接同步执行，行为与 LoopbackTransport 一致。
     */
    @Override public void connect() {
        if (scheduler != null) { scheduler.postDelayed(0, new Runnable() { public void run() { connectInternal(); } }); return; }
        connectInternal();
    }

    @Override public void disconnect() {
        if (scheduler != null) { scheduler.postDelayed(0, new Runnable() { public void run() { disconnectInternal(); } }); return; }
        disconnectInternal();
    }

    @Override public void write(final byte[] frame, final FrameCallback callback) {
        if (scheduler != null) { scheduler.postDelayed(0, new Runnable() { public void run() { AndroidBleTransport.super.write(frame, callback); } }); return; }
        super.write(frame, callback);
    }

    // ================================ 连接生命周期 ================================

    /** 幂等：连接中/已连接时重复调用是 no-op。 */
    private void connectInternal() {
        if (phase != Phase.IDLE) return;
        String reason = link.notReadyReason();
        if (reason != null) { failConnect(reason); return; }
        attempt = 0;
        cccdOk = false;
        mtuArrived = false;
        lastMtu = 23;                                // 新连接会话从 ATT 默认 23 起（防上一会话的陈旧值）
        if (scanEnabled) {
            phase = Phase.SCAN;
            log("扫描目标设备（" + SCAN_TIMEOUT_MS + "ms 超时后转直连）");
            final long epoch = connectEpoch;
            schedule(SCAN_TIMEOUT_MS, new Runnable() { public void run() {          // 扫描超时→直连
                if (phase != Phase.SCAN || connectEpoch != epoch) return;
                log("扫描超时未命中，转直连");
                link.stopScan();
                beginAttempt();
            }});
            link.startScan();
        } else {
            beginAttempt();
        }
    }

    /** 用户主动断开：停扫描、释放 GATT，未完成帧按失败回调（markNotReady 契约）。 */
    private void disconnectInternal() {
        link.stopScan();
        link.closeGatt();                           // 触发的 onConnectionStateChange 在 IDLE 相位被忽略
        if (phase == Phase.IDLE) return;
        phase = Phase.IDLE;
        connectEpoch++;                             // 在途看门狗/退避任务失效
        writeEpoch++;
        failConnect("主动断开");
    }

    private void beginAttempt() {
        attempt++;
        phase = Phase.CONNECT;
        final long epoch = ++connectEpoch;
        log("连接尝试 " + attempt + "/" + CONNECT_ATTEMPTS);
        schedule(CONNECT_TIMEOUT_MS, new Runnable() { public void run() {           // 整体看门狗：超时直接失败不重试
            if (phase == Phase.READY || phase == Phase.IDLE || connectEpoch != epoch) return;
            link.closeGatt();
            phase = Phase.IDLE;
            failConnect("连接/初始化超过 " + CONNECT_TIMEOUT_MS + "ms");
        }});
        link.connectGatt();
    }

    /** 连接初始化失败的重试入口（GATT 断开/发现失败/特征缺失/订阅失败）。 */
    private void retryOrFail(String reason) {
        link.closeGatt();
        phase = Phase.IDLE;
        if (attempt < CONNECT_ATTEMPTS) {
            log(reason + "；" + RETRY_BACKOFF_MS + "ms 后重试");
            final long epoch = connectEpoch;
            schedule(RETRY_BACKOFF_MS, new Runnable() { public void run() {
                if (phase != Phase.IDLE || connectEpoch != epoch) return;          // 用户已断开/已开新连接
                beginAttempt();
            }});
        } else {
            failConnect(reason + "（已尝试 " + attempt + " 次）");
        }
    }

    /** 终态失败：markNotReady（未完成帧按失败回调；连续断线事件由 SerialBleTransport 去重）。 */
    private void failConnect(String reason) {
        log("连接失败: " + reason);
        markNotReady(reason);
    }

    // ================================ Link 回调 ================================

    private void handleScanResult() {
        if (phase != Phase.SCAN) return;
        log("扫描命中目标，转连接");
        link.stopScan();
        beginAttempt();
    }

    private void handleScanFailed(int error) {
        if (phase != Phase.SCAN) return;
        log("扫描启动失败(" + error + ")，转直连");
        beginAttempt();
    }

    private void handleConnectionState(int status, int state) {
        if (phase == Phase.IDLE) return;            // 主动断开/已放弃的旧连接回调
        if (status == 0 && state == STATE_CONNECTED) {
            if (phase != Phase.CONNECT) return;     // 重复的 CONNECTED 回调
            phase = Phase.DISCOVER;
            log("GATT 已连接，发现服务");
            if (!link.discoverServices()) retryOrFail("discoverServices 请求被拒");
            return;
        }
        // 断开或错误：READY 后=意外掉线（不自动重连）；初始化中=按尝试失败重试
        if (phase == Phase.READY) {
            log("连接断开 status=" + status);
            link.closeGatt();
            phase = Phase.IDLE;
            writeEpoch++;
            markNotReady("连接断开 status=" + status);
        } else {
            retryOrFail("GATT 断开 status=" + status);
        }
    }

    private void handleServicesDiscovered(int status) {
        if (phase != Phase.DISCOVER) return;
        if (status != 0) { retryOrFail("服务发现失败 " + status); return; }
        if (!link.characteristicsPresent()) { retryOrFail("缺少 QCY A002/写/通知特征"); return; }
        int props = link.writeProperties();
        if ((props & PROPERTY_WRITE) != 0) writeType = WRITE_TYPE_DEFAULT;           // 属性齐则优先带响应写
        else if ((props & PROPERTY_WRITE_NO_RESPONSE) != 0) writeType = WRITE_TYPE_NO_RESPONSE; // H3S 实测属性 4
        else { retryOrFail("写特征无 WRITE 属性(属性=" + props + ")"); return; }
        log("特征就绪，writeType=" + (writeType == WRITE_TYPE_DEFAULT ? "DEFAULT" : "NO_RESPONSE"));
        phase = Phase.SUBSCRIBE;
        if (!link.enableNotifications()) retryOrFail("通知订阅请求被拒");
    }

    private void handleDescriptorWrite(int status) {
        if (phase != Phase.SUBSCRIBE) return;
        if (status != 0) { retryOrFail("CCCD 写失败 " + status); return; }
        cccdOk = true;
        maybeReady();
    }

    private void handleMtuChanged(int mtu, int status) {
        if (status == 0 && mtu >= 23) {
            if (mtu != lastMtu) log("MTU 回调 " + lastMtu + "→" + mtu);
            lastMtu = mtu;
            mtuArrived = true;
        } else {
            // 协商失败不致命：按当前 MTU（默认 23）就绪，命令帧照发只是片数变多
            log("MTU 协商失败(status=" + status + " mtu=" + mtu + ")，按 " + lastMtu + " 继续");
            mtuArrived = true;
        }
        maybeReady();
    }

    /** CCCD 与 MTU 双标志齐 → READY（两者先后任意——Android 可能主动回报 MTU）。 */
    private void maybeReady() {
        if (phase != Phase.SUBSCRIBE && phase != Phase.MTU_WAIT) return;
        if (!cccdOk) return;
        if (!mtuArrived) {
            phase = Phase.MTU_WAIT;
            if (!link.requestMtu(MTU_REQUEST)) {    // 请求被同步拒：用当前 MTU 就绪
                mtuArrived = true;
                maybeReady();
            }
            return;
        }
        phase = Phase.READY;
        log("就绪 MTU=" + lastMtu);
        markReady(lastMtu);
        runScheduler();                             // 手动调度器（JVM）下把 connect 后积压帧的片任务排空；
                                                    // 真机 Handler 调度器下 manualQueue 恒空，no-op
    }

    // ================================ 写路径 ================================

    /** 一片 GATT 写（串行/分片/10ms 间隔全在 SerialBleTransport；本方法只粘 GATT）。 */
    @Override protected void doWriteChunk(byte[] chunk) {
        if (phase != Phase.READY) { chunkWritten(false, "未连接"); runScheduler(); return; }
        final long epoch = ++writeEpoch;
        schedule(CHUNK_WRITE_TIMEOUT_MS, new Runnable() { public void run() {       // 写看门狗：晚到自检 no-op
            if (writeEpoch != epoch) return;
            log("片写回调超时 " + CHUNK_WRITE_TIMEOUT_MS + "ms，断链");
            link.closeGatt();
            phase = Phase.IDLE;
            writeEpoch++;
            markNotReady("片写回调超时 " + CHUNK_WRITE_TIMEOUT_MS + "ms");
        }});
        boolean accepted = link.writeChunk(chunk, writeType);
        if (!accepted) {
            writeEpoch++;                           // 看门狗失效（回调必不来）
            chunkWritten(false, "GATT 写请求被拒");
        }
        runScheduler();                             // 默认手动调度器兼容（照 LoopbackTransport）
    }

    private void handleCharacteristicWrite(int status) {
        writeEpoch++;                               // 写看门狗失效
        if (status == 0) chunkWritten(true, null);
        else chunkWritten(false, "GATT status " + status);
    }

    // ================================ 调度 ================================

    /** 定时任务统一出口：注入 Scheduler 则走它（Android=Handler.postDelayed），否则默认手动队列。 */
    @Override protected void schedule(long delayMs, Runnable r) {
        if (scheduler != null) { scheduler.postDelayed(delayMs, r); return; }
        super.schedule(delayMs, r);
    }

    @Override protected void scheduleTimeout(Runnable r) {
        if (scheduler != null) { scheduler.postDelayed(RESPONSE_TIMEOUT_MS, r); return; }
        super.scheduleTimeout(r);
    }

    private void log(String message) {
        if (logger != null) logger.log(message);
    }

    // 测试探针
    /** 当前连接相位名（测试断言用；正常流转 IDLE→SCAN→CONNECT→DISCOVER→SUBSCRIBE→MTU_WAIT→READY）。 */
    public String phase() { return phase.name(); }
    /** 选定的 GATT 写类型（服务发现后有效；测试断言用）。 */
    public int writeType() { return writeType; }
}
