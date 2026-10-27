"""Execute original V32 OTA handlers and boot-copy code with flash HW modeled.

Only flash erase/program/read drivers and reset are intercepted; no BLE/device
access. Boot clock-start wait is skipped. Does not establish FW33 transport,
power-loss recovery, electrical flash behavior or physical hardware compatibility.
"""
from pathlib import Path
import hashlib,json,struct,unittest
from unicorn import Uc,UC_ARCH_ARM,UC_MODE_THUMB,UC_MODE_MCLASS,UC_HOOK_CODE
from unicorn.arm_const import UC_ARM_REG_R0,UC_ARM_REG_R1,UC_ARM_REG_R2,UC_ARM_REG_R3,UC_ARM_REG_SP,UC_ARM_REG_LR,UC_ARM_REG_PC
from build_ota_lab import PACKAGE,parse_package,data_request,APPLICATION_END

ROOT=Path(__file__).resolve().parent
BASE=ROOT.parent/'research/firmware-candidates/OTA_U1_5_V32.bin'
STOP=0x3FFF0
REQUEST,RESPONSE,LENGTH=0x20006000,0x20006200,0x20006400


class OtaMachine:
    def __init__(self):
        self.m=Uc(UC_ARCH_ARM,UC_MODE_THUMB|UC_MODE_MCLASS)
        self.m.mem_map(0,0x40000);self.m.mem_write(0,b'\xff'*0x40000)
        self.m.mem_write(0,BASE.read_bytes()[36:])
        self.m.mem_map(0x20000000,0x8000)
        self.m.mem_map(0x4001F000,0x1000)
        self.m.mem_map(0x50001000,0x1000)
        self.m.mem_map(0xE000E000,0x1000)
        for address in (0x40008000,0x40010000,0x50005000,0x50006000):self.m.mem_map(address,0x1000)
        self.flash_writes=[];self.boot_writes=[];self.reset=False;self.boot_finished=False
        self.m.hook_add(UC_HOOK_CODE,self.code)

    def reg(self,n):return self.m.reg_read((UC_ARM_REG_R0,UC_ARM_REG_R1,UC_ARM_REG_R2,UC_ARM_REG_R3)[n])
    def ret(self,value=0):
        self.m.reg_write(UC_ARM_REG_R0,value);self.m.reg_write(UC_ARM_REG_PC,self.m.reg_read(UC_ARM_REG_LR))
    def erase(self,address,phase):
        if address&127:raise AssertionError(('Unaligned erase',hex(address)))
        (self.boot_writes if phase=='boot' else self.flash_writes).append(('erase',address,128))
        self.m.mem_write(address,b'\xff'*128)
    def program(self,address,data,phase):
        if address&3 or len(data)>128:raise AssertionError(('Invalid program',hex(address),len(data)))
        before=bytes(self.m.mem_read(address,len(data)))
        if any((old&new)!=new for old,new in zip(before,data)):raise AssertionError('Program attempted0->1 without erase')
        (self.boot_writes if phase=='boot' else self.flash_writes).append(('program',address,len(data)))
        self.m.mem_write(address,data)

    def code(self,uc,pc,size,data):
        if pc==0x97AC:
            self.program(self.reg(0),bytes(uc.mem_read(self.reg(1),4*self.reg(2))),'ota');self.ret()
        elif pc==0x9796:self.erase(self.reg(0),'ota');self.ret()
        elif pc==0x70B4:
            uc.mem_write(self.reg(1),bytes(uc.mem_read(self.reg(0),self.reg(2))));self.ret()
        elif pc==0x4914:self.ret()
        elif pc==0x10A4:
            self.m.mem_write(0x20000270,b'\x01');self.ret()
        elif pc==0x3D6C:self.reset=True;uc.emu_stop()
        elif pc==0x3F19C:self.erase(self.reg(0),'boot');self.ret()
        elif pc==0x3F308:
            # The bootloader's128-byte programming driver copies one page.
            self.program(self.reg(0),bytes(uc.mem_read(self.reg(1),128)),'boot');self.ret()
        elif pc==0x3F36C:
            address,command,words=self.reg(0),self.reg(1),self.reg(2)
            if command==4:self.erase(address,'boot')
            elif command==1:self.program(address,bytes(uc.mem_read(0x4001F200,4*words)),'boot')
            else:raise AssertionError(('Unknown boot flash command',command))
            self.ret()

    def call(self,address,*args,count=100000):
        for r,v in zip((UC_ARM_REG_R0,UC_ARM_REG_R1,UC_ARM_REG_R2,UC_ARM_REG_R3),args):self.m.reg_write(r,v)
        self.m.reg_write(UC_ARM_REG_SP,0x20007F00);self.m.reg_write(UC_ARM_REG_LR,STOP|1)
        self.m.emu_start(address|1,STOP,count=count)
        if not self.reset and self.m.reg_read(UC_ARM_REG_PC)!=STOP:raise AssertionError(('Did not return',hex(self.m.reg_read(UC_ARM_REG_PC))))
        return self.reg(0)
    def request(self,address,packet):
        self.m.mem_write(REQUEST,packet);self.m.mem_write(LENGTH,b'\0')
        self.call(address,REQUEST,len(packet),RESPONSE,LENGTH)
        n=self.m.mem_read(LENGTH,1)[0];return bytes(self.m.mem_read(RESPONSE,n))
    def command(self,opcode):return self.request(0x2A0C,bytes([opcode,0]))
    def initialize_transfer(self):
        self.m.mem_write(0x20000,b'\xff'*0x1F000)
        for op in (1,2,3):
            if self.command(op)[3]!=1:raise AssertionError(op)
        if self.command(4)!=b'\x08\x02\x04\x01':raise AssertionError('start')
    def transfer(self,application):
        for address in range(0,APPLICATION_END,16):
            response=self.request(0x2B28,data_request(address,application[address:address+16]))
            if response!=address.to_bytes(3,'little')+b'\x01':raise AssertionError((hex(address),response.hex()))


class OtaTests(unittest.TestCase):
    def test_ota_loop_entry_erases_dirty_staging_without_touching_active_app(self):
        m=OtaMachine();original=bytes(m.m.mem_read(0,0x20000))
        m.m.mem_write(0x20000,bytes(APPLICATION_END))
        self.assertEqual(m.command(11),b'\x08\x02\x0b\x06')
        self.assertEqual(m.command(6),b'\x08\x02\x06\x01')
        self.assertEqual(m.m.mem_read(0x20000369,1)[0]&8,8)
        m.m.mem_write(0x20000270,b'\0')
        m.m.reg_write(UC_ARM_REG_SP,0x20007F00);m.m.reg_write(UC_ARM_REG_LR,STOP|1)
        m.m.emu_start(0x41AD,0x42F2,count=1000000)
        self.assertEqual(m.m.reg_read(UC_ARM_REG_PC),0x42F2)
        self.assertEqual(bytes(m.m.mem_read(0x20000,APPLICATION_END)),b'\xff'*APPLICATION_END)
        self.assertEqual(bytes(m.m.mem_read(0,0x20000)),original)
        self.assertTrue(all(0x20000<=a and a+n<=0x3F000 for _,a,n in m.flash_writes))
    def test_full_transfer_commit_and_boot_copy_preserve_persistent_state(self):
        m=OtaMachine();application=parse_package(PACKAGE.read_bytes())
        # Distinct fixtures prove these areas are not replaced by file contents.
        persistent=bytes((n*73+17)&255 for n in range(0x1000))
        m.m.mem_write(0x1F000,persistent)
        bootloader=bytes(m.m.mem_read(0x3F000,0xF80))
        m.initialize_transfer();m.transfer(application)
        self.assertEqual(bytes(m.m.mem_read(0x20000,APPLICATION_END)),application)
        self.assertEqual(m.command(5),b'\x08\x02\x05\x01')
        self.assertEqual(m.command(7),b'\x08\x02\x07\x01')
        # Re-arming the same completely acknowledged staging is idempotent.
        # Device-side termination ensures commit does not wait on Windows.
        self.assertEqual(m.command(7),b'\x08\x02\x07\x01')
        self.assertEqual(m.command(6),b'\x08\x02\x06\x01')
        self.assertEqual(m.m.mem_read(0x20000369,1)[0]&1,1)
        m.m.mem_write(0x20000270,b'\0')
        m.call(0x41AC)
        self.assertTrue(m.reset)
        self.assertEqual(bytes(m.m.mem_read(0x1EF80,128)),b'\xff'*128)
        # Original bootloader resumes after its clock-ready wait; keep all
        # descriptor checks and copy-loop instructions from the downloaded V32.
        m.reset=False;m.m.mem_write(0x3FF80,b'\xff'*128)
        m.m.mem_write(0x20007F00-28,struct.pack('<7I',0,0,0,0,0,0,STOP|1))
        m.m.reg_write(UC_ARM_REG_SP,0x20007F00-28);m.m.reg_write(UC_ARM_REG_LR,STOP|1)
        m.m.emu_start(0x3F07D,STOP,count=1000000)
        self.assertEqual(m.m.reg_read(UC_ARM_REG_PC),STOP)
        self.assertEqual(bytes(m.m.mem_read(0,APPLICATION_END)),application)
        self.assertEqual(bytes(m.m.mem_read(0x1F000,0x1000)),persistent)
        self.assertEqual(bytes(m.m.mem_read(0x3F000,0xF80)),bootloader)
        self.assertEqual(bytes(m.m.mem_read(0x3FF80,4)),b'\xaa'*4)
        self.assertTrue(all(0x20000<=a and a+n<=0x3F000 for kind,a,n in m.flash_writes if kind=='program'))
    def test_disconnect_from_prepared_loop_erases_staging_and_resets_without_commit(self):
        m=OtaMachine();original=bytes(m.m.mem_read(0,0x20000))
        self.assertEqual(m.command(11),b'\x08\x02\x0b\x06')
        m.m.mem_write(0x20000270,b'\0')
        m.m.reg_write(UC_ARM_REG_SP,0x20007F00);m.m.reg_write(UC_ARM_REG_LR,STOP|1)
        m.m.emu_start(0x41AD,0x42FA,count=1000000)
        self.assertEqual(m.m.reg_read(UC_ARM_REG_PC),0x42FA)
        self.assertEqual(m.command(11),b'\x08\x02\x0b\x01')
        m.m.mem_write(0x20000270,b'\x03')
        self.assertEqual(m.command(6),b'\x08\x02\x06\x01')
        self.assertEqual(struct.unpack('<I',m.m.mem_read(0x20000334,4))[0]&1,1)
        m.m.mem_write(0x20000,b'\x00'*APPLICATION_END)
        m.m.mem_write(0x20000270,b'\0')
        # Resume the original dedicated loop after its advertising setup.
        m.m.emu_start(0x41F9,STOP,count=1000000)
        self.assertTrue(m.reset)
        self.assertEqual(bytes(m.m.mem_read(0x20000,APPLICATION_END)),b'\xff'*APPLICATION_END)
        self.assertEqual(bytes(m.m.mem_read(0,0x20000)),original)
        self.assertTrue(all(kind=='erase' and 0x20000<=a and a+n<=0x3F000 for kind,a,n in m.flash_writes))
    def test_out_of_order_data_is_rejected_without_flash_write(self):
        m=OtaMachine();m.initialize_transfer()
        self.assertEqual(m.request(0x2B28,data_request(0,bytes(16)))[-1],1)
        self.assertEqual(m.request(0x2B28,data_request(32,bytes(16)))[-1],3)
        self.assertEqual(m.flash_writes,[])
    def test_start_requires_information_handshake(self):
        self.assertEqual(OtaMachine().command(4),b'\x08\x02\x04\x05')
    def test_bootloader_rejects_corrupt_descriptor_without_copying(self):
        m=OtaMachine();descriptor=bytearray(parse_package(PACKAGE.read_bytes())[0x1EF80:0x1F000])
        descriptor[28]^=1;m.m.mem_write(0x3EF80,bytes(descriptor))
        self.assertEqual(m.call(0x3F138,16),0xFFFFFFFF)
        self.assertEqual(m.boot_writes,[])


if __name__=='__main__':
    result=unittest.TextTestRunner(verbosity=2).run(unittest.defaultTestLoader.loadTestsFromTestCase(OtaTests))
    report={'tests_run':result.testsRun,'passed':result.wasSuccessful(),
            'package_sha256':hashlib.sha256(PACKAGE.read_bytes()).hexdigest(),
            'scope':'Original V32 OTA command/data handlers + boot descriptor checks/copy loop. Flash drivers modeled, clock wait skipped. No actual FW33/BLE transport, power-loss or recovery validation.'}
    (ROOT/'ota-arm-test-results.json').write_text(json.dumps(report,indent=2)+'\n',encoding='utf-8')
    raise SystemExit(0 if result.wasSuccessful() else 1)
