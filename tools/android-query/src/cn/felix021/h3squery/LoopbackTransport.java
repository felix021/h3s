package cn.felix021.h3squery;

import java.util.ArrayDeque;
import java.util.ArrayList;
import java.util.List;

/**
 * 回环传输桩（离线端到端单测用）：H3SBridge → SerialBleTransport 串行队列 → 本桩
 * 「当耳机」——把写出的片按序重组出命令帧，生成协议形状的响应帧（EQ/ANC/音量写返回
 * status=0 成功响应），再经 notify 路径（deviceNotify + RcspDeframer 重组）送回。
 *
 * 「离线测的就是真实路径」：桩只实现 doWriteChunk（收片）与连接假动作，分片/串行/片间隔/
 * 重组全部走 SerialBleTransport 的通用代码——真机适配层替换的也只是这两个点
 * （粘合清单见 tools/android-query/README.md「传输接线骨架」）。
 *
 * 响应帧形状：`FE DC BA | 00(status 响应 flags) | FF | len | status=00 | SN | cmdid | data_len | data | EF`。
 * status+SN 起始是实测钉死的（docs/protocol.md：RX `FE DC BA 00 FF 00 07 00 6F ...`）；
 * cmdid 起的载荷部分对 setter 是回显（分发器包装格式 `cmdid|n|data` 是读路径钉死的，
 * 写路径 setter 响应载荷 [未钉]——真机联调时以实机帧为准，这里回显只为打通 SN 匹配链路）。
 *
 * 测试钩子：pauseResponses()/releaseResponses() 把「设备出响应」挂起/放行，用来测帧级串行
 * （第二条帧必须等第一条帧响应后才发出）；构造参数 MTU 可压小，用来测跨片重组。
 */
public class LoopbackTransport extends SerialBleTransport {
    private final int negotiatedMtu;
    private final SerialBleTransport.RcspDeframer deviceRxDeframer = new SerialBleTransport.RcspDeframer();
    /** 设备桩收到的片（按写出顺序；测试探针，逐字节=子类 doWriteChunk 收到什么记什么）。 */
    public final List<byte[]> writtenChunks = new ArrayList<byte[]>();
    /** 设备桩重组出的完整命令帧（按到达顺序；测试探针，逐字节对照点）。 */
    public final List<byte[]> receivedFrames = new ArrayList<byte[]>();
    /** 挂起未响应的命令帧（pauseResponses 期间积压）。 */
    private final ArrayDeque<byte[]> pausedCommands = new ArrayDeque<byte[]>();
    private boolean paused;
    private boolean connected;

    /** negotiatedMtu：模拟协商到的 ATT MTU（默认 247=实测值；压小可测跨片路径）。 */
    public LoopbackTransport(int negotiatedMtu) { this.negotiatedMtu = negotiatedMtu; }
    public LoopbackTransport() { this(247); }

    @Override public void connect() {
        if (connected) return;
        connected = true;
        markReady(negotiatedMtu);                  // 假装 discover+CCCD+MTU 一次到位
    }

    @Override public void disconnect() {
        if (!connected) return;
        connected = false;
        markNotReady("loopback disconnected");
    }

    /** 挂起设备响应（帧照收、片照写完，但不回响应——命令帧在设备侧积压）。 */
    public void pauseResponses() { paused = true; }

    /** 放行积压的命令帧：逐条生成响应并走 notify 路径送回（驱动串行队列发下一条帧）。 */
    public void releaseResponses() {
        paused = false;
        while (!pausedCommands.isEmpty()) respondTo(pausedCommands.poll());
        runScheduler();
    }

    /** 「一次 GATT 写」：收片 → 重组 → （完整帧则生成响应走 notify）→ 回写回调。 */
    @Override protected void doWriteChunk(byte[] chunk) {
        writtenChunks.add(chunk.clone());
        List<byte[]> frames = deviceRxDeframer.feed(chunk);
        // 先回片写回调（真机 onCharacteristicWrite），再让设备出响应——两事件先后由
        // SerialBleTransport 的完成条件容忍（响应先到/后到都正确收口）。
        chunkWritten(true, null);
        for (byte[] frame : frames) {
            receivedFrames.add(frame);
            if (paused) pausedCommands.add(frame);   // 暂存，releaseResponses 时响应
            else respondTo(frame);
        }
        runScheduler();
    }

    /** 设备桩处理一条命令帧：生成响应帧、按 MTU 分片走 notify 路径送回。 */
    private void respondTo(byte[] commandFrame) {
        byte[] response = buildSuccessResponse(commandFrame);
        if (response == null) return;              // 非 setter 形状（如只读 FE 01 cmdid）不响应——真机联调再补
        for (byte[] piece : BlePacketizer.split(response, mtu())) deviceNotify(piece);
    }

    /**
     * 生成成功响应帧。命令 body=`SN cmdid data_len data`（写形态，docs/cmd-0x17-0x1d-layout.md §1）
     * → 响应 body=`00(status) SN cmdid n data(回显)`——cmdid|n|data 对齐读路径实测的分发器包装
     * （docs/protocol.md：RX `... 00 FF 00 07 | 00 6F | 17 03 01 01 02`，n=载荷字节数）。
     * 另答 ADV type2 写（opcode C2，KeyRemapFrame 候选 A）：响应 body=`status SN mask 三元组(回显)`，
     * 写对偶响应形状同属 [未钉]，回显只为打通 SN 匹配链路。
     * setter 响应载荷回显部分 [未钉]（见类注释）；status+SN 位置是实测的。
     */
    public static byte[] buildSuccessResponse(byte[] commandFrame) {
        if (commandFrame == null || commandFrame.length < 11) return null;
        int flags = commandFrame[3] & 0xff;
        int opcode = commandFrame[4] & 0xff;
        if (flags != EqFrame.FLAGS_CMD) return null;
        if (opcode == KeyRemapFrame.ADV_WRITE_OPCODE) return buildAdvType2Response(commandFrame);
        if (opcode != EqFrame.OPCODE_CUSTOM) return null;
        int sn = commandFrame[7] & 0xff;
        int cmdId = commandFrame[8] & 0xff;
        int dataLen = commandFrame[9] & 0xff;
        if (cmdId == 0xfe || dataLen != commandFrame.length - 11) return null;  // 只读形态/长度不符不响应
        byte[] body = new byte[4 + dataLen];      // status + SN + cmdid + n + data
        body[0] = 0x00;                            // status=0 成功
        body[1] = (byte) sn;
        body[2] = (byte) cmdId;
        body[3] = (byte) dataLen;                  // n（载荷字节数，分发器包装格式）
        System.arraycopy(commandFrame, 10, body, 4, dataLen);
        return wrapResponseBody(body, opcode);
    }

    /** ADV type2 写（body=SN+mask+三元组，共 8B）→ 响应 body=status+SN+mask+三元组回显。 */
    private static byte[] buildAdvType2Response(byte[] commandFrame) {
        if (commandFrame.length != 16) return null;                    // 8 头尾 + 8 body
        byte[] body = new byte[9];                                     // status + SN + mask(4) + 三元组(3)
        body[0] = 0x00;                                                // status=0 成功
        body[1] = commandFrame[7];                                     // SN
        System.arraycopy(commandFrame, 8, body, 2, 7);                 // mask + 三元组回显
        return wrapResponseBody(body, KeyRemapFrame.ADV_WRITE_OPCODE);
    }

    private static byte[] wrapResponseBody(byte[] body, int opcode) {
        byte[] frame = new byte[8 + body.length];
        frame[0] = (byte) 0xfe; frame[1] = (byte) 0xdc; frame[2] = (byte) 0xba;
        frame[3] = 0x00;                           // flags=00 响应
        frame[4] = (byte) opcode;
        frame[5] = (byte) (body.length >> 8); frame[6] = (byte) body.length;
        System.arraycopy(body, 0, frame, 7, body.length);
        frame[frame.length - 1] = (byte) 0xef;
        return frame;
    }
}
