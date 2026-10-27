# Change log

## LAB17 — Mute, battery, display latency, and boot defaults

- Added physical button 13 mute/restore using confirmed SoundSend volume.
- Added battery percentage and a mute indicator to the dedicated OLED view.
- Moved small confirmed-value updates into checked idle gaps between BLE events.
- Limited each display update to one field per poll after instruction-budget testing.
- Enabled the dedicated controls at boot with the physically established radio settings.
- Preserved the retained SRAM allocation and added sleep/mute regression coverage.
- Passed 123 offline tests; actual installation and enabled boot verified.
- Physical confirmation of the new feature behavior remains pending.

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
