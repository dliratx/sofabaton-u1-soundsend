"""Read-only application queries reconstructed from the SofaBaton app.

Sources: ConnectActivity.event() and MainActivity.onResume() in the legacy
www.romateck.com.u package. The script never sends profile writes or OTA data.
"""

import argparse
import asyncio
import json
from datetime import datetime, timezone
from pathlib import Path

from bleak import BleakClient, BleakScanner

from ble_probe import adapter_identity
from u1_transport import known_targets

SERVICE = "0000fff0-0000-1000-8000-00805f9b34fb"
NOTIFY = "0000fff1-0000-1000-8000-00805f9b34fb"
WRITE = "0000fff2-0000-1000-8000-00805f9b34fb"
QUERIES = (("identity", bytes.fromhex("a5 5a 00 01 00")),
           ("device_list", bytes.fromhex("a5 5a 00 0a 09")))


async def run(args):
    await adapter_identity("VID_33FA&PID_0012")
    device = await BleakScanner.find_device_by_address(args.address, timeout=15)
    if device is None:
        raise RuntimeError("The selected U1 is not advertising. Put it in Pairing again.")
    destination = Path(args.output)
    destination.parent.mkdir(parents=True, exist_ok=True)
    buffer = bytearray()
    frame_count = 0
    queries = [(name, message, 8) for name, message in QUERIES]
    for device_id in args.key_device_ids:
        message = bytes.fromhex("a5 5a 02 0c") + bytes([device_id, 255])
        message += bytes([sum(message) & 255])
        queries.append((f"read_all_keys_device_{device_id}", message, 35))
    with destination.open("x", encoding="utf-8") as log:
        def record(event, **fields):
            row = {"time": datetime.now(timezone.utc).isoformat(), "event": event, **fields}
            log.write(json.dumps(row, ensure_ascii=False) + "\n")
            log.flush()
            print(json.dumps(row, ensure_ascii=False), flush=True)

        def on_notification(characteristic, data):
            nonlocal frame_count
            record("rx", hex=bytes(data).hex(" "))
            buffer.extend(data)
            while len(buffer) >= 2:
                start = buffer.find(b"\xa5\x5a")
                if start < 0:
                    keep = 1 if buffer[-1] == 0xA5 else 0
                    discarded = bytes(buffer[:-1] if keep else buffer)
                    if discarded:
                        record("unframed", hex=discarded.hex(" "))
                    buffer[:] = buffer[-1:] if keep else b""
                    return
                if start:
                    record("unframed", hex=bytes(buffer[:start]).hex(" "))
                    del buffer[:start]
                if len(buffer) < 4:
                    return
                length = buffer[2] + 5
                if len(buffer) < length:
                    return
                frame = bytes(buffer[:length])
                del buffer[:length]
                valid = (sum(frame[:-1]) & 0xFF) == frame[-1]
                frame_count += 1
                record("frame", hex=frame.hex(" "), opcode=frame[3], payload=frame[4:-1].hex(" "), checksum_valid=valid)

        record("start", address=device.address, name=device.name, operation="known_app_information_queries")
        try:
            async with BleakClient(device, timeout=20, winrt={"use_cached_services": False}) as client:
                service = client.services.get_service(SERVICE)
                if service is None:
                    raise RuntimeError("Expected U1 configuration service was not found.")
                receive = service.get_characteristic(NOTIFY)
                transmit = service.get_characteristic(WRITE)
                if receive is None or transmit is None:
                    raise RuntimeError("Expected application characteristics were not found.")
                name_characteristic = client.services.get_characteristic("00002a00-0000-1000-8000-00805f9b34fb")
                actual_name = bytes(await client.read_gatt_char(name_characteristic)).decode("utf-8")
                expected_name=known_targets().get(device.address.upper())
                if expected_name is None or actual_name != expected_name:
                    raise RuntimeError(f"Unexpected selected-device identity: {actual_name!r}.")
                await client.start_notify(receive, on_notification)
                for name, message, seconds in queries:
                    record("tx_query", query=name, hex=message.hex(" "))
                    await client.write_gatt_char(transmit, message, response=True)
                    await asyncio.sleep(seconds)
                if buffer:
                    record("remaining_bytes", hex=bytes(buffer).hex(" "))
                record("capture_complete", frames=frame_count)
        except BaseException as error:
            record("error", error=f"{type(error).__name__}: {error}")
            raise
        finally:
            record("end")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--address", default="20:21:03:00:2E:AB")
    parser.add_argument("--output", required=True)
    parser.add_argument("--key-device-ids", type=parse_key_device_ids, default=[],
                        help="Optional comma-separated device IDs whose key records should be read. This never writes key/profile data.")
    args = parser.parse_args()
    try:
        asyncio.run(run(args))
    except (Exception, KeyboardInterrupt) as error:
        parser.exit(1, f"{type(error).__name__}: {error}\n")


def parse_key_device_ids(value):
    try:
        ids = [int(part) for part in value.split(",")]
    except ValueError as error:
        raise argparse.ArgumentTypeError("Use comma-separated numeric device IDs") from error
    if not ids or any(not 1 <= item <= 255 for item in ids) or len(ids) != len(set(ids)):
        raise argparse.ArgumentTypeError("Use unique device IDs from 1 to 255")
    return ids


if __name__ == "__main__":
    main()
