"""Bounded incremental OTA for this already-installed U1 LAB only.

Compares a recorded expected installed application against the current compiled
target. At most5 application ranges plus a self-copied boot-descriptor page.
No Bluetooth/device access. Expected bytes are a transfer receipt, not a dump.
"""
from pathlib import Path
import hashlib,json,struct

ROOT=Path(__file__).resolve().parent
APP_END=0x1EF80
PAGE_END=0x1F000
STATE=ROOT/'installed-history/installed-state.json'
PACKAGE=ROOT/'OTA_U1_HW3_V32_LAB_DELTA.bin'
PLAN=ROOT/'u1-lab-delta-plan.json'

def digest(data):return hashlib.sha256(data).hexdigest()

def initialize_installed_state():
    """Initialize once from the exact first transfer and observed custom boot."""
    if STATE.exists():return json.loads(STATE.read_text())
    receipt=json.loads((ROOT.parent/'captures/u1-lab-install-summary-01.json').read_text())
    archive=ROOT/'installed-history/OTA_U1_HW3_V32_LAB_b3aa79d7.bin'
    package=archive.read_bytes()
    if digest(package)!='b3aa79d73d790120bd6e2235c8ad3a86d6df44cb74213e9f097b595848be5311':raise ValueError('Initial installed archive mismatch')
    if not receipt['custom_lab_boot_verified'] or not receipt['commit_acknowledged']:raise ValueError('No initial installation/boot receipt')
    application=package[36:36+PAGE_END]
    image=ROOT/'installed-history/expected-active-first-lab.bin'
    with image.open('xb') as f:f.write(application)
    state={'expected_application_file':str(image.relative_to(ROOT)),'expected_sha256':digest(application),
           'expected_prefix_sha256':digest(application[:APP_END]),'source_receipt':'captures/u1-lab-install-summary-01.json',
           'current_address':receipt['current_address'],'custom_lab_boot_verified':True,
           'scope':'Expected bytes derived from acknowledged OTA and custom boot, not a physical flash dump'}
    STATE.write_text(json.dumps(state,indent=2)+'\n');return state

def changed_ranges(before,after):
    if len(before)!=PAGE_END or len(after)<PAGE_END:raise ValueError('Unexpected application size')
    ranges=[]
    for start in range(0,APP_END,128):
        if before[start:start+128]!=after[start:start+128]:
            if ranges and ranges[-1][1]==start:ranges[-1]=(ranges[-1][0],start+128)
            else:ranges.append((start,start+128))
    if not ranges:raise ValueError('No application change to install')
    # Descriptor records are16bytes, header16bytes, final marker16bytes.
    # Merge the closest gaps when necessary so6 records fit in128bytes.
    while len(ranges)>5:
        i=min(range(len(ranges)-1),key=lambda i:ranges[i+1][0]-ranges[i][1])
        ranges[i:i+2]=[(ranges[i][0],ranges[i+1][1])]
    return ranges+[(APP_END,PAGE_END)]

def make_descriptor(ranges):
    if not 2<=len(ranges)<=6:raise ValueError('Expected1..5 app ranges plus descriptor')
    descriptor=bytearray(b'\x5a\xa5'*64)
    values=(len(ranges),0x10001,0xFFFFFFFF)
    struct.pack_into('<4I',descriptor,0,*values,sum(values)&0xFFFFFFFF)
    for i,(start,end) in enumerate(ranges):
        if start&127 or end&127 or not 0<=start<end<=PAGE_END:raise ValueError('Unsafe delta range')
        words=(start,(end-start)//128,start+0x20000)
        struct.pack_into('<4I',descriptor,16+16*i,*words,sum(words)&0xFFFFFFFF)
    return bytes(descriptor)

def create_delta(before,after):
    ranges=changed_ranges(before,after);application=bytearray(after[:PAGE_END])
    application[APP_END:PAGE_END]=make_descriptor(ranges)
    header=bytes(12)+b'\xff'*4+len(ranges).to_bytes(4,'big')
    for start,end in ranges:header+=b'\x00\x01'+start.to_bytes(3,'big')+(end-1).to_bytes(3,'big')
    header+=b'HEAD_END'
    return header+application,ranges

def parse_delta(data):
    if data[:16]!=bytes(12)+b'\xff'*4:raise ValueError('Unexpected delta header')
    count=int.from_bytes(data[16:20],'big');size=28+8*count
    if not 2<=count<=6 or len(data)!=size+PAGE_END or data[size-8:size]!=b'HEAD_END':raise ValueError('Invalid delta file size/count')
    ranges=[]
    for i in range(count):
        field=data[20+8*i:28+8*i]
        if field[:2]!=b'\x00\x01':raise ValueError('Unexpected delta region marker')
        ranges.append((int.from_bytes(field[2:5],'big'),int.from_bytes(field[5:8],'big')+1))
    if ranges[-1]!=(APP_END,PAGE_END) or any(end>APP_END for _,end in ranges[:-1]):raise ValueError('Delta exceeds application boundaries')
    if any(ranges[i][1]>ranges[i+1][0] for i in range(len(ranges)-1)):raise ValueError('Overlapping/unsorted delta regions')
    application=data[size:]
    if application[APP_END:]!=make_descriptor(ranges):raise ValueError('Boot descriptor/range mismatch')
    return application,ranges

def build(restore_stock_poll=False):
    state=initialize_installed_state();before=(ROOT/state['expected_application_file']).read_bytes()
    if digest(before)!=state['expected_sha256']:raise ValueError('Recorded installed bytes mismatch')
    manifest=json.loads((ROOT/'u1-v32-lab-manifest.json').read_text())
    if restore_stock_poll:
        if state['expected_sha256']!='b654941bdf23c293a856afc5e1696d1fd2053359f2258d56e96f73a887d16928':
            raise ValueError('Stock-poll recovery is limited to the individually verified first LAB')
        after=bytearray(before)
        after[0x151FF:0x15205]=bytes.fromhex('20 21 03 00 2e ab')
        after[0x41AC:0x41BC]=bytes.fromhex('f0 b5 ff 48 85 b0 01 78 00 25 fe 4f fe 4c 03 29')
        target_file='u1-stock-poll-recovery-target.bin';(ROOT/target_file).write_bytes(after)
        result_dispatch='stock'
    else:
        target_file='u1-v32-lab-offline.bin';after=(ROOT/target_file).read_bytes()
        if digest(after)!=manifest['raw_image_sha256']:raise ValueError('Compiled target mismatch')
        result_dispatch='lab'
    package,ranges=create_delta(before,after);application,checked=parse_delta(package)
    PACKAGE.write_bytes(package)
    plan={'package':PACKAGE.name,'package_sha256':digest(package),'base_expected_sha256':state['expected_sha256'],
          'target_application_file':target_file,'result_poll_dispatch':result_dispatch,
          'base_prefix_sha256':state['expected_prefix_sha256'],'target_prefix_sha256':digest(application[:APP_END]),
          'expected_result_sha256':digest(application),'current_address':state['current_address'],
          'result_address':manifest['preserved_device_address'],'data_packets':sum((end-start)//16 for start,end in ranges),
          'regions':[{'start':start,'end_exclusive':end,'pages':(end-start)//128} for start,end in ranges],
          'scope':'Incremental experimental LAB update; fixed original application and boot-driver model tested. No persistent1f000..1ffff or bootloader3f000..3ffff transfers.',
          'installed':False}
    if result_dispatch=='lab':
        plan['result_lab_revision']=manifest['lab_protocol_revision']
        plan['result_lab_build_id']=manifest['lab_build_id']
    PLAN.write_text(json.dumps(plan,indent=2)+'\n');return plan

if __name__=='__main__':
    import argparse
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--restore-stock-poll',action='store_true')
    args=parser.parse_args();plan=build(args.restore_stock_poll);print(json.dumps(plan,indent=2))
