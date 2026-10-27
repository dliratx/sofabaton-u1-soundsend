"""Finish one exact, completely acknowledged U1 update; no firmware data/erase.

Default validates the saved transfer offline. Execution can re-arm its commit
and request vendor device-side disconnect, then require the new build identity.
"""
import argparse,asyncio,json
from pathlib import Path
from datetime import datetime,timezone
from u1_delta_install import validated_delta,validated_full_application,verify_dispatch,verify_result_dispatch,remember_install
from u1_ota_install import Channel
from u1_transport import open_remote,read_identity
from u1_debug import exchange
from firmware.build_ota_lab import data_request
from firmware.build_delta_lab import digest

ROOT=Path(__file__).resolve().parent

def validate(args):
    validator=validated_full_application if getattr(args,'full_application',False) else validated_delta
    application,ranges,plan,state=validator(args.expected_sha256)
    raw=Path(args.receipt).read_bytes();rows=[json.loads(line) for line in raw.decode().splitlines()]
    start=next(r for r in rows if r['event']=='start')
    if start['package_sha256']!=plan['package_sha256'] or start['base_expected_sha256']!=state['expected_sha256']:
        raise ValueError('Prior acknowledged transfer belongs to another package/base')
    sent=[r for r in rows if r['event']=='ota_data_tx']
    addresses=[a for first,end in ranges for a in range(first,end,16)]
    if [r['address'] for r in sent]!=addresses:raise ValueError('Prior transfer is incomplete/out of sequence')
    for row in sent:
        a=row['address']
        if bytes.fromhex(row['hex'])!=data_request(a,application[a:a+16]):raise ValueError('Prior data differs from this candidate')
    acknowledgements=[bytes.fromhex(r['hex']) for r in rows if r['event']=='ota_rx' and r.get('channel')=='data']
    if acknowledgements!=[a.to_bytes(3,'little')+b'\x01' for a in addresses]:
        raise ValueError('Prior transfer lacks the exact complete ordered data acknowledgements')
    if not any(r['event']=='all_data_acknowledged' and r['packets']==plan['data_packets'] for r in rows):raise ValueError('No complete data acknowledgement')
    if not any(r['event']=='commit_acknowledged' for r in rows):raise ValueError('No prior commit acknowledgement')
    if not any(r['event']=='ota_rx' and r.get('channel')=='command' and r.get('hex')=='08 02 07 01' for r in rows):
        raise ValueError('Prior commit has no matching device acknowledgement')
    return application,plan,state,{'receipt':args.receipt,'receipt_sha256':digest(raw),'data_packets':len(sent)}

async def run(args,application,plan,state,proof):
    accepted=json.loads((ROOT/'research/firmware-analysis/status.json').read_text())['experimental_install_authorization']['accepted']
    if not accepted:raise RuntimeError('Experimental U1 scope not accepted')
    with Path(args.output).open('x',encoding='utf-8') as log:
        def record(event,**fields):
            row={'time':datetime.now(timezone.utc).isoformat(),'event':event,**fields}
            log.write(json.dumps(row)+'\n');log.flush()
            if event!='ota_rx':print(json.dumps(row),flush=True)
        record('start',operation='verify_exact_acknowledged_candidate_only' if args.verify_only else 'complete_exact_acknowledged_pending_commit',proof=proof,package_sha256=plan['package_sha256'])
        try:
            already=False
            async with open_remote(timeout=args.wait_seconds,address=plan['current_address']) as client:
                identity=await read_identity(client);lab=await verify_dispatch(client,'lab',allow_enabled=args.verify_only)
                record('identity_and_status',identity=identity,state=lab)
                if identity['firmware']!=32 or identity['battery']<80:raise RuntimeError('Unexpected firmware/battery')
                if args.verify_only:
                    if lab['lab_protocol_revision']!=plan['result_lab_revision']:
                        raise RuntimeError(f'Candidate is not running; no OTA controls sent: {lab}')
                    clock=await exchange(client,5)
                    if clock['build_id']!=plan['result_lab_build_id']:raise RuntimeError('Build identifier does not match candidate')
                    record('boot_and_control_plane_verified',identity=identity,state=lab,clock=clock,ota_controls_sent=False)
                    remember_install(application,identity,plan,Path(args.output),record)
                    return
                if lab['lab_protocol_revision']==plan['result_lab_revision']:already=True
                elif lab['lab_protocol_revision']==state['lab_protocol_revision']:
                    channel=Channel(client,record);await channel.start()
                    await channel.command(7);record('same_staging_commit_rearmed',firmware_data_sent=0)
                    # Let the original foreground/indication dispatch finish
                    # before issuing a second control command on the same link.
                    await asyncio.sleep(1)
                    if client.is_connected:
                        try:await channel.command(6);record('device_disconnect_acknowledged')
                        except Exception as error:record('device_disconnect_ack_missing',error=f'{type(error).__name__}: {error}')
                else:raise RuntimeError('Unexpected running LAB revision')
            await asyncio.sleep(2 if already else 5)
            async with open_remote(timeout=35,address=plan['result_address']) as client:
                identity=await read_identity(client);lab=await verify_result_dispatch(client,plan)
                clock=await exchange(client,5)
                if clock['build_id']!=plan['result_lab_build_id']:raise RuntimeError('Build identifier does not match candidate')
                record('boot_and_control_plane_verified',identity=identity,state=lab,clock=clock)
                remember_install(application,identity,plan,Path(args.output),record)
                if args.probe:
                    try:record('probe_requested',**await exchange(client,2))
                    except Exception as error:record('probe_request_error',error=f'{type(error).__name__}: {error}')
            if args.probe:
                await asyncio.sleep(6)
                try:
                    async with open_remote(timeout=35,address=plan['result_address']) as client:
                        record('probe_result',**await exchange(client,1))
                        record('clock_snapshot',**await exchange(client,5))
                        record('radio_snapshot',**await exchange(client,6))
                except Exception as error:record('probe_result_error',error=f'{type(error).__name__}: {error}')
        except BaseException as error:record('error',error=f'{type(error).__name__}: {error}');raise
        finally:record('end')

def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--expected-sha256',required=True)
    p.add_argument('--receipt',required=True);p.add_argument('--output');p.add_argument('--execute',action='store_true');p.add_argument('--probe',action='store_true')
    p.add_argument('--wait-seconds',type=int,default=35)
    p.add_argument('--full-application',action='store_true',help='Validate a full original one-region acknowledged transfer')
    p.add_argument('--verify-only',action='store_true',help='Only read identity/build; never re-arm commit or send OTA controls')
    args=p.parse_args()
    if args.execute and (not args.output or Path(args.output).exists()):p.error('Execution requires a new output log')
    try:
        application,plan,state,proof=validate(args)
        if args.execute:asyncio.run(run(args,application,plan,state,proof))
        else:print(json.dumps({'validated':True,'proof':proof,'expected_revision':plan['result_lab_revision']},indent=2))
    except (Exception,KeyboardInterrupt) as error:p.exit(1,f'{type(error).__name__}: {error}\n')

if __name__=='__main__':main()
