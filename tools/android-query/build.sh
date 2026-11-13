#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")"
mkdir -p build
rm -f build/h3s-query.apk
sdk_root="${ANDROID_SDK_ROOT:-/opt/android-sdk}"
bt="$sdk_root/build-tools/35.0.0"
android_jar="$sdk_root/platforms/android-35/android.jar"
test -f "$android_jar" || { echo 'Android SDK platform 35 is required' >&2; exit 1; }
bash test.sh
device_config=build/generated/cn/felix021/h3squery/DeviceConfig.java
python3 generate_device_config.py --output "$device_config"
rm -rf build/classes build/dex
mkdir -p build/classes build/dex build/lib/arm64-v8a
echo '7b0671e4f98b39537ed1a0701fcd0aecd16c17eebc43468fa58f17af0e84ee15  vendor/jl_bt_ota.aar' | sha256sum -c -
unzip -p vendor/jl_bt_ota.aar classes.jar > build/sdk.jar
echo 'aaf6734226fff923784f92f65d78a2984dbf17534138855c5ce2038f18656e0b  vendor/androidx-core-1.7.0.aar' | sha256sum -c -
unzip -p vendor/androidx-core-1.7.0.aar classes.jar > build/androidx-core.jar
unzip -p vendor/jl_bt_ota.aar jni/arm64-v8a/libjl_ota_auth.so > build/lib/arm64-v8a/libjl_ota_auth.so
javac -encoding UTF-8 -source 8 -target 8 -cp "$android_jar:build/sdk.jar" -d build/classes "$device_config" src/cn/felix021/h3squery/*.java
jar cf build/app.jar -C build/classes .
"$bt/d8" --min-api 26 --lib "$android_jar" --output build/dex build/app.jar build/sdk.jar build/androidx-core.jar
"$bt/aapt" package -f -M AndroidManifest.xml -A assets -I "$android_jar" -F build/unsigned.apk
(cd build/dex && zip -q ../unsigned.apk classes.dex)
(cd build && zip -q unsigned.apk lib/arm64-v8a/libjl_ota_auth.so)
if [ ! -f build/debug.keystore ]; then
  keytool -genkeypair -keystore build/debug.keystore -storepass android -keypass android -alias androiddebugkey -dname CN=H3SQuery -keyalg RSA -validity 10000 >/dev/null 2>&1
fi
"$bt/zipalign" -f 4 build/unsigned.apk build/aligned.apk
"$bt/apksigner" sign --ks build/debug.keystore --ks-pass pass:android --out build/h3s-query.apk build/aligned.apk
"$bt/apksigner" verify build/h3s-query.apk
sha256sum build/h3s-query.apk
