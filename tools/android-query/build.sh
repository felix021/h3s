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
# BR28 RAM loader（tools/usb-recovery/h3s_usb.py 同源钉死；MIT 上游 kagaimiq/jl-uboot-tool）。
# 下载一次按摘要缓存到 assets/loader/（git 忽略），随 APK 打包；缺网构建允许缺失——
# 届时 USB tab 的 loader 装载/窗读/备份返回明确错误，其余功能不受影响。
loader_file=assets/loader/br28loader.bin
loader_sha=ab1b77eb73385aaa78ab769253b2af7ef63d0ade46f391bd7424cc598a40267c
mkdir -p assets/loader
if ! echo "$loader_sha  $loader_file" | sha256sum -c - >/dev/null 2>&1; then
  curl -fsSL --retry 3 -o "$loader_file" \
    'https://raw.githubusercontent.com/kagaimiq/jl-uboot-tool/adb3f18889e88ac512ce0a3c4d8cc3d3cb30696a/data/loaderblobs/usb/br28loader.bin' \
    || echo 'WARN: loader 下载失败（继续构建，USB 读操作将在运行时报 loader 未打包）' >&2
  [ -f "$loader_file" ] && echo "$loader_sha  $loader_file" | sha256sum -c -
fi
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
"$bt/aapt" package -f -M AndroidManifest.xml -S res -A assets -I "$android_jar" -F build/unsigned.apk
(cd build/dex && zip -q ../unsigned.apk classes.dex)
(cd build && zip -q unsigned.apk lib/arm64-v8a/libjl_ota_auth.so)
if [ ! -f build/debug.keystore ]; then
  keytool -genkeypair -keystore build/debug.keystore -storepass android -keypass android -alias androiddebugkey -dname CN=H3SQuery -keyalg RSA -validity 10000 >/dev/null 2>&1
fi
"$bt/zipalign" -f 4 build/unsigned.apk build/aligned.apk
# 签名（仿 podic 配置仓模式）：H3S_CONFIG_DIR（默认 ~/.config/podic 共用同一套 key）
# 下的 env.sh 提供 H3S_RELEASE_KEYSTORE/STORE_PASS/ALIAS；缺失时回退本地 debug.keystore。
# 密钥/密码只存配置仓，绝不进 git（podic 同款约定）。
config_dir="${H3S_CONFIG_DIR:-$HOME/.config/podic}"
if [ -f "$config_dir/env.sh" ]; then
  # shellcheck disable=SC1090
  PODIC_CONFIG_DIR="$config_dir"; export PODIC_CONFIG_DIR
  set -a; source "$config_dir/env.sh"; set +a
fi
if [ -n "${H3S_RELEASE_KEYSTORE:-}" ] || [ -n "${PODIC_RELEASE_KEYSTORE:-}" ]; then
  ks="${H3S_RELEASE_KEYSTORE:-${PODIC_RELEASE_KEYSTORE:-}}"
  ks="${ks/#\$PODIC_CONFIG_DIR/$config_dir}"
  if [ ! -f "$ks" ]; then
    echo "release keystore 不存在：$ks" >&2; exit 1
  fi
  pass="${H3S_RELEASE_STORE_PASS:-${PODIC_RELEASE_STORE_PASS:-}}"
  alias="${H3S_RELEASE_ALIAS:-${PODIC_RELEASE_ALIAS:-podic-release}}"
  echo "signing: release keystore ($ks)"
  "$bt/apksigner" sign --ks "$ks" --ks-pass "pass:$pass" --ks-key-alias "$alias" --key-pass "pass:$pass" --out build/h3s-query.apk build/aligned.apk
else
  echo 'signing: local debug.keystore（未配置 release key，覆盖安装旧签名版需先卸载）' >&2
  "$bt/apksigner" sign --ks build/debug.keystore --ks-pass pass:android --out build/h3s-query.apk build/aligned.apk
fi
"$bt/apksigner" verify build/h3s-query.apk
sha256sum build/h3s-query.apk
