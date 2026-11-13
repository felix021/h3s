package org.json;

import java.util.LinkedHashMap;
import java.util.Map;

/**
 * JVM 单测桩：org.json.JSONObject 的最小子集（Android 内置真类）。
 * 只实现 H3SBridge/H3sTransport 与其单测用到的方法；语义对齐 org.json（缺失 optDouble 返回
 * 默认值、getInt 非数值抛 JSONException、语法错误抛 JSONException）。
 * 仅供 tools/android-query/test.sh 的纯 JVM 编译。
 */
public class JSONObject {
    private final LinkedHashMap<String, Object> map = new LinkedHashMap<String, Object>();

    public JSONObject() {}

    public JSONObject(String json) throws JSONException {
        Object v = MiniJson.parse(json);
        if (!(v instanceof JSONObject)) throw new JSONException("文本不是 JSON 对象");
        map.putAll(((JSONObject) v).map);
    }

    public JSONObject put(String key, int value) throws JSONException { map.put(key, Double.valueOf(value)); return this; }
    public JSONObject put(String key, boolean value) throws JSONException { map.put(key, Boolean.valueOf(value)); return this; }
    public JSONObject put(String key, String value) throws JSONException { map.put(key, value); return this; }
    public JSONObject put(String key, Object value) throws JSONException { map.put(key, value); return this; }

    public boolean has(String key) { return map.containsKey(key); }

    public boolean isNull(String key) { return map.get(key) == null; }

    public double optDouble(String key, double fallback) {
        Object v = map.get(key);
        if (v instanceof Number) return ((Number) v).doubleValue();
        if (v instanceof String) {
            try { return Double.parseDouble((String) v); } catch (NumberFormatException e) { return fallback; }
        }
        return fallback;
    }

    public int optInt(String key, int fallback) { return (int) Math.round(optDouble(key, fallback)); }

    /** 对齐 Android org.json：取 int，不存在/非数值抛 JSONException。 */
    public int getInt(String key) throws JSONException {
        Object v = map.get(key);
        if (v instanceof Number) {
            double d = ((Number) v).doubleValue();
            if (!Double.isNaN(d) && !Double.isInfinite(d)) return (int) Math.round(d);
        }
        throw new JSONException("字段 " + key + " 不是数值");
    }

    public String getString(String key) throws JSONException {
        Object v = map.get(key);
        if (v instanceof String) return (String) v;
        throw new JSONException("字段 " + key + " 不是字符串");
    }

    public boolean getBoolean(String key) throws JSONException {
        Object v = map.get(key);
        if (v instanceof Boolean) return (Boolean) v;
        throw new JSONException("字段 " + key + " 不是布尔");
    }

    public String optString(String key, String fallback) {
        Object v = map.get(key);
        return v instanceof String ? (String) v : fallback;
    }

    @Override public String toString() {
        StringBuilder out = new StringBuilder("{");
        boolean first = true;
        try {
            for (Map.Entry<String, Object> e : map.entrySet()) {
                if (!first) out.append(',');
                first = false;
                out.append(MiniJson.quote(e.getKey())).append(':').append(MiniJson.stringify(e.getValue()));
            }
        } catch (JSONException impossible) { // Double 均有限，numberToString 实际不抛
            throw new IllegalStateException(impossible);
        }
        return out.append('}').toString();
    }
}
