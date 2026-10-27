# Switching between native Bluetooth profiles and SoundSend

The U1 uses one radio for its native Bluetooth remote-control profiles and the custom SoundSend central. The profiles remain configured together, but their active links must be handed over. Selecting a profile and establishing its control connection are separate events.

## Reported LAB19 failure

After a new Fire TV profile was added through the SofaBaton app and paired successfully, Fire TV control worked. Returning to SoundSend left its previous volume percentage visible, but volume commands no longer worked. The Fire TV interface still showed the U1 as connected.

A fresh configuration read found five profiles: Proyector (1), SoundSend (2), Android TV (3), HDMI (4), and the newly added Fire TV (5). The previous Fire TV backup was not restored. LAB19 diagnostics retained volume 55 and reported a failed central operation with reason `0x08`; the displayed number was not evidence of a currently working SoundSend connection.

## Native reconnection race

The original post-poll main-loop code uses the native timeout field at `0x200000C8` as part of its HID connection policy. Values `0x82` and `0x514` cause it to restart the previous HID advertiser when the link becomes idle. The relevant decision starts at `0xCFEE` and calls the native HID initializer at `0x87D8`.

LAB19 requested a link-layer disconnect, then waited for an idle radio. The original main loop could restart the HID advertiser between those polls, preventing SoundSend from acquiring the released radio. An ARM regression executes that original idle-path decision with a modeled asynchronous disconnect and reproduces repeated HID restarts for both timeout values.

LAB20 ends the previous HID retry policy before requesting disconnect:

- Restore the normal inactivity threshold from the native setting at `0x200000BA` and reset the inactivity counter.
- Clear the two pending native key fields so an old Fire TV action is not replayed.
- Keep the native connection-mode field intact until disconnect completes, so an existing HID link is not mistaken for a phone configuration connection.
- Wait for the native stack to release the radio before starting the SoundSend central.

The original HID key path remains available when returning to Fire TV. Neither the profile records nor their assigned keys are rewritten by this handoff. Custom SoundSend keys also yield while the native app or device-pairing screen is active.

## SoundSend link indicator

The top-right corner of the SoundSend view distinguishes the connection state from the cached volume:

| Indicator | Meaning |
| --- | --- |
| `...` | A control operation is waiting for or establishing the SoundSend connection. |
| `BT` | The active SoundSend session has received a valid volume value. |
| `!` | The last attempt ended with an error; the displayed percentage may be an earlier confirmed value. |
| Blank | No active SoundSend control session. The next volume or mute press reconnects as needed. |

The link closes after the existing idle interval, so `BT` normally disappears shortly after control activity stops. It is not a permanent pairing symbol. The indicator uses the existing bounded display-update window; no display or ADC work is added to the scanning or packet-launch path.

## Validation scope

Regression coverage includes acquisition after asynchronous HID disconnect, the original post-poll reconnection decision, return to the native HID key path, pairing-mode exclusion, and successful/failed connection indicators. The ordinary inactivity, held-volume, mute, display timing, and OTA checks remain part of the full build.

The HID advertiser and RF peer are modeled in these tests. The diagnosed native reconnection race and its offline correction do not replace physical testing of repeated Fire TV-to-SoundSend transitions. Installation status and subsequent hardware feedback are recorded in [validation.md](validation.md).

For daily use and adding a new Bluetooth device through the app, see [connection and changing devices](connection-and-devices.md).
