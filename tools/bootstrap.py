#!/usr/bin/env python3
"""Fetch pinned Android dependencies / optional reference SDK material only."""
import argparse
from pathlib import Path
from artifacts import retrieve

ROOT = Path(__file__).resolve().parents[1]
COMMIT = '67328f4741c69993c88e504c67377b196c724f97'
SDK_BASE = 'https://gitlab.zh-jieli.com/ai_test/701n/-/raw/' + COMMIT + '/'
ANDROID = [
    ('jl_bt_ota.aar', 'https://raw.githubusercontent.com/Jieli-Tech/Android-JL_Bluetooth/6293873b641ffd8d461773873ecedef1a9d9038d/libs/jl_bt_ota_V1.10.0_10932-release.aar', '7b0671e4f98b39537ed1a0701fcd0aecd16c17eebc43468fa58f17af0e84ee15'),
    ('androidx-core-1.7.0.aar', 'https://dl.google.com/dl/android/maven2/androidx/core/core/1.7.0/core-1.7.0.aar', 'aaf6734226fff923784f92f65d78a2984dbf17534138855c5ce2038f18656e0b'),
]
REFERENCES = [
    ('volume_node.c', 'SDK/audio/framework/nodes/volume_node.c', None),
    ('volume_node.h', 'SDK/audio/framework/include/volume_node.h', None),
    ('effects_adj.c', 'SDK/audio/effect/effects_adj.c', '300d6483aca3e50af3b7c34b7555b6e06c196951cc2bcfec26f289d68d3b34fe'),
    ('audio_anc.c', 'SDK/audio/cpu/br28/audio_anc.c', 'e1552b0ab41a658e400046329bb339dd8093115af50cbebf25d6ecfdd53d2b87'),
    ('audio_anc.h', 'SDK/audio/cpu/br28/audio_anc.h', '169ddc88d748d3b2ec3535346e058ee0aaed47771868b6c20d7bb83910502633'),
    ('adv_anc_voice_key.c', 'SDK/apps/common/third_party_profile/jieli/rcsp/server/functions/rcsp_setting_opt/settings/adv_anc_voice_key.c', 'abbc070313d63ef198ecf9d51b9e8e99bc87505d6ffef53b507f83f9c63d5f1d'),
]
BINARY_REFERENCES = [
    ('media.a', 'SDK/cpu/br28/liba/media.a', '6d1c5ed5e03594f3f508933ab06ec6a125f4c3cba75aad7be31da318120af233'),
    ('update.a', 'SDK/cpu/br28/liba/update.a', '8bda8f5441fef55b5b00c4a8dbe7ab473b8f91acd967e0d24ee375acdf9e3fdf'),
]

p = argparse.ArgumentParser(description=__doc__)
p.add_argument('--android', action='store_true')
p.add_argument('--reference-sdk', action='store_true')
p.add_argument('--reference-binaries', action='store_true')
a = p.parse_args()
if not any(vars(a).values()):
    p.error('Select --android, --reference-sdk or --reference-binaries')
if a.android:
    for name, url, sha in ANDROID:
        retrieve(url, ROOT / 'tools/android-query/vendor' / name, sha)
        print('Verified', name)
if a.reference_sdk or a.reference_binaries:
    import json
    pinned = json.loads((ROOT / 'data/dependencies.json').read_text())['reference_source_sha256']
    items = (REFERENCES if a.reference_sdk else []) + (BINARY_REFERENCES if a.reference_binaries else [])
    for name, path, sha in items:
        retrieve(SDK_BASE + path, ROOT / 'tools/reference-cache' / name, sha or pinned[name])
        print('Verified', name)
