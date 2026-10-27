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

LAB21 keeps the link available for 10 seconds after the last accepted operation or confirmation, extending LAB20's 800 ms interval. `BT` stays visible during that active session and disappears when it closes. Selecting SoundSend alone shows the last confirmed volume. Pressing volume or mute establishes a connection when needed; successive presses and held-volume repetition reuse it while active. This is a connection to the already configured address, not a new pairing exchange. The indicator uses the existing bounded display-update window; no display or ADC work is added to the scanning or packet-launch path.

Selecting another profile or entering native pairing releases the control session before the 10-second interval expires. Native screen rendering and control can then use the restored radio path.

## Validation scope

Regression coverage includes acquisition after asynchronous HID disconnect, the original post-poll reconnection decision, return to the native HID key path, pairing-mode exclusion, and successful/failed connection indicators. The ordinary inactivity, held-volume, mute, display timing, and OTA checks remain part of the full build.

The HID advertiser and RF peer are modeled in these tests. After LAB20 installation, an initial scan timeout coincided with SoundSend being undiscoverable to both the UGREEN adapter and the iPhone. Advertisements returned after a transmitter restart, and subsequent manual feedback confirmed Fire TV/SoundSend switching and volume control. That acceptance applies to LAB20; LAB21's longer interval has its own validation record. Long-duration reliability and instrumented timing remain unmeasured. Installation, diagnostics, and manual feedback are recorded separately in [validation.md](validation.md).

For daily use and adding a new Bluetooth device through the app, see [connection and changing devices](connection-and-devices.md).
