package cn.felix021.h3squery;

import android.content.Context;
import android.media.AudioAttributes;
import android.media.AudioDeviceInfo;
import android.media.AudioFormat;
import android.media.AudioManager;
import android.media.AudioTrack;
import android.webkit.JavascriptInterface;
import java.io.ByteArrayOutputStream;
import java.io.IOException;
import java.io.InputStream;
import java.util.ArrayList;
import java.util.Collections;
import java.util.List;
import org.json.JSONArray;
import org.json.JSONException;
import org.json.JSONObject;

/**
 * 助听 tab 的测试音/样本播放桥（注入名 "h3sha"）。
 *
 * 目的：在助听界面播放「单频正弦测试音」与「语音/音乐/自然」样本，且尽量走手机内置扬声器
 * （TYPE_BUILTIN_SPEAKER），绕开 BLE A2DP 的默认蓝牙路由——否则耳机拾音放大后用户再通过
 * 同一副耳机听测试音，会形成回环/无法判断辅助效果。技术点：
 *
 *   - AudioTrack MODE_STATIC + Builder.setPreferredDevice(builtinSpeaker)：setPreferredDevice
 *     是「首选设备」提示（android.jar 无 MODIFY_AUDIO_ROUTING 权限要求），真机 BT 连着时是否
 *     完全强制路由到扬声器需真机验证（AVD 无真实 BT，只能验 bridge 不崩）。
 *   - 单频音：生成整周期倍数长度的正弦缓冲 → setLoopPoints 无缝循环，直到 stop()。
 *   - 样本：assets/samples/{speech,music,nature}/<名>.wav（二级目录），PCM 16-bit 单声道
 *     （语音=edge-tts 构建期生成；音乐/自然=Wikimedia Commons 公共领域录音，见
 *     assets/samples/manifest.json），读 fmt/data 块按实际采样率/声道喂给 AudioTrack；
 *     loop 用 MODE_STATIC 循环点。样本清单从 assets 目录动态列举（加样本不必改本类）。
 *
 * 返回统一 JSON：成功 {"ok":true,...}，失败 {"ok":false,"error":"..."}。
 * 本类只依赖 android.media + android.webkit，纯 JVM 不可实例化（不进 test.sh），Android 构建
 * 走 build.sh 的 android.jar 编译。
 */
public class H3sToneBridge {
    /** 样本二级目录（assets/samples/ 下）。 */
    private static final String[] SAMPLE_DIRS = {"speech", "music", "nature"};

    private static final int TONE_SAMPLE_RATE = 16000;

    private final Context context;
    private final AudioManager audioManager;
    private AudioTrack track;
    private String[] sampleIds;

    public H3sToneBridge(Context context) {
        this.context = context.getApplicationContext();
        this.audioManager = (AudioManager) this.context.getSystemService(Context.AUDIO_SERVICE);
    }

    /** 播单频正弦（freqHz 20..20000，volume 0..100，钳位），直到 stop()。 */
    @JavascriptInterface
    public String playTone(int freqHz, int volume) {
        stopAndRelease();
        if (freqHz < 20 || freqHz > 20000) return err("频率需在 20–20000 Hz 内: " + freqHz);
        if (volume < 0) volume = 0;
        if (volume > 100) volume = 100;
        short[] pcm = sine(freqHz, (int) Math.round(32767.0 * volume / 100.0));
        AudioDeviceInfo speaker = builtinSpeaker();
        boolean routed = start(pcm, TONE_SAMPLE_RATE, true, speaker);
        if (!routed) stopAndRelease(); // 没建成/没路由就归位，避免残留半成品 track
        return result("tone", freqHz, volume, routed);
    }

    /** 播内置样本（id 形如 "speech/en_f_daily"，即 samples/ 下 "<目录>/<名>"），loop=true 循环。 */
    @JavascriptInterface
    public String playSample(String name, boolean loop) {
        stopAndRelease();
        if (name == null) return err("样本名不能为空");
        boolean known = false;
        for (String s : listSamples()) if (s.equals(name)) { known = true; break; }
        if (!known) return err("未知样本: " + name);
        Sample sample;
        try {
            sample = loadSample(name);
        } catch (IOException e) {
            return err("读取样本失败: " + e.getMessage());
        }
        AudioDeviceInfo speaker = builtinSpeaker();
        boolean routed = start(sample.pcm, sample.sampleRate, loop, speaker);
        return result("sample", name, loop ? 1 : 0, routed);
    }

    /** 停止当前播放并释放 AudioTrack。 */
    @JavascriptInterface
    public String stop() {
        stopAndRelease();
        try { return new JSONObject().put("ok", true).put("playing", false).toString(); }
        catch (JSONException impossible) { return "{\"ok\":true,\"playing\":false}"; }
    }

    /** 页面轮询用：当前播放状态 + 内置扬声器是否可用 + 样本清单。 */
    @JavascriptInterface
    public String info() {
        AudioDeviceInfo speaker = builtinSpeaker();
        try {
            JSONObject o = new JSONObject();
            o.put("ok", true);
            o.put("playing", track != null && track.getPlayState() == AudioTrack.PLAYSTATE_PLAYING);
            o.put("speaker", speaker != null);
            JSONArray arr = new JSONArray();
            for (String s : listSamples()) arr.put(s);
            o.put("samples", arr);
            return o.toString();
        } catch (JSONException impossible) { return "{\"ok\":true}"; }
    }

    /** 列举 assets/samples/{speech,music,nature}/ 下的样本 id（"<目录>/<名>"），首次列举后缓存。 */
    private String[] listSamples() {
        if (sampleIds == null) {
            List<String> ids = new ArrayList<>();
            for (String dir : SAMPLE_DIRS) {
                String[] files;
                try {
                    files = context.getAssets().list("samples/" + dir);
                } catch (IOException e) {
                    files = null;
                }
                if (files == null) continue;
                for (String f : files) {
                    if (f.endsWith(".wav")) ids.add(dir + "/" + f.substring(0, f.length() - 4));
                }
            }
            Collections.sort(ids);
            sampleIds = ids.toArray(new String[0]);
        }
        return sampleIds;
    }

    /** Activity onDestroy 收尾：停播 + 释放。 */
    public void release() {
        stopAndRelease();
    }

    private AudioDeviceInfo builtinSpeaker() {
        AudioDeviceInfo[] outs = audioManager.getDevices(AudioManager.GET_DEVICES_OUTPUTS);
        for (AudioDeviceInfo d : outs) {
            if (d.getType() == AudioDeviceInfo.TYPE_BUILTIN_SPEAKER) return d;
        }
        return null;
    }

    /** 建 MODE_STATIC AudioTrack、写 PCM、可选循环并播放；返回是否成功把首选设备设为扬声器。 */
    private boolean start(short[] pcm, int sampleRate, boolean loop, AudioDeviceInfo speaker) {
        if (pcm == null || pcm.length == 0) return false;
        int bytes = pcm.length * 2;
        AudioTrack t;
        try {
            t = new AudioTrack.Builder()
                .setAudioAttributes(new AudioAttributes.Builder()
                    .setUsage(AudioAttributes.USAGE_MEDIA)
                    .setContentType(AudioAttributes.CONTENT_TYPE_MUSIC)
                    .build())
                .setAudioFormat(new AudioFormat.Builder()
                    .setSampleRate(sampleRate)
                    .setEncoding(AudioFormat.ENCODING_PCM_16BIT)
                    .setChannelMask(AudioFormat.CHANNEL_OUT_MONO)
                    .build())
                .setBufferSizeInBytes(bytes)
                .setTransferMode(AudioTrack.MODE_STATIC)
                .build();
        } catch (IllegalArgumentException e) {
            return false; // 采样率/格式不被设备支持时兜底，不崩
        }
        boolean routed = false;
        if (speaker != null) {
            try { routed = t.setPreferredDevice(speaker); } catch (Exception e) { routed = false; }
        }
        if (t.write(pcm, 0, pcm.length) < 0) {
            t.release();
            return false;
        }
        if (loop) t.setLoopPoints(0, pcm.length, -1);
        t.play();
        track = t;
        return routed;
    }

    private void stopAndRelease() {
        if (track != null) {
            try { track.stop(); } catch (IllegalStateException ignore) { }
            track.release();
            track = null;
        }
    }

    /** 生成整周期倍数长度的正弦（默认 ~0.2s，循环点无缝），振幅按 16-bit 峰值。 */
    private short[] sine(int freqHz, int amplitude) {
        int g = gcd(freqHz, TONE_SAMPLE_RATE);
        int period = TONE_SAMPLE_RATE / g;
        int target = TONE_SAMPLE_RATE / 5;
        int repeats = target / period + 1;
        int len = period * repeats;
        short[] out = new short[len];
        double step = 2.0 * Math.PI * freqHz / TONE_SAMPLE_RATE;
        double phase = 0.0;
        for (int i = 0; i < len; i++) {
            out[i] = (short) Math.round(amplitude * Math.sin(phase));
            phase += step;
            if (phase >= 2.0 * Math.PI) phase -= 2.0 * Math.PI;
        }
        return out;
    }

    private static int gcd(int a, int b) {
        while (b != 0) { int t = a % b; a = b; b = t; }
        return a;
    }

    /** 读取 assets/samples/<name>.wav 并解析 fmt/data 块为 PCM 16-bit 单声道。 */
    private Sample loadSample(String name) throws IOException {
        InputStream in = null;
        byte[] b;
        try {
            in = context.getAssets().open("samples/" + name + ".wav");
            ByteArrayOutputStream bos = new ByteArrayOutputStream();
            byte[] buf = new byte[8192];
            int n;
            while ((n = in.read(buf)) > 0) bos.write(buf, 0, n);
            b = bos.toByteArray();
        } finally {
            if (in != null) { try { in.close(); } catch (IOException ignore) { } }
        }
        if (b.length < 44 || b[0] != 'R' || b[1] != 'I' || b[2] != 'F' || b[3] != 'F'
                || b[8] != 'W' || b[9] != 'A' || b[10] != 'V' || b[11] != 'E') {
            throw new IOException("不是有效 WAV 文件");
        }
        int sampleRate = 0, channels = 0, bits = 0;
        int dataOff = -1, dataLen = 0;
        int pos = 12;
        while (pos + 8 <= b.length) {
            String id = new String(b, pos, 4, "US-ASCII");
            int size = le32(b, pos + 4);
            int start = pos + 8;
            if ("fmt ".equals(id)) {
                if (start + 16 > b.length) throw new IOException("fmt 块损坏");
                int audioFormat = le16(b, start);
                channels = le16(b, start + 2);
                sampleRate = le32(b, start + 4);
                bits = le16(b, start + 14);
                if (audioFormat != 1) throw new IOException("仅支持 PCM WAV");
            } else if ("data".equals(id)) {
                dataOff = start;
                dataLen = size;
                break;
            }
            pos = start + size + (size & 1);
        }
        if (dataOff < 0 || dataLen <= 0) throw new IOException("未找到 data 块");
        if (bits != 16) throw new IOException("仅支持 16-bit PCM");
        if (channels < 1) throw new IOException("声道数非法: " + channels);
        int frames = Math.min(dataLen / (channels * 2), (b.length - dataOff) / (channels * 2));
        short[] pcm = new short[frames];
        for (int i = 0; i < frames; i++) {
            if (channels == 1) {
                pcm[i] = (short) le16(b, dataOff + i * 2);
            } else {
                int sum = 0;
                for (int c = 0; c < channels; c++) sum += le16(b, dataOff + (i * channels + c) * 2);
                pcm[i] = (short) (sum / channels);
            }
        }
        return new Sample(pcm, sampleRate);
    }

    private static final class Sample {
        final short[] pcm;
        final int sampleRate;
        Sample(short[] pcm, int sampleRate) { this.pcm = pcm; this.sampleRate = sampleRate; }
    }

    private String result(String kind, Object key, int aux, boolean routed) {
        try {
            JSONObject o = new JSONObject();
            o.put("ok", true);
            o.put("playing", true);
            o.put("speaker", routed);
            o.put("kind", kind);
            if (key instanceof Integer) o.put("freq", (Integer) key);
            else o.put("sample", String.valueOf(key));
            if (kind.equals("sample")) o.put("loop", aux == 1);
            else o.put("volume", aux);
            return o.toString();
        } catch (JSONException impossible) { return "{\"ok\":true}"; }
    }

    private static String err(String message) {
        try { return new JSONObject().put("ok", false).put("error", message).toString(); }
        catch (JSONException impossible) { return "{\"ok\":false}"; }
    }

    private static int le16(byte[] b, int off) {
        return (b[off] & 0xff) | ((b[off + 1] & 0xff) << 8);
    }

    private static int le32(byte[] b, int off) {
        return (b[off] & 0xff) | ((b[off + 1] & 0xff) << 8)
            | ((b[off + 2] & 0xff) << 16) | ((b[off + 3] & 0xff) << 24);
    }
}
