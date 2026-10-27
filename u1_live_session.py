"""Temporary U1 lab session: read profiles and keep one guarded BLE link open.

No OTA, flash, key/profile writes, audio bridge or arbitrary memory commands.
Probe/enable use the same bounded debug protocol as u1_debug.py.
"""
import argparse,asyncio,json,sys,threading
from datetime import datetime,timezone
from pathlib import Path
from u1_transport import open_remote,read_identity,APP_NOTIFY,APP_WRITE
from u1_debug import exchange
from u1_delta_install import verify_dispatch
from firmware.build_delta_lab import STATE


async def run(args):
    destination=Path(args.output);destination.parent.mkdir(parents=True,exist_ok=True)
    with destination.open('x',encoding='utf-8') as log:
        def record(event,**fields):
            row={'time':datetime.now(timezone.utc).isoformat(),'event':event,**fields}
            log.write(json.dumps(row,ensure_ascii=False)+'\n');log.flush()
            if event!='frame' or fields.get('opcode')==2:print(json.dumps(row,ensure_ascii=True),flush=True)
        commands=asyncio.Queue();loop=asyncio.get_running_loop()
        def reader():
            for line in sys.stdin:loop.call_soon_threadsafe(commands.put_nowait,line.strip())
            loop.call_soon_threadsafe(commands.put_nowait,'quit')
        threading.Thread(target=reader,daemon=True).start()
        buffer=bytearray();frames=0
        def notify(characteristic,data):
            nonlocal frames
            buffer.extend(data)
            while len(buffer)>=2:
                start=buffer.find(b'\xa5\x5a')
                if start<0:
                    buffer[:]=buffer[-1:] if buffer[-1]==0xa5 else b'';return
                if start:del buffer[:start]
                if len(buffer)<4 or len(buffer)<buffer[2]+5:return
                n=buffer[2]+5;frame=bytes(buffer[:n]);del buffer[:n]
                valid=sum(frame[:-1])&255==frame[-1]
                frames+=1;record('frame',hex=frame.hex(' '),opcode=frame[3],checksum_valid=valid)
        async def query(client,opcode,payload=b'',seconds=8):
            p=bytes([0xa5,0x5a,len(payload),opcode])+payload;p+=bytes([sum(p)&255])
            before=frames;record('tx_query',opcode=opcode,hex=p.hex(' '))
            await client.write_gatt_char(APP_WRITE,p,response=True);await asyncio.sleep(seconds)
            record('query_complete',opcode=opcode,frames=frames-before)
        try:
            async with open_remote(timeout=45) as client:
                identity=await read_identity(client);record('identity',**identity)
                if identity['firmware']!=32:raise RuntimeError('Expected verified V32 lab')
                mode=json.loads(STATE.read_text()).get('poll_dispatch','lab') if STATE.exists() else 'lab'
                record('status',**await verify_dispatch(client,mode))
                await client.start_notify(APP_NOTIFY,notify)
                await query(client,10)
                record('ready',commands='status, profiles, keys, probe, enable, disable, tx1/tx2/tx3, delta, delta-loop, quit',poll_dispatch=mode)
                while client.is_connected:
                    try:command=await asyncio.wait_for(commands.get(),4)
                    except TimeoutError:
                        # Use the app's real identity query as keepalive. A
                        # vendor debug echo alone need not refresh app activity.
                        await query(client,1,seconds=0.5)
                        if mode=='lab':record('heartbeat',**await verify_dispatch(client,mode))
                        else:record('heartbeat',app_identity_query=True)
                        continue
                    if command=='quit':break
                    if command=='status':record('status',**await verify_dispatch(client,mode))
                    elif command=='profiles':await query(client,10)
                    elif command=='keys':
                        for device_id in (1,2,4,3):await query(client,12,bytes([device_id,255]),35)
                    elif command in ('probe','enable','disable'):
                        if mode!='lab':record('unsupported_in_stock_dispatch',command=command);continue
                        op=2 if command=='probe' else 3;argument=1 if command=='enable' else 0
                        record(command,**await exchange(client,op,argument))
                        if command!='disable':break
                    elif command in ('tx1','tx2','tx3'):record('tx_mode',**await exchange(client,4,int(command[-1])))
                    elif command=='delta':
                        if not args.prepared_from or not args.delta_log:raise RuntimeError('Delta requires preparation receipt and update log arguments')
                        from u1_delta_install import validated_delta,execute_preconnected
                        application,ranges,plan,state=validated_delta()
                        await client.stop_notify(APP_NOTIFY)
                        await execute_preconnected(client,args,application,ranges,plan,state)
                        break
                    elif command=='delta-loop':
                        if not args.delta_log:raise RuntimeError('Delta update log path required')
                        from u1_delta_install import validated_delta,execute
                        from types import SimpleNamespace
                        application,ranges,plan,state=validated_delta()
                        await client.stop_notify(APP_NOTIFY)
                        await execute(SimpleNamespace(output=args.delta_log),application,ranges,plan,state,existing_client=client)
                        break
                    else:record('unknown_command',command=command)
        except BaseException as e:
            record('error',error=f'{type(e).__name__}: {e}');raise
        finally:record('end',frames=frames)


def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--output',required=True)
    p.add_argument('--prepared-from');p.add_argument('--delta-log')
    args=p.parse_args()
    if Path(args.output).exists():p.error('Capture already exists')
    try:asyncio.run(run(args))
    except (Exception,KeyboardInterrupt) as e:p.exit(1,f'{type(e).__name__}: {e}\n')

if __name__=='__main__':main()
