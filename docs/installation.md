# Installation and device-specific constraints

## Studied hardware

The recorded U1 is model MD8107M, hardware revision 3, production batch 20190404, originally running vendor firmware 33. The patch base is the available V32 image. The SoundSend reports MCU 1.28, DSP 1.2, and SummitTx 208.2. Its firmware is not changed.

The development adapter is the UGREEN/BARROT USB device with VID `33FA` and PID `0012`. The scripts enforce the adapter and the exact previously identified U1. Firmware source and host tools contain that unit's Bluetooth identities; this is intentional targeting, not a generic device-discovery fallback.

## Recorded update procedure

The implementation uses the remote's original OTA handlers. A full application transfer covers `0x00000..0x1EFFF`, including the descriptor at `0x1EF80`, in 7,936 chunks of 16 bytes. The transfer does not replace the persistent-state region `0x1F000..0x1FFFF` or bootloader region `0x3F000..0x3FFFF`. The bootloader may perform its own status writes as part of its normal process.

The uploader checks the candidate hash, passing reports, recorded installed image, exact device identity, battery threshold, and disabled/idle state. It prepares empty staging, transfers each chunk once with a matching acknowledgement, sends finish and commit, allows dispatch to settle, then reconnects to verify the actual boot revision and build.

The installed LAB21 package is identified in [validation.md](validation.md). A clone deliberately excludes the installation authorization record, raw receipts, installed-history binaries, and personal profile backups. Do not fabricate those files to bypass the guards.

## Boot and normal use

LAB21 enables the dedicated controls at startup with TX mode 10, RX-clear mode 2, and 72 µs lead. Selecting another profile retains its native behavior. While a phone or PC configuration connection is active, the custom button override yields to configuration.

For daily use, power SoundSend, disconnect any U1 configuration session, select the dedicated profile, and press a control button. No daily pairing step is needed. The transmitter address is compiled into the extension, so changing SoundSend units currently requires rebuilding and updating the U1. See [connection and changing devices](connection-and-devices.md) for the complete distinction between configuration pairing, normal reconnection, and provisioning another unit.

The helper `u1_arm_soundsend.py` verifies the exact installed build and profile, sets the known radio configuration, enables control, and requests the native configuration disconnect. It is a development helper, not a required everyday bridge.

## Known limitations

- No exact original FW33 dump or tested rollback is available. A profile backup is not a firmware backup.
- Some earlier complete, acknowledged updates rebooted into the previous revision. A second freshly prepared full transfer succeeded. The cause remains unresolved; acknowledgement alone is never treated as activation.
- Recovery from loss of power or an unusable experimental image is unproven. Preserving the bootloader region does not prove Bluetooth recovery will remain reachable.
- Direct control depends on SoundSend being powered and within range. In this setup, the projector supplies its power.
- Battery percentage comes from the native discrete estimator, not a calibrated continuous fuel gauge.
- Mute memory survives normal retained sleep but is lost on a full reset. After such a reset, a zero-volume device does not trigger an invented restore level.
- This implementation targets the observed unencrypted BLE control path and does not fake pairing or security negotiation.
