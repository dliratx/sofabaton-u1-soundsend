# Change log

## LAB20 — Native Bluetooth handoff and connection feedback

- End the previous native HID retry policy before asynchronous radio acquisition, preventing the original main loop from restarting the Fire TV advertiser between SoundSend polls.
- Preserve native profile records and the HID key path; yield custom keys during app and device-pairing screens.
- Add connecting, active-session, and failure indicators separate from the cached volume percentage, within the existing display timing budget and 424-byte retained state.
- Accept additional native device profiles in the physical-key activation helper while retaining the exact SoundSend profile guard.
- Passed 139 offline tests and exact-package installer preflight. Installation and physical switching validation are pending; LAB19 remains installed.
- Document app-based creation of a new Fire TV profile and distinguish it from U1 configuration pairing.

## Documentation — Hardware photos and connection guide

- Added three original photographs showing the SoundSend display, native profile menu, and U1 rear label.
- Recorded subsequent manual feedback that the installed LAB19 setup works as intended, separately from the earlier diagnostic-session results.
- Documented everyday use, reconnection, configuration pairing, and the fixed SoundSend address.
- Explained the rebuild required for another transmitter and the identity and hardware checks required for another U1.
- No firmware or device settings changed in this documentation update.

## LAB19 — Preserve the repeat window after a slow response

- Start the ready interval when discovery or a volume write is confirmed, so a slow operation cannot consume the entire idle window.
- Added regression coverage for delayed discovery, delayed volume confirmation, and a subsequent press after a slow operation.
- Passed 134 offline tests across seven suites; the exact OTA candidate passed installer preflight validation.
- This addresses a timing defect that reproduces LAB18's failed physical hold test; subsequent manual feedback reports that the remote now works as intended.
- Installed with 7,936 acknowledged blocks; actual LAB19 boot, build identity, and enabled defaults verified.
- Hardware inactivity ticks advanced after radio release. The first read-only SoundSend query failed to establish a working link; later manual acceptance is recorded separately from that diagnostic result.

## LAB18 — Native sleep restoration and bounded volume repetition

- Fixed the inactivity timer left stopped after radio acquisition in LAB17.
- Preserved a pending native inactivity interrupt when releasing the radio.
- Kept the original sleep path reachable after volume use and profile changes.
- Added volume hold repetition after 400 ms, then every 120 ms with confirmation gating.
- Kept mute edge-triggered and stopped timed repeats when the key is released.
- Extended the bounded warm connection interval from 500 to 800 ms for successive presses.
- Added a read-only power snapshot and native ARM regression coverage.
- Passed 132 offline tests; actual installation and enabled boot verified.
- Read SoundSend's volume on hardware and verified inactivity ticks advancing after radio release.
- The inactivity timer was verified on hardware, but the physical held-volume test still produced only one change. LAB19 adds delayed-response coverage and a timing correction.

## LAB17 — Mute, battery, display latency, and boot defaults

- Added physical button 13 mute/restore using confirmed SoundSend volume.
- Added battery percentage and a mute indicator to the dedicated OLED view.
- Moved small confirmed-value updates into checked idle gaps between BLE events.
- Limited each display update to one field per poll after instruction-budget testing.
- Enabled the dedicated controls at boot with the physically established radio settings.
- Preserved the retained SRAM allocation and added sleep/mute regression coverage.
- Passed 123 offline tests; actual installation and enabled boot verified.
- Physical confirmation was still pending at this revision; later installation and manual feedback are recorded under LAB19.

## LAB16 — Stable dedicated screen

- Bypassed the native profile-list renderer for a valid SoundSend selection.
- Preserved native selection repair, low-battery handling, and other profiles.
- Manual testing confirmed the flicker correction; display latency remained observable.

## LAB15 — Physical key edges and dedicated profile

- Counted current/previous native key edges rather than repeated held callbacks.
- Cleared completed acquisition requests to prevent unsolicited reconnections.
- Restricted interception to the named SoundSend profile.
- Added the confirmed-volume OLED view.
- Configured the dedicated SoundSend profile and verified preservation of the other profiles.

## LAB14 — Direct control established

- Removed extra connection-packet construction from the tight initiating interval.
- Achieved direct U1-to-SoundSend volume control on the studied hardware.
- Manual testing identified the held-key repetition issue corrected in LAB15.

Earlier revisions established diagnostics, OTA transport, retained memory placement, packet encoding, radio launch behavior, and timing compensation. Their incomplete claims are not presented as current implementation status.
