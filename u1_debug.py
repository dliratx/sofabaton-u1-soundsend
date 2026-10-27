"""Temporary PC diagnostics for the custom U1 lab firmware, not an audio bridge.

No OTA/update operations. Commands use a bounded20-byte FFF4/FFF3 protocol.
The firmware must already be installed; stock echoes are explicitly rejected.
"""
import argparse,asyncio,json
from datetime import datetime,timezone
from pathlib import Path
from u1_transport import open_remote,read_identity,APP_SERVICE

READ='0000fff3-0000-1000-8000-00805f9b34fb'
WRITE='0000fff4-0000-1000-8000-00805f9b34fb'
COMMANDS={'status':1,'probe':2,'enable':3,'disable':3,'tx-mode':4,'clock':5,'radio':6,'tx-snapshot':7,'rx-clear-mode':8,'radio-config':9,'modem-config':10,'tx-lead':11,'delay-config':13,'power':14}


def request_packet(command,argument=0):
    if command not in range(1,15) or not 0<=argument<=255:raise ValueError('Invalid debug command/argument')
    p=bytearray(20);p[:5]=bytes([0x53,0x53,1,command,argument]);p[19]=sum(p[:19])&255;return bytes(p)


def decode_response(packet,command):
    p=bytes(packet)
    if len(p)!=20 or p[:4]!=bytes([0x53,0x53,1,command|0x80]) or sum(p[:19])&255!=p[19] or p[18] not in range(1,20):
        raise ValueError('Response is not from the custom firmware or has an invalid checksum')
    if command==14:
        if p[18]<18:raise ValueError('Power diagnostics require LAB18')
        control=int.from_bytes(p[12:16],'little')
        return {'idle_ticks':int.from_bytes(p[4:8],'little'),'idle_timeout_ticks':int.from_bytes(p[8:12],'little'),
                'inactivity_timer_control':hex(control),'inactivity_timer_running':bool(control&1),
                'screen_mode':p[16],'native_ble_state':p[17],'lab_protocol_revision':p[18]}
    if command==5:
        if p[18]<2:raise ValueError('Clock snapshot requires LAB revision2 or later')
        before=int.from_bytes(p[8:12],'little');after=int.from_bytes(p[12:16],'little')
        return {'build_id':hex(int.from_bytes(p[4:8],'little')),'clock_before':before,'clock_after':after,
                'clock_advanced':before!=after,'timer_control':p[16]&0x7f if p[18]>=11 else p[16],
                'sample_kind':'missed_raw_launch_anchor' if p[18]>=14 and p[17]==0x85 and p[16]&0x80 else
                    ('raw_tx_state_interval' if p[18]>=12 else 'raw_tx_polling_interval') if p[18]>=11 and p[16]&0x80 else 'entropy_clock_wait',
                'port_error':p[17],'lab_protocol_revision':p[18]}
    if command==6:
        if p[18]<3:raise ValueError('Radio snapshot requires LAB revision3')
        status=int.from_bytes(p[4:8],'little');header=int.from_bytes(p[8:12],'little');control=int.from_bytes(p[12:16],'little')
        return {'rf_status':hex(status),'rf_header':hex(header),'rf_control':hex(control),
                'pdu_type':header&15,'pdu_length':(header>>8)&63,'tx_mode':p[16],'port_error':p[17],'lab_protocol_revision':p[18]}
    if command==7:
        if p[18]<5:raise ValueError('TX snapshot requires LAB revision5')
        header=int.from_bytes(p[6:8],'little')
        missed=p[18]>=14 and p[17]==0x85 and p[13]==1
        return {'launch_status':hex(int.from_bytes(p[4:6],'little')),'intended_tx_header':hex(header),
                'intended_tx_type':header&15,'intended_tx_length':(header>>8)&63,
                'rx_clear_mode':p[8] if p[18]>=6 else None,
                'raw_tx_polling_ticks':int.from_bytes(p[9:13],'little') if p[18]>=11 and p[13]==1 and not missed else None,
                'raw_tx_polling_us_assuming_16mhz':int.from_bytes(p[9:13],'little')/16 if p[18]>=11 and p[13]==1 and not missed else None,
                'missed_anchor_ticks':int.from_bytes(p[9:13],'little') if missed else None,
                'missed_anchor_us_assuming_16mhz':int.from_bytes(p[9:13],'little')/16 if missed else None,
                'tx_lead_us':p[14] if p[18]>=12 else 0,
                'tx_mode':p[16],'port_error':p[17],'lab_protocol_revision':p[18]}
    if command==8:
        if p[18]<6 or p[4]>2 or p[5]>2:raise ValueError('RX-clear mode requires LAB revision6')
        return {'rx_clear_mode':p[4],'phase':('idle','acquire','active')[p[5]],
                'tx_mode':p[16],'port_error':p[17],'lab_protocol_revision':p[18]}
    if command==9:
        if p[18]<7:raise ValueError('Radio configuration snapshot requires LAB revision7')
        fifo=int.from_bytes(p[4:8],'little')
        return {'tx_fifo_control':hex(fifo),'tx_fifo_end_pointer':fifo&255,
                'current_ble_time':hex(int.from_bytes(p[8:12],'little')),
                'current_fifo_control':hex(int.from_bytes(p[12:16],'little')),
                'tx_mode':p[16],'port_error':p[17],'lab_protocol_revision':p[18]}
    if command==10:
        if p[18]<8:raise ValueError('Modem configuration requires LAB revision8')
        return {'current_mode_control':hex(int.from_bytes(p[4:8],'little')),
                'current_rf_gpio':hex(int.from_bytes(p[8:12],'little')),
                'current_data_rate':hex(int.from_bytes(p[12:16],'little')),
                'tx_mode':p[16],'port_error':p[17],'lab_protocol_revision':p[18]}
    if command==11:
        if p[18]<12 or p[4]>140 or p[4]&3 or p[5]>2:raise ValueError('TX lead requires LAB12')
        return {'tx_lead_us':p[4],'phase':('idle','acquire','active')[p[5]],
                'tx_mode':p[16],'port_error':p[17],'lab_protocol_revision':p[18]}
    if command==13:
        if p[18]<12:raise ValueError('Delay configuration requires LAB12')
        return {'current_rf_delay':hex(int.from_bytes(p[4:8],'little')),
                'current_rf_clock':hex(int.from_bytes(p[8:12],'little')),
                ('selected_tx_delay_computed' if p[18]>=13 else 'current_rf_clock2'):hex(int.from_bytes(p[12:16],'little')),
                'tx_mode':p[16],'port_error':p[17],'lab_protocol_revision':p[18]}
    if p[4]>1 or p[5]>2 or p[9] not in range(1,11 if p[18]>=11 else 8):raise ValueError('Invalid custom firmware state')
    return {'enabled':bool(p[4]),'phase':('idle','acquire','active')[p[5]],'central_state':p[6],
            'reason':p[7],'volume':None if p[8]==255 else p[8],'tx_mode':p[9],
            'tx_packets':int.from_bytes(p[10:12],'little'),'rx_packets':int.from_bytes(p[12:14],'little'),
            'crc_errors':int.from_bytes(p[14:16],'little'),'errors':int.from_bytes(p[16:18],'little'),
            'lab_protocol_revision':p[18]}


async def exchange(client,command,argument=0,trace=None):
    service=client.services.get_service(APP_SERVICE)
    receive=service.get_characteristic(READ) if service else None
    transmit=service.get_characteristic(WRITE) if service else None
    if receive is None or transmit is None:raise RuntimeError('Diagnostic channel is missing')
    received=asyncio.Queue()
    def notify(characteristic,data):
        if trace:trace('debug_rx',hex=bytes(data).hex(' '),command=command)
        try:decoded=decode_response(data,command)
        except ValueError:return
        received.put_nowait(decoded)
    await asyncio.wait_for(client.start_notify(receive,notify),5)
    try:
        if trace:trace('debug_tx',hex=request_packet(command,argument).hex(' '),command=command)
        await asyncio.wait_for(client.write_gatt_char(transmit,request_packet(command,argument),response=True),5)
        if trace:trace('debug_write_acknowledged',command=command)
        return await asyncio.wait_for(received.get(),5)
    finally:
        if client.is_connected:
            try:await asyncio.wait_for(client.stop_notify(receive),5)
            except (Exception,asyncio.TimeoutError):pass


async def run(args):
    events=[]
    def record(event,**fields):
        row={'time':datetime.now(timezone.utc).isoformat(),'event':event,**fields};events.append(row)
        print(json.dumps(row,ensure_ascii=False),flush=True)
    try:
        async with open_remote() as client:
            identity=await read_identity(client);record('identity',**identity)
            if identity['firmware']!=32:raise RuntimeError('Expected V32-based custom firmware; no diagnostics sent to stock firmware')
            # Always prove that this is the custom lab before a probe/enable.
            state=await exchange(client,1,trace=record);record('status',**state)
            if args.command!='status':
                if args.command=='power' and state['lab_protocol_revision']<18:raise RuntimeError('Power diagnostics require LAB18')
                if args.command=='clock' and state['lab_protocol_revision']<2:raise RuntimeError('Installed LAB has no clock snapshot command')
                if args.command=='radio' and state['lab_protocol_revision']<3:raise RuntimeError('Installed LAB has no radio snapshot command')
                if args.command=='tx-snapshot' and state['lab_protocol_revision']<5:raise RuntimeError('Installed LAB has no TX snapshot command')
                if args.mode>3 and state['lab_protocol_revision']<5:raise RuntimeError('Installed LAB has no header-launch modes')
                if (args.mode>5 or args.command=='radio-config') and state['lab_protocol_revision']<7:raise RuntimeError('Installed LAB has no FIFO-launch mode/configuration snapshot')
                if (args.mode>6 or args.command=='modem-config') and state['lab_protocol_revision']<8:raise RuntimeError('Installed LAB has no raw FIFO mode/modem configuration snapshot')
                if args.mode>7 and state['lab_protocol_revision']<11:raise RuntimeError('Raw bit-order variants require LAB11')
                if (args.command in ('tx-lead','delay-config') or args.lead_us is not None) and state['lab_protocol_revision']<12:
                    raise RuntimeError('TX lead/delay diagnostics require LAB12')
                if args.command=='tx-lead' and args.lead_us is None:raise RuntimeError('Specify --lead-us for tx-lead')
                rx_mode=getattr(args,'rx_mode',None)
                if rx_mode is not None or args.command=='rx-clear-mode':
                    if state['lab_protocol_revision']<6:raise RuntimeError('Installed LAB has no RX-clear-mode command')
                    if rx_mode is None:raise RuntimeError('Specify --rx-mode for rx-clear-mode')
                argument=1 if args.command=='enable' else args.mode if args.command=='tx-mode' else rx_mode if args.command=='rx-clear-mode' else args.lead_us if args.command=='tx-lead' else 0
                if args.command=='probe':
                    # A nondefault mode also marks volatile runtime state. A
                    # return to mode1 after this read-only probe reveals reset.
                    state=await exchange(client,4,args.mode);record('probe_mode',**state)
                    if state['lab_protocol_revision']>=13 and args.mode>=7:
                        record('raw_mode_definition',mode=args.mode,definition={7:'stock_delay',8:'PDL_bits_cleared',9:'TDL_bits_cleared',10:'PDL_and_TDL_bits_cleared'}[args.mode])
                    if rx_mode is not None:record('probe_rx_mode',**await exchange(client,8,rx_mode))
                    if state['lab_protocol_revision']>=12:record('probe_tx_lead',**await exchange(client,11,args.lead_us or 0))
                    record('probe_request_sent',command=2,changes_audio=False)
                    try:record('probe',**await exchange(client,2))
                    except Exception as error:
                        record('probe_request_error',error=f'{type(error).__name__}: {error}')
                else:
                    state=await exchange(client,COMMANDS[args.command],argument);record(args.command,**state)
        if args.command=='probe':
            # The U1 releases the PC link to use its own radio as central.
            await asyncio.sleep(6)
            async with open_remote() as client:
                state=await exchange(client,1);record('probe_result',**state)
                if state['lab_protocol_revision']>=2:record('clock_snapshot',**await exchange(client,5))
                if state['lab_protocol_revision']>=3:record('radio_snapshot',**await exchange(client,6))
                if state['lab_protocol_revision']>=5:record('tx_snapshot',**await exchange(client,7))
                if state['lab_protocol_revision']>=7:record('radio_config',**await exchange(client,9))
                if state['lab_protocol_revision']>=8:record('modem_config',**await exchange(client,10))
                if state['lab_protocol_revision']>=12:record('delay_config',**await exchange(client,13))
                if args.mode!=1:record('probe_mode_restored',**await exchange(client,4,1))
                default_rx=2 if state['lab_protocol_revision']>=13 else 0
                if args.rx_mode not in (None,default_rx):record('probe_rx_mode_restored',**await exchange(client,8,default_rx))
                if state['lab_protocol_revision']>=12 and args.lead_us:record('probe_tx_lead_restored',**await exchange(client,11,0))
    finally:
        if args.output:
            with Path(args.output).open('x',encoding='utf-8') as log:
                for row in events:log.write(json.dumps(row,ensure_ascii=False)+'\n')


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('command',choices=COMMANDS,default='status',nargs='?')
    p.add_argument('--mode',type=int,choices=range(1,11),default=1)
    p.add_argument('--rx-mode',type=int,choices=(0,1,2),help='Revision6 only:0=EOPF zero,1=stock no-write,2=legacy1400')
    p.add_argument('--lead-us',type=int,choices=range(0,141,4),help='LAB12 volatile TX advance,0..140us in4us steps')
    p.add_argument('--output',help='New JSONL capture file; existing files are never overwritten')
    args=p.parse_args()
    if args.output and Path(args.output).exists():p.error('Capture file already exists')
    try:asyncio.run(run(args))
    except (Exception,KeyboardInterrupt) as e:p.exit(1,f'{type(e).__name__}: {e}\n')

if __name__=='__main__':main()
