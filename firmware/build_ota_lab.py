"""Build a bounded HW3/V32 lab OTA file and an offline transfer plan.

No BLE imports or device writes. The header uses an inclusive aligned-region
end (as supported by the vendor app) so the boot descriptor is transferred too.
The downloaded V32 header ends at1ef80 and omits that descriptor in the reviewed
Android parser; it is deliberately not copied blindly into the lab package.
"""
from pathlib import Path
import hashlib,json,struct

ROOT=Path(__file__).resolve().parent
PACKAGE=ROOT/'OTA_U1_HW3_V32_SoundSend_LAB.bin'
APPLICATION_END=0x1F000
DESCRIPTOR=0x1EF80


def digest(data):return hashlib.sha256(data).hexdigest()

def data_request(address,payload):
    if len(payload)!=16 or address&15 or not 0<=address<APPLICATION_END:
        raise ValueError('Expected one aligned16-byte application chunk')
    return address.to_bytes(3,'little')+b'\x10'+payload

def parse_package(data):
    if len(data)!=36+APPLICATION_END or data[28:36]!=b'HEAD_END':
        raise ValueError('Unexpected lab package length/header')
    if data[:16]!=bytes(12)+b'\xff'*4 or data[16:22]!=bytes.fromhex('00 00 00 01 00 01'):
        raise ValueError('Unexpected version/region header')
    if int.from_bytes(data[22:25],'big')!=0 or int.from_bytes(data[25:28],'big')!=APPLICATION_END-1:
        raise ValueError('Transfer must cover exactly the application and descriptor')
    application=data[36:]
    header=struct.unpack_from('<4I',application,DESCRIPTOR)
    region=struct.unpack_from('<4I',application,DESCRIPTOR+16)
    if header!=(1,0x10001,0xFFFFFFFF,0x10001) or region!=(0,APPLICATION_END//128,0x20000,0x20000+APPLICATION_END//128):
        raise ValueError('Unrecognized downloaded V32 boot descriptor')
    if application[-2:]!=b'\x5a\xa5':raise ValueError('Missing final data-handler marker')
    return application

def build():
    raw=(ROOT/'u1-v32-lab-offline.bin').read_bytes()
    manifest=json.loads((ROOT/'u1-v32-lab-manifest.json').read_text())
    if digest(raw)!=manifest['raw_image_sha256']:raise ValueError('Raw image does not match its manifest')
    header=bytes(12)+b'\xff'*4+(1).to_bytes(4,'big')+b'\x00\x01'+bytes(3)+(APPLICATION_END-1).to_bytes(3,'big')+b'HEAD_END'
    package=header+raw[:APPLICATION_END];application=parse_package(package)
    frames=[data_request(a,application[a:a+16]) for a in range(0,APPLICATION_END,16)]
    PACKAGE.write_bytes(package)
    plan={
        'package':PACKAGE.name,'package_sha256':digest(package),'bytes':len(package),
        'base':'Vendor V32, HW3 / batch20190404; no original FW33 backup is available',
        'transfer_start':'0x00000','transfer_end_inclusive':'0x1efff',
        'staging_start':'0x20000','staging_end_inclusive':'0x3efff',
        'boot_descriptor_transferred':True,'chunk_data_bytes':16,'data_packets':len(frames),
        'data_stream_sha256':digest(b''.join(frames)),
        'first_data_packet':frames[0].hex(' '),'last_data_packet':frames[-1].hex(' '),
        'control_information':['01 00','02 00','03 00'],
        'control_prepare_ota_loop':'0b 00, then disconnect/reconnect; stock firmware erases staging before advertising again',
        'control_start':'04 00','control_finish':'05 00','control_commit':'07 00',
        'protected_ranges':['0x1f000..0x1ffff (stock on-chip persistent state)',
                            '0x3f000..0x3ffff (stock bootloader, apart from its own status writes)'],
        'profile_key_copy':'captures/u1-profiles-and-keys-01.json (restore not tested)',
        'scope':'Offline experimental OTA file. Live update entry/transport, FW33 compatibility, RF and recovery after failure remain unverified.',
        'installed':False,'live_upload_authorized_in_this_build':False,
        'lab_disabled_on_reset':manifest['lab_disabled_on_reset'],'soundsend_firmware_changes':False,
    }
    (ROOT/'u1-lab-ota-plan.json').write_text(json.dumps(plan,indent=2)+'\n',encoding='utf-8')
    manifest['ota_package_generated']=True
    manifest['ota_package_file']=PACKAGE.name
    manifest['ota_package_sha256']=plan['package_sha256']
    (ROOT/'u1-v32-lab-manifest.json').write_text(json.dumps(manifest,indent=2)+'\n',encoding='utf-8')
    return plan

if __name__=='__main__':
    p=build();print('Offline OTA lab file:',p['package']);print('SHA256',p['package_sha256']);print('Data packets',p['data_packets'])
