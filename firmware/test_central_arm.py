"""Execute the new central + SoundSend client on ARM against a protocol peer.

The peer is a test model, not a simulated RF chip or a physical Bluetooth link.
"""
from pathlib import Path
import hashlib
import json
import struct
import unittest
from test_client_arm import ArmClient, CONTEXT, INPUT, OUTPUT, SERVICE, CONTROL

ROOT=Path(__file__).resolve().parent
ELF=ROOT/"central-emulation.elf"
OWN=bytes.fromhex("ab 2e 00 03 21 20")
PEER=bytes.fromhex("0a 93 18 93 90 cc")
MASK=0xFFFFFFFF
def ticks(us): return (us*16)&MASK
def l2cap(p,cid=4): return struct.pack("<HH",len(p),cid)+bytes(p)


class Central(ArmClient):
    def __init__(self):
        super().__init__(ELF,"bc_init")
        self.anchor=0;self.peer_sn=0
    def state(self): return self.call("bc_status",CONTEXT)
    def volume(self):
        v=self.call("bc_volume",CONTEXT)
        return v if v<0x80000000 else v-0x100000000
    def key(self,delta): self.call("bc_key_delta",CONTEXT,delta)
    def start(self,now=0,seed=0x12345678):
        self.machine.mem_write(INPUT,OWN);self.machine.mem_write(INPUT+16,PEER)
        return self.call("bc_start",CONTEXT,INPUT,0,INPUT+16,0,seed,now)
    def poll(self,now): self.call("bc_poll",CONTEXT,now&MASK)
    def radio_action(self):
        size=self.call("bc_take_action",CONTEXT,OUTPUT,64)
        if not size:return None
        if size!=56:raise AssertionError(size)
        at,deadline,aa,crc,kind,channel,header,n,p=struct.unpack("<IIIIBBBB34s2x",self.machine.mem_read(OUTPUT,size))
        return dict(at=at,deadline=deadline,aa=aa,crc=crc,kind=kind,channel=channel,header=header,payload=p[:n])
    def rx(self,header,p,end,valid=1):
        self.machine.mem_write(INPUT,bytes(p));self.call("bc_rx",CONTEXT,header,INPUT,len(p),end&MASK,valid)
    def tx_done(self,end): self.call("bc_tx_done",CONTEXT,end&MASK)
    def establish(self,now=0,early=0):
        assert self.start(now)==1
        assert self.radio_action()["kind"]==1
        for _ in range(abs(early)):self.key(1 if early>0 else -1)
        self.rx(0,PEER,(now+ticks(1000))&MASK)
        a=self.radio_action();assert a["kind"]==2
        end=(a["at"]+ticks((10+len(a["payload"]))*8))&MASK
        self.tx_done(end);self.anchor=(end+ticks(1750))&MASK
        return a
    def event(self,response=b"",llid=2,valid=1,ack=True,sn=None):
        self.poll((self.anchor-ticks(500))&MASK)
        action=self.radio_action();assert action and action["kind"]==3,action
        end=(action["at"]+ticks((10+len(action["payload"]))*8))&MASK
        self.tx_done(end)
        payload=response(action) if callable(response) else response
        tx_sn=(action["header"]>>3)&1
        peer_sn=self.peer_sn if sn is None else sn
        h=(llid if payload else 1)|((tx_sn^int(ack))<<2)|(peer_sn<<3)
        self.rx(h,payload,(end+ticks(150+(10+len(payload))*8))&MASK,valid)
        if valid and sn is None:self.peer_sn^=1
        if valid:
            idle=self.radio_action();assert idle and idle["kind"]==4,idle
        else:
            self.poll(end+ticks(2001));assert self.radio_action()["kind"]==4
        self.anchor=(self.anchor+ticks(30000))&MASK
        return action


class Peer:
    """Independent default-MTU ATT server using this device's captured UUIDs."""
    def __init__(self):self.volume=18;self.writes=[]
    def reply(self,a):
        if (a["header"]&3)!=2 or not a["payload"]:return b""
        declared,cid=struct.unpack_from("<HH",a["payload"])
        p=a["payload"][4:];assert len(p)==declared
        if cid!=4:return b""
        if p[0]==6:
            assert p==b"\x06\x01\x00\xff\xff\x00\x28"+SERVICE
            reply=bytes.fromhex("07 1b 00 1e 00")
        elif p[0]==8:
            assert p[-2:]==b"\x03\x28"
            reply=b"\x09\x15"+struct.pack("<HBH",0x1C,0x14,0x1D)+CONTROL
        elif p[0]==4:reply=bytes.fromhex("05 01 1e 00 02 29")
        elif p[0]==0x12:
            assert p==bytes.fromhex("12 1e 00 01 00");reply=b"\x13"
        elif p[0]==0x52:
            assert p[1:3]==b"\x1d\x00"
            if p[3:6]==b"\x01\x04\x01":
                assert 0<=p[6]<=100;self.volume=p[6];self.writes.append(p[6])
            else:assert p[3:]==b"\x00\x04\x00"
            reply=b"\x1b\x1d\x00\x02\x04\x01"+bytes([self.volume])
        else:raise AssertionError(p.hex())
        return l2cap(reply)


class CentralTests(unittest.TestCase):
    def setUp(self):self.c=Central()
    def ready(self,early=0):
        self.c.establish(early=early);peer=Peer()
        for _ in range(5):self.c.event(peer.reply)
        return peer
    def test_access_address_constraints(self):
        for value in (0,0xFFFFFFFF,0x8E89BED6,0x8E89BED7,0x55555555,0xCCCCCCCC):
            self.assertEqual(self.c.call("bc_valid_access_address",value),0,hex(value))
        self.assertEqual(self.c.call("bc_valid_access_address",0x12345678),1)
    def test_connect_ind_wire_format_and_timing(self):
        a=self.c.establish()
        self.assertEqual(a["at"],ticks(1150));self.assertEqual(a["header"],5)
        p=a["payload"];self.assertEqual(len(p),34);self.assertEqual(p[:12],OWN+PEER)
        self.assertEqual(p[19:],bytes.fromhex("02 00 00 18 00 00 00 c8 00 ff ff ff ff 1f 05"))
        self.assertTrue(self.c.call("bc_valid_access_address",struct.unpack_from("<I",p,12)[0]))
    def test_selects_only_known_peer_and_address_type(self):
        self.c.start();self.c.radio_action()
        for header,p in [(0,bytes(6)),(0x40,PEER),(2,PEER),(1,PEER+bytes(6))]:
            self.c.rx(header,p,ticks(1000));self.assertEqual(self.c.state(),1);self.assertIsNone(self.c.radio_action())
    def test_crc_failed_advertisement_ignored(self):
        self.c.start();self.c.radio_action();self.c.rx(0,PEER,ticks(1000),0)
        self.assertEqual(self.c.state(),1);self.assertIsNone(self.c.radio_action())
    def test_scan_hops_and_timeout(self):
        self.c.start();self.c.radio_action()
        for time,channel in [(20000,38),(40000,39),(60000,37)]:
            self.c.poll(ticks(time));self.assertEqual(self.c.radio_action()["channel"],channel)
        self.c.poll(ticks(3000000));self.assertEqual(self.c.state(),6)
    def test_zero_entropy_is_rejected(self):
        self.assertEqual(self.c.start(seed=0),0);self.assertEqual(self.c.state(),6)
    def test_full_client_volume_roundtrip(self):
        peer=self.ready();self.assertEqual(self.c.volume(),18)
        self.c.key(-1);self.c.event(peer.reply);self.assertEqual(self.c.volume(),17)
        self.c.key(1);self.c.event(peer.reply);self.assertEqual(self.c.volume(),18)
        self.assertEqual(peer.writes,[17,18])
    def test_key_queued_while_scanning(self):
        peer=self.ready(early=2);self.c.event(peer.reply)
        self.assertEqual(self.c.volume(),20);self.assertEqual(peer.writes,[20])
    def test_connection_hops_and_sequence_numbers(self):
        self.c.establish();channels=[];headers=[]
        for _ in range(9):
            a=self.c.event();channels.append(a["channel"]);headers.append(a["header"])
        self.assertEqual(channels,[5,10,15,20,25,30,35,3,8])
        self.assertEqual([(h>>3)&1 for h in headers],[0,1,0,1,0,1,0,1,0])
    def test_bad_crc_keeps_packet_for_retransmission(self):
        self.c.establish();a=self.c.event(valid=0);b=self.c.event()
        self.assertEqual(a["payload"],b["payload"]);self.assertEqual(a["header"],b["header"])
        self.assertNotEqual(a["channel"],b["channel"])
    def test_missing_ack_retransmits_without_advancing_tx_sn(self):
        self.c.establish();a=self.c.event(ack=False);b=self.c.event()
        self.assertEqual(a["payload"],b["payload"]);self.assertEqual((a["header"]>>3)&1,(b["header"]>>3)&1)
    def test_feature_request_and_original_version_reply(self):
        self.c.establish();self.c.event(bytes([8])+bytes(8),llid=3)
        a=self.c.event();self.assertEqual(a["header"]&3,3);self.assertEqual(a["payload"],bytes([9])+bytes(8))
        self.c.event(bytes.fromhex("0c 06 0a 00 01 00"),llid=3)
        a=self.c.event();self.assertEqual(a["payload"],bytes.fromhex("0c 06 c0 00 01 00"))
    def test_unknown_ll_procedure_replied(self):
        self.c.establish();self.c.event(bytes([0x14])+bytes(8),llid=3)
        self.assertEqual(self.c.event()["payload"],bytes.fromhex("07 14"))
    def test_connection_parameter_request_is_rejected(self):
        self.c.establish();self.c.event(l2cap(bytes.fromhex("12 07 08 00 18 00 28 00 00 00 c8 00"),5))
        self.assertEqual(self.c.event()["payload"],l2cap(bytes.fromhex("13 07 02 00 01 00"),5))
    def test_fragmented_l2cap_response(self):
        self.c.establish()
        reply=bytes.fromhex("07 1b 00 1e 00")
        self.c.event(struct.pack("<HH",len(reply),4)+reply[:2])
        self.c.event(reply[2:],llid=1)
        self.assertEqual(self.c.event()["payload"][4:5],b"\x08")
    def test_peer_termination_stops_client(self):
        self.c.establish();self.c.event(bytes.fromhex("02 13"),llid=3)
        self.assertEqual(self.c.state(),6);self.assertEqual(self.c.volume(),-1)
    def test_clock_wrap_preserves_first_anchor(self):
        self.c.establish(now=0xFFFFF000);a=self.c.event()
        self.assertEqual(a["kind"],3);self.assertEqual(a["channel"],5)
    def test_stop_cancels_early_key_events(self):
        self.c.start();self.c.radio_action();self.c.key(1)
        self.c.call("bc_stop",CONTEXT,ticks(1000))
        self.assertEqual(self.c.state(),0);self.assertEqual(self.c.radio_action()["kind"],4)
    def test_graceful_disconnect_sends_terminate_and_waits_for_ack(self):
        self.ready()
        self.c.call('bc_disconnect',CONTEXT,(self.c.anchor-ticks(1000))&MASK)
        a=self.c.event(ack=False)
        self.assertEqual(a['header']&3,3);self.assertEqual(a['payload'],bytes.fromhex('02 13'))
        self.assertNotEqual(self.c.state(),0)
        b=self.c.event();self.assertEqual(b['payload'],a['payload'])
        self.assertEqual(self.c.state(),0)
    def test_graceful_disconnect_has_a_bounded_deadline(self):
        self.ready();now=(self.c.anchor-ticks(1000))&MASK
        self.c.call('bc_disconnect',CONTEXT,now)
        self.c.poll((now+ticks(120000))&MASK)
        self.assertEqual(self.c.state(),0);self.assertEqual(self.c.radio_action()['kind'],4)


if __name__=="__main__":
    result=unittest.TextTestRunner(verbosity=2).run(unittest.defaultTestLoader.loadTestsFromTestCase(CentralTests))
    report={"tests_run":result.testsRun,"passed":result.wasSuccessful(),"context_bytes":Central().call("bc_context_size"),
            "elf_sha256":hashlib.sha256(ELF.read_bytes()).hexdigest(),
            "source_sha256":hashlib.sha256((ROOT/"ble_central.c").read_bytes()).hexdigest(),
            "scope":"Central+ATT ARM instructions with protocol peer model; no physical RF/timing validation or installed firmware"}
    (ROOT/"central-arm-test-results.json").write_text(json.dumps(report,indent=2)+"\n")
    raise SystemExit(0 if result.wasSuccessful() else 1)
