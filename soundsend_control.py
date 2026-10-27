"""Local Bluetooth volume control, validated against a specifically selected SoundSend.

Initial UUID and message hypotheses came from tvdstaaij/wisa2mqtt.
Our captures document which parts have actually been verified on this unit.
"""

import argparse
import asyncio
import json
from datetime import datetime, timezone
from pathlib import Path

from bleak import BleakClient, BleakScanner

from volume_read import SERVICE, CHARACTERISTIC, REFERENCE


class SoundSend:
    def __init__(self, address, record):
        self.address = address
        self.record = record
        self.client = None
        self.characteristic = None
        self.received = bytearray()
        self.volumes = asyncio.Queue()

    async def connect(self):
        device = await BleakScanner.find_device_by_address(self.address, timeout=15)
        if device is None:
            raise RuntimeError("Selected SoundSend is not advertising.")
        self.client = BleakClient(device, timeout=20, winrt={"use_cached_services": False})
        try:
            await self.client.connect()
            services = [s for s in self.client.services if s.uuid.lower() == SERVICE]
            if len(services) != 1:
                raise RuntimeError("Expected SoundSend service did not match uniquely.")
            characteristics = [c for c in services[0].characteristics if c.uuid.lower() == CHARACTERISTIC]
            if len(characteristics) != 1:
                raise RuntimeError("Expected control characteristic did not match uniquely.")
            self.characteristic = characteristics[0]
            if not {"notify", "indicate"}.intersection(self.characteristic.properties):
                raise RuntimeError("Control characteristic cannot notify.")
            if "write-without-response" not in self.characteristic.properties:
                raise RuntimeError("Expected write-without-response mode is unavailable.")
            self.received.clear()
            self.volumes = asyncio.Queue()
            await self.client.start_notify(self.characteristic, self._notification)
            self.record("connected", address=self.address, service=SERVICE, characteristic=CHARACTERISTIC)
        except BaseException:
            await self.disconnect()
            raise

    async def disconnect(self):
        if self.client is not None:
            try:
                await self.client.disconnect()
            finally:
                self.client = None
                self.characteristic = None

    def _notification(self, characteristic, data):
        self.record("rx", hex=bytes(data).hex(" "))
        self.received.extend(data)
        while len(self.received) >= 3:
            message_type, field, size = self.received[:3]
            if len(self.received) < size + 3:
                return
            frame = bytes(self.received[:size + 3])
            del self.received[:size + 3]
            if message_type == 2 and field == 4 and size == 1 and 0 <= frame[3] <= 100:
                self.volumes.put_nowait(frame[3])

    async def _send(self, payload):
        if self.client is None or not self.client.is_connected:
            raise RuntimeError("SoundSend connection was lost.")
        self.record("tx", hex=payload.hex(" "))
        await self.client.write_gatt_char(self.characteristic, payload, response=False)

    async def read_volume(self):
        while not self.volumes.empty():
            self.volumes.get_nowait()
        await self._send(bytes((0, 4, 0)))
        value = await asyncio.wait_for(self.volumes.get(), timeout=8)
        self.record("volume_read", volume=value)
        return value

    async def set_volume(self, value):
        if type(value) is not int or not 0 <= value <= 100:
            raise ValueError("Volume must be an integer between 0 and 100.")
        await self._send(bytes((1, 4, 1, value)))
        await asyncio.sleep(0.2)
        observed = await self.read_volume()
        if observed != value:
            raise RuntimeError(f"Requested volume {value}, but read back {observed}.")
        self.record("volume_write_verified", volume=value)

    async def test_step(self, expected):
        original = await self.read_volume()
        if original != expected:
            raise RuntimeError(f"Expected starting volume {expected}, found {original}. No setting was changed.")
        if original == 0:
            raise RuntimeError("Cannot perform the downward test from zero. No setting was changed.")
        trial = original - 1
        self.record("test_begin", original=original, trial=trial)
        try:
            await self.set_volume(trial)
            await asyncio.sleep(0.4)
        finally:
            # Restore even if writing/verifying the trial value raises.
            for attempt in range(2):
                try:
                    if self.client is None or not self.client.is_connected:
                        await self.disconnect()
                        await self.connect()
                    await self.set_volume(original)
                    self.record("test_restored", volume=original)
                    break
                except Exception as error:
                    self.record("restore_attempt_failed", attempt=attempt + 1, error=str(error))
                    if attempt == 1:
                        raise RuntimeError(f"Could not verify restoration to {original}; inspect capture.") from error
                    await self.disconnect()
        self.record("test_passed", original=original, trial=trial, restored=original)


async def run(args):
    path = Path(args.output)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("x", encoding="utf-8") as log:
        def record(event, **fields):
            row = {"time": datetime.now(timezone.utc).isoformat(), "event": event, **fields}
            log.write(json.dumps(row, ensure_ascii=False) + "\n")
            log.flush()
            print(json.dumps(row, ensure_ascii=False), flush=True)

        record("start", address=args.address, command=args.command, protocol_reference=REFERENCE)
        controller = SoundSend(args.address, record)
        try:
            await controller.connect()
            if args.command == "read":
                await controller.read_volume()
            elif args.command == "set":
                await controller.set_volume(args.value)
            elif args.command == "step":
                current = await controller.read_volume()
                await controller.set_volume(max(0, min(100, current + args.delta)))
            elif args.command == "test-step":
                await controller.test_step(args.expected)
        except BaseException as error:
            record("error", error=f"{type(error).__name__}: {error}")
            raise
        finally:
            await controller.disconnect()
            record("end")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--address", required=True, help="Explicit address of the user's identified SoundSend.")
    parser.add_argument("--output", required=True, help="New capture filename; existing captures are preserved.")
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("read")
    set_command = commands.add_parser("set")
    set_command.add_argument("value", type=int, choices=range(101), metavar="0..100")
    step_command = commands.add_parser("step")
    step_command.add_argument("delta", type=int, choices=(-1, 1))
    test_command = commands.add_parser("test-step", help="Read, reduce by one, verify, and restore the starting level.")
    test_command.add_argument("--expected", type=int, choices=range(1, 101), required=True, metavar="1..100")
    args = parser.parse_args()
    try:
        asyncio.run(run(args))
    except (Exception, KeyboardInterrupt) as error:
        parser.exit(1, f"{type(error).__name__}: {error}\n")


if __name__ == "__main__":
    main()
