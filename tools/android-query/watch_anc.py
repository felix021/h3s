#!/usr/bin/env python3
"""Read current QCY ANC mode while a person operates the headphone's mode key.

Uses only the fixed anc-info query (FF / FE 01 17). Never sets a mode,
changes a key mapping, starts OTA or reconnects Bluetooth automatically.
Connect the query Activity first and wait until its device query completes.
"""
import argparse
import json
from pathlib import Path
import re
import signal
import subprocess
import time

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument('--serial', required=True)
parser.add_argument('--seconds', type=int, default=45, choices=range(5, 121))
parser.add_argument('--output', required=True, type=Path)
args = parser.parse_args()
adb = ['adb', '-s', args.serial]
pkg = 'cn.felix021.h3squery'
private = adb + ['exec-out', 'run-as', pkg]
state = json.loads(subprocess.check_output(private + ['cat', 'files/upgrade-state.json'], timeout=10))
if state['state'] not in ('SUCCEEDED', 'FAILED') or state['armed']:
    raise SystemExit('Refusing to query during an upgrade/preparation')
names = subprocess.check_output(private + ['ls', '-1', 'files'], text=True, timeout=10).splitlines()
log = max(n for n in names if re.fullmatch(r'query-[0-9]+\.jsonl', n))
start = int(time.time() * 1000)
deadline = time.monotonic() + args.seconds
seen = set()
observations = []
last = None
labels = {1: 'ANC_ON', 2: 'NORMAL_OFF', 3: 'TRANSPARENT'}
stopping = False
def stop_observer(signum, frame):
    global stopping
    stopping = True
signal.signal(signal.SIGINT, stop_observer)
signal.signal(signal.SIGTERM, stop_observer)
print('Read-only ANC observation started; press the physical mode key when ready.', flush=True)
while not stopping and time.monotonic() < deadline:
    subprocess.run(adb + ['shell', 'am', 'start', '-n', pkg + '/.MainActivity',
                          '-a', pkg + '.PROBE', '--es', 'operation', 'anc-info'],
                   check=True, capture_output=True, timeout=10)
    time.sleep(1)
    data = subprocess.check_output(private + ['cat', 'files/' + log], text=True, timeout=10)
    for line in data.splitlines():
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue  # An asynchronously appended final line may be incomplete.
        if row['time_ms'] < start or row['event'] != 'anc_response':
            continue
        key = (row['time_ms'], str(row['data']))
        if key in seen:
            continue
        seen.add(key)
        match = re.fullmatch(r'status=0 raw=(17[0-9a-f]+)', row['data'])
        if not match:
            continue
        raw = bytes.fromhex(match[1])
        if len(raw) != 5 or raw[1] != 3:
            continue
        item = {'time_ms': row['time_ms'], 'raw': raw.hex(), 'qcy_mode': raw[2],
                'mode': labels.get(raw[2], 'UNKNOWN'), 'scene': raw[3], 'strength': raw[4]}
        observations.append(item)
        if item['mode'] != last:
            print(json.dumps(item), flush=True)
            last = item['mode']
    time.sleep(1)
sequence = []
for item in observations:
    if not sequence or item['mode'] != sequence[-1]:
        sequence.append(item['mode'])
report = {'query_log': log, 'started_ms': start, 'observations': observations,
          'stopped_early': stopping,
          'mode_sequence': sequence, 'changes_observed': max(0, len(sequence) - 1),
          'only_anc_transparent_observed': bool(sequence) and all(x in ('ANC_ON', 'TRANSPARENT') for x in sequence),
          'initial_normal_then_only_anc_transparent': len(sequence) > 1 and sequence[0] == 'NORMAL_OFF' and all(x in ('ANC_ON', 'TRANSPARENT') for x in sequence[1:]),
          'limitation': 'Observed modes do not independently identify which user action caused a change'}
args.output.parent.mkdir(parents=True, exist_ok=True)
args.output.write_text(json.dumps(report, indent=2) + '\n')
print(json.dumps({k: v for k, v in report.items() if k != 'observations'}), flush=True)
