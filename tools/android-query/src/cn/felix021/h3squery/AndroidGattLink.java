package cn.felix021.h3squery;

import android.Manifest;
import android.bluetooth.BluetoothAdapter;
import android.bluetooth.BluetoothDevice;
import android.bluetooth.BluetoothGatt;
import android.bluetooth.BluetoothGattCallback;
import android.bluetooth.BluetoothGattCharacteristic;
import android.bluetooth.BluetoothGattDescriptor;
import android.bluetooth.BluetoothGattService;
import android.bluetooth.BluetoothManager;
import android.bluetooth.BluetoothProfile;
import android.bluetooth.le.BluetoothLeScanner;
import android.bluetooth.le.ScanCallback;
import android.bluetooth.le.ScanFilter;
import android.bluetooth.le.ScanResult;
import android.bluetooth.le.ScanSettings;
import android.content.Context;
import android.content.pm.PackageManager;
import android.os.Handler;
import java.util.UUID;
import android.os.Looper;

/**
 * {@link AndroidBleTransport.Link} 的真机实现：android.bluetooth 薄粘合，全部形状照
 * MainActivity/UpgradeService 先例（这两个类是对这台 H3S 实测过的完整先例）。本文件是
 * R3 传输链上唯一接触 android.bluetooth 的代码——因此它不进 test.sh 的纯 JVM 编译集
 * （同 MainActivity/UpgradeService/WebShellActivity），只在 build.sh 按 android.jar 编译；
 * 状态机逻辑全在 AndroidBleTransport（纯 JVM，假 Link 单测覆盖）。
 *
 * 目标门槛（README 清单第 8 条）：只连构造时传入的 address（构建注入的 DeviceConfig.ADDRESS，
 * 照 UpgradeService「禁止连接未确认设备」先例）——扫描也按该地址过滤，不扫/不连任意设备。
 *
 * 线程模型：平台回调（Binder 线程来的 BluetoothGattCallback/ScanCallback）统一 main.post
 * 后再调 Link.Callbacks；Link 的动作方法由 transport 在主线程调用（transport 注入的
 * Scheduler 就是主线程 Handler，见 AndroidBleTransport 线程约定），故动作直接执行。
 * notReadyReason() 允许任意线程（只读查询）。
 *
 * 诚实标注：本粘合层未连真机（红线）——离线只验证「编译通过 + 状态机契约（假 Link）」，
 * 真机行为（回调时序、栈特异性）待授权联调。
 */
public class AndroidGattLink implements AndroidBleTransport.Link {
    // QCY BLE 服务/特征（docs/protocol.md 实测；与 MainActivity.SERVICE/WRITE/NOTIFY/CCCD 同值。
    // 本地声明而不是引用 MainActivity：MainActivity 依赖 JL OTA SDK（构建机才有），R3 传输粘合
    // 保持独立可在本机按 android.jar 单独编译检查）。
    static final UUID SERVICE = UUID.fromString("0000a002-0000-1000-8000-00805f9b34fb");
    static final UUID WRITE = UUID.fromString("00000001-0000-1000-8000-00805f9b34fb");
    static final UUID NOTIFY = UUID.fromString("00000002-0000-1000-8000-00805f9b34fb");
    static final UUID CCCD = UUID.fromString("00002902-0000-1000-8000-00805f9b34fb");

    private final Context context;
    private final String address;
    private final Handler main;
    private Callbacks cb;
    private BluetoothGatt gatt;
    private BluetoothGattCharacteristic tx, rx;
    private int txProperties;
    private BluetoothLeScanner scanner;
    private boolean scanning;

    public AndroidGattLink(Context context, String address) {
        this.context = context;
        this.address = address;
        this.main = new Handler(Looper.getMainLooper());
    }

    /** 主线程调度器（transport 的 Scheduler 注入用；postDelayed(0)=post）。 */
    public static AndroidBleTransport.Scheduler mainScheduler() {
        final Handler h = new Handler(Looper.getMainLooper());
        return new AndroidBleTransport.Scheduler() {
            @Override public void postDelayed(long delayMs, Runnable r) { h.postDelayed(r, delayMs); }
        };
    }

    @Override public void setCallbacks(Callbacks callbacks) { this.cb = callbacks; }

    @Override public String notReadyReason() {
        if (context.checkSelfPermission(Manifest.permission.BLUETOOTH_CONNECT) != PackageManager.PERMISSION_GRANTED
                || context.checkSelfPermission(Manifest.permission.BLUETOOTH_SCAN) != PackageManager.PERMISSION_GRANTED)
            return "缺少附近设备权限（BLUETOOTH_CONNECT/SCAN）";
        BluetoothAdapter adapter = adapter();
        if (adapter == null) return "设备不支持蓝牙";
        if (!adapter.isEnabled()) return "蓝牙未开启";
        if (!BluetoothAdapter.checkBluetoothAddress(address)) return "目标地址无效: " + address;
        return null;
    }

    /** 按目标地址过滤扫描（不扫任意设备；服务 UUID 过滤不加——H3S 广播是否含 A002 [未钉]，地址过滤即精确门槛）。 */
    @Override public void startScan() {
        BluetoothLeScanner s = scanner();
        if (s == null) { post(new Runnable() { public void run() { if (cb != null) cb.onScanFailed(-1); } }); return; }
        scanning = true;
        s.startScan(java.util.Collections.singletonList(new ScanFilter.Builder().setDeviceAddress(address).build()),
            new ScanSettings.Builder().setScanMode(ScanSettings.SCAN_MODE_LOW_LATENCY).build(), scanCallback);
    }

    @Override public void stopScan() {
        if (!scanning) return;
        scanning = false;
        BluetoothLeScanner s = scanner();
        if (s != null) try { s.stopScan(scanCallback); } catch (Exception ignored) { /* 已停止 */ }
    }

    private final ScanCallback scanCallback = new ScanCallback() {
        @Override public void onScanResult(int callbackType, ScanResult result) {
            if (cb == null || result.getDevice() == null || !address.equals(result.getDevice().getAddress())) return;
            post(new Runnable() { public void run() { if (cb != null) cb.onScanResult(); } });
        }
        @Override public void onScanFailed(int errorCode) {
            post(new Runnable() { public void run() { if (cb != null) cb.onScanFailed(errorCode); } });
        }
    };

    @Override public void connectGatt() {
        BluetoothAdapter adapter = adapter();
        if (adapter == null) { postState(0, BluetoothProfile.STATE_DISCONNECTED); return; }
        gatt = adapter.getRemoteDevice(address).connectGatt(context, false, gattCallback, BluetoothDevice.TRANSPORT_LE);
        if (gatt == null) postState(0, BluetoothProfile.STATE_DISCONNECTED);   // connectGatt 返回空（照 UpgradeService 处理）
    }

    @Override public boolean discoverServices() {
        return gatt != null && gatt.discoverServices();
    }

    @Override public boolean characteristicsPresent() { return tx != null && rx != null; }

    @Override public int writeProperties() { return txProperties; }

    @Override public boolean enableNotifications() {
        if (gatt == null || rx == null) return false;
        if (!gatt.setCharacteristicNotification(rx, true)) return false;
        BluetoothGattDescriptor cccd = rx.getDescriptor(CCCD);
        if (cccd == null) return false;
        cccd.setValue(BluetoothGattDescriptor.ENABLE_NOTIFICATION_VALUE);
        return gatt.writeDescriptor(cccd);
    }

    @Override public boolean requestMtu(int mtu) {
        return gatt != null && gatt.requestMtu(mtu);
    }

    @Override public boolean writeChunk(byte[] chunk, int writeType) {
        if (gatt == null || tx == null) return false;
        tx.setWriteType(writeType);
        tx.setValue(chunk);
        return gatt.writeCharacteristic(tx);
    }

    @Override public void closeGatt() {
        stopScan();
        BluetoothGatt old = gatt;
        gatt = null; tx = null; rx = null;
        if (old != null) { old.disconnect(); old.close(); }                    // 触发的断开回调在 transport IDLE 相位被忽略
    }

    // ================================ 平台回调翻译（统一过主线程） ================================

    private final BluetoothGattCallback gattCallback = new BluetoothGattCallback() {
        @Override public void onConnectionStateChange(BluetoothGatt g, int status, int state) { postState(status, state); }
        @Override public void onServicesDiscovered(BluetoothGatt g, int status) { postServices(status); }
        @Override public void onDescriptorWrite(BluetoothGatt g, BluetoothGattDescriptor d, int status) {
            if (!CCCD.equals(d.getUuid())) return;                             // 只认 CCCD（照 MainActivity 过滤）
            post(new Runnable() { public void run() { if (cb != null) cb.onDescriptorWrite(status); } });
        }
        @Override public void onMtuChanged(BluetoothGatt g, int mtu, int status) {
            post(new Runnable() { public void run() { if (cb != null) cb.onMtuChanged(mtu, status); } });
        }
        @Override public void onCharacteristicWrite(BluetoothGatt g, BluetoothGattCharacteristic c, int status) {
            post(new Runnable() { public void run() { if (cb != null) cb.onCharacteristicWrite(status); } });
        }
        @Override public void onCharacteristicChanged(BluetoothGatt g, BluetoothGattCharacteristic c, byte[] value) {
            receive(c, value);
        }
        @Override public void onCharacteristicChanged(BluetoothGatt g, BluetoothGattCharacteristic c) { // API<33
            receive(c, c.getValue());
        }
        private void receive(BluetoothGattCharacteristic c, byte[] value) {
            if (value == null || !NOTIFY.equals(c.getUuid())) return;
            final byte[] copy = value.clone();
            post(new Runnable() { public void run() { if (cb != null) cb.onCharacteristicChanged(copy); } });
        }
    };

    /** 服务发现成功的翻译：解析 A002/1/2 与写特征属性（transport 只看 characteristicsPresent/writeProperties）。 */
    private void postServices(final int status) {
        post(new Runnable() { public void run() {
            if (status == 0 && gatt != null) {
                BluetoothGattService service = gatt.getService(SERVICE);
                tx = service == null ? null : service.getCharacteristic(WRITE);
                rx = service == null ? null : service.getCharacteristic(NOTIFY);
                txProperties = tx == null ? 0 : tx.getProperties();
            }
            if (cb != null) cb.onServicesDiscovered(status);
        }});
    }

    private void postState(final int status, final int state) {
        post(new Runnable() { public void run() { if (cb != null) cb.onConnectionStateChange(status, state); } });
    }

    private void post(Runnable r) { main.post(r); }

    private BluetoothAdapter adapter() {
        BluetoothManager manager = (BluetoothManager) context.getSystemService(Context.BLUETOOTH_SERVICE);
        return manager == null ? null : manager.getAdapter();
    }

    private BluetoothLeScanner scanner() {
        BluetoothAdapter adapter = adapter();
        BluetoothLeScanner s = adapter == null ? null : adapter.getBluetoothLeScanner();  // 蓝牙关时为 null
        this.scanner = s;
        return s;
    }
}
