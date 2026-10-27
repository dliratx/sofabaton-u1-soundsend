# Problem and solution

## Starting point

The setup uses a projector, a WiSA SoundSend transmitter, speakers, an iPhone, and a SofaBaton U1. The projector powers SoundSend. HDMI-CEC volume control failed in this installation, leaving the iPhone app as the working volume interface.

The owner required direct control from the U1, without a PC, phone automation, MQTT service, external hub, or Bluetooth bridge in the operational path. The Windows PC and UGREEN USB Bluetooth adapter were permitted as temporary research and installation tools.

## Why a device profile alone was insufficient

Bluetooth is a transport family, not one universal command language. The recovered SoundSend interface uses a custom GATT service and characteristic. The U1 needed to discover that interface, subscribe to notifications, read the current volume, and write the requested value. Assigning a conventional remote-control volume key would not implement those operations.

The solution therefore adds the missing Bluetooth central and protocol logic inside the U1. The dedicated profile is a selector for the custom firmware path. Its native key table remains empty; it does not contain copied Fire TV commands.

## Investigation and implementation

1. Identify the owned devices and verify the UGREEN adapter. Power and range matter: several early scans occurred with SoundSend unpowered because the projector was off.
2. Observe the SoundSend service, characteristic, volume query, notification, and write behavior. Confirm a bounded volume round trip.
3. Inspect the vendor U1 V32 image for the studied hardware revision and recover key dispatch, OLED routines, radio registers, and the OTA protocol.
4. Build a Cortex-M0 BLE central, test packet encoding and ATT behavior in Unicorn, and integrate it with the existing firmware.
5. Install bounded application updates through the original U1 OTA handlers, retaining the original bootloader region and persistent settings.
6. Use physical feedback to fix repeated held-key callbacks, unwanted reconnects, and display flicker.
7. Add mute/restore, battery display, earlier confirmed-volume rendering, and enabled-on-boot defaults in LAB17.

## Resulting behavior

On a fresh connection, the U1 reads the actual volume before changing it. Volume keys request one step and never exceed 0–100. The OLED displays confirmed values rather than an unacknowledged target.

Mute is implemented with the already verified volume command: remember the current level, set 0, and restore on the next toggle. No unverified mute opcode is invented. A second controller's change is handled by re-reading the device: a new nonzero level is muted and remembered instead of being overwritten by a stale restore level.

The UI does not wait for the entire Bluetooth connection to close before displaying a confirmed change. Small updates run only in checked idle intervals between connection events. The native battery measurement is displayed outside radio activity.

## Evidence boundaries

The owner confirmed direct volume control and the LAB16 flicker correction. LAB17 passed its offline tests and booted on the physical remote with its daily defaults enabled. Physical confirmation of the new mute button behavior, battery display, and perceived latency remains outstanding unless updated in [validation.md](validation.md).
