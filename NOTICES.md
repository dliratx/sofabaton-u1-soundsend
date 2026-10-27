# Provenance and references

This repository documents independent firmware and protocol research for SofaBaton U1 and WiSA SoundSend. Product names identify the hardware and protocols under study. This is not a vendor release or endorsement.

The primary [wisa2mqtt SoundSend source](https://github.com/tvdstaaij/wisa2mqtt/blob/master/src/soundsend.js) was used as a protocol reference for the SoundSend service, messages, and volume-based mute approach. The repository does not require that project's MQTT bridge at runtime.

The U1 integration is based on analysis of a vendor V32 image for the studied unit. Vendor firmware binaries, decompiler output, downloaded SDKs, local toolchains, raw captures, and installation backups are not redistributed here. Their absence means that a fresh clone requires local prerequisites before full firmware assembly or vendor-code emulation can run.

See the official [Zig](https://ziglang.org/), [Unicorn](https://www.unicorn-engine.org/), [Capstone](https://www.capstone-engine.org/), and [Bleak](https://bleak.readthedocs.io/) projects for their distribution terms and documentation. A project license has not been assigned.
