#!/usr/bin/env python3
"""Independently compare every captured OTA block with the selected uFW bytes."""
import argparse
from collections import Counter
import hashlib
import json
from pathlib import Path
from summarize_log import frames

p=argparse.ArgumentParser(description=__doc__)
p.add_argument('log',type=Path)
p.add_argument('firmware',type=Path)
p.add_argument('--original',type=Path)
p.add_argument('--expected-sha256',default='b79cb3a6f8702eb9e1d12eaf181a968f6b5848a66c051b2c792c39aa8b42fa1b')
a=p.parse_args()
image=a.firmware.read_bytes()
assert hashlib.sha256(image).hexdigest()==a.expected_sha256
requests={}
rx=bytearray()
blocks=[]
counts=Counter()
issues=[]
states=[]
results=[]
eof=0

def received(frame):
    body=frame['body']
    if frame['command'] and frame['opcode']==229:
        if len(body)!=7:
            issues.append('invalid block-request size')
            return
        sn=body[0]
        offset=int.from_bytes(body[1:5],'big')
        size=int.from_bytes(body[5:7],'big')
        requests[sn]=(offset,size)
    elif not frame['command'] and frame['opcode'] in (226,227,230,231):
        results.append({'opcode':f"0x{frame['opcode']:02x}",'body':body.hex()})

for line in a.log.read_text().splitlines():
    row=json.loads(line)
    if row['event']=='upgrade_state': states.append(row['data']['state'])
    if row['event']=='blocked_tx': issues.append('transport blocked a frame: '+row['data'][:24])
    if row['event']=='rx':
        rx.extend(bytes.fromhex(row['data']))
        while len(rx)>=7:
            if rx[:3]!=b'\xfe\xdc\xba': raise ValueError('invalid RX frame header')
            end=int.from_bytes(rx[5:7],'big')+8
            if len(rx)<end: break
            packet=bytes(rx[:end]); del rx[:end]
            for frame in frames(packet): received(frame)
    if row['event']=='tx_frame':
        for frame in frames(bytes.fromhex(row['data'])):
            counts[f"0x{frame['opcode']:02x}"]+=1
            if frame['opcode']!=229 or frame['command']: continue
            body=frame['body']
            if len(body)<2 or body[0]!=0: issues.append('unsuccessful E5 block response'); continue
            sn=body[1]
            if sn not in requests: issues.append('E5 response without matching request'); continue
            offset,size=requests.pop(sn)
            if size==0:
                eof+=1
                if body[2:]: issues.append('nonempty EOF response')
                continue
            expected=image[offset:offset+size]
            actual=body[2:]
            if len(expected)!=size or actual!=expected: issues.append(f'block mismatch offset={offset} size={size}')
            blocks.append((offset,offset+size))

merged=[]
for start,end in sorted(blocks):
    if merged and start<=merged[-1][1]: merged[-1][1]=max(merged[-1][1],end)
    else: merged.append([start,end])
report={
    'log':str(a.log),'firmware_sha256':hashlib.sha256(image).hexdigest(),
    'block_responses_verified':len(blocks),'block_response_payload_bytes':sum(e-s for s,e in blocks),
    'unique_file_bytes_requested':sum(e-s for s,e in merged),'requested_ranges':merged,
    'eof_responses':eof,'tx_opcode_counts':dict(counts),'states':list(dict.fromkeys(states)),
    'device_result_responses':results,'pending_block_requests':len(requests),
    'incomplete_rx_bytes':len(rx),'issues':issues,
    'limitations':['Matching sent bytes and SDK success do not independently prove flash contents or audible effect'],
}
if a.original:
    original=a.original.read_bytes()
    assert len(original)==len(image)
    assert hashlib.sha256(original).hexdigest()=='65cafc7f9a7545c5c30701d141c77db334e37065589e42c51306132c94c879af'
    changed=[i for i,(x,y) in enumerate(zip(original,image)) if x!=y]
    uncovered=[i for i in changed if not any(s<=i<e for s,e in merged)]
    report['changed_ufw_bytes']=len(changed)
    report['changed_ufw_bytes_not_requested']=uncovered
print(json.dumps(report,ensure_ascii=False,indent=2))
if issues: raise SystemExit(1)
