# Validation record

## LAB20 installation: native Bluetooth handoff

LAB20 / `0x544d524b` / TMRK passed **139 tests across seven suites** and the full-application installer preflight. It ends the native HID reconnect policy before asynchronous radio handoff and adds separate connecting, active-session, and error indicators. The package was installed with 7,936 acknowledged blocks. Actual LAB20 boot and enabled radio defaults were verified on 2026-10-27 at 19:44:04 UTC. A subsequent read confirmed all five profile records unchanged and enabled controls; the native configuration disconnect was acknowledged. See [installation evidence](evidence/lab20-installation-summary.json).

The exact candidate has package SHA-256 `5db5d7f7c48cc4e0cb32b95ef6f4c66a3bb553114316aee63cf4f208a139f34c`, expected application SHA-256 `f3a5c1f8caedee8b01fd4386bfefb5d1c74a3acaa872eceac56292b683921253`, and application-prefix SHA-256 `2483e8d222ae47d896d24084f31bbf770c55f85681fb7a4c5fa40c93ac9dcdcc`.

Before installation, a live read at 2026-10-27 19:13 UTC confirmed LAB19 and five profiles: Proyector (1), SoundSend (2), Android TV (3), HDMI (4), and the newly added Fire TV (5). No old Fire TV backup was restored. The cached volume was 55, with the last central attempt failed at reason `0x08`. This diagnostic does not prove that the reproduced native reconnect race was the only physical failure cause.

The regression executes the original native post-poll HID restart branch with modeled asynchronous disconnect and RF behavior. LAB19 restarted the previous advertiser 27 times in 32 modeled poll iterations; LAB20 acquires the radio and completes the modeled SoundSend operation. Additional cases cover the return to the native HID key path, pairing exclusion, and honest link indicators. Successful repeated physical Fire TV-to-SoundSend switching remains unconfirmed for LAB20. See [handoff details](bluetooth-profile-handoff.md) and [candidate evidence](evidence/lab20-candidate-summary.json).

The first LAB20 manual attempt displayed `!` and did not control SoundSend. Diagnostics showed scan timeout `0x08`, zero transmitted packets, and no confirmed volume. A 15-second UGREEN scan found 16 nearby devices but not the exact SoundSend address; the iPhone also could not find the powered transmitter. SoundSend advertisements reappeared after a transmitter restart was announced. The scan was passive and did not connect the PC to SoundSend. This identifies unavailability during that attempt, without establishing why advertising stopped. A repeated switching test with the transmitter available is pending. See [manual feedback and discovery evidence](evidence/lab20-manual-feedback.json).

## Installed candidate

| Item | Value |
| --- | --- |
| Custom revision | LAB20 |
| Build ID | `0x544d524b` / TMRK |
| Actual post-update boot | 2026-10-27 19:44:04 UTC |
| Vendor-reported firmware | 32 |
| Controls enabled on boot | Verified true |
| Radio defaults | Mode 10, RX-clear 2, TX lead 72 µs |
| Offline suites | 7 |
| Offline tests passed | 139 |
| Full-transfer acknowledgements | 7,936 / 7,936 |
| SoundSend firmware changes | None |

```text
OTA package SHA-256
5db5d7f7c48cc4e0cb32b95ef6f4c66a3bb553114316aee63cf4f208a139f34c

Expected installed application SHA-256
f3a5c1f8caedee8b01fd4386bfefb5d1c74a3acaa872eceac56292b683921253

Application prefix SHA-256
2483e8d222ae47d896d24084f31bbf770c55f85681fb7a4c5fa40c93ac9dcdcc
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

That first SoundSend query did not establish a working link: its terminal central reason was `0x3e` and volume remained unknown. A follow-up PC connection timed out before issuing another query. The subsequent physical-key activation attempt also could not reach the U1. The diagnostic had disabled the key override in volatile state, leaving reactivation and manual feedback outstanding at the end of that instrumented session. The verified full-boot default remained enabled. These are historical diagnostic results; subsequent manual feedback is recorded below.

See [LAB19 installation evidence](evidence/lab19-installation-summary.json), [candidate evidence](evidence/lab19-candidate-summary.json), and the historical [LAB18 installation evidence](evidence/lab18-installation-summary.json).

## Subsequent manual acceptance and photographs

On 2026-10-27, subsequent feedback reported that the remote now works as intended. This is overall manual functional acceptance of the installed setup and supersedes the earlier status awaiting feedback after diagnostics. The report was not a separate checklist for every feature, and no new instrumented session or firmware transfer accompanied it.

Three original photographs were supplied for publication:

| Photograph | Visible evidence |
| --- | --- |
| [Dedicated SoundSend view](images/u1-soundsend-display.png) | The physical U1 displays SoundSend, volume 42%, and battery 100%. |
| [Native profile menu](images/u1-native-profile-menu.png) | The native menu displays Proyector, HDMI, and Android TV. |
| [Rear hardware label](images/u1-rear-label.png) | Model U1 and FCC ID 2AT2U-U1. |

The photographs were copied without image transformations. They show interface content and hardware identification, not key timing, a live Bluetooth session, battery-estimator accuracy, or the screen-off sequence. Their hashes, dimensions, and the scope of the manual report are recorded in [manual feedback and photo evidence](evidence/lab19-manual-feedback.json).

## Remaining measurement limits

LAB19's earlier SoundSend-only use received manual functional acceptance; the subsequent Fire TV-to-SoundSend failure is recorded above. LAB20 is installed and still requires physical switching validation. Separate measured hold/release timing, automatic screen-off timing in each profile, a mute/restore checklist, and display latency were not supplied with the earlier report. Long-duration RF reliability, battery consumption, independent power-cycle behavior over repeated cycles, and recovery after a failed update remain uncharacterized. The photographs establish that the volume and battery display was present on the physical LAB19 remote.

## Evidence included in Git

The `docs/evidence/` directory contains bounded JSON test results, installation summaries, and subsequent manual feedback without raw BLE payload logs or local filesystem paths. Installation summaries retain the state at the end of their diagnostic sessions; later feedback is separate evidence. Original hardware photographs are in `docs/images/`. Raw development receipts, exact backups, and generated binaries stay in the local research workspace. The small `fixtures/volume-roundtrip.jsonl` contains only protocol TX/RX messages needed by one regression test.
