"""Behavioral tests executing the newly compiled Cortex-M0 code in Unicorn.

No host Bluetooth or physical device operations are available to this harness.
"""
from pathlib import Path
import hashlib
import json
import struct
import unittest
from uuid import UUID

from unicorn import Uc, UC_ARCH_ARM, UC_MODE_THUMB, UC_MODE_MCLASS
from unicorn.arm_const import UC_ARM_REG_R0, UC_ARM_REG_R1, UC_ARM_REG_R2, UC_ARM_REG_R3, UC_ARM_REG_SP, UC_ARM_REG_LR, UC_ARM_REG_PC

ROOT = Path(__file__).resolve().parent
ELF = ROOT / "soundsend-client-emulation.elf"
SERVICE = UUID("97ded94c-b564-48ab-ba96-7e1d2daa0edd").bytes[::-1]
CONTROL = UUID("a4a8d442-b8d0-404c-a0fb-f120115acf5e").bytes[::-1]
CONTEXT, INPUT, OUTPUT, STACK, STOP = 0x20006000, 0x20006400, 0x20006600, 0x20007FF0, 0x3FFF0


def load_elf(data):
    header = struct.unpack_from("<16sHHIIIIIHHHHHH", data)
    if header[0][:6] != b"\x7fELF\x01\x01" or header[2] != 40:
        raise ValueError("Expected little-endian ELF32 for ARM")
    segments = []
    for i in range(header[10]):
        kind, offset, vaddr, paddr, size, memsize, flags, align = struct.unpack_from("<8I", data, header[5] + i * header[9])
        if kind == 1:
            segments.append((vaddr, data[offset:offset+size]))
    sections = [struct.unpack_from("<10I", data, header[6]+i*header[11]) for i in range(header[12])]
    symbols = {}
    for section in sections:
        if section[1] != 2:
            continue
        string_section = sections[section[6]]
        strings = data[string_section[4]:string_section[4]+string_section[5]]
        for offset in range(section[4], section[4]+section[5], section[9]):
            name, address, size, info, other, index = struct.unpack_from("<IIIBBH", data, offset)
            if name and index:
                name = strings[name:strings.index(0, name)].decode()
                symbols[name] = address
    return segments, symbols


class ArmClient:
    def __init__(self,elf=ELF,initialize="ss_init"):
        self.machine = Uc(UC_ARCH_ARM, UC_MODE_THUMB | UC_MODE_MCLASS)
        self.machine.mem_map(0, 0x40000)
        self.machine.mem_map(0x20000000, 0x8000)
        segments, self.symbols = load_elf(elf.read_bytes())
        for address, data in segments:
            if data:
                self.machine.mem_write(address, data)
        self.call(initialize, CONTEXT)
        assert self.call("ss_context_size") < 256

    def call(self, name, *args, instruction_limit=100000):
        machine = self.machine
        call_stack=getattr(self,'call_stack',STACK)
        for register, value in zip((UC_ARM_REG_R0,UC_ARM_REG_R1,UC_ARM_REG_R2,UC_ARM_REG_R3),args):
            machine.reg_write(register, value & 0xFFFFFFFF)
        for index,value in enumerate(args[4:]):
            machine.mem_write(call_stack+4*index,struct.pack("<I",value&0xFFFFFFFF))
        machine.reg_write(UC_ARM_REG_SP, call_stack)
        machine.reg_write(UC_ARM_REG_LR, STOP | 1)
        machine.emu_start(self.symbols[name] | 1, STOP, count=instruction_limit)
        if machine.reg_read(UC_ARM_REG_PC) != STOP:
            raise AssertionError(f"{name} did not return within instruction limit")
        return machine.reg_read(UC_ARM_REG_R0)

    def receive(self, packet):
        self.machine.mem_write(INPUT, bytes(packet))
        self.call("ss_receive_att", CONTEXT, INPUT, len(packet))

    def tx(self):
        length = self.call("ss_take_tx", CONTEXT, OUTPUT, 23)
        return bytes(self.machine.mem_read(OUTPUT, length))

    def key(self, delta): self.call("ss_key_delta", CONTEXT, delta)
    def tick(self, milliseconds): self.call("ss_tick", CONTEXT, milliseconds)
    def state(self): return self.call("ss_status", CONTEXT)
    def volume(self):
        value = self.call("ss_volume", CONTEXT)
        return value if value < 0x80000000 else value - 0x100000000
    def notify(self, payload, handle=0x1D): self.receive(b"\x1b"+struct.pack("<H",handle)+bytes(payload))


class ClientTests(unittest.TestCase):
    def setUp(self): self.c = ArmClient()

    def connect(self, service=0x1B, end=0x1E, value=0x1D, descriptor=0x1E, early=0):
        c=self.c; c.call("ss_connected", CONTEXT)
        self.assertEqual(c.tx(), b"\x06\x01\x00\xff\xff\x00\x28"+SERVICE)
        for _ in range(abs(early)): c.key(1 if early>0 else -1)
        c.receive(b"\x07"+struct.pack("<HH",service,end))
        self.assertEqual(c.tx(), b"\x08"+struct.pack("<HHH",service,end,0x2803))
        c.receive(b"\x09\x15"+struct.pack("<HBH",service+1,0x14,value)+CONTROL)
        self.assertEqual(c.tx(), b"\x04"+struct.pack("<HH",value+1,end))
        c.receive(b"\x05\x01"+struct.pack("<HH",descriptor,0x2902))
        self.assertEqual(c.tx(), b"\x12"+struct.pack("<HH",descriptor,1))
        c.receive(b"\x13")
        self.assertEqual(c.tx(), b"\x52"+struct.pack("<H",value)+b"\x00\x04\x00")

    def ready(self, volume=18, **kwargs):
        self.connect(**kwargs)
        self.c.notify((2,4,1,volume),kwargs.get("value",0x1D))

    def test_discovery_and_dynamic_handles(self):
        self.ready(service=0x100,end=0x10A,value=0x102,descriptor=0x105)
        self.assertEqual(self.c.state(),6)
        self.c.key(1)
        self.assertEqual(self.c.tx(),bytes.fromhex("52 02 01 01 04 01 13"))
    def test_absolute_mute_and_restore_wait_for_confirmations(self):
        self.ready();c=self.c;c.call('ss_set_volume',CONTEXT,0)
        self.assertEqual(c.tx(),bytes.fromhex('52 1d 00 01 04 01 00'))
        c.call('ss_set_volume',CONTEXT,18);self.assertEqual(c.tx(),b'')
        c.notify((2,4,1,18));self.assertEqual(c.volume(),18);self.assertEqual(c.tx(),b'')
        c.notify((2,4,1,0));self.assertEqual(c.volume(),0)
        self.assertEqual(c.tx(),bytes.fromhex('52 1d 00 01 04 01 12'))
        c.notify((2,4,1,18));self.assertEqual(c.state(),6);self.assertEqual(c.volume(),18)
    def test_absolute_volume_rejects_unknown_and_out_of_range(self):
        c=self.c;c.call('ss_set_volume',CONTEXT,0);self.assertEqual(c.tx(),b'')
        self.connect();c.call('ss_set_volume',CONTEXT,0);self.assertEqual(c.tx(),b'')
        c.notify((2,4,1,18));c.call('ss_set_volume',CONTEXT,101);self.assertEqual(c.tx(),b'')
        self.assertEqual(c.volume(),18);self.assertEqual(c.state(),6)

    def test_rapid_keys_and_delayed_notification(self):
        self.ready(); c=self.c; c.key(1)
        self.assertEqual(c.tx(),bytes.fromhex("52 1d 00 01 04 01 13"))
        c.key(1); c.key(1); c.notify((2,4,1,18))
        self.assertEqual(c.tx(),b"")
        c.notify((2,4,1,19))
        self.assertEqual(c.tx(),bytes.fromhex("52 1d 00 01 04 01 15"))
        c.notify((2,4,1,19)); self.assertEqual(c.state(),7)
        c.notify((2,4,1,21)); self.assertEqual(c.volume(),21); self.assertEqual(c.state(),6)

    def test_direction_change_while_waiting(self):
        self.ready(); c=self.c; c.key(1); c.tx(); c.key(-1)
        c.notify((2,4,1,19))
        self.assertEqual(c.tx(),bytes.fromhex("52 1d 00 01 04 01 12"))
        c.notify((2,4,1,18)); self.assertEqual(c.state(),6)

    def test_volume_limits(self):
        self.ready(100); self.c.key(1); self.assertEqual(self.c.tx(),b"")
        self.c.notify((2,4,1,0)); self.c.key(-1); self.assertEqual(self.c.tx(),b"")
        self.c.key(1); self.assertEqual(self.c.tx()[-1],1)

    def test_buttons_before_initial_volume(self):
        self.ready(18,early=2)
        self.assertEqual(self.c.tx(),bytes.fromhex("52 1d 00 01 04 01 14"))

    def test_fragmented_and_multiple_tlvs(self):
        self.connect(); c=self.c
        c.notify((0xEE,0xEF,0,2,4)); self.assertEqual(c.volume(),-1)
        c.notify((1,18,0xEE,0xEF,2,0,0,2,4,1,19))
        self.assertEqual(c.volume(),19); self.assertEqual(c.state(),6)

    def test_wrong_handle_and_invalid_volume_ignored(self):
        self.connect(); c=self.c
        c.notify((2,4,1,18),0x99); self.assertEqual(c.volume(),-1)
        c.notify((2,4,1,101)); self.assertEqual(c.volume(),-1)
        c.notify((2,4,1,18)); self.assertEqual(c.volume(),18)

    def test_disconnect_drops_queued_keys(self):
        self.ready(); c=self.c; c.key(1); c.tx(); c.key(1)
        c.call("ss_disconnected",CONTEXT); c.key(1)
        self.assertEqual(c.state(),0); self.assertEqual(c.volume(),-1); self.assertEqual(c.tx(),b"")
        self.ready(); self.assertEqual(c.tx(),b""); self.assertEqual(c.volume(),18)

    def test_timeout_cancels_pending_change(self):
        self.ready(); c=self.c; c.key(1); c.tx(); c.tick(2999)
        self.assertEqual(c.state(),7); c.tick(1); c.key(1)
        self.assertEqual(c.state(),8); self.assertEqual(c.tx(),b"")

    def test_malformed_discovery_fails(self):
        for reply in (b"\x07",b"\x07\x00\x00\x01\x00",b"\x07\x20\x00\x10\x00"):
            self.c=ArmClient(); self.c.call("ss_connected",CONTEXT); self.c.tx()
            self.c.receive(reply); self.assertEqual(self.c.state(),8)

    def test_characteristic_requires_correct_properties(self):
        c=self.c; c.call("ss_connected",CONTEXT); c.tx()
        c.receive(bytes.fromhex("07 1b 00 1e 00")); c.tx()
        c.receive(b"\x09\x15"+struct.pack("<HBH",0x1C,0x08,0x1D)+CONTROL)
        self.assertEqual(c.state(),8); self.assertEqual(c.tx(),b"")

    def test_att_error_and_oversized_packet(self):
        self.c.call("ss_connected",CONTEXT); self.c.tx()
        self.c.receive(bytes.fromhex("01 06 01 00 0a")); self.assertEqual(self.c.state(),8)
        self.c=ArmClient(); self.c.call("ss_connected",CONTEXT); self.c.tx()
        self.c.receive(b"\x07"+bytes(23)); self.assertEqual(self.c.state(),8)

    def test_actual_saved_volume_roundtrip(self):
        self.connect(); c=self.c
        records=[json.loads(line) for line in (ROOT.parent/"captures/volume-roundtrip-01.jsonl").read_text().splitlines()]
        for record in records:
            if record["event"]=="rx": c.notify(bytes.fromhex(record["hex"]))
            if record["event"]=="tx":
                payload=bytes.fromhex(record["hex"])
                if payload[:3]==b"\x01\x04\x01":
                    c.key(payload[3]-c.volume())
                    self.assertEqual(c.tx(),b"\x52\x1d\x00"+payload)
        self.assertEqual(c.volume(),18); self.assertEqual(c.state(),6)

    def test_new_packets_through_original_v32_queue(self):
        image=(ROOT.parent/"research/firmware-candidates/OTA_U1_5_V32.bin").read_bytes()
        self.assertEqual(hashlib.sha256(image).hexdigest(),
                         "6facfda529024239abfa7eb7293d9d4d2ca17c11008c245699de94e4b5c49fc7")
        for packet_kind in ("discovery", "volume"):
            self.c=ArmClient(); c=self.c
            if packet_kind=="discovery": c.call("ss_connected",CONTEXT)
            else: self.ready(); c.key(1)
            att=c.tx(); frame=struct.pack("<HH",len(att),4)+att
            # Combine code in the EMULATOR only. No modified firmware is saved.
            c.machine.mem_write(0,image[36:])
            for address,data in load_elf(ELF.read_bytes())[0]:
                if address>=0x18000 and data:
                    self.assertTrue(all(x==0xFF for x in image[36+address:36+address+len(data)]))
                    c.machine.mem_write(address,data)
            c.machine.mem_write(INPUT,frame)
            c.symbols["original_v32_queue"]=0x7991
            # Unencrypted, empty queue fixture; original code runs without stubs.
            c.call("original_v32_queue",INPUT,len(frame))
            self.assertEqual(bytes(c.machine.mem_read(0x2000346C,len(frame))),frame)
            self.assertEqual(c.machine.mem_read(0x20000287,1)[0],1)
            self.assertEqual(struct.unpack("<H",c.machine.mem_read(0x20003458,2))[0],(len(frame)<<8)|2)


if __name__=="__main__":
    suite=unittest.defaultTestLoader.loadTestsFromTestCase(ClientTests)
    result=unittest.TextTestRunner(verbosity=2).run(suite)
    _,symbols=load_elf(ELF.read_bytes())
    client=ArmClient()
    report={"elf_sha256":hashlib.sha256(ELF.read_bytes()).hexdigest(),
            "source_sha256":hashlib.sha256((ROOT/"soundsend_client.c").read_bytes()).hexdigest(),
            "header_sha256":hashlib.sha256((ROOT/"soundsend_client.h").read_bytes()).hexdigest(),
            "test_harness_sha256":hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
            "tests_run":result.testsRun,"passed":result.wasSuccessful(),
            "target":"ARM Cortex-M0 / thumb-freestanding-eabi",
            "context_bytes":client.call("ss_context_size"),
            "public_functions":{k:hex(v) for k,v in symbols.items() if k.startswith("ss_")},
            "scope":"New Cortex-M0 protocol component executed offline; no BLE radio driver, key integration, OTA image, or real-device validation"}
    (ROOT/"arm-test-results.json").write_text(json.dumps(report,indent=2)+"\n",encoding="utf-8")
    raise SystemExit(0 if result.wasSuccessful() else 1)
