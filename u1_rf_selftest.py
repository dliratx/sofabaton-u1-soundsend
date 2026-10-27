"""Observe a temporary nonconnectable U1 lab beacon through UGREEN.

No OTA, profile changes, SoundSend connection, or volume-setting commands.
Requires LAB10+, disabled volume override. This is development diagnostics.
"""
import argparse,asyncio,json
from datetime import datetime,timezone
from pathlib import Path
from bleak import BleakScanner
from u1_transport import open_remote,read_identity
from u1_debug import exchange

async def run(args):
    with Path(args.output).open('x',encoding='utf-8') as log:
        def record(event,**fields):
            row={'time':datetime.now(timezone.utc).isoformat(),'event':event,**fields}
            log.write(json.dumps(row)+'\n');log.flush();print(json.dumps(row),flush=True)
        seen=[];address=None;scanner=None;callbacks=0;own_advertisements=0
        def detected(device,advertisement):
            nonlocal callbacks,own_advertisements
            callbacks+=1
            if device.address.upper()==address or advertisement.local_name=='SSLAB-RFTEST':
                row={'address':device.address,'name':advertisement.local_name,'rssi':advertisement.rssi}
                own_advertisements+=1;record('candidate_advertisement',**row)
                if device.address.upper()==address and advertisement.local_name=='SSLAB-RFTEST':
                    seen.append(row);record('beacon_observed',**row)
        try:
            async with open_remote() as client:
                identity=await read_identity(client);address=identity['address'].upper()
                state=await exchange(client,1);record('identity_and_status',identity=identity,state=state)
                if state['lab_protocol_revision']<10 or state['enabled'] or state['phase']!='idle':
                    raise RuntimeError('Requires disabled idle LAB10+; no self-test requested')
                if args.mode>7 and state['lab_protocol_revision']<11:
                    raise RuntimeError('Bit-order comparison requires LAB11; no self-test requested')
                if args.lead_us and state['lab_protocol_revision']<12:raise RuntimeError('TX advance requires LAB12')
                selected=await exchange(client,4,args.mode);record('mode_selected',**selected)
                if state['lab_protocol_revision']>=13:
                    record('raw_mode_definition',mode=args.mode,definition={7:'stock_delay',8:'PDL_bits_cleared',9:'TDL_bits_cleared',10:'PDL_and_TDL_bits_cleared'}[args.mode])
                if selected['tx_mode']!=args.mode:raise RuntimeError('Requested radio mode was not selected')
                if state['lab_protocol_revision']>=12:record('tx_lead_selected',**await exchange(client,11,args.lead_us))
                scanner=BleakScanner(detection_callback=detected,scanning_mode='passive')
                await scanner.start();record('air_listener_started',listen_seconds=args.listen_seconds)
                await asyncio.sleep(1)
                try:record('rf_test_requested',changes_audio=False,**await exchange(client,12))
                except Exception as error:record('request_ack_missing',error=f'{type(error).__name__}: {error}')
            await asyncio.sleep(args.listen_seconds)
            await scanner.stop();scanner=None
            record('air_result',beacon_seen=bool(seen),observations=len(seen),
                   all_scanner_callbacks=callbacks,candidate_advertisements=own_advertisements)
            async with open_remote(timeout=35) as client:
                state=await exchange(client,1);record('status_after_test',**state)
                record('clock_snapshot',**await exchange(client,5));record('radio_snapshot',**await exchange(client,6))
                record('tx_snapshot',**await exchange(client,7));record('radio_config',**await exchange(client,9))
                record('modem_config',**await exchange(client,10))
                if state['lab_protocol_revision']>=12:record('delay_config',**await exchange(client,13))
                record('mode_restored',**await exchange(client,4,1))
                if state['lab_protocol_revision']>=12 and args.lead_us:record('tx_lead_restored',**await exchange(client,11,0))
        except BaseException as error:
            record('error',error=f'{type(error).__name__}: {error}');raise
        finally:
            if scanner is not None:await scanner.stop()
            record('end')

if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--output',required=True)
    p.add_argument('--mode',type=int,choices=range(7,11),default=7)
    p.add_argument('--lead-us',type=int,choices=range(0,141,4),default=0)
    p.add_argument('--listen-seconds',type=int,choices=range(6,31),default=20);args=p.parse_args()
    if Path(args.output).exists():p.error('Capture already exists')
    try:asyncio.run(run(args))
    except (Exception,KeyboardInterrupt) as error:p.exit(1,f'{type(error).__name__}: {error}\n')
