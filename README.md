# SofaBaton U1 → WiSA SoundSend

Direct Bluetooth volume and mute control from a SofaBaton U1 remote to a WiSA SoundSend transmitter, without a computer, phone, hub, or bridge during everyday use.

This repository contains a Cortex-M0 firmware extension for U1 hardware revision 3, Windows development tools, ARM emulation tests, and protocol and validation documentation.

## The problem

When HDMI-CEC volume control is unavailable, the SoundSend app remains a working control interface, but it does not provide physical remote buttons. Direct control from the U1 requires a Bluetooth central that implements SoundSend's GATT protocol: service discovery, notification subscription, volume queries, and volume writes. A conventional Bluetooth remote-control key mapping does not provide those operations.

## The solution

The U1 firmware extension implements a small BLE central, a SoundSend ATT client, and hooks into the remote's key and display routines. Selecting the dedicated **SoundSend** profile activates this control path. The extension reads the actual volume, sends a bounded change, and shows the value confirmed by the transmitter.

```mermaid
flowchart TD
    K[U1 buttons] --> P["SoundSend<br/>profile"]
    P --> C["BLE central<br/>ATT client"]
    C <-->|Direct Bluetooth| S[SoundSend]
    S --> A[Speakers]
    C --> O["OLED<br/>Volume, mute<br/>Battery"]
    D[PC and UGREEN] -. Development and OTA only .-> P
```

Only the **U1 firmware** is modified. SoundSend retains its original firmware. Bluetooth carries control messages here, not the audio stream.

## Current status

**LAB17 / TMRH was installed and its actual boot verified on 2026-10-27 at 05:07:36 UTC.** It starts with the dedicated controls enabled and the previously verified radio configuration. The vendor application still identifies the underlying firmware as version 32; custom diagnostics identify revision 17.

| Capability | Evidence |
| --- | --- |
| Direct U1 → SoundSend volume changes | Confirmed through manual testing on earlier installed revisions |
| One volume step per physical press | Implemented and ARM-tested; up/down operation confirmed through manual testing |
| Dedicated SoundSend profile | Read back from the remote; other profiles preserved |
| Stable volume screen without profile-menu flash | LAB16 correction confirmed through manual testing |
| Mute and restore previous volume | Implemented and emulation-tested in LAB17; physical button confirmation pending |
| Earlier display update and battery percentage | Implemented and tested in LAB17; physical display confirmation pending |
| Controls enabled after boot | Verified in the actual LAB17 post-update boot |
| Offline validation | **123 tests passed** across seven suites |

See [validation and installation evidence](docs/validation.md) for exact hashes, limits, and outstanding checks. Test success is not a claim of universal hardware compatibility or proven recovery.

## Controls

Select the **SoundSend** profile on the U1 and keep the transmitter powered.

| Button | Action |
| --- | --- |
| Volume up | Increase actual volume by one, up to 100 |
| Volume down | Decrease actual volume by one, down to 0 |
| Mute | Read the actual level, remember it, and set volume to 0 |
| Mute again | Restore the remembered level if the actual level is still 0 |

A held button counts once until released. Volume keys leave mute starting from the actual zero level. A different controller's intervening volume change is read before the next operation. The remembered mute level survives normal retained sleep, but not battery removal or a full reset. The installed OLED volume label is `Volumen`; repository documentation is English.

## Repository guide

| Location | Purpose |
| --- | --- |
| [docs/problem-and-solution.md](docs/problem-and-solution.md) | Requirements, investigation, and design decisions |
| [docs/architecture.md](docs/architecture.md) | Firmware modules, timing, memory, and integration |
| [docs/protocol.md](docs/protocol.md) | Verified SoundSend messages and U1 controls |
| [docs/build-and-test.md](docs/build-and-test.md) | Offline build setup and test reproduction |
| [docs/installation.md](docs/installation.md) | Unit-specific OTA workflow and constraints |
| [docs/validation.md](docs/validation.md) | Recorded results and remaining physical checks |
| [CHANGELOG.md](CHANGELOG.md) | Key experimental revisions |
| [firmware/](firmware/) | C, Thumb assembly, linker scripts, builders, and ARM tests |
| `u1_*.py`, `ble_probe.py` | Windows installation, identification, and diagnostic tools |
| [fixtures/](fixtures/) | Minimal protocol-only test transcript |
| [scripts/](scripts/) | Local preparation and prerequisite checks |
| [NOTICES.md](NOTICES.md) | References, provenance, and excluded vendor artifacts |

The host scripts remain at the repository root because their imports and artifact paths depend on that layout. Raw captures, machine-specific state, credentials, vendor binaries, generated firmware, and toolchains are excluded from Git.

## Scope

This is a version-specific research implementation, not a generic U1 update. The physical unit originally ran firmware 33; the available patch base is the vendor's V32 image. An exact FW33 backup and a tested recovery procedure are unavailable. The code contains guards for the studied unit, profile, adapter, base hash, and installed state. A fresh clone can support offline reproduction after supplying the required local assets; it is deliberately insufficient for an unattended flash of another remote.
