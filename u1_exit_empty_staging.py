"""Exit an interrupted, individually identified EMPTY staging session.

Only vendor disconnect6 is sent. No data, erase request, or commit is issued.
The referenced receipt must prove this unit/base and contain no firmware data.
"""
import argparse,asyncio,json
from pathlib import Path
from datetime import datetime,timezone
from u1_transport import open_remote,read_identity
from u1_ota_install import Channel
from u1_debug import exchange

async def run(args):
    state=json.loads(Path('firmware/installed-history/installed-state.json').read_text())
    rows=[json.loads(x) for x in Path(args.receipt).read_text().splitlines()]
    start=next((r for r in rows if r['event']=='start'),{})
    normal=next((r for r in rows if r['event']=='normal_lab_verified'),{})
    identity=normal.get('identity',{})
    if (start.get('operation')!='prepare_empty_staging_only' or
        start.get('base_expected_sha256')!=state['expected_sha256'] or
        identity.get('address')!=state['current_address'] or identity.get('hardware')!=3 or
        identity.get('batch')!='20190404' or identity.get('firmware')!=32 or
        normal.get('state',{}).get('lab_protocol_revision')!=state['lab_protocol_revision'] or
        not any(r['event']=='ota_loop_requested' for r in rows) or
        any(r['event'] in ('ota_data_tx','all_data_acknowledged','commit_acknowledged') for r in rows)):
        raise ValueError('Receipt does not establish empty staging for current exact U1/base')
    age=(datetime.now(timezone.utc)-datetime.fromisoformat(normal['time'])).total_seconds()
    if not 0<=age<600:raise ValueError('Identity proof must be less than10minutes old')
    with Path(args.output).open('x',encoding='utf-8') as log:
        def record(event,**fields):
            row={'time':datetime.now(timezone.utc).isoformat(),'event':event,**fields}
            log.write(json.dumps(row)+'\n');log.flush();print(json.dumps(row),flush=True)
        record('start',receipt=args.receipt,address=state['current_address'],firmware_data_sent=False,commit_sent=False)
        try:
            async with open_remote(timeout=25,address=state['current_address']) as client:
                channel=Channel(client,record);await channel.start()
                try:await channel.command(6);record('disconnect_acknowledged')
                except Exception as error:record('disconnect_ack_missing',error=f'{type(error).__name__}: {error}')
            await asyncio.sleep(5)
            async with open_remote(timeout=25,address=state['current_address']) as client:
                actual=await read_identity(client);lab=await exchange(client,1)
                if actual['address']!=state['current_address'] or lab['lab_protocol_revision']!=state['lab_protocol_revision']:
                    raise RuntimeError('Original current LAB could not be verified')
                record('normal_lab_verified',identity=actual,state=lab)
        except BaseException as error:
            record('error',error=f'{type(error).__name__}: {error}');raise
        finally:record('end')

if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--receipt',required=True);p.add_argument('--output',required=True)
    args=p.parse_args()
    if Path(args.output).exists():p.error('Capture already exists')
    try:asyncio.run(run(args))
    except (Exception,KeyboardInterrupt) as error:p.exit(1,f'{type(error).__name__}: {error}\n')
