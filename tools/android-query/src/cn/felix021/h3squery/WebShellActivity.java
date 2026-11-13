package cn.felix021.h3squery;

import android.Manifest;
import android.annotation.SuppressLint;
import android.app.Activity;
import android.content.pm.PackageManager;
import android.os.Bundle;
import android.util.Log;
import android.webkit.ConsoleMessage;
import android.webkit.WebChromeClient;
import android.webkit.WebSettings;
import android.webkit.WebView;

/**
 * R3 独立 app 的 WebView 壳：加载本地 assets/index.html，注入 H3SBridge 为 JS 全局 "h3s"。
 * 页面（UI）用 HTML5 迭代，壳只封装组帧与传输接线（路 AU）。
 *
 * 传输接线：bridge 注入 AndroidBleTransport（状态机 + AndroidGattLink 真机粘合），
 * 目标地址=构建注入的 DeviceConfig.ADDRESS（不开任意设备连接）。连接由页面显式触发
 * （h3s.connect()），写命令仍全部过武装门控 + EqWritePolicy 白名单——本壳不自动连接、
 * 不自动发送。异步结果（response/mtu/disconnected/send_failed）经 ResultSink 用
 * evaluateJavascript 推给页面的 window.onH3sEvent。
 *
 * 权限：进入即请求 BLUETOOTH_SCAN/CONNECT（照 MainActivity 先例；Android 12 以下是
 * 安装时授予的普通权限，请求直接通过）。连接时 transport 会再查一次并经断线事件报原因。
 *
 * 生命周期：onDestroy 停扫描、断开并释放 GATT（transport.disconnect），再销毁 WebView。
 *
 * 诚实标注：真机联调未做（红线：授权前不连真机）——本壳的传输链路离线验证 =
 * H3SBridge/AndroidBleTransport 单测（TransportContractTest）+ Android 侧类按 android.jar
 * 编译检查；真机行为（回调时序/栈特异性）待授权联调。
 */
public class WebShellActivity extends Activity {
    static final String TAG = "H3SWebShell";
    private WebView web;
    private H3SBridge bridge;
    private AndroidBleTransport transport;

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
        web.addJavascriptInterface(bridge, "h3s");
        web.setWebChromeClient(new WebChromeClient() {
            @Override public boolean onConsoleMessage(ConsoleMessage m) {
                Log.d(TAG, m.lineNumber() + ": " + m.message());
                return true;
            }
        });
        setContentView(web);
        web.loadUrl("file:///android_asset/index.html");
        if (!permissionsGranted())
            requestPermissions(new String[]{Manifest.permission.BLUETOOTH_SCAN, Manifest.permission.BLUETOOTH_CONNECT}, 1);
    }

    private boolean permissionsGranted() {
        return checkSelfPermission(Manifest.permission.BLUETOOTH_SCAN) == PackageManager.PERMISSION_GRANTED
            && checkSelfPermission(Manifest.permission.BLUETOOTH_CONNECT) == PackageManager.PERMISSION_GRANTED;
    }

    @Override public void onBackPressed() {
        if (web != null && web.canGoBack()) web.goBack();
        else super.onBackPressed();
    }

    @Override protected void onDestroy() {
        if (transport != null) transport.disconnect();   // 停扫描 + 断开 + 释放 GATT（绑定释放）
        transport = null;
        bridge = null;
        if (web != null) web.destroy();
        super.onDestroy();
    }
}
