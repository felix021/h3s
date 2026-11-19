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
 * R3 独立 app 的 WebView 壳（launcher）：加载本地 assets/index.html（耳机/USB/助听/固件四 tab，
 * 0.7.3 起固件升级独立 tab），全部 UI 在 WebView 渲染，原生零 UI。注入四个 JS bridge：
 *   - "h3s"（H3SBridge + AndroidBleTransport）：耳机 tab——连接/查询/EQ/ANC/按键/音量/模式；
 *   - "h3susb"（H3sUsbBridge + AndroidUsbLink）：USB tab——枚举/授权/INQUIRY/ROM 只读
 *     （FB06 装载 loader + FD07 窗读 + FD05 备份，双读一致；无 Flash 写路径）；
 *   - "h3sha"（H3sToneBridge）：助听 tab——单频正弦测试音 + 静态样本播放（TTS/公共领域录音，见 assets/samples/manifest.json），AudioTrack
 *     尽量路由到内置扬声器（TYPE_BUILTIN_SPEAKER）绕开 BLE A2DP；
 *   - "h3sota"（OtaWebBridge）：固件 tab——版本查询（h3s 桥 03 命令）/固定候选清单
 *     （UpgradeGate.REGISTRY）/SAF 选文件过 sha 门/准备-确认-进度（UpgradeService 复用，
 *     本壳只接线：SAF onActivityResult 转回，见 onActivityResult）。
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
    private H3sUsbBridge.Link usbLink;
    private BroadcastReceiver usbDetachReceiver;
    private BroadcastReceiver driveReceiver;      // SIMB1E E2E 驱动口（见 registerDrive 注释）
    private java.util.concurrent.ExecutorService usbIo;
    private OtaWebBridge otaBridge;
    private H3sToneBridge toneBridge;

    @SuppressLint("SetJavaScriptEnabled") // 本地 asset 页面 + 显式 bridge，JS 必须开
    @Override protected void onCreate(Bundle savedInstanceState) {
        // Activity 主题随系统亮暗切换，WebView 的 prefers-color-scheme 才能反映系统主题
        int night = getResources().getConfiguration().uiMode
                & android.content.res.Configuration.UI_MODE_NIGHT_MASK;
        setTheme(night == android.content.res.Configuration.UI_MODE_NIGHT_YES
                ? android.R.style.Theme_Material_NoActionBar
                : android.R.style.Theme_Material_Light_NoActionBar);
        super.onCreate(savedInstanceState);
        web = new WebView(this);
        WebSettings settings = web.getSettings();
        settings.setJavaScriptEnabled(true);
        settings.setDomStorageEnabled(true); // 页面主题选择等 localStorage 持久化
        // SIMBRIDGE M1-a/M2（docs/sim-bridge-design.md §4.3/§4.4 接线点）：仿真模式经 intent
        // extra「sim」注入 SimulationGattLink/SimulationUsbLink（localhost TCP，默认 127.0.0.1:7788
        // 走 adb reverse；「sim_host/sim_port」可覆盖——AVD 直连 10.0.2.2 免 adbd 中转，联调
        // 长传输更稳）；默认不传=真机 AndroidGattLink/AndroidUsbLink，真机行为零改动。
        // 局部/字段类型保持 Link 接口（codex 复核建议）。
        final boolean sim = getIntent().getBooleanExtra("sim", false);
        String simHostValue = getIntent().getStringExtra("sim_host");
        if (simHostValue == null) simHostValue = "127.0.0.1";
        final String simHost = simHostValue;
        final int simPort = getIntent().getIntExtra("sim_port", SimulationGattLink.DEFAULT_PORT);
        // 路 BLEDISC2：目标地址默认由扫描发现（厂商 AD 0x521C/型号 0x417C 主识别，广播名/注入
        // 地址为辅——H3S 广播实测无 Local Name，官方 App 亦按厂商数据筛选）；构建注入
        // H3S_BLE_ADDRESS 时它作为优先地址（扫到即连，兼容注入构建）。GattLink 前置过滤与
        // transport 发现选优共用同一 policy 实例。
        BleDiscoveryPolicy discovery = new BleDiscoveryPolicy(DeviceConfig.ADDRESS, DeviceConfig.NAME,
            "exact".equals(DeviceConfig.NAME_MATCH));
        AndroidBleTransport.Link link = sim
            ? new SimulationGattLink(simHost, simPort, AndroidGattLink.mainScheduler())
            : new AndroidGattLink(this, discovery);
        Log.i(TAG, "sim link config: sim=" + sim + " host=" + simHost + " port=" + simPort);
        transport = new AndroidBleTransport(link, discovery, AndroidGattLink.mainScheduler(),
            new AndroidBleTransport.Logger() {
                @Override public void log(String message) { Log.i(TAG, message); }
            });
        bridge = new H3SBridge(transport, new H3SBridge.ResultSink() {
            @Override public void emit(final String json) {
                web.post(new Runnable() { public void run() { web.evaluateJavascript("window.onH3sEvent&&onH3sEvent(" + json + ")", null); } });
            }
        });
        // 连接过程状态（扫描中/发现/连接中）→ 页面 conn 事件；终态仍走 mtu/disconnected。
        transport.setProgress(new AndroidBleTransport.Progress() {
            @Override public void onConnectState(String state, String detail) {
                if (bridge != null) bridge.emitConnectState(state, detail);
            }
        });
        usbLink = sim
            ? new SimulationUsbLink(simHost, simPort, mainThreadExecutor())
            : new AndroidUsbLink(this);
        usbIo = Executors.newSingleThreadExecutor();
        // M3（SIMB3A）：写能力位只随 sim 链路开启（运行时隔离——真机构建走默认构造=只读
        // 红线；UsbScsi 两道白名单+计划守卫+server armed 门三层兜底）。
        usbBridge = new H3sUsbBridge(usbLink, new H3sUsbBridge.ResultSink() {
            @Override public void emit(final String json) {
                web.post(new Runnable() { public void run() { web.evaluateJavascript("window.onH3sUsbEvent&&onH3sUsbEvent(" + json + ")", null); } });
            }
        }, usbIo, fileSaver(), loadBundledLoader(), sim, targetLoader());
        web.addJavascriptInterface(bridge, "h3s");
        web.addJavascriptInterface(usbBridge, "h3susb");
        otaBridge = new OtaWebBridge(this, new OtaWebBridge.ResultSink() {
            @Override public void emit(final String json) {
                web.post(new Runnable() { public void run() { web.evaluateJavascript("window.onH3sOtaEvent&&onH3sOtaEvent(" + json + ")", null); } });
            }
        });
        web.addJavascriptInterface(otaBridge, "h3sota");
        toneBridge = new H3sToneBridge(this);
        web.addJavascriptInterface(toneBridge, "h3sha");
        web.setWebChromeClient(new WebChromeClient() {
            @Override public boolean onConsoleMessage(ConsoleMessage m) {
                Log.d(TAG, m.lineNumber() + ": " + m.message());
                return true;
            }
        });
        setContentView(web);
        web.loadUrl("file:///android_asset/index.html");
        registerUsbDetach();
        if (sim) {   // 仅仿真模式：DRIVE 调试口 + M3 目标镜像目录
            registerDrive();   // P0（codex 审查 2026-11-17）：仅仿真模式注册——exported 广播
                               // 含 armWrite/setAnc/writeEq 真机写入口，真机构建不得暴露
            // M3：预建外部 files/usb（宿主 adb push 放目标镜像）。须由 app 自建——shell 建的
            // 目录在 sdcardfs 下属主是 shell，app 读取会 EACCES（AVD 实测 2026-11-17）。
            File extUsb = new File(getExternalFilesDir(null), "usb");
            if (!extUsb.exists()) extUsb.mkdirs();
        }
        if (!sim && !permissionsGranted())   // 仿真模式不触 BT 权限面（AVD 上弹窗无意义，设计 §4.3）
            requestPermissions(new String[]{Manifest.permission.BLUETOOTH_SCAN, Manifest.permission.BLUETOOTH_CONNECT}, 1);
    }

    /** 主线程执行面（SimulationUsbLink 回调用；对照 AndroidUsbLink=广播 onReceive 天然主线程）。 */
    private java.util.concurrent.Executor mainThreadExecutor() {
        return new java.util.concurrent.Executor() {
            @Override public void execute(Runnable r) { runOnUiThread(r); }
        };
    }

    /**
     * SIMB1E E2E 驱动口（调试注入，非用户功能）：本 AVD 在宿主机高负载下触摸 click
     * 事件无法触达页面 JS（实测 input tap/长按 swipe 均丢，合成器滚动正常；见
     * analysis/local-checks/sim-b1e 记录）。adb broadcast 直调与页面按钮完全相同的
     * H3SBridge 方法 → 同一 transport/BLE 链路 → 同一 ResultSink → 同一 DOM 更新，
     * 仅绕过坏掉的「触摸→onclick」一环。E2E 断言仍以 UI 截图为准。
     * action=cn.felix021.h3squery.DRIVE，extras：method=connect/disconnect/arm/readStatus/
     * setAnc/writeEq/usbPlanRestore/usbConfirmRestore/usbRestore/usbReboot（writeEq 另带
     * eq_type/master_centid/bands_json；arm 带 on；readStatus 带 cmd；setAnc 带 mode；
     * usbPlanRestore 带 target 文件名；usbConfirmRestore 带 nonce）。仅 E2E 用：
     * RECEIVER_EXPORTED + 不含任何写死的私有信息。
     * P0 门（codex 审查 2026-11-17）：只在 sim 模式注册——接收器 exported 且含 armWrite 等
     * 真机写入口，任何第三方 app 都能发广播，真机构建绝不注册。
     */
    @SuppressLint("UnspecifiedRegisterReceiverFlag")
    private void registerDrive() {
        driveReceiver = new BroadcastReceiver() {
            @Override public void onReceive(Context context, Intent intent) {
                String m = intent.getStringExtra("method");
                if (m == null || bridge == null) return;
                Log.i(TAG, "DRIVE " + m);
                if ("connect".equals(m)) bridge.connect();
                else if ("disconnect".equals(m)) bridge.disconnect();
                else if ("arm".equals(m)) bridge.armWrite(intent.getBooleanExtra("on", false));
                else if ("readStatus".equals(m)) bridge.readStatus(intent.getIntExtra("cmd", 0x2f));
                else if ("setAnc".equals(m)) bridge.setAnc(intent.getIntExtra("mode", 1), 0, 0);
                else if ("writeEq".equals(m)) {
                    // 紧凑段格式 f,q,gainCentid,iir;…（am broadcast 参数长度限制，页面形状
                    // bandsJson 在 Java 侧组装，字段与页面 eqSend 完全一致）。坏串（转义
                    // 残留等）解析返回 null：记日志并忽略该次驱动，不崩进程（SIMB1G 登记）。
                    String compact = intent.getStringExtra("bands");
                    String bandsJson = intent.getStringExtra("bands_json");
                    if (bandsJson == null && compact != null) {
                        bandsJson = H3SBridge.bandsJsonFromCompact(compact);
                        if (bandsJson == null) {
                            Log.w(TAG, "DRIVE writeEq: malformed bands ignored: " + compact);
                            return;
                        }
                    }
                    // 默认 type=255（TYPE_CUSTOM_NO_HISTORY）：自定义曲线无历史时下发；type=1 是
                    // 固件「装载自定义槽」指令、载荷被忽略（docs/eq-cmd-0x22-layout.md §2.3）。
                    bridge.writeEq(intent.getIntExtra("eq_type", EqFrame.TYPE_CUSTOM_NO_HISTORY),
                        intent.getIntExtra("master_centid", 0), bandsJson);
                }
                // M3 USB 恢复卡驱动口（SIMB3A：与页面按钮同一 H3sUsbBridge 方法/事件/DOM；
                // 同受本接收器 sim-only 注册门保护——真机构建不注册）
                else if ("usbPlanRestore".equals(m) && usbBridge != null)
                    usbBridge.planRestore(intent.getStringExtra("target"));
                else if ("usbConfirmRestore".equals(m) && usbBridge != null)
                    usbBridge.confirmRestore(intent.getStringExtra("nonce"));
                else if ("usbRestore".equals(m) && usbBridge != null)
                    usbBridge.restoreFlash();
                else if ("usbReboot".equals(m) && usbBridge != null)
                    usbBridge.rebootDevice();
            }
        };
        IntentFilter filter = new IntentFilter("cn.felix021.h3squery.DRIVE");
        if (android.os.Build.VERSION.SDK_INT >= 33) registerReceiver(driveReceiver, filter, Context.RECEIVER_EXPORTED);
        else registerReceiver(driveReceiver, filter);
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

    /** 目标镜像读入器（M3 planRestore）：先外部 files/usb/<name>（adb push 直达，免 run-as），
     *  再私有 files/usb/<name>。文件名已在业务层白名单化，这里再拒一次路径分量。 */
    private H3sUsbBridge.FileLoader targetLoader() {
        return new H3sUsbBridge.FileLoader() {
            @Override public byte[] load(String name) {
                if (name == null || !name.matches("[A-Za-z0-9._-]{1,64}") || name.contains("/")) return null;
                File external = getExternalFilesDir(null);
                File[] candidates = external != null
                    ? new File[]{new File(new File(external, "usb"), name), new File(new File(getFilesDir(), "usb"), name)}
                    : new File[]{new File(new File(getFilesDir(), "usb"), name)};
                for (File f : candidates) {
                    InputStream in = null;
                    try {
                        in = new java.io.FileInputStream(f);
                        java.io.ByteArrayOutputStream out = new java.io.ByteArrayOutputStream();
                        byte[] buf = new byte[8192];
                        int n;
                        while ((n = in.read(buf)) > 0) out.write(buf, 0, n);
                        return out.toByteArray();
                    } catch (Exception e) {
                        Log.w(TAG, "target load failed " + f + ": " + e);
                    } finally { try { if (in != null) in.close(); } catch (Exception ignore) { } }
                }
                return null;
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

    /** 首次安装授权结果（问题 2）：loadUrl 先于权限请求执行，页面启动的自动扫描会赶在授权
     *  前触发并因缺权限失败。全部授予后通知页面补一次扫描；拒绝则什么都不做（页面仍可
     *  手动点「连接耳机」重试，系统会在下次点击时按需再弹授权）。仅真机构建走到这里
     *  （sim 模式不请求权限）。 */
    @Override public void onRequestPermissionsResult(int requestCode, String[] permissions, int[] grantResults) {
        super.onRequestPermissionsResult(requestCode, permissions, grantResults);
        if (requestCode != 1) return;
        boolean allGranted = grantResults != null && grantResults.length > 0;
        if (grantResults != null) {
            for (int r : grantResults) if (r != PackageManager.PERMISSION_GRANTED) allGranted = false;
        }
        if (!allGranted) return;
        web.post(new Runnable() {
            @Override public void run() {
                web.evaluateJavascript("window.onH3sPermissionReady&&onH3sPermissionReady()", null);
            }
        });
    }

    /** SAF 文件选择结果转给 h3sota 桥（升级页在 WebView 内，不再跳原生工具页）。 */
    @Override protected void onActivityResult(int request, int result, Intent data) {
        super.onActivityResult(request, result, data);
        if (request == 42 && result == RESULT_OK && data != null && data.getData() != null
                && otaBridge != null) {
            otaBridge.onFilePicked(data.getData());
        }
    }

    @Override public void onBackPressed() {
        if (web != null && web.canGoBack()) web.goBack();
        else super.onBackPressed();
    }

    @Override protected void onDestroy() {
        if (usbDetachReceiver != null) { try { unregisterReceiver(usbDetachReceiver); } catch (IllegalArgumentException ignore) { } }
        if (driveReceiver != null) { try { unregisterReceiver(driveReceiver); } catch (IllegalArgumentException ignore) { } }
        if (transport != null) transport.disconnect();   // 停扫描 + 断开 + 释放 GATT（绑定释放）
        if (usbBridge != null) usbBridge.close();
        if (usbIo != null) {
            usbIo.execute(new Runnable() { @Override public void run() { if (usbLink != null) usbLink.close(); } });
            usbIo.shutdown();
        }
        transport = null;
        bridge = null;
        usbBridge = null;
        if (toneBridge != null) { toneBridge.release(); toneBridge = null; }
        if (web != null) web.destroy();
        super.onDestroy();
    }
}
