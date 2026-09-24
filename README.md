# House Audio Server

Central playback, control, and synchronized-audio service for the whole-house music system.

The core rule is simple: **there is one house playback session**. Devices on the home network do not start separate competing music sessions. A room may be the only active output, or several rooms may be active, but every participating output follows the same queue, track, playback position, shuffle state, and transport state.

## Permanent server host

The permanent server is the existing **Raspberry Pi that already owns and serves the music files over Samba**.

The local Linux music root is locked as:

```text
/mnt/sharedrive/John/Shared Music
```

House playback reads those files directly from the local filesystem. The Pi does **not** connect back to its own Samba share for house playback.

Samba remains in place for the existing Android/Windows standalone clients. Samba and the house-audio stack are parallel consumers of the same local files.

```text
                         Raspberry Pi
                              |
                 /mnt/sharedrive/John/Shared Music
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

## Permanent software stack

The proof server is the first usable version of the real server, not a disposable test harness.

- **MPD** — owns the one playback session: queue, current track, transport state, seek position, shuffle, and folder-derived playlist state.
- **Snapserver** — distributes timestamped/buffered synchronized audio to renderers.
- **house-audio-server** — thin custom control/discovery layer to be added around the permanent stack. It will expose HOUSE-mode state/control and LAN discovery without reimplementing decoding or synchronization.
- **Samba** — continues serving the same files to existing standalone clients and is not replaced by this project.

The proven audio path is:

```text
/mnt/sharedrive/John/Shared Music
        -> MPD
        -> /tmp/snapfifo (48000:16:2 PCM)
        -> Snapserver
        -> FLAC Snapcast stream
        -> synchronized clients
```

### Verified MPD configuration

MPD 0.24.4 is configured against the real library:

```text
music_directory "/mnt/sharedrive/John/Shared Music"
```

Its Snapcast output is:

```text
audio_output {
        type            "fifo"
        name            "Snapcast"
        path            "/tmp/snapfifo"
        format          "48000:16:2"
        mixer_type      "software"
}
```

The MPD user can read the real music root, the library has been indexed, and folder-first browsing is proven. Top-level folders observed through MPD include `CDs`, `Country`, `MP3s`, `Oldies`, and `Rap`.

### Verified Snapserver path

Snapserver 0.31.0 is running on Debian 13 (trixie), aarch64, and consumes `/tmp/snapfifo` as the `default` stream. Logs have confirmed:

```text
sampleFormat: 48000:16:2
codec: flac
state: idle => playing
```

A real MP3 from the library was decoded by MPD and carried through the FIFO into Snapserver.

### Verified ESP32-S3 client proof

The permanent Snapserver has also been proven with the real target renderer hardware: a Seeed Studio XIAO ESP32-S3 running an ESPHome/ESP-IDF Snapcast client.

The client:

- joined the home LAN
- connected to Snapserver on TCP port 1704
- completed the Snapcast hello/stream handshake
- negotiated FLAC at `48000:16:2`
- filled its 1000 ms timing buffer
- changed mute state when playback started/stopped
- continuously received and acknowledged the actual audio stream

Server-side TCP counters proved sustained payload transfer to the XIAO. In one 59-second sample, `bytes_sent` increased by **6,292,952 bytes** and `data_segs_out` increased by **5,343**, approximately **0.85 Mbit/s** of sustained stream traffic. This closes the ambiguity between "connected to Snapserver" and "actually receiving the song."

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

The system uses **Snapcast/Snapserver** timestamped/buffered distribution so renderers compensate for network jitter and clock drift instead of independently opening the same file and trying to stay aligned.

When an output powers up during an existing song, it should join the song at the **current house timestamp** after it connects and fills its synchronization buffer. It must not restart the track.

## Build strategy: proof becomes production

Do not create a temporary proof server that is later abandoned. Build the permanent Pi stack incrementally:

1. **DONE** — Configure MPD to use `/mnt/sharedrive/John/Shared Music` directly.
2. **DONE** — Feed MPD audio into Snapserver through `/tmp/snapfifo`.
3. **DONE** — Prove Snapserver exposes and carries the real audio stream.
4. **DONE** — Prove the first ESP32-S3 can receive that stream without a DAC.
5. **NEXT** — Add an I2S DAC to the ESP32 renderer and produce real audio.
6. Add the custom `house-audio-server` control/discovery service around the working stack.
7. Integrate Android and Windows HOUSE-mode control.
8. Add additional synchronized renderers and perform the audible room-to-room synchronization test.

Every successful step remains part of the final installation.

## Remaining server-side Phase 1 check

The working MPD -> FIFO -> Snapserver -> ESP32 path is proven live. A reboot/service-startup test is still needed before the server foundation issue is considered completely closed.

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

**MPD -> Snapserver -> ESP32-S3 network reception is proven on the permanent hardware and permanent Pi stack.** The next renderer step is an I2S line-level DAC and actual audio output. The remaining server-foundation check is service recovery after reboot.