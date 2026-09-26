# House Audio Server

Central playback, control, and synchronized-audio service for the whole-house music system.

The core rule is simple: **there is one house playback session**. Devices on the home network do not start separate competing music sessions. A room may be the only active output, or several rooms may be active, but every participating output follows the same queue, track, playback position, shuffle state, and transport state.

## Agreed playback and session behavior

**Read [docs/SESSION_BEHAVIOR.md](docs/SESSION_BEHAVIOR.md) before implementing the control service or integrating any client.** It records the agreed rules for the Pi, Android app, PC player, browser controller, and passive nodes. These are approved requirements, not implemented features.

The important session distinctions are:

- With nobody connected, the Pi is idle, except while finishing the last playing track after everyone disconnects.
- Only a **passive node** starts music automatically from fresh idle: the default `MP3s` folder, Shuffle, Repeat All, continuing its saved rotation. A phone, PC, or browser connecting first waits for Play.
- A controller joining existing playback adopts the current song and queue. Changes it deliberately makes remain in effect after that controller leaves while other nodes remain.
- All nodes disconnecting during playback means finish the current track, then stop despite Repeat All. A node returning before track end cancels the pending stop and preserves the session.
- Only a muted phone remaining means **pause and retain the playlist, track, and exact position**, not finish and discard the session. An audible node returning or that phone unmuting resumes the retained session.
- Save the default MP3s shuffle order and progress separately from controller-selected queues. Continue the remaining order next session; generate a fresh shuffle after the complete cycle without an immediate boundary repeat.
- HOUSE/standalone authority is separate from local output mute. The phone gets a HOUSE-only **Mute output / Unmute output** button. Leaving home while unmuted and playing automatically continues the same song through standalone SMB/Tailscale without sending a stop or queue replacement to MPD; muted/paused phones stay silent.

The accepted browser interface is another folder-first controller alongside the Android and Windows players. It should share their Pi-side control service rather than introduce a different player or queue. Detailed edge cases and implementation questions are explicitly separated from confirmed decisions in the behavior document.

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
- reported a configured buffer length of 1000 ms and `latency buffer full`
- logged mute/unmute activity during the test
- continuously received and acknowledged the actual audio stream

Server-side TCP counters showed sustained payload transfer to the XIAO. In one 59-second sample, `bytes_sent` increased by **6,292,952 bytes** and `data_segs_out` increased by **5,343**, approximately **0.85 Mbit/s** of sustained stream traffic, with acknowledgements tracking transmission. `bytes_sent` alone is not reception proof; follow `bytes_acked` on the current client connection as well. This is a network-reception milestone, not yet proof of audible DAC output or two-room synchronization.

## System role

The server is the authoritative owner of:

- the current folder/queue
- current track and exact playback position
- play / pause / seek / previous / next
- shuffle state
- the synchronized audio stream
- shared state exposed to controllers

The music library remains filesystem-first: **folders are playlists**. The server must not require a metadata-first library database.

Controllers are optional. An Android phone or Windows player can select music and then disappear while remaining outputs continue using that queue. When no audible outputs or no nodes remain, apply the pause/finish/idle rules in [Session behavior](docs/SESSION_BEHAVIOR.md), rather than making the Pi play forever.

## HOUSE vs STANDALONE behavior

Android and Windows clients should select behavior automatically. This integration is planned, not implemented.

**HOUSE** means the client discovers and verifies this service directly on the physical home LAN. Wi-Fi and Ethernet both count. Temporary loss while at home stays HOUSE/reconnecting rather than starting a competing independent queue.

**STANDALONE** is the away-from-home independent playback mode. A failed discovery request alone must not be treated as proof of departure. The phone's agreed same-song departure handoff and mute behavior are documented in [Session behavior](docs/SESSION_BEHAVIOR.md#8-automatic-homeaway-selection-and-phone-handoff).

Preferred discovery direction:

1. Advertise a local service with mDNS / DNS-SD, provisionally `_houseaudio._tcp.local`.
2. Client performs a short handshake with the discovered service before entering HOUSE mode.
3. A fixed/reserved LAN address may be used as a fallback, but the client must verify that the route is through a normal LAN interface rather than a VPN/tunnel.
4. **Tailscale/VPN reachability alone must never trigger HOUSE mode.** A phone or laptop away from home remains STANDALONE even if it can reach the house through Tailscale.

GPS and SSID checks are not required for the normal decision. The useful question is not "am I geographically near home?" but "am I directly attached to the LAN that contains the house-audio service?" Network-transition grace periods and ambiguous cases still need implementation definition.

## Synchronized playback

The system uses **Snapcast/Snapserver** timestamped/buffered distribution so renderers compensate for network jitter and clock drift instead of independently opening the same file and trying to stay aligned.

When an output powers up during an existing song, it should join the song at the **current house timestamp** after it connects and fills its synchronization buffer. It must not restart the track. This describes the target audible behavior; two-output synchronization remains to be tested.

## Build strategy: proof becomes production

Do not create a temporary proof server that is later abandoned. Build the permanent Pi stack incrementally:

1. **DONE** — Configure MPD to use `/mnt/sharedrive/John/Shared Music` directly.
2. **DONE** — Feed MPD audio into Snapserver through `/tmp/snapfifo`.
3. **DONE** — Prove Snapserver exposes and carries the real audio stream.
4. **DONE** — Prove the first ESP32-S3 can receive that stream without a DAC.
5. **NEXT renderer milestone** — Add an I2S DAC to the ESP32 renderer and produce real audio.
6. Add the custom `house-audio-server` control/discovery service around the working stack, implementing the agreed session rules.
7. Integrate Android and Windows HOUSE-mode control and the accepted browser controller.
8. Add additional synchronized renderers and perform the audible room-to-room synchronization test.

Every successful step remains part of the final installation.

## Remaining server-side Phase 1 check

The working MPD -> FIFO -> Snapserver -> ESP32 network path is proven live. A reboot/service-startup test is still needed before the server foundation issue is considered completely closed.

The latest user-reported inspection showed `mpd` disabled for automatic service startup, `snapserver` enabled, and `state_file "/var/lib/mpd/state"` configured. These are observations, not changes made by this documentation update. The approved idle/no-node policy must be respected when implementing startup recovery; do not turn service startup into unconditional playback.

## Non-goals

- multiple independent songs playing on different house nodes
- metadata-first library management
- requiring a phone to keep playback alive while passive nodes are listening
- requiring every ESP32 node to mount SMB or build its own queue
- making the Raspberry Pi access its own music through SMB
- throwaway server software used only for the proof
- resetting the default shuffle to its beginning on each session

## Related projects

- [smb-music-player](https://github.com/oolah10293/smb-music-player) — Android folder-first player/controller
- [smb-player-pc](https://github.com/oolah10293/smb-player-pc) — Windows folder-first player/controller
- [house-audio-esp32](https://github.com/oolah10293/house-audio-esp32) — ESP32-S3 synchronized renderer nodes

## Status

**MPD -> Snapserver -> ESP32-S3 network reception is proven on the permanent hardware and permanent Pi stack.** The next renderer step is an I2S line-level DAC and actual audio output. Service recovery after reboot remains unverified. Session lifecycle, controller/output behavior, phone handoff, and persistent default shuffle are now recorded in [docs/SESSION_BEHAVIOR.md](docs/SESSION_BEHAVIOR.md) as approved requirements; the custom control service is not yet implemented.
