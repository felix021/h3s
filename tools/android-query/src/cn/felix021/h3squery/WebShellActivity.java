package cn.felix021.h3squery;

import android.annotation.SuppressLint;
import android.app.Activity;
import android.os.Bundle;
import android.util.Log;
import android.webkit.ConsoleMessage;
import android.webkit.WebChromeClient;
import android.webkit.WebSettings;
import android.webkit.WebView;

/**
 * R3 独立 app 的 WebView 壳：加载本地 assets/index.html，注入 H3SBridge 为 JS 全局 "h3s"。
 * 页面（UI）用 HTML5 迭代，壳只封装组帧能力——本壳不连真机（bridge 纯组帧，见 H3SBridge 注释）。
 * 不引第三方库；WebView 相关逻辑只有「加载 + 注入 + console 转日志」，无业务分支，
 * 因此不进 JVM 单测（无 android.jar/WebView 运行时），组帧逻辑由 H3SBridgeTest 覆盖。
 */
public class WebShellActivity extends Activity {
    static final String TAG = "H3SWebShell";
    private WebView web;
    private final H3SBridge bridge = new H3SBridge();

    @SuppressLint("SetJavaScriptEnabled") // 本地 asset 页面 + 显式 bridge，JS 必须开
    @Override protected void onCreate(Bundle savedInstanceState) {
        super.onCreate(savedInstanceState);
        web = new WebView(this);
        WebSettings settings = web.getSettings();
        settings.setJavaScriptEnabled(true);
        web.addJavascriptInterface(bridge, "h3s");
        web.setWebChromeClient(new WebChromeClient() {
            @Override public boolean onConsoleMessage(ConsoleMessage m) {
                Log.d(TAG, m.lineNumber() + ": " + m.message());
                return true;
            }
        });
        setContentView(web);
        web.loadUrl("file:///android_asset/index.html");
    }

    @Override public void onBackPressed() {
        if (web != null && web.canGoBack()) web.goBack(); else super.onBackPressed();
    }

    @Override protected void onDestroy() {
        if (web != null) web.destroy();
        super.onDestroy();
    }
}
