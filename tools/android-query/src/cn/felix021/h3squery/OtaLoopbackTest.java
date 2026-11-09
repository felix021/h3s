package cn.felix021.h3squery;

import android.bluetooth.*;
import android.content.Context;
import android.os.*;
import com.jieli.jl_bt_ota.impl.BluetoothOTAManager;
import com.jieli.jl_bt_ota.interfaces.*;
import com.jieli.jl_bt_ota.model.BluetoothOTAConfigure;
import com.jieli.jl_bt_ota.model.base.*;
import org.json.JSONObject;
import java.nio.ByteBuffer;
import java.util.*;
import java.util.function.BiConsumer;

/** Official SDK end-to-end test with an in-memory peer. No GATT/socket/radio write method exists here. */
public final class OtaLoopbackTest {
    final Context context;
    final Handler main=new Handler(Looper.getMainLooper());
    final BiConsumer<String,Object> report;
    final byte[] image=new byte[4096];
    final Set<Integer> observed=new HashSet<>();
    BluetoothDevice peer;
    FakeManager sdk;
    boolean done, started, eof;
    int offset, pending, deviceSn=160, frames;
    public OtaLoopbackTest(Context c,BiConsumer<String,Object> logger) { context=c; report=logger; for(int i=0;i<image.length;i++) image[i]=(byte)((i*31)^(i>>>8)); }
    public void start() {
        try {
            peer=((BluetoothManager)context.getSystemService(Context.BLUETOOTH_SERVICE)).getAdapter().getRemoteDevice(MainActivity.ADDRESS);
            sdk=new FakeManager();
            sdk.registerBluetoothCallback(new BtEventCallback() {
                @Override public void onConnection(BluetoothDevice d,int state) { main.post(() -> {
                    if(state!=1 || started || done) return; started=true;
                    sdk.getBluetoothOption().setFirmwareFileData(image);
                    sdk.startOTA(new IUpgradeCallback() {
                        @Override public void onStartOTA() { report.accept("loopback_start","4096 dummy bytes; memory transport only"); }
                        @Override public void onNeedReconnect(String mac,boolean ble) { fail("Unexpected reconnect"); }
                        @Override public void onProgress(int type,float percent) { }
                        @Override public void onStopOTA() { main.postDelayed(() -> {
                            if(done) return;
                            if(offset!=image.length || !observed.containsAll(Arrays.asList(227,229,230,231,232))) fail("Incomplete OTA flow "+observed+" bytes="+offset);
                            else finish(true,"SDK enter/update-size/data/result/reboot/completion passed");
                        },100); }
                        @Override public void onCancelOTA() { if(!done) fail("SDK canceled"); }
                        @Override public void onError(BaseError e) { fail(e.toString()); }
                    });
                }); }
                @Override public void onError(BaseError e) { fail(e.toString()); }
            });
            sdk.onBtDeviceConnection(peer,1);
            main.postDelayed(() -> { if(!done) fail("Loopback timed out"); },30000);
        } catch(Exception e) { fail(e.toString()); }
    }
    static byte[] fromHex(String s) { byte[] b=new byte[s.length()/2]; for(int i=0;i<b.length;i++) b[i]=(byte)Integer.parseInt(s.substring(i*2,i*2+2),16); return b; }
    void feed(boolean command,int op,int sn,byte[] data) {
        if(done) return;
        int n=data.length+(command?1:2); byte[] f=new byte[n+8];
        f[0]=(byte)0xfe; f[1]=(byte)0xdc; f[2]=(byte)0xba; f[3]=(byte)(command?0xc0:0);
        f[4]=(byte)op; f[5]=(byte)(n>>8); f[6]=(byte)n;
        if(command) f[7]=(byte)sn; else { f[7]=0; f[8]=(byte)sn; }
        System.arraycopy(data,0,f,command?8:9,data.length); f[f.length-1]=(byte)0xef;
        main.postDelayed(() -> { if(!done) sdk.onReceiveDeviceData(peer,f); },5);
    }
    void requestBlock() {
        pending=Math.min(512,image.length-offset);
        if(pending==0) { eof=true; feed(true,229,deviceSn++ & 255,new byte[6]); }
        else feed(true,229,deviceSn++ & 255,ByteBuffer.allocate(6).putInt(offset).putShort((short)pending).array());
    }
    void outbound(byte[] frame) {
        if(done) return;
        try {
            if(!UpgradePolicy.permitted(frame,true)) throw new IllegalStateException("Armed policy rejected SDK frame "+MainActivity.hex(frame));
            int n=((frame[5]&255)<<8)|(frame[6]&255), op=frame[4]&255; boolean command=(frame[3]&128)!=0;
            frames++; observed.add(op); int sn=frame[command?7:8]&255;
            if(op>=227 && UpgradePolicy.permitted(frame,false)) throw new IllegalStateException("Unarmed policy allowed OTA");
            report.accept("loopback_sdk_frame",MainActivity.hex(frame));
            if(command) {
                switch(op) {
                    case 3:
                        // Synthetic normal-mode identity and double-backup capabilities; no real authKey.
                        feed(false,3,sn,fromHex("02002005010000000107040000000100000305011902060a03071b3404080100010409000000050a1c52417c050d0110021c")); break;
                    case 225: feed(false,op,sn,new byte[6]); break;
                    case 226: feed(false,op,sn,new byte[]{0}); break;
                    case 227:
                        feed(false,op,sn,new byte[]{0});
                        main.postDelayed(() -> feed(true,232,deviceSn++ & 255,ByteBuffer.allocate(4).putInt(image.length).array()),40); break;
                    case 230: feed(false,op,sn,new byte[]{0}); break;
                    case 231: feed(false,op,sn,new byte[0]); break;
                    default: throw new IllegalStateException("Unexpected SDK command "+op);
                }
            } else if(op==232) requestBlock();
            else if(op==229) {
                if(eof) { if(n!=2) throw new IllegalStateException("EOF response not empty"); return; }
                byte[] actual=Arrays.copyOfRange(frame,9,frame.length-1);
                if(!Arrays.equals(actual,Arrays.copyOfRange(image,offset,offset+pending))) throw new IllegalStateException("Wrong block bytes at "+offset);
                offset+=pending; requestBlock();
            } else throw new IllegalStateException("Unexpected SDK response "+op);
        } catch(Exception e) { fail(e.toString()); }
    }
    void fail(String message) { main.post(() -> finish(false,message)); }
    void finish(boolean passed,String message) {
        if(done) return; done=true; main.removeCallbacksAndMessages(null);
        try { report.accept("loopback_result",new JSONObject().put("passed",passed).put("hardware_transport_used",false).put("validated_dummy_bytes",offset).put("sdk_frames",frames).put("message",message)); }
        catch(Exception e) { report.accept("loopback_error",e.toString()); }
        if(sdk!=null) { FakeManager old=sdk; sdk=null; old.release(); }
    }
    class FakeManager extends BluetoothOTAManager {
        FakeManager() { super(OtaLoopbackTest.this.context); configure(new BluetoothOTAConfigure().setPriority(0).setUseAuthDevice(false).setUseJLServer(false).setNeedChangeMtu(false).setMtu(20).setTimeoutMs(3000)); }
        @Override public BluetoothDevice getConnectedDevice() { return peer; }
        @Override public BluetoothGatt getConnectedBluetoothGatt() { return null; }
        @Override public void connectBluetoothDevice(BluetoothDevice d) { fail("Test has no hardware connection implementation"); }
        @Override public void disconnectBluetoothDevice(BluetoothDevice d) {
            if(done) return;
            if(observed.contains(231) && offset==image.length) report.accept("loopback_disconnect","SDK closes transport after simulated reboot");
            else fail("Unexpected disconnect before completion");
        }
        @Override public void errorEventCallback(BaseError e) { fail(e.toString()); }
        @Override public boolean sendDataToDevice(BluetoothDevice d,byte[] bytes) { byte[] copy=bytes.clone(); main.post(() -> outbound(copy)); return true; }
    }
}
