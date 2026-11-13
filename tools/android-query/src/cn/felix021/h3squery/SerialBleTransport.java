package cn.felix021.h3squery;

import java.util.ArrayDeque;
import java.util.ArrayList;
import java.util.List;

/**
 * 串行发送队列的通用实现（transport 层，AGENTS 不变量 6）：
 *
 *   - 帧级串行：一次只发一条 RCSP 帧，等它终态（写完所有片 +（需响应时）收到响应帧或超时）
 *     再发下一条——「等前一个回调再发下一个」。
 *   - 片级串行：帧按 BlePacketizer.split(frame, mtu) 分片（GATT 有效载荷 = MTU-3），
 *     一次只有一片在写，等该片写回调、且距上一片 >= interChunkDelayMs（默认 10ms，实测要求）
 *     才发下一片。
 *   - 接收：deviceNotify(fragment) 喂 notify 分片，RcspDeframer 按 length+EF 重组出完整帧
 *     （可能跨片/一次多帧），响应帧（flags=00）释放当前等待的命令帧。
 *
 * 子类只需实现「写一片」（doWriteChunk：一次 GATT write，完成后必须调 chunkWritten），
 * 以及连接生命周期（connect/disconnect 里维护 ready/mtu，notify 分片喂 deviceNotify）。
 * LoopbackTransport 与将来的 Android BLE 适配层共用本路径——离线单测覆盖的就是真机路径。
 *
 * 时序容忍：响应 notify 可能先于最后一片的写回调到达（真机回调无顺序保证），
 * 完成条件是「片写完 && (响应到 || 不需要响应)」，两个事件任意先后都能正确收口。
 *
 * 计时：两类延时任务分开——
 *   - schedule(delayMs, r)（片间隔，顺序任务）：默认手动队列，runScheduler() 同步驱动
 *     （JVM 单测推进顺序、并对 delay 值断言）；Android 适配层覆盖为 Handler.postDelayed。
 *   - scheduleTimeout(r)（响应超时）：默认挂起不自动执行（手动队列无时钟，立即执行会把
 *     「等响应」误判成 0ms 超时），测试用 runTimeouts() 显式触发；Android 覆盖为 postDelayed。
 * 超时任务用 generation 计数惰性失效（帧完成后旧任务 run 时自检 no-op），不需要 cancel 句柄。
 */
public abstract class SerialBleTransport implements H3sTransport {
    public static final int INTER_CHUNK_DELAY_MS = 10;   // 片间隔下限（实测服务要求，AGENTS 不变量 6）
    public static final int RESPONSE_TIMEOUT_MS = 5000;  // 命令帧等响应超时（对齐 UpgradeService 写超时）

    /** 排队/在途的帧。 */
    private static final class PendingFrame {
        final byte[] frame;
        final FrameCallback callback;
        final long generation;
        List<byte[]> chunks;      // 出队时按当时 MTU 分片
        int chunkIndex;
        boolean allChunksWritten;
        boolean responseArrived;
        PendingFrame(byte[] frame, FrameCallback callback, long generation) {
            this.frame = frame; this.callback = callback; this.generation = generation;
        }
    }

    private Listener listener;
    private final ArrayDeque<PendingFrame> queue = new ArrayDeque<PendingFrame>();
    private PendingFrame current;
    private long generation;                     // 每帧递增，惰性失效旧超时任务
    private int mtu = 23;                        // ATT MTU 默认值；connect 链路里协商更新
    private boolean ready;
    /** 每次 schedule 的 delay 记录（片间隔断言用；只增不删，含已失效任务）。测试探针。 */
    public final List<Long> scheduledDelays = new ArrayList<Long>();
    /** 手动调度队列（默认调度器）：schedule 入队，runScheduler() 同步驱动。 */
    private final ArrayDeque<Runnable> manualQueue = new ArrayDeque<Runnable>();
    /** 挂起的响应超时任务（默认调度器无时钟不自动执行；runTimeouts() 显式触发）。 */
    private final List<Runnable> pendingTimeouts = new ArrayList<Runnable>();

    protected SerialBleTransport() {}

    @Override public void setListener(Listener listener) { this.listener = listener; }

    @Override public boolean isReady() { return ready; }
    @Override public int mtu() { return mtu; }

    /** 子类在连接就绪（discover+CCCD+MTU 全到位）时调；会向 listener 报 onMtuChanged。 */
    protected void markReady(int negotiatedMtu) {
        this.mtu = negotiatedMtu;
        this.ready = true;
        if (listener != null) listener.onMtuChanged(negotiatedMtu);
        pump();                                    // connect 前积压的帧此时开闸
    }

    /** 子类在链路断开时调；未完成帧按失败回调、清队列（不重复报 onDisconnected）。 */
    protected void markNotReady(String reason) {
        ready = false;
        failAllPending(reason);
        if (listener != null) { Listener l = listener; listener = null; l.onDisconnected(reason); }
    }

    @Override public void write(byte[] frame, FrameCallback callback) {
        if (frame == null) { callback.onDone(false, "帧不能为 null"); return; }
        queue.add(new PendingFrame(frame.clone(), callback, ++generation));
        pump();
        runScheduler();                            // 默认手动调度器：驱动刚入队的片任务
    }

    /** 取下一条帧开始发送（帧级串行入口）。 */
    private void pump() {
        if (current != null || !ready || queue.isEmpty()) return;
        current = queue.poll();
        current.chunks = BlePacketizer.split(current.frame, mtu);
        current.chunkIndex = 0;
        sendNextChunk();
    }

    private void sendNextChunk() {
        schedule(INTER_CHUNK_DELAY_MS, new Runnable() { public void run() { writeChunkNow(); } });
    }

    private void writeChunkNow() {
        if (current == null) return;
        doWriteChunk(current.chunks.get(current.chunkIndex));
    }

    /** 子类实现：把一片写进通道。完成后必须调 chunkWritten(ok, error)（可在任意线程语义下回调）。 */
    protected abstract void doWriteChunk(byte[] chunk);

    /** 片写回调：成功继续下一片（隔 interChunkDelayMs），失败终结当前帧。 */
    protected final void chunkWritten(boolean ok, String error) {
        if (current == null) return;
        if (!ok) { failCurrent("片写入失败: " + error); return; }
        current.chunkIndex++;
        if (current.chunkIndex < current.chunks.size()) { sendNextChunk(); return; }
        current.allChunksWritten = true;
        if (needsResponse(current.frame) && !current.responseArrived) {
            scheduleTimeout(timeoutTask(current.generation));
        }
        tryCompleteCurrent();
    }

    /** 喂 notify 分片：按 length+EF 重组，每个完整帧交给 listener；响应帧释放当前命令帧。 */
    protected final void deviceNotify(byte[] fragment) {
        for (byte[] frame : deframer.feed(fragment)) handleDeviceFrame(frame);
    }
    private final RcspDeframer deframer = new RcspDeframer();

    private void handleDeviceFrame(byte[] frame) {
        if (listener != null) listener.onFrame(frame);
        if (current != null && needsResponse(current.frame) && isResponseFrame(frame)) {
            current.responseArrived = true;
            tryCompleteCurrent();
        }
    }

    private void tryCompleteCurrent() {
        if (current == null || !current.allChunksWritten) return;
        if (needsResponse(current.frame) && !current.responseArrived) return;
        finishCurrent(true, null);
    }

    private void finishCurrent(boolean ok, String error) {
        PendingFrame f = current;
        current = null;
        generation++;                                  // 旧超时任务失效
        if (f != null) f.callback.onDone(ok, error);
        pump();
    }

    private void failCurrent(String error) { finishCurrent(false, error); }

    private void failAllPending(String error) {
        if (current != null) failCurrent(error);
        while (!queue.isEmpty()) queue.poll().callback.onDone(false, error);
    }

    private Runnable timeoutTask(final long gen) {
        return new Runnable() { public void run() {
            if (current != null && current.generation == gen) failCurrent("响应超时 " + RESPONSE_TIMEOUT_MS + "ms");
        } };
    }

    /** 帧是否需要响应：flags=C0（命令需响应，docs/protocol.md）。 */
    public static boolean needsResponse(byte[] frame) {
        return frame != null && frame.length >= 8 && (frame[3] & 0xff) == EqFrame.FLAGS_CMD;
    }

    /** 是否 RCSP 响应帧：flags=00。 */
    public static boolean isResponseFrame(byte[] frame) {
        return frame != null && frame.length >= 8 && (frame[3] & 0xff) == 0x00;
    }

    /**
     * 片间隔任务调度：默认手动队列（记录 delay 供断言、runScheduler() 同步驱动）；
     * Android 适配层覆盖为 main Handler.postDelayed(r, delayMs) 走真实时钟。
     */
    protected void schedule(long delayMs, Runnable r) {
        scheduledDelays.add(delayMs);
        manualQueue.add(r);
    }

    /**
     * 响应超时任务调度：默认挂起（手动队列无时钟，自动执行会把「等响应」误判成 0ms 超时），
     * runTimeouts() 显式触发；Android 适配层覆盖为 handler.postDelayed(r, RESPONSE_TIMEOUT_MS)。
     */
    protected void scheduleTimeout(Runnable r) {
        pendingTimeouts.add(r);
    }

    /** 驱动手动调度队列直到空（仅默认调度器需要；执行中新入队的任务也会被消费）。 */
    void runScheduler() {
        while (!manualQueue.isEmpty()) manualQueue.poll().run();
    }

    /** 显式触发挂起的超时任务（测试用；generation 已失效的任务自检 no-op）。 */
    public void runTimeouts() {
        List<Runnable> due = new ArrayList<Runnable>(pendingTimeouts);
        pendingTimeouts.clear();
        for (Runnable r : due) r.run();
    }

    /**
     * RCSP 帧重组器：输入 notify 字节流片段，按「FE DC BA 头 + len_BE16 + EF 尾」切出完整帧
     * （docs/protocol.md：接收可能跨通知分片、也可能一次多帧）。头前的杂散字节丢弃。
     * 单元测试可直接使用。
     */
    public static final class RcspDeframer {
        private final java.io.ByteArrayOutputStream buffer = new java.io.ByteArrayOutputStream();
        /** 喂入一个片段，返回其中重组出的完整帧（0..n 条）。 */
        public List<byte[]> feed(byte[] fragment) {
            if (fragment != null) buffer.write(fragment, 0, fragment.length);
            List<byte[]> frames = new ArrayList<byte[]>();
            byte[] data = buffer.toByteArray();
            int p = 0;
            while (true) {
                int head = findHead(data, p);
                if (head < 0) { p = data.length; break; }          // 无头：全部是垃圾，丢弃
                if (data.length - head < 8) { p = head; break; }   // 头不完整：保留从 head 起
                int bodyLen = ((data[head + 5] & 0xff) << 8) | (data[head + 6] & 0xff);
                int end = head + 8 + bodyLen;
                if (end > data.length) { p = head; break; }        // 帧未到齐：保留
                if ((data[end - 1] & 0xff) != 0xef) { p = head + 1; continue; }  // 尾不对：头是假头，后移
                frames.add(java.util.Arrays.copyOfRange(data, head, end));
                p = end;
            }
            buffer.reset();
            if (p < data.length) buffer.write(data, p, data.length - p);
            return frames;
        }
        private static int findHead(byte[] data, int from) {
            for (int i = from; i + 2 < data.length; i++) {
                if ((data[i] & 0xff) == 0xfe && (data[i + 1] & 0xff) == 0xdc && (data[i + 2] & 0xff) == 0xba) return i;
            }
            return -1;
        }
    }
}
