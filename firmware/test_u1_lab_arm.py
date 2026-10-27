"""Run patched V32 hooks, integration, central and MMIO port in Unicorn.

Radio/timer/RSSI and legacy poll/restore are modeled. This cannot establish
physical A8107 launch semantics, actual RF timing, IRQ behavior or OTA recovery.
"""
from pathlib import Path
import hashlib,json,struct,unittest
from unicorn import UC_HOOK_CODE,UC_HOOK_MEM_READ,UC_HOOK_MEM_WRITE
from unicorn.arm_const import UC_ARM_REG_R0,UC_ARM_REG_R1,UC_ARM_REG_R2,UC_ARM_REG_R3,UC_ARM_REG_R6,UC_ARM_REG_PC,UC_ARM_REG_LR,UC_ARM_REG_SP,UC_ARM_REG_CPSR
from test_client_arm import ArmClient,INPUT,OUTPUT,STACK,STOP,load_elf
from test_central_arm import OWN,PEER,Peer,ticks

ROOT=Path(__file__).resolve().parent
ELF=ROOT/"u1-lab-module.elf"
IMAGE=ROOT/"u1-v32-lab-offline.bin"


class Machine(ArmClient):
    def __init__(self):
        super().__init__(ELF,"u1_runtime_init")
        self.machine.mem_write(0,IMAGE.read_bytes())
        for address in (0x40000000,0x50000000):self.machine.mem_map(address,0x10000)
        self.machine.mem_map(0x40010000,0x10000);self.machine.mem_map(0xE000E000,0x1000)
        self.call_stack=self.symbols['__ss_stack_top'];self.lowest_sp=self.call_stack
        self.clock=0;self.timer_start=0;self.timer_running=False;self.irq=0xFFFFFFFF;self.irq_pending=0
        self.timer_value=0;self.timer_reload=0;self.timer_frozen=False;self.timer_requires_irq_enable=False
        self.rf_state="idle";self.packet=None;self.response=None;self.tx_end=None
        self.ad_end=None;self.rssi_index=0;self.constant_rssi=False;self.transmit_works=True
        self.peer=Peer();self.expected_sn=0;self.peer_sn=0;self.peer_pending=None
        self.tx=[];self.restore_calls=0;self.key_calls=[];self.poll_calls=[];self.poll_stack=[]
        self.stub_poll=True;self.stub_key=True;self.stub_restore=True
        self.disconnect_delay=0
        self.stub_echo_log=False
        self.require_ble_rx_engine=False;self.rx_launch_controls=[]
        self.header_triggers_tx=False;self.fifo_last_byte_triggers_tx=False;self.rf_writes=[];self.raw_tx_frames=[]
        self.raw_tx_modes=[];self.raw_tx_delays=[];self.tx_duration_extra=0
        self.tx_fifo_written=set()
        self.wtr_tail=0;self.tx_start_delay=0
        self.codec_delay=0;self.skip_scan_channel37=False
        self.connect_build_delay=0;self.connect_build_times=[]
        self.fifo_write_times=[];self.advertisements=[]
        self.rf_flags=0;self.bad_scan_packets=0;self.stalled_scan_packets=0;self.stall_current_tail=False
        self.original_key_fixture=False;self.original_restore_fixture=False
        self.stock_key_dispatch_fixture=False;self.stub_screen=True;self.screen_calls=0;self.screen_lines=[]
        self.battery_level=85;self.battery_reads=0;self.separator_clears=0
        self.stub_ascii=True;self.screen_times=[];self.screen_delay=0
        self.native_sleep_fixture=False;self.sleep_entries=0
        self.machine.hook_add(UC_HOOK_CODE,self.code)
        self.machine.hook_add(UC_HOOK_MEM_READ,self.read)
        self.machine.hook_add(UC_HOOK_MEM_WRITE,self.write)
        # Requested dedicated profile2; native profile table layout recovered
        # from11ec8, not an app/GATT compatibility assumption.
        self.put8(0x20000105,1);self.put8(0x20001435,2);self.put8(0x20000103,2)
        self.put8(0x20000102,1);self.put8(0x20001437,1);self.put8(0x2000143f,1)
        self.machine.mem_write(0x20001443,b'SoundSend\0')
        # Diagnostic tests explicitly use the original native-mode baseline.
        # The daily-boot tests below execute init again and exercise its real
        # enabled/mode10/RX2/lead72 defaults without these fixture overrides.
        self.debug(3,0);self.debug(4,1);self.debug(11,0)

    def u32(self,a):return struct.unpack("<I",self.machine.mem_read(a,4))[0]
    def put32(self,a,v):self.machine.mem_write(a,struct.pack("<I",v&0xFFFFFFFF))
    def put8(self,a,v):self.machine.mem_write(a,bytes([v&255]))
    def ret(self,value=0):
        self.machine.reg_write(UC_ARM_REG_R0,value)
        self.machine.reg_write(UC_ARM_REG_PC,self.machine.reg_read(UC_ARM_REG_LR))
    def code(self,uc,pc,size,data):
        self.clock+=1
        if self.native_sleep_fixture:
            if pc==0x10A4:self.ret();return
            if pc==0x123C:self.sleep_entries+=1;uc.emu_stop();return
        if pc==self.symbols['ble_encode_packet']&~1:self.clock+=self.codec_delay
        if pc==self.symbols['bc_connect_payload']&~1:
            self.connect_build_times.append(self.clock);self.clock+=self.connect_build_delay
        self.lowest_sp=min(self.lowest_sp,uc.reg_read(UC_ARM_REG_SP))
        if pc==self.symbols.get('u1_original_screen',-1)&~1 and self.stub_screen:
            self.screen_calls+=1;self.ret();return
        if pc==0x136A8:self.battery_reads+=1;self.ret(self.battery_level);return
        if pc==0x124D4:self.separator_clears+=1;self.ret();return
        if pc==0x11DC8:
            p=uc.reg_read(UC_ARM_REG_R2);value=bytes(uc.mem_read(p,17)).split(b'\0')[0].decode('ascii')
            self.screen_lines.append((uc.reg_read(UC_ARM_REG_R0),uc.reg_read(UC_ARM_REG_R1),value))
            self.screen_times.append((self.clock,self.restore_calls,self.rf_state))
            if self.stub_ascii:self.clock+=self.screen_delay;self.ret();return
        if pc==self.symbols['u1_original_poll']&~1 and self.stub_poll:
            self.poll_calls.append(tuple(uc.reg_read(x) for x in (UC_ARM_REG_R0,UC_ARM_REG_R1,UC_ARM_REG_R2,UC_ARM_REG_R3)))
            self.poll_stack.append(uc.reg_read(UC_ARM_REG_SP))
            if self.u32(0x20000334)&1:
                if self.disconnect_delay:self.disconnect_delay-=1
                else:self.put8(0x20000270,0);self.put32(0x20000334,self.u32(0x20000334)&~1)
            self.ret(0x12345678)
        elif pc==self.symbols['u1_original_key']&~1 and self.stub_key:
            self.key_calls.append((uc.reg_read(UC_ARM_REG_R0),uc.reg_read(UC_ARM_REG_R1)));self.ret(7)
        elif pc==0x8720 and self.stub_restore:
            self.restore_calls+=1;self.put8(0x20000270,1);self.put8(0x20000163,0)
            # Stock8720 uses interval128, then10a4 stores128*4+128=640.
            uc.mem_write(0x20002CEC,struct.pack('<BBHHBBBB',7,0,640,10000,0,1,1,0));self.ret()
        elif self.original_restore_fixture and pc==0x1B18:self.ret()
        elif self.original_restore_fixture and pc==0x10A4:
            self.put8(0x20000270,1);self.ret()
        elif self.original_key_fixture:
            if pc==0x73C8:self.ret()
            elif pc==0x748C:self.ret(0xEF4014)
            elif pc==0x7430:
                n=uc.reg_read(UC_ARM_REG_R2)
                if n!=4:raise AssertionError(n)
                uc.mem_write(uc.reg_read(UC_ARM_REG_R1),bytes.fromhex('fe 0c 00 e9'));self.ret()
            elif pc==0x11D04:self.ret(3)
        if self.stub_echo_log and pc==0x9904:self.ret()
    def timer_current(self):
        elapsed=self.clock-self.timer_start if self.timer_running and not self.timer_frozen else 0
        if elapsed<=self.timer_value:return self.timer_value-elapsed
        return self.timer_reload-((elapsed-self.timer_value-1)%(self.timer_reload+1))
    def read(self,uc,access,address,size,value,data):
        if address==0x40001004:
            self.put32(address,self.timer_current())
        elif address==0xE000E100:self.put32(address,self.irq)
        elif address==0x50001244:
            pattern=(0,1,1,0);sample=0 if self.constant_rssi else pattern[self.rssi_index&3]
            self.rssi_index+=1;self.put32(address,sample)
        elif address==0x50001010:
            status=self.rf_flags
            if self.rf_state=='tx':
                if self.clock<self.tx_end:status=0x83
                else:self.rf_state='idle'
            if self.rf_state=='idle' and self.tx_end is not None and self.clock<self.tx_end+self.wtr_tail:
                status|=0x80
            if self.rf_state=='rx':
                status=0x81|self.rf_flags
                if self.packet:
                    start,end,header,payload=self.packet
                    if self.clock>=start+ticks(40):status|=0x100
                    if self.clock>=end:
                        if self.stall_current_tail:
                            status=0x181
                        else:
                            self.put32(0x50001304,header|(len(payload)<<8));uc.mem_write(0x50001500,payload)
                            self.packet=None;self.rf_flags=0x1400
                            if self.bad_scan_packets and self.u32(0x50001018)==0xD6BE898E:
                                self.bad_scan_packets-=1;self.rf_flags|=0x20;status=0x81|self.rf_flags
                            else:self.rf_state='idle';status=self.rf_flags
            self.put32(address,status)
    def write(self,uc,access,address,size,value,data):
        if address in (0x50001004,0x50001300,0x50001304) or 0x50001400<=address<0x50001500:self.rf_writes.append((address,value))
        if 0x50001400<=address<0x50001500:
            self.fifo_write_times.append((self.clock,address));self.tx_fifo_written.add(address-0x50001400)
        if address==0x50001000 and value&0x40:self.tx_fifo_written.clear()
        if address==0x40001000:
            self.timer_value=self.timer_current();self.timer_start=self.clock
            self.timer_running=bool(value&1 and (not self.timer_requires_irq_enable or value&8))
        elif address==0x40001004:
            self.timer_value=value;self.timer_start=self.clock
        elif address==0x40001008:
            # Vendor13.5.1: RELOAD is static; writing it also loads VALUE.
            self.timer_reload=value;self.timer_value=value;self.timer_start=self.clock
            self.put32(0x40001004,value)
        elif address==0xE000E180:self.irq&=~value
        elif address==0xE000E100:self.irq|=value
        elif address==0xE000E280:self.irq_pending&=~value
        elif address==0x50001010:
            # Updated from physical LAB12 comparison: zero left stale EOP,
            # write1400 accepted a fresh peer. Public EOP prose is ambiguous.
            self.rf_flags&=~(value&0x1400)
        elif address==0x50001304 and self.header_triggers_tx and self.u32(0x50001300)&2:
            self.transmit(uc,value)
        elif address==0x50001304 and self.fifo_last_byte_triggers_tx and not value&0x3F00 and self.u32(0x50001300)&2:
            self.transmit(uc,value)
        elif 0x50001400<=address<0x50001500 and self.fifo_last_byte_triggers_tx and self.u32(0x50001300)&2:
            n=(self.u32(0x50001304)>>8)&63
            if n and address==0x50001400+n-1:
                uc.mem_write(address,bytes([value&255]));self.transmit(uc,self.u32(0x50001304))
        elif address==0x50001004:
            if value in (0xA0,0xB0):self.rf_state='idle';self.packet=None
            elif value==0xC0:
                self.rf_state='rx';self.rf_flags&=0x1000;self.stall_current_tail=False
                control=self.u32(0x50001300);self.rx_launch_controls.append(control)
                if self.require_ble_rx_engine and self.u32(0x50001240)!=0x2F and not control&2:
                    raise AssertionError('Modeled receiver requires the stock BLECTRL bit1 before RX')
                if self.response:
                    self.packet=self.response;self.response=None
                elif (self.u32(0x50001018)==0xD6BE898E and self.u32(0x50001240)!=0x2F and
                      not (self.skip_scan_channel37 and self.u32(0x50001308)==37)):
                    payload=PEER+b'\x09\x09SoundSend'
                    start=self.clock+ticks(200);end=start+ticks((10+len(payload))*8)
                    self.packet=(start,end,0,payload);self.ad_end=end
                    if self.stalled_scan_packets:
                        self.stalled_scan_packets-=1;self.stall_current_tail=True
            elif value==0xD0 and self.transmit_works:
                if self.u32(0x50001300)==0 and self.u32(0x50001008)&2:
                    self.transmit_raw(uc)
                else:self.transmit(uc,self.u32(0x50001304))

    def transmit_raw(self,uc):
        # Independent slow bit-oriented decode, not the C encoder/table code.
        count=(self.u32(0x50001020)&255)+1
        if not set(range(count))<=self.tx_fifo_written:
            raise AssertionError('TX FIFO write pointer was reset after preloading its bytes')
        raw=bytes(uc.mem_read(0x50001400,count));self.raw_tx_frames.append(raw)
        s=self.u32(0x50001308)|64;plain=bytearray(raw)
        for i in range(count):
            for bit in range(8):
                white=s&1;s=(s>>1)^(0x44 if white else 0)
                plain[i]^=white<<bit
        if len(plain)!=plain[1]+5:raise AssertionError(('Raw FIFO length mismatch',plain.hex()))
        crc=self.u32(0x50001310)&0xFFFFFF
        for b in plain[:-3]:
            for bit in range(8):
                feedback=((crc>>23)^(b>>bit))&1;crc=(crc<<1)&0xFFFFFF
                if feedback:crc^=0x65B
        expected=bytes(int(f'{(crc>>shift)&255:08b}'[::-1],2) for shift in (16,8,0))
        if plain[-3:]!=expected:raise AssertionError(('Raw CRC mismatch',plain.hex(),expected.hex()))
        if self.u32(0x50001180)!=0x01280000:raise AssertionError('Double CRC/whitening in raw mode')
        mode=self.u32(0x50001008)&0x706;self.raw_tx_modes.append(mode)
        self.raw_tx_delays.append(self.u32(0x500011c0))
        if mode!=0x502:raise AssertionError('Keep the physically decoded mode7 bit order')
        self.transmit(uc,plain[0]|(plain[1]<<8),bytes(plain[2:-3]))

    def transmit(self,uc,full_header,payload=None):
        # The header-trigger case is an explicitly hypothetical launch model.
        # Original instruction ordering is checked separately below; only the
        # physical U1 can establish which write actually starts its RF packet.
        if not self.transmit_works:return
        header=full_header&255;n=(full_header>>8)&63
        if payload is None:payload=bytes(uc.mem_read(0x50001400,n))
        at=self.clock+(self.tx_start_delay if self.u32(0x50001300)==0 else 0)
        self.tx.append(dict(at=at,header=header,payload=payload,channel=self.u32(0x50001308)))
        self.rf_state='tx';self.tx_end=at+ticks((10+n)*8)+self.tx_duration_extra
        if self.u32(0x50001018)==0xD6BE898E and header&15==2:
            if payload[:6]!=OWN or b'\x09SSLAB-RFTEST' not in payload:raise AssertionError('Invalid lab beacon identity/name')
            self.advertisements.append(self.tx[-1]);return
        if header&15==5 and n==34:
            if self.ad_end is None or abs(at-self.ad_end-ticks(150))>ticks(2):
                raise AssertionError(('Modeled initiating IFS missed',at-self.ad_end if self.ad_end else None))
            if payload[:12]!=OWN+PEER:raise AssertionError(payload.hex())
            self.expected_sn=0;self.peer_sn=0;self.peer_pending=None
        else:
            if self.peer_pending is not None and ((header>>2)&1)==(self.peer_sn^1):
                self.peer_sn^=1;self.peer_pending=None
            sn=(header>>3)&1
            if sn==self.expected_sn:
                self.expected_sn^=1
                reply=self.peer.reply(dict(header=header,payload=payload))
                if self.peer_pending is None:self.peer_pending=reply
            reply=self.peer_pending or b''
            reply_header=(2 if reply else 1)|(self.expected_sn<<2)|(self.peer_sn<<3)
            start=self.tx_end+ticks(150);end=start+ticks((10+len(reply))*8)
            self.response=(start,end,reply_header,reply)

    def debug(self,command,argument=0):
        p=bytearray(20);p[:5]=bytes([0x53,0x53,1,command,argument]);p[19]=sum(p[:19])&255
        self.machine.mem_write(INPUT,bytes(p))
        if self.call('u1_debug_command',INPUT,OUTPUT)!=1:raise AssertionError('Debug command rejected')
        return bytes(self.machine.mem_read(OUTPUT,20))
    def poll(self):return self.call('u1_poll_gateway',1,2,3,4)
    def key(self,profile,button):
        # One explicit Python key call means one fresh physical press.
        self.put8(0x20000136,button);self.put8(0x20000135,0)
        result=self.call('u1_key_gateway',profile,button);self.put8(0x20000135,button)
        return result
    def echo_fragment(self,packet):
        # Execute the original main-loop FFF4 dispatch after its shared write
        # flag has already been consumed. No poll-entry observer can see it.
        self.stub_echo_log=True
        self.put8(0x20000270,3);self.put8(0x200000AB,1);self.put8(0x2000013E,1)
        self.machine.mem_write(0x200002B6,struct.pack('<H',97))
        self.machine.mem_write(0x2000141E,packet)
        self.machine.mem_write(0x200002B0,struct.pack('<H',4));self.put32(0x2000035C,0x2000141E)
        self.symbols['stock_get_write']=0x130D;self.call('stock_get_write')
        fragment_stack=self.call_stack-0x80
        self.put32(fragment_stack+0x34,len(packet));self.put32(fragment_stack+0x38,0x2000141E)
        self.machine.reg_write(UC_ARM_REG_SP,fragment_stack)
        self.machine.emu_start(0x10819,0x1086A,count=10000)
        if self.machine.reg_read(UC_ARM_REG_PC)!=0x1086A:raise AssertionError('Original echo fragment did not finish')
        return bytes(self.machine.mem_read(0x200032AE,27))
    def run_until_idle(self,limit=5000):
        entered=False
        for _ in range(limit):
            self.poll();self.clock+=ticks(250)
            phase=self.debug(1)[5]
            if phase:entered=True
            if (entered or self.restore_calls) and not phase:return self.debug(1)
        raise AssertionError(('Runtime did not become idle',self.debug(1).hex(),self.tx[-3:]))

    def native_idle_tick(self):
        # The real LPTIMER1 interrupt is gated by its peripheral and NVIC.
        # Its original ARM handler rearms the peripheral and increments C4.
        if not (self.u32(0x50006014)&1 and self.irq&(1<<24)):return False
        self.irq_pending&=~(1<<24)
        self.symbols['stock_idle_tick']=0x7285
        self.call('stock_idle_tick')
        return True

    def native_sleep_entry(self):
        # Execute the original inactivity decision and GPIO/timer shutdown.
        # Stop at the low-power service entry; no physical sleep is modeled.
        self.native_sleep_fixture=True
        self.put32(0x40010000,0xffffffff)
        self.machine.reg_write(UC_ARM_REG_SP,self.call_stack)
        self.machine.reg_write(UC_ARM_REG_LR,STOP|1)
        self.machine.emu_start(0x12A61,STOP,count=300000)
        self.native_sleep_fixture=False


class IntegrationTests(unittest.TestCase):
    def setUp(self):self.m=Machine()
    def test_inactivity_timer_and_pending_tick_survive_radio_ownership(self):
        m=self.m;m.put32(0x50006010,0x333);m.put32(0x50006014,0xe7)
        m.debug(2);m.poll()
        self.assertEqual(m.u32(0x50006014),0xe7,'Radio acquisition must not stop the unrelated inactivity timer')
        self.assertFalse(m.irq&(1<<24),'Keep its ISR out of timed radio windows')
        m.irq_pending|=1<<24
        m.run_until_idle()
        self.assertTrue(m.irq_pending&(1<<24),'Do not discard a pending inactivity tick on release')
        before=m.u32(0x200000c4)
        self.assertTrue(m.native_idle_tick())
        self.assertEqual(m.u32(0x200000c4),before+1)
    def test_native_timeout_reaches_sleep_after_volume_and_profile_exit(self):
        for profile in (2,1):
            with self.subTest(profile=profile):
                m=Machine();m.put32(0x50006014,0xe7);m.put32(0x200000c8,10)
                m.put8(0x200000ba,10);m.debug(3,1);m.key(2,4);m.run_until_idle()
                m.put8(0x20000103,profile)
                for _ in range(10):self.assertTrue(m.native_idle_tick())
                self.assertEqual(m.u32(0x200000c4),10)
                m.native_sleep_entry()
                self.assertEqual(m.sleep_entries,1,'Original sleep path must remain reachable on both profiles')
                self.assertEqual(m.u32(0x50006014)&1,0,'Native sleep stops the inactivity timer itself')
    def test_inactivity_timer_survives_failed_and_cancelled_radio_operations(self):
        for failure in ('entropy','cancel','tx'):
            with self.subTest(failure=failure):
                m=Machine();m.put32(0x50006014,0xe7)
                if failure=='entropy':m.constant_rssi=True
                if failure=='tx':m.transmit_works=False
                m.debug(2);m.call('u1_poll_gateway',1,2,3,4,instruction_limit=2500000)
                if failure=='cancel':m.call('u1_key_gateway',1,4)
                else:m.run_until_idle()
                self.assertTrue(m.native_idle_tick())
                self.assertEqual(m.u32(0x200000c4),1)
    def test_preexisting_disabled_inactivity_timer_is_not_force_enabled(self):
        m=self.m;m.irq&=~(1<<24);m.put32(0x50006014,0)
        m.debug(2);m.run_until_idle()
        self.assertEqual(m.u32(0x50006014),0);self.assertFalse(m.irq&(1<<24))
    def test_power_diagnostic_reads_native_state_without_changing_it(self):
        import sys
        sys.path.insert(0,str(ROOT.parent))
        from u1_debug import decode_response
        m=self.m;m.put32(0x200000c4,7);m.put32(0x200000c8,10)
        m.put32(0x50006014,0xe7);m.put8(0x20000162,3)
        before=bytes(m.machine.mem_read(0x200000c4,8))
        state=decode_response(m.debug(14),14)
        self.assertEqual((state['idle_ticks'],state['idle_timeout_ticks'],state['screen_mode']),(7,10,3))
        self.assertTrue(state['inactivity_timer_running']);self.assertEqual(state['lab_protocol_revision'],19)
        self.assertEqual(bytes(m.machine.mem_read(0x200000c4,8)),before)
        self.assertEqual(m.u32(0x50006014),0xe7)
    def test_held_volume_repeats_at_bounded_rate_and_stops_on_release(self):
        for button,start in ((4,18),(10,30)):
            with self.subTest(button=button):
                m=Machine();m.peer.volume=start;m.debug(3,1)
                m.symbols['stock_dispatch']=0x9C55;m.put8(0x20000162,3)
                m.put32(0x20000108,1<<(button-1));m.put32(0x2000010c,0)
                began=m.clock
                while m.clock-began<ticks(1200000):
                    m.call('stock_dispatch');m.poll();m.clock+=ticks(250)
                self.assertGreaterEqual(len(m.peer.writes),4)
                self.assertLessEqual(len(m.peer.writes),9,'No catch-up burst or callback-rate repeats')
                direction=1 if button==4 else -1
                self.assertEqual(m.peer.writes,list(range(start+direction,m.peer.writes[-1]+direction,direction)))
                m.put32(0x20000108,0);m.call('stock_dispatch');m.run_until_idle(limit=5000)
                count=len(m.peer.writes)
                for _ in range(100):m.call('stock_dispatch');m.poll();m.clock+=ticks(1000)
                self.assertEqual(len(m.peer.writes),count)
                self.assertEqual(m.restore_calls,1,'A held key should reuse one connection')
    def test_held_volume_does_not_queue_repeats_before_volume_is_known(self):
        m=self.m;m.debug(3,1);m.key(2,4)
        for _ in range(20):
            m.clock+=ticks(200000);m.call('u1_key_gateway',2,4)
        s=m.run_until_idle(limit=5000)
        self.assertEqual(s[8],19);self.assertEqual(m.peer.writes,[19])
    def test_long_hold_keeps_mute_edge_only_and_volume_within_bounds(self):
        for button,volume,expected in ((13,18,0),(4,99,100),(10,1,0)):
            with self.subTest(button=button):
                m=Machine();m.peer.volume=volume;m.debug(3,1);m.key(2,button);began=m.clock
                while m.clock-began<ticks(1200000):
                    m.call('u1_key_gateway',2,button);m.poll();m.clock+=ticks(250)
                m.put8(0x20000136,0);m.put8(0x20000135,0);m.run_until_idle()
                self.assertEqual(m.peer.writes,[expected])
                self.assertEqual(m.restore_calls,1)
    def test_successive_press_after_600ms_reuses_live_connection(self):
        m=self.m;m.debug(3,1);m.key(2,4);began=m.clock
        while m.clock-began<ticks(600000):m.poll();m.clock+=ticks(250)
        self.assertEqual(m.restore_calls,0)
        self.assertEqual(m.debug(1)[5],2)
        m.key(2,4);m.run_until_idle(limit=5000)
        self.assertEqual(m.peer.writes,[19,20]);self.assertEqual(m.restore_calls,1)
    def delay_first_peer_response(self,m,volume_write=False):
        original_reply=m.peer.reply;pending=[];release_at=[0];delayed=[False]
        def slow_response(action):
            if pending and m.clock>=release_at[0]:return pending.pop()
            response=original_reply(action)
            if response and not delayed[0] and (not volume_write or m.peer.writes):
                delayed[0]=True;pending.append(response);release_at[0]=m.clock+ticks(900000);return b''
            return response
        m.peer.reply=slow_response
    def test_held_volume_survives_discovery_longer_than_warm_timeout(self):
        for volume_write in (False,True):
            with self.subTest(delayed_write_confirmation=volume_write):
                m=Machine();self.delay_first_peer_response(m,volume_write);m.debug(3,1)
                m.symbols['stock_dispatch']=0x9C55;m.put8(0x20000162,3)
                m.put32(0x20000108,1<<3);m.put32(0x2000010c,0);began=m.clock
                while m.clock-began<ticks(2100000):
                    m.call('stock_dispatch');m.poll();m.clock+=ticks(250)
                self.assertGreaterEqual(len(m.peer.writes),4,'A delayed first confirmation must not close before held-key service')
                self.assertEqual(m.peer.writes,list(range(19,m.peer.writes[-1]+1)))
                m.put32(0x20000108,0);m.call('stock_dispatch');m.run_until_idle()
                self.assertEqual(m.restore_calls,1)
    def test_warm_window_starts_after_slow_confirmation(self):
        m=self.m;self.delay_first_peer_response(m);m.debug(3,1);m.key(2,4)
        for _ in range(8000):
            m.poll();m.clock+=ticks(250)
            if m.debug(1)[8]==19:break
        else:self.fail('No initial confirmation')
        confirmed=m.clock
        while m.clock-confirmed<ticks(600000):m.poll();m.clock+=ticks(250)
        self.assertEqual(m.restore_calls,0,'Keep the warm window after a slow successful operation')
        m.key(2,4);m.run_until_idle()
        self.assertEqual(m.peer.writes,[19,20]);self.assertEqual(m.restore_calls,1)
    def test_daily_boot_enables_dedicated_keys_with_verified_radio_defaults(self):
        m=self.m;m.call('u1_runtime_init');s=m.debug(1)
        self.assertEqual(s[4:6],b'\1\0');self.assertEqual(s[8:10],b'\xff\x0a')
        self.assertEqual(m.debug(7)[8],2);self.assertEqual(m.debug(7)[14],72)
        m.tx_start_delay=ticks(72);m.key(2,13);s=m.run_until_idle()
        self.assertEqual(s[7],0);self.assertEqual(s[8],0);self.assertEqual(m.peer.writes,[0])
    def test_original_stock_initialization_preserves_recorded_unit_mac_and_name(self):
        m=self.m;m.stub_restore=False;m.original_restore_fixture=True
        m.symbols['stock_advertise']=0x8721;m.call('stock_advertise')
        self.assertEqual(bytes(m.machine.mem_read(0x20000175,6)),bytes.fromhex('20 21 03 00 2e ab'))
        self.assertIn(b'SB202103002EAB',bytes(m.machine.mem_read(0x20000000,0x39D8)))
    def test_post_scatter_boot_hook_initializes_lab_on_first_ota_boot(self):
        m=self.m
        start=m.symbols['__ss_ram_start'];end=m.symbols['__ss_ram_end']
        m.machine.mem_write(start,b'\xa5'*(end-start))
        m.machine.reg_write(UC_ARM_REG_SP,0x200039D8)
        m.machine.emu_start(0x133,0xC1A8,count=10000)
        self.assertEqual(m.machine.reg_read(UC_ARM_REG_PC),0xC1A8)
        self.assertEqual(m.machine.reg_read(UC_ARM_REG_SP),m.symbols['__ss_stack_top'])
        self.assertLessEqual(m.symbols['__ss_ram_end'],m.symbols['__ss_stack_limit'])
        self.assertLess(m.symbols['__ss_stack_top'],0x20004000,'PM1/PM2 turn off upper LCD SRAM')
        self.assertEqual(m.debug(1)[4:6],b'\1\0')
        self.assertEqual(m.debug(1)[8:10],b'\xff\x0a')
        self.assertEqual(m.u32(4),0x181)
    def test_original_main_loop_skips_legacy_radio_paths_while_owned(self):
        m=self.m;m.put8(0x20000270,4)
        m.machine.reg_write(UC_ARM_REG_SP,STACK)
        m.machine.emu_start(0xCE71,0xE084,count=100)
        self.assertEqual(m.machine.reg_read(UC_ARM_REG_PC),0xE084)
        self.assertEqual(m.restore_calls,0)
    def test_patched_key_entry_preserves_legacy_behavior_when_disabled(self):
        self.m.symbols['patched_key']=0x10B59
        self.assertEqual(self.m.call('patched_key',2,4),7);self.assertEqual(self.m.key_calls,[(2,4)])
    def test_patched_poll_entry_preserves_all_four_arguments(self):
        self.m.symbols['patched_poll']=0x41AD
        self.assertEqual(self.m.call('patched_poll',0xA1,0xB2,0xC3,0xD4),0x12345678)
        self.assertEqual(self.m.poll_calls[-1],(0xA1,0xB2,0xC3,0xD4))
        self.assertEqual(self.m.poll_stack[-1],self.m.call_stack,'Stock OTA poll must not retain an extra gateway frame')
    def test_poll_trampoline_relocates_literals_and_preserves_cmp_flags(self):
        m=self.m;m.stub_poll=False
        for state in (0,3):
            m.put8(0x20000270,state)
            m.machine.reg_write(UC_ARM_REG_R3,0x1234ABCD);m.machine.reg_write(UC_ARM_REG_SP,STACK)
            m.machine.reg_write(UC_ARM_REG_LR,STOP|1)
            m.machine.emu_start(m.symbols['u1_original_poll']|1,0x41BC,count=1000)
            self.assertEqual(m.machine.reg_read(UC_ARM_REG_PC),0x41BC)
            self.assertEqual(m.machine.reg_read(UC_ARM_REG_SP),STACK-40)
            self.assertEqual(m.machine.reg_read(UC_ARM_REG_R3),0x1234ABCD)
            self.assertEqual((m.machine.reg_read(UC_ARM_REG_CPSR)>>30)&1,int(state==3))
    def test_original_hid_key_path_still_executes_after_patch(self):
        m=self.m;m.stub_key=False;m.original_key_fixture=True
        for address,value in {0x20000270:3,0x20000163:2,0x20000167:1,0x2000013E:1,0x200000A6:1}.items():m.put8(address,value)
        m.machine.mem_write(0x200002B6,struct.pack('<H',97))
        m.symbols['patched_key']=0x10B59
        self.assertEqual(m.call('patched_key',1,4),0)
        self.assertEqual(bytes(m.machine.mem_read(0x200032AE,9)),bytes.fromhex('05 00 04 00 1b 47 00 e9 00'))
    def test_configuration_connection_bypasses_volume_override(self):
        m=self.m;m.debug(3,1);m.put8(0x20000270,3);m.put8(0x20000163,0)
        self.assertEqual(m.key(2,4),7);self.assertEqual(m.key_calls,[(2,4)])
    def test_advertising_stop_inverts_stock_interval_storage_transform(self):
        m=self.m;m.debug(3,1);m.put8(0x20000270,1)
        m.machine.mem_write(0x20002CEC,struct.pack('<BBHHBBBB',7,0,7200*4+128,10000,0,1,1,0))
        m.key(2,4);s=m.run_until_idle()
        self.assertEqual(s[7],0,s.hex());self.assertEqual(m.peer.writes,[19])
    def test_button_to_radio_to_volume_with_mmio_model(self):
        m=self.m;m.debug(3,1)
        self.assertEqual(m.key(2,4),0)
        s=m.run_until_idle()
        self.assertEqual(s[7],0,s.hex());self.assertEqual(s[8],19)
        self.assertEqual(m.peer.writes,[19]);self.assertGreaterEqual(len(m.tx),7)
        self.assertEqual(m.restore_calls,1);self.assertEqual(m.key_calls,[])
        self.assertGreaterEqual(m.lowest_sp,m.symbols['__ss_stack_limit'],'Central call chain overlaps retained patch state')
        self.assertEqual(m.tx[-1]['header']&3,3)
        self.assertEqual(m.tx[-1]['payload'],bytes.fromhex('02 13'))
    def test_probe_reads_volume_without_any_volume_write(self):
        m=self.m;m.debug(2);s=m.run_until_idle()
        self.assertEqual(s[7],0,s.hex());self.assertEqual(s[8],18);self.assertEqual(m.peer.writes,[])
    def test_completed_probe_or_single_key_does_not_reopen_without_a_new_request(self):
        for key in (None,10):
            with self.subTest(key=key):
                m=Machine();m.debug(4,10);m.debug(11,72);m.tx_start_delay=ticks(72)
                if key is None:m.debug(2)
                else:m.debug(3,1);m.key(2,key)
                state=m.run_until_idle();self.assertEqual(state[7],0)
                count=len(m.tx);restores=m.restore_calls
                for _ in range(100):m.poll();m.clock+=ticks(250)
                self.assertEqual(len(m.tx),count,'Original request must not survive normal termination')
                self.assertEqual(m.restore_calls,restores)
                self.assertEqual(m.peer.writes,[] if key is None else [17])
    def test_executed_stock_held_key_dispatch_counts_one_step_until_release(self):
        m=self.m;m.stock_key_dispatch_fixture=True;m.symbols['stock_dispatch']=0x9C55
        m.put8(0x20000162,3);m.debug(4,10);m.debug(11,72);m.tx_start_delay=ticks(72);m.debug(3,1)
        for button,expected in ((4,19),(4,20),(10,19),(10,18)):
            m.put32(0x20000108,1<<(button-1));m.put32(0x2000010c,0)
            for _ in range(50):m.call('stock_dispatch')
            self.assertEqual(m.machine.mem_read(0x20000135,1)[0],button)
            state=m.run_until_idle();self.assertEqual(state[8],expected);self.assertEqual(state[7],0)
            count=len(m.tx)
            for _ in range(10):m.call('stock_dispatch');m.poll()
            self.assertEqual(len(m.tx),count,'Holding must not generate a second volume operation')
            m.put32(0x20000108,0);m.call('stock_dispatch')
            self.assertEqual(m.machine.mem_read(0x20000135,1)[0],0)
        self.assertEqual(m.peer.writes,[19,20,19,18])
    def test_volume_override_is_limited_to_dedicated_profile_and_physical_edge(self):
        m=self.m;m.debug(3,1)
        self.assertEqual(m.key(1,4),7);self.assertEqual(m.key_calls,[(1,4)])
        m.put8(0x20000136,0);m.put8(0x20000135,0)
        self.assertEqual(m.call('u1_key_gateway',2,4),0);m.poll();self.assertEqual(m.tx,[])
    def test_oled_uses_confirmed_volume_and_preserves_other_profile_or_configuration_screens(self):
        m=self.m;m.call('u1_screen_gateway')
        self.assertIn('---',m.screen_lines[-2][2]);self.assertEqual([p for x,p,s in m.screen_lines],[0,3,6])
        m.debug(2);state=m.run_until_idle();self.assertEqual(state[8],18)
        self.assertIn('18 %',m.screen_lines[-2][2]);self.assertNotIn('19',m.screen_lines[-2][2])
        count=len(m.screen_lines);m.put8(0x20000103,1);m.call('u1_screen_gateway')
        self.assertEqual(len(m.screen_lines),count)
        m.put8(0x20000103,2);m.put8(0x20000270,3);m.put8(0x20000163,0);m.call('u1_screen_gateway')
        self.assertEqual(len(m.screen_lines),count);self.assertEqual(m.screen_calls,2)
    def test_screen_trampoline_replays_original_prologue_before_native_literals(self):
        m=self.m;m.stub_screen=False;m.machine.reg_write(UC_ARM_REG_SP,m.call_stack)
        m.machine.emu_start(m.symbols['u1_original_screen']|1,0x11ED0,count=100)
        self.assertEqual(m.machine.reg_read(UC_ARM_REG_PC),0x11ED0)
        self.assertEqual(m.machine.reg_read(UC_ARM_REG_SP),m.call_stack-24)
        self.assertEqual(m.u32(m.call_stack-24),0)
    def test_battery_percentage_uses_native_measurement(self):
        m=self.m
        for level in (85,70,100):
            m.battery_level=level;m.call('u1_screen_gateway')
            self.assertIn(f'{level} %',m.screen_lines[-1][2]);self.assertEqual(m.screen_calls,0)
        self.assertEqual(m.battery_reads,3)
    def test_confirmed_digits_are_drawn_before_radio_disconnect(self):
        m=self.m;m.call('u1_screen_gateway');m.debug(3,1);m.key(2,4)
        # Deliberately charge each partial draw5ms. Each redraw checks its own
        # guarded12ms interval without breaking the30ms BLE event schedule.
        m.screen_delay=ticks(5000)
        state=m.run_until_idle();self.assertEqual(state[7],0);self.assertEqual(m.peer.writes,[19])
        partial=[(line,time) for line,time in zip(m.screen_lines,m.screen_times) if line==(72,3,' 19')]
        self.assertTrue(partial);self.assertEqual(partial[0][1][1],0,'Update before release, not after500ms hold')
        self.assertEqual(partial[0][1][2],'idle','Never write OLED during TX/RX')
        self.assertEqual(m.battery_reads,1,'No ADC while the central owns radio')
        m.key(2,13);state=m.run_until_idle();self.assertEqual(state[7],0)
        self.assertIn((96,6,'MUTE'),m.screen_lines)
    def test_native_partial_oled_renderer_is_bounded_and_fits_stack(self):
        m=self.m;m.stub_ascii=False;m.symbols['native_ascii']=0x11DC9
        for text,x,page in [(b'100\0',72,3),(b'MUTE\0',96,6)]:
            m.machine.mem_write(INPUT,text);start=m.clock;m.call('native_ascii',x,page,INPUT)
            # Budget4 timer ticks per executed instruction. Only one partial
            # renderer call is permitted before the idle-window check repeats.
            self.assertLess((m.clock-start)*4,ticks(10000))
        self.assertGreaterEqual(m.lowest_sp,m.symbols['__ss_stack_limit'])
    def test_mute_restores_confirmed_volume_across_connections_and_counts_edges(self):
        m=self.m;m.call('u1_screen_gateway');m.debug(3,1)
        m.key(2,13)
        for _ in range(50):m.call('u1_key_gateway',2,13)
        state=m.run_until_idle();self.assertEqual(state[7],0);self.assertEqual(state[8],0)
        self.assertEqual(m.peer.writes,[0]);self.assertIn('MUTE',m.screen_lines[-1][2])
        m.key(2,13);state=m.run_until_idle()
        self.assertEqual(state[7],0);self.assertEqual(state[8],18);self.assertEqual(m.peer.writes,[0,18])
        self.assertNotIn('MUTE',m.screen_lines[-1][2]);self.assertEqual(m.key_calls,[])
    def test_mute_double_press_before_query_cancels_and_zero_never_invents_restore(self):
        m=self.m;m.debug(3,1);m.key(2,13);m.key(2,13)
        state=m.run_until_idle();self.assertEqual(state[8],18);self.assertEqual(m.peer.writes,[])
        m.peer.volume=0
        for _ in range(2):m.key(2,13);state=m.run_until_idle();self.assertEqual(state[8],0)
        self.assertEqual(m.peer.writes,[])
    def test_volume_key_leaves_mute_from_zero_and_external_change_is_not_overwritten(self):
        m=self.m;m.debug(3,1);m.key(2,13);m.run_until_idle()
        m.key(2,4);state=m.run_until_idle();self.assertEqual(state[8],1)
        self.assertEqual(m.peer.writes,[0,1])
        m.key(2,13);m.run_until_idle();m.peer.volume=7
        m.key(2,13);state=m.run_until_idle();self.assertEqual(state[8],0)
        m.key(2,13);state=m.run_until_idle();self.assertEqual(state[8],7)
        self.assertEqual(m.peer.writes,[0,1,0,0,7])
    def test_mute_other_profile_and_configuration_use_native_key(self):
        m=self.m;m.debug(3,1);self.assertEqual(m.key(1,13),7)
        m.put8(0x20000270,3);m.put8(0x20000163,0);self.assertEqual(m.key(2,13),7)
        self.assertEqual(m.key_calls,[(1,13),(2,13)]);self.assertEqual(m.peer.writes,[])
    def test_stock_key_wake_and_release_do_not_flash_profile_menu_for_soundsend(self):
        m=self.m;m.stock_key_dispatch_fixture=True;m.symbols['stock_dispatch']=0x9C55
        m.symbols['stock_refresh']=0x8EDD;m.debug(3,1)
        m.put8(0x20000162,0);m.put32(0x20000108,1<<3)
        m.call('stock_dispatch')
        self.assertEqual(m.screen_calls,0,'Key wake must not draw the original device menu first')
        self.assertEqual([page for x,page,line in m.screen_lines],[0,3,6])
        self.assertEqual(m.machine.mem_read(0x2000016f,1)[0],3)
        self.assertEqual(m.u32(0x40010020)&0x5c03e000,0x5c03e000,'Preserve stock OLED/wake GPIO effect')
        m.poll();before=(len(m.screen_lines),m.screen_calls,m.battery_reads)
        m.call('u1_screen_gateway')
        self.assertEqual((len(m.screen_lines),m.screen_calls,m.battery_reads),before,'Keep current screen while RF owns the port')
        state=m.run_until_idle();self.assertEqual(state[8],19);self.assertEqual(m.peer.writes,[19])
        m.put32(0x20000108,0);m.call('stock_dispatch')
        m.put8(0x20000162,4);m.put8(0x20000106,2);m.call('stock_refresh')
        self.assertEqual(m.screen_calls,0,'Release refresh must not return to the device list')
        self.assertIn('19 %',m.screen_lines[-2][2])
    def test_direct_volume_screen_keeps_native_low_battery_and_selection_repair(self):
        m=self.m;m.battery_level=55;m.call('u1_screen_gateway')
        self.assertEqual(m.screen_calls,1,'Native low-power handling must remain reachable')
        m.battery_level=85;m.put8(0x20000102,0);m.call('u1_screen_gateway')
        self.assertEqual(m.screen_calls,2,'Native selected-device repair must remain reachable')
        m.put8(0x20000102,1);m.put8(0x20000103,1);m.call('u1_screen_gateway')
        self.assertEqual(m.screen_calls,3,'Wheel navigation to another device must use its native screen')
    def test_timer_reads_current_value_and_stops_discarded_link_clock(self):
        m=self.m;m.timer_reload=0x2468;m.timer_value=0x1357
        m.put32(0x40001008,0x2468);m.put32(0x40001004,0x1357)
        m.debug(2);m.poll()
        self.assertEqual(m.u32(0x40001008),0xFFFFFFFF,'RELOAD must remain static')
        first=m.call('ap_now');m.clock+=160
        self.assertGreater(m.call('ap_now'),first,'Time must come from VALUE, not RELOAD')
        s=m.run_until_idle();self.assertEqual(s[7],0)
        self.assertEqual(m.u32(0x40001000),0)
    def test_stalled_current_value_reports_physical_clock_fault(self):
        m=self.m;m.timer_frozen=True;m.debug(2)
        m.call('u1_poll_gateway',1,2,3,4,instruction_limit=6000000)
        s=m.debug(1)
        self.assertEqual(s[5],0);self.assertEqual(s[7],0x81)
        self.assertEqual(m.tx,[]);self.assertEqual(m.restore_calls,1)
    def test_stock_timer_control_fallback_keeps_irq_masked_and_releases_stopped(self):
        m=self.m;m.timer_requires_irq_enable=True
        m.put32(0x40001000,9);m.timer_running=True
        m.debug(2);m.call('u1_poll_gateway',1,2,3,4,instruction_limit=3000000)
        self.assertEqual(m.u32(0x40001000),9)
        self.assertEqual(m.irq&(1<<9),0)
        s=m.run_until_idle();self.assertEqual(s[7],0);self.assertEqual(s[8],18)
        self.assertEqual(m.u32(0x40001000),0,'An expired old-link timer must not restart')
    def test_read_only_clock_diagnostic_identifies_build_and_snapshots(self):
        import sys
        sys.path.insert(0,str(ROOT.parent))
        from u1_debug import decode_response
        state=decode_response(self.m.debug(5),5)
        self.assertEqual(state['build_id'],'0x544d524a')
        self.assertEqual(state['lab_protocol_revision'],19)
        self.assertEqual(state['clock_before'],0);self.assertEqual(state['clock_after'],0)
        self.assertEqual(state['port_error'],0);self.assertEqual(self.m.tx,[])
    def test_read_only_radio_snapshot_does_not_start_radio(self):
        import sys
        sys.path.insert(0,str(ROOT.parent))
        from u1_debug import decode_response
        state=decode_response(self.m.debug(6),6)
        self.assertEqual(state['rf_status'],'0x0');self.assertEqual(state['rf_header'],'0x0')
        self.assertEqual(state['rf_control'],'0x0');self.assertEqual(state['lab_protocol_revision'],19)
        self.assertEqual(self.m.tx,[])
    def test_lab_mode3_matches_stock_ble_control_before_rx(self):
        m=self.m;m.symbols['stock_ble_control_start']=0x6CD
        m.call('stock_ble_control_start');self.assertEqual(m.u32(0x50001300)&2,2)
        m.require_ble_rx_engine=True;m.debug(4,3);m.debug(2)
        state=m.run_until_idle();self.assertEqual(state[7],0);self.assertEqual(state[8],18)
        self.assertTrue(all(control&2 for control in m.rx_launch_controls[1:]))
    def test_crc_filtered_end_flag_recovers_while_receiver_remains_enabled(self):
        m=self.m;m.bad_scan_packets=1;m.debug(2);state=m.run_until_idle()
        self.assertEqual(state[7],0);self.assertEqual(state[8],18)
        self.assertEqual(int.from_bytes(state[14:16],'little'),1)
        self.assertEqual(m.peer.writes,[])
    def test_header_launch_modes_follow_executed_stock_control_then_header_order(self):
        original=Machine()
        original.symbols['stock_start']=0x6CD;original.symbols['stock_header']=0x7785
        original.call('stock_start');original.call('stock_header',5,0,0,28)
        stock=[a for a,v in original.rf_writes if a in (0x50001300,0x50001304)]
        self.assertEqual(stock,[0x50001300,0x50001304])
        self.assertEqual(original.u32(0x50001304),0x2205)
        for mode in (4,5):
            with self.subTest(mode=mode):
                m=Machine();m.header_triggers_tx=True;m.require_ble_rx_engine=True
                m.debug(4,mode);m.debug(2);state=m.run_until_idle()
                self.assertEqual(state[7],0);self.assertEqual(state[8],18)
                self.assertEqual(m.peer.writes,[])
                self.assertTrue(m.tx)
                self.assertNotIn((0x50001004,0xD0),m.rf_writes)
                for i,(address,value) in enumerate(m.rf_writes):
                    if address==0x50001304:self.assertEqual(m.rf_writes[i-1][0],stock[0])
    def test_tx_diagnostic_records_attempt_even_when_transmission_fails(self):
        import sys
        sys.path.insert(0,str(ROOT.parent))
        from u1_debug import decode_response
        m=self.m;m.transmit_works=False;m.debug(4,4);m.debug(2);state=m.run_until_idle()
        self.assertEqual(state[7],0x86)
        tx=decode_response(m.debug(7),7)
        self.assertEqual(tx['intended_tx_header'],'0x2205');self.assertEqual(tx['tx_mode'],4)
        self.assertEqual(tx['lab_protocol_revision'],19);self.assertEqual(tx['port_error'],0x86)
        self.assertEqual(m.tx,[])
    def test_execute_original_advertising_fragment_records_engine_header_then_fifo(self):
        m=self.m
        m.machine.mem_write(0x20002CEC,bytes(16));m.put8(0x20002CF6,3)
        m.machine.mem_write(0x20002CF7,bytes.fromhex('02 01 06'))
        m.machine.mem_write(0x20000175,bytes.fromhex('20 21 03 00 2e ab'))
        m.machine.reg_write(UC_ARM_REG_R6,0x20002CEC)
        m.machine.reg_write(UC_ARM_REG_SP,m.call_stack)
        m.machine.emu_start(0xAF3,0xB9E,count=10000)
        self.assertEqual(m.machine.reg_read(UC_ARM_REG_PC),0xB9E)
        addresses=[a for a,v in m.rf_writes]
        self.assertEqual(addresses[:2],[0x50001300,0x50001304])
        self.assertEqual(addresses[2:],list(range(0x50001400,0x50001409)))
        self.assertEqual(bytes(m.machine.mem_read(0x50001400,9)),OWN+bytes.fromhex('02 01 06'))
    def test_complete_stock_order_with_last_byte_at_anchor_and_empty_data_pdus(self):
        m=self.m;m.fifo_last_byte_triggers_tx=True;m.require_ble_rx_engine=True
        m.put32(0x50001020,0xC000003F)
        m.debug(4,6);m.debug(2);state=m.run_until_idle()
        self.assertEqual(state[7],0);self.assertEqual(state[8],18)
        self.assertEqual(m.peer.writes,[])
        self.assertTrue(any(not p['payload'] for p in m.tx),'Empty data PDUs must also launch')
        self.assertNotIn((0x50001004,0xD0),m.rf_writes)
        import sys
        sys.path.insert(0,str(ROOT.parent))
        from u1_debug import decode_response
        config=decode_response(m.debug(9),9)
        self.assertEqual(config['tx_fifo_control'],'0xc000003f')
        self.assertEqual(config['tx_fifo_end_pointer'],63)
    def test_raw_fifo_packet_reaches_peer_with_valid_crc_and_restores_native_radio(self):
        m=self.m;m.put32(0x50001008,0x701);m.put32(0x50001020,0xC000003F);m.put32(0x50001300,9)
        m.debug(4,7);m.debug(2);state=m.run_until_idle()
        self.assertEqual(state[7],0,state.hex());self.assertEqual(state[8],18)
        self.assertEqual(m.peer.writes,[]);self.assertTrue(m.raw_tx_frames)
        self.assertEqual(len(m.raw_tx_frames[0]),39)
        self.assertEqual(m.u32(0x50001008),0x701);self.assertEqual(m.u32(0x50001020),0xC000003F)
        self.assertEqual(m.u32(0x50001300),9)
        self.assertGreaterEqual(m.lowest_sp,m.symbols['__ss_stack_limit'],'Raw packet must fit retained stack')
        before_connect=[at for at,addr in m.fifo_write_times if at<m.tx[0]['at']]
        self.assertEqual(len(before_connect),39)
        self.assertLess(max(before_connect),m.ad_end,'CONNECT FIFO copy must occur before the peer advertisement ends')
    def test_raw_fifo_failure_restores_native_controls_before_stock_recovery(self):
        m=self.m;m.transmit_works=False
        m.put32(0x50001008,0x701);m.put32(0x50001020,0xC000003F);m.put32(0x50001300,9)
        m.debug(4,7);m.debug(2);state=m.run_until_idle()
        self.assertEqual(state[7],0x86);self.assertEqual(m.restore_calls,1)
        self.assertEqual(m.u32(0x50001008),0x701);self.assertEqual(m.u32(0x50001020),0xC000003F)
        self.assertEqual(m.u32(0x50001300),9);self.assertEqual(m.tx,[])
    def test_slow_packet_encoder_is_outside_connect_deadline_and_cache_tracks_scan_hop(self):
        m=self.m;m.codec_delay=ticks(200);m.skip_scan_channel37=True
        m.debug(4,7);m.debug(2);state=m.run_until_idle()
        self.assertEqual(state[7],0,state.hex());self.assertEqual(state[8],18)
        self.assertEqual(m.tx[0]['channel'],38,'Cached whitening must follow the advertising channel hop')
        self.assertEqual(m.peer.writes,[])
    def test_cached_advertisement_never_rebuilds_connect_payload_inside_advanced_ifs(self):
        m=self.m;m.connect_build_delay=ticks(120);m.tx_start_delay=ticks(72)
        m.debug(4,10);m.debug(11,72);m.debug(2);state=m.run_until_idle()
        self.assertEqual(state[7],0,state.hex());self.assertEqual(state[8],18)
        self.assertTrue(m.connect_build_times)
        self.assertTrue(all(t<m.ad_end for t in m.connect_build_times),'CONNECT construction must be before live advertisement end')
        self.assertEqual(m.peer.writes,[])
    def test_nonconnectable_radio_selftest_sends_nine_known_beacons_without_sound_or_att(self):
        m=self.m;m.debug(4,7);m.debug(12);state=m.run_until_idle(limit=9000)
        self.assertEqual(state[7],0);self.assertEqual(state[8],255);self.assertEqual(state[4:6],bytes(2))
        self.assertEqual(len(m.advertisements),9)
        self.assertEqual([a['channel'] for a in m.advertisements],[37,38,39]*3)
        self.assertEqual(len(m.tx),9);self.assertEqual(m.peer.writes,[])
        self.assertTrue(all(a['header']==2 for a in m.advertisements))
        self.assertGreaterEqual(m.lowest_sp,m.symbols['__ss_stack_limit'])
    def test_radio_selftest_is_rejected_when_volume_override_is_enabled(self):
        m=self.m;m.debug(4,7);m.debug(3,1);state=m.debug(12)
        self.assertEqual(state[7],0x90);m.poll();self.assertEqual(m.tx,[])
    def test_raw_delay_comparison_preserves_stock_controls_and_never_writes_volume(self):
        for mode,delay in ((7,0x601512),(8,0x601510),(9,0x601502),(10,0x601500)):
            with self.subTest(mode=mode):
                m=Machine();m.put32(0x50001008,0x322);m.put32(0x50001020,0xC000003F);m.put32(0x500011c0,0x601512)
                m.debug(4,mode);m.debug(12);state=m.run_until_idle(limit=9000)
                self.assertEqual(state[7],0);self.assertEqual(int.from_bytes(state[10:12],'little'),9)
                self.assertEqual(m.raw_tx_modes,[0x502]*9);self.assertEqual(m.peer.writes,[])
                self.assertEqual(m.raw_tx_delays,[delay]*9);self.assertEqual(m.u32(0x500011c0),0x601512)
                self.assertEqual(m.u32(0x50001008),0x322);self.assertEqual(m.u32(0x50001020),0xC000003F)
                self.assertGreaterEqual(m.lowest_sp,m.symbols['__ss_stack_limit'])
    def test_raw_tx_duration_reports_polling_interval_and_keeps_timer_control_separate(self):
        import sys
        sys.path.insert(0,str(ROOT.parent))
        from u1_debug import decode_response
        m=self.m;m.debug(4,7);m.debug(12);m.run_until_idle(limit=9000)
        clock=decode_response(m.debug(5),5);tx=decode_response(m.debug(7),7)
        self.assertEqual(clock['timer_control'],1);self.assertEqual(clock['sample_kind'],'raw_tx_state_interval')
        self.assertEqual(tx['raw_tx_polling_ticks'],(clock['clock_after']-clock['clock_before'])&0xFFFFFFFF)
        self.assertGreater(tx['raw_tx_polling_ticks'],ticks(200));self.assertLess(tx['raw_tx_polling_ticks'],ticks(300))
        self.assertEqual(m.peer.writes,[])
    def test_raw_timeout_retains_elapsed_measurement_before_restoring_stock_radio(self):
        import sys
        sys.path.insert(0,str(ROOT.parent))
        from u1_debug import decode_response
        m=self.m;m.tx_duration_extra=ticks(1000);m.put32(0x50001008,0x322)
        m.debug(4,7);m.debug(12);state=m.run_until_idle(limit=9000)
        self.assertEqual(state[7],0x87)
        tx=decode_response(m.debug(7),7)
        self.assertGreaterEqual(tx['raw_tx_polling_ticks'],ticks(800))
        self.assertEqual(m.u32(0x50001008),0x322);self.assertEqual(m.restore_calls,1)
        self.assertEqual(m.peer.writes,[])
    def test_queued_radio_selftest_cannot_switch_bit_order_before_execution(self):
        m=self.m;m.debug(4,7);m.debug(12);state=m.debug(4,8)
        self.assertEqual(state[9],7);self.assertEqual(state[7],0x91)
    def test_raw_tx_release_completes_while_wtr_tail_stays_high(self):
        m=self.m;m.wtr_tail=ticks(20);m.debug(4,7);m.debug(2)
        state=m.run_until_idle();self.assertEqual(state[7],0);self.assertEqual(state[8],18)
        self.assertEqual(m.peer.writes,[]);self.assertEqual(m.restore_calls,1)
    def test_stuck_wtr_tail_is_bounded_and_restores_original_delay(self):
        m=self.m;m.wtr_tail=ticks(200);m.put32(0x500011c0,0x601512)
        m.debug(4,10);m.debug(12);state=m.run_until_idle(limit=9000)
        self.assertEqual(state[7],0x8a);self.assertEqual(state[10:12],bytes(2))
        self.assertEqual(m.u32(0x500011c0),0x601512);self.assertEqual(m.restore_calls,1)
        self.assertEqual(m.peer.writes,[])
    def test_raw_settling_is_compensated_before_connect_and_data_anchors(self):
        m=self.m;m.tx_start_delay=ticks(40);m.debug(11,40);m.debug(4,7);m.debug(2)
        state=m.run_until_idle();self.assertEqual(state[7],0,state.hex());self.assertEqual(state[8],18)
        self.assertEqual(m.peer.writes,[]);self.assertTrue(m.tx)
        self.assertGreaterEqual(m.lowest_sp,m.symbols['__ss_stack_limit'])
    def test_tx_lead_preserves_rx_options_and_rejects_invalid_or_busy_changes(self):
        m=self.m;m.debug(8,1);self.assertEqual(m.debug(11,40)[4],40)
        self.assertEqual(m.debug(8,1)[4],1)
        self.assertEqual(m.debug(11,141)[4],40);self.assertEqual(m.debug(11,42)[4],40)
        m.debug(3,1);self.assertEqual(m.debug(11,44)[4],40)
        m.debug(3,0);m.debug(2);self.assertEqual(m.debug(11,44)[4],40)
        self.assertEqual(m.debug(7)[14],40);self.assertEqual(m.tx,[])
    def test_zero_eopf_write_leaves_stale_latch_and_1400_recovers_fresh_scan(self):
        m=self.m;m.rf_flags=0x1400;m.debug(8,0);m.debug(2);m.poll()
        self.assertEqual(m.rf_flags&0x1000,0x1000)
        self.assertEqual(m.tx,[])
        self.assertGreater(int.from_bytes(m.debug(1)[12:14],'little'),0)
        self.assertEqual(m.debug(7)[8],0)
        m.call('u1_key_gateway',1,13)
        self.assertEqual(m.debug(1)[5],0)
        m.debug(8,2);m.debug(2);state=m.run_until_idle()
        self.assertEqual(state[7],0);self.assertEqual(state[8],18)
        self.assertEqual(m.peer.writes,[])
    def test_rx_clear_mode_changes_only_while_idle(self):
        m=self.m
        self.assertEqual(m.debug(8,1)[4],1)
        m.debug(8,3);self.assertEqual(m.debug(8,1)[4],1)
        m.debug(8,0);m.debug(2)
        self.assertEqual(m.debug(8,2)[4],0,'A queued probe cannot change its radio clear semantics')
    def test_false_sync_tail_timeout_rearms_scan_instead_of_aborting(self):
        m=self.m;m.stalled_scan_packets=1;m.debug(2)
        m.call('u1_poll_gateway',1,2,3,4,instruction_limit=200000)
        state=m.run_until_idle()
        self.assertEqual(state[7],0);self.assertEqual(state[8],18)
        self.assertEqual(int.from_bytes(state[16:18],'little'),1)
        self.assertEqual(m.peer.writes,[])
    def test_acquisition_waits_for_asynchronous_stock_disconnect(self):
        m=self.m;m.put8(0x20000270,3);m.put8(0x20000163,0);m.disconnect_delay=200
        m.debug(2);s=m.run_until_idle()
        self.assertEqual(s[7],0,s.hex());self.assertEqual(s[8],18)
        self.assertEqual(m.peer.writes,[]);self.assertEqual(m.restore_calls,1)
    def test_key_during_termination_is_carried_to_a_new_connection(self):
        m=self.m;m.debug(3,1);m.key(2,4)
        for _ in range(5000):
            m.poll();m.clock+=ticks(250)
            if m.tx and m.tx[-1]['payload']==bytes.fromhex('02 13'):break
        else:self.fail('No graceful termination was transmitted')
        m.key(2,4)
        m.run_until_idle();m.poll();s=m.run_until_idle()
        self.assertEqual(m.peer.writes,[19,20]);self.assertEqual(s[8],20);self.assertEqual(m.restore_calls,2)
    def test_failed_tx_returns_to_original_radio_mode(self):
        m=self.m;m.transmit_works=False;m.debug(2);s=m.run_until_idle()
        self.assertEqual(s[7],0x86);self.assertEqual(m.restore_calls,1);self.assertEqual(s[5],0)
    def test_non_volume_key_cancels_probe_before_original_key(self):
        m=self.m;m.debug(2);m.poll()
        self.assertEqual(m.debug(1)[5],2)
        self.assertEqual(m.call('u1_key_gateway',1,13),7)
        self.assertEqual(m.restore_calls,1);self.assertEqual(m.key_calls,[(1,13)])
        self.assertEqual(m.debug(1)[5],0);self.assertEqual(m.peer.writes,[])
    def test_constant_rssi_does_not_transmit_with_a_fixed_seed(self):
        m=self.m;m.constant_rssi=True;m.debug(2)
        m.call('u1_poll_gateway',1,2,3,4,instruction_limit=2500000)
        s=m.debug(1)
        self.assertEqual(s[5],0);self.assertEqual(s[7],0x82)
        self.assertEqual(m.tx,[]);self.assertEqual(m.restore_calls,1)
    def test_debug_checksum_is_required(self):
        self.m.machine.mem_write(INPUT,bytes.fromhex('53 53 01 03 01')+bytes(15))
        self.assertEqual(self.m.call('u1_debug_command',INPUT,OUTPUT),0)
    def test_pc_diagnostic_framing_matches_compiled_arm(self):
        import sys
        sys.path.insert(0,str(ROOT.parent))
        from u1_debug import request_packet,decode_response
        m=self.m;m.machine.mem_write(INPUT,request_packet(1))
        self.assertEqual(m.call('u1_debug_command',INPUT,OUTPUT),1)
        state=decode_response(bytes(m.machine.mem_read(OUTPUT,20)),1)
        self.assertEqual(state['enabled'],False);self.assertEqual(state['tx_mode'],1)
        with self.assertRaises(ValueError):decode_response(request_packet(1),1)
    def test_late_fff4_debug_write_through_original_dispatch_and_notification_queue(self):
        import sys
        sys.path.insert(0,str(ROOT.parent))
        from u1_debug import request_packet,decode_response
        m=self.m;m.poll() # The new write arrives after our foreground poll.
        frame=m.echo_fragment(request_packet(1))
        self.assertEqual(m.u32(0x200002B0)&4,0)
        self.assertEqual(frame[:7],bytes.fromhex('17 00 04 00 1b 55 00'))
        self.assertEqual(decode_response(frame[7:],1)['enabled'],False)
    def test_ordinary_fff4_write_keeps_original_echo(self):
        packet=bytes(range(20));frame=self.m.echo_fragment(packet)
        self.assertEqual(frame[:7],bytes.fromhex('17 00 04 00 1b 55 00'))
        self.assertEqual(frame[7:],packet)


if __name__=='__main__':
    result=unittest.TextTestRunner(verbosity=2).run(unittest.defaultTestLoader.loadTestsFromTestCase(IntegrationTests))
    report={'tests_run':result.testsRun,'passed':result.wasSuccessful(),
            'module_sha256':hashlib.sha256(ELF.read_bytes()).hexdigest(),'image_sha256':hashlib.sha256(IMAGE.read_bytes()).hexdigest(),
            'scope':'Patched ARM hooks + C integration + MMIO port with modeled RF/timer/RSSI and stubbed legacy poll/restore. Not physical RF or OTA recovery validation.'}
    (ROOT/'u1-lab-test-results.json').write_text(json.dumps(report,indent=2)+'\n')
    raise SystemExit(0 if result.wasSuccessful() else 1)
