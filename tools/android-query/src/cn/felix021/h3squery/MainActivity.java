package cn.felix021.h3squery;

import android.Manifest;
import android.app.Activity;
import android.bluetooth.*;
import android.bluetooth.le.*;
import android.content.*;
import android.content.pm.PackageManager;
import android.net.Uri;
import android.os.*;
import android.widget.*;
import com.jieli.jl_bt_ota.impl.BluetoothOTAManager;
import com.jieli.jl_bt_ota.interfaces.*;
import com.jieli.jl_bt_ota.model.BluetoothOTAConfigure;
import com.jieli.jl_bt_ota.model.base.*;
import com.jieli.jl_bt_ota.model.response.TargetInfoResponse;
import com.jieli.jl_bt_ota.model.response.CustomResponse;
import com.jieli.jl_bt_ota.model.command.CustomCmd;
import com.jieli.jl_bt_ota.model.parameter.CustomParam;
import com.jieli.jl_bt_ota.model.FileOffset;
import com.jieli.jl_bt_ota.tool.RcspOTA;
import java.io.*;
import java.lang.reflect.Method;
import java.security.MessageDigest;
import java.util.*;
import org.json.*;

/** Capability probe. Transport only permits the explicit queries in QueryPolicy. */
public class MainActivity extends Activity {
    static final UUID SERVICE = UUID.fromString("0000a002-0000-1000-8000-00805f9b34fb");
    static final UUID WRITE = UUID.fromString("00000001-0000-1000-8000-00805f9b34fb");
    static final UUID NOTIFY = UUID.fromString("00000002-0000-1000-8000-00805f9b34fb");
    static final UUID CCCD = UUID.fromString("00002902-0000-1000-8000-00805f9b34fb");
    static final String ADDRESS = DeviceConfig.ADDRESS;
    final Handler main = new Handler(Looper.getMainLooper());
    final Set<String> seen = new HashSet<>();
    final ArrayDeque<byte[]> writes = new ArrayDeque<>();
    BluetoothAdapter adapter;
    BluetoothGatt gatt;
    BluetoothGattCharacteristic tx;
    QueryManager manager;
    TextView output;
    EditText address;
    File logFile;
    boolean scanning, ready, writeBusy, dead;
    int mtu = 23, writeType;
    TargetInfoResponse info;
    FileOffset fileFlag;
    OtaLoopbackTest loopback;

    @Override public void onCreate(Bundle b) {
        super.onCreate(b);
        logFile = new File(getFilesDir(), "query-" + System.currentTimeMillis() + ".jsonl");
        LinearLayout layout = new LinearLayout(this); layout.setOrientation(1); layout.setPadding(20, 40, 20, 12);
        address = new EditText(this); address.setSingleLine(); address.setText(ADDRESS); layout.addView(address);
        button(layout, "扫描附近设备（20 秒）", () -> scan());
        button(layout, "连接并查询", () -> connect(address.getText().toString().trim().toUpperCase(Locale.ROOT)));
        button(layout, "断开", () -> closeConnection());
        button(layout, "检查本地 uFW 文件", () -> {
            Intent pick = new Intent(Intent.ACTION_OPEN_DOCUMENT).setType("*/*").addCategory(Intent.CATEGORY_OPENABLE);
            startActivityForResult(pick, 1);
        });
        button(layout, "查询升级检查参数", () -> queryFileFlag());
        button(layout, "检查当前升级条件", () -> queryEligibility());
        button(layout, "准备本次候选升级（不写入）", () -> prepareUpgrade());
        button(layout, "查看并确认本次刷写", () -> confirmUpgrade());
        button(layout, "取消升级准备", () -> startService(new Intent(this,UpgradeService.class).setAction(UpgradeService.CANCEL)));
        output = new TextView(this); output.setTextIsSelectable(true); output.setTextSize(12);
        ScrollView scroll = new ScrollView(this); scroll.addView(output); layout.addView(scroll, new LinearLayout.LayoutParams(-1, 0, 1));
        setContentView(layout);
        adapter = ((BluetoothManager)getSystemService(BLUETOOTH_SERVICE)).getAdapter();
        log("session", "查询/升级工具 0.4.1-public；SDK 1.10.0/10932；日志 " + logFile.getName());
        if (!permissions()) requestPermissions(new String[]{Manifest.permission.BLUETOOTH_CONNECT, Manifest.permission.BLUETOOTH_SCAN}, 1);
        probeIntent(getIntent());
    }
    @Override protected void onNewIntent(Intent intent) { super.onNewIntent(intent); setIntent(intent); probeIntent(intent); }
    void probeIntent(Intent intent) {
        if (!"cn.felix021.h3squery.PROBE".equals(intent.getAction())) return;
        String operation = intent.getStringExtra("operation");
        main.post(() -> {
            try {
                log("probe_operation", operation == null ? "null" : operation);
                if ("connect".equals(operation)) connect(ADDRESS);
                else if ("flag".equals(operation)) queryFileFlag();
                else if ("eligibility".equals(operation)) queryEligibility();
                else if ("inspect-cached".equals(operation)) inspectFile(Uri.fromFile(new File(getFilesDir(), "candidate.ufw")));
                else if ("power-info".equals(operation)) queryPowerInfo();
                else if ("sleep-info".equals(operation)) querySleepInfo();
                else if ("anc-cycle-info".equals(operation)) queryAncCycle();
                else if ("anc-info".equals(operation)) queryAncInfo();
                else if ("negotiate-mtu".equals(operation)) {
                    if(gatt==null || !ready || writeBusy || !writes.isEmpty()) throw new IllegalStateException("需要已初始化且发送队列空闲的连接");
                    log("mtu_request",gatt.requestMtu(517));
                }
                else if ("disconnect".equals(operation)) closeConnection();
                else if ("prepare-upgrade".equals(operation)) prepareUpgrade();
                else if ("cancel-preparation".equals(operation)) startService(new Intent(this,UpgradeService.class).setAction(UpgradeService.CANCEL));
                else if ("upgrade-state".equals(operation)) log("upgrade_state", readUpgradeState());
                else if ("ota-loopback".equals(operation)) {
                    if(upgradeServiceRunning() || (loopback!=null && !loopback.done)) throw new IllegalStateException("已有测试或升级服务运行");
                    closeConnection(); loopback=new OtaLoopbackTest(this,(event,data) -> log(event,data)); loopback.start();
                }
                else log("error", "未知查询操作");
            } catch (Exception e) { log("probe_error", e.toString()); }
        });
    }
    void button(LinearLayout l, String label, Runnable action) {
        Button b = new Button(this); b.setText(label); b.setOnClickListener(v -> {
            try { action.run(); } catch (Exception e) { log("error", e.toString()); }
        }); l.addView(b);
    }
    boolean permissions() { return checkSelfPermission(Manifest.permission.BLUETOOTH_CONNECT) == PackageManager.PERMISSION_GRANTED && checkSelfPermission(Manifest.permission.BLUETOOTH_SCAN) == PackageManager.PERMISSION_GRANTED; }
    synchronized void log(String event, Object value) {
        try {
            JSONObject j = new JSONObject().put("time_ms", System.currentTimeMillis()).put("event", event).put("data", value);
            try (FileOutputStream out = new FileOutputStream(logFile, true)) { out.write((j + "\n").getBytes("UTF-8")); }
            android.util.Log.i("H3SQuery", j.toString());
            main.post(() -> { if (!dead) { output.append(event + ": " + value + "\n"); } });
        } catch (Exception e) { android.util.Log.e("H3SQuery", "log", e); }
    }
    static String hex(byte[] data) { StringBuilder s = new StringBuilder(); for (byte b : data) s.append(String.format(Locale.ROOT, "%02x", b & 255)); return s.toString(); }
    void scan() {
        if (!permissions() || !adapter.isEnabled()) { log("error", "需要蓝牙权限并开启蓝牙"); return; }
        stopScan(); seen.clear(); scanning = true;
        adapter.getBluetoothLeScanner().startScan(null, new ScanSettings.Builder().setScanMode(ScanSettings.SCAN_MODE_LOW_LATENCY).build(), scanCallback);
        log("scan", "started"); main.postDelayed(() -> stopScan(), 20000);
    }
    void stopScan() { if (scanning) { adapter.getBluetoothLeScanner().stopScan(scanCallback); scanning = false; log("scan", "stopped"); } }
    final ScanCallback scanCallback = new ScanCallback() {
        @Override public void onScanResult(int type, ScanResult r) {
            String mac = r.getDevice().getAddress(); if (!seen.add(mac)) return;
            log("advertisement", mac + " name=" + r.getDevice().getName() + " rssi=" + r.getRssi() + " raw=" + (r.getScanRecord() == null ? "" : hex(r.getScanRecord().getBytes())));
        }
        @Override public void onScanFailed(int error) { scanning = false; log("scan_error", error); }
    };
    void connect(String mac) {
        if (upgradeServiceRunning()) { log("error", "升级服务正在占用连接，请先完成或取消准备"); return; }
        if (!permissions()) { log("error", "需要蓝牙权限"); return; }
        if (!BluetoothAdapter.checkBluetoothAddress(mac)) throw new IllegalArgumentException("蓝牙地址无效");
        stopScan(); closeConnection();
        log("connect", mac); gatt = adapter.getRemoteDevice(mac).connectGatt(this, false, gattCallback, BluetoothDevice.TRANSPORT_LE);
        BluetoothGatt attempt = gatt;
        main.postDelayed(() -> { if (gatt == attempt && !ready) { log("timeout", "连接/初始化超过 30 秒"); closeConnection(); } }, 30000);
    }
    boolean upgradeServiceRunning() {
        android.app.ActivityManager am=(android.app.ActivityManager)getSystemService(ACTIVITY_SERVICE);
        for(android.app.ActivityManager.RunningServiceInfo s:am.getRunningServices(100))
            if(s.service.getClassName().equals(UpgradeService.class.getName())) return true;
        return false;
    }
    JSONObject readUpgradeState() throws Exception {
        try(InputStream in=new FileInputStream(new File(getFilesDir(),UpgradeService.STATE_FILE)); ByteArrayOutputStream out=new ByteArrayOutputStream()) {
            byte[] b=new byte[4096]; int n;
            while((n=in.read(b))!=-1) { if(out.size()+n>32768) throw new IOException("升级状态文件过大"); out.write(b,0,n); }
            return new JSONObject(new String(out.toByteArray(),"UTF-8"));
        }
    }
    void prepareUpgrade() {
        if(!permissions()) { log("error", "需要蓝牙权限"); return; }
        if(upgradeServiceRunning()) { log("error", "已有升级服务，请查看状态或取消准备"); return; }
        stopScan(); closeConnection();
        startForegroundService(new Intent(this,UpgradeService.class).setAction(UpgradeService.PREPARE));
        log("upgrade_prepare", "服务将核对私有目录中的 candidate.ufw 并重读设备状态；尚未刷写");
    }
    void confirmUpgrade() {
        try {
            JSONObject plan=readUpgradeState();
            if(!upgradeServiceRunning() || !"PREPARED".equals(plan.optString("state"))) { log("error", "没有有效的升级计划，请先准备"); return; }
            String token=plan.getString("nonce"); JSONObject device=plan.getJSONObject("device");
            String detail="设备：本次确认的 QCY H3S\n电量："+device.getInt("battery")+"%\n版本：0.1.1.8 同版本更新\n修改："+UpgradeGate.DESCRIPTION+"\nSHA-256："+plan.getString("sha256")+"\n\n已验证此前修正版 OTA 成功。本次修改按键循环表一个字节，行为和同版本更新尚未实测；失败恢复仍未验证。版本号不会改变，需要实际按键验证。\n\n确认后立即复查设备，并开始实际写入。";
            new android.app.AlertDialog.Builder(this).setTitle("确认实际刷入候选固件").setMessage(detail)
                .setNegativeButton("取消",null)
                .setPositiveButton("确认刷入",(dialog,which) -> startService(new Intent(this,UpgradeService.class).setAction(UpgradeService.CONFIRM).putExtra("nonce",token))).show();
        } catch(Exception e) { log("error", "无法读取升级计划："+e); }
    }
    final BluetoothGattCallback gattCallback = new BluetoothGattCallback() {
        @Override public void onConnectionStateChange(BluetoothGatt g, int status, int state) { main.post(() -> {
            if (g != gatt) return;
            log("gatt_state", "status=" + status + " state=" + state);
            if (status == 0 && state == BluetoothProfile.STATE_CONNECTED) {
                if (!g.discoverServices()) { log("error", "discoverServices rejected"); closeConnection(); }
            } else if (state == BluetoothProfile.STATE_DISCONNECTED || status != 0) closeConnection();
        }); }
        @Override public void onServicesDiscovered(BluetoothGatt g, int status) { main.post(() -> {
            if (g != gatt) return;
            log("services_status", status); if (status != 0) { closeConnection(); return; }
            for (BluetoothGattService s : g.getServices()) for (BluetoothGattCharacteristic c : s.getCharacteristics())
                log("gatt_characteristic", s.getUuid() + "/" + c.getUuid() + " properties=" + c.getProperties());
            BluetoothGattService service = g.getService(SERVICE);
            tx = service == null ? null : service.getCharacteristic(WRITE);
            BluetoothGattCharacteristic rx = service == null ? null : service.getCharacteristic(NOTIFY);
            if (tx == null || rx == null) { log("error", "缺少 QCY A002/1/2 特征"); closeConnection(); return; }
            writeType = (tx.getProperties() & BluetoothGattCharacteristic.PROPERTY_WRITE) != 0 ? BluetoothGattCharacteristic.WRITE_TYPE_DEFAULT : BluetoothGattCharacteristic.WRITE_TYPE_NO_RESPONSE;
            if (!g.setCharacteristicNotification(rx, true)) { log("error", "开启通知失败"); closeConnection(); return; }
            BluetoothGattDescriptor d = rx.getDescriptor(CCCD);
            if (d == null) { log("error", "CCCD missing"); closeConnection(); return; }
            d.setValue(BluetoothGattDescriptor.ENABLE_NOTIFICATION_VALUE);
            if (!g.writeDescriptor(d)) { log("error", "CCCD write rejected"); closeConnection(); }
        }); }
        @Override public void onDescriptorWrite(BluetoothGatt g, BluetoothGattDescriptor d, int status) { main.post(() -> {
            if (g != gatt || !CCCD.equals(d.getUuid())) return;
            log("cccd", status); if (status != 0) { closeConnection(); return; }
            ready = true;
            manager = new QueryManager();
            manager.registerBluetoothCallback(new BtEventCallback() {
                @Override public void onConnection(BluetoothDevice device, int state) { main.post(() -> {
                    log("sdk_connection", state); if (state == 1 && manager != null) recordInfo(manager.getDeviceInfo());
                }); }
                @Override public void onError(BaseError e) { log("sdk_error", e.toString()); }
                @Override public void onMandatoryUpgrade(BluetoothDevice d) { log("mandatory_upgrade", "设备报告需要升级；工具继续保持查询模式"); }
                @Override public void onReceiveCommand(BluetoothDevice d, CommandBase c) { log("sdk_command", c.toString()); }
            });
            manager.onBtDeviceConnection(g.getDevice(), 1);
        }); }
        @Override public void onCharacteristicChanged(BluetoothGatt g, BluetoothGattCharacteristic c, byte[] value) { receive(g, c, value); }
        @Override public void onMtuChanged(BluetoothGatt g,int value,int status) { main.post(() -> {
            if(g!=gatt) return;
            log("gatt_mtu","status="+status+" mtu="+value);
            if(status==0 && value>=23) mtu=value;
        }); }
        @Override public void onCharacteristicChanged(BluetoothGatt g, BluetoothGattCharacteristic c) { receive(g, c, c.getValue()); }
        @Override public void onCharacteristicWrite(BluetoothGatt g, BluetoothGattCharacteristic c, int status) { main.post(() -> {
            if (g != gatt) return; log("write_result", status);
            if (status != 0) { closeConnection(); return; } writes.poll(); writeBusy = false; pump();
        }); }
    };
    void receive(BluetoothGatt g, BluetoothGattCharacteristic c, byte[] value) {
        if (value == null || !NOTIFY.equals(c.getUuid())) return;
        byte[] copy = value.clone(); main.post(() -> {
            if (g != gatt) return; log("rx", hex(copy)); if (manager != null) manager.onReceiveDeviceData(g.getDevice(), copy);
        });
    }
    void pump() {
        if (writeBusy || writes.isEmpty() || gatt == null) return;
        writeBusy = true; byte[] chunk = writes.peek(); tx.setWriteType(writeType); tx.setValue(chunk);
        log("tx_chunk", hex(chunk)); if (!gatt.writeCharacteristic(tx)) { log("error", "GATT write rejected"); closeConnection(); return; }
        byte[] pending = chunk;
        main.postDelayed(() -> { if (writeBusy && writes.peek() == pending) { log("timeout", "GATT write callback missing"); closeConnection(); } }, 5000);
    }
    static boolean permitted(byte[] data) {
        return QueryPolicy.permitted(data);
    }
    class QueryManager extends BluetoothOTAManager {
        QueryManager() {
            super(MainActivity.this);
            configure(new BluetoothOTAConfigure().setPriority(0).setUseAuthDevice(false).setUseJLServer(false).setNeedChangeMtu(false).setMtu(20).setTimeoutMs(3000).setBleIntervalMs(500));
        }
        @Override public BluetoothDevice getConnectedDevice() { return gatt == null ? null : gatt.getDevice(); }
        @Override public BluetoothGatt getConnectedBluetoothGatt() { return gatt; }
        @Override public void connectBluetoothDevice(BluetoothDevice d) { log("blocked_reconnect", d.getAddress()); }
        @Override public void disconnectBluetoothDevice(BluetoothDevice d) { main.post(() -> closeConnection()); }
        @Override public void errorEventCallback(BaseError e) { log("sdk_error_event", e.toString()); }
        @Override public boolean sendDataToDevice(BluetoothDevice d, byte[] data) {
            if (data == null || !permitted(data)) { log("blocked_tx", data == null ? "null" : hex(data)); return false; }
            byte[] copy = data.clone(); main.post(() -> {
                if (manager != this || !ready || gatt == null || !gatt.getDevice().equals(d)) return;
                log("tx_frame", hex(copy));
                for (int p = 0; p < copy.length; p += mtu - 3) writes.add(Arrays.copyOfRange(copy, p, Math.min(copy.length, p + mtu - 3)));
                pump();
            }); return true;
        }
    }
    void recordInfo(TargetInfoResponse value) {
        if (value == null) { log("error", "SDK 未返回设备信息"); return; } info = value;
        JSONObject obj = new JSONObject();
        for (Method m : value.getClass().getDeclaredMethods()) {
            if (m.getParameterTypes().length == 0 && (m.getName().startsWith("get") || m.getName().startsWith("is"))) {
                try { obj.put(m.getName(), m.invoke(value)); } catch (Exception e) { log("field_error", m.getName() + ":" + e); }
            }
        }
        log("device_info", obj);
        manager.sendCommandAsync(new CustomCmd(new CustomParam(new byte[]{(byte)0xfe, 1, 47})), new CommandCallback() {
            @Override public void onErrCode(BaseError e) { log("battery_error", e.toString()); }
            @Override public void onCommandResponse(CommandBase c) {
                byte[] data = c.getResponse() instanceof CustomResponse ? ((CustomResponse)c.getResponse()).getData() : null;
                log("battery_response", "status=" + c.getStatus() + " raw=" + (data == null ? "null" : hex(data)));
                if (c.getStatus() == 0 && data != null && data.length == 5 && data[0] == 47 && data[1] == 3) {
                    JSONArray slots = new JSONArray();
                    for (int i = 2; i < 5; i++) {
                        try { slots.put(new JSONObject().put("slot", i - 2).put("percent", data[i] & 127).put("charging", (data[i] & 128) != 0)); }
                        catch (JSONException e) { log("battery_error", e.toString()); }
                    }
                    log("battery_slots", slots);
                }
            }
        });
    }
    void queryFileFlag() {
        if (manager == null || info == null) { log("error", "先连接并查询设备"); return; }
        new RcspOTA(manager).readUpgradeFileFlag(new IActionCallback<FileOffset>() {
            @Override public void onError(BaseError e) { log("file_flag_error", e.toString()); }
            @Override public void onSuccess(FileOffset value) { main.post(() -> { fileFlag = value; log("file_flag", value.toString()); }); }
        });
    }
    void queryPowerInfo() {
        if(manager==null || info==null) { log("error","先连接并查询设备"); return; }
        manager.sendCommandAsync(new CustomCmd(new CustomParam(new byte[]{(byte)0xfe,1,20})),new CommandCallback() {
            @Override public void onErrCode(BaseError e) { log("power_error",e.toString()); }
            @Override public void onCommandResponse(CommandBase c) {
                byte[] b=c.getResponse() instanceof CustomResponse?((CustomResponse)c.getResponse()).getData():null;
                log("power_response","status="+c.getStatus()+" raw="+(b==null?"null":hex(b)));
                if(c.getStatus()==0 && b!=null && b.length==6 && b[0]==20 && b[1]==4) {
                    try { log("power_info",new JSONObject().put("powerTime_raw",(b[2]&255)|((b[3]&255)<<8)).put("curTime_raw",(b[4]&255)|((b[5]&255)<<8))); }
                    catch(JSONException e) { log("power_error",e.toString()); }
                }
            }
        });
    }
    void querySleepInfo() {
        if(manager==null || info==null) { log("error","先连接并查询设备"); return; }
        manager.sendCommandAsync(new CustomCmd(new CustomParam(new byte[]{(byte)0xfe,1,16})),new CommandCallback() {
            @Override public void onErrCode(BaseError e) { log("sleep_error",e.toString()); }
            @Override public void onCommandResponse(CommandBase c) {
                byte[] b=c.getResponse() instanceof CustomResponse?((CustomResponse)c.getResponse()).getData():null;
                log("sleep_response","status="+c.getStatus()+" raw="+(b==null?"null":hex(b)));
            }
        });
    }
    void queryAncCycle() {
        if(manager==null || info==null || info.getPid()!=16764) { log("error","先连接并查询 H3S"); return; }
        // The OTA subset omits ADV_GET_INFO; register the vendor's generic RCSP parser.
        com.jieli.jl_bt_ota.constant.Command.getValidCommandList().put(193,new com.jieli.jl_bt_ota.model.cmdHandler.RcspCmdHandler());
        CommandBase command=new CommandWithParamAndResponse<CustomParam,CommonResponse>(193,"ReadAncCycle",new CustomParam(new byte[]{0,0,4,0}));
        manager.sendCommandAsync(command,new CommandCallback() {
            @Override public void onErrCode(BaseError e) { log("anc_cycle_error",e.toString()); }
            @Override public void onCommandResponse(CommandBase c) {
                CommonResponse response=c.getResponse();
                byte[] b=response==null?null:response.getRawData();
                log("anc_cycle_response","status="+c.getStatus()+" raw="+(b==null?"null":hex(b)));
            }
        });
    }
    void queryAncInfo() {
        if(manager==null || info==null || info.getPid()!=16764) { log("error","先连接并查询 H3S"); return; }
        manager.sendCommandAsync(new CustomCmd(new CustomParam(new byte[]{(byte)0xfe,1,23})),new CommandCallback() {
            @Override public void onErrCode(BaseError e) { log("anc_error",e.toString()); }
            @Override public void onCommandResponse(CommandBase c) {
                byte[] b=c.getResponse() instanceof CustomResponse?((CustomResponse)c.getResponse()).getData():null;
                log("anc_response","status="+c.getStatus()+" raw="+(b==null?"null":hex(b)));
            }
        });
    }
    void queryEligibility() {
        if (manager == null || info == null || info.getPid() != 16764 || fileFlag == null || fileFlag.getOffset() != 0 || fileFlag.getLen() != 0) {
            log("error", "需要先读取 H3S 设备信息和升级检查参数；本工具仅实现 offset=0/len=0 的 BLE 条件查询"); return;
        }
        // Same E2 payload used by the official SDK when E1 returned offset=0,len=0.
        // Deliberately use RcspOTA directly; BluetoothOTAManager.startOTA would advance to E3.
        new RcspOTA(manager).inquiryDeviceCanOTA(new byte[]{0}, new IActionCallback<Integer>() {
            @Override public void onError(BaseError e) { log("eligibility_error", e.toString()); }
            @Override public void onSuccess(Integer code) {
                String[] meanings = {"当前条件允许请求升级", "电量过低", "文件检查失败", "版本相同", "TWS 未连接", "不在充电盒中", "已有升级进行中", "设备处于双连接"};
                log("eligibility", "code=" + code + " " + (code >= 0 && code < meanings.length ? meanings[code] : "未知结果") + "；未发送固件内容，未验证候选文件或降级支持");
            }
        });
    }
    void closeConnection() {
        ready = false; info = null; fileFlag = null; writes.clear(); writeBusy = false;
        QueryManager old = manager; manager = null;
        if (old != null) old.release();
        BluetoothGatt oldGatt = gatt; gatt = null; tx = null; mtu = 23;
        if (oldGatt != null) { oldGatt.disconnect(); oldGatt.close(); log("disconnect", "closed"); }
    }
    @Override protected void onActivityResult(int request, int result, Intent data) {
        super.onActivityResult(request, result, data);
        if (request == 1 && result == RESULT_OK && data != null) inspectFile(data.getData());
    }
    void inspectFile(Uri uri) {
        new Thread(() -> {
            try (InputStream in = getContentResolver().openInputStream(uri)) {
                MessageDigest digest = MessageDigest.getInstance("SHA-256"); byte[] buf = new byte[8192]; int n, total = 0;
                while ((n = in.read(buf)) > 0) { total += n; if (total > 16*1024*1024) throw new IOException("文件超过 16 MiB"); digest.update(buf, 0, n); }
                String hash = hex(digest.digest());
                boolean official = hash.equals("65cafc7f9a7545c5c30701d141c77db334e37065589e42c51306132c94c879af");
                boolean candidate = hash.equals(UpgradeGate.SHA);
                log("file_check", new JSONObject().put("bytes", total).put("sha256", hash).put("known", official || candidate).put("kind", official ? "official_0.1.1.8" : candidate ? "anc_on_trans_UNTESTED_0.1.1.8" : "unknown").put("flash_enabled", false));
                main.post(() -> log("compatibility", !official && !candidate ? "未识别的文件，无法确定型号和版本" : info == null ? "设备信息未读取，兼容性未知" : "设备型号ID=" + info.getPid() + "，版本=" + info.getVersionName() + "；已知文件对应 H3S 0.1.1.8；该修改包已在一台 H3S 验证，其他设备兼容性与故障恢复未验证"));
            } catch (Exception e) { log("file_error", e.toString()); }
        }, "file-inspection").start();
    }
    @Override public void onDestroy() { stopScan(); closeConnection(); dead = true; super.onDestroy(); }
}
