package cn.felix021.h3squery;

/**
 * R3 独立 app 的传输抽象（路 Z / sim-h3stransport）：把「一条完整 RCSP 帧进出设备」
 * 从具体通道（真 BLE / 回环桩）里隔离出来，H3SBridge 只面向本接口。
 *
 * 职责边界（对齐 docs/app-protocol.md §1/§4.3）：
 *   - write(frame, cb)：把一条完整 RCSP 帧发给设备；cb 是该帧的终态（成功=写完且
 *     （帧不需要响应 或 已收到响应帧），失败=写失败/超时，带错误说明）。
 *   - Listener.onFrame：notify 流里重组出的完整 RCSP 帧（可能跨多个 notify 分片，
 *     也可能一次多帧——由 transport 内部按 length+EF 重组，见 SerialBleTransport.RcspDeframer）。
 *   - 连接生命周期：connect/disconnect/isReady/mtu（连接顺序 discover→CCCD→MTU 的
 *     细节属于具体实现，接口只暴露结果）。
 *
 * 串行约束（AGENTS 不变量 6）由 SerialBleTransport 统一实现：单写串行（等前一片
 * 写回调再发下一片）、片间隔 >=10ms、一次只出一条帧（等前一条帧终态再发下一条）。
 * 具体实现只负责「一次 GATT 写一片」（doWriteChunk）和「喂 notify 分片」（deviceNotify）。
 *
 * 离线验证：LoopbackTransport 走同一串行路径（保证单测覆盖的就是真机路径）；
 * 真机 Android BLE 适配层未实现（红线：不连真机），粘合清单见 tools/android-query/README.md
 * 「传输接线骨架」一节。
 */
public interface H3sTransport {
    /** notify 重组出的完整帧 / 连接事件回调。实现方保证 onFrame 传入的帧已通过头/长度/尾校验。 */
    interface Listener {
        /** 收到一个完整 RCSP 帧（响应帧 flags=00，body 以 status+SN 起，见 docs/protocol.md）。 */
        void onFrame(byte[] frame);
        /** MTU 协商结果（实测 247；GATT 有效载荷 = MTU-3）。 */
        void onMtuChanged(int mtu);
        /** 连接断开（主动 disconnect 或链路丢失）。 */
        void onDisconnected(String reason);
    }

    /** 单帧发送终态回调：ok=false 时 error 说明原因（写失败/响应超时/未连接）。 */
    interface FrameCallback {
        void onDone(boolean ok, String error);
    }

    void setListener(Listener listener);

    /** 建立连接（异步；ready 变化经 onMtuChanged/MTU 到位体现，isReady 轮询）。 */
    void connect();

    /** 断开并清空未发送队列（未完成帧按失败回调）。 */
    void disconnect();

    /** 连接是否就绪（discover+CCCD+MTU 全部到位）。 */
    boolean isReady();

    /** 当前 ATT MTU（未就绪时返回默认 23）。 */
    int mtu();

    /**
     * 发送一条完整 RCSP 帧（排队，串行；帧 flags=C0 时等响应帧到达或超时才算成功）。
     * 返回后帧仍在队列里，真正的结果经 FrameCallback 通知。
     */
    void write(byte[] frame, FrameCallback callback);
}
