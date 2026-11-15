package cn.felix021.h3squery;

import android.Manifest;
import android.annotation.SuppressLint;
import android.app.Activity;
import android.content.BroadcastReceiver;
import android.content.Context;
import android.content.Intent;
import android.content.IntentFilter;
import android.content.pm.PackageManager;
import android.os.Bundle;
import android.util.Log;
import android.webkit.ConsoleMessage;
import android.webkit.WebChromeClient;
import android.webkit.WebSettings;
import android.webkit.WebView;
import java.io.File;
import java.io.FileOutputStream;
import java.io.InputStream;
import java.io.OutputStream;
import java.util.concurrent.Executors;

/**
 * R3 独立 app 的 WebView 壳（launcher）：加载本地 assets/index.html（耳机/USB 双 tab），
 * 全部 UI 在 WebView 渲染，原生零 UI。注入两个 JS bridge：
 *   - "h3s"（H3SBridge + AndroidBleTransport）：耳机 tab——连接/查询/EQ/ANC/按键/音量/模式；
 *   - "h3susb"（H3sUsbBridge + AndroidUsbLink）：USB tab——枚举/授权/INQUIRY/ROM 只读
 *     （FB06 装载 loader + FD07 窗读 + FD05 备份，双读一致；无 Flash 写路径）。
 *
 * USB 资产：assets/loader/br28loader.bin（构建时按 SHA256 打包、可缺省）→ loader 缺失时
 * USB 读操作返回明确错误，不影响其余功能。USB 插拔广播 → onH3sUsbEvent 事件（usb_detached）。
 * 备份落盘：app 私有目录 files/usb/（run-as 可取，不进任何公共存储）。
 *
 * 权限：进入即请求 BLUETOOTH_SCAN/CONNECT（照 MainActivity 先例）；USB 权限按设备
 * UsbManager.requestPermission 运行时弹窗（页面触发）。
 *
 * 生命周期：onDestroy 停扫描、断开 BLE、关 USB、注销广播，再销毁 WebView。
 *
 * 诚实标注：BLE 真机联调与 USB OTG 物理链路均未验证（红线：授权前不连真机/不刷机）——
 * 离线验证 = H3SBridge/AndroidBleTransport/H3sUsbBridge/UsbScsi 单测 + android.jar 编译检查。
 */
public class WebShellActivity extends Activity {
    static final String TAG = "H3SWebShell";
    private WebView web;
    private H3SBridge bridge;
    private AndroidBleTransport transport;
    private H3sUsbBridge usbBridge;
    private AndroidUsbLink usbLink;
    private BroadcastReceiver usbDetachReceiver;
    private java.util.concurrent.ExecutorService usbIo;

    @SuppressLint("SetJavaScriptEnabled") // 本地 asset 页面 + 显式 bridge，JS 必须开
    @Override protected void onCreate(Bundle savedInstanceState) {
        super.onCreate(savedInstanceState);
        web = new WebView(this);
        WebSettings settings = web.getSettings();
        settings.setJavaScriptEnabled(true);
        AndroidGattLink link = new AndroidGattLink(this, DeviceConfig.ADDRESS);
        transport = new AndroidBleTransport(link, AndroidGattLink.mainScheduler(),
            new AndroidBleTransport.Logger() {
                @Override public void log(String message) { Log.i(TAG, message); }
            });
        bridge = new H3SBridge(transport, new H3SBridge.ResultSink() {
            @Override public void emit(final String json) {
                web.post(new Runnable() { public void run() { web.evaluateJavascript("window.onH3sEvent&&onH3sEvent(" + json + ")", null); } });
            }
        });
        usbLink = new AndroidUsbLink(this);
        usbIo = Executors.newSingleThreadExecutor();
        usbBridge = new H3sUsbBridge(usbLink, new H3sUsbBridge.ResultSink() {
            @Override public void emit(final String json) {
                web.post(new Runnable() { public void run() { web.evaluateJavascript("window.onH3sUsbEvent&&onH3sUsbEvent(" + json + ")", null); } });
            }
        }, usbIo, fileSaver(), loadBundledLoader());
        web.addJavascriptInterface(bridge, "h3s");
        web.addJavascriptInterface(usbBridge, "h3susb");
        web.addJavascriptInterface(shellInterface(), "h3sshell");
        web.setWebChromeClient(new WebChromeClient() {
            @Override public boolean onConsoleMessage(ConsoleMessage m) {
                Log.d(TAG, m.lineNumber() + ": " + m.message());
                return true;
            }
        });
        setContentView(web);
        web.loadUrl("file:///android_asset/index.html");
        registerUsbDetach();
        if (!permissionsGranted())
            requestPermissions(new String[]{Manifest.permission.BLUETOOTH_SCAN, Manifest.permission.BLUETOOTH_CONNECT}, 1);
    }

    /** USB 插拔 → 桥状态作废 + 页面 usb_detached 事件。 */
    @SuppressLint("UnspecifiedRegisterReceiverFlag") // 系统广播 action；minSdk 26 无需 RECEIVER_EXPORTED
    private void registerUsbDetach() {
        usbDetachReceiver = AndroidUsbLink.detachReceiver(new H3sUsbBridge.Callbacks() {
            @Override public void onPermission(String key, boolean granted) { /* 走 link 内部接收器 */ }
            @Override public void onDetached(String key) { usbBridge.notifyDetached(key); }
        });
        IntentFilter filter = new IntentFilter(android.hardware.usb.UsbManager.ACTION_USB_DEVICE_DETACHED);
        if (android.os.Build.VERSION.SDK_INT >= 33) registerReceiver(usbDetachReceiver, filter, Context.RECEIVER_NOT_EXPORTED);
        else registerReceiver(usbDetachReceiver, filter);
    }

    /** 备份/窗读落盘器：app 私有 files/usb/<name>（0600 语义由 app 私有目录保证）。 */
    private H3sUsbBridge.FileSaver fileSaver() {
        return new H3sUsbBridge.FileSaver() {
            @Override public String save(String name, byte[] data) {
                File dir = new File(getFilesDir(), "usb");
                if (!dir.exists()) dir.mkdirs();
                File out = new File(dir, name);
                OutputStream os = null;
                try {
                    os = new FileOutputStream(out);
                    os.write(data);
                    return out.getAbsolutePath();
                } catch (Exception e) {
                    Log.w(TAG, "save failed: " + e);
                    return null;
                } finally {
                    try { if (os != null) os.close(); } catch (Exception ignore) { }
                }
            }
        };
    }

    /** 读构建内置的 BR28 loader（assets/loader/br28loader.bin，可缺省；摘要由桥校验）。 */
    private byte[] loadBundledLoader() {
        InputStream in = null;
        try {
            in = getAssets().open("loader/br28loader.bin");
            java.io.ByteArrayOutputStream out = new java.io.ByteArrayOutputStream();
            byte[] buf = new byte[8192];
            int n;
            while ((n = in.read(buf)) > 0) out.write(buf, 0, n);
            return out.toByteArray();
        } catch (Exception e) {                                    // 资产缺省（无网络构建）：读操作报错，不装死
            Log.w(TAG, "bundled loader missing: " + e);
            return null;
        } finally {
            try { if (in != null) in.close(); } catch (Exception ignore) { }
        }
    }

    private boolean permissionsGranted() {
        return checkSelfPermission(Manifest.permission.BLUETOOTH_SCAN) == PackageManager.PERMISSION_GRANTED
            && checkSelfPermission(Manifest.permission.BLUETOOTH_CONNECT) == PackageManager.PERMISSION_GRANTED;
    }

    /** 壳自身的 JS 接口（注入名 "h3sshell"）：页面按钮跳原生升级工具页（UpgradeGate 门控流程）。 */
    private Object shellInterface() {
        return new Object() {
            @android.webkit.JavascriptInterface
            public void openUpgradeTool() {
                startActivity(new Intent(WebShellActivity.this, MainActivity.class));
            }
        };
    }

    @Override public void onBackPressed() {
        if (web != null && web.canGoBack()) web.goBack();
        else super.onBackPressed();
    }

    @Override protected void onDestroy() {
        if (usbDetachReceiver != null) { try { unregisterReceiver(usbDetachReceiver); } catch (IllegalArgumentException ignore) { } }
        if (transport != null) transport.disconnect();   // 停扫描 + 断开 + 释放 GATT（绑定释放）
        if (usbBridge != null) usbBridge.close();
        if (usbIo != null) {
            usbIo.execute(new Runnable() { @Override public void run() { if (usbLink != null) usbLink.close(); } });
            usbIo.shutdown();
        }
        transport = null;
        bridge = null;
        usbBridge = null;
        if (web != null) web.destroy();
        super.onDestroy();
    }
}
