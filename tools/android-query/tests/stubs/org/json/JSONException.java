package org.json;

/** JVM 单测桩：org.json.JSONException（Android 内置真类是受检异常，extends Exception）。 */
public class JSONException extends Exception {
    public JSONException(String s) { super(s); }
}
