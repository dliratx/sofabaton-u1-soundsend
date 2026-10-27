"""HW3/V32 experimental U1 updater. Default: inspect local files, no Bluetooth.

--check-device only reads identity. --execute is a real firmware replacement;
it requires the exact package hash and explicit acceptance of the V32 experiment.
This is not a firmware33 backup/recovery tool. Never touches SoundSend.
"""
import argparse,asyncio,hashlib,json
from datetime import datetime,timezone
from pathlib import Path
from firmware.build_ota_lab import PACKAGE,parse_package,data_request,APPLICATION_END
from u1_transport import open_remote,read_identity

ROOT=Path(__file__).resolve().parent
SERVICE='0000fdf2-0000-1000-8000-00805f9b34fb'
CMD='0000fa11-0000-1000-8000-00805f9b34fb'
DATA='0000fa10-0000-1000-8000-00805f9b34fb'
ERROR='0000fa13-0000-1000-8000-00805f9b34fb'


def validated_package(expected=None):
    package=PACKAGE.read_bytes();application=parse_package(package)
    actual=hashlib.sha256(package).hexdigest()
    plan=json.loads((ROOT/'firmware/u1-lab-ota-plan.json').read_text())
    manifest=json.loads((ROOT/'firmware/u1-v32-lab-manifest.json').read_text())
    if actual!=plan['package_sha256'] or (expected is not None and actual!=expected.lower()):
        raise ValueError('Package SHA-256 mismatch; Bluetooth was not connected')
    raw=(ROOT/'firmware/u1-v32-lab-offline.bin').read_bytes()
    if hashlib.sha256(raw).hexdigest()!=manifest['raw_image_sha256'] or raw[:APPLICATION_END]!=application:
        raise ValueError('Package, image, and manifest are inconsistent')
    daily=({17:'0x544d5248',18:'0x544d5249',19:'0x544d524a'}.get(manifest.get('lab_protocol_revision'))==manifest.get('lab_build_id') and
           manifest.get('lab_protocol_revision') in (17,18,19) and
           manifest.get('boot_radio_configuration')=={'tx_mode':10,'rx_clear_mode':2,'tx_lead_us':72})
    if (not manifest['lab_disabled_on_reset'] and not daily) or manifest['soundSend_firmware_modified']:
        raise ValueError('Unexpected boot configuration or SoundSend firmware change')
    reports=('arm-test-results.json','central-arm-test-results.json','ble-packet-arm-test-results.json','u1-lab-test-results.json','ota-arm-test-results.json','ota-transport-test-results.json')
    for name in reports:
        report=json.loads((ROOT/'firmware'/name).read_text())
        if not report['passed']:raise ValueError(f'Failed tests: {name}')
        if 'package_sha256' in report and report['package_sha256']!=actual:raise ValueError('OTA test belongs to another package')
        if 'image_sha256' in report and report['image_sha256']!=manifest['raw_image_sha256']:raise ValueError('Integration test belongs to another image')
    return application,plan


class Channel:
    def __init__(self,client,record):
        self.client=client;self.record=record;self.received=asyncio.Queue();self.started=[]
        service=client.services.get_service(SERVICE)
        self.cmd=service.get_characteristic(CMD) if service else None
        self.data=service.get_characteristic(DATA) if service else None
        self.error=service.get_characteristic(ERROR) if service else None
        if not self.cmd or not self.data or not self.error:raise RuntimeError('Expected OTA service is missing')
        if not {'write','indicate'}.issubset(self.cmd.properties) or not {'notify','write-without-response'}.issubset(self.data.properties):
            raise RuntimeError('Unexpected OTA properties')
    async def start(self):
        for label,characteristic in (('command',self.cmd),('data',self.data),('error',self.error)):
            def notify(characteristic,data,label=label):
                raw=bytes(data);self.record('ota_rx',channel=label,hex=raw.hex(' '));self.received.put_nowait((label,raw))
            await self.client.start_notify(characteristic,notify);self.started.append(characteristic)
    async def stop(self):
        if self.client.is_connected:
            for characteristic in reversed(self.started):await self.client.stop_notify(characteristic)
        self.started=[]
    async def wait(self,label):
        async with asyncio.timeout(8):
            while True:
                kind,packet=await self.received.get()
                if kind=='error':raise RuntimeError(f'Device OTA error: {packet.hex(" ")}')
                if kind!=label:raise RuntimeError(f'Out-of-order OTA response: {kind}, {packet.hex(" ")}')
                return packet
    async def command(self,opcode,allowed=(1,)):
        if opcode not in (1,2,3,4,5,6,7,11):raise ValueError('Command is outside the bounded procedure')
        packet=bytes([opcode,0]);self.record('ota_control_tx',hex=packet.hex(' '))
        await self.client.write_gatt_char(self.cmd,packet,response=True)
        response=await self.wait('command')
        if len(response)<4 or response[:1]!=b'\x08' or response[1]!=len(response)-2 or response[2]!=opcode or response[3] not in allowed:
            raise RuntimeError(f'Unexpected OTA response for {opcode}: {response.hex(" ")}')
        return response
    async def information(self):
        for op in (1,2,3):
            response=await self.command(op)
            if response[4:]!=bytes(4):raise RuntimeError('OTA identity differs from the captured identity of this U1')
    async def send_data(self,address,payload):
        packet=data_request(address,payload);self.record('ota_data_tx',address=address,hex=packet.hex(' '))
        await self.client.write_gatt_char(self.data,packet,response=False)
        response=await self.wait('data')
        if response!=address.to_bytes(3,'little')+b'\x01':
            raise RuntimeError(f'Invalid OTA acknowledgement at {address:#x}: {response.hex(" ")}')
        # No retries: this stock data handler rejects duplicate addresses once
        # its cursor advances. An ambiguous acknowledgement stops the upload.


async def finish_commit_link(channel,record):
    """Ask the device to terminate its link after commit, before Windows closes.

    V32 applies the acknowledged staging when its actual link ends. Closing a
    Windows GATT client alone can leave that link alive and delay activation.
    """
    # ACK7 confirms command acceptance, not completion of foreground dispatch.
    # On the real U1 a re-arm7 closed the link by itself during this interval.
    record('commit_settle_wait',seconds=1)
    await asyncio.sleep(1)
    if channel.client.is_connected:
        try:
            await channel.command(6);record('commit_link_termination_acknowledged')
        except Exception as error:
            record('commit_link_termination_ack_missing',error=f'{type(error).__name__}: {error}')
    else:record('commit_link_already_disconnected')


async def execute(args,application,plan):
    destination=Path(args.output);destination.parent.mkdir(parents=True,exist_ok=True)
    with destination.open('x',encoding='utf-8') as log:
        def record(event,**fields):
            row={'time':datetime.now(timezone.utc).isoformat(),'event':event,**fields}
            log.write(json.dumps(row,ensure_ascii=False)+'\n');log.flush()
            if event!='ota_rx' and event!='ota_data_tx':print(json.dumps(row,ensure_ascii=False),flush=True)
        record('start',operation='experimental_u1_firmware_replacement',package_sha256=plan['package_sha256'],
               exact_firmware33_backup=False,recovery_verified=False)
        try:
            async with open_remote() as client:
                identity=await read_identity(client);record('identity',**identity)
                if identity['firmware'] not in (32,33):raise RuntimeError('Installed firmware version is outside the experiment')
                if identity['battery']<80:raise RuntimeError('Battery level is below the threshold for this firmware experiment')
                channel=Channel(client,record);await channel.start()
                await channel.information()
                await channel.command(11,(1,6))
                record('ota_loop_requested',active_application_commit_sent=False)
                # Stock V32 enters a dedicated OTA loop after disconnection and
                # erases the staging region before advertising again.
            await asyncio.sleep(3)
            async with open_remote(timeout=35) as client:
                channel=Channel(client,record);await channel.start()
                await channel.command(11,(1,))
                await channel.information();await channel.command(4)
                for address in range(0,APPLICATION_END,16):
                    await channel.send_data(address,application[address:address+16])
                    if (address+16)%2048==0:
                        print(f'Transferencia U1: {(address+16)*100/APPLICATION_END:.1f}%',flush=True)
                record('all_data_acknowledged',packets=APPLICATION_END//16)
                # The descriptor was in the last page. From this point an
                # external power cycle may also activate the staged image.
                await channel.command(5);await channel.command(7)
                record('commit_acknowledged',firmware_running_verified=False)
                await finish_commit_link(channel,record)
            # Installation is not reported successful until a new connection
            # proves both the expected application and custom lab protocol.
            await asyncio.sleep(5)
            async with open_remote(timeout=35) as client:
                identity=await read_identity(client)
                if identity['firmware']!=32:raise RuntimeError(f'Unexpected firmware version after OTA: {identity}')
                from u1_debug import exchange
                state=await exchange(client,1)
                daily=not plan['lab_disabled_on_reset']
                if state['enabled']!=daily or state['phase']!='idle':raise RuntimeError('Unexpected firmware boot state')
                if daily:
                    clock=await exchange(client,5);radio=await exchange(client,7)
                    if state['lab_protocol_revision']!=plan['lab_protocol_revision'] or clock['build_id']!=plan['lab_build_id'] or state['tx_mode']!=10 or radio['rx_clear_mode']!=2 or radio['tx_lead_us']!=72:
                        raise RuntimeError('Unexpected daily build or radio defaults')
                record('custom_lab_boot_verified',identity=identity,state=state,
                       physical_soundsend_control_verified=False)
        except BaseException as e:
            record('error',error=f'{type(e).__name__}: {e}',automatic_retry=False)
            raise
        finally:record('end')


async def check_device():
    async with open_remote() as client:print(json.dumps(await read_identity(client),ensure_ascii=False,indent=2))


def main():
    p=argparse.ArgumentParser(description=__doc__)
    action=p.add_mutually_exclusive_group()
    action.add_argument('--check-device',action='store_true',help='Read identity only; do not enter OTA mode')
    action.add_argument('--execute',action='store_true',help='Replace U1 firmware with the V32-based experiment')
    p.add_argument('--expected-sha256')
    p.add_argument('--accept-v32-experiment',action='store_true',help='Explicit acceptance: replaces V33 without an exact backup or proven recovery')
    p.add_argument('--output',help='A new JSONL receipt is required for an actual installation')
    args=p.parse_args()
    if args.execute and (not args.expected_sha256 or not args.accept_v32_experiment or not args.output):
        p.error('--execute requires --expected-sha256, --accept-v32-experiment and --output')
    if args.execute and Path(args.output).exists():p.error('Installation receipt already exists')
    try:
        application,plan=validated_package(args.expected_sha256)
        if args.execute:asyncio.run(execute(args,application,plan))
        elif args.check_device:asyncio.run(check_device())
        else:print(json.dumps(plan,ensure_ascii=False,indent=2))
    except (Exception,KeyboardInterrupt) as e:p.exit(1,f'{type(e).__name__}: {e}\n')

if __name__=='__main__':main()
