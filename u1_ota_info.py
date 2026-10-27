"""Read identification from AMICCOM's OTA command channel without updating.

Only fixed information queries from the app's FlowControlCommands are present.
Feature flags describe the OTA protocol, not support for the BLE central role.
"""

import argparse
import asyncio
import json
from datetime import datetime, timezone
from pathlib import Path

from bleak import BleakClient, BleakScanner
from ble_probe import adapter_identity

SERVICE = "0000fdf2-0000-1000-8000-00805f9b34fb"
COMMAND = "0000fa11-0000-1000-8000-00805f9b34fb"
QUERIES = (("customer_version", bytes((1, 0))),
           ("product_version", bytes((2, 0))),
           ("firmware_version", bytes((3, 0))),
           ("ota_protocol_features", bytes((15, 0))))


async def run(args):
    await adapter_identity("VID_33FA&PID_0012")
    device = await BleakScanner.find_device_by_address(args.address, timeout=15)
    if device is None:
        raise RuntimeError("U1 is not advertising. Close its phone app and put the U1 in Pairing.")
    destination = Path(args.output)
    destination.parent.mkdir(parents=True, exist_ok=True)
    received = asyncio.Queue()
    with destination.open("x", encoding="utf-8") as log:
        def record(event, **fields):
            row = {"time": datetime.now(timezone.utc).isoformat(), "event": event, **fields}
            log.write(json.dumps(row, ensure_ascii=False) + "\n")
            log.flush()
            print(json.dumps(row, ensure_ascii=False), flush=True)

        def notification(characteristic, data):
            raw = bytes(data)
            record("rx_indication", hex=raw.hex(" "))
            received.put_nowait(raw)

        record("start", address=device.address, operation="fixed_ota_identification_queries", source="APK com.amiccom.ota_library.OTA.FlowControlCommands")
        try:
            async with BleakClient(device, timeout=20, winrt={"use_cached_services": False}) as client:
                name = bytes(await client.read_gatt_char("00002a00-0000-1000-8000-00805f9b34fb")).decode("utf-8")
                model = bytes(await client.read_gatt_char("00002a24-0000-1000-8000-00805f9b34fb")).decode("utf-8")
                if name != "SB202103002EAB" or model != "MD8107M":
                    raise RuntimeError(f"Unexpected U1 identity: {name!r}, {model!r}; no queries sent.")
                service = client.services.get_service(SERVICE)
                command = service.get_characteristic(COMMAND) if service else None
                if command is None or not {"write", "indicate"}.issubset(command.properties):
                    raise RuntimeError("Expected command channel and properties not found; no queries sent.")
                await client.start_notify(command, notification)
                for label, request in QUERIES:
                    while not received.empty():
                        received.get_nowait()
                    record("tx_information_query", query=label, hex=request.hex(" "))
                    await client.write_gatt_char(command, request, response=True)
                    try:
                        async with asyncio.timeout(6):
                            while True:
                                response = await received.get()
                                if len(response) >= 4 and response[0] == 8 and response[2] == request[0]:
                                    record("query_response", query=label, result_code=response[3], payload=response[4:].hex(" "))
                                    break
                    except TimeoutError:
                        record("query_timeout", query=label, later_queries_sent=False)
                        break
                    await asyncio.sleep(0.2)
        except BaseException as error:
            record("error", error=f"{type(error).__name__}: {error}")
            raise
        finally:
            record("end")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--address", default="20:21:03:00:2E:AB")
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    try:
        asyncio.run(run(args))
    except (Exception, KeyboardInterrupt) as error:
        parser.exit(1, f"{type(error).__name__}: {error}\n")


if __name__ == "__main__":
    main()
