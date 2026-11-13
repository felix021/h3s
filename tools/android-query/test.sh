#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")"
test_config=build/test-generated/cn/felix021/h3squery/DeviceConfig.java
python3 generate_device_config.py --test --output "$test_config"
mkdir -p build/tests
# tests/stubs = android.webkit.JavascriptInterface 注解 + org.json 最小子集，
# 让 H3SBridge 源码不经修改即可在纯 JVM 下编译（Android 构建用 android.jar + 内置 org.json）。
stubs="tests/stubs/android/webkit/JavascriptInterface.java tests/stubs/org/json/JSONException.java tests/stubs/org/json/MiniJson.java tests/stubs/org/json/JSONObject.java tests/stubs/org/json/JSONArray.java"
javac -d build/tests "$test_config" $stubs src/cn/felix021/h3squery/QueryPolicy.java src/cn/felix021/h3squery/UpgradePolicy.java src/cn/felix021/h3squery/UpgradeGate.java src/cn/felix021/h3squery/BlePacketizer.java src/cn/felix021/h3squery/EqFrame.java src/cn/felix021/h3squery/EqWritePolicy.java src/cn/felix021/h3squery/H3SBridge.java tests/QueryPolicyTest.java tests/UpgradeGateTest.java tests/BlePacketizerTest.java tests/EqFrameTest.java tests/H3SBridgeTest.java
java -cp build/tests QueryPolicyTest
java -cp build/tests UpgradeGateTest
java -cp build/tests BlePacketizerTest
java -cp build/tests EqFrameTest
java -cp build/tests H3SBridgeTest
