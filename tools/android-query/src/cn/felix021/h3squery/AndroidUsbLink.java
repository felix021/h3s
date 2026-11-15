package cn.felix021.h3squery;

import android.annotation.SuppressLint;
import android.app.PendingIntent;
import android.content.BroadcastReceiver;
import android.content.Context;
import android.content.Intent;
import android.content.IntentFilter;
import android.hardware.usb.UsbConstants;
import android.hardware.usb.UsbDevice;
import android.hardware.usb.UsbDeviceConnection;
import android.hardware.usb.UsbEndpoint;
import android.hardware.usb.UsbInterface;
import android.hardware.usb.UsbManager;
import java.util.HashMap;
import org.json.JSONArray;
import org.json.JSONException;
import org.json.JSONObject;

/**
 * H3sUsbBridge.Link 的 Android 实现：R3 USB tab 唯一接触 android.hardware.usb 的文件
 * （照 AndroidGattLink 先例——平台接触面收敛在一个粘合类，纯 JVM 桥可离线单测）。
 *
 * 覆盖面（骨架，物理链路未验证）：
 *   - 枚举：UsbManager.getDeviceList()，按 3654:4a55（QCY 普通）/ 4c4a:3442（BR28 ROM）
 *     标记身份；key = "vid:pid:/dev/bus/usb 路径"（deviceKey 由 UsbManager.getDeviceKeyId?
 *     没有——用 device.getDeviceId()，重启后可能变，scan() 重新取）。
 *   - 授权：UsbManager.requestPermission + PendingIntent 广播（结果经 Callbacks.onPermission）。
 *   - 打开：openDevice + claimInterface(接口 0, force=true)。
 *   - bulk 管道：优先用接口描述符里的 BULK 端点（OUT=0x01/IN=0x81 实测值兜底），
 *     bulkTransfer 定时 5000ms（usbfs.py 同值）。
 *   - 进入 ROM 的厂商控制传输：bmRequestType=0x83 bRequest=0x00 wValue=0xfc wIndex=0xfe
 *     （docs/usb-recovery.md「进入 ROM 的准确控制请求」；wLength=8 能力读 / 0 进入请求）。
 *
 * 诚实标注：手机 OTG 连耳机进 ROM 模式的物理链路**未验证**——端点号/接口号按实测桌面
 * 值兜底、真机行为（枚举稳定性、claim 冲突、-71 协议错）待联调；本类不含任何 Flash
 * 写路径。
 */
public class AndroidUsbLink implements H3sUsbBridge.Link {
    /** 期望的 bulk 端点（docs/usb-recovery.md：MSC 08/06/50，IN 81 / OUT 01，64B 包）——
     *  实际以接口描述符为准，描述符缺失时 open 失败，不臆造端点。 */
    private static final int TIMEOUT_MS = 5000;
    /** USB 授权广播 action（app 私有）。 */
    public static final String ACTION_USB_PERMISSION = "cn.felix021.h3squery.USB_PERMISSION";

    private final UsbManager usb;
    private final Context context;
    private H3sUsbBridge.Callbacks callbacks;      // 主线程约定：广播 onReceive 天然主线程
    private UsbDeviceConnection connection;
    private UsbInterface claimed;
    private UsbEndpoint epOut, epIn;

    public AndroidUsbLink(Context context) {
        this.context = context.getApplicationContext();
        this.usb = (UsbManager) this.context.getSystemService(Context.USB_SERVICE);
    }

    /** scan/open 共用的 key：设备名（UsbManager 设备键，枚举期间稳定）。 */
    private static String keyOf(UsbDevice d) { return d.getDeviceName(); }

    @Override public String scanJson() {
        JSONArray list = new JSONArray();
        if (usb == null) return list.toString();
        HashMap<String, UsbDevice> devices = usb.getDeviceList();
        if (devices == null) return list.toString();
        for (UsbDevice d : devices.values()) {
            try {
                list.put(new JSONObject()
                    .put("key", keyOf(d))
                    .put("vid", d.getVendorId())
                    .put("pid", d.getProductId())
                    .put("name", safeName(d)));
            } catch (JSONException impossible) { throw new RuntimeException(impossible); }
        }
        return list.toString();
    }

    private static String safeName(UsbDevice d) {
        String name = d.getProductName();
        return name == null ? "" : name;
    }

    @Override public boolean hasPermission(String key) {
        UsbDevice d = find(key);
        return d != null && usb.hasPermission(d);
    }

    @SuppressLint("UnspecifiedRegisterReceiverFlag") // app 私有 action；minSdk 26 无需 RECEIVER_EXPORTED
    @Override public void requestPermission(final String key) {
        final UsbDevice d = find(key);
        if (d == null) {
            if (callbacks != null) callbacks.onPermission(key, false);
            return;
        }
        BroadcastReceiver receiver = new BroadcastReceiver() {
            @Override public void onReceive(Context c, Intent intent) {
                try { context.unregisterReceiver(this); } catch (IllegalArgumentException ignore) { }
                if (!ACTION_USB_PERMISSION.equals(intent.getAction())) return;
                UsbDevice granted = intent.getParcelableExtra(UsbManager.EXTRA_DEVICE);
                boolean ok = intent.getBooleanExtra(UsbManager.EXTRA_PERMISSION_GRANTED, false)
                    && granted != null && keyOf(granted).equals(key);
                if (callbacks != null) callbacks.onPermission(key, ok);
            }
        };
        IntentFilter filter = new IntentFilter(ACTION_USB_PERMISSION);
        ContextCompatRegister.register(context, receiver, filter);
        PendingIntent pi = PendingIntent.getBroadcast(context, 0,
            new Intent(ACTION_USB_PERMISSION).setPackage(context.getPackageName()),
            PendingIntent.FLAG_UPDATE_CURRENT | PendingIntent.FLAG_IMMUTABLE);
        usb.requestPermission(d, pi);
    }

    /** minSdk 26 直接 registerReceiver；API 33+ 走带 flag 重载（编译面兼容，运行面未验证）。 */
    private static final class ContextCompatRegister {
        static void register(Context c, BroadcastReceiver r, IntentFilter f) {
            if (android.os.Build.VERSION.SDK_INT >= 33) c.registerReceiver(r, f, Context.RECEIVER_NOT_EXPORTED);
            else c.registerReceiver(r, f);
        }
    }

    private UsbDevice find(String key) {
        if (usb == null || key == null) return null;
        HashMap<String, UsbDevice> devices = usb.getDeviceList();
        if (devices == null) return null;
        for (UsbDevice d : devices.values()) if (keyOf(d).equals(key)) return d;
        return null;
    }

    @Override public boolean open(String key) {
        close();
        UsbDevice d = find(key);
        if (d == null) return false;
        if (!usb.hasPermission(d)) return false;
        connection = usb.openDevice(d);
        if (connection == null) return false;
        if (d.getInterfaceCount() < 1) { close(); return false; }
        claimed = d.getInterface(0);                       // MSC 单接口（08/06/50）
        if (!connection.claimInterface(claimed, true)) { close(); return false; }
        for (int i = 0; i < claimed.getEndpointCount(); i++) {
            UsbEndpoint e = claimed.getEndpoint(i);
            if (e.getType() == UsbConstants.USB_ENDPOINT_XFER_BULK) {
                if (e.getDirection() == UsbConstants.USB_DIR_OUT && epOut == null) epOut = e;
                if (e.getDirection() == UsbConstants.USB_DIR_IN && epIn == null) epIn = e;
            }
        }
        if (epOut == null || epIn == null) {               // 描述符无 bulk 端点：不臆造端点号
            close();
            return false;
        }
        return true;
    }

    @Override public void close() {
        if (connection != null) {
            try { if (claimed != null) connection.releaseInterface(claimed); } catch (Exception ignore) { }
            try { connection.close(); } catch (Exception ignore) { }
        }
        connection = null; claimed = null; epOut = null; epIn = null;
    }

    @Override public UsbScsi.Channel channel() {
        final UsbDeviceConnection c = connection;
        if (c == null) return null;
        return new UsbScsi.Channel() {
            @Override public int bulkOut(byte[] data) {
                int len = data.length;
                int off = 0;
                while (off < len) {                        // bulkTransfer 单次上限 16KiB，保守分片
                    int n = c.bulkTransfer(epOut, data, off, Math.min(len - off, 16384), TIMEOUT_MS);
                    if (n < 0) return off;
                    off += n;
                }
                return off;
            }
            @Override public byte[] bulkIn(int max) {
                byte[] buf = new byte[max];
                int n = c.bulkTransfer(epIn, buf, max, TIMEOUT_MS);
                if (n < 0) return new byte[0];
                byte[] out = new byte[n];
                System.arraycopy(buf, 0, out, 0, n);
                return out;
            }
        };
    }

    @Override public byte[] recoveryCapability() {
        if (connection == null) return null;
        byte[] buf = new byte[8];
        int n = connection.controlTransfer(0x83, 0x00, 0xfc, 0xfe, buf, 8, 250);
        return n == 8 ? buf : null;
    }

    /** 一次性进入请求（wLength=0）。请求后设备重启，连接即作废——调用方负责 close。 */
    @Override public boolean enterRomRequest() {
        if (connection == null) return false;
        int n = connection.controlTransfer(0x83, 0x00, 0xfc, 0xfe, null, 0, 250);
        return n >= 0;                                     // -71 协议错也可能已进入：以重扫身份为准
    }

    @Override public void setCallbacks(H3sUsbBridge.Callbacks cb) { this.callbacks = cb; }

    /** 供壳注册的插拔广播：转发 onDetached（key=设备名）。 */
    public static BroadcastReceiver detachReceiver(final H3sUsbBridge.Callbacks cb) {
        return new BroadcastReceiver() {
            @Override public void onReceive(Context c, Intent intent) {
                if (!UsbManager.ACTION_USB_DEVICE_DETACHED.equals(intent.getAction())) return;
                UsbDevice d = intent.getParcelableExtra(UsbManager.EXTRA_DEVICE);
                if (cb != null) cb.onDetached(d == null ? null : d.getDeviceName());
            }
        };
    }
}
