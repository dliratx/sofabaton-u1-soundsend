"""Replace only this unit's Fire TV profile with an empty SoundSend profile.

Vendor app protocol: wf0.OooO0o0, wy.OooOo0O, qs/ts deletion.
Default is an offline plan. Never updates firmware or connects to SoundSend.
"""
import argparse
import asyncio
import hashlib
import json
import sys
import threading
from datetime import datetime, timezone
from pathlib import Path

from u1_transport import APP_NOTIFY, APP_WRITE, open_remote, read_identity
from u1_delta_install import verify_dispatch


def frame(opcode, payload=b''):
    p = bytes([0xa5, 0x5a, len(payload), opcode]) + payload
    return p + bytes([sum(p) & 255])


def create_packets(sort):
    name = b'SoundSend'
    body = bytearray(len(name) + 20)
    count = (len(body) + 11) // 12
    body[:8] = bytes([1, 0, count, 0, 2, 1, sort, 2])
    body[11:15] = bytes([23, 0xa5, 0x5a, 1])
    body[18:18 + len(name)] = name
    body[-1] = sum(body[:-1]) & 255
    return [frame(7, b'\x01' + (i + 1).to_bytes(2, 'big') + body[i*12:(i+1)*12])
            for i in range(count)]


def assemble(frames, opcode):
    records = {}
    for p in frames:
        if p[3] != opcode:
            continue
        payload = p[4:-1]
        if len(payload) < 4:
            raise ValueError('Short record fragment')
        record, seq = payload[0], int.from_bytes(payload[1:3], 'big')
        chunks = records.setdefault(record, {})
        if seq in chunks:
            raise ValueError('Duplicate record fragment')
        chunks[seq] = payload[3:]
    decoded = []
    totals = set()
    for record, chunks in sorted(records.items()):
        if 1 not in chunks:
            raise ValueError('Missing first fragment')
        body = b''.join(chunks[s] for s in sorted(chunks))
        if len(body) < 4 or sum(body[:-1]) & 255 != body[-1]:
            raise ValueError('Bad inner checksum')
        count = int.from_bytes(body[1:3], 'big')
        if sorted(chunks) != list(range(1, count+1)):
            raise ValueError('Incomplete record')
        totals.add(body[0])
        decoded.append(body)
    if decoded and (len(totals) != 1 or sorted(records) != list(range(1, next(iter(totals))+1))):
        raise ValueError('Incomplete record set')
    return decoded


def metadata(frames):
    result = {}
    for body in assemble(frames, 11):
        if len(body) < 20 or body[4] in result:
            raise ValueError('Bad/duplicate device metadata')
        result[body[4]] = dict(device_id=body[4], name=body[18:-1].decode('ascii').strip(' \0'),
                              icon=body[5], sort=body[6], code_type=body[7],
                              remote_id=int.from_bytes(body[8:11], 'big'), remote_type=body[11],
                              state=body[14], raw_record=body.hex(' '))
    if not result:
        raise ValueError('No profiles returned')
    return result


class AppChannel:
    def __init__(self, client, record):
        self.client, self.record = client, record
        self.buffer = bytearray()
        self.frames = []
        self.failure = None

    def receive(self, characteristic, data):
        self.buffer.extend(data)
        while len(self.buffer) >= 4:
            if self.buffer[:2] != b'\xa5\x5a':
                self.failure = 'Unframed app notification'
                return
            length = self.buffer[2]+5
            if len(self.buffer) < length:
                return
            p = bytes(self.buffer[:length]); del self.buffer[:length]
            if sum(p[:-1]) & 255 != p[-1]:
                self.failure = 'Invalid app notification checksum'
                return
            self.frames.append(p)
            self.record('frame', opcode=p[3], hex=p.hex(' '), checksum_valid=True)

    async def send(self, p):
        if self.failure:
            raise RuntimeError(self.failure)
        self.record('app_tx', opcode=p[3], hex=p.hex(' '))
        await self.client.write_gatt_char(APP_WRITE, p, response=True)

    async def transaction(self, packets, label):
        start = len(self.frames)
        for p in packets:
            await self.send(p)
        deadline = asyncio.get_running_loop().time()+8
        while asyncio.get_running_loop().time() < deadline:
            if self.failure:
                raise RuntimeError(self.failure)
            replies = [p for p in self.frames[start:] if p[3] == 3]
            if replies:
                if len(replies) != 1 or replies[0][4:-1] != b'\0':
                    raise RuntimeError(f'{label} rejected: {[p.hex() for p in replies]}')
                self.record('app_transaction_confirmed', operation=label)
                return
            await asyncio.sleep(.04)
        raise TimeoutError(f'{label}: no success status; read state before any retry')

    async def query(self, opcode, payload=b'', seconds=8):
        start = len(self.frames)
        await self.send(frame(opcode, payload))
        await asyncio.sleep(seconds)
        if self.failure or self.buffer:
            raise RuntimeError(self.failure or 'Trailing incomplete app notification')
        return self.frames[start:]


async def execute(args, backup):
    with args.output.open('x', encoding='utf8') as log:
        def record(event, **fields):
            row = dict(time=datetime.now(timezone.utc).isoformat(), event=event, **fields)
            log.write(json.dumps(row, ensure_ascii=False)+'\n'); log.flush()
            if event != 'frame':
                print(json.dumps(row, ensure_ascii=True), flush=True)
        record('start', backup=str(args.backup), backup_sha256=hashlib.sha256(args.backup.read_bytes()).hexdigest(),
               operation='delete Fire TV device2 and create empty SoundSend device2')
        try:
            async with open_remote(timeout=45) as client:
                identity = await read_identity(client)
                if identity['firmware'] != 32:
                    raise RuntimeError('Unexpected firmware identity')
                record('identity', **identity)
                record('disabled_idle_verified', **await verify_dispatch(client, 'lab'))
                channel = AppChannel(client, record)
                await client.start_notify(APP_NOTIFY, channel.receive)
                before = metadata(await channel.query(10))
                old = {p['device_id']: p for p in backup['profiles']}
                if set(before) != set(old):
                    raise RuntimeError('Profile set changed since full key backup')
                for did in before:
                    if before[did]['raw_record'] != old[did]['raw_record']:
                        raise RuntimeError(f'Profile{did} changed since backup')
                if before[2]['name'] != 'Fire TV':
                    raise RuntimeError('Only Fire TV device2 may be deleted')
                record('profile_snapshot_verified', profiles=list(before.values()))
                await channel.transaction([frame(20)], 'delete preamble')
                await channel.transaction([frame(9, b'\x02')], 'delete Fire TV device2')
                deleted = metadata(await channel.query(10))
                if set(deleted) != set(old)-{2}:
                    raise RuntimeError('Unexpected device set after delete')
                fields = ('device_id', 'name', 'icon', 'code_type', 'remote_id', 'remote_type', 'state')
                for did in deleted:
                    if any(deleted[did][f] != old[did][f] for f in fields):
                        raise RuntimeError(f'Other profile{did} changed after delete')
                used = {p['sort'] for p in deleted.values()}
                sort = next(s for s in range(1, 5) if s not in used)
                record('delete_verified', profiles=list(deleted.values()), new_sort=sort)
                await channel.transaction([frame(20)], 'create preamble')
                await channel.transaction(create_packets(sort), 'create SoundSend device2')
                after = metadata(await channel.query(10))
                if set(after) != set(old):
                    raise RuntimeError('Unexpected profile set after create')
                expected = dict(device_id=2, name='SoundSend', icon=1, sort=sort,
                                code_type=2, remote_id=0, remote_type=23, state=1)
                if any(after[2][k] != v for k, v in expected.items()):
                    raise RuntimeError('Dedicated profile did not read back as requested')
                for did in deleted:
                    if any(after[did][f] != deleted[did][f] for f in (*fields, 'sort')):
                        raise RuntimeError(f'Other profile{did} changed after create')
                record('dedicated_profile_verified', profiles=list(after.values()))
                for did in (1, 4, 3):
                    replies = await channel.query(12, bytes([did, 255]), 35)
                    actual = {p[4]: p.hex(' ') for opcode in (13, 19, 21) for p in assemble(replies, opcode)}
                    wanted = {k['button_id']: k['raw_record'] for k in old[did]['keys']}
                    if actual != wanted:
                        raise RuntimeError(f'Other profile{did} key records changed/incomplete')
                    record('other_profile_keys_preserved', device_id=did, keys=len(actual))
                record('complete', fire_tv_deleted=True, soundsend_profile_id=2,
                       other_profiles_and_keys_preserved=True, volume_override_enabled=False)
                if args.stay:
                    record('keepalive_ready', exit_command='quit')
                    commands = asyncio.Queue()
                    loop = asyncio.get_running_loop()
                    def read_commands():
                        for line in sys.stdin:
                            loop.call_soon_threadsafe(commands.put_nowait, line.strip())
                        loop.call_soon_threadsafe(commands.put_nowait, 'quit')
                    threading.Thread(target=read_commands, daemon=True).start()
                    while client.is_connected:
                        try:
                            command = await asyncio.wait_for(commands.get(), 4)
                            if command.strip() == 'quit': break
                        except TimeoutError:
                            await channel.query(1, seconds=.5)
                await client.stop_notify(APP_NOTIFY)
        except BaseException as e:
            record('error', error=f'{type(e).__name__}: {e}'); raise
        finally:
            record('end')


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--backup', type=Path, default=Path('captures/u1-before-dedicated-soundsend-profile-01.json'))
    p.add_argument('--execute', action='store_true')
    p.add_argument('--output', type=Path)
    p.add_argument('--stay', action='store_true')
    args = p.parse_args()
    backup = json.loads(args.backup.read_text(encoding='utf8'))
    if (backup['identity']['mac'] != '20:21:03:00:2E:AB' or
        not backup['all_reported_profile_key_records_complete'] or
        {r['device_id'] for r in backup['profiles']} != {1, 2, 3, 4}):
        p.error('Exact unit and complete four-profile backup required')
    if not args.execute:
        print(json.dumps(dict(delete=[frame(20).hex(' '), frame(9, b'\x02').hex(' ')],
                              create_sort2=[frame(20).hex(' '), *[v.hex(' ') for v in create_packets(2)]],
                              execute=False), indent=2))
        return
    if not args.output or args.output.exists():
        p.error('Fresh output path required')
    asyncio.run(execute(args, backup))


if __name__ == '__main__':
    main()
