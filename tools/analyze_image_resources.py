#!/usr/bin/env python3
"""镜像资源级清单与多镜像 diff：外层 UFW 条目 + flash.bin 双备份区 JLFS 资源（含目录内嵌套子项）。

逐项校验 CRC（复用 verify_ufw_integrity 的独立审计与解析语义：表头 <HHIIBBH16s>，
内容 = 条目起点+32，目录条目 flags 0x83/0x93 的内容区为 32B 子表头序列，子项 0x10 位
表示无内联数据、address/size 是 Flash 分区坐标）。任何 CRC 失败、越界、未知 flags、
重名都直接报错，不静默跳过；报告不含任何文件路径。
修复候选（67ba97ef）/历史 r2（c8dae523）参与时，按仓库钉死事实校验其相对已测试
b79 的资源级差异形状，出现预期外差异立即报错（显著标记）。
"""
import argparse
import hashlib
import json
from pathlib import Path
import struct

from probe_boot import APP_SHA, OFFICIAL_SHA
from probe_pi32v2 import ROOT, require
from verify_eq_candidate import load_image
from verify_ufw_integrity import crc, decode, sfc, verify

TESTED_SHA='b79cb3a6f8702eb9e1d12eaf181a968f6b5848a66c051b2c792c39aa8b42fa1b'
REPAIRED_SHA='67ba97efb83a15ec9df14de574667c6a77126d43ac6d8e5f5e645cb91126f222'
FAILED_R2_SHA='c8dae5233678102aac152f0ff94952edea83c6eeca6afaf216db43226d0b6696'
REPAIRED_PAYLOAD_SHA='f4e9501e515a7f74e7114633ba9bc165d1da9b399481530a78be8663dbe0e49a'
ENCRYPTED_KINDS=(50,52,55,56,161,251)
PLAIN_KINDS=(0,2,100,255)
HEADER='<HHIIBBH16s'
DIR_FLAGS=(0x83,0x93)
FILE_FLAG=0x82
MAX_DIFF_RUNS=64

# 官方原版镜像钉死清单（资源名 -> (kind/flags, size/block, 内容 SHA256)），无参数自检用。
# app_area_head 内嵌 app.bin 数据另与 probe_boot.APP_SHA 互证；p11_code.bin 与 analyze_p11 钉死摘要一致。
PINNED_OFFICIAL={
 'outer':{
  'flash.bin':(0,0xe6000,'13d15b3ea419010f3d59491a0a9807746bb172be3214d8fe4a602d21ef452305'),
  'info.log':(2,0x0,'e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855'),
  'uboot.version':(55,0x20,'c9f405e7dc6b9c45d0fadd3f59c672c4f74a41bf42f34c2d9c1644b8f55a88ef'),
  'TON':(50,0xb7b0,'6a511c89acfa4433fbab7d46f22bbbb62c8524cdd93cd72c9b5101f104ae52b5'),
  'TON1':(50,0x10340,'f765091dadf26e5042cbfd94ad8570f71be4714e5fdfa98ed0bcf3f0b4c1fc66'),
  'ANCIF':(50,0x100,'885121fbe0dbdaa5bedc5713ba1987c8fd553b6596eb01df20c2523737d69a1d'),
  'ANCIF1':(50,0xac8,'47bb7e25b035c79cf90998a45b299c2cb8c4f8eadb86558f7fa1bc8c9e7b46b8'),
  'isd_config.ini':(52,0x5f6,'0f8baddb56cb6e08d2c2169bb9ac75636c1b001b7619630bda7350baf8fd9e1e'),
  'v_ota.bin':(56,0x40,'adf4e3a709b7a9afdb51254e73a695241a6b9a2977b3bccb3da41ec37dc2b5de'),
  'ota.bin':(100,0x40ab0,'8dfb12feb3dcfe050f096d0353111103fd86ca3559ab658964e292f11aba06a2'),
  'farg.cfg':(251,0x5,'f2b83317a349520477a4e8c12bf48a3ddd21e3347c9baee436cece67d0a78816'),
  'blimit.bin':(161,0x90,'fc15eddfbd6b66fc092bcf20da3ac800fb7b266db666df95bc1d770dd3f71815'),
  'tail.bin':(255,0x40,'1494cf5795c2e11841dbe5900517d24c2437209b9935b0926c0b611d3cf069b7')},
 'jlfs':{
  'app_area_head':(0x83,0xcdca0,'7a7d89e05fe4f25852c5540358651a9b539c4d59461be695bd6f743ed306da12'),
  'EXT_RESERVED':(0x93,0x60,'0a27811abdee49e17b67d91e2a8745800b564d945f8bc0d3f6bcf7b40234da1b'),
  'ALIGN_DIR':(0x83,0xf310,'dce91a449c2a605485e94fe2e01b3dc9bc124126471c8891b90c0b1ad5b347c2'),
  '3mic_coeff.bin':(0x82,0x424,'f186c7cd9b3db9833431e3aa3827ad30db833552a5ca6a87ab3577f480a0ee78'),
  'cfg_tool.bin':(0x82,0x488,'b31c195f28af94e520791798f13db44e036de8548fa9c4deefd94da620c679d0'),
  'p11_code.bin':(0x82,0x2ff8,'1fe36008118168d2178c0cb3aeec265fc78e47ce70cd652e633236beb8270882'),
  'scene.bin':(0x82,0x178,'357dcfbb838d712653aaf192a3b613bf9a061af6ca92dc55a4b31939a6ec7b13'),
  'stream.bin':(0x82,0x24a7,'19c9deebc2ad26d8bda47f979bfff759ec1203c50e5b6980c4f3d528581e72d2'),
  'config.dat':(0x82,0x180,'8a3fb6c96affad66b4bf93973d8af5c442f1e6a69caad5bdf26f192e35b35fa9')}}


def sha256(data):
    return hashlib.sha256(data).hexdigest()


def resource_name(raw):
    name=raw.split(b'\0')[0]
    require(bool(name) and all(32<=b<127 for b in name) and b'/' not in name and b'\\' not in name,
            '资源名非法')
    return name.decode('ascii')


def walk_outer(raw):
    """外层 UFW 目录：kind 已知集合 + 逐条 CRC；附带解码内容供 diff 使用（不入报告）。"""
    header=decode(raw[:64]);hc,dc,size,count,_,_,_=struct.unpack('<HHIHHI48s',header)
    require(size==len(raw) and crc(header[2:])==hc,'UFW 头长度/CRC 校验失败')
    require(crc(raw[64:64+count*80])==dc,'UFW 目录 CRC 校验失败')
    entries=[]
    for i in range(count):
        kind,_,check,_,offset,length,padded,_,raw_name=struct.unpack('<HHHHIII44s16s',
            decode(raw[64+i*80:144+i*80]))
        name=resource_name(raw_name)
        require(kind in ENCRYPTED_KINDS+PLAIN_KINDS,f'未知 UFW 条目类型 {kind}: {name}')
        require(length<=padded and offset+padded<=len(raw),f'UFW 条目越界: {name}')
        block=raw[offset:offset+padded]
        content=(sfc(block,offset) if kind in ENCRYPTED_KINDS else block)[:length]
        require(crc(content)==check,f'UFW 条目 CRC 校验失败: {name}')
        entries.append({'name':name,'kind':kind,'offset':offset,'size':length,'padded_size':padded,
                        'sfc_decrypted':kind in ENCRYPTED_KINDS,'crc_ok':True,'sha256':sha256(content),
                        'content':content})
    require(len({e['name'] for e in entries})==count,'外层资源名重复')
    return entries


def walk_nested(plain,position,block,parent):
    """目录条目内容区：连续 32B 子表头直到 index=1；0x10 位 = 无内联数据（分区描述）。"""
    entries=[];q=position+32
    while q+32<=position+block:
        hcrc,dcrc,address,size,flags,_,index,raw=struct.unpack_from(HEADER,plain,q)
        name=resource_name(raw)
        require(crc(plain[q+2:q+32])==hcrc,f'嵌套表头 CRC 校验失败: {parent}/{name}')
        child={'name':name,'subheader_offset':q-position,'flags':flags,'index':bool(index)}
        if flags&0x10:
            child.update({'inline_data':False,'flash_address':address,'flash_size':size})
        else:
            require(flags==FILE_FLAG,f'未知嵌套条目 flags {flags:#x}: {parent}/{name}')
            require(address>=q+32-position and position+address+size<=position+block,
                    f'嵌套数据越界: {parent}/{name}')
            data=plain[position+address:position+address+size]
            require(crc(data)==dcrc,f'嵌套数据 CRC 校验失败: {parent}/{name}')
            child.update({'inline_data':True,'data_offset':address,'size':size,
                          'sha256':sha256(data),'data_crc_ok':True})
        entries.append(child)
        if index:break
        q+=32
    require(entries,f'目录条目无嵌套子项: {parent}')
    require(len({e['name'] for e in entries})==len(entries),f'嵌套资源名重复: {parent}')
    return entries


def walk_jlfs(plain):
    """双备份应用区顶层 JLFS：全覆盖 + 逐条表头/数据 CRC。"""
    entries=[];p=0
    while p+32<=len(plain):
        hcrc,dcrc,address,block,flags,_,index,raw=struct.unpack_from(HEADER,plain,p)
        name=resource_name(raw)
        require(block>=32 and p+block<=len(plain),f'JLFS 条目越界: {name}')
        require(crc(plain[p+2:p+32])==hcrc,f'JLFS 表头 CRC 校验失败: {name}')
        require(crc(plain[p+32:p+block])==dcrc,f'JLFS 数据 CRC 校验失败: {name}')
        entry={'name':name,'header_offset':p,'address':address,'block':block,'flags':flags,
               'index':bool(index),'content_offset':p+32,'content_bytes':block-32,
               'header_crc_ok':True,'data_crc_ok':True,'sha256':sha256(plain[p+32:p+block]),
               'block_sha256':sha256(plain[p:p+block])}
        if flags in DIR_FLAGS:
            entry['children']=walk_nested(plain,p,block,name)
        else:
            require(flags==FILE_FLAG,f'未知 JLFS 条目 flags {flags:#x}: {name}')
        entries.append(entry)
        p+=block
        if index:break
    require(p==len(plain),'JLFS 未完整覆盖双备份应用区')
    require(len({e['name'] for e in entries})==len(entries),'顶层资源名重复')
    return entries


def inventory(path,label):
    """单镜像完整清单：独立 CRC 审计 -> 外层 -> JLFS（含嵌套）。"""
    report=verify(path)
    require(report['passed'],'独立 CRC 审计失败: '+str(report['errors']))
    raw,flash,plain=load_image(path)
    _,plain_crc,cipher_crc,_,length=struct.unpack_from('<HHHHI',decode(flash[0x2000:0x2020]),0)
    require(crc(plain)==plain_crc and crc(flash[0x2020:0x2020+length])==cipher_crc,
            'boot-info 双 CRC 校验失败')
    outer=walk_outer(raw)
    for entry in outer:entry.pop('content')
    jlfs=walk_jlfs(plain)
    return {'label':label,'image_sha256':sha256(raw),'image_bytes':len(raw),
            'app_area_bytes':len(plain),'boot_crc_ok':True,'outer':outer,
            'outer_count':len(outer),'jlfs':jlfs,'jlfs_count':len(jlfs),
            'nested_count':sum(len(e.get('children',())) for e in jlfs),'all_crc_ok':True},raw,plain


def diff_runs(a,b):
    """等长资源段的差异游程摘要（段内相对偏移，只报位置不报内容）。"""
    require(len(a)==len(b),'diff 资源段长度不一致')
    runs=[];i=0
    while i<len(a):
        if a[i]!=b[i]:
            j=i+1
            while j<len(a) and a[j]!=b[j]:j+=1
            runs.append([i,j]);i=j
        else:i+=1
    return {'diff_bytes':sum(j-i for i,j in runs),
            'runs':[[hex(i),hex(j)] for i,j in runs[:MAX_DIFF_RUNS]],
            'runs_total':len(runs),'runs_truncated':len(runs)>MAX_DIFF_RUNS}


def identity(e,key='sha256'):
    """资源一致性判据：内联数据用 SHA256；分区描述用 (flags, flash_address, flash_size)。

    顶层 JLFS 用整块 sha256（表头+内容），使 index/表头 CRC 等纯元数据变化也算差异。
    """
    return e.get(key) or (e['flags'],e.get('flash_address'),e.get('flash_size'))


def diff_named(items_a,items_b,label_a,label_b,*,key='sha256'):
    """按名字对齐一层资源：same / different / only_in_*。"""
    map_a={e['name']:e for e in items_a};map_b={e['name']:e for e in items_b}
    rows=[]
    for name in sorted(set(map_a)|set(map_b)):
        if name not in map_b:rows.append({'name':name,'status':'only_in_'+label_a});continue
        if name not in map_a:rows.append({'name':name,'status':'only_in_'+label_b});continue
        status='same' if identity(map_a[name],key)==identity(map_b[name],key) else 'different'
        rows.append({'name':name,'status':status})
    return rows,map_a,map_b


def diff_images(first,second):
    """两镜像全部资源层 diff：外层 / 顶层 JLFS（整块对齐）/ 嵌套子项（数据段对齐）。"""
    a,raw_a,plain_a=first;b,raw_b,plain_b=second
    label_a,label_b=a['label'],b['label']
    outer_a,outer_b=walk_outer(raw_a),walk_outer(raw_b)
    outer_rows,outer_map_a,outer_map_b=diff_named(outer_a,outer_b,label_a,label_b)
    for row in outer_rows:
        if row['status']!='different':continue
        ea,eb=outer_map_a[row['name']],outer_map_b[row['name']]
        if ea['size']==eb['size']:
            row['content_diff']=diff_runs(ea['content'],eb['content'])
        else:
            row['size']=[ea['size'],eb['size']]
    jlfs_a={e['name']:e for e in a['jlfs']};jlfs_b={e['name']:e for e in b['jlfs']}
    jlfs_rows,_,_=diff_named(a['jlfs'],b['jlfs'],label_a,label_b,key='block_sha256')
    for row in jlfs_rows:
        if row['status']!='different':continue
        ea,eb=jlfs_a[row['name']],jlfs_b[row['name']]
        if ea['block']!=eb['block']:
            row['block_size']=[ea['block'],eb['block']];continue
        row['block_diff']=diff_runs(
            plain_a[ea['header_offset']:ea['header_offset']+ea['block']],
            plain_b[eb['header_offset']:eb['header_offset']+eb['block']])
    nested_rows=[]
    for name in sorted(set(jlfs_a)&set(jlfs_b)):
        pa,pb=jlfs_a[name],jlfs_b[name]
        if 'children' not in pa or 'children' not in pb:continue
        require(pa['block']==pb['block'],'嵌套 diff 要求同名目录块同尺寸: '+name)
        rows,child_a,child_b=diff_named(pa['children'],pb['children'],label_a,label_b)
        for row in rows:
            if row['status']=='different' and child_a[row['name']].get('inline_data'):
                ca,cb=child_a[row['name']],child_b[row['name']]
                if (ca['data_offset'],ca['size'])==(cb['data_offset'],cb['size']):
                    base=pa['header_offset']
                    row['data_diff']=diff_runs(
                        plain_a[base+ca['data_offset']:base+ca['data_offset']+ca['size']],
                        plain_b[base+ca['data_offset']:base+ca['data_offset']+ca['size']])
            if row['status']!='same':row['parent']=name
        nested_rows+=[r for r in rows if r['status']!='same']
    return {'baseline':label_a,'image':label_b,
            'image_sha256':[a['image_sha256'],b['image_sha256']],
            'outer':outer_rows,'jlfs':jlfs_rows,'jlfs_nested':nested_rows}


def check_pinned_official(result):
    """无参数自检：官方镜像资源清单必须与钉死摘要逐项一致。"""
    official=result['images'][0]
    require(official['image_sha256']==OFFICIAL_SHA,'官方镜像摘要不符')
    for level in ('outer','jlfs'):
        actual={e['name']:e for e in official[level]}
        require(set(actual)==set(PINNED_OFFICIAL[level]),f'{level} 资源集合与钉死清单不一致')
        for name,pinned in PINNED_OFFICIAL[level].items():
            e=actual[name]
            require((e.get('kind',e.get('flags')),e.get('block',e.get('size')),e['sha256'])==pinned,
                    f'{level}/{name} 与钉死摘要不一致')
    app=next(c for e in official['jlfs'] if e['name']=='app_area_head' for c in e['children']
             if c['name']=='app.bin')
    require(app['sha256']==APP_SHA,'app.bin 摘要与 probe_boot 钉死值不符')
    result['pinned_official_inventory_match']=True


def expanded_runs(row):
    require(not row['runs_truncated'],'差异游程被截断，形状校验不完整')
    return {i for start,end in ((int(a,16),int(b,16)) for a,b in row['runs']) for i in range(start,end)}


def check_candidate_shape(diff,candidate_entries,plain,expect_fence):
    """修复候选/历史 r2 相对已测试 b79 的资源级差异形状，按仓库钉死事实校验。

    钉死来源：eq_toggle_image.py（追加 eq_toggle.bin = 32B 表头 + b'\\xff' + 337B 载荷，
    flags 0x82 / index=1；五个 6B 钩子原位替换；修复候选仅另改 0x34e9a..0x34e9d 的
    PC 上限立即数）与 verify_eq_candidate.py 的 allowed 偏移集合。
    任何预期外差异（新偏移、缺失资源、载荷摘要不符）都直接报错，不降级为警告。
    """
    rows={r['name']:r for r in diff['jlfs']}
    added={n for n,r in rows.items() if r['status']=='only_in_'+diff['image']}
    removed={n for n,r in rows.items() if r['status']=='only_in_'+diff['baseline']}
    require(added=={'eq_toggle.bin'} and not removed,
            f'顶层资源集合变化不符合钉死事实: +{sorted(added)} -{sorted(removed)}')
    different={n for n,r in rows.items() if r['status']=='different'}
    require(different=={'app_area_head','config.dat'},
            f'差异资源集合不符合钉死事实: {sorted(different)}')
    appended=next(e for e in candidate_entries if e['name']=='eq_toggle.bin')
    content=plain[appended['header_offset']+32:appended['header_offset']+appended['block']]
    require(appended['flags']==FILE_FLAG and appended['block']==0x172 and content[0]==0xff and
            sha256(content[1:])==REPAIRED_PAYLOAD_SHA,'追加 eq_toggle.bin 载荷形状不符')
    allowed=set(range(4))|set(range(0x20,0x24))|set(range(0xe,0xf))
    for site in (0x59ce,0x25030,0x2510e,0x25364,0x78846):allowed.update(range(site,site+6))
    if expect_fence:allowed.update(range(0x34e9a,0x34e9e))
    require('block_diff' in rows['app_area_head'],'app_area_head 块尺寸变化不符合钉死事实')
    changed=expanded_runs(rows['app_area_head']['block_diff'])
    require(changed<=allowed,'app_area_head 出现预期外差异偏移: '+
            str(sorted(hex(i) for i in changed-allowed)))
    require(set(range(4))<=changed and set(range(0x20,0x24))<=changed,'CRC 元数据字节未如期更新')
    require('block_diff' in rows['config.dat'],'config.dat 块尺寸变化不符合钉死事实')
    config_changed=expanded_runs(rows['config.dat']['block_diff'])
    require(config_changed<=allowed and 0xe in config_changed,'config.dat index/表头 CRC 变化不符')
    return {'baseline':'tested','appended_entry':'eq_toggle.bin','payload_bytes':337,
            'payload_sha256':REPAIRED_PAYLOAD_SHA,
            'fence_immediate_change_expected':expect_fence,
            'unexpected_diff_found':False}


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--image',action='append',default=[],metavar='PATH[=LABEL]',
                   help='参与对比的 UFW，形如 path=label；可多次。缺省仅分析官方原版并校验钉死清单')
    p.add_argument('--expected-missing',action='append',default=[],metavar='LABEL=SHA256',
                   help='声明本轮应参与但文件不可用的镜像，如实记入报告（不臆造其内容）')
    p.add_argument('--report',type=Path,required=True)
    args=p.parse_args()
    specs=args.image or ['%s=official'%(ROOT/'firmware/QCY_H3S_7018F_1T2_release_note_V1.1.8_011.ufw')]
    report={'device_accessed':False,'whole_h3s_boot_simulated':False,'images':[],'diffs':[]}
    loaded=[]
    for spec in specs:
        path_text,sep,label=spec.rpartition('=')
        path=Path(path_text if sep else spec);label=label if sep else path.name
        result,raw,plain=inventory(path,label)
        report['images'].append(result);loaded.append((result,raw,plain))
    if len(report['images'])==1 and report['images'][0]['image_sha256']==OFFICIAL_SHA:
        check_pinned_official(report)
    for label,digest in (m.split('=',1) for m in args.expected_missing):
        report.setdefault('expected_but_unavailable',[]).append(
            {'label':label,'sha256':digest,'status':'file_not_available_for_analysis',
             'note':'本机文件系统、Release assets 与私有归档均未找到该摘要的文件；未做任何臆测'})
    for index in range(1,len(loaded)):
        diff=diff_images(loaded[0],loaded[index])
        result,raw,plain=loaded[index]
        if result['image_sha256'] in (REPAIRED_SHA,FAILED_R2_SHA):
            require(loaded[0][0]['image_sha256']==TESTED_SHA,'候选形状校验要求基线为已测试 b79 镜像')
            diff['candidate_shape_check']=check_candidate_shape(
                diff,result['jlfs'],plain,result['image_sha256']==REPAIRED_SHA)
        report['diffs'].append(diff)
    args.report.parent.mkdir(parents=True,exist_ok=True)
    args.report.write_text(json.dumps(report,indent=2)+'\n')
    summary=', '.join('%s: %d JLFS/%d 嵌套/%d 外层'%(i['label'],i['jlfs_count'],i['nested_count'],
                                                     i['outer_count']) for i in report['images'])
    print('资源清单完成：%s。全部 CRC 通过；未访问设备。'%summary)


if __name__=='__main__':
    main()
