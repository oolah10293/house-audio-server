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

Android and Windows clients should select behavior automatically. This integration is planned, not implemented.

**HOUSE** means the client verifies the Pi directly through the physical home LAN. Wi-Fi and Ethernet both count. Temporary loss while at home stays HOUSE/reconnecting rather than starting a competing independent queue.

**STANDALONE** is the away-from-home independent playback mode. A failed discovery request alone must not be treated as proof of departure. The phone's agreed same-song departure handoff and mute behavior are documented in [Session behavior](docs/SESSION_BEHAVIOR.md#8-automatic-homeaway-selection-and-phone-handoff).

Home detection is intentionally minimal:

1. Client chooses a non-VPN Wi-Fi/Ethernet network/interface.
2. Through that specific LAN path, it opens a short TCP connection to its locally configured/reserved house LAN address on MPD port `6600`.
3. It requires MPD's normal greeting beginning `OK MPD `; an optional `ping` / `OK` is enough for an additional liveness check.
4. A valid MPD response on that bound LAN path means HOUSE.

This probe is only for home presence and service identity. It is **not** the controller API: normal queue/state/transport/presence behavior still goes through `house-audio-server`.

Do not add mDNS/DNS-SD, SSID matching, GPS, a separate discovery daemon, or a custom handshake unless later testing demonstrates a real need. **Tailscale/VPN reachability alone must never trigger HOUSE mode.** A phone or laptop away from home remains STANDALONE even if it can reach the Pi through Tailscale. The configured LAN address belongs in client-local configuration rather than being hard-coded into application source.

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
8. **DONE for passive-radio basics** — renderer-driven session behavior is working: fresh-idle radio power-on starts default `MP3s` Random/Repeat playback; joining active playback preserves the queue; v0.5.1 also resumes an existing paused session when a passive radio appears.
9. **NEXT session milestone** — implement durable saved progress/order for the default `MP3s` shuffle rotation, then controller-presence/output-state policy.
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

This is **implemented and unit-tested but not yet runtime-validated on the permanent Pi**. Fresh-idle default `MP3s` startup and persistent shuffle progress remain the next chunk.

### Passive radio power-on auto-start — v0.5.0

The service now implements the basic appliance behavior for passive radios: **turn a radio on and music starts**.

If a passive renderer is present while the house is fresh-idle, or renderer presence changes from zero to positive while MPD is stopped, the service loads the configured default folder (`MP3s` by default), enables Repeat and Random, disables Single/Consume, and starts playback. If music is already playing, the arriving radio simply joins that session without replacing the queue.

v0.5.1 now implements the approved pause override: if MPD already has a paused session, powering on a passive radio resumes that existing session from its current position. It does not replace the paused queue with default `MP3s`. Controller arrival alone still must not bulldoze through Pause.

This also works when `house-audio-server` starts while a radio is already powered on; the initial presence baseline is treated as a real passive-node arrival when MPD is stopped.

The default folder is configurable with `PASSIVE_DEFAULT_FOLDER`. Durable cross-session preservation of the exact default shuffled order/progress is still pending; v0.5.0 creates a fresh MPD Random order for each fresh default session.

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
- persistent default `MP3s` shuffle progress;
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

**The permanent end-to-end house-audio path is now proven through two simultaneously audible, synchronized ESP32-S3 + PCM5102A renderers.** The basic MPD control API, renderer presence (including abrupt hard-power loss), fresh-idle passive-radio auto-start, active-session rejoin, and passive-radio resume-through-Pause behavior are all runtime-proven. The observed radio power-on/rejoin time is about six seconds on the current hardware. Remaining major server work is durable default-`MP3s` shuffle progress, controller/output presence policy, unattended reboot/startup validation, and reliability diagnosis for occasional few-second single-node dropouts. v0.6.0 provides the first unattended diagnostics capture for that investigation.


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
