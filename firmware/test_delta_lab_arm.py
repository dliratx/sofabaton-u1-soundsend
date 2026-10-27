"""Original V32 OTA/boot-copy instructions for an incremental LAB correction."""
from pathlib import Path
import hashlib,json,struct,unittest
from test_ota_lab_arm import OtaMachine,STOP
from build_delta_lab import ROOT,STATE,PACKAGE,APP_END,PAGE_END,parse_delta,create_delta,digest
from build_ota_lab import data_request
from unicorn.arm_const import UC_ARM_REG_SP,UC_ARM_REG_LR,UC_ARM_REG_PC


class DeltaTests(unittest.TestCase):
    def test_installed_lab_routes_normal_mode_ota_without_dedicated_loop(self):
        state=json.loads(STATE.read_text());before=(ROOT/state['expected_application_file']).read_bytes()
        m=OtaMachine();m.m.mem_write(0,before);m.initialize_transfer()
        m.m.mem_write(0x20000270,b'\x03');m.m.mem_write(0x2000007C,b'\x01')
        m.m.mem_write(0x2000013E,b'\x01');m.m.mem_write(0x200002B6,struct.pack('<H',97))
        packet=data_request(0x15180,bytes(16))
        m.m.mem_write(0x200013CE,packet);m.m.mem_write(0x20000374,struct.pack('<I',0x200013CE))
        m.m.mem_write(0x2000036B,bytes([len(packet)]))
        m.call(0x41AC)
        frame=bytes(m.m.mem_read(0x200032AE,11))
        self.assertEqual(frame[:5],bytes.fromhex('07 00 04 00 1b'))
        self.assertEqual(frame[-4:],bytes.fromhex('80 51 01 01'))
        self.assertEqual(m.m.mem_read(0x20000369,1)[0]&8,0,'Dedicated OTA loop must remain disabled')
    def test_complete_incremental_transfer_and_two_region_boot_copy(self):
        application,ranges=parse_delta(PACKAGE.read_bytes())
        state=json.loads(STATE.read_text());before=(ROOT/state['expected_application_file']).read_bytes()
        m=OtaMachine();m.m.mem_write(0,before)
        # Also load the installed module used by its real poll-hook veneer.
        # Module bytes are already part of before, and its BSS starts zeroed.
        persistent=bytes((i*31+7)&255 for i in range(0x1000));m.m.mem_write(0x1F000,persistent)
        boot=bytes(m.m.mem_read(0x3F000,0xF80));m.initialize_transfer()
        for i,(start,end) in enumerate(ranges):
            if i:self.assertEqual(m.command(4)[3],1)
            for address in range(start,end,16):
                response=m.request(0x2B28,data_request(address,application[address:address+16]))
                self.assertEqual(response,address.to_bytes(3,'little')+b'\x01')
            self.assertEqual(m.command(5)[3],1)
        self.assertEqual(m.command(7)[3],1);m.m.mem_write(0x20000270,b'\0');m.call(0x41AC)
        self.assertTrue(m.reset);m.reset=False
        # A prior successful physical update leaves theAAAA status marker.
        # Exercise the actual repeated-update entry instead of blanking it.
        m.m.mem_write(0x3FF80,b'\xaa'*4+bytes(124))
        m.m.mem_write(0x20007F00-28,struct.pack('<7I',0,0,0,0,0,0,STOP|1))
        m.m.reg_write(UC_ARM_REG_SP,0x20007F00-28);m.m.reg_write(UC_ARM_REG_LR,STOP|1)
        m.m.emu_start(0x3F07D,STOP,count=1000000)
        self.assertEqual(m.m.reg_read(UC_ARM_REG_PC),STOP)
        self.assertEqual(bytes(m.m.mem_read(0,PAGE_END)),application)
        self.assertEqual(bytes(m.m.mem_read(0x1F000,0x1000)),persistent)
        self.assertEqual(bytes(m.m.mem_read(0x3F000,0xF80)),boot)
        programmed=[(a,n) for kind,a,n in m.boot_writes if kind=='program' and a<0x1F000]
        expected=[(a,128) for start,end in ranges for a in range(start,end,128)]
        self.assertEqual(programmed,expected)
        self.assertEqual(bytes(m.m.mem_read(0x151FF,6)),bytes.fromhex('20 21 03 00 2e ab'))
    def test_no_change_is_not_an_update(self):
        state=json.loads(STATE.read_text());before=(ROOT/state['expected_application_file']).read_bytes()
        with self.assertRaises(ValueError):create_delta(before,before)
    def test_descriptor_or_region_tampering_is_rejected(self):
        p=bytearray(PACKAGE.read_bytes());p[-1]^=1
        with self.assertRaises(ValueError):parse_delta(p)


if __name__=='__main__':
    result=unittest.TextTestRunner(verbosity=2).run(unittest.defaultTestLoader.loadTestsFromTestCase(DeltaTests))
    report={'tests_run':result.testsRun,'passed':result.wasSuccessful(),'package_sha256':digest(PACKAGE.read_bytes()),
            'scope':'Incremental multi-region OTA + original boot descriptor validation/copy loop with modeled flash drivers. Physical updater not yet exercised.'}
    (ROOT/'delta-arm-test-results.json').write_text(json.dumps(report,indent=2)+'\n')
    raise SystemExit(0 if result.wasSuccessful() else 1)
