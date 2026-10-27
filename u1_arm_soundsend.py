"""Arm one-step physical U1 volume keys after exact build/profile readback.

Only volatile debug configuration and the vendor disconnect command6.
No firmware data, OTA preparation, SoundSend connection, or PC volume writes.
"""
import argparse
import asyncio
import json
from datetime import datetime, timezone
from pathlib import Path

from u1_debug import exchange
from u1_ota_install import Channel
from u1_soundsend_profile import AppChannel, metadata
from u1_transport import APP_NOTIFY, open_remote, read_identity
from firmware.build_delta_lab import STATE


async def run(args):
    expected = json.loads(STATE.read_text())
    revision=expected.get('lab_protocol_revision');build=expected.get('lab_build_id')
    if ({15:'0x544d5246',16:'0x544d5247',17:'0x544d5248'}.get(revision)!=build or
        not expected.get('boot_and_control_plane_verified')):
        raise RuntimeError('Only a verified physical LAB15/TMRF, LAB16/TMRG or LAB17/TMRH may be armed by this script')
    with args.output.open('x', encoding='utf8') as log:
        def record(event, **fields):
            row = dict(time=datetime.now(timezone.utc).isoformat(), event=event, **fields)
            log.write(json.dumps(row)+'\n'); log.flush()
            if event != 'frame': print(json.dumps(row), flush=True)
        try:
            async with open_remote(timeout=45) as client:
                record('identity', **await read_identity(client))
                state = await exchange(client, 1)
                record('before', **state)
                if state['lab_protocol_revision'] != revision or (state['enabled'] and revision!=17) or state['phase'] != 'idle':
                    raise RuntimeError(f'Expected disabled/idle LAB{revision}')
                clock = await exchange(client, 5)
                record('build', **clock)
                if clock['build_id'] != build:
                    raise RuntimeError(f'LAB{revision} build identity mismatch')
                if state['enabled']:record('disabled_for_configuration',**await exchange(client,3,0))
                app = AppChannel(client, record)
                await client.start_notify(APP_NOTIFY, app.receive)
                profiles = metadata(await app.query(10))
                if set(profiles) != {1, 2, 3, 4} or profiles[2]['name'] != 'SoundSend' or profiles[2]['code_type'] != 2:
                    raise RuntimeError('Dedicated SoundSend profile2 missing')
                record('dedicated_profile_verified', profiles=list(profiles.values()))
                keys = await app.query(12, bytes([2, 255]))
                if keys != [bytes.fromhex('a5 5a 01 03 07 0a')]:
                    raise RuntimeError('Dedicated empty profile key state is ambiguous or has leftover keys')
                record('dedicated_profile_has_no_old_fire_tv_keys', replies=[p.hex(' ') for p in keys])
                await client.stop_notify(APP_NOTIFY)
                mode = await exchange(client, 4, 10)
                rx = await exchange(client, 8, 2)
                lead = await exchange(client, 11, 72)
                record('tx_mode', **mode); record('rx_mode', **rx); record('tx_lead', **lead)
                if mode['tx_mode'] != 10 or rx['rx_clear_mode'] != 2 or lead['tx_lead_us'] != 72:
                    raise RuntimeError('Volatile radio configuration rejected')
                enabled = await exchange(client, 3, 1)
                record('physical_key_override_enabled', **enabled)
                if not enabled['enabled'] or enabled['phase'] != 'idle':
                    raise RuntimeError('Physical key override not enabled/idle')
                channel = Channel(client, record)
                await channel.start()
                try:
                    await channel.command(6)
                    record('device_configuration_disconnect_acknowledged')
                except Exception as error:
                    record('configuration_disconnect_ack_missing', error=f'{type(error).__name__}: {error}')
                await asyncio.sleep(1)
                record('armed_for_physical_test', firmware_revision=revision, daily_boot_default_enabled=revision==17,
                       expected_step=1, radio_mode=10, tx_lead_us=72, confirmed_baseline_volume=state['volume'],
                       pc_soundSend_connection=False, pc_volume_write=False, firmware_data_written=False)
        except BaseException as error:
            record('error', error=f'{type(error).__name__}: {error}'); raise
        finally: record('end')


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--output', type=Path, required=True)
    args = p.parse_args()
    if args.output.exists(): p.error('Fresh output required')
    asyncio.run(run(args))


if __name__ == '__main__': main()
