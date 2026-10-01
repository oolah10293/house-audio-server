# House Audio Server

Central playback, control, and synchronized-audio service for the whole-house music system.

Current source/deployed version: **v0.8.2**. Server behavior, deployment status, and field-proven server milestones are documented here. Android release/acceptance details are intentionally kept in [smb-music-player](https://github.com/oolah10293/smb-music-player), especially its `HOUSE_VALIDATION.md`; ESP32 renderer details are kept in [house-audio-esp32](https://github.com/oolah10293/house-audio-esp32).

## Agreed playback and session behavior

**Read [docs/SESSION_BEHAVIOR.md](docs/SESSION_BEHAVIOR.md) before changing the control service or integrating any client.** It records the agreed rules for the Pi, Android app, PC player, browser controller, and passive nodes. Some passive-renderer rules are implemented and field-proven. Runtime default-folder selection is field-proven in v0.7.0. Controller-aware rules are implemented in v0.8.0 and deployed; the zero-controller/passive-radio baseline is validated, while physical muted-controller transitions remain pending. v0.8.1 restart-to-fresh-idle behavior is now deployed and field-proven for an S3 already present at restart.

The important session distinctions are:

- With nobody connected, the Pi is idle, except while finishing the last playing track after everyone disconnects.
- Only a **passive node** automatically starts a new default session from fresh idle: the configured `MP3s` or `Rap` folder, a new random shuffle, and Repeat All. A controller merely connecting waits for Play. A returning phone that is already playing SMB instead transfers that live session into idle HOUSE; a later S3 joins it rather than starting a separate default. See [SESSION_BEHAVIOR §8](docs/SESSION_BEHAVIOR.md).
- A controller joining existing playback adopts the current song and queue. Changes it deliberately makes remain in effect after that controller leaves while other nodes remain.
- All nodes disconnecting during playback means finish the current track, then stop despite Repeat All. A node returning before track end cancels the pending stop and preserves the session.
- Only a muted phone remaining means **pause and retain the playlist, track, and exact position**, not finish and discard the session. An audible node returning or that phone unmuting resumes the retained session.
- Every completed drain ends the session. The next passive power-on loads a newly shuffled queue from the currently configured default, even if MPD retained yesterday's CD/Rap queue in `pause @ 0.0`. Do not persist/continue the completed shuffle order or force a different first song; chance repeats are allowed. Only the default folder setting is intended to persist.
- HOUSE/standalone authority is separate from local output mute. Android HOUSE rendering requires Bluetooth audio: no Bluetooth means a muted phone, including after Play/Resume and queue changes. Route disconnect mutes the phone; the existing server policy pauses/retains only when no audible output remains. Route connection joins existing playback and may resume a server-owned automatic pause, but does not start fresh idle or override deliberate Pause/Stop. Full output, Quit, and home/away rules are in [docs/SESSION_BEHAVIOR.md](docs/SESSION_BEHAVIOR.md); Android-local SMB Bluetooth lifecycle is defined in its linked Android contract.

**2026-09-30 requirement clarifications:** phone Bluetooth eligibility supersedes transport-driven auto-unmute, and active SMB return-home playback must become the HOUSE session when HOUSE is idle. Coordinate that transfer with passive auto-start so a later S3 never creates a competing default queue. Existing pause/retention and final-node rules remain in force. These are requirements updates, not a new server release; transfer implementation and Android acceptance remain pending. Regression evidence and checks live in [HOUSE_VALIDATION.md](https://github.com/oolah10293/smb-music-player/blob/main/docs/HOUSE_VALIDATION.md). Dated release sections below describe their historical implementations.

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
- **house-audio-server** — implemented thin control/session layer around the permanent stack. It exposes HOUSE-mode state/control, renderer presence, passive-radio session policy, and diagnostics without reimplementing decoding or synchronization. Home presence does not require a separate discovery protocol; clients can identify the LAN by a bound MPD probe.
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

Snapserver 0.31.0 was proven on Debian 13 (trixie), aarch64, consuming `/tmp/snapfifo` as the `default` stream. Logs confirmed:

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

Server-side TCP counters showed sustained payload transfer to the XIAO. In one 59-second sample, `bytes_sent` increased by **6,292,952 bytes** and `data_segs_out` increased by **5,343**, approximately **0.85 Mbit/s** of sustained stream traffic, with acknowledgements tracking transmission. `bytes_sent` alone is not reception proof; follow `bytes_acked` on the current client connection as well.

The renderer has since advanced beyond the network-only proof: PCM5102A-based nodes produce **real audible analog playback** through the permanent FLAC Snapcast path. The proven XIAO mapping is `LCK -> D3 (GPIO4)`, `BCK -> D4 (GPIO5)`, and `DIN -> D5 (GPIO6)`.

**Two independent XIAO ESP32-S3 + PCM5102A renderers have now been proven audibly synchronized.** They were feeding very different downstream analog systems/speakers, yet played as one coherent source with no obvious echo or phasing during the acceptance test.

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

Android and Windows clients should select behavior automatically. Android v0.4.0 implements initial app-launch selection and HOUSE control/audio; live home/away handoff remains later work. Windows integration remains planned.

**HOUSE** means the client verifies the Pi directly through the physical home LAN. Wi-Fi and Ethernet both count. Temporary loss while at home stays HOUSE/reconnecting rather than starting a competing independent queue.

**STANDALONE** is the away-from-home independent playback mode. A failed discovery request alone must not be treated as proof of departure. The phone's agreed same-song departure handoff and mute behavior are documented in [Session behavior](docs/SESSION_BEHAVIOR.md#8-automatic-homeaway-selection-and-phone-handoff).

Home detection remains intentionally minimal, but the first Android field pass changed how the client uses the physical network:

1. Client identifies a non-VPN Wi-Fi/Ethernet network/interface.
2. Its directly connected routes must include the locally configured house LAN address. That physical-route fact is the home-presence evidence.
3. Through normal platform routing, the client verifies the expected Pi identity at that LAN address (for example MPD's `OK MPD ` greeting and/or the expected control-service identity).
4. Only physical-route qualification plus expected Pi identity means HOUSE.
5. The client watches that qualifying physical network; losing it is the departure signal, subject to the later grace policy.

Normal HOUSE MPD/HTTP/Snapcast traffic should use normal platform routing rather than being forcibly pinned to the physical Android `Network`. This is required because the delivered v0.4.0 app stopped updating when Tailscale was enabled even though the Pi remained reachable from the same phone through normal routing.

This logic is only for home presence and service identity. It is **not** the controller API: normal queue/state/transport/presence behavior still goes through `house-audio-server`.

Do not add mDNS/DNS-SD, SSID matching, GPS, a separate discovery daemon, or a custom handshake unless later testing demonstrates a real need. **Tailscale/VPN reachability alone must never trigger HOUSE mode.** A phone or laptop away from home remains STANDALONE if the configured Pi address is not on a directly connected physical-LAN route, even if the Pi is VPN-reachable. The configured LAN address belongs in client-local configuration rather than being hard-coded into application source.

MPD must listen on both localhost and the configured home-LAN listener so clients can verify its identity. Deployment-specific private addresses remain local configuration and must not be committed.

Network-transition grace periods and ambiguous cases still need implementation definition.

## Synchronized playback

The system uses **Snapcast/Snapserver** timestamped/buffered distribution so renderers compensate for network jitter and clock drift instead of independently opening the same file and trying to stay aligned.

When an output powers up during an existing song, it joins the song at the **current house timestamp** after it connects and fills its synchronization buffer. It does not restart the track. This behavior is now proven with two real ESP32/PCM5102A renderers playing audibly in sync.

## Build strategy: proof becomes production

Do not create a temporary proof server that is later abandoned. Build the permanent Pi stack incrementally:

1. **DONE** — Configure MPD to use `/mnt/sharedrive/John/Shared Music` directly.
2. **DONE** — Feed MPD audio into Snapserver through `/tmp/snapfifo`.
3. **DONE** — Prove Snapserver exposes and carries the real audio stream.
4. **DONE** — Prove the first ESP32-S3 can receive that stream without a DAC.
5. **DONE** — Add the PCM5102A I2S DAC and prove real audible playback from the permanent FLAC Snapcast stream.
6. **DONE** — Add and runtime-validate the basic `house-audio-server` MPD browse/state/queue/transport API on the permanent Pi.
7. **DONE** — Track Snapserver renderer presence, including reliable hard-power-off detection via `lastSeen` freshness rather than Snapserver's raw connected flag.
8. **DONE for passive-radio basics** — renderer-driven session behavior is working: fresh-idle radio power-on starts default `MP3s` Random/Repeat playback; joining active playback preserves the queue; v0.5.1 resumes an ordinary paused session; v0.6.1 discovered MPD's `pause @ 0.0` boundary artifact. Its old-queue resume behavior is superseded by v0.6.2 fresh-idle handling. v0.6.2 is installed, with initial short/long power-cycle results recorded below.
9. **IN PROGRESS HOUSE server contract** — persisted runtime default-folder selection is field-proven. Controller-presence/output-state policy is deployed; initial Pi baseline validation is good, while physical muted-controller pause/resume testing remains pending. v0.8.1 fresh-idle restart behavior is field-proven for an already-present passive S3. Cross-session shuffle persistence is no longer desired.
10. Integrate Android and Windows HOUSE-mode control and the accepted browser controller.
11. **DONE** — two independent ESP32/PCM5102A renderers have passed the real audible synchronization test.
12. **IN PROGRESS reliability work** — diagnose occasional few-second single-node audio dropouts using the v0.6.0 unattended diagnostics recorder.

Every successful step remains part of the final installation.

## Control service — basic MPD API implemented

The `house-audio-server` runtime is now a usable basic MPD control layer. It remains deliberately small and uses only the Python standard library.

Implemented read endpoints:

- `GET /health` — identifies `house-audio-server` and reports whether local MPD is reachable. The HTTP service itself can remain healthy while MPD is intentionally stopped; in that case the payload reports `status: degraded`.
- `GET /state` — reads MPD `status` and `currentsong` and returns the current transport state, position/duration, queue length/version, Shuffle/Repeat flags, and current-song identity/metadata.
- `GET /queue` — returns the complete active MPD queue in order.
- `GET /browse?path=...` — browses MPD's indexed folder-first library using relative paths.

Implemented write endpoints:

- `POST /play`, `/pause`, `/stop`, `/next`, `/previous`
- `POST /seek`
- `POST /shuffle`, `/repeat`
- `POST /queue/clear`
- `POST /queue/replace` — accepts an ordered list of relative library paths, start index, optional start position, and play flag.
- `POST /queue/reorder` — v0.8.2: guarded ID/revision-based reordering that preserves the current track, position, transport, and policy state.

The API does not expose an arbitrary MPD-command passthrough. Queue/library paths are validated as relative paths before being sent to MPD. See [docs/API.md](docs/API.md) for the current contract.

Default runtime configuration:

```text
house-audio-server HTTP: 0.0.0.0:8787
MPD dependency:           127.0.0.1:6600
```

All values are overridable through `/etc/default/house-audio-server`. Deployment-specific/private addresses stay out of Git.

The included systemd unit runs the service as the unprivileged `houseaudio` user and restarts it after a failure. `install.sh` installs and enables **only** this control-service skeleton; it deliberately does not start MPD or Snapserver.

Typical Pi install/update from a repository checkout:

```bash
sudo sh install.sh
curl http://127.0.0.1:8787/health
```

The original read-only skeleton has already been runtime-proven on the permanent Pi. The v0.2.0 service has now also been installed and restarted successfully on that Pi. `GET /queue` is runtime-proven against the real retained MPD queue; it returned all five queue entries in order, including position/id, relative file path, modified time, duration, and available metadata. The retained test queue happened to contain five duplicate copies of the same track, which the endpoint reported correctly rather than collapsing or rewriting them.

The installer was also corrected so an update restarts an already-running `house-audio-server` process instead of merely enabling the existing service. Snapserver presence and autonomous session-policy logic remain separate work.

A GitHub Actions workflow compiles the service and runs the standard-library unit tests on pushes and pull requests.

### Runtime validation on the permanent Pi

The skeleton has now been installed and exercised on the permanent Raspberry Pi.

Confirmed:

- systemd successfully starts `house-audio-server`;
- with MPD intentionally stopped, `GET /health` remains available and reports `status: degraded` with MPD unreachable rather than crashing;
- after starting MPD, `GET /health` changes to `status: ok` and reports the MPD protocol greeting/version;
- `GET /state` successfully reads real MPD state, including stopped/playing state, queue length/version, current queue position/id, Shuffle/Repeat flags, current file identity, available metadata, and duration;
- the observed stopped MPD session retained a five-item queue and selected item, demonstrating that the service reads the real existing MPD session rather than constructing its own queue;
- `GET /queue` is now runtime-proven and returned the exact five retained MPD entries in order, including duplicate entries, queue position/id, relative file identity, modified time, duration, and available metadata;
- LAN access to the HTTP service was confirmed from another PC on the home network;
- updating the installed service exposed that `systemctl enable --now` does not restart an already-running process; `install.sh` now explicitly restarts the service after copying new code.

The complete v0.2.0 basic MPD API is now runtime-proven on the permanent Pi:

- `GET /browse` successfully returned the real folder-first MPD library, including the large `Rap` folder and relative file identities;
- `POST /queue/replace` replaced the old duplicate test queue with three distinct ordered tracks, preserved the requested order, and started at an arbitrary requested index;
- `POST /pause` retained the current track and exact playback position;
- `POST /seek` moved the paused track to an exact requested elapsed position without forcing playback;
- `POST /play` resumed from that position;
- `POST /next` and `POST /previous` moved to the correct adjacent queue entries;
- `POST /shuffle` mapped correctly to MPD Random;
- `POST /repeat` mapped correctly to MPD Repeat;
- `POST /stop` stopped transport without clearing the queue or changing the selected item;
- `GET /state` accurately reflected the resulting transport, queue position/id, elapsed time, Random/Repeat state, and current file;
- transport-only operations left the MPD queue version unchanged, confirming they did not unnecessarily rebuild the queue.

The basic MPD control layer is therefore considered **runtime-validated**.

### Snapserver renderer presence — v0.3.0

v0.3.0 adds a persistent raw-TCP JSON-RPC connection to Snapserver's control port (default 1705). The service takes an initial `Server.GetStatus` snapshot, listens for client/group/stream notifications, and refreshes the full status after relevant events.

New `GET /renderers` output includes connected/audible counts plus per-client Snapcast id, name, network identity, mute/volume/latency, group, stream, and client-version information. `GET /health` now reports both MPD and Snapserver reachability, and normal state responses include a compact renderer summary.

The presence layer was partially runtime-validated on the permanent Pi: powering the ESP32 on correctly changed the known client from disconnected to connected/audible without restarting either service.

### Hard-power renderer detection

Testing also exposed an important real-world behavior: when the ESP32 is hard-powered off while the Snapcast stream is idle, the Pi can retain the TCP 1704 socket in `ESTABLISHED` state for a while and Snapserver can continue reporting the client as `connected: true`. That raw flag is therefore not sufficient for the radio power-switch use case.

v0.3.1 fixes the presence definition without changing Snapserver: the service polls `Server.GetStatus`, reads each client's Snapcast `lastSeen` timestamp, and exposes `present`/`presentCount` separately from Snapserver's raw `connected` flag. A client defaults to stale after five seconds without fresh Snapcast activity. `audibleCount` now depends on fresh presence as well as mute state.

Autonomous session policy must use `presentCount`, not raw `connectedCount`.

This behavior is now **runtime-proven on the permanent Pi in both directions**:

- with the ESP32 powered on and allowed to settle, the service reported `present: true`, `presentCount: 1`, `audibleCount: 1`, with a fresh `lastSeenAgeSeconds`;
- after hard power-off and a short wait, Snapserver and Linux could still retain the raw stream connection as `connected: true` / TCP `ESTABLISHED`, but the service correctly changed to `present: false`, `presentCount: 0`, `audibleCount: 0` using stale `lastSeen` age;
- powering the renderer back on returned the same remembered client to fresh/present/audible state without restarting Snapserver or `house-audio-server`.

Renderer presence is therefore considered **runtime-validated**, including the actual hard power-switch behavior required by the vintage-radio installations. The next server step is to feed `presentCount` transitions into the agreed autonomous MPD session policy.

### Autonomous final-track policy — v0.4.0

The first renderer-driven session rule is now implemented in source.

When the final effective passive renderer disappears while MPD is actively playing, `house-audio-server` arms a finish-current-track stop. Because MPD Single + Repeat would repeat the same song, the service temporarily disables Repeat, uses MPD 0.24's `single oneshot` boundary stop, and remembers the prior Repeat/Single settings. If a renderer returns before the song ends, those settings are restored immediately and the pending stop is cancelled. If the song reaches its end with nobody back, MPD stops at the boundary and the original options are restored while stopped.

The policy deliberately ignores Snapserver outages rather than converting them into false departures, and its state is exposed at `GET /session`.

This describes the historical v0.4.0 slice. Fresh-idle startup was added later; v0.6.2 now makes completed drains fresh idle and creates a new default shuffle each fresh session. Saved cross-session shuffle progress is no longer a planned feature.

### Passive radio power-on auto-start — v0.5.0

The service now implements the basic appliance behavior for passive radios: **turn a radio on and music starts**.

If a passive renderer is present while the house is fresh-idle, or renderer presence changes from zero to positive while MPD is stopped, the service loads the configured default folder (`MP3s` by default), enables Repeat and Random, disables Single/Consume, and starts playback. If music is already playing, the arriving radio simply joins that session without replacing the queue.

v0.5.1 now implements the approved pause override: if MPD already has a paused session, powering on a passive radio resumes that existing session from its current position. It does not replace the paused queue with default `MP3s`. Controller arrival alone still must not bulldoze through Pause.

This also works when `house-audio-server` starts while a radio is already powered on; the initial presence baseline is treated as a real passive-node arrival when MPD is stopped.

The default folder is configurable with `PASSIVE_DEFAULT_FOLDER`. v0.5.0 requested a fresh MPD Random order at each default start. v0.6.2 explicitly shuffles the actual queue using OS entropy for every fresh session; exact order/progress is not carried across completed sessions.

### Passive radio auto-start runtime validation

v0.5.0 is now **runtime-proven on the permanent Pi and real ESP32 radio hardware**.

Observed:

- with MPD stopped and the radio powered on, the server automatically loaded the default `MP3s` session and started playback with no phone or manual MPD command;
- the first observed random selections included Cake — *War Pigs* and Weezer — *Island in the Sun*;
- after hard-powering the radio off for more than ten seconds and powering it back on while the house session was still active, the renderer rejoined the **same song** rather than rebuilding/restarting the queue;
- audible playback returned about **six seconds after power-on**, which is the current observed end-to-end boot/connect/buffer time for this hardware.

That proves the core appliance behavior: **turn the radio on and music comes out; power-cycle it during an active session and it rejoins the existing house playback.**

### Two-renderer synchronization proof

The second physical XIAO ESP32-S3 + PCM5102A node was built as a clone of the first working renderer and joined the same Snapserver stream.

Runtime result:

- two independent ESP32 renderers connected at the same time;
- both produced real analog audio through different downstream amplifier/speaker systems;
- the two outputs were **audibly synchronized**, with no objectionable echo or phasing during the acceptance test;
- hard power-cycling a node for more than ten seconds and powering it back on rejoined the still-active house session on the same song;
- one measured power-on/rejoin took about **six seconds** from plug-in to audible output.

This closes the original Phase 3 question: the architecture is not merely capable of multiple clients; it produces real synchronized audio across independent hardware.

### Leave-and-return pause edge — runtime proven fix

A longer real-world test exposed a policy mismatch rather than a renderer fault. Both nodes were unplugged, the user left the house, and both were powered again later. Snapserver saw both renderers as healthy/present, but MPD was paused at 0.0 seconds, so v0.5.0 intentionally left the session silent.

v0.5.1 changed passive-radio arrival to **resume the existing paused queue/position** instead of remaining silent or replacing the queue with default `MP3s`. Installing/restarting v0.5.1 with both radios already present caused both outputs to start immediately, providing direct runtime proof of the fix.

The product rule is now explicit: **powering on a passive radio is a Play intent signal**. Controller attachment alone still does not override Pause.

### Current intermittent-dropout investigation

With two nodes playing synchronously, occasional short silences of a few seconds have been heard on one renderer or the other. They are not necessarily frequent and have not been reported as simultaneous on both nodes.

Known facts:

- both XIAO S3 nodes have their external 2.4 GHz antennas installed;
- the system otherwise stays synchronized and recovers automatically;
- the cause is not yet established, so this is **not** being labeled a Wi-Fi problem, Snapserver problem, or decoder problem prematurely.

v0.6.0 adds bounded in-memory diagnostics at `GET /diagnostics` to record per-client Snapcast time-sync stalls/recovery, connected/present/audible changes, Snapserver reachability, and stream-state changes. The next useful evidence is a field capture after an audible dropout. If server-side timing/presence remains clean, instrumentation should move into the ESP32 decoder/buffer/I2S path.

Renderer-side tracking: [house-audio-esp32 Issue #3](https://github.com/oolah10293/house-audio-esp32/issues/3).

## MPD control-service boundary

The remaining controller problem is **not figuring out how to operate MPD**. The required MPD operations are understood: folder/library browsing, queue inspection/replacement/reordering, current-song and position state, Play/Pause/Stop, Seek, Previous/Next, Shuffle/Random, Repeat, and change notifications.

The custom `house-audio-server` should be the single client-facing bridge to those MPD operations. Android, Windows, and the browser controller should not each connect directly to MPD's native control port. The bridge exists so one place can enforce:

- the approved one-house-session lifecycle;
- controller vs renderer presence;
- muted-output behavior;
- fresh-idle vs retained-session rules;
- finish-current-track behavior when all nodes leave;
- new default-folder shuffle on each fresh session; persisted default-folder choice (server API implemented in v0.7.0; Android v0.4.0 selector awaiting phone acceptance);
- HOUSE session/control protocol/version identity; home presence itself is established by the direct MPD LAN probe;
- command acknowledgement, stale-state protection, and shared state updates.

The exact network API/schema is still to be implemented. The architecture is settled enough to proceed with the bridge without further MPD research.

## Remaining server-side Phase 1 check

The MPD -> FIFO -> Snapserver -> ESP32 network path has been proven. A reboot/service-startup test is still needed before the server foundation issue is considered completely closed.

### Historical parked state

Earlier in the project, after the first network proof, the Pi test stack was deliberately shut down and cleaned up. That state is retained here only as history; it is **not the current test state**. At that earlier point:

```text
mpd:        inactive / disabled
snapserver: inactive / disabled
mpd process:        none
snapserver process: none
MPD/Snapcast listening ports: none
/tmp/snapfifo: gone
```

Since then the stack has been brought back up repeatedly for permanent-Pi runtime testing, including autonomous radio startup, two-renderer synchronization, and diagnostics work. Reboot/service-startup suitability still needs its own unattended test.

The MPD state file remains configured at `/var/lib/mpd/state`. The approved idle/no-node policy must be respected when startup recovery is implemented; starting the service stack must not automatically mean starting music.

### Operational lifecycle requirement

Before this becomes an always-available appliance, add a clean **House Audio On / House Audio Off** lifecycle. One supported operation should start the required house-audio services in the correct order and verify health; another should stop playback/services cleanly and remove only transient house-audio runtime state where appropriate. It must leave Samba and unrelated Pi services alone, preserve configuration/library state, and require no package uninstall/reinstall cycle.

This lifecycle is a recorded requirement only; it is **not implemented yet**.

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

**The permanent end-to-end house-audio path is now proven through two simultaneously audible, synchronized ESP32-S3 + PCM5102A renderers.** The basic MPD control API, renderer presence (including abrupt hard-power loss), fresh-idle passive-radio auto-start, active-session rejoin, and passive-radio resume-through-Pause behavior are all runtime-proven. The observed radio power-on/rejoin time is about six seconds on the current hardware. Remaining major server work is physical validation of controller/output policy, unattended reboot/startup validation, and reliability diagnosis for occasional few-second single-node dropouts. Persisted runtime default-folder selection is field-proven in v0.7.0. Controller/output presence is deployed in v0.8.0 with baseline checks passing; physical controller transitions remain pending. v0.6.0 provides the first unattended diagnostics capture for that investigation.

### Leave-and-return pause edge — v0.5.1

A real leave-the-house test found both renderers reconnecting correctly (`presentCount: 2`) while MPD was paused at 0.0 seconds on the queued track. Snapserver was healthy but idle, so the failure was not renderer connectivity: it was exactly the v0.5.0 policy branch that left paused sessions untouched.

v0.5.1 changes passive-radio arrival to resume the existing paused session. This preserves the queue and avoids a fresh default `MP3s` rebuild while restoring the intended appliance behavior.

### Automatic dropout diagnostics — v0.6.0

With two synchronized renderers running, occasional few-second silences were reported on one node or the other. v0.6.0 adds unattended diagnostics so reproducing the problem does not require watching multiple terminals.

The service now keeps a bounded in-memory event history of Snapserver reachability, stream-state transitions, each client's connected/present/audible transitions, Snapcast `lastSeen` stalls/recovery, stall counts, and worst observed last-seen age.

After a dropout, inspect:

```bash
curl -s http://127.0.0.1:8787/diagnostics
```

If one renderer shows a time-sync/presence anomaly while the other stays clean, investigate that renderer's Wi-Fi/client path. If the Snapserver stream changes state, investigate upstream. If neither happens during the audible dropout, instrument the ESP32 decoder/buffer/I2S path next.

### MPD oneshot boundary edge — v0.6.1

The v0.6 diagnostics immediately helped expose another real session-policy edge.

A passive renderer was plugged in but produced no audio. The renderer itself was healthy and present, while MPD showed `pause` at 0.0 seconds on the next queued track and Snapserver was idle. The policy action showed that a pending finish-current-track stop had just been cancelled by the renderer return.

Root cause: the policy expected MPD `single oneshot` to complete as transport `stop`. On the permanent MPD 0.24 stack, the real observed behavior can instead be a **pause at the next-track boundary**.

v0.6.1 restored output by resuming the retained queue after this boundary. That historical behavior is superseded: a completed drain ends the session, and v0.6.2 starts the configured passive default with fresh randomness on the next radio arrival.

### v0.6.1 runtime proof — final-track boundary return

The MPD `single oneshot` boundary fix is now **field-proven on the permanent Pi and real ESP32 renderer hardware**.

Observed failure before the fix:

- a passive S3 was powered on but no music came out;
- Snapserver showed the renderer healthy, fresh, present, and nominally audible;
- MPD was actually `pause` at **0.0 seconds on the next queued track**;
- Snapserver's `default` stream was `idle`;
- the session policy still showed `pending_stop_cancelled_renderer_returned`.

This proved that on the real MPD 0.24 stack, `single oneshot` may complete the departing session by landing in **Pause at the next-track boundary**, not only by reporting transport `stop`.

v0.6.1 recognized that boundary and resumed the old queue. The observation remains valid, but that queue-resume choice conflicts with the now-authoritative §4 and has been replaced in v0.6.2.

Runtime result after installing v0.6.1 with the S3 still powered: **music resumed automatically.** No ESP32 firmware or wiring change was required.

This proved the MPD boundary artifact and restored sound in v0.6.1. It did not validate the corrected fresh-session queue behavior; v0.6.2 has its own initial radio-test results recorded below.

### Runtime-selectable passive default folder — v0.7.0

`GET /settings` reads the passive default; `POST /settings` saves an explicit `passiveDefaultFolder` of `MP3s` or `Rap` on the Pi. The saved choice survives service restarts and upgrades. `PASSIVE_DEFAULT_FOLDER` supplies the fallback only until a choice is saved. The API can read/set the value even when MPD/Snapserver are unavailable.

The Android HOUSE Browser will reuse the button position that is SMB in STANDALONE as this selector. That button is still upcoming Android work. Changing the server default does not alter the current queue, song, position, transport, or pending drain; it affects only a subsequent genuinely fresh passive-radio auto-start session. A return before track end and an ordinary paused session keep their original queue.

Storage uses atomic replacement in the existing systemd state directory, `/var/lib/house-audio-server/settings.json`. The installer preserves saved settings and existing environment configuration. See [docs/API.md](docs/API.md) for request/response, validation, and storage failure behavior.

**Validation:** 42 tests passed for v0.7.0, including HTTP requests without MPD, both folder values, persistence across a recreated service settings object, concurrent sets, storage errors, unchanged active/paused sessions, and selection after completed drain. Subsequent Pi installation and default-selection field results are recorded below.

After installing v0.7.0 with the existing `sudo sh install.sh`, these commands read the setting and select Rap without changing playback:

```bash
curl -fsS http://127.0.0.1:8787/settings
curl -fsS -H 'Content-Type: application/json' -d '{"passiveDefaultFolder":"Rap"}' http://127.0.0.1:8787/settings
```

Use `MP3s` in the same request to select it again. The field check is to save either choice during playback, confirm the current song/queue continues, and confirm a fresh passive session uses that folder. The saved choice should also be readable after a control-service restart. Restart now ends every prior session under SESSION_BEHAVIOR §13; v0.8.1 implements that boundary without reconstructing a drain.

## v0.6.2 — completed drain ends the session

- Return before the final song ends: restore the temporary options and preserve the playing song, position, and queue.
- Completed drain, whether MPD reports stopped or paused on the next old-queue song: explicitly stop, restore options, and leave fresh idle. A later return uses the configured passive default; a return first observed in the completion poll does the same.
- Fresh default startup queries that folder's indexed files and shuffles their queue order with Python `SystemRandom` (OS entropy), then enables MPD Random/Repeat and starts shuffled item zero. No fixed seed, saved rotation, previous-first-song exclusion, or reroll is used.
- Ordinary unfinished paused sessions still resume. Pending drains remain distinguishable through Snapserver monitor outages; failed completion writes are retried.
- In v0.6.2 the environment `PASSIVE_DEFAULT_FOLDER` was the configuration source. v0.7.0 adds the persisted runtime MP3s/Rap setting described above.

Local regression tests cover early/late returns, pause/stop boundaries, completion-and-arrival in one poll, both default folders, processed-drain service restart, monitor outages, write failure, fresh shuffles, and permitted chance repeats. **v0.6.2 is now deployed; initial Pi/radio results are recorded below.**

Remaining targeted field check: select a CD/Rap queue, switch the last radio off, let the track end, and verify `/state` is stopped and `/session` has no pending stop. Power a radio on and verify the configured default starts. Repeat with return before track end (same session), ordinary Pause (resume), and several completed sessions (fresh randomness; occasional repeat first songs are valid).

## v0.6.2 initial Pi/radio results — 2026-09-29

The service is installed and running on the permanent Pi. The supplied `/session` response identifies version **0.6.2** and the current passive default **MP3s**.

User-reported audible tests:

- After about **10 seconds unplugged**, the radio returned to the **same song**.
- After about **five minutes unplugged**, powering the radio back on started a **different new song**.

The supplied snapshot reports:

```json
{
  "version": "0.6.2",
  "sessionPolicy": {
    "mode": "passive_renderers_only",
    "defaultFolder": "MP3s",
    "presentCount": 1,
    "pendingFinalStop": false,
    "pendingSongId": null,
    "lastAction": "pending_stop_cancelled_renderer_returned",
    "freshIdleAutoStartImplemented": true,
    "freshSessionShuffleImplemented": true,
    "defaultShufflePolicy": "new_each_fresh_session",
    "controllerPresenceImplemented": false
  }
}
```

This directly confirms the deployed version and the pre-completion return/cancellation path. The longer-off audible result is consistent with a new session, but no `started_default_session` snapshot or queue comparison was supplied for that trial. In particular, replacing a manually selected CD/Rap queue with the configured default after completed drain remains a separate field check. The policy and shuffle regression tests remain passing (27 local tests; GitHub CI passed).

At the time of these v0.6.2 tests, MP3s was the deployment setting and no runtime selector existed. v0.7.0 now provides the field-proven server settings API; the Android button remains pending. Controller/output presence is deployed in v0.8.0 with baseline checks passing; physical controller transitions remain pending. These results do not represent an Android HOUSE build or an ESP32 firmware release.

### v0.7.0 permanent-Pi field validation — PASS

v0.7.0 is now installed on the permanent Raspberry Pi and the persisted passive-default behavior has been exercised with a real S3 renderer.

Confirmed:

- `GET /settings` reported server version `0.7.0`, current default `MP3s`, and allowed values `MP3s` / `Rap`;
- `POST /settings` successfully changed the persisted default from `MP3s` to `Rap`;
- the song already playing **did not change** when the default was changed, proving the setting is independent of the active queue/session;
- after the S3 was unplugged for roughly ten minutes—long enough for the old session to drain completely—powering it back on started a **fresh Rap session**;
- the first observed track of that fresh Rap session was Ludacris — *Southern Hospitality*.

This proves the server behavior required by the future Android HOUSE `MP3s` / `Rap` button: the phone can change the Pi-owned future passive default without disturbing current listeners, and the saved choice is consumed on the next genuinely fresh passive-S3 session.

That next server milestone is implemented in v0.8.0 below; physical controller/output validation remains pending.

## v0.8.0 — controllers and muted-phone sessions

The Pi now has a shared controller-presence API for Android, Windows, and browser clients:

- `GET /controllers`; `POST /controllers/attach`, `/controllers/heartbeat`, and `/controllers/detach`.
- Five-second heartbeats and fifteen-second expiry; background/screen-off apps remain present while renewing. Quit detaches immediately.
- Controller and renderer roles belong to one device. Durable renderer ownership prevents a known phone's Snapcast socket from becoming a passive radio after expiry, Quit, or service restart.
- Muted/unavailable-output controllers hold a paused queue/song/position. Audible return resumes that automatic pause; explicit Pause/Stop remain respected.
- The last controller leaving an automatic pause ends the session without advancing. All nodes leaving during playback still finish the current song, and pre-boundary return preserves that session.
- Fresh-idle controller attachment never starts or reshuffles music. Only passive radios start the configured default after completed drain.
- Lease tokens and increasing sequence numbers reject stale lifecycle/output reports. Local mute choice and actual output readiness are reported separately.

The two previously open choices—background presence and ending the last muted controller's auto-paused session—were confirmed on 2026-09-29 and recorded in the canonical session document. The existing Android UI and planned Browser polish remain unchanged in scope; this release supplies their server dependency.

**Validation:** 73 tests passed for v0.8.0, including the prior passive/shuffle/settings suite, HTTP lifecycle requests, persistence of renderer ownership, expiry/stale reports, drain boundaries, manual transport overrides, output failure/recovery, and MPD-write retry. v0.8.0 is installed on the Pi with health/passive-S3 baseline checks passing, as recorded below. Physical controller transitions remain pending; there is no Android HOUSE APK or ESP32 firmware change in this milestone.

Use the existing installer. The current systemd state directory also stores `controllers.json`; no MPD/Snapserver configuration change is needed. See [docs/API.md](docs/API.md) for the complete client contract. The main field check is a controller holding a muted lease while the last radio leaves (pause), an audible radio returning (resume), and the last muted controller detaching/expiring (stop, then fresh default on the next radio).

Restart policy is now explicitly settled: a `house-audio-server` restart is a **hard listening-session boundary**. Do not reconstruct the prior live controller/session state. Live leases, mute/readiness reports, automatic-pause ownership, pending drains, and the old queue/session are disposable across restart. Persist only durable configuration/identity such as the selected `MP3s`/`Rap` passive default and controller-to-renderer ownership. After restart the house should normalize to fresh idle; a controller reconnecting first stays idle, while a passive S3 present/arriving starts a new shuffled configured default. v0.8.1 implements the startup fresh-idle boundary in source/tests; Pi deployment and restart validation remain pending. Authentication/pairing and transport-command deduplication remain open.

### v0.8.0 permanent-Pi deployment baseline — PASS

v0.8.0 is now installed on the permanent Raspberry Pi.

Immediately after deployment:

- `GET /health` reported service version `0.8.0`, status `ok`, MPD reachable at protocol `0.24.0`, and Snapserver `0.31.0` reachable;
- one real S3 renderer was connected, present, and audible;
- the second remembered S3 was correctly retained as known-but-not-present;
- `GET /controllers` reported zero controllers, one passive renderer, `presentCount: 1`, and `audibleCount: 1`;
- the active S3 was correctly classified as `passive: true`, proving the new controller layer did not disturb existing passive-radio classification.

This validates installation and the no-controller baseline only. The new muted-controller pause/resume/expiry behavior still needs physical/API field testing.

### Restart semantics — settled product rule

A `house-audio-server` restart is a **fresh-session boundary**, not a state-recovery exercise.

After restart:

- discard live controller leases, mute/output-ready reports, automatic-pause reason, pending drain state, and the previous listening session;
- preserve only durable configuration/identity, including the persisted `MP3s`/`Rap` passive default and controller↔renderer ownership;
- normalize MPD to fresh idle instead of trying to infer whether an old Pause was automatic or deliberate;
- a controller reconnecting first does not start music;
- a passive S3 present/arriving starts a new shuffled session from the configured default.

This supersedes the earlier idea of persisting/restoring automatic-pause ownership after restart. v0.8.1 implements the startup fresh-idle boundary and is now deployed. The already-present-radio restart case is field-proven; the all-radios-off restart variant has not yet been separately exercised.

## v0.8.1 — fresh idle on server restart

Implements the settled restart rule in SESSION_BEHAVIOR §13. On each service start, stop MPD, clear the old queue, and reset Single/Consume/Repeat/Random to off. Verify empty stopped state before allowing playback commands. Saved passive-default configuration and controller↔renderer ownership remain intact; live leases, output reports, pause reasons, and drains are not restored.

- Controller reconnect alone leaves the house idle.
- A passive radio already present or arriving later starts the configured MP3s/Rap default with a new random shuffle and Repeat All. Natural chance repeats are allowed.
- MPD startup failure or a partial reset retries. Snapserver can be offline during the reset. Normal dependency reconnects after readiness do not clear the new session.
- `GET /health` exposes `startup.ready` and remains degraded while reset is pending. `GET /session` exposes the same object under `sessionPolicy.startup`. Playback writes return 503 `startup_pending` until ready; settings and controller lifecycle endpoints stay available.
- The restart boundary also applies when automatic presence policy is disabled; passive auto-start remains disabled in that configuration.

**Validation:** 85 local tests pass, covering startup from playing/paused/stopped and drain-artifact states, saved settings/ownership, controller-first silence, passive arrival, partial failure/retry, dependency reconnects, write gating, and all existing session regressions. Return before the final song ends in the same running service still cancels the stop and preserves queue/song/position. Unit tests do not establish physical playback behavior.

**One Pi checkpoint:** install v0.8.1 through the existing update workflow. With radios off, restart from a selected queue/paused or draining state and verify `startup.ready: true`, Stop, and an empty queue; a controller attaching first stays silent. Power on a radio (and repeat with a radio already present at restart) to verify a fresh configured default. Check the saved folder and known phone-renderer ownership survive. Include the pending v0.8.0 muted-controller pause/resume/expiry checks in this checkpoint. A naturally repeated first song is valid.

**Next implementation slice:** Android HOUSE backend and synchronized phone output through the existing Browser/Now Playing screens, including the approved Browser polish and MP3s/Rap selector. Preserve STANDALONE. Home/away recovery and final device acceptance follow; the settled restart rule requires no session-recovery subsystem.

### v0.8.1 permanent-Pi restart result — PASS for radio-already-present case

v0.8.1 was installed while one passive S3 remained powered on. The service restart produced a fresh randomized Rap session rather than preserving the prior listening session.

The subsequent `GET /session` snapshot reported:

- service version `0.8.1`;
- `defaultFolder: "Rap"`;
- `presentCount: 1`, `passiveCount: 1`, `audibleCount: 1`;
- `controllerCount: 0`;
- `autoPaused: false` and no pending final stop;
- `lastAction: "started_default_session"`;
- `startup.ready: true` with no startup error.

Together with the audible fresh randomized Rap playback, this field-proves the v0.8.1 restart boundary when a passive radio is already present: the prior live session is discarded, startup reaches fresh-idle readiness, durable settings survive, and the present passive radio starts a genuinely fresh default session.

The separate restart-with-all-radios-off case has not yet been explicitly field-tested. Physical controller pause/resume/expiry behavior also remains pending.

## v0.8.2 — Android queue-sort support

Android v0.4.0 now implements the first HOUSE backend and bundled Snapcast receiver through its existing Browser/Now Playing UI, including the approved Browser polish and saved MP3s/Rap selector. It retains its standalone SMB/Media3 engine. No ESP32 firmware change is required.

This server release adds `POST /queue/reorder`: require the expected queue revision and every existing MPD ID exactly once, then move those entries in place. This allows Now Playing Sort to preserve paused/playing/stopped state, current song, elapsed position, shuffle/repeat, automatic-pause ownership, and pending drain. Stale selections fail with 409 before any move; uncertain writes must be refreshed, never blindly replayed. See [API.md](docs/API.md).

**Validation:** 90 local tests and GitHub CI pass. v0.8.2 is now installed on the permanent Pi; health/startup are good and the existing passive-S3/Rap session is working. Android v0.4.0 successfully entered HOUSE and adopted the current MPD track after MPD's LAN listener was enabled. Queue-reorder behavior and phone/S3 synchronization still need dedicated device checks.

**Next combined checkpoint:** keep the installed Pi v0.8.2, install Android v0.4.1, first repeat Tailscale-on launch/toggle and conditional mute cases, then follow [the phone/S3 checklist](https://github.com/oolah10293/smb-music-player/blob/main/docs/HOUSE_VALIDATION.md). Validate silent opening, phone/S3 synchronization, sort during pause/play, local mute, background controller presence, audible-return resume, and Quit preserving remaining listeners. Live home/away handoff follows that implementation slice; its open decisions remain unchanged.

### HOUSE Country Buffer direction

The synchronized stream is now intended to use a **multi-second playout buffer** rather than treating the current ~1 second as the final target. Keep `chunk_ms` small (currently ~20 ms); buffer depth and chunk size are separate.

The purpose is resilience to brief LAN/Wi-Fi stalls plus headroom for client-specific latency correction. Exact depth remains a field-tuning choice. Deliberate transport/queue changes should not intentionally wait for the whole stale buffer to drain; verify the real Snapcast reset/discontinuity behavior and invalidate/rebase old audio as promptly as the stack supports.

This is an approved design direction, not yet a deployed configuration change or a diagnosis of the current S3 dropout issue.

## Next major system goals

Future feature details live in their tracking issues rather than being duplicated here:

- Internet radio through MPD: [Issue #5](https://github.com/oolah10293/house-audio-server/issues/5).
- Dedicated synchronized subwoofer renderer: [house-audio-esp32 Issue #4](https://github.com/oolah10293/house-audio-esp32/issues/4).
