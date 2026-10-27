# Validation record

## Installed candidate

| Item | Value |
| --- | --- |
| Custom revision | LAB18 |
| Build ID | `0x544d5249` / TMRI |
| Actual post-update boot | 2026-10-27 16:08:45 UTC |
| Vendor-reported firmware | 32 |
| Controls enabled on boot | Verified true |
| Radio defaults | Mode 10, RX-clear 2, TX lead 72 µs |
| Offline suites | 7 |
| Offline tests passed | 132 |
| Full-transfer acknowledgements | 7,936 / 7,936 |
| SoundSend firmware changes | None |

```text
OTA package SHA-256
0ca09ee46c91f4edacf722994892416dee0e8075ea6a7bc2195eeb25dac553da

Expected installed application SHA-256
1413a56ac4a5904cefd473ff9691f843ca87d65d2c14c80045c8dd6b620d04eb

Application prefix SHA-256
0753b66651301a792ce96eeb87f718f765cff9f117f6e4259d602063a060fbcf
```

The expected installed hash is derived from the acknowledged candidate plus verified custom boot. It is **not** a full physical readback or firmware dump.

## Physical evidence already obtained

Manual testing confirmed direct U1-to-SoundSend volume changes. The initial key handler counted repeated callbacks during a held press, causing volume to reach 0 or 100. Key-edge handling corrected this behavior; subsequent manual testing confirmed volume-up/down operation and the LAB16 correction for the device-list flash. These observations are manual functional results, not instrumented RF or latency measurements.

The dedicated SoundSend profile was read back from the remote. The other three profiles retained their 50 assigned keys after the dedicated profile was configured. LAB18's post-update diagnostics verified the actual revision, exact build, enabled state, and radio defaults. The dedicated profile and four-profile metadata were read back again before enabling the physical keys and acknowledging the native configuration disconnect.

A read-only LAB18 radio query retrieved volume 45. After radio release, three hardware snapshots showed LPTIMER1 enabled and inactivity ticks advancing from 20 to 21 to 23. This verifies timer progress under a configuration connection, not physical sleep current or automatic OLED shutdown after disconnect.

The physical LAB18 held-volume test failed: separate presses changed volume, but holding a key did not repeat. A delayed-response ARM regression subsequently reproduced that failure.

## Validated LAB19 candidate

LAB19 / `0x544d524a` / TMRJ passed **134 tests across seven suites**, including delayed discovery, delayed write confirmation, and warm connection reuse after a slow operation. The correction is described in [power management](power-management.md). The original main-loop control flow was also exercised with a modeled key-matrix sampler and RF peer; 5,000 iterations continued calling the key and poll hooks.

The exact candidate passed the offline full-application installer checks. Its package SHA-256 is `79c0704cab48668e03aceebdab096c1863297e8c3d6ba37822471b259f41473c`. The expected application SHA-256 is `054665cc6b2ef3f03087d7a7580fcde5c363434257696f23666aeee11350969f`.

LAB19 is **not installed**. Connection attempts could not reach the U1; Pairing mode is required to continue. LAB18 remains the verified installed image. See [candidate evidence](evidence/lab19-candidate-summary.json) and [LAB18 installation evidence](evidence/lab18-installation-summary.json).

## Pending physical checks

Automatic screen-off in SoundSend and after leaving the profile remains pending manual confirmation. The held-volume defect remains open until a corrected candidate is installed and physically tested. Mute/restore, battery display, and perceived latency also require confirmation. Long-duration RF reliability, battery consumption, independent power-cycle behavior over repeated cycles, and recovery after a failed update are not established by the offline tests.

## Evidence included in Git

The `docs/evidence/` directory contains bounded JSON test results and an installation summary without raw BLE payload logs or local filesystem paths. Raw development receipts, exact backups, and generated binaries stay in the local research workspace. The small `fixtures/volume-roundtrip.jsonl` contains only protocol TX/RX messages needed by one regression test.
