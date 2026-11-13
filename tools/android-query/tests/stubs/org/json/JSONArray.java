package org.json;

import java.util.ArrayList;

/**
 * JVM 单测桩：org.json.JSONArray 的最小子集（Android 内置真类）。
 * 只实现 H3SBridge 与其单测用到的方法（解析、长度、按下标取对象、put、序列化）。
 */
public class JSONArray {
    private final ArrayList<Object> list = new ArrayList<Object>();

    public JSONArray() {}

    public JSONArray(String json) throws JSONException {
        Object v = MiniJson.parse(json);
        if (!(v instanceof JSONArray)) throw new JSONException("文本不是 JSON 数组");
        list.addAll(((JSONArray) v).list);
    }

    public int length() { return list.size(); }

    public JSONObject getJSONObject(int index) throws JSONException {
        Object v = list.get(index);
        if (v instanceof JSONObject) return (JSONObject) v;
        throw new JSONException("第 " + index + " 项不是 JSON 对象");
    }

    public JSONArray put(Object value) { list.add(value); return this; }

    @Override public String toString() {
        try { return "[" + MiniJson.join(list) + "]"; }
        catch (JSONException impossible) { throw new IllegalStateException(impossible); }
    }
}
