"""Exact-device guards and the known read-only U1 identity query."""
import asyncio
import json
from contextlib import asynccontextmanager,AsyncExitStack
from pathlib import Path

ADDRESS='20:21:03:00:2E:AB'
NAME='SB202103002EAB'
MODEL='MD8107M'
APP_SERVICE='0000fff0-0000-1000-8000-00805f9b34fb'
APP_NOTIFY='0000fff1-0000-1000-8000-00805f9b34fb'
APP_WRITE='0000fff2-0000-1000-8000-00805f9b34fb'


def known_targets():
    """Allow the original unit and its individually verified post-OTA identity.

    This does not accept arbitrary SB devices. The additional exact address
    must have the saved HW3/batch/V32/custom-lab verification receipt.
    """
    targets={ADDRESS:NAME}
    receipt=Path(__file__).resolve().parent/'captures/u1-lab-new-address-verification-01.json'
    if receipt.exists():
        r=json.loads(receipt.read_text(encoding='utf-8'));identity=r.get('identity',{})
        address=r.get('address','').upper();name=r.get('name')
        if (r.get('custom_lab_boot_verified') is True and r.get('model')==MODEL and
            identity.get('hardware')==3 and identity.get('batch')=='20190404' and identity.get('firmware')==32 and
            identity.get('address','').upper()==address and name=='SB'+address.replace(':','') and
            r.get('debug_status',{}).get('lab_protocol_revision')==1):
            targets[address]=name
        else:raise RuntimeError('Invalid post-update identity receipt; no additional device accepted')
    return targets


@asynccontextmanager
async def open_remote(timeout=20,address=None,require_advertisement=False):
    from bleak import BleakClient,BleakScanner
    from bleak.backends.device import BLEDevice
    from bleak.exc import BleakError
    from ble_probe import adapter_identity
    await adapter_identity('VID_33FA&PID_0012')
    targets=known_targets()
    if address is None:
        status_path=Path(__file__).resolve().parent/'research/firmware-analysis/status.json'
        status=json.loads(status_path.read_text(encoding='utf-8')) if status_path.exists() else {}
        address=status.get('current_bluetooth_address') or next(reversed(targets))
    address=address.upper()
    if address not in targets:raise RuntimeError('Requested Bluetooth address is not an individually verified U1 target')
    device=await BleakScanner.find_device_by_address(address,timeout=timeout if require_advertisement else min(timeout,5))
    if require_advertisement and device is None:raise RuntimeError('No fresh U1 advertisement after requested link transition')
    async with AsyncExitStack() as connections:
        if device is None:
            # WinRT can retain an exact known unit's ACL/GATT session after a
            # client closes. Direct addressing does not invent scan evidence.
            # OTA transitions can explicitly require a fresh advertisement.
            print('No fresh U1 advertisement; trying its individually verified Bluetooth address directly.',flush=True)
            known=BleakClient(BLEDevice(address,targets[address],None),timeout=20,winrt={'use_cached_services':False})
            try:client=await connections.enter_async_context(known)
            except (TimeoutError,OSError,BleakError):
                print('Direct connection unavailable; waiting for a fresh U1 advertisement.',flush=True)
                device=await BleakScanner.find_device_by_address(address,timeout=max(1,timeout-5))
                if device is None:raise RuntimeError('U1 unavailable; close the phone app and activate Pairing with – and E')
                client=await connections.enter_async_context(BleakClient(device,timeout=20,winrt={'use_cached_services':False}))
        else:client=await connections.enter_async_context(BleakClient(device,timeout=20,winrt={'use_cached_services':False}))
        name=bytes(await client.read_gatt_char('00002a00-0000-1000-8000-00805f9b34fb')).decode('utf-8')
        model=bytes(await client.read_gatt_char('00002a24-0000-1000-8000-00805f9b34fb')).decode('utf-8')
        if name!=targets[address] or model!=MODEL:raise RuntimeError(f'Unexpected GATT identity: {name!r}, {model!r}')
        yield client


async def read_identity(client):
    service=client.services.get_service(APP_SERVICE)
    receive=service.get_characteristic(APP_NOTIFY) if service else None
    transmit=service.get_characteristic(APP_WRITE) if service else None
    if receive is None or transmit is None:raise RuntimeError('U1 configuration channel is missing')
    buffer=bytearray();received=asyncio.Queue()
    def notify(characteristic,data):
        buffer.extend(data)
        while len(buffer)>=2:
            start=buffer.find(b'\xa5\x5a')
            if start<0:
                buffer[:]=buffer[-1:] if buffer[-1]==0xa5 else b'';return
            if start:del buffer[:start]
            if len(buffer)<4:return
            length=buffer[2]+5
            if len(buffer)<length:return
            frame=bytes(buffer[:length]);del buffer[:length]
            if sum(frame[:-1])&255==frame[-1] and frame[3]==2:received.put_nowait(frame[4:-1])
    await client.start_notify(receive,notify)
    try:
        await client.write_gatt_char(transmit,bytes.fromhex('a5 5a 00 01 00'),response=True)
        p=await asyncio.wait_for(received.get(),6)
    finally:
        if client.is_connected:await client.stop_notify(receive)
    address=client.address.upper()
    if address not in known_targets() or len(p)!=15 or p[:6]!=bytes.fromhex(address.replace(':','')):
        raise RuntimeError('Invalid identity response or different Bluetooth address')
    identity={'address':address,'battery':p[6],'hardware':p[7],'batch':p[8:12].hex(),'firmware':p[12]}
    if identity['hardware']!=3 or identity['batch']!='20190404':raise RuntimeError(f'Unexpected hardware or production batch: {identity}')
    return identity
