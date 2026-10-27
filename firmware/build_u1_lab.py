"""Create a V32-only OFFLINE lab image and an auditable change manifest.

No Bluetooth/USB API is imported. This does not create or send an OTA package.
"""
from pathlib import Path
import hashlib
import json
import struct
from test_client_arm import load_elf

ROOT=Path(__file__).resolve().parent
BASE=ROOT.parent/"research/firmware-candidates/OTA_U1_5_V32.bin"
BASE_SHA="6facfda529024239abfa7eb7293d9d4d2ca17c11008c245699de94e4b5c49fc7"
ELF=ROOT/"u1-lab-module.elf"


def create_image():
    original=BASE.read_bytes()
    if hashlib.sha256(original).hexdigest()!=BASE_SHA:raise ValueError("Unrecognized base image")
    image=bytearray(original[36:]);elf=ELF.read_bytes();_,symbols=load_elf(elf)
    header=struct.unpack_from("<16sHHIIIIIHHHHHH",elf)
    patches=[]
    def put(address,data,reason):
        before=bytes(image[address:address+len(data)])
        if len(before)!=len(data):raise ValueError("Patch outside image")
        image[address:address+len(data)]=data
        patches.append({"address":hex(address),"bytes":len(data),"before_sha256":hashlib.sha256(before).hexdigest(),
                        "after_sha256":hashlib.sha256(data).hexdigest(),"reason":reason})
    for i in range(header[12]):
        section=struct.unpack_from("<10I",elf,header[6]+i*header[11])
        _,kind,flags,address,offset,size,*_=section
        if not (flags&2) or not size or kind==8:continue
        if not (0x18000<=address and address+size<=0x1EF80):raise ValueError("Allocated code outside validated patch range")
        if any(x!=0xFF for x in image[address:address+size]):raise ValueError("New code overlaps nonempty original flash")
        put(address,elf[offset:offset+size],"Allocated ELF section in unused V32 flash")
    if not (0x200039D8<=symbols['__ss_ram_start']<=symbols['__ss_ram_end']<=0x20003B80):
        raise ValueError("Patch state exceeds reserved retained SRAM")
    if not (symbols['__ss_ram_end']<=symbols['__ss_stack_limit']<symbols['__ss_stack_top']<0x20004000):
        raise ValueError('Main stack overlaps state or enters nonretained LCD SRAM')
    # Both stock8720 and87d8 copy this manufacturer address into20000175.
    # Public V32 carries a generic MAC; retain the user's captured unit identity.
    if image[0x151FF:0x15205]!=bytes.fromhex('38 7a 93 ff ff f4'):
        raise ValueError('Unexpected factory Bluetooth-address constant')
    put(0x151FF,bytes.fromhex('20 21 03 00 2e ab'),
        'Preserve original unit Bluetooth identity captured before experimental OTA')
    if struct.unpack_from('<I',image,4)[0]!=0x181:raise ValueError("Unexpected original reset vector")
    # The original reset handler invokes the bootloader before scatter loading.
    # After an OTA copy it continues in the new image without re-reading the
    # reset vector. Hook its post-scatter main call so the first OTA boot is
    # initialized too; leave the reset vector and bootloader call unchanged.
    if image[0x132:0x136]!=bytes.fromhex('0c f0 39 f8'):raise ValueError("Main-call mismatch")
    displacement=(symbols['u1_boot_gateway']&~1)-(0x132+4)
    if not -0x400000<=displacement<0x400000 or displacement&1:raise ValueError("Main hook out of BL range")
    branch=struct.pack('<HH',0xf000|((displacement>>12)&0x7ff),0xf800|((displacement>>1)&0x7ff))
    put(0x132,branch,"Initialize patch RAM after stock OTA bootloader and scatter loader, before main")
    if image[0x10B58:0x10B60]!=bytes.fromhex('f0 b5 9f b0 05 46 0e 46'):
        raise ValueError("Key-handler prologue mismatch")
    key=bytes.fromhex('00 4b 18 47')+struct.pack('<I',symbols['u1_key_gateway']|1)
    put(0x10B58,key,"Key gateway; original prologue replayed by trampoline")
    if image[0x11EC8:0x11ED0]!=bytes.fromhex('f8 b5 00 24 00 20 00 90'):
        raise ValueError('Screen-renderer prologue mismatch')
    put(0x11EC8,bytes.fromhex('00 4b 18 47')+struct.pack('<I',symbols['u1_screen_gateway']|1),
        'Native OLED screen gateway; dedicated SoundSend profile displays confirmed volume after RF release')
    if image[0x41AC:0x41BC]!=bytes.fromhex('f0 b5 ff 48 85 b0 01 78 00 25 fe 4f fe 4c 03 29'):
        raise ValueError("Poll-handler prologue mismatch")
    poll=bytes.fromhex('08 b4 02 4b 9c 46 08 bc 60 47 00 bf')+struct.pack('<I',symbols['u1_poll_gateway']|1)
    put(0x41AC,poll,"Poll gateway preserving r0..r3 and stack alignment")
    echo_call=0x1084E
    def encode_bl(source,target):
        delta=target-(source+4)
        if not -0x400000<=delta<0x400000 or delta&1:raise ValueError('Echo hook out of BL range')
        return struct.pack('<HH',0xf000|((delta>>12)&0x7ff),0xf800|((delta>>1)&0x7ff))
    if image[echo_call:echo_call+4]!=encode_bl(echo_call,0x1338):raise ValueError('Original FFF4 echo-call mismatch')
    put(echo_call,encode_bl(echo_call,symbols['u1_echo_gateway']&~1),
        'Intercept diagnostics at the original FFF4 echo dispatch; ordinary writes retain original notification path')
    # Preserve metadata, persistent settings, bootloader and the downloaded source.
    if image[0x1EF80:]!=original[36+0x1EF80:]:raise AssertionError("Protected tail changed")
    report={"base_file":str(BASE),"base_sha256":BASE_SHA,"target":"Downloaded V32 HW3/lote20190404 ONLY; not FW33",
            "module_sha256":hashlib.sha256(elf).hexdigest(),"raw_image_sha256":hashlib.sha256(image).hexdigest(),
            "patches":patches,"ram_start":hex(symbols['__ss_ram_start']),"ram_end":hex(symbols['__ss_ram_end']),
            "main_stack_limit":hex(symbols['__ss_stack_limit']),"main_stack_top":hex(symbols['__ss_stack_top']),
            "lab_disabled_on_reset":False,"soundSend_firmware_modified":False,
            "boot_radio_configuration":{"tx_mode":10,"rx_clear_mode":2,"tx_lead_us":72},
            "lab_protocol_revision":17,"lab_build_id":"0x544d5248",
            "preserved_device_address":"20:21:03:00:2E:AB",
            "physical_radio_validation":False,"ota_package_generated":False,"installed":False,
            "scope":"Offline experimental image. Radio launch modes, IRQ/main-loop interactions and recovery remain unverified on physical hardware."}
    out=ROOT/'u1-v32-lab-offline.bin';out.write_bytes(image)
    (ROOT/'u1-v32-lab-manifest.json').write_text(json.dumps(report,indent=2)+'\n',encoding='utf-8')
    return report


if __name__=='__main__':
    report=create_image()
    print('Offline V32 image built; no OTA package, no device writes')
    print('SHA256',report['raw_image_sha256'])
    print('New RAM',report['ram_start'],report['ram_end'])
