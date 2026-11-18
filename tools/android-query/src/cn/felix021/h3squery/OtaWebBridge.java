package cn.felix021.h3squery;

import android.app.Activity;
import android.content.Intent;
import android.net.Uri;
import android.os.Handler;
import android.os.Looper;
import java.io.ByteArrayOutputStream;
import java.io.File;
import java.io.FileInputStream;
import java.io.FileOutputStream;
import java.io.InputStream;
import java.io.OutputStream;
import java.security.MessageDigest;
import org.json.JSONObject;

/**
 * WebView 固件 tab 的原生桥（注入名 "h3sota"）：把原 MainActivity 按钮编排换成
 * HTML 交互面。gate 全部保留在 UpgradeGate/UpgradeService，本桥只做编排：
 *   pickUfw()        SAF 选文件 + 流式 SHA-256（16 MiB 上限）→ ota_file 事件
 *                    （sha 命中 UpgradeGate.REGISTRY 钉死候选才 is_candidate=true；
 *                    未命中明确拒绝并在 note 列出允许的 sha 清单）。
 *   candidates()     返回 UpgradeGate.REGISTRY（页面渲染内置候选清单——从
 *                    审批门本体读，不存在页面与门各维护一份清单的分叉）。
 *   installCandidate()  仅当上次检查结果命中固定候选注册表时，把该 Uri 复制到
 *                    files/candidate.ufw 并回读校验 → ota_candidate 事件。
 *   prepare()        startForegroundService(UpgradeService.PREPARE)：服务核对
 *                    candidate.ufw + 连接设备 fresh-read 准入，不写入。
 *   confirm(nonce)   复核 upgrade-state.json 为 PREPARED 且 nonce 匹配后才发
 *                    UpgradeService.CONFIRM（服务侧 authorize 会再验一次）。
 *   cancel()         UpgradeService.CANCEL（已 armed/复查中不可取消，服务拒绝）。
 *   readState()      读 upgrade-state.json（含 state/message/sent_bytes/device），
 *                    页面 2s 轮询渲染进度。
 * 不新增任何任意写入通道：候选固定为 UpgradeGate.REGISTRY 钉死清单（0.7.3 固件
 * tab 起 b79 ANC 恢复镜像 + HAL5 full MPO95 两条），确认仍走
 * nonce+PREPARED+fresh-read 三重门。BLE 真机链路未验证（红线：授权前不刷机）。
 */
public class OtaWebBridge {
    /** 页面事件出口：ota_file / ota_candidate / ota_error（JSON 字符串）。 */
    public interface ResultSink { void emit(String json); }

    static final String OFFICIAL_SHA = "65cafc7f9a7545c5c30701d141c77db334e37065589e42c51306132c94c879af";
    static final int MAX_BYTES = 16 * 1024 * 1024;

    final Activity activity;
    final ResultSink sink;
    final Handler main = new Handler(Looper.getMainLooper());
    Uri picked;
    String pickedKind, pickedSha;
    int pickedBytes;

    public OtaWebBridge(Activity activity, ResultSink sink) {
        this.activity = activity;
        this.sink = sink;
    }

    static String hex(byte[] data) {
        StringBuilder s = new StringBuilder();
        for (byte b : data) s.append(String.format("%02x", b & 255));
        return s.toString();
    }

    void emit(String event, JSONObject payload) {
        try { payload.put("event", event); sink.emit(payload.toString()); }
        catch (Exception e) { android.util.Log.w("H3SOta", "emit", e); }
    }

    void emitError(String where, Exception e) {
        try { emit("ota_error", new JSONObject().put("where", where).put("error", String.valueOf(e))); }
        catch (Exception ignore) { }
    }

    /** 页面按钮触发 SAF 选择器（launcher 由壳 Activity 注册后回调 onFilePicked）。 */
    @android.webkit.JavascriptInterface
    public void pickUfw() {
        main.post(() -> {
            try {
                Intent pick = new Intent(Intent.ACTION_OPEN_DOCUMENT)
                        .setType("*/*").addCategory(Intent.CATEGORY_OPENABLE);
                activity.startActivityForResult(pick, 42); // 结果在壳 onActivityResult 转回
            } catch (Exception e) { emitError("pick", e); }
        });
    }

    /** SAF 回调入口（壳 onActivityResult 调用，主线程）。 */
    public void onFilePicked(final Uri uri) {
        picked = null; pickedKind = null; pickedSha = null; pickedBytes = 0;
        new Thread(() -> {
            try {
                JSONObject result = inspect(uri);
                pickedSha = result.optString("sha256");
                pickedKind = result.optString("kind");
                pickedBytes = result.optInt("bytes", 0);
                picked = uri;
                emit("ota_file", result);
            } catch (Exception e) { emitError("inspect", e); }
        }, "ota-inspect").start();
    }

    JSONObject inspect(Uri uri) throws Exception {
        MessageDigest digest = MessageDigest.getInstance("SHA-256");
        long total = 0;
        try (InputStream in = activity.getContentResolver().openInputStream(uri)) {
            if (in == null) throw new IllegalStateException("无法打开所选文件");
            byte[] buf = new byte[8192]; int n;
            while ((n = in.read(buf)) > 0) {
                total += n;
                if (total > MAX_BYTES) throw new IllegalStateException("文件超过 16 MiB");
                digest.update(buf, 0, n);
            }
        }
        String hash = hex(digest.digest());
        boolean official = hash.equals(OFFICIAL_SHA);
        // 固定候选门：sha+长度同时命中 UpgradeGate.REGISTRY 才算候选（int 溢出前 total 不会超 MAX_BYTES）。
        UpgradeGate.Candidate hit = UpgradeGate.candidateFor(hash, (int) total);
        String kind = hit != null ? hit.kind
                : official ? "official_0.1.1.8" : "unknown";
        return new JSONObject()
                .put("bytes", total).put("sha256", hash).put("kind", kind)
                .put("is_candidate", hit != null)
                .put("note", hit != null
                        ? hit.description + "（可设为本次候选）"
                        : official ? "官方 0.1.1.8 原版（本工具不用它刷机）；允许的候选 SHA-256：" + UpgradeGate.allowedShaList()
                        : "未识别文件：不在固定候选清单内，拒绝设为候选；允许的候选 SHA-256：" + UpgradeGate.allowedShaList());
    }

    /** 内置候选清单（UpgradeGate.REGISTRY 原样投影，页面「升级通道」卡渲染用）。 */
    @android.webkit.JavascriptInterface
    public String candidates() {
        try {
            org.json.JSONArray list = new org.json.JSONArray();
            for (UpgradeGate.Candidate c : UpgradeGate.REGISTRY)
                list.put(new JSONObject().put("sha256", c.sha).put("bytes", c.bytes)
                        .put("version", c.version).put("kind", c.kind)
                        .put("description", c.description));
            return new JSONObject().put("candidates", list).toString();
        } catch (Exception e) {
            try { return new JSONObject().put("error", String.valueOf(e)).toString(); }
            catch (Exception ignore) { return "{\"error\":\"unavailable\"}"; }
        }
    }

    /** 仅接受上次检查命中固定候选注册表的 Uri，复制到 files/candidate.ufw 并回读校验。 */
    @android.webkit.JavascriptInterface
    public void installCandidate() {
        new Thread(() -> {
            try {
                if (picked == null || pickedSha == null || pickedBytes <= 0)
                    throw new IllegalStateException("请先检查文件，且候选必须命中固定候选清单（"
                            + UpgradeGate.allowedShaList() + "）");
                final UpgradeGate.Candidate plan = UpgradeGate.candidateFor(pickedSha, pickedBytes);
                if (plan == null)
                    throw new IllegalStateException("所选文件不在固定候选清单内；允许的候选 SHA-256："
                            + UpgradeGate.allowedShaList());
                File out = new File(activity.getFilesDir(), "candidate.ufw");
                File tmp = new File(activity.getFilesDir(), "candidate.ufw.tmp");
                MessageDigest digest = MessageDigest.getInstance("SHA-256");
                long total = 0;
                try (InputStream in = activity.getContentResolver().openInputStream(picked);
                     OutputStream os = new FileOutputStream(tmp)) {
                    if (in == null) throw new IllegalStateException("无法重新打开所选文件");
                    byte[] buf = new byte[8192]; int n;
                    while ((n = in.read(buf)) > 0) {
                        total += n;
                        if (total > plan.bytes) throw new IllegalStateException("文件超过候选长度上限");
                        digest.update(buf, 0, n);
                        os.write(buf, 0, n);
                    }
                }
                String hash = hex(digest.digest());
                if (total != plan.bytes || !plan.sha.equals(hash))
                    throw new IllegalStateException("复制后校验不通过（长度或 SHA-256 不匹配）");
                if (!tmp.renameTo(out)) throw new IllegalStateException("候选落盘重命名失败");
                // 回读一遍，确认磁盘上的 candidate.ufw 与校验值一致
                JSONObject verify = inspect(android.net.Uri.fromFile(out));
                if (!plan.sha.equals(verify.optString("sha256")))
                    throw new IllegalStateException("落盘回读 SHA-256 不匹配");
                emit("ota_candidate", new JSONObject()
                        .put("bytes", total).put("sha256", hash).put("kind", plan.kind)
                        .put("description", plan.description)
                        .put("path", out.getName()).put("ready_for_prepare", true));
            } catch (Exception e) { emitError("install_candidate", e); }
        }, "ota-install").start();
    }

    boolean serviceRunning() {
        android.app.ActivityManager am = (android.app.ActivityManager)
                activity.getSystemService(Activity.ACTIVITY_SERVICE);
        for (android.app.ActivityManager.RunningServiceInfo s : am.getRunningServices(100))
            if (s.service.getClassName().equals(UpgradeService.class.getName())) return true;
        return false;
    }

    @android.webkit.JavascriptInterface
    public void prepare() {
        main.post(() -> {
            try {
                if (activity.checkSelfPermission(android.Manifest.permission.BLUETOOTH_CONNECT)
                        != android.content.pm.PackageManager.PERMISSION_GRANTED)
                    throw new IllegalStateException("需要蓝牙权限（附近设备）");
                Intent intent = new Intent(activity, UpgradeService.class).setAction(UpgradeService.PREPARE);
                activity.startForegroundService(intent);
                emit("ota_prepare", new JSONObject().put("started", true)
                        .put("note", "服务将核对 candidate.ufw 并即时读取设备准入条件；尚未刷写"));
            } catch (Exception e) { emitError("prepare", e); }
        });
    }

    String readStateJson() throws Exception {
        File file = new File(activity.getFilesDir(), UpgradeService.STATE_FILE);
        if (!file.exists()) return null;
        try (InputStream in = new FileInputStream(file); ByteArrayOutputStream out = new ByteArrayOutputStream()) {
            byte[] b = new byte[4096]; int n;
            while ((n = in.read(b)) != -1) {
                if (out.size() + n > 32768) throw new IllegalStateException("升级状态文件过大");
                out.write(b, 0, n);
            }
            return new String(out.toByteArray(), "UTF-8");
        }
    }

    /** 页面 2s 轮询；无状态文件时返回 {"state":"NONE"}。 */
    @android.webkit.JavascriptInterface
    public String readState() {
        try {
            String raw = readStateJson();
            return raw == null ? new JSONObject().put("state", "NONE").toString() : raw;
        } catch (Exception e) {
            try { return new JSONObject().put("state", "ERROR").put("error", String.valueOf(e)).toString(); }
            catch (Exception ignore) { return "{\"state\":\"ERROR\"}"; }
        }
    }

    /** PREPARED+nonce 复核后才发 CONFIRM；服务侧 authorize 再验一次（fresh-read 120s）。 */
    @android.webkit.JavascriptInterface
    public String confirm(String nonce) {
        try {
            JSONObject plan = new JSONObject(readStateJson());
            if (!"PREPARED".equals(plan.optString("state")) || plan.isNull("nonce"))
                return new JSONObject().put("ok", false)
                        .put("error", "没有有效的升级计划（需要先准备，且未过期）").toString();
            String stored = plan.getString("nonce");
            if (nonce == null || !stored.equals(nonce))
                return new JSONObject().put("ok", false).put("error", "确认令牌不匹配").toString();
            Intent intent = new Intent(activity, UpgradeService.class)
                    .setAction(UpgradeService.CONFIRM).putExtra("nonce", stored);
            activity.startService(intent);
            return new JSONObject().put("ok", true).toString();
        } catch (Exception e) {
            try { return new JSONObject().put("ok", false).put("error", String.valueOf(e)).toString(); }
            catch (Exception ignore) { return "{\"ok\":false}"; }
        }
    }

    @android.webkit.JavascriptInterface
    public void cancel() {
        main.post(() -> {
            try {
                activity.startService(new Intent(activity, UpgradeService.class).setAction(UpgradeService.CANCEL));
                emit("ota_cancel", new JSONObject().put("sent", true));
            } catch (Exception e) { emitError("cancel", e); }
        });
    }
}
