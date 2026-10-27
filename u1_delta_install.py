"""Incremental correction of the individually verified installed U1 LAB.

Default is offline validation. --execute requires an exact delta hash and log.
Reuses the user's accepted experimental U1-install scope; never touches SoundSend.
"""
import argparse,asyncio,json
from contextlib import asynccontextmanager
from pathlib import Path
from datetime import datetime,timezone
from firmware.build_delta_lab import ROOT as FWROOT,STATE,PACKAGE,PLAN,APP_END,PAGE_END,parse_delta,digest
from u1_ota_install import Channel,finish_commit_link,validated_package
from u1_transport import open_remote,read_identity,APP_NOTIFY,APP_WRITE
from u1_debug import exchange

ROOT=Path(__file__).resolve().parent


@asynccontextmanager
async def selected_connection(existing,address,timeout=45):
    if existing is None:
        async with open_remote(timeout=timeout,address=address) as client:yield client
    else:
        try:yield existing
        finally:
            if existing.is_connected:await existing.disconnect()


def validated_delta(expected=None):
    package=PACKAGE.read_bytes();application,ranges=parse_delta(package)
    plan=json.loads(PLAN.read_text());state=json.loads(STATE.read_text())
    if digest(package)!=plan['package_sha256'] or (expected and digest(package)!=expected):raise ValueError('Delta hash mismatch')
    before=(FWROOT/state['expected_application_file']).read_bytes()
    if digest(before)!=state['expected_sha256'] or state['expected_sha256']!=plan['base_expected_sha256']:raise ValueError('Recorded installed base changed')
    target=(FWROOT/plan.get('target_application_file','u1-v32-lab-offline.bin')).read_bytes()
    if application[:APP_END]!=target[:APP_END] or digest(application)!=plan['expected_result_sha256']:raise ValueError('Delta target mismatch')
    report=json.loads((FWROOT/'delta-arm-test-results.json').read_text())
    if not report['passed'] or report['package_sha256']!=plan['package_sha256']:raise ValueError('Delta ARM checks missing/stale')
    return application,ranges,plan,state


def validated_full_application(expected=None):
    """Use the original single-region descriptor and rewrite only0..1efff.

    This does not assume that the retained physical FW33 bootloader has every
    multi-region behavior of the emulated public V32 bootloader.
    """
    application,full=validated_package(expected)
    state=json.loads(STATE.read_text());before=(FWROOT/state['expected_application_file']).read_bytes()
    if digest(before)!=state['expected_sha256']:raise ValueError('Recorded current expected image changed')
    manifest=json.loads((FWROOT/'u1-v32-lab-manifest.json').read_text())
    plan={'package':full['package'],'package_sha256':full['package_sha256'],
          'base_expected_sha256':state['expected_sha256'],'current_address':state['current_address'],
          'result_address':manifest['preserved_device_address'],'full_application':True,
          'data_packets':len(application)//16,'expected_result_sha256':digest(application),
          'target_prefix_sha256':digest(application[:APP_END]),'result_poll_dispatch':'lab',
          'result_lab_revision':manifest['lab_protocol_revision'],'result_lab_build_id':manifest['lab_build_id'],
          'result_default_enabled':not manifest['lab_disabled_on_reset'],
          'scope':'Original one-region full application descriptor;0..1efff only. No persistent settings or bootloader transfer.'}
    return application,[(0,len(application))],plan,state


def validate_empty_preparation(receipt_path,state):
    """Only accept the recorded, prepared attempt that never flushed a page.

    The V32 handler programs staging only at offset0x70 in each128-byte page.
    This proof is single-use; a failed normal transfer cannot reuse it.
    """
    path=Path(receipt_path);raw=path.read_bytes()
    rows=[json.loads(line) for line in raw.decode('utf-8').splitlines()]
    start=next((r for r in rows if r['event']=='start'),{})
    if start.get('base_expected_sha256')!=state['expected_sha256'] or start.get('current_address')!=state['current_address']:
        raise ValueError('Preparation belongs to a different installed base/unit')
    if not any(r['event']=='ota_loop_requested' for r in rows):raise ValueError('No staging preparation recorded')
    if not any(r['event']=='ota_rx' and r.get('channel')=='command' and r.get('hex')=='08 02 0b 01' for r in rows):
        raise ValueError('No acknowledgement from the prepared OTA loop')
    sent=[r for r in rows if r['event']=='ota_data_tx']
    if any((r['address']&127)==112 for r in sent) or any(r['event'] in ('all_data_acknowledged','commit_acknowledged') for r in rows):
        raise ValueError('Cannot establish empty staging from this receipt')
    proof=digest(raw);consumption=FWROOT/'installed-history/staging-preparation-consumption.json'
    if consumption.exists() and json.loads(consumption.read_text()).get('proof_sha256')==proof:
        raise ValueError('Staging proof was already consumed; fresh preparation required')
    return {'receipt':str(path),'proof_sha256':proof,'sent_packets_without_page_flush':len(sent)}


async def prepare_staging(args,plan,state):
    """Use the original staging preparation, then disconnect without data/commit.

    Original V32 erases only staging and resets when that connection closes.
    The separate normal-mode upload consumes this new, single-use receipt.
    """
    status=json.loads((ROOT/'research/firmware-analysis/status.json').read_text())
    if status.get('experimental_install_authorization',{}).get('accepted') is not True:raise RuntimeError('Experimental scope not accepted')
    path=Path(args.output);path.parent.mkdir(parents=True,exist_ok=True)
    with path.open('x',encoding='utf-8') as log:
        def record(event,**fields):
            row={'time':datetime.now(timezone.utc).isoformat(),'event':event,**fields}
            log.write(json.dumps(row)+'\n');log.flush()
            if event!='ota_rx':print(json.dumps(row),flush=True)
        record('start',operation='prepare_empty_staging_only',**plan)
        try:
            if args.resume_prepared_from:
                previous=Path(args.resume_prepared_from);raw=previous.read_bytes()
                rows=[json.loads(line) for line in raw.decode().splitlines()]
                start=next((r for r in rows if r['event']=='start'),{})
                normal=next((r for r in rows if r['event']=='normal_lab_verified'),{})
                identity=normal.get('identity',{})
                if (start.get('base_expected_sha256')!=state['expected_sha256'] or
                    start.get('current_address')!=state['current_address'] or
                    identity.get('address')!=state['current_address'] or identity.get('firmware')!=32 or
                    identity.get('hardware')!=3 or identity.get('batch')!='20190404' or
                    not any(r['event']=='ota_loop_requested' for r in rows) or
                    any(r['event'] in ('ota_data_tx','commit_acknowledged') for r in rows)):
                    raise ValueError('Invalid identity/preparation reference')
                age=(datetime.now(timezone.utc)-datetime.fromisoformat(normal['time'])).total_seconds()
                if not 0<=age<600:raise ValueError('Preparation identity reference is not recent')
                record('ota_loop_requested',previous_receipt=str(previous),previous_sha256=digest(raw),identity=identity)
            else:
                async with open_remote(timeout=args.wait_seconds,address=plan['current_address']) as client:
                    identity=await read_identity(client);record('identity',**identity)
                    lab=await verify_dispatch(client,state.get('poll_dispatch','lab'))
                    if state.get('lab_protocol_revision') is not None and lab.get('lab_protocol_revision')!=state['lab_protocol_revision']:
                        raise RuntimeError('Running LAB revision changed; staging preparation stopped')
                    record('normal_lab_verified',identity=identity,state=lab)
                    if identity['address']!=plan['current_address'] or identity['firmware']!=32 or identity['battery']<80:
                        raise RuntimeError('Unexpected preparation identity/battery')
                    channel=Channel(client,record);await channel.start();await channel.information()
                    await channel.command(11,(6,));record('ota_loop_requested')
                    try:await channel.command(6);record('preparation_entry_disconnect_acknowledged')
                    except Exception as error:record('preparation_entry_disconnect_ack_missing',error=f'{type(error).__name__}: {error}')
                await asyncio.sleep(3)
            prepared=False
            for attempt in range(2):
                async with open_remote(timeout=35,address=plan['current_address'],require_advertisement=True) as client:
                    channel=Channel(client,record);await channel.start()
                    await channel.information();reply=await channel.command(11,(1,6))
                    if reply[3]==1:
                        prepared=True;record('staging_prepared',data_packets_sent=0,commit_sent=False,fresh_advertisement=True)
                    else:record('ota_loop_requested',reentry_attempt=attempt+1)
                    try:
                        await channel.command(6)
                        record('preparation_termination_acknowledged' if prepared else 'preparation_entry_disconnect_acknowledged')
                    except Exception as error:
                        record('preparation_disconnect_ack_missing',error=f'{type(error).__name__}: {error}')
                if prepared:break
                await asyncio.sleep(3)
            if not prepared:raise RuntimeError('Prepared OTA loop not confirmed; no firmware data sent')
            await asyncio.sleep(5)
            async with open_remote(timeout=35,address=plan['current_address']) as client:
                identity=await read_identity(client)
                lab=await verify_dispatch(client,state.get('poll_dispatch','lab'),allow_enabled=state.get('default_enabled',False))
                if identity['firmware']!=32:raise RuntimeError('Unexpected identity after preparation reset')
                if lab.get('enabled'):
                    clock=await exchange(client,5)
                    if lab.get('lab_protocol_revision')!=state.get('lab_protocol_revision') or clock['build_id']!=state.get('lab_build_id'):
                        raise RuntimeError('Unexpected daily firmware after preparation reset')
                    lab=await exchange(client,3,0)
                    if lab['enabled'] or lab['phase']!='idle':raise RuntimeError('Could not disable daily keys before transfer')
                    record('daily_keys_disabled_after_preparation_reset',state=lab)
                record('normal_mode_after_preparation_verified',identity=identity,state=lab)
        except BaseException as error:
            record('error',error=f'{type(error).__name__}: {error}');raise
        finally:record('end')


def remember_install(application,identity,plan,path,record):
    saved=FWROOT/'installed-history'/('expected-active-'+plan['expected_result_sha256'][:16]+'.bin')
    with saved.open('xb') as f:f.write(application)
    new_state={'expected_application_file':str(saved.relative_to(FWROOT)),
               'expected_sha256':digest(application),'expected_prefix_sha256':digest(application[:APP_END]),
               'source_receipt':str(path.relative_to(ROOT)) if path.is_absolute() else str(path),
               'current_address':identity['address'],'custom_lab_boot_verified':plan.get('result_poll_dispatch','lab')=='lab',
               'poll_dispatch':plan.get('result_poll_dispatch','lab'),'boot_and_control_plane_verified':True,
              'scope':'Expected bytes from acknowledged OTA and verified custom boot; not a physical dump'}
    if plan.get('result_lab_revision') is not None:new_state['lab_protocol_revision']=plan['result_lab_revision']
    if plan.get('result_lab_build_id') is not None:new_state['lab_build_id']=plan['result_lab_build_id']
    new_state['default_enabled']=plan.get('result_default_enabled',False)
    STATE.write_text(json.dumps(new_state,indent=2)+'\n')
    status_path=ROOT/'research/firmware-analysis/status.json';status=json.loads(status_path.read_text())
    status['current_bluetooth_address']=identity['address'];status['custom_lab_installed']=True
    status['installed_poll_dispatch']=new_state['poll_dispatch']
    status['latest_install_receipt']=str(path);status['current_project_jobs']='Incremental correction, identity and selected control plane verified. Original U1 MAC restored; no BLE link left open.'
    status['installed_expected_application_sha256']=new_state['expected_sha256']
    status['installed_application_prefix_sha256']=new_state['expected_prefix_sha256']
    status_path.write_text(json.dumps(status,indent=2)+'\n')
    record('installed_state_recorded',**new_state)


async def verify_dispatch(client,mode,allow_enabled=False):
    if mode=='lab':
        # Revision1's poll observer can lose a read-only status request to the
        # original echo path. Retry only this nonmutating query, never OTA data.
        for attempt in range(3):
            try:
                state=await exchange(client,1);break
            except TimeoutError:
                if attempt==2 or not client.is_connected:raise
                await asyncio.sleep(0.1)
        if (state['enabled'] and not allow_enabled) or state['phase']!='idle':raise RuntimeError('LAB must be disabled/idle')
        return state
    if mode!='stock':raise ValueError('Unknown poll dispatch')
    from u1_debug import request_packet,READ,WRITE
    q=asyncio.Queue();packet=request_packet(1)
    await client.start_notify(READ,lambda characteristic,data:q.put_nowait(bytes(data)))
    try:
        await client.write_gatt_char(WRITE,packet,response=True)
        raw=await asyncio.wait_for(q.get(),5)
        if raw!=packet:raise RuntimeError('Expected the original stock FFF4/FFF3 echo')
        return {'stock_poll_echo_verified':True,'custom_control_active':False}
    finally:
        if client.is_connected:await client.stop_notify(READ)


async def verify_result_dispatch(client,plan):
    daily=plan.get('result_default_enabled',False)
    state=await verify_dispatch(client,plan.get('result_poll_dispatch','lab'),allow_enabled=daily)
    expected=plan.get('result_lab_revision')
    if expected is not None and state.get('lab_protocol_revision')!=expected:
        raise RuntimeError(f'Installed diagnostic revision does not match candidate: {state}')
    if plan.get('result_poll_dispatch','lab')=='lab':
        if state['enabled']!=daily:raise RuntimeError('Installed boot enable state differs from candidate')
        clock=await exchange(client,5)
        if clock['build_id']!=plan['result_lab_build_id']:raise RuntimeError('Installed build differs from candidate')
        if daily:
            radio=await exchange(client,7)
            if state['tx_mode']!=10 or radio['rx_clear_mode']!=2 or radio['tx_lead_us']!=72:
                raise RuntimeError('Daily boot radio defaults differ from verified configuration')
    return state


async def execute_preconnected(client,args,application,ranges,plan,state):
    """Use normal stock OTA dispatch after a verified empty preparation/reset.

    Caller owns an existing guarded normal-mode LAB connection. No OTA-loop
    request is sent. The actual original normal dispatch is ARM-tested.
    """
    if plan.get('full_application'):validated_full_application(plan['package_sha256'])
    else:validated_delta(plan['package_sha256'])
    proof=validate_empty_preparation(args.prepared_from,state)
    status=json.loads((ROOT/'research/firmware-analysis/status.json').read_text())
    if status.get('experimental_install_authorization',{}).get('accepted') is not True:raise RuntimeError('Experimental scope not accepted')
    path=Path(args.delta_log);path.parent.mkdir(parents=True,exist_ok=True)
    with path.open('x',encoding='utf-8') as log:
        def record(event,**fields):
            row={'time':datetime.now(timezone.utc).isoformat(),'event':event,**fields}
            log.write(json.dumps(row,ensure_ascii=False)+'\n');log.flush()
            if event not in ('ota_rx','ota_data_tx'):print(json.dumps(row,ensure_ascii=True),flush=True)
        record('start',operation='normal_mode_full_application' if plan.get('full_application') else 'normal_mode_incremental_correction',proof=proof,**plan)
        try:
            identity=await read_identity(client);record('identity',**identity)
            lab=await verify_dispatch(client,state.get('poll_dispatch','lab'))
            if state.get('lab_protocol_revision') is not None and lab.get('lab_protocol_revision')!=state['lab_protocol_revision']:
                raise RuntimeError('Running LAB revision changed; firmware transfer stopped')
            record('normal_lab_verified',identity=identity,state=lab)
            if identity['address']!=plan['current_address'] or identity['firmware']!=32 or identity['battery']<80:
                raise RuntimeError('Unexpected normal-mode LAB state')
            channel=Channel(client,record);await channel.start();await channel.information()
            consumption=FWROOT/'installed-history/staging-preparation-consumption.json'
            consumption.write_text(json.dumps({**proof,'consumed':True,'update_log':str(path)},indent=2)+'\n')
            packets=0
            for start,end in ranges:
                await channel.command(4)
                for address in range(start,end,16):
                    await channel.send_data(address,application[address:address+16]);packets+=1
                    if packets%256==0:record('upload_progress',packets=packets,total=plan['data_packets'])
                    await asyncio.sleep(0.01)
                await channel.command(5);record('region_acknowledged',start=start,end_exclusive=end,packets=packets)
            record('all_data_acknowledged',packets=packets)
            await channel.command(7);record('commit_acknowledged',custom_boot_verified=False)
            await finish_commit_link(channel,record)
            if client.is_connected:await client.disconnect()
            await asyncio.sleep(5)
            async with open_remote(timeout=35,address=plan['result_address'],require_advertisement=True) as after:
                identity=await read_identity(after);lab=await verify_result_dispatch(after,plan)
                if identity['firmware']!=32:raise RuntimeError('Unexpected corrected firmware identity')
                record('boot_and_control_plane_verified',identity=identity,state=lab)
                remember_install(application,identity,plan,path,record)
                if getattr(args,'probe_after_install',False):
                    if plan.get('result_poll_dispatch')!='lab':raise RuntimeError('Probe requires LAB dispatch')
                    try:record('probe_requested',**await exchange(after,2))
                    except Exception as error:record('probe_request_error',error=f'{type(error).__name__}: {error}')
            if getattr(args,'probe_after_install',False):
                await asyncio.sleep(6)
                try:
                    async with open_remote(timeout=35,address=plan['result_address']) as after:
                        record('probe_result',**await exchange(after,1))
                        if plan.get('result_lab_revision',0)>=2:
                            clock=await exchange(after,5);record('clock_snapshot',**clock)
                            if clock['build_id']!=plan['result_lab_build_id']:raise RuntimeError('Clock build identifier mismatch')
                        if plan.get('result_lab_revision',0)>=3:record('radio_snapshot',**await exchange(after,6))
                except Exception as error:record('probe_result_error',error=f'{type(error).__name__}: {error}')
        except BaseException as e:
            record('error',error=f'{type(e).__name__}: {e}',automatic_retry=False);raise
        finally:record('end')


async def capture_profiles(client,record):
    buffer=bytearray();count=0
    def receive(characteristic,data):
        nonlocal count
        buffer.extend(data)
        while len(buffer)>=2:
            start=buffer.find(b'\xa5\x5a')
            if start<0:
                buffer[:]=buffer[-1:] if buffer[-1]==0xa5 else b'';return
            if start:del buffer[:start]
            if len(buffer)<4 or len(buffer)<buffer[2]+5:return
            n=buffer[2]+5;p=bytes(buffer[:n]);del buffer[:n]
            valid=sum(p[:-1])&255==p[-1]
            record('frame',opcode=p[3],hex=p.hex(' '),checksum_valid=valid)
            if valid and p[3]==11:count+=1
    await client.start_notify(APP_NOTIFY,receive)
    try:
        await client.write_gatt_char(APP_WRITE,bytes.fromhex('a5 5a 00 0a 09'),response=True)
        await asyncio.sleep(8)
    finally:
        if client.is_connected:await client.stop_notify(APP_NOTIFY)
    record('profile_read_complete',valid_profile_frames=count,conclusive=count>0)


async def execute(args,application,ranges,plan,state,existing_client=None):
    path=Path(args.output);path.parent.mkdir(parents=True,exist_ok=True)
    authorization=json.loads((ROOT/'research/firmware-analysis/status.json').read_text())
    if authorization.get('experimental_install_authorization',{}).get('accepted') is not True:
        raise RuntimeError('Experimental installation acceptance is not recorded')
    with path.open('x',encoding='utf-8') as log:
        def record(event,**fields):
            row={'time':datetime.now(timezone.utc).isoformat(),'event':event,**fields}
            log.write(json.dumps(row,ensure_ascii=False)+'\n');log.flush()
            if event not in ('ota_rx','ota_data_tx','frame'):print(json.dumps(row,ensure_ascii=True),flush=True)
        record('start',operation='incremental_lab_correction',**plan)
        try:
            async with selected_connection(existing_client,plan['current_address'],getattr(args,'wait_seconds',45)) as client:
                identity=await read_identity(client);record('identity',**identity)
                if identity['firmware']!=32 or identity['battery']<80:raise RuntimeError('Unexpected firmware/battery')
                lab=await verify_dispatch(client,state.get('poll_dispatch','lab'));record('control_plane_status',**lab)
                if existing_client is None and not getattr(args,'skip_profile_reread',False):await capture_profiles(client,record)
                else:
                    before=json.loads((ROOT/'captures/u1-device-metadata-01.json').read_text())['device_metadata']
                    after=json.loads((ROOT/'captures/u1-profiles-after-lab-01.json').read_text())['device_metadata']
                    if {x['device_id']:x['raw_record'] for x in before}!={x['device_id']:x['raw_record'] for x in after}:
                        raise RuntimeError('Previously verified profile records differ')
                    record('profiles_previously_verified',capture='captures/u1-profiles-after-lab-01.json',profiles=len(after))
                channel=Channel(client,record);await channel.start();await channel.information()
                await channel.command(11,(1,6));record('ota_loop_requested')
            await asyncio.sleep(3)
            async with open_remote(timeout=35,address=plan['current_address']) as client:
                channel=Channel(client,record);await channel.start()
                await channel.command(11,(1,));await channel.information()
                packets=0
                for start,end in ranges:
                    await channel.command(4)
                    for address in range(start,end,16):
                        await channel.send_data(address,application[address:address+16]);packets+=1
                    await channel.command(5);record('region_acknowledged',start=start,end_exclusive=end)
                record('all_data_acknowledged',packets=packets)
                await channel.command(7);record('commit_acknowledged',custom_boot_verified=False)
                await finish_commit_link(channel,record)
            await asyncio.sleep(5)
            async with open_remote(timeout=35,address=plan['result_address']) as client:
                identity=await read_identity(client);lab=await verify_result_dispatch(client,plan)
                if identity['firmware']!=32:raise RuntimeError('Unexpected post-update firmware')
                record('boot_and_control_plane_verified',identity=identity,state=lab)
                remember_install(application,identity,plan,path,record)
                if getattr(args,'probe_after_install',False):
                    if plan.get('result_poll_dispatch')!='lab':raise RuntimeError('Probe requires LAB dispatch')
                    try:record('probe_requested',**await exchange(client,2))
                    except Exception as error:record('probe_request_error',error=f'{type(error).__name__}: {error}')
            if getattr(args,'probe_after_install',False):
                await asyncio.sleep(6)
                try:
                    async with open_remote(timeout=35,address=plan['result_address']) as client:
                        record('probe_result',**await exchange(client,1))
                        if plan.get('result_lab_revision',0)>=2:
                            clock=await exchange(client,5);record('clock_snapshot',**clock)
                            if clock['build_id']!=plan['result_lab_build_id']:raise RuntimeError('Clock build identifier mismatch')
                        if plan.get('result_lab_revision',0)>=3:record('radio_snapshot',**await exchange(client,6))
                except Exception as error:record('probe_result_error',error=f'{type(error).__name__}: {error}')
        except BaseException as e:
            record('error',error=f'{type(e).__name__}: {e}',automatic_retry=False);raise
        finally:record('end')


def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--execute',action='store_true')
    p.add_argument('--expected-sha256');p.add_argument('--output')
    p.add_argument('--normal-mode',action='store_true');p.add_argument('--prepared-from')
    p.add_argument('--full-application',action='store_true',help='Original single-region full application instead of sparse correction; settings/bootloader stay excluded')
    p.add_argument('--prepare-and-install',action='store_true',help='Run preparation and guarded normal transfer consecutively')
    p.add_argument('--preparation-output',help='New separate log for the preparation phase')
    p.add_argument('--prepare-only',action='store_true',help='Erase staging through original OTA preparation; send no firmware data/commit')
    p.add_argument('--resume-prepared-from',help='Resume a recent, exact-unit preparation that sent no firmware data')
    p.add_argument('--wait-seconds',type=int,default=45)
    p.add_argument('--skip-profile-reread',action='store_true')
    p.add_argument('--probe-after-install',action='store_true');args=p.parse_args()
    if args.execute and (not args.expected_sha256 or not args.output):p.error('Execution requires exact hash and new output log')
    if args.prepare_only and (not args.execute or args.normal_mode):p.error('Preparation requires --execute and cannot use --normal-mode')
    if args.resume_prepared_from and not args.prepare_only:p.error('Resuming preparation requires --prepare-only')
    if args.prepare_and_install and (not args.execute or args.prepare_only or args.resume_prepared_from):p.error('Pipeline requires execution and a fresh preparation')
    if args.prepare_and_install and (not args.preparation_output or Path(args.preparation_output).exists()):p.error('Pipeline requires a new preparation log')
    if args.execute and args.full_application and not (args.prepare_only or args.normal_mode or args.prepare_and_install):p.error('Full application uses the reviewed normal-mode procedure')
    if args.output and Path(args.output).exists():p.error('Log already exists')
    try:
        application,ranges,plan,state=(validated_full_application if args.full_application else validated_delta)(args.expected_sha256)
        if args.prepare_and_install:
            async def pipeline():
                preparation=argparse.Namespace(**vars(args));preparation.output=args.preparation_output
                await prepare_staging(preparation,plan,state)
                args.prepared_from=args.preparation_output;args.delta_log=args.output
                async with open_remote(timeout=args.wait_seconds,address=plan['current_address']) as client:
                    await execute_preconnected(client,args,application,ranges,plan,state)
            asyncio.run(pipeline())
        elif args.prepare_only:asyncio.run(prepare_staging(args,plan,state))
        elif args.execute and args.normal_mode:
            if not args.prepared_from:p.error('Normal mode requires a validated staging preparation receipt')
            args.delta_log=args.output
            async def normal():
                async with open_remote(timeout=args.wait_seconds,address=plan['current_address']) as client:
                    await execute_preconnected(client,args,application,ranges,plan,state)
            asyncio.run(normal())
        elif args.execute:asyncio.run(execute(args,application,ranges,plan,state))
        else:print(json.dumps(plan,indent=2))
    except (Exception,KeyboardInterrupt) as e:p.exit(1,f'{type(e).__name__}: {e}\n')

if __name__=='__main__':main()
