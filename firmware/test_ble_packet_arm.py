"""Run ARM codec against Bluetooth SIG primary CRC and whitening samples.

Core Vol6 PartC2.1,4.1,4.2,4.3.1 (bit strings are transmission order):
https://www.bluetooth.com/wp-content/uploads/Files/Specification/HTML/Core-62/out/en/low-energy-controller/sample-data.html
No RF or device operations. This establishes wire bytes, not physical launch.
"""
import hashlib,json,unittest
from test_u1_lab_arm import Machine,ROOT,ELF,IMAGE
from test_client_arm import INPUT,OUTPUT

SOURCE='https://www.bluetooth.com/wp-content/uploads/Files/Specification/HTML/Core-62/out/en/low-energy-controller/sample-data.html'
def wire(bits):return bytes(int(b[::-1],2) for b in bits.split())

class PacketTests(unittest.TestCase):
    def setUp(self):self.m=Machine()
    def crc(self,p,init):
        self.m.machine.mem_write(INPUT,p)
        n=self.m.call('ble_crc24',INPUT,len(p),init)
        return n.to_bytes(3,'little')
    def whiten(self,p,ch):
        self.m.machine.mem_write(INPUT,p)
        self.assertEqual(self.m.call('ble_whiten',INPUT,len(p),ch),1)
        return bytes(self.m.machine.mem_read(INPUT,len(p)))
    def encode(self,header,p,init,ch):
        self.m.machine.mem_write(INPUT,p or b'\0')
        n=self.m.call('ble_encode_packet',header,INPUT,len(p),init,ch,OUTPUT,64)
        self.assertEqual(n,len(p)+5)
        return bytes(self.m.machine.mem_read(OUTPUT,n))
    def test_sig_reference_ble_crc(self):
        self.assertEqual(self.crc(bytes.fromhex('00 03 42 4c 45'),0x555555),bytes.fromhex('29 0a ce'))
    def test_sig_advertising_crc(self):
        self.assertEqual(self.crc(bytes.fromhex('42 09 a6 a5 a4 a3 a2 c1 01 02 03'),0x555555),wire('10110101 00101101 11010111'))
    def test_sig_connection_crc_with_asymmetric_initial_value(self):
        self.assertEqual(self.crc(bytes.fromhex('16 05 01 02 03 04 05'),0xC4C181),wire('10100010 00001011 01001011'))
    def test_sig_whitening_channel0(self):
        self.assertEqual(self.whiten(bytes(8),0),wire('00000010 01001101 00111101 11000011 11111000 11101100 01010010 11111010'))
    def test_sig_whitening_advertising_channels(self):
        for ch,bits in {
            37:'10110001 01001011 11101010 10000101 10111100 11100101 01100110 00001101',
            38:'01101011 10100011 00100010 00000100 10011010 01111011 10000111 11110001',
            39:'11111000 11101100 01010010 11111010 10100001 01101111 00111001 01011001',
        }.items():
            with self.subTest(channel=ch):self.assertEqual(self.whiten(bytes(8),ch),wire(bits))
    def test_sig_complete_advertising_packet(self):
        expected=wire('00101001 00110011 01000111 10100001 10111111 10111110 11000010 01110010 01011000 11100101 00110101 11110111 11110011 10100101')
        self.assertEqual(self.encode(0x42,bytes.fromhex('a6 a5 a4 a3 a2 c1 01 02 03'),0x555555,38),expected)
    def test_sig_complete_data_packet(self):
        expected=wire('01100011 11011001 01001010 10001100 11011011 01111101 10111001 10110010 00101111 10011000')
        self.assertEqual(self.encode(0x16,bytes.fromhex('01 02 03 04 05'),0xC4C181,16),expected)
    def test_invalid_length_channel_capacity_do_not_write_output(self):
        m=self.m;m.machine.mem_write(INPUT,bytes(35));sentinel=b'\xa5'*64
        for n,ch,capacity in ((35,37,64),(34,40,64),(34,37,38)):
            m.machine.mem_write(OUTPUT,sentinel)
            self.assertEqual(m.call('ble_encode_packet',5,INPUT,n,0x555555,ch,OUTPUT,capacity),0)
            self.assertEqual(bytes(m.machine.mem_read(OUTPUT,64)),sentinel)

if __name__=='__main__':
    result=unittest.TextTestRunner(verbosity=2).run(unittest.defaultTestLoader.loadTestsFromTestCase(PacketTests))
    report={'tests_run':result.testsRun,'passed':result.wasSuccessful(),'source':SOURCE,
            'module_sha256':hashlib.sha256(ELF.read_bytes()).hexdigest(),'image_sha256':hashlib.sha256(IMAGE.read_bytes()).hexdigest(),
            'scope':'ARM CRC/whitening against primary Bluetooth SIG vectors; not physical RF proof.'}
    (ROOT/'ble-packet-arm-test-results.json').write_text(json.dumps(report,indent=2)+'\n')
    raise SystemExit(0 if result.wasSuccessful() else 1)
