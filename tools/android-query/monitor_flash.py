#!/usr/bin/env python3
"""Read-only monitor for an already authorized flash; never starts or retries OTA."""
import argparse
import json
from pathlib import Path
import re
import subprocess
import time

p=argparse.ArgumentParser(description=__doc__)
p.add_argument('--serial',required=True)
p.add_argument('--output',type=Path,required=True)
a=p.parse_args()
a.output.mkdir(parents=True,exist_ok=True)
base=['adb','-s',a.serial,'exec-out','run-as','cn.felix021.h3squery']
last=None
last_raw=0
while True:
    try:
        raw=subprocess.check_output(base+['cat','files/upgrade-state.json'],timeout=10)
        state=json.loads(raw)
        (a.output/'latest-state.json').write_bytes(raw)
        with (a.output/'state-history.jsonl').open('a') as out:
            out.write(json.dumps(state,ensure_ascii=False)+'\n')
        current=(state['state'],state['message'])
        if current!=last:
            print(json.dumps({'state':state['state'],'message':state['message'],'time_ms':state['time_ms']},ensure_ascii=False),flush=True)
            last=current
        terminal=state['state'] in ('FAILED','SUCCEEDED')
        if terminal or time.monotonic()-last_raw>=30:
            name=state['log']
            if not re.fullmatch(r'upgrade-[0-9]+\.jsonl',name):
                raise ValueError('invalid log filename')
            data=subprocess.check_output(base+['cat','files/'+name],timeout=15)
            (a.output/name).write_bytes(data)
            last_raw=time.monotonic()
        if terminal:
            print('Terminal state captured; no retry or additional device commands issued.',flush=True)
            break
    except Exception as e:
        print('monitor_read_error: '+str(e),flush=True)
    time.sleep(5)
