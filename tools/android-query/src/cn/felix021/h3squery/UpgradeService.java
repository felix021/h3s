package cn.felix021.h3squery;

import android.app.*;
import android.bluetooth.*;
import android.content.*;
import android.os.*;
import android.util.AtomicFile;
import com.jieli.jl_bt_ota.impl.BluetoothOTAManager;
import com.jieli.jl_bt_ota.interfaces.*;
import com.jieli.jl_bt_ota.model.*;
import com.jieli.jl_bt_ota.model.base.*;
import com.jieli.jl_bt_ota.model.command.CustomCmd;
import com.jieli.jl_bt_ota.model.parameter.CustomParam;
import com.jieli.jl_bt_ota.model.response.*;
import com.jieli.jl_bt_ota.tool.RcspOTA;
import com.jieli.jl_bt_ota.util.CommandBuilder;
import org.json.*;
import java.io.*;
import java.security.MessageDigest;
import java.util.*;

/** Service owns transport and firmware bytes independently of Activity/lockscreen lifecycle. */
public class UpgradeService extends Service {
    static final String PREPARE="cn.felix021.h3squery.PREPARE_UPGRADE";
    static final String CONFIRM="cn.felix021.h3squery.CONFIRM_UPGRADE";
    static final String CANCEL="cn.felix021.h3squery.CANCEL_PREPARATION";
    static final String STATE_FILE="upgrade-state.json", CHANNEL="h3s-upgrade";
    final Handler main=new Handler(Looper.getMainLooper());
    final UpgradeGate gate=new UpgradeGate();
    final ArrayDeque<byte[]> queue=new ArrayDeque<>();
    BluetoothDevice peer;
    BluetoothGatt gatt;
    BluetoothGattCharacteristic tx;
    Manager sdk;
    UpgradeGate.Snapshot snapshot;
    volatile boolean ready;
    boolean busy, destroyed, sdkSuccess, rebootQueued, cccdEnabled, initializedForGatt;
    int queuedBytes, chunks, reconnects, attMtu=23;
    long sentBytes, nextWriteAt;
    byte[] firmware;
    String sha, nonce, message="尚未准备";
    File logFile;
    PowerManager.WakeLock wakeLock;
    Runnable stageTimeout, writeTimeout;

    @Override public void onCreate() {
        super.onCreate();
        logFile=new File(getFilesDir(), "upgrade-"+System.currentTimeMillis()+".jsonl");
        NotificationManager nm=(NotificationManager)getSystemService(NOTIFICATION_SERVICE);
        nm.createNotificationChannel(new NotificationChannel(CHANNEL,"H3S 固件升级",NotificationManager.IMPORTANCE_LOW));
        startForeground(701, notification("准备升级，尚未写入"));
        wakeLock=((PowerManager)getSystemService(POWER_SERVICE)).newWakeLock(PowerManager.PARTIAL_WAKE_LOCK,"h3s:upgrade");
        wakeLock.acquire(2*60*60*1000L);
        state("新会话，未恢复任何刷写授权");
    }
    @Override public int onStartCommand(Intent intent,int flags,int id) {
        if (intent==null) { stopSelf(); return START_NOT_STICKY; }
        String action=intent.getAction();
        if (PREPARE.equals(action)) {
            if (gate.state()!=UpgradeGate.State.IDLE) { log("prepare_rejected","当前会话已在进行"); return START_NOT_STICKY; }
            gate.preparing(); state("读取并核对候选文件，尚未写入"); timeout(45000,"准备超时");
            new Thread(() -> {
                try {
                    byte[] bytes;
                    try (InputStream in=new FileInputStream(new File(getFilesDir(),"candidate.ufw")); ByteArrayOutputStream out=new ByteArrayOutputStream()) {
                        byte[] buf=new byte[8192]; int n;
                        while ((n=in.read(buf))!=-1) { if (out.size()+n>UpgradeGate.FILE_BYTES) throw new IOException("候选文件过大"); out.write(buf,0,n); }
                        bytes=out.toByteArray();
                    }
                    String hash=MainActivity.hex(MessageDigest.getInstance("SHA-256").digest(bytes));
                    if (bytes.length!=UpgradeGate.FILE_BYTES || !UpgradeGate.SHA.equals(hash)) throw new IOException("候选文件大小或 SHA-256 不匹配");
                    main.post(() -> { if (!destroyed && gate.state()==UpgradeGate.State.PREPARING) {
                        firmware=bytes; sha=hash;
                        peer=((BluetoothManager)getSystemService(BLUETOOTH_SERVICE)).getAdapter().getRemoteDevice(MainActivity.ADDRESS);
                        openGatt();
                    } });
                } catch(Exception e) { main.post(() -> fail("文件准备失败："+e)); }
            },"h3s-load-image").start();
        } else if (CONFIRM.equals(action)) {
            try {
                gate.authorize(intent.getStringExtra("nonce"),SystemClock.elapsedRealtime());
                nonce=null; state("用户已确认，重新读取设备信息与准入条件，尚未写入");
                log("user_confirmation","仅对本次内存中的候选和连接有效");
                timeout(45000,"刷写前复查超时"); queryFreshInfo();
            } catch(Exception e) {
                log("confirmation_rejected",e.toString());
                if (gate.state()==UpgradeGate.State.FAILED || gate.state()==UpgradeGate.State.IDLE) {
                    gate.fail(); nonce=null; finish("确认已过期或不属于本次会话，请重新准备");
                }
            }
        } else if (CANCEL.equals(action)) {
            if (gate.armed() || gate.state()==UpgradeGate.State.RECHECKING) log("cancel_rejected","升级已开始复查或传输，不能取消准备");
            else { gate.fail(); nonce=null; finish("准备已取消，未写入"); }
        } else {
            log("action_rejected",String.valueOf(action));
            if(gate.state()==UpgradeGate.State.IDLE) { gate.fail(); finish("无效操作，未写入"); }
        }
        return START_NOT_STICKY;
    }
    Notification notification(String text) {
        PendingIntent open=PendingIntent.getActivity(this,0,new Intent(this,MainActivity.class),PendingIntent.FLAG_UPDATE_CURRENT|PendingIntent.FLAG_IMMUTABLE);
        return new Notification.Builder(this,CHANNEL).setSmallIcon(android.R.drawable.stat_sys_upload).setContentTitle("H3S 固件准备 / 升级").setContentText(text).setContentIntent(open).setOnlyAlertOnce(true).setOngoing(gate.state()!=UpgradeGate.State.FAILED && gate.state()!=UpgradeGate.State.SUCCEEDED).build();
    }
    synchronized void log(String event,Object data) {
        try {
            JSONObject row=new JSONObject().put("time_ms",System.currentTimeMillis()).put("event",event).put("data",data);
            try(FileOutputStream out=new FileOutputStream(logFile,true)) { out.write((row+"\n").getBytes("UTF-8")); }
            android.util.Log.i("H3SUpgrade",row.toString());
        } catch(Exception e) { android.util.Log.e("H3SUpgrade","log",e); }
    }
    void state(String text) {
        message=text;
        try {
            JSONObject j=new JSONObject().put("state",gate.state().name()).put("message",text).put("log",logFile.getName())
                .put("nonce",nonce==null?JSONObject.NULL:nonce).put("sha256",sha==null?JSONObject.NULL:sha)
                .put("file_bytes",firmware==null?0:firmware.length).put("armed",gate.armed()).put("time_ms",System.currentTimeMillis());
            j.put("att_mtu",attMtu).put("gatt_payload",attMtu-3);
            if (snapshot!=null) j.put("device",new JSONObject().put("pid",snapshot.pid).put("vid",snapshot.vid).put("uid",snapshot.uid).put("version",snapshot.version).put("battery",snapshot.battery).put("double_backup",snapshot.doubleBackup).put("eligibility",snapshot.eligibility));
            AtomicFile file=new AtomicFile(new File(getFilesDir(),STATE_FILE));
            FileOutputStream out=file.startWrite();
            try { out.write(j.toString(2).getBytes("UTF-8")); file.finishWrite(out); }
            catch(Exception e) { file.failWrite(out); throw e; }
            log("upgrade_state",j);
            ((NotificationManager)getSystemService(NOTIFICATION_SERVICE)).notify(701,notification(text));
        } catch(Exception e) { log("state_error",e.toString()); }
    }
    void timeout(long ms,String reason) {
        if (stageTimeout!=null) main.removeCallbacks(stageTimeout);
        stageTimeout=() -> fail(reason); main.postDelayed(stageTimeout,ms);
    }
    void fail(String reason) {
        if (destroyed || gate.state()==UpgradeGate.State.FAILED || gate.state()==UpgradeGate.State.SUCCEEDED) return;
        gate.fail(); nonce=null; finish("失败："+reason+"；未验证自动恢复，不自动重刷");
    }
    void finish(String text) {
        if (stageTimeout!=null) main.removeCallbacks(stageTimeout);
        state(text); closeTransport(true); stopSelf();
    }
    void openGatt() {
        if (destroyed || gatt!=null) return;
        state(gate.armed()?"回连已确认的 H3S":"连接 H3S，尚未写入");
        gatt=peer.connectGatt(this,false,callbacks,BluetoothDevice.TRANSPORT_LE);
        if (gatt==null) fail("connectGatt 返回空");
    }
    final BluetoothGattCallback callbacks=new BluetoothGattCallback() {
        @Override public void onConnectionStateChange(BluetoothGatt g,int status,int connection) { main.post(() -> {
            if (g!=gatt) return;
            log("gatt_state","status="+status+" state="+connection);
            if (status==0 && connection==BluetoothProfile.STATE_CONNECTED) {
                if (!g.discoverServices()) fail("发现服务请求失败");
            } else if (status!=0 || connection==BluetoothProfile.STATE_DISCONNECTED) {
                if (rebootQueued || sdkSuccess) { log("expected_disconnect","最终重启后断开，等待 SDK 完成回调"); closeTransport(false); }
                else if (gate.armed()) reconnect("GATT disconnected");
                else if (!sdkSuccess) fail("BLE 连接断开，status="+status);
            }
        }); }
        @Override public void onServicesDiscovered(BluetoothGatt g,int status) { main.post(() -> {
            if(g!=gatt) return;
            if(status!=0) { fail("发现服务失败 "+status); return; }
            BluetoothGattService service=g.getService(MainActivity.SERVICE);
            tx=service==null?null:service.getCharacteristic(MainActivity.WRITE);
            BluetoothGattCharacteristic rx=service==null?null:service.getCharacteristic(MainActivity.NOTIFY);
            if(tx==null || rx==null || (tx.getProperties()&BluetoothGattCharacteristic.PROPERTY_WRITE_NO_RESPONSE)==0) { fail("GATT 与已实测的 QCY 特征不匹配"); return; }
            if(!g.setCharacteristicNotification(rx,true)) { fail("启用通知失败"); return; }
            BluetoothGattDescriptor cccd=rx.getDescriptor(MainActivity.CCCD);
            if(cccd==null) { fail("缺少 CCCD"); return; }
            cccd.setValue(BluetoothGattDescriptor.ENABLE_NOTIFICATION_VALUE);
            if(!g.writeDescriptor(cccd)) fail("CCCD 请求失败");
        }); }
        @Override public void onDescriptorWrite(BluetoothGatt g,BluetoothGattDescriptor d,int status) { main.post(() -> {
            if(g!=gatt || !MainActivity.CCCD.equals(d.getUuid())) return;
            if(status!=0) { fail("CCCD 失败 "+status); return; }
            cccdEnabled=true;
            if(attMtu>=247) initializeSdk();
            else if(!g.requestMtu(517)) fail("MTU 协商请求被拒绝");
        }); }
        @Override public void onMtuChanged(BluetoothGatt g,int value,int status) { main.post(() -> {
            if(g!=gatt) return;
            log("gatt_mtu","status="+status+" mtu="+value);
            if(status!=0 || value<247 || value>517) { fail("未协商到已验证的大包 MTU"); return; }
            attMtu=value;
            if(cccdEnabled) initializeSdk();
        }); }
        @Override public void onCharacteristicChanged(BluetoothGatt g,BluetoothGattCharacteristic c,byte[] data) { receive(g,c,data); }
        @Override public void onCharacteristicChanged(BluetoothGatt g,BluetoothGattCharacteristic c) { receive(g,c,c.getValue()); }
        @Override public void onCharacteristicWrite(BluetoothGatt g,BluetoothGattCharacteristic c,int status) { main.post(() -> {
            if(g!=gatt) return;
            if(writeTimeout!=null) main.removeCallbacks(writeTimeout);
            if(status!=0) { fail("GATT 写入返回 "+status); return; }
            byte[] bytes=queue.poll(); if(bytes!=null) {
                queuedBytes-=bytes.length; sentBytes+=bytes.length; chunks++;
                log("gatt_chunk_completed","chunk="+chunks+" bytes="+bytes.length+" queued="+queuedBytes);
            }
            busy=false; nextWriteAt=SystemClock.elapsedRealtime()+10; pump();
        }); }
    };
    void initializeSdk() {
            if(initializedForGatt || !cccdEnabled || attMtu<247 || gatt==null) return;
            initializedForGatt=true; ready=true;
            if(sdk==null) {
                sdk=new Manager(); final Manager session=sdk;
                sdk.registerBluetoothCallback(new BtEventCallback() {
                    @Override public void onConnection(BluetoothDevice d,int connection) { main.post(() -> {
                        if(sdk!=session || destroyed) return;
                        log("sdk_connection",connection);
                        if(connection==1 && gate.state()==UpgradeGate.State.PREPARING) queryFreshInfo();
                        else if(connection==1 && gate.state()==UpgradeGate.State.RECONNECTING) {
                            if(stageTimeout!=null) main.removeCallbacks(stageTimeout);
                            gate.reconnected(); state("SDK 已回连，继续本次升级");
                        }
                    }); }
                    @Override public void onError(BaseError e) { main.post(() -> { if(sdk==session) fail("SDK："+e); }); }
                });
            }
            sdk.onBtDeviceConnection(peer,1);
    }
    void receive(BluetoothGatt g,BluetoothGattCharacteristic c,byte[] bytes) {
        if(bytes==null || !MainActivity.NOTIFY.equals(c.getUuid())) return;
        byte[] copy=bytes.clone(); main.post(() -> {
            if(g!=gatt || sdk==null) return;
            log("rx",MainActivity.hex(copy)); sdk.onReceiveDeviceData(peer,copy);
        });
    }
    void pump() {
        if(busy || queue.isEmpty() || gatt==null || !ready) return;
        long wait=nextWriteAt-SystemClock.elapsedRealtime();
        if(wait>0) { main.postDelayed(() -> pump(),wait); return; }
        busy=true; tx.setWriteType(BluetoothGattCharacteristic.WRITE_TYPE_NO_RESPONSE); tx.setValue(queue.peek());
        if(!gatt.writeCharacteristic(tx)) { fail("GATT 写入请求被拒绝"); return; }
        writeTimeout=() -> fail("GATT 写入回调超时"); main.postDelayed(writeTimeout,5000);
    }
    void queryFreshInfo() {
        if(sdk==null || !ready) { fail("SDK 尚未连接"); return; }
        final Manager session=sdk;
        sdk.sendCommandAsync(CommandBuilder.buildGetTargetInfoCmdForAll(),new CommandCallback() {
            @Override public void onErrCode(BaseError e) { postError(session,"设备信息读取失败："+e); }
            @Override public void onCommandResponse(CommandBase c) { main.post(() -> {
                if(sdk!=session) return;
                if(c.getStatus()!=0 || !(c.getResponse() instanceof TargetInfoResponse)) { fail("设备信息响应无效"); return; }
                TargetInfoResponse i=(TargetInfoResponse)c.getResponse(); snapshot=new UpgradeGate.Snapshot();
                snapshot.address=peer.getAddress(); snapshot.pid=i.getPid(); snapshot.vid=i.getVid(); snapshot.uid=i.getUid(); snapshot.version=i.getVersionCode();
                snapshot.doubleBackup=i.isSupportDoubleBackup(); snapshot.needBootloader=i.isNeedBootLoader(); snapshot.mandatory=i.isMandatoryUpgrade();
                // Reject identity/mode mismatches before querying any OTA-specific conditions.
                if(snapshot.pid!=16764 || snapshot.vid!=7250 || snapshot.uid!=7250 || snapshot.version!=UpgradeGate.EXPECTED_VERSION || !snapshot.doubleBackup || snapshot.needBootloader || snapshot.mandatory) { fail("设备身份、版本或双备份模式不匹配"); return; }
                queryBattery(session);
            }); }
        });
    }
    void queryBattery(Manager session) {
        session.sendCommandAsync(new CustomCmd(new CustomParam(new byte[]{(byte)0xfe,1,47})),new CommandCallback() {
            @Override public void onErrCode(BaseError e) { postError(session,"QCY 电量查询失败："+e); }
            @Override public void onCommandResponse(CommandBase c) { main.post(() -> {
                if(sdk!=session) return;
                byte[] b=c.getResponse() instanceof CustomResponse?((CustomResponse)c.getResponse()).getData():null;
                if(c.getStatus()!=0 || b==null || b.length!=5 || b[0]!=47 || b[1]!=3) { fail("QCY 电量响应无效"); return; }
                snapshot.battery=b[3]&127; log("battery",snapshot.battery);
                if(snapshot.battery<=0 || snapshot.battery>100) { fail("QCY 电量槽无有效读值"); return; }
                queryFlag(session);
            }); }
        });
    }
    void queryFlag(Manager session) {
        new RcspOTA(session).readUpgradeFileFlag(new IActionCallback<FileOffset>() {
            @Override public void onError(BaseError e) { postError(session,"E1 失败："+e); }
            @Override public void onSuccess(FileOffset f) { main.post(() -> {
                if(sdk!=session) return;
                snapshot.offset=f.getOffset(); snapshot.length=f.getLen(); log("file_flag",f.toString());
                if(snapshot.offset!=0 || snapshot.length!=0) { fail("E1 检查字段与已实测条件不一致"); return; }
                new RcspOTA(session).inquiryDeviceCanOTA(new byte[]{0},new IActionCallback<Integer>() {
                    @Override public void onError(BaseError e) { postError(session,"E2 失败："+e); }
                    @Override public void onSuccess(Integer code) { main.post(() -> {
                        if(sdk!=session) return;
                        snapshot.eligibility=code; log("eligibility",code);
                        try {
                            if(gate.state()==UpgradeGate.State.PREPARING) {
                                nonce=UUID.randomUUID().toString();
                                gate.prepared(snapshot,sha,firmware.length,nonce,SystemClock.elapsedRealtime());
                                state("准备完成：0.1.1.8 同版本更新 / "+UpgradeGate.DESCRIPTION+"；等待本次确认，尚未写入");
                                timeout(UpgradeGate.MAX_AGE_MS,"升级计划已过期，请重新准备");
                            } else if(gate.state()==UpgradeGate.State.RECHECKING) beginTransfer();
                            else fail("准入响应到达了无效阶段");
                        } catch(Exception e) { fail(e.toString()); }
                    }); }
                });
            }); }
        });
    }
    void postError(Manager session,String error) { main.post(() -> { if(sdk==session) fail(error); }); }
    void beginTransfer() {
        try {
            String current=MainActivity.hex(MessageDigest.getInstance("SHA-256").digest(firmware));
            if(attMtu<247 || !cccdEnabled || !ready) throw new IllegalStateException("传输连接或大包 MTU 不再有效");
            gate.begin(snapshot,current,firmware.length); // Only this transition arms the transport.
            if(stageTimeout!=null) main.removeCallbacks(stageTimeout);
            state("已确认并通过即时复查，开始官方 SDK OTA");
            sdk.getBluetoothOption().setFirmwareFileData(firmware.clone());
            sdk.startOTA(new IUpgradeCallback() {
                @Override public void onStartOTA() { log("ota_start","SDK 已开始；设备效果尚未验证"); }
                @Override public void onProgress(int type,float percent) { main.post(() -> {
                    if(!gate.armed()) return;
                    log("ota_progress","type="+type+" percent="+percent); state("升级中 "+Math.round(percent)+"%（阶段 "+type+"）");
                }); }
                @Override public void onNeedReconnect(String mac,boolean ble) { main.post(() -> {
                    if(!ble || !MainActivity.ADDRESS.equalsIgnoreCase(mac)) fail("SDK 要求未实现的地址或 SPP 回连："+mac);
                    else reconnect("SDK 请求回连");
                }); }
                @Override public void onStopOTA() { main.post(() -> {
                    sdkSuccess=true; log("ota_sdk_success","等待最终 GATT 发送完成；仍需重读版本和听音验证"); drainSuccess(0);
                }); }
                @Override public void onCancelOTA() { main.post(() -> fail("SDK 取消升级")); }
                @Override public void onError(BaseError e) { main.post(() -> fail("OTA："+e)); }
            });
        } catch(Exception e) { fail("启动升级失败："+e); }
    }
    void drainSuccess(int attempt) {
        if(destroyed || !gate.armed()) return;
        // SDK may report completion immediately before queueing its final reboot command.
        if(attempt>=2 && queue.isEmpty() && !busy) {
            gate.succeed(); log("transport_totals","bytes="+sentBytes+" chunks="+chunks);
            finish("SDK 报告完成；版本仍为 0.1.1.8，需实际验证 ANC 按键只切降噪/通透"); return;
        }
        if(attempt>=12) { fail("SDK 完成后发送队列未清空"); return; }
        main.postDelayed(() -> drainSuccess(attempt+1),500);
    }
    void reconnect(String reason) {
        if(!gate.armed() || sdkSuccess) return;
        if(gate.state()==UpgradeGate.State.RECONNECTING && gatt!=null) return;
        if(++reconnects>2) { fail("回连次数超过限制"); return; }
        gate.reconnecting(); state(reason+"；仅回连本次 H3S"); closeTransport(false);
        if(sdk!=null) sdk.onBtDeviceConnection(peer,0);
        timeout(30000,"升级回连超时");
        main.postDelayed(() -> { if(!destroyed && gate.armed() && gatt==null) openGatt(); },1000);
    }
    void closeTransport(boolean release) {
        ready=false; cccdEnabled=false; initializedForGatt=false; attMtu=23; nextWriteAt=0;
        if(writeTimeout!=null) main.removeCallbacks(writeTimeout);
        queue.clear(); queuedBytes=0; busy=false;
        BluetoothGatt oldGatt=gatt; gatt=null; tx=null;
        if(oldGatt!=null) { oldGatt.disconnect(); oldGatt.close(); }
        if(release && sdk!=null) { Manager old=sdk; sdk=null; old.release(); }
    }
    class Manager extends BluetoothOTAManager {
        Manager() { super(UpgradeService.this); configure(new BluetoothOTAConfigure().setPriority(0).setUseAuthDevice(false).setUseJLServer(false).setNeedChangeMtu(false).setMtu(20).setTimeoutMs(5000).setBleIntervalMs(500)); }
        @Override public BluetoothDevice getConnectedDevice() { return ready?peer:null; }
        @Override public BluetoothGatt getConnectedBluetoothGatt() { return gatt; }
        @Override public void errorEventCallback(BaseError e) { postError(this,"SDK event："+e); }
        @Override public void connectBluetoothDevice(BluetoothDevice d) { main.post(() -> {
            if(sdk!=this) return;
            if(!gate.armed() || !MainActivity.ADDRESS.equals(d.getAddress())) { fail("禁止连接未确认设备"); return; }
            reconnect("SDK 连接请求");
        }); }
        @Override public void disconnectBluetoothDevice(BluetoothDevice d) { main.post(() -> {
            if(sdk!=this) return;
            if(rebootQueued || sdkSuccess) { log("expected_disconnect","SDK 最终关闭连接"); closeTransport(false); }
            else if(gate.armed()) reconnect("SDK 断开后回连请求"); else fail("SDK 断开连接");
        }); }
        @Override public boolean sendDataToDevice(BluetoothDevice device,byte[] data) {
            if(!UpgradePolicy.permitted(data,gate.armed())) {
                log("blocked_tx",data==null?"null":MainActivity.hex(data));
                postError(this,"SDK 请求发送未允许的帧"); return false;
            }
            byte[] copy=data.clone(); main.post(() -> {
                if(sdk!=this || destroyed || !ready || gatt==null || !peer.equals(device)) return;
                if(!UpgradePolicy.permitted(copy,gate.armed())) { fail("发送阶段已失效"); return; }
                if(queuedBytes+copy.length>65536) { fail("BLE 发送队列超限"); return; }
                log("tx_frame",MainActivity.hex(copy));
                for(int p=0;p<copy.length;) {
                    if(gate.armed() && (copy[p+4]&255)==231) rebootQueued=true;
                    p+=8+(((copy[p+5]&255)<<8)|(copy[p+6]&255));
                }
                queue.addAll(BlePacketizer.split(copy,attMtu));
                queuedBytes+=copy.length; pump();
            }); return true;
        }
    }
    @Override public IBinder onBind(Intent intent) { return null; }
    @Override public void onDestroy() {
        destroyed=true; main.removeCallbacksAndMessages(null);
        if(gate.state()!=UpgradeGate.State.SUCCEEDED && gate.state()!=UpgradeGate.State.FAILED) { gate.fail(); nonce=null; state("服务结束，授权已失效；请重新查询设备状态"); }
        closeTransport(true);
        if(wakeLock!=null && wakeLock.isHeld()) wakeLock.release();
        stopForeground(STOP_FOREGROUND_DETACH); firmware=null; super.onDestroy();
    }
}
