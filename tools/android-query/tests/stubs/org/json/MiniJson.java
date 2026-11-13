package org.json;

import java.util.ArrayList;

/**
 * JVM 单测桩的共享解析/序列化内核。仅实现 H3SBridge 与其单测用到的 JSON 子集
 * （对象/数组/字符串/数字/true/false/null），语法错误抛 JSONException，行为对齐
 * org.json 关键语义。不用于生产（Android 用系统内置 org.json）。
 */
final class MiniJson {
    private final String s;
    private int i;

    private MiniJson(String s) { this.s = s; }

    static Object parse(String json) throws JSONException {
        if (json == null) throw new JSONException("null 文本");
        MiniJson p = new MiniJson(json);
        p.ws();
        Object v = p.value();
        p.ws();
        if (p.i != p.s.length()) throw p.err("末尾有多余内容");
        return v;
    }

    private JSONException err(String why) { return new JSONException("位置 " + i + ": " + why); }

    private void ws() { while (i < s.length() && Character.isWhitespace(s.charAt(i))) i++; }

    private char peek() throws JSONException {
        if (i >= s.length()) throw err("意外结束");
        return s.charAt(i);
    }

    private Object value() throws JSONException {
        char c = peek();
        if (c == '{') return object();
        if (c == '[') return array();
        if (c == '"') return string();
        if (c == 't') { lit("true"); return Boolean.TRUE; }
        if (c == 'f') { lit("false"); return Boolean.FALSE; }
        if (c == 'n') { lit("null"); return null; }
        return number();
    }

    private void lit(String word) throws JSONException {
        if (!s.startsWith(word, i)) throw err("非法字面量");
        i += word.length();
    }

    private JSONObject object() throws JSONException {
        JSONObject o = new JSONObject();
        i++; ws();
        if (peek() == '}') { i++; return o; }
        while (true) {
            if (peek() != '"') throw err("键必须是字符串");
            String key = string();
            ws();
            if (peek() != ':') throw err("缺冒号");
            i++; ws();
            o.put(key, value());
            ws();
            char c = peek();
            if (c == ',') { i++; ws(); continue; }
            if (c == '}') { i++; return o; }
            throw err("对象缺 , 或 }");
        }
    }

    private JSONArray array() throws JSONException {
        JSONArray a = new JSONArray();
        i++; ws();
        if (peek() == ']') { i++; return a; }
        while (true) {
            a.put(value());
            ws();
            char c = peek();
            if (c == ',') { i++; ws(); continue; }
            if (c == ']') { i++; return a; }
            throw err("数组缺 , 或 ]");
        }
    }

    private String string() throws JSONException {
        i++; // 开引号
        StringBuilder out = new StringBuilder();
        while (true) {
            if (i >= s.length()) throw err("字符串未闭合");
            char c = s.charAt(i++);
            if (c == '"') return out.toString();
            if (c == '\\') {
                if (i >= s.length()) throw err("转义未闭合");
                char e = s.charAt(i++);
                switch (e) {
                    case '"': out.append('"'); break;
                    case '\\': out.append('\\'); break;
                    case '/': out.append('/'); break;
                    case 'b': out.append('\b'); break;
                    case 'f': out.append('\f'); break;
                    case 'n': out.append('\n'); break;
                    case 'r': out.append('\r'); break;
                    case 't': out.append('\t'); break;
                    case 'u':
                        if (i + 4 > s.length()) throw err("\\u 转义不完整");
                        try { out.append((char) Integer.parseInt(s.substring(i, i + 4), 16)); }
                        catch (NumberFormatException ex) { throw err("\\u 转义非法"); }
                        i += 4;
                        break;
                    default: throw err("非法转义 \\" + e);
                }
            } else {
                out.append(c);
            }
        }
    }

    private Double number() throws JSONException {
        int start = i;
        if (peek() == '-') i++;
        while (i < s.length()) {
            char c = s.charAt(i);
            if ((c >= '0' && c <= '9') || c == '.' || c == 'e' || c == 'E' || c == '+' || c == '-') i++;
            else break;
        }
        String text = s.substring(start, i);
        if (text.isEmpty() || text.equals("-")) throw err("非法数字");
        try { return Double.valueOf(Double.parseDouble(text)); }
        catch (NumberFormatException ex) { throw err("非法数字: " + text); }
    }

    static String quote(String value) {
        StringBuilder out = new StringBuilder("\"");
        for (int k = 0; k < value.length(); k++) {
            char c = value.charAt(k);
            switch (c) {
                case '"': out.append("\\\""); break;
                case '\\': out.append("\\\\"); break;
                case '\b': out.append("\\b"); break;
                case '\f': out.append("\\f"); break;
                case '\n': out.append("\\n"); break;
                case '\r': out.append("\\r"); break;
                case '\t': out.append("\\t"); break;
                default:
                    if (c < 0x20) out.append(String.format("\\u%04x", (int) c));
                    else out.append(c);
            }
        }
        return out.append('"').toString();
    }

    static String numberToString(Double d) throws JSONException {
        if (d.isNaN() || d.isInfinite()) throw new JSONException("非有限数字");
        if (d == Math.floor(d) && !d.isInfinite() && d >= -9.007199254740992E15 && d <= 9.007199254740992E15) {
            return String.valueOf(d.longValue());
        }
        return d.toString();
    }

    static String join(ArrayList<Object> list) throws JSONException {
        StringBuilder out = new StringBuilder();
        for (int k = 0; k < list.size(); k++) {
            if (k > 0) out.append(',');
            out.append(stringify(list.get(k)));
        }
        return out.toString();
    }

    static String stringify(Object v) throws JSONException {
        if (v == null) return "null";
        if (v instanceof String) return quote((String) v);
        if (v instanceof Boolean) return v.toString();
        if (v instanceof Double) return numberToString((Double) v);
        if (v instanceof JSONObject) return v.toString();
        if (v instanceof JSONArray) return v.toString();
        throw new JSONException("不支持的值类型: " + v.getClass());
    }
}
