# Connection, reconnection, and changing devices

The installed U1 already knows which SoundSend to control. Normal use requires no pairing procedure or running phone/PC application. LAB19 contains one fixed target address; it does not offer a device-discovery menu or a configurable list of transmitters.

## Everyday use

1. Power on SoundSend. In the documented setup, this means turning on the projector that supplies its power.
2. Disconnect any active SofaBaton app or PC configuration session from the U1.
3. Select the existing **SoundSend** profile with the U1's device-selection wheel.
4. Press volume up, volume down, or mute. Hold a volume button for gradual repetition.

The U1 scans for the configured transmitter when a control action needs a connection. It discovers the control service, subscribes to notifications, reads the actual volume, and applies the requested change. A ready connection is kept for approximately 800 ms after the last accepted key or completed operation, allowing subsequent presses and held-key repeats to reuse it. After that idle interval, it disconnects and returns radio ownership to the native firmware.

There is no need to keep the Bluetooth link permanently open. The next control press starts a new connection when necessary. The OLED shows the last confirmed volume; while disconnected, it is not a continuously refreshed monitor of changes made by another controller.

## What was configured

| Setting | Installed value | Where it is stored |
| --- | --- | --- |
| SoundSend Bluetooth address | `CC:90:93:18:93:0A` | `peer_le` in the compiled U1 extension |
| SoundSend address type | Public, type `0` | The `bc_start` call in the extension |
| U1 Bluetooth identity | `20:21:03:00:2E:AB` | `own_le` and the patched vendor identity constant |
| Dedicated profile | Internal ID `2`, name `SoundSend` | Native U1 profile settings |
| Custom controls at startup | Enabled | Firmware startup default |

The address match is explicit: another nearby device advertising the same name will not be selected. The installed SoundSend address also matched the `Mac ID` shown in the supplied SoundSend app system-information screen.

The dedicated profile selects the custom button handler. Its native key table is empty because the extension handles volume and mute directly. The former Fire TV profile only supplied the slot that was replaced; no Fire TV Stick participates in the connection.

## Why pairing mode was used during development

The U1's configuration connection lets the phone or PC inspect settings, manage profiles, run diagnostics, and transfer firmware. The repeated pairing requests during development concerned that connection to the **U1**.

Normal control uses a different role: the U1 initiates a BLE connection directly to SoundSend. The implemented control path uses the observed unencrypted GATT interface. It neither creates a persistent Bluetooth bond nor implements PIN exchange or SMP pairing. Thus, selecting a generic Bluetooth device in the SofaBaton app does not configure this extension's target.

An active U1 configuration connection takes precedence over custom button handling. Disconnect that session before using the SoundSend controls. Pairing mode is not a step in the everyday procedure.

## Power loss and reconnection

| Situation | Action and expected behavior |
| --- | --- |
| Projector or SoundSend was off | Restore power, let SoundSend start, then make a new volume or mute press. |
| U1 batteries were replaced | Select SoundSend if necessary and press a control button. The compiled target remains and controls enable at boot. The remembered mute-restore level is lost. |
| U1 moved out of range | Move back within range, release the key, and press again. Discovery attempts are bounded; there is no continuous background reconnect loop. |
| Phone or PC is configuring the U1 | Disconnect the configuration session before trying the custom controls. |
| SoundSend cannot be reached | Check power and distance, close any SoundSend app/diagnostic connection for the retry, then make a new press. Concurrent control connections have not been characterized. |
| A diagnostic temporarily disabled the custom controls | A full U1 restart restores the enabled startup default. Recreating the profile is unnecessary. |

The initial scan is bounded to three seconds; later connection and protocol stages have their own timeouts. A failed attempt does not erase or select a different target. Selecting the profile alone does not start a continuous connection.

## Using the same U1 with another SoundSend

**Changing the SoundSend target currently requires a new U1 firmware build and OTA installation.** There is no supported address-setting command, learning button, or app screen for this in LAB19. SoundSend itself does not need modified firmware.

1. Identify the replacement transmitter. Its app's system-information page can provide a candidate MAC address; verify the actual BLE address and address type rather than selecting a device solely by name.
2. Verify the [control service, characteristic, and volume protocol](protocol.md). This implementation expects a stable public address and the observed unencrypted interface. A different security requirement or rotating private address needs additional implementation work.
3. Change `peer_le` in [u1_integration.c](../firmware/u1_integration.c) to the verified target. The six bytes are stored in reverse display order. The current target is:

   ```c
   /* CC:90:93:18:93:0A */
   static const uint8_t peer_le[6] = {0x0a, 0x93, 0x18, 0x93, 0x90, 0xcc};
   ```

4. Update the modeled peer in [test_central_arm.py](../firmware/test_central_arm.py), which is also imported by the integration tests. Rebuild and run the [offline validation workflow](build-and-test.md). Changing only the transmitter does not change the U1's own identity.
5. Prepare and verify the new candidate using the [guarded installation workflow](installation.md), install it on the same verified U1, and confirm the actual boot. Candidate hashes and evidence must correspond to the new image.
6. Disconnect the configuration session, select the existing SoundSend profile, and verify operation against the replacement transmitter. The old transmitter is no longer the configured target.

The repository includes Windows discovery and read-only inspection tools. These commands help identify a device; they do not change the U1 target or install firmware:

```powershell
.\.venv\Scripts\python.exe ble_probe.py scan --seconds 15 --all --output captures/replacement-soundsend-scan.json
$soundSendAddress = 'CC:90:93:18:93:0A' # Replace with the newly verified address.
.\.venv\Scripts\python.exe ble_probe.py inspect $soundSendAddress --output captures/replacement-soundsend-gatt.json
.\.venv\Scripts\python.exe soundsend_control.py --address $soundSendAddress --output captures/replacement-soundsend-volume.jsonl read
```

Check the adapter reported by the scan before continuing; the development setup uses the UGREEN/BARROT USB adapter. A successful host query is protocol evidence, not proof that a replacement device will work with the embedded central. The scanner report alone does not establish the public/random address type.

## Using another U1 or a different kind of device

Another U1 requires its own hardware, firmware, identity, and profile checks. This image contains the studied remote's identity in both `own_le` and [build_u1_lab.py](../firmware/build_u1_lab.py); the host tools and installer also enforce the recorded unit. Copying the image unchanged to another remote would duplicate that identity. Adapt the implementation and guards to verified data for the new unit and collect its own installation evidence; do not reuse the existing unit's receipts.

Controlling a different product is a separate protocol implementation, not just an address change. For existing native devices on this U1, select their normal profiles; the SoundSend address does not affect those profiles. A future on-remote transmitter-selection menu would require new firmware work and is not present in LAB19.

Implementation references: [button handling and target selection](../firmware/u1_integration.c), [advertisement address filtering and link lifecycle](../firmware/ble_central.c), and [configuration activation helper](../u1_arm_soundsend.py).
