package cn.felix021.h3squery;

import java.util.Arrays;

/**
 * RCSP 自定义命令（opcode 0xFF）setter 放行策略，类比 UpgradePolicy 的 armed 状态。
 *
 * 只在 armed 时放行「形状完全匹配」的 setter 帧（docs/cmd-0x17-0x1d-layout.md 路 P 钉死）：
 *   - 0x22 EQ：data_len%7==3（3B 头 + 每段 7B，docs/eq-cmd-0x22-layout.md 路 H）；
 *   - 0x17 ANC：data_len==3，data=`mode scene strength`，mode 1..3（QCY 线协议降噪/正常/通透）；
 *   - 0x1d 提示音音量：data_len==2，data=`value 占位`，value∈{3,6,8}（固件只读 data[0]，
 *     第二字节忽略；档位 3/6/8 是控制面板层约束，固件接收层不钳）；
 *   - 0x2b 按键重映射（候选 B）：data_len==3，data=[key,gesture,action]，字段域照
 *     KeyRemapFrame（key∈{3,5,7}、gesture 1..5（5=长按假设）、action 0..7/11）；
 *   - 0x30 R4 key-store（docs/r4-command-hook.md）：data=[30,v0..v7]（len 9，写 key 30）/
 *     data=[30]（len 1，读 key 30）；key31 同族（路 HAL4）：data=[31,v0..v87]（len 89，写）/
 *     data=[31]（len 1，读）——固件侧 --r4-keyhook 离线候选的 keystore_hook 载荷
 *     （UNTESTED），值字节对本层不透明，白名单只钉形状（key30 先例同款，不开任意帧写）；
 *   - 0x2E 音频模式（docs/wired-game-mode.md §2.1，音乐 1/游戏 3/电影 4）：固件 setter
 *     0x6026134 只读载荷首字节写 0x124a04；data_len=1 是本仓假设（0x1d 先例固件要求
 *     len>=2，0x2E 的 len 要求未钉），真机联调对照；
 *   - 0xC2 ADV type2 写（候选 A，[假设] C1 读对偶）：body=SN+mask(00 00 00 04)+3B 三元组，
 *     字段域同上（docs/app-protocol.md §2.5，两条 wire 真机抓包定案前并存放行）；
 *   - 「更多功能」族（docs/morefunc-protocol.md，控制面板全功能+固件注册表反解）：
 *     0x14 定时关机 len4（分钟 LE16∈{5,10,30,60,FFFF}，0=立即关机拒绝）、0x1f 断连关机
 *     len2（同域）、0x19 提示音语言 len4（key 必为 "JL00"/"JL01"）、0x23 LDAC len1 bool、
 *     0x24 双设备 len1 bool、0x03 清除配对 len1 data=[02]（[推断]，实验性）；
 *   - 批量同步（默认按键 wire，2026-11-19 路 MOREFUNC 钉死）：opcode 0xC0 item cmdid=2
 *     （data=合法三元组或 45B 全表）/ cmdid=1 设备名（1..20B，固件 32B 上限从严到面板 20）；
 *     opcode 0xC1 读 mask==0x00000004（按键表）；opcode 0xC5 body=SN+{01 恢复出厂,02 找耳机}。
 * 不开任意字节写总开关（AGENTS 红线）。未 armed 时退回 QueryPolicy（只读帧），所有写一律拒绝。
 *
 * 纯逻辑判定，不接真机传输层——传输接线需真机联调，本路不做。
 */
public final class EqWritePolicy {
    public static final int CMD_ANC = 0x17;            // ANC 模式切换 cmdid
    public static final int CMD_PROMPT_VOLUME = 0x1d;  // 提示音音量 cmdid
    public static final int CMD_KEY_REMAP = KeyRemapFrame.CMD_ID; // 按键重映射 cmdid（候选 B）
    public static final int CMD_KEY_STORE = KeyStoreFrame.CMD_ID; // R4 key-store cmdid（0x30）
    public static final int CMD_AUDIO_MODE = 0x2e;       // 音频模式 cmdid（46；音乐 1/游戏 3/电影 4）
    /** ADV 设备设置写 opcode（候选 A；[假设] C1 读对偶，KeyRemapFrame 详注）。 */
    public static final int ADV_WRITE_OPCODE = KeyRemapFrame.ADV_WRITE_OPCODE;
    /** 批量同步写/读与 0xC5 恢复族 opcode（MoreFuncFrame）。 */
    public static final int BATCH_WRITE_OPCODE = MoreFuncFrame.OPCODE_BATCH_WRITE;
    public static final int BATCH_READ_OPCODE = MoreFuncFrame.OPCODE_BATCH_READ;
    public static final int RESET_FAMILY_OPCODE = MoreFuncFrame.OPCODE_RESET_FAMILY;
    /** 提示音音量合法档位（控制面板 3/6/8=最小/中等/最大；固件接收层不钳，白名单从严）。 */
    public static final int[] PROMPT_VOLUME_LEVELS = {3, 6, 8};

    private EqWritePolicy() {}

    public static boolean permitted(byte[] data, boolean armed) {
        if (!armed) return QueryPolicy.permitted(data);
        if (data == null || data.length == 0) return false;
        int p = 0;
        while (p < data.length) {
            if (data.length - p < 9 || data[p] != (byte) 0xfe || data[p + 1] != (byte) 0xdc || data[p + 2] != (byte) 0xba) return false;
            int flags = data[p + 3] & 255, op = data[p + 4] & 255, n = ((data[p + 5] & 255) << 8) | (data[p + 6] & 255);
            if (n < 1 || p + n + 8 > data.length || data[p + n + 7] != (byte) 0xef) return false;
            byte[] frame = Arrays.copyOfRange(data, p, p + n + 8);
            boolean read = QueryPolicy.permitted(frame);
            boolean setter = isSetter(frame, flags, op, n);
            if (!read && !setter) return false;
            p += n + 8;
        }
        return true;
    }

    /** 形状完全匹配的 setter：flags=C0、（opcode=0xFF 且 cmdid∈{0x17,0x1d,0x22,0x2b,0x30} 且各自
     *  载荷形状匹配）或（opcode=0xC2 且 body=SN+type2 mask+合法三元组，ADV type2 写候选）。 */
    private static boolean isSetter(byte[] frame, int flags, int op, int n) {
        if (flags != EqFrame.FLAGS_CMD) return false;
        if (op == ADV_WRITE_OPCODE) return isAdvType2Setter(frame, n);
        if (op == BATCH_WRITE_OPCODE) return isBatchWriteSetter(frame, n);
        if (op == BATCH_READ_OPCODE) return isBatchReadSetter(frame, n);
        if (op == RESET_FAMILY_OPCODE) return isResetFamilySetter(frame, n);
        if (op != EqFrame.OPCODE_CUSTOM || n < 4) return false; // body 至少 SN+cmdid+data_len+1B data（0x30 读形状最小）
        int cmdId = frame[8] & 255;                              // body[1] = cmdid
        int dataLen = frame[9] & 255;                            // body[2] = data_len
        if (dataLen != n - 3) return false;
        switch (cmdId) {
            case EqFrame.CMD_ID:            // 0x22 EQ：3B 头 + 每段 7B
                return dataLen % 7 == 3;
            case CMD_ANC:                   // 0x17 ANC：data=`mode scene strength`，mode 1..3
                return dataLen == 3 && (frame[10] & 255) >= 1 && (frame[10] & 255) <= 3;
            case CMD_PROMPT_VOLUME:         // 0x1d：data=`value 占位`（固件只读 data[0]），值∈{3,6,8}
                if (dataLen != 2) return false;
                int v = frame[10] & 255;
                for (int level : PROMPT_VOLUME_LEVELS) if (level == v) return true;
                return false;
            case CMD_KEY_REMAP:             // 0x2b：data=[key,gesture,action]，字段域照 KeyRemapFrame
                return dataLen == 3 && KeyRemapFrame.isValidTriple(frame[10] & 255, frame[11] & 255, frame[12] & 255);
            case CMD_KEY_STORE:             // 0x30：key30 data=[30,8B]/[30]；key31 data=[31,88B]/[31]
                int key = frame[10] & 255;  // （写/读形状钉死、值不透明，key30 先例同款）
                if (key == KeyStoreFrame.KEY_ID)
                    return dataLen == KeyStoreFrame.WRITE_LEN || dataLen == KeyStoreFrame.READ_LEN;
                if (key == KeyStoreFrame.EQ_KEY_ID)
                    return dataLen == KeyStoreFrame.EQ_WRITE_LEN || dataLen == KeyStoreFrame.READ_LEN;
                return false;
            case CMD_AUDIO_MODE:            // 0x2E：data=[模式]（len 1 [假设]），模式∈{1,3,4}
                int mode = frame[10] & 255;
                return dataLen == 1 && (mode == 1 || mode == 3 || mode == 4);
            case MoreFuncFrame.CMD_SLEEP_TIMER:   // 0x14：data=[分钟 LE16,00,00]，档位从严（0=立即关机拒绝）
                if (dataLen != 4) return false;
                int mins14 = (frame[10] & 255) | ((frame[11] & 255) << 8);
                return MoreFuncFrame.isLegalMinutes(mins14);
            case MoreFuncFrame.CMD_AUTO_OFF:      // 0x1f：data=[分钟 LE16]，档位同 0x14
                if (dataLen != 2) return false;
                int mins1f = (frame[10] & 255) | ((frame[11] & 255) << 8);
                return MoreFuncFrame.isLegalMinutes(mins1f);
            case MoreFuncFrame.CMD_TONE_LANGUAGE: // 0x19：data=4B key，只认 JL00/JL01
                if (dataLen != 4) return false;
                byte[] langKey = new byte[4];
                System.arraycopy(frame, 10, langKey, 0, 4);
                return java.util.Arrays.equals(langKey, MoreFuncFrame.TONE_KEY_EN)
                    || java.util.Arrays.equals(langKey, MoreFuncFrame.TONE_KEY_ZH);
            case MoreFuncFrame.CMD_LDAC:          // 0x23 / 0x24：data=[01/00]（len 1 bool）
            case MoreFuncFrame.CMD_DUAL_DEVICE:
                return dataLen == 1 && ((frame[10] & 255) == 0 || (frame[10] & 255) == 1);
            case MoreFuncFrame.CMD_CLEAR_PAIRING: // 0x03 [推断] 清除配对：data=[02]（实验性从严）
                return dataLen == 1 && (frame[10] & 255) == 0x02;
            default:
                return false;
        }
    }

    /** 批量同步写（opcode 0xC0）：body=SN+item{len,cmdid,data}，只放行 cmdid 2（按键三元组/
     *  45B 全表）与 cmdid 1（设备名 1..20B）两种 item——固件表 C 还有一项 cmdid 12，语义
     *  未钉（docs/morefunc-protocol.md §3），不放行。 */
    private static boolean isBatchWriteSetter(byte[] frame, int n) {
        if (n < 4 || n > 1 + 2 + 254) return false;             // SN + item 头 + data
        int itemLen = frame[8] & 255;                           // body[1] = item len（含 cmdid）
        if (itemLen < 2 || 2 + itemLen != n) return false;      // n = SN(1) + len字节(1) + itemLen
        int cmdId = frame[9] & 255;
        int dataLen = itemLen - 1;
        switch (cmdId) {
            case KeyRemapFrame.BATCH_CMDID:                     // 按键：3B 三元组或 45B 全表
                if (dataLen == 3)
                    return KeyRemapFrame.isValidTriple(frame[10] & 255, frame[11] & 255, frame[12] & 255);
                return dataLen == KeyRemapFrame.TABLE_LEN;
            case MoreFuncFrame.BATCH_CMDID_NAME:                // 设备名：1..20B（面板 maxLength 从严）
                return dataLen >= 1 && dataLen <= MoreFuncFrame.NAME_MAX_LEN;
            default:
                return false;
        }
    }

    /** 批量同步读（opcode 0xC1）：body=SN+mask(4B 大端)，只放行按键表 mask 00 00 00 04。 */
    private static boolean isBatchReadSetter(byte[] frame, int n) {
        if (n != 5) return false;
        int mask = ((frame[8] & 255) << 24) | ((frame[9] & 255) << 16) | ((frame[10] & 255) << 8) | (frame[11] & 255);
        return mask == KeyRemapFrame.ADV_TYPE2_MASK;
    }

    /** 0xC5 恢复/查找族：body=SN+{01 恢复出厂, 02 找耳机}，其它子命令不放行。 */
    private static boolean isResetFamilySetter(byte[] frame, int n) {
        if (n != 2) return false;
        int sub = frame[8] & 255;
        return sub == MoreFuncFrame.RESET_SUBCMD_FACTORY || sub == MoreFuncFrame.RESET_SUBCMD_FIND;
    }

    /** ADV type2 写（候选 A）：body=SN+mask(4B 大端=00 00 00 04)+[key,gesture,action]，共 8B。 */
    private static boolean isAdvType2Setter(byte[] frame, int n) {
        if (n != 8) return false;
        int mask = ((frame[8] & 255) << 24) | ((frame[9] & 255) << 16) | ((frame[10] & 255) << 8) | (frame[11] & 255);
        if (mask != KeyRemapFrame.ADV_TYPE2_MASK) return false;
        return KeyRemapFrame.isValidTriple(frame[12] & 255, frame[13] & 255, frame[14] & 255);
    }
}
