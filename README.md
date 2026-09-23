# House Audio Server

Central playback, control, and synchronized-audio service for the whole-house music system.

The core rule is simple: **there is one house playback session**. Devices on the home network do not start separate competing music sessions. A room may be the only active output, or several rooms may be active, but every participating output follows the same queue, track, playback position, shuffle state, and transport state.

## Permanent server host

The permanent server is the existing **Raspberry Pi that already owns and serves the music files over Samba**.

The local Linux music root is locked as:

```text
/mnt/sharedrive/Shared Music
```

House playback should read those files directly from the local filesystem. The Pi should **not** connect back to its own Samba share for house playback.

Samba remains in place for the existing Android/Windows standalone clients. Samba and the house-audio stack are parallel consumers of the same local files.

```text
                         Raspberry Pi
                              |
                    /mnt/sharedrive/Shared Music
                         /                 \
                      Samba                MPD
                       |                    |
             existing SMB clients          | PCM
                                            v
                                       Snapserver
                                            |
                                  synchronized stream
                                  /        |         \
                               ESP32      PC(s)      other
```

## Planned permanent software stack

The proof server should be the first usable version of the real server, not a disposable test harness.

Current plan:

- **MPD** — owns the one playback session: queue, current track, transport state, seek position, shuffle, and folder-derived playlist state.
- **Snapserver** — distributes timestamped/buffered synchronized audio to renderers.
- **house-audio-server** — thin custom control/discovery layer added around the permanent stack. It will expose HOUSE-mode state/control and LAN discovery without reimplementing decoding or synchronization.
- **Samba** — continues serving the same files to existing standalone clients and is not replaced by this project.

The intended audio path is:

```text
/mnt/sharedrive/Shared Music -> MPD -> PCM/FIFO -> Snapserver -> synchronized clients
```

The exact MPD-to-Snapserver pipe/configuration will be locked only after it is tested on the Pi.

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

The intended direction is **Snapcast/Snapserver** timestamped/buffered distribution so renderers compensate for network jitter and clock drift instead of independently opening the same file and trying to stay aligned.

When an output powers up during an existing song, it should join the song at the **current house timestamp** after it connects and fills its synchronization buffer. It must not restart the track.

## Build strategy: proof becomes production

Do not create a temporary proof server that is later abandoned. Build the permanent Pi stack incrementally:

1. Configure MPD to use `/mnt/sharedrive/Shared Music` directly.
2. Feed MPD audio into Snapserver.
3. Prove a normal Snapcast client can receive the stream.
4. Prove the first ESP32-S3 can connect as a serial-only Snapcast client before adding a DAC.
5. Add the custom `house-audio-server` control/discovery service around the working stack.
6. Integrate Android and Windows HOUSE-mode control.
7. Add additional synchronized renderers.

Every successful step should remain part of the final installation.

## Initial ESP32 proof dependency

The first ESP32 test now depends on the Pi running the same Snapserver instance intended for production. No direct SMB-on-ESP32 test is required unless a future design change creates a reason for it.

## Non-goals

- multiple independent songs playing on different house nodes
- metadata-first library management
- requiring a phone to keep playback alive
- requiring every ESP32 node to mount SMB or build its own queue
- making the Raspberry Pi access its own music through SMB
- throwaway server software used only for the proof

## Related projects

- [smb-music-player](https://github.com/oolah10293/smb-music-player) — Android folder-first player/controller
- [smb-player-pc](https://github.com/oolah10293/smb-player-pc) — Windows folder-first player/controller
- [house-audio-esp32](https://github.com/oolah10293/house-audio-esp32) — ESP32-S3 synchronized renderer nodes

## Status

Architecture is now locked around the existing Raspberry Pi as the permanent host, local music root `/mnt/sharedrive/Shared Music`, MPD as the playback/session engine, and Snapserver as the synchronized distribution layer. Installation/configuration has not yet been performed.
