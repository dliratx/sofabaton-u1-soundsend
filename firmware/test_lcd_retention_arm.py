"""Regression for loss of upper LCD SRAM in the chip's sleep modes.

Models bank loss only; does not simulate physical sleep/wakeup or RF behavior.
"""
import hashlib,json,unittest
from test_u1_lab_arm import Machine,ROOT,ELF,IMAGE


class RetentionTests(unittest.TestCase):
    def test_mute_restore_level_survives_upper_lcd_bank_loss(self):
        m=Machine();m.debug(3,1);m.key(2,13);state=m.run_until_idle()
        self.assertEqual(state[8],0);self.assertEqual(m.peer.writes,[0])
        m.machine.mem_write(0x20004000,b'\xa5'*0x4000)
        m.key(2,13);state=m.run_until_idle()
        self.assertEqual(state[7],0);self.assertEqual(state[8],18);self.assertEqual(m.peer.writes,[0,18])
    def test_upper_lcd_bank_loss_preserves_lab_and_volume_path(self):
        m=Machine();m.debug(3,1)
        m.machine.mem_write(0x20004000,b'\xa5'*0x4000)
        state=m.debug(1)
        self.assertEqual(state[4:6],b'\x01\x00')
        self.assertLess(m.call_stack,0x20004000)
        self.assertLess(m.symbols['__ss_ram_end'],0x20004000)
        m.key(2,4);state=m.run_until_idle()
        self.assertEqual(state[7],0);self.assertEqual(state[8],19)
        self.assertEqual(m.peer.writes,[19])
        self.assertGreaterEqual(m.lowest_sp,m.symbols['__ss_stack_limit'])


if __name__=='__main__':
    result=unittest.TextTestRunner(verbosity=2).run(unittest.defaultTestLoader.loadTestsFromTestCase(RetentionTests))
    report={'tests_run':result.testsRun,'passed':result.wasSuccessful(),
            'module_sha256':hashlib.sha256(ELF.read_bytes()).hexdigest(),
            'image_sha256':hashlib.sha256(IMAGE.read_bytes()).hexdigest(),
            'scope':'Actual patched ARM state/control survives modeled complete LCD-SRAM loss; RF/timer remain modeled. Not a physical sleep/RF test.'}
    (ROOT/'lcd-retention-arm-test-results.json').write_text(json.dumps(report,indent=2)+'\n')
    raise SystemExit(0 if result.wasSuccessful() else 1)
