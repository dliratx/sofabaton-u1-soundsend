# Firmware source

The current source is LAB20 / TMRK. Read the root [README](../README.md) for its actual installation status and [architecture](../docs/architecture.md) for module responsibilities.

- `soundsend_client.*`: SoundSend ATT client and confirmed-volume state.
- `ble_central.*`: single-peer BLE central and connection lifecycle.
- `ble_packet.*`, `ble_packet_tables.h`: packet encoding and generated tables.
- `a8107_port.*`: studied A8107M0 radio/timer backend.
- `u1_integration.*`, `u1_trampolines.S`: keys, mute, OLED, battery, native hooks, and diagnostics.
- `u1-lab.ld`: retained RAM, stack, and flash placement.
- `build-all.ps1`: offline assembly and seven validation suites.
- `test_*.py`: ARM execution and host transport tests.

The historical local `README.txt` is not part of the maintained repository documentation. Build artifacts, vendor images, and installed-history files remain untracked.
