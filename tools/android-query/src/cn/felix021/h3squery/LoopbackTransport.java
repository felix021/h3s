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
    /** R4 key-store 读回桩值（cmdid 0x30 data_len=1 的回执 8 字节；测试可改写）。 */
    public byte[] keyStoreValue = new byte[KeyStoreFrame.VALUE_LEN];
    /** R4 key-store key31 读回桩值（data=[31] 的回执 88 字节；测试可改写，全零=非协议值）。 */
    public byte[] eqKeyStoreValue = new byte[KeyStoreFrame.EQ_VALUE_LEN];
    /** 更多功能（MOREFUNC）FF 只读查询回读桩值：cmdid→getter 载荷（0x14/0x1f/0x19/0x23/0x24/0x2e）。
     *  FF 写命令按真固件行为**静默**（setter 返回 0），写时同步更新本表——回读即得「设备态」。 */
    public final java.util.Map<Integer, byte[]> moreFuncState = new java.util.HashMap<>();
    /** 按键映射表桩（批量同步 C0 写改这里 / C1 读回这里；初始=固件默认表 0x0609e250）。 */
    public byte[] keyRemapTable = {
        0x07, 0x01, 0x01, 0x07, 0x02, 0x07, 0x07, 0x03, 0x04, 0x07, 0x04, 0x00, 0x07, 0x05, 0x00,
        0x03, 0x01, 0x06, 0x03, 0x02, 0x00, 0x03, 0x03, 0x00, 0x03, 0x04, 0x00, 0x03, 0x05, 0x02,
        0x05, 0x01, 0x05, 0x05, 0x02, 0x00, 0x05, 0x03, 0x00, 0x05, 0x04, 0x00, 0x05, 0x05, 0x03};

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

    /** 测试钩子：把一帧完整设备通知（如 flags=0x80 主动通知）从 notify 路径喂进接收侧。 */
    public void injectDeviceNotify(byte[] frame) { deviceNotify(frame); }

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

    /** 设备桩处理一条命令帧：生成响应帧、按 MTU 分片走 notify 路径送回。
     *  零长响应=真固件静默 setter（返回 0 不回帧，MOREFUNC 族），同样不出帧。 */
    private void respondTo(byte[] commandFrame) {
        byte[] response = buildDeviceResponse(commandFrame);
        if (response == null || response.length == 0) return;  // 非应答形状/静默 setter——不出帧
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
        return buildSuccessResponse(commandFrame, null);
    }

    /** 带 R4 key-store 桩值的版本（key30/key31 各自的读回桩值）。 */
    public byte[] buildDeviceResponse(byte[] commandFrame) {
        byte[] more = buildMoreFuncResponse(commandFrame);
        return more != null ? more : buildSuccessResponse(commandFrame, keyStoreValue, eqKeyStoreValue);
    }

    /**
     * 更多功能族（MOREFUNC）设备桩：按**真固件反解行为**应答（docs/morefunc-protocol.md）——
     * FF 写 0x14/0x1f/0x19/0x23/0x24 setter 返回 0=静默（不回帧，只更新 moreFuncState 供回读）；
     * FF 只读（FE 01 cmd）回 `cmdid n data`（分发器读路径包装）；批量同步 C0 写 cmdid 2 改
     * keyRemapTable 后回 0xC0 status 帧（回执前缀形状 [未钉]，桩取 `00 SN 00`）；C1 读 mask
     * bit2 回 `00 SN 00` + item{2E 02 <45B>}；C5 01 恢复出厂重置全部桩态、C5 02 清除配对回
     * status（真机清配对后断连，桩不模拟断连）。
     * 返回 null 表示交回既有回显路径（非更多功能族命令）。
     */
    private byte[] buildMoreFuncResponse(byte[] f) {
        if (f == null || f.length < 10) return null;
        int flags = f[3] & 0xff, opcode = f[4] & 0xff;
        if (flags != EqFrame.FLAGS_CMD) return null;
        int sn = f[7] & 0xff;
        if (opcode == MoreFuncFrame.OPCODE_BATCH_WRITE) return respondBatchWrite(f, sn);
        if (opcode == MoreFuncFrame.OPCODE_BATCH_READ) return respondBatchRead(f, sn);
        if (opcode == MoreFuncFrame.OPCODE_RESET_FAMILY) return respondResetFamily(f, sn);
        if (opcode != EqFrame.OPCODE_CUSTOM) return null;
        int cmdId = f[8] & 0xff, dataLen = f[9] & 0xff;
        // FF 只读查询 body=SN FE 01 cmd：只答 moreFuncState 里登记过的 cmdid
        if (f.length == 12 && (f[8] & 0xff) == 0xfe && (f[9] & 0xff) == 0x01) {
            byte[] value = moreFuncState.get(f[10] & 0xff);
            if (value == null) return null;                        // 未登记（如 0x2f 电量）走既有路径/不答
            byte[] payload = new byte[2 + value.length];
            payload[0] = (byte) (f[10] & 0xff);
            payload[1] = (byte) value.length;
            System.arraycopy(value, 0, payload, 2, value.length);
            return wrapKeystoreBody(sn, payload, opcode);
        }
        switch (cmdId) {                                            // FF 写：静默 + 记桩态
            case MoreFuncFrame.CMD_SLEEP_TIMER:
            case MoreFuncFrame.CMD_AUTO_OFF:
            case MoreFuncFrame.CMD_TONE_LANGUAGE:
            case MoreFuncFrame.CMD_LDAC:
            case MoreFuncFrame.CMD_DUAL_DEVICE:
            case EqWritePolicy.CMD_AUDIO_MODE:
                if (dataLen != f.length - 11) return null;
                byte[] value = java.util.Arrays.copyOfRange(f, 10, 10 + dataLen);
                byte[] getterValue;
                if (cmdId == MoreFuncFrame.CMD_SLEEP_TIMER && dataLen == 4)
                    getterValue = new byte[]{value[0], value[1], value[0], value[1]}; // 回读 4B=[设定,剩余]
                else if (cmdId == EqWritePolicy.CMD_AUDIO_MODE && dataLen == 1)
                    getterValue = value.clone();
                else
                    getterValue = value.clone();                   // 0x1f 2B / 0x19 4B / 0x23,0x24 1B 回读同写值
                moreFuncState.put(cmdId, getterValue);
                return new byte[0];                                // 静默：零长响应=不出帧
            default:
                return null;                                       // 其余（0x22/0x17/0x1d/0x2b/0x30）走既有回显
        }
    }

    /** 批量同步写桩：cmdid 2 三元组/全表改 keyRemapTable（照 0x0602550e 匹配改写），回 0xC0 status。 */
    private byte[] respondBatchWrite(byte[] f, int sn) {
        int itemLen = f[8] & 0xff, cmdId = f[9] & 0xff;
        if (itemLen < 2 || f.length != 8 + 1 + 1 + itemLen) return new byte[0];
        int dataLen = itemLen - 1;
        if (cmdId == KeyRemapFrame.BATCH_CMDID && dataLen == 3) {
            int key = f[10] & 0xff, gesture = f[11] & 0xff;
            for (int i = 0; i < KeyRemapFrame.TABLE_ENTRIES; i++) {
                if (keyRemapTable[i * 3] == key && keyRemapTable[i * 3 + 1] == gesture) {
                    keyRemapTable[i * 3 + 2] = f[12];
                    break;
                }
            }
        } else if (cmdId == KeyRemapFrame.BATCH_CMDID && dataLen == KeyRemapFrame.TABLE_LEN) {
            keyRemapTable = java.util.Arrays.copyOfRange(f, 10, 10 + dataLen);
        } else if (cmdId == MoreFuncFrame.BATCH_CMDID_NAME) {
            byte[] name = java.util.Arrays.copyOfRange(f, 10, 10 + dataLen); // 名字桩：记态即可
            moreFuncState.put(0x2100 + MoreFuncFrame.BATCH_CMDID_NAME, name);
        } else {
            return new byte[0];                                     // 未知 item：照静默处理
        }
        return wrapKeystoreBody(sn, new byte[]{0x00}, MoreFuncFrame.OPCODE_BATCH_WRITE);
    }

    /** 批量同步读桩：mask bit2 → `00 SN 00` + item{2E 02 <45B>}（回执前缀 [未钉]，桩取单 status 字节）。 */
    private byte[] respondBatchRead(byte[] f, int sn) {
        if (f.length != 13) return new byte[0];
        int mask = ((f[8] & 0xff) << 24) | ((f[9] & 0xff) << 16) | ((f[10] & 0xff) << 8) | (f[11] & 0xff);
        if (mask != KeyRemapFrame.ADV_TYPE2_MASK) return new byte[0];
        byte[] payload = new byte[3 + 2 + KeyRemapFrame.TABLE_LEN];
        payload[0] = 0x00;                                          // [未钉] 前缀 status 字节
        payload[1] = 0x00;
        payload[2] = 0x00;
        payload[3] = (byte) (1 + KeyRemapFrame.TABLE_LEN);          // item len=46=0x2E
        payload[4] = (byte) KeyRemapFrame.BATCH_CMDID;
        System.arraycopy(keyRemapTable, 0, payload, 5, KeyRemapFrame.TABLE_LEN);
        return wrapKeystoreBody(sn, payload, MoreFuncFrame.OPCODE_BATCH_READ);
    }

    /** 0xC5 桩：01 恢复出厂=重置全部桩态回默认，02 清除配对=回 status（真机此时断连，桩不模拟）。 */
    private byte[] respondResetFamily(byte[] f, int sn) {
        if (f.length != 10) return new byte[0];            // 8 头尾 + SN + 子命令
        if ((f[8] & 0xff) == MoreFuncFrame.RESET_SUBCMD_FACTORY) {
            moreFuncState.clear();
            keyRemapTable = new byte[]{
                0x07, 0x01, 0x01, 0x07, 0x02, 0x07, 0x07, 0x03, 0x04, 0x07, 0x04, 0x00, 0x07, 0x05, 0x00,
                0x03, 0x01, 0x06, 0x03, 0x02, 0x00, 0x03, 0x03, 0x00, 0x03, 0x04, 0x00, 0x03, 0x05, 0x02,
                0x05, 0x01, 0x05, 0x05, 0x02, 0x00, 0x05, 0x03, 0x00, 0x05, 0x04, 0x00, 0x05, 0x05, 0x03};
        }
        return wrapKeystoreBody(sn, new byte[]{0x00}, MoreFuncFrame.OPCODE_RESET_FAMILY);
    }

    static byte[] buildSuccessResponse(byte[] commandFrame, byte[] keyStoreValue) {
        return buildSuccessResponse(commandFrame, keyStoreValue, new byte[KeyStoreFrame.EQ_VALUE_LEN]);
    }

    /** key31 分支照 keystore_hook.S 的 HAL4 扩展：写→`30 01 status`，读→`30 58`+88B 桩值。 */
    static byte[] buildSuccessResponse(byte[] commandFrame, byte[] keyStoreValue, byte[] eqKeyStoreValue) {
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
        // R4 key-store（docs/r4-command-hook.md + keystore_hook.S HAL4 扩展）：按固件载荷的
        // 设计回执应答（写→`30 01 00`，读 key30→`30 08 桩值`，读 key31→`30 58 桩值`）
        // ——不是回显，也不是实测设备帧。
        if (cmdId == KeyStoreFrame.CMD_ID && dataLen == KeyStoreFrame.READ_LEN) {
            int key = commandFrame[10] & 0xff;
            int valueLen = key == KeyStoreFrame.EQ_KEY_ID ? KeyStoreFrame.EQ_VALUE_LEN
                    : key == KeyStoreFrame.KEY_ID ? KeyStoreFrame.VALUE_LEN : -1;
            if (valueLen >= 0) {
                byte[] value = key == KeyStoreFrame.EQ_KEY_ID
                        ? (eqKeyStoreValue == null ? new byte[KeyStoreFrame.EQ_VALUE_LEN] : eqKeyStoreValue)
                        : (keyStoreValue == null ? new byte[KeyStoreFrame.VALUE_LEN] : keyStoreValue);
                byte[] payload = new byte[2 + valueLen];
                payload[0] = (byte) cmdId;
                payload[1] = (byte) valueLen;
                System.arraycopy(value, 0, payload, 2, valueLen);
                return wrapKeystoreBody(sn, payload, opcode);
            }
        }
        if (cmdId == KeyStoreFrame.CMD_ID
                && (dataLen == KeyStoreFrame.WRITE_LEN || dataLen == KeyStoreFrame.EQ_WRITE_LEN)
                && (commandFrame[10] & 0xff) == (dataLen == KeyStoreFrame.EQ_WRITE_LEN
                        ? KeyStoreFrame.EQ_KEY_ID : KeyStoreFrame.KEY_ID)) {
            return wrapKeystoreBody(sn, new byte[]{(byte) cmdId, 0x01, 0x00}, opcode);
        }
        byte[] body = new byte[4 + dataLen];      // status + SN + cmdid + n + data
        body[0] = 0x00;                            // status=0 成功
        body[1] = (byte) sn;
        body[2] = (byte) cmdId;
        body[3] = (byte) dataLen;                  // n（载荷字节数，分发器包装格式）
        System.arraycopy(commandFrame, 10, body, 4, dataLen);
        return wrapResponseBody(body, opcode);
    }

    /** key-store 回执包装：body=status(00)+SN+payload，再套响应帧头尾。 */
    private static byte[] wrapKeystoreBody(int sn, byte[] payload, int opcode) {
        byte[] body = new byte[2 + payload.length];          // status + SN + payload
        body[0] = 0x00;
        body[1] = (byte) sn;
        System.arraycopy(payload, 0, body, 2, payload.length);
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
