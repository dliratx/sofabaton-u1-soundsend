# Problem and solution

## System requirements

The test system consists of a projector, a WiSA SoundSend transmitter, speakers, and a SofaBaton U1. SoundSend receives power from the projector. HDMI-CEC volume control was unavailable in this installation; the SoundSend iPhone app provided the initial working control interface.

The design requires direct U1-to-SoundSend control, with no PC, phone automation, MQTT service, external hub, or Bluetooth bridge in the operational path. A Windows PC and UGREEN USB Bluetooth adapter provide development, diagnostics, and OTA installation only.

## Why a device profile alone was insufficient

Bluetooth is a transport family, not one universal command language. The recovered SoundSend interface uses a custom GATT service and characteristic. The U1 needed to discover that interface, subscribe to notifications, read the current volume, and write the requested value. Assigning a conventional remote-control volume key would not implement those operations.

The solution adds the Bluetooth central and protocol logic inside the U1. The dedicated profile selects the custom firmware path. Its native key table remains empty because the firmware handles the volume and mute buttons directly; the profile itself does not encode the SoundSend protocol.

## Investigation and implementation

1. Identify the target devices and verify the UGREEN adapter. Confirm that SoundSend is powered and within range before Bluetooth discovery.
2. Observe the SoundSend service, characteristic, volume query, notification, and write behavior. Confirm a bounded volume round trip.
3. Inspect the vendor U1 V32 image for the studied hardware revision and recover key dispatch, OLED routines, radio registers, and the OTA protocol.
4. Build a Cortex-M0 BLE central, test packet encoding and ATT behavior in Unicorn, and integrate it with the existing firmware.
5. Install bounded application updates through the original U1 OTA handlers, retaining the original bootloader region and persistent settings.
6. Validate button and display behavior on hardware, then correct repeated held-key callbacks, unwanted reconnects, and display flicker.
7. Add mute/restore, battery display, earlier confirmed-volume rendering, and enabled-on-boot defaults in LAB17.
8. Restore the native inactivity timer after radio use and add paced volume repetition in LAB18 after physical testing exposed persistent screen-on behavior and missing hold support.
9. Reproduce the remaining physical hold failure with a delayed-response test, then restart the ready window after successful discovery or write confirmation in LAB19.

## Resulting behavior

On a fresh connection, the U1 reads the actual volume before changing it. A volume-key press requests one step and never exceeds 0–100. Holding the key repeats gradually, with an initial delay and confirmation of the preceding change before another repeat is accepted. The OLED displays confirmed values rather than an unacknowledged target.

Mute is implemented with the already verified volume command: remember the current level, set 0, and restore on the next toggle. No unverified mute opcode is invented. A second controller's change is handled by re-reading the device: a new nonzero level is muted and remembered instead of being overwritten by a stale restore level.

The UI does not wait for the entire Bluetooth connection to close before displaying a confirmed change. Small updates run only in checked idle intervals between connection events. The native battery measurement is displayed outside radio activity.

Radio ownership preserves the separate native inactivity timer and its pending interrupt. After the bounded connection closes, the original timeout and sleep path remain available, including after changing profiles. See [power management](power-management.md) for the recovered timer path and regression coverage.

## Evidence boundaries

Manual testing confirmed direct volume control and the LAB16 flicker correction. LAB18 passed 132 offline tests and booted on the physical remote with its daily defaults enabled. A read-only SoundSend query succeeded, followed by three hardware snapshots showing the inactivity timer running and its counter advancing after radio release. Its physical hold test failed. LAB19 passed 134 offline tests, including delayed-response regressions, and awaits installation and physical confirmation. Automatic screen-off, mute behavior, battery display, and perceived latency also require manual confirmation. The current evidence and limitations are recorded in [validation.md](validation.md).
