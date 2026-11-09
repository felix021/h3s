#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")"
test_config=build/test-generated/cn/felix021/h3squery/DeviceConfig.java
python3 generate_device_config.py --test --output "$test_config"
mkdir -p build/tests
javac -d build/tests "$test_config" src/cn/felix021/h3squery/QueryPolicy.java src/cn/felix021/h3squery/UpgradePolicy.java src/cn/felix021/h3squery/UpgradeGate.java src/cn/felix021/h3squery/BlePacketizer.java tests/QueryPolicyTest.java tests/UpgradeGateTest.java tests/BlePacketizerTest.java
java -cp build/tests QueryPolicyTest
java -cp build/tests UpgradeGateTest
java -cp build/tests BlePacketizerTest
