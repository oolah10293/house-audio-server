# House Audio Server

Central playback, control, and synchronized-audio service for the whole-house music system.

The core rule is simple: **there is one house playback session**. Devices on the home network do not start separate competing music sessions. A room may be the only active output, or several rooms may be active, but every participating output follows the same queue, track, playback position, shuffle state, and transport state.

## System role

The server is the authoritative owner of:

- the current folder/queue
- current track and exact playback position
- play / pause / seek / previous / next
- shuffle state
- the synchronized audio stream
- shared state exposed to controllers

The music library remains filesystem-first: **folders are playlists**. The server must not require a metadata-first library database.

Controllers are disposable. An Android phone or Windows player can start or control playback and then disappear; the house session must continue without that controller remaining open.

## Related projects

- [smb-music-player](https://github.com/oolah10293/smb-music-player) — Android folder-first player/controller
- [smb-player-pc](https://github.com/oolah10293/smb-player-pc) — Windows folder-first player/controller
- [house-audio-esp32](https://github.com/oolah10293/house-audio-esp32) — ESP32-S3 synchronized renderer nodes

## HOUSE vs STANDALONE behavior

Android and Windows clients should select behavior automatically.

**HOUSE** means the client can discover and verify this service directly on the physical home LAN. Wi-Fi and Ethernet both count.

**STANDALONE** means the house service is not present on the local LAN. The existing Android/Windows players then behave exactly as they do today.

Preferred discovery direction:

1. Advertise a local service with mDNS / DNS-SD, provisionally `_houseaudio._tcp.local`.
2. Client performs a short handshake with the discovered service before entering HOUSE mode.
3. A fixed/reserved LAN address may be used as a fallback, but the client must verify that the route is through a normal LAN interface rather than a VPN/tunnel.
4. **Tailscale/VPN reachability alone must never trigger HOUSE mode.** A phone or laptop away from home remains STANDALONE even if it can reach the house through Tailscale.

GPS and SSID checks are not required for the normal decision. The useful question is not "am I geographically near home?" but "am I directly attached to the LAN that contains the house-audio service?"

## Synchronized playback

The intended direction is a Snapcast-style timestamped/buffered stream so renderers compensate for network jitter and clock drift instead of independently opening the same file and trying to stay aligned.

The exact transport is **not locked yet**. Existing Snapcast-compatible ESP32-S3 client implementations should be tested before custom synchronization code is written.

When an output powers up during an existing song, it should join the song at the **current house timestamp** after it connects and fills its synchronization buffer. It must not restart the track.

## Server host

The final server hardware/OS is intentionally undecided. It only needs to be an always-on machine that can access the music library and run the control/session service plus the synchronized-audio server.

## Initial milestones

1. Establish a minimal server process that advertises itself on the LAN and exposes health/session state.
2. Prove one ESP32-S3 can discover/connect and receive the synchronized stream.
3. Add an I2S DAC and prove real audio from one ESP32 node.
4. Add a second renderer and verify that room-to-room echo is effectively inaudible.
5. Integrate HOUSE-mode control into the existing Android and Windows players without disturbing their STANDALONE behavior.

## Non-goals

- multiple independent songs playing on different house nodes
- metadata-first library management
- requiring a phone to keep playback alive
- requiring every ESP32 node to mount SMB or build its own queue

## Status

Architecture / proof-of-concept stage. Do not lock the final synchronization transport until the ESP32-S3 renderer test is complete.
