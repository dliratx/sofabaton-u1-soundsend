# Observed protocols

This document describes the studied devices and captured behavior. It does not imply that all SoundSend or U1 hardware versions expose identical internals.

## SoundSend GATT

| Item | UUID |
| --- | --- |
| Control service | `97ded94c-b564-48ab-ba96-7e1d2daa0edd` |
| Control characteristic | `a4a8d442-b8d0-404c-a0fb-f120115acf5e` |
| Observed advertising UUID | `2d513513-694a-4751-8366-f6ee79b5f0ab` |

The advertising UUID is not the control-service UUID. The characteristic supports notifications and write without response. Handles are discovered rather than assumed to remain constant.

Messages use a three-byte header followed by the indicated payload:

| Operation | Application bytes | Meaning |
| --- | --- | --- |
| Query volume | `00 04 00` | Read field 4 with no payload |
| Set volume | `01 04 01 vv` | Write field 4 with one value byte |
| Volume notification | `02 04 01 vv` | Confirm field 4 with one value byte |

`vv` is a binary integer from 0 to 100, not an ASCII number. Decimal 18 is byte `12` in hexadecimal.

The ATT client discovers the service, characteristic, and CCCD, enables notifications, and queries volume. It handles notifications split across fragments. After a write, only a notification matching the sent value completes that write; delayed notifications cannot erase queued key changes.

## Mute semantics

The implementation uses `set volume 0` and a retained previous level. The same strategy appears in the primary [wisa2mqtt SoundSend implementation](https://github.com/tvdstaaij/wisa2mqtt/blob/master/src/soundsend.js), used here as a protocol reference. This project does not run that bridge.

Two pending mute toggles before a query completes cancel each other. Muting an already-zero device with no saved level does not invent a louder restore value. Volume keys cancel mute intent and adjust the actual level. An external nonzero change prevents restoration of an obsolete saved value.

## U1 key and profile integration

| Logical button | Function |
| --- | --- |
| 4 | Volume up |
| 10 | Volume down |
| 13 | Mute |

The native dispatcher repeatedly invokes the key handler while a key is held. Current key byte `0x20000136` and previous key byte `0x20000135` identify a physical edge. Counting handler calls instead caused the original runaway-to-0/100 defect.

The dedicated profile has device ID 2 and the native name `SoundSend`. Its empty native key table is intentional. Other profile records and their 50 assigned keys were verified after replacement of the former Fire TV profile.

## U1 development interfaces

| Interface | UUID |
| --- | --- |
| App service | `0000fff0-0000-1000-8000-00805f9b34fb` |
| App notifications | `0000fff1-0000-1000-8000-00805f9b34fb` |
| App writes | `0000fff2-0000-1000-8000-00805f9b34fb` |
| Diagnostic response | `0000fff3-0000-1000-8000-00805f9b34fb` |
| Diagnostic request | `0000fff4-0000-1000-8000-00805f9b34fb` |

The custom diagnostic request begins `53 53 01`, includes an operation byte, and ends with an 8-bit checksum. Responses identify the custom revision and, through a separate query, the build. The normal vendor app firmware number remains 32 because this is a V32-based extension.

The original OTA transfer uses 16-byte application chunks with individually checked address acknowledgements. A complete acknowledged transfer is not sufficient evidence of activation: the actual post-reset build must be read back.
