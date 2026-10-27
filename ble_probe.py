"""BLE discovery and read-only GATT inventory for the user's SoundSend."""

import argparse
import asyncio
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

from bleak import BleakClient, BleakScanner


def timestamp():
    return datetime.now(timezone.utc).isoformat()


def save(path, report):
    destination = Path(path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"Report: {destination.resolve()}", flush=True)


async def adapter_identity(required_identifier=None):
    if sys.platform != "win32":
        if required_identifier:
            raise RuntimeError("Adapter identifier validation currently requires Windows.")
        return {"platform": sys.platform}
    from winrt.windows.devices.bluetooth import BluetoothAdapter

    adapter = await BluetoothAdapter.get_default_async()
    if adapter is None:
        raise RuntimeError("Windows has no default Bluetooth adapter.")
    radio = await adapter.get_radio_async()
    identity = {
        "device_id": adapter.device_id,
        "bluetooth_address": ":".join(f"{byte:02X}" for byte in adapter.bluetooth_address.to_bytes(6, "big")),
        "name": radio.name,
        "state": radio.state.name,
        "central_role_supported": adapter.is_central_role_supported,
    }
    if required_identifier and required_identifier.casefold() not in adapter.device_id.casefold():
        raise RuntimeError(f"Default adapter does not match {required_identifier!r}; scan cancelled. Actual adapter: {adapter.device_id}")
    print("Windows default adapter: " + json.dumps(identity, ensure_ascii=False), flush=True)
    return identity


async def scan(args):
    adapter = await adapter_identity(args.require_adapter)
    observations = {}

    def on_advertisement(device, advertisement):
        previous = observations.get(device.address, {})
        observations[device.address] = {
            "address": device.address,
            "name": advertisement.local_name or device.name or previous.get("name"),
            "rssi": advertisement.rssi,
            "service_uuids": sorted(set(previous.get("service_uuids", [])) | set(advertisement.service_uuids)),
            "manufacturer_data": {**previous.get("manufacturer_data", {}), **{str(k): v.hex(" ") for k, v in advertisement.manufacturer_data.items()}},
            "service_data": {**previous.get("service_data", {}), **{k: v.hex(" ") for k, v in advertisement.service_data.items()}},
            "advertisement_count": previous.get("advertisement_count", 0) + 1,
        }

    print(f"Scanning BLE advertisements for {args.seconds:g} seconds; no device connections.", flush=True)
    async with BleakScanner(on_advertisement):
        await asyncio.sleep(args.seconds)
    devices = sorted(observations.values(), key=lambda d: d["rssi"], reverse=True)
    report = {"captured_at": timestamp(), "seconds": args.seconds, "operation": "advertisement_scan", "adapter": adapter, "devices": devices}
    save(args.output, report)
    print(f"Discovered {len(devices)} BLE devices.", flush=True)
    for device in devices:
        if args.all or any(word in (device["name"] or "").lower() for word in ("sound", "wisa", "sofa")):
            print(json.dumps(device, ensure_ascii=False), flush=True)


async def inspect(args, selected_device=None):
    print(f"Looking for the explicitly selected device {args.address}.", flush=True)
    device = selected_device or await BleakScanner.find_device_by_address(args.address, timeout=15)
    if device is None:
        raise RuntimeError("Selected BLE device was not advertising; no connection attempted.")
    report = {"captured_at": timestamp(), "operation": "gatt_inventory", "address": device.address, "name": device.name, "services": []}
    print("Connecting for GATT discovery and reads only; no audio-setting writes.", flush=True)
    async with BleakClient(device, timeout=20, winrt={"use_cached_services": False}) as client:
        for service in client.services:
            entry = {"uuid": service.uuid, "description": service.description, "handle": service.handle, "characteristics": []}
            for characteristic in service.characteristics:
                item = {
                    "uuid": characteristic.uuid,
                    "handle": characteristic.handle,
                    "description": characteristic.description,
                    "properties": characteristic.properties,
                    "descriptors": [{"uuid": descriptor.uuid, "handle": descriptor.handle} for descriptor in characteristic.descriptors],
                }
                for descriptor, descriptor_item in zip(characteristic.descriptors, item["descriptors"]):
                    if descriptor.uuid.lower() == "00002901-0000-1000-8000-00805f9b34fb":
                        try:
                            value = await asyncio.wait_for(client.read_gatt_descriptor(descriptor.handle), timeout=3)
                            descriptor_item["read_hex"] = value.hex(" ")
                            descriptor_item["read_text"] = value.decode("utf-8", errors="replace")
                        except Exception as error:
                            descriptor_item["read_error"] = f"{type(error).__name__}: {error}"
                if "read" in characteristic.properties:
                    try:
                        value = await asyncio.wait_for(client.read_gatt_char(characteristic), timeout=5)
                        item["read_hex"] = value.hex(" ")
                        try:
                            item["read_text"] = value.decode("utf-8")
                        except UnicodeDecodeError:
                            pass
                    except Exception as error:
                        item["read_error"] = f"{type(error).__name__}: {error}"
                entry["characteristics"].append(item)
            report["services"].append(entry)
    save(args.output, report)
    if not getattr(args, "quiet", False):
        print(json.dumps(report, indent=2, ensure_ascii=False), flush=True)


async def probe(args):
    await adapter_identity(args.require_adapter)
    names = [name.strip().casefold() for name in args.names.split(",") if name.strip()]
    if not names:
        raise ValueError("At least one explicit device-name fragment is required.")

    def matches(device, advertisement):
        advertised_name = f"{advertisement.local_name or ''} {device.name or ''}".casefold()
        return any(name in advertised_name for name in names)

    print(f"Waiting up to {args.seconds:g} seconds for a name containing {names}; GATT reads only.", flush=True)
    device = await BleakScanner.find_device_by_filter(matches, timeout=args.seconds)
    if device is None:
        raise RuntimeError("No matching remote advertised during this attempt; no connection made.")
    print(f"Matched remote: {device.name!r} at {device.address}", flush=True)
    args.address = device.address
    await inspect(args, selected_device=device)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    scan_parser = commands.add_parser("scan", help="Collect BLE advertisements without connecting.")
    scan_parser.add_argument("--seconds", type=float, default=15)
    scan_parser.add_argument("--all", action="store_true", help="Print all results, including devices without a SoundSend-like name.")
    scan_parser.add_argument("--require-adapter", help="Abort unless the Windows default adapter ID contains this identifier.")
    scan_parser.add_argument("--output", default="captures/scan.json")
    scan_parser.set_defaults(run=scan)
    inspect_parser = commands.add_parser("inspect", help="Discover and read GATT on a manually selected device.")
    inspect_parser.add_argument("address")
    inspect_parser.add_argument("--output", default="captures/gatt.json")
    inspect_parser.add_argument("--quiet", action="store_true", help="Save the report without printing the full GATT inventory.")
    inspect_parser.set_defaults(run=inspect)
    probe_parser = commands.add_parser("probe", help="Find a specifically named remote and immediately inspect its GATT services.")
    probe_parser.add_argument("names", help="Comma-separated device-name fragments, e.g. SofaBaton,U1.")
    probe_parser.add_argument("--seconds", type=float, default=45)
    probe_parser.add_argument("--require-adapter")
    probe_parser.add_argument("--output", default="captures/remote-gatt.json")
    probe_parser.add_argument("--quiet", action="store_true")
    probe_parser.set_defaults(run=probe)
    args = parser.parse_args()
    if getattr(args, "seconds", 1) <= 0 or getattr(args, "seconds", 1) > 60:
        parser.error("Scan duration must be greater than 0 and at most 60 seconds.")
    try:
        asyncio.run(args.run(args))
    except (Exception, KeyboardInterrupt) as error:
        parser.exit(1, f"{type(error).__name__}: {error}\n")


if __name__ == "__main__":
    main()
