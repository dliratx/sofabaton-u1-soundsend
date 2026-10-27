# Validation record

## Installed candidate

| Item | Value |
| --- | --- |
| Custom revision | LAB19 |
| Build ID | `0x544d524a` / TMRJ |
| Actual post-update boot | 2026-10-27 16:42:40 UTC |
| Vendor-reported firmware | 32 |
| Controls enabled on boot | Verified true |
| Radio defaults | Mode 10, RX-clear 2, TX lead 72 µs |
| Offline suites | 7 |
| Offline tests passed | 134 |
| Full-transfer acknowledgements | 7,936 / 7,936 |
| SoundSend firmware changes | None |

```text
OTA package SHA-256
79c0704cab48668e03aceebdab096c1863297e8c3d6ba37822471b259f41473c

Expected installed application SHA-256
054665cc6b2ef3f03087d7a7580fcde5c363434257696f23666aeee11350969f

Application prefix SHA-256
ef9d46286de46e3af2cff8fb50a63d6b7aecc6a69435c8e3d5da0b744edca405
```

The expected installed hash is derived from the acknowledged candidate plus verified custom boot. It is **not** a full physical readback or firmware dump.

## Physical evidence already obtained

Manual testing confirmed direct U1-to-SoundSend volume changes. The initial key handler counted repeated callbacks during a held press, causing volume to reach 0 or 100. Key-edge handling corrected this behavior; subsequent manual testing confirmed volume-up/down operation and the LAB16 correction for the device-list flash. These observations are manual functional results, not instrumented RF or latency measurements.

The dedicated SoundSend profile was read back from the remote. The other three profiles retained their 50 assigned keys after the dedicated profile was configured. LAB18's post-update diagnostics verified the actual revision, exact build, enabled state, and radio defaults. The dedicated profile and four-profile metadata were read back again before enabling the physical keys and acknowledging the native configuration disconnect.

A read-only LAB18 radio query retrieved volume 45. After radio release, three hardware snapshots showed LPTIMER1 enabled and inactivity ticks advancing from 20 to 21 to 23. This verifies timer progress under a configuration connection, not physical sleep current or automatic OLED shutdown after disconnect.

The physical LAB18 held-volume test failed: separate presses changed volume, but holding a key did not repeat. A delayed-response ARM regression subsequently reproduced that failure.

## LAB19 installation and diagnostics

LAB19 / `0x544d524a` / TMRJ passed **134 tests across seven suites**, including delayed discovery, delayed write confirmation, and warm connection reuse after a slow operation. The correction is described in [power management](power-management.md). The original main-loop control flow was also exercised with a modeled key-matrix sampler and RF peer; 5,000 iterations continued calling the key and poll hooks.

The exact candidate passed the offline full-application installer checks. Its package SHA-256 is `79c0704cab48668e03aceebdab096c1863297e8c3d6ba37822471b259f41473c`. The expected application SHA-256 is `054665cc6b2ef3f03087d7a7580fcde5c363434257696f23666aeee11350969f`.

LAB19 was installed with all 7,936 blocks acknowledged. Fresh post-update diagnostics verified revision 19, build TMRJ, enabled startup, and the expected radio settings. After a read-only radio operation, three hardware snapshots showed LPTIMER1 enabled and inactivity ticks advancing from 43 to 44 to 46.

That first SoundSend query did not establish a working link: its terminal central reason was `0x3e` and volume remained unknown. A follow-up PC connection timed out before issuing another query. The subsequent physical-key activation attempt also could not reach the U1. The diagnostic had disabled the key override in volatile state; final reactivation is pending, while the verified full-boot default remains enabled. A hardware volume or hold success is not claimed for LAB19.

See [LAB19 installation evidence](evidence/lab19-installation-summary.json), [candidate evidence](evidence/lab19-candidate-summary.json), and the historical [LAB18 installation evidence](evidence/lab18-installation-summary.json).

## Pending physical checks

Final reactivation after diagnostics can use the guarded host activation helper or a full U1 restart, which restores the verified enabled boot default. Automatic screen-off in SoundSend and after leaving the profile remains pending manual confirmation. The held-volume defect remains open until the installed correction is physically tested. Mute/restore, battery display, and perceived latency also require confirmation. Long-duration RF reliability, battery consumption, independent power-cycle behavior over repeated cycles, and recovery after a failed update are not established by the offline tests.

## Evidence included in Git

The `docs/evidence/` directory contains bounded JSON test results and an installation summary without raw BLE payload logs or local filesystem paths. Raw development receipts, exact backups, and generated binaries stay in the local research workspace. The small `fixtures/volume-roundtrip.jsonl` contains only protocol TX/RX messages needed by one regression test.
