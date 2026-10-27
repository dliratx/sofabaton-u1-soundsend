# Architecture

## Firmware layers

| Module | Responsibility |
| --- | --- |
| `soundsend_client.c` | ATT discovery, CCCD subscription, fragmented notification parsing, volume read/write, queued changes, and timeouts |
| `ble_central.c` | Legacy LE 1M central, CSA#1 channel hopping, connection events, SN/NESN handling, L2CAP, and graceful termination |
| `ble_packet.c` | BLE CRC, whitening, and packet encoding |
| `a8107_port.c` | A8107M0 MMIO radio and timer access, bounded waits, acquisition, and restoration |
| `u1_integration.c` | Dedicated-profile selection, button edges, mute state, display updates, battery value, and bounded diagnostics |
| `u1_trampolines.S` | Thumb entry hooks preserving the original firmware calling conventions |

The original firmware retains its other profiles, native key behavior, configuration connection, and OTA handler. The extension consumes volume-up, volume-down, and mute only when the named dedicated profile is selected and no configuration connection owns the remote.

## Operation

```mermaid
sequenceDiagram
    participant Key as Physical button
    participant U1 as U1 extension
    participant SS as SoundSend
    participant OLED as U1 display
    Key->>U1: New physical edge
    U1->>SS: Connect and discover control characteristic
    U1->>SS: Subscribe and query actual volume
    SS-->>U1: Confirm current volume
    U1->>SS: Write bounded target
    SS-->>U1: Confirm changed volume
    U1->>OLED: Small update in a checked radio-idle interval
    U1->>SS: Terminate after the warm-connection interval
    U1->>U1: Restore original radio behavior
```

The connection interval is 30 ms. A 500 ms inactivity hold allows consecutive presses to reuse a live connection. LAB16 delayed its display until this hold and termination completed; LAB17 retains connection reuse while displaying confirmed updates earlier.

## Display timing

Full screen rendering and ADC battery reads stay outside active radio ownership. During a connection, a partial update is allowed only when:

- The SoundSend view is valid and selected.
- The central is between connection events.
- No launch action, armed receiver, or receive lock is pending.
- At least 12 ms remains before the next connection anchor.

One poll draws at most one field: three volume digits or four mute-indicator characters. It checks the time again on a later poll before drawing another field. Native-renderer instruction-count tests and injected display delays exercise the budget; they do not substitute for physical timing measurement.

The existing screen gateway bypasses the native device-list renderer for a valid SoundSend selection. Other profiles, selection repair, and the native low-battery screen retain their original paths.

## Memory and sleep

The extension occupies 424 bytes of retained lower SRAM, from `0x200039D8` to `0x20003B80`. Its stack limit is `0x20003B80`, with top `0x20003FF0`. The upper LCD SRAM bank is not retained in the studied sleep modes and must not hold live protocol or mute state.

Tests overwrite the upper bank to verify preservation of volume control and the remembered mute level. Complete battery removal resets runtime state, including the remembered level and last-known volume.

## Boot defaults and configuration

LAB17 boots enabled with TX mode 10, RX-clear mode 2, and a 72 µs TX lead. These are the settings previously used for successful physical control. Unknown volume starts as `---`; no guessed percentage is shown.

Diagnostic commands use a bounded 20-byte FFF4/FFF3 exchange. They do not provide arbitrary memory access. OTA preparation still requires disabled, idle control. Post-update verification checks the actual revision, build identifier, enabled state, and radio defaults before recording a successful installation.
