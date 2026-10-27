# Offline build and tests

## Environment

The recorded build used Windows PowerShell, Python 3.13, Zig 0.15.2, Unicorn 2.1.4, Capstone 5.0.9, and Bleak 3.0.2. Zig targets `thumb-freestanding-eabi`, Cortex-M0, with freestanding C99 and no standard library.

Create a local Python environment and install the project dependencies:

```powershell
py -3.13 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements-dev.txt
```

Place the official Windows x86-64 Zig 0.15.2 distribution at:

```text
research/zig-toolchain/zig-x86_64-windows-0.15.2/zig.exe
```

Obtain the appropriate vendor base image independently and place it at:

```text
research/firmware-candidates/OTA_U1_5_V32.bin
```

The required SHA-256 is:

```text
6facfda529024239abfa7eb7293d9d4d2ca17c11008c245699de94e4b5c49fc7
```

Vendor images and toolchains are intentionally not committed. A filename alone is insufficient; the image builder rejects a different base hash.

## Reproduce

```powershell
.\.venv\Scripts\python.exe scripts/prepare-offline-fixtures.py
.\.venv\Scripts\python.exe scripts/check-prerequisites.py
powershell -NoProfile -File firmware/build-all.ps1
```

The fixture preparation copies only volume protocol messages into the historical test path when that path is absent. It does not overwrite an existing local capture. No Bluetooth connection or firmware installation occurs during these commands.

The full build produces three ARM ELF files, an assembled image, an OTA package, and JSON test reports. Those generated files remain local. The package SHA can differ when build inputs differ; the installer requires the exact candidate SHA and matching test evidence.

## Test suites

| Suite | LAB20 tests | Coverage |
| --- | ---: | --- |
| `test_client_arm.py` | 16 | ATT discovery, notifications, bounds, deferred changes, mute writes |
| `test_central_arm.py` | 20 | BLE central, packet order, fragmentation, connection lifecycle |
| `test_ble_packet_arm.py` | 8 | CRC, whitening, and packet vectors |
| `test_u1_lab_arm.py` | 79 | Patched hooks, native HID handoff and reconnection policy, pairing exclusion, link indicators, timer handoff, sleep entry, held-key repetition, OLED, battery, mute, boot defaults |
| `test_lcd_retention_arm.py` | 2 | Lower-SRAM state and mute restoration after modeled upper-bank loss |
| `test_ota_lab_arm.py` | 6 | Original OTA handlers, boot copy, descriptor and protected regions |
| `test_ota_transport.py` | 8 | Host framing, strict acknowledgements, stop behavior, postboot identity |
| **Total** | **139** | **All passed for LAB20; installation and physical switching status are recorded separately** |

The standalone `test_delta_lab_arm.py` is historical research and is not part of this full-application build. It requires local installed-state and delta artifacts that are not included in a clone.

Unicorn executes the compiled ARM code. Radio peers, GATT transport, selected hardware registers, and some native routines are modeled. Other tests execute actual native key, OLED, and OTA routines with the relevant peripherals stubbed. These checks do not establish physical RF reliability, electrical timing, battery life, or recovery after interrupted flashing.
