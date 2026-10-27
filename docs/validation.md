# Validation record

## Installed candidate

| Item | Value |
| --- | --- |
| Custom revision | LAB17 |
| Build ID | `0x544d5248` / TMRH |
| Actual post-update boot | 2026-10-27 05:07:36 UTC |
| Vendor-reported firmware | 32 |
| Controls enabled on boot | Verified true |
| Radio defaults | Mode 10, RX-clear 2, TX lead 72 µs |
| Offline suites | 7 |
| Offline tests passed | 123 |
| Full-transfer acknowledgements | 7,936 / 7,936 |
| SoundSend firmware changes | None |

```text
OTA package SHA-256
a75b9dd99b6b3b7ac278f1300ee3cbfdf7a235c4b86ae0262e40243d0e8c3da2

Expected installed application SHA-256
11261280c6e5a4a8ade9ac1943b6f47f43c4fe2c93943ce7680fa75fab39912e

Application prefix SHA-256
b04f3018cfbe2d963315cd4b164811cc6c7bfd1814447f1187816fe028e7eaee
```

The expected installed hash is derived from the acknowledged candidate plus verified custom boot. It is **not** a full physical readback or firmware dump.

## Physical evidence already obtained

The owner confirmed that the U1 directly changed SoundSend volume. The first implementation repeated a held key and ran to an endpoint; later key-edge handling corrected that behavior. The owner then confirmed volume-up/down operation and the LAB16 correction for the device-list flash.

The dedicated SoundSend profile was read back from the remote. The other three profiles retained their 50 assigned keys after the Fire TV replacement. LAB17's post-update diagnostics verified the actual revision, exact build, enabled state, and radio defaults.

## Pending physical checks

The new LAB17 feature behavior still needs owner confirmation: one mute press silences, a second restores the previous level, the battery appears, and the display responds sooner. Long-duration RF reliability, battery consumption, independent power-cycle behavior over repeated cycles, and recovery after a failed update are not established by the offline tests.

## Evidence included in Git

The `docs/evidence/` directory contains bounded JSON test results and an installation summary without raw BLE payload logs or local filesystem paths. Raw development receipts, exact backups, and generated binaries stay in the local research workspace. The small `fixtures/volume-roundtrip.jsonl` contains only protocol TX/RX messages needed by one regression test.
