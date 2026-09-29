# HTTP control API

Current source and deployed Pi service version: **0.6.2**. Unit tests/CI passed; initial radio power-cycle results are recorded at the end of this document.

This is the first usable MPD control layer for the house-audio project. It is intentionally small and exposes only allowlisted operations.

The service listens on port `8787` by default and talks to MPD locally on `127.0.0.1:6600`.

## Read endpoints

### `GET /health`

Reports service identity and whether MPD is reachable.

### `GET /state`

Returns the current MPD transport/session snapshot:

- play / pause / stop state
- elapsed and duration
- volume
- Repeat / Random / Single / Consume
- queue length and version
- selected queue position/id
- current file identity and available metadata

### `GET /renderers`

Returns the live Snapserver renderer snapshot from the JSON-RPC control connection on port 1705.

The response includes:

- whether Snapserver is reachable;
- Snapserver version/control-protocol information when available;
- connected renderer count;
- currently audible renderer count;
- each client's stable Snapcast id, configured/display name, host/IP/MAC, client version, mute/volume/latency, group, and stream;
- current stream ids/status.

The service polls Snapserver's `Server.GetStatus` over the local JSON-RPC control socket (default TCP 1705). Polling is intentional because abrupt ESP32 power loss can leave Snapserver's raw TCP/audio connection looking established for a while; effective presence is derived from fresh Snapcast `lastSeen` activity rather than raw connection state alone.

### `GET /session`

Returns the current autonomous passive-renderer policy state, including:

- whether the policy is enabled;
- the last effective passive-renderer count seen by policy;
- whether a final-track stop is armed;
- the MPD song id being allowed to finish;
- the most recent policy action.

Session-policy output includes `freshIdleAutoStartImplemented`, `freshSessionShuffleImplemented: true`, `defaultShufflePolicy: "new_each_fresh_session"`, and `controllerPresenceImplemented`. The obsolete `persistentDefaultShuffleImplemented` field is removed: saved order/progress across completed sessions is no longer a requirement. `defaultFolder` is the currently configured passive default.

### `GET /diagnostics?limit=<n>`

Returns a small in-memory diagnostic history for renderer dropouts. The server records **events, not every sample**, so it can run unattended without producing a giant log.

Tracked automatically:

- Snapserver reachable/unreachable transitions;
- Snapserver stream state changes such as `playing -> idle`;
- per-renderer `connected`, effective `present`, and `audible` transitions;
- Snapcast time-sync stalls when `lastSeenAgeSeconds` exceeds the configured warning threshold;
- recovery from those stalls and approximate stall duration;
- per-renderer counters plus the worst observed `lastSeenAgeSeconds` since service start.

Example:

```text
/diagnostics
/diagnostics?limit=50
```

The default warning threshold is 2.5 seconds. This endpoint is diagnostic only and does not change playback.

### `GET /queue`

Returns every current MPD queue entry in order, including MPD queue position/id and available metadata.

### `GET /browse?path=<relative-path>`

Uses MPD's indexed library to list one folder.

Examples:

```text
/browse
/browse?path=Rap
/browse?path=CDs/Some%20Album
```

Returned paths are library-relative and use forward slashes.

## Write endpoints

Every successful write returns the updated MPD state unless noted otherwise.

### `POST /play`

Resume/start the selected MPD item.

### `POST /pause`

Pause MPD.

### `POST /stop`

Stop MPD.

### `POST /next`

Advance to the next MPD queue item.

### `POST /previous`

Go to the previous MPD queue item.

### `POST /seek`

JSON body:

```json
{
  "seconds": 90.5
}
```

Seeks the current MPD item to an absolute elapsed position in seconds.

### `POST /shuffle`

Maps the app's Shuffle control to MPD Random.

```json
{
  "enabled": true
}
```

### `POST /repeat`

```json
{
  "enabled": true
}
```

### `POST /queue/clear`

Clears the current MPD queue.

### `POST /queue/replace`

Atomically from the API caller's perspective, replace the current MPD queue with an ordered list of library-relative files.

```json
{
  "tracks": [
    "Rap/Track A.mp3",
    "Rap/Track B.mp3",
    "Rap/Track C.mp3"
  ],
  "startIndex": 0,
  "play": true,
  "positionSeconds": 0
}
```

Rules:

- `tracks` is required and must be a non-empty array of relative MPD-library paths.
- `startIndex` defaults to `0`.
- `play` defaults to `true`.
- `positionSeconds` defaults to `0` and is applied only when `play` is true.
- absolute paths, backslashes, and `..` traversal are rejected.
- the API never accepts arbitrary MPD protocol commands from a client.

This endpoint is the intended primitive for Android/Windows `PLAY LIST`, search-filtered queues, selected-track-first rotation, and deliberate sorted-queue replacement.

## Current boundaries

Implemented now:

- health/state
- live Snapserver renderer presence
- queue inspection
- folder/library browsing
- basic transport
- seek
- Shuffle/Repeat
- queue clear/replace

Implemented:

- fresh idle + passive renderer present/arrives -> load the configured default folder (`MP3s`), enable Random + Repeat All, and start playback;
- passive renderer joining active playback -> leave the existing queue untouched;
- passive renderer joining an ordinary paused/retained session resumes it without replacing its queue; a completed-drain boundary pause is excluded;
- final passive renderer leaves during playback -> finish the current track, then normalize MPD to stopped/fresh idle, including a pause-on-next-track boundary;
- renderer returns before track end -> cancel the pending stop and preserve the current song, position, and queue;
- renderer returns after completed drain -> new randomized queue of the configured passive default; no saved old rotation and no forced first-song difference;
- Snapserver outage -> never interpret it as all renderers leaving.

Still not implemented:

- muted-controller pause/retain behavior
- persisted runtime passive-default folder selection
- controller attach/heartbeat/detach
- local renderer mute state
- Android/Windows authentication/pairing
- push state feed

For now, controllers can poll `/state`; the Android design intentionally does not require WebSockets for the first integration.


## Runtime validation

The v0.2.0 API has been exercised against the real MPD instance on the permanent Raspberry Pi.

Confirmed in real use:

- `/health`, `/state`, `/queue`, and `/browse`;
- ordered queue replacement with a requested start index;
- Play, Pause, Stop, Previous, and Next;
- absolute seek while paused, followed by resume from the exact requested position;
- Random/Shuffle and Repeat toggles;
- Stop preserving the queue and selected item;
- state reads reflecting the actual MPD session after every command;
- transport-only operations leaving the MPD queue version unchanged.

One browse test returned the real large `Rap` folder, confirming that library-relative identities and folder-first browsing work through the HTTP layer. One queue test also confirmed that duplicate MPD queue entries are preserved and reported exactly rather than deduplicated by the service.

The next API work is no longer basic MPD transport. v0.3.0 adds live Snapserver renderer presence; after that is runtime-validated, the next step is autonomous house-session policy using that presence.


### Abrupt power-off presence rule

A hard-powered-off ESP32 can leave Snapserver's TCP stream socket temporarily in `ESTABLISHED`, so Snapserver may continue reporting the remembered client with `connected: true` for a while even though the renderer is physically gone.

v0.3.1 therefore distinguishes:

- `connected` — Snapserver's raw client flag;
- `present` — raw connected **and** a fresh Snapcast `lastSeen` timestamp;
- `presentCount` — clients considered physically present by that freshness rule;
- `audibleCount` — fresh/present clients that are not client- or group-muted.

The monitor polls `Server.GetStatus` once per second by default and treats a client as stale after five seconds without fresh Snapcast activity. Both values are configurable. Autonomous house-session policy must use `present`/`presentCount`, not the raw `connected` flag.


## Renderer presence runtime validation

The v0.3.1 presence model has been exercised on the permanent Raspberry Pi with the real XIAO ESP32-S3 renderer.

Confirmed:

- known-but-powered-off clients remain listed by Snapserver but are not treated as present;
- power-on changes the client to `present: true`, `presentCount: 1`, and `audibleCount: 1` with a fresh `lastSeen`;
- abrupt hard power-off can leave Snapserver's raw `connected: true` and the Linux TCP 1704 socket in `ESTABLISHED`;
- despite that stale raw connection, after the freshness timeout the API correctly reports `present: false`, `presentCount: 0`, and `audibleCount: 0`;
- powering the same renderer on again returns it to fresh/present state without restarting Snapserver or the control service.

Therefore all future autonomous house-session policy must use `present` / `presentCount` as renderer-presence authority. Raw `connected` remains diagnostic only.


## v0.4.0 final-track policy

This is the first autonomous house-session behavior wired to proven renderer presence.

When effective `presentCount` transitions from a positive value to zero while MPD is playing, the service arms a one-song boundary stop. MPD's normal Single mode cannot be used blindly with Repeat enabled because Single + Repeat repeats the current song. On the installed MPD 0.24.x stack, the service therefore:

1. saves the current Repeat and Single settings;
2. temporarily disables Repeat if necessary;
3. sets `single oneshot`;
4. allows the current song to finish naturally;
5. after MPD stops at the boundary, restores the saved Repeat/Single settings.

If effective renderer presence returns before the song ends, the service immediately restores the saved options and cancels the pending stop, so the current house session continues without restarting the track or replacing the queue.

A Snapserver/control outage clears the policy's presence baseline instead of manufacturing a false "all renderers left" transition.

This historical phase did not auto-start the default folder. Later versions add fresh-session startup; cross-session shuffle/bookmark persistence is no longer planned.


## v0.5.0 passive-node auto-start

This version implements the appliance behavior the passive radios need:

**power on a passive renderer in a fresh-idle house -> music starts automatically.**

When effective renderer presence is already positive at service startup, or changes from zero to positive, the policy checks MPD:

- if MPD is stopped, the service rebuilds the configured default folder queue, sets Repeat on, Single off, Consume off, enables Random, and starts playback;
- if MPD is already playing, the renderer simply joins the existing session;
- if MPD is paused, passive-radio arrival resumes the existing paused session from its current track/position instead of replacing the queue with default `MP3s`.

This override applies specifically to passive-radio arrival. A phone/PC/browser controller merely connecting still must not override Pause.

The default folder is configurable with `PASSIVE_DEFAULT_FOLDER` and defaults to `MP3s`.

v0.5.0 toggled Random off and back on around the rebuild. v0.6.2 additionally randomizes the actual queue using OS entropy on every genuinely fresh session. Cross-session shuffle progress is intentionally not retained.


## v0.5.1 pause-resume fix

Real-world leave-and-return testing exposed the expected v0.5.0 edge case: after both passive renderers had been unplugged long enough for the prior session to settle, powering them back on found MPD in `pause` at the start of a queued track. Both renderers were healthy/present, but the v0.5.0 policy intentionally left paused sessions alone, so no audio flowed.

v0.5.1 implements the approved behavior: passive-radio arrival while MPD is paused sends `play` to resume the existing house queue and position. It does **not** rebuild the queue or load default `MP3s`. This preserves session continuity while satisfying the appliance rule that powering on a passive radio should produce music.


## v0.6.0 unattended dropout diagnostics

Occasional short silences were observed with two synchronized ESP32 renderers, sometimes affecting only one node. Rather than requiring simultaneous ping windows or manual timing, v0.6.0 adds an unattended event recorder around the Snapserver state already being polled.

The recorder is intentionally lightweight and memory-only. After hearing a dropout, query `GET /diagnostics` later and compare the affected renderer's events with the other node and the global Snapserver stream.

Interpretation:

- `timesync_stall_started` on only one renderer strongly points toward that node's network/client path;
- `client_present_changed` or `client_connected_changed` confirms a larger renderer connectivity interruption;
- `stream_status_changed` affecting the global stream points upstream toward MPD/Snapserver rather than one renderer;
- if audio drops while all of those remain clean, the next place to instrument is the ESP32 decoder/audio-buffer/I2S path rather than basic network presence.


## v0.5.1 runtime validation

A real leave-and-return test reproduced the paused-session edge exactly:

- both ESP32 renderers were powered off for an extended period;
- both were powered back on later;
- Snapserver reported both as connected/present/audible;
- MPD was `pause` at 0.0 seconds on the retained queue;
- v0.5.0 reported `renderer_joined_paused_session`, leaving the Snapserver stream idle and producing no audio.

v0.5.1 changes passive-radio arrival on a paused session to `play`. After installing/restarting that version with both radios already present, **both outputs started immediately**, proving the behavior on the permanent Pi. The existing queue was resumed rather than rebuilt.

## Two-renderer field result

Two independent XIAO ESP32-S3 + PCM5102A renderers have now played the same house session simultaneously through different downstream analog systems with no objectionable echo or phasing during the sync acceptance test.

A hard-powered renderer also rejoined an already-active song after more than ten seconds powered off. One observed plug-in-to-audible rejoin took about six seconds.

This proves the synchronized distribution path beyond API/network state: multiple real analog outputs are audibly aligned.

## Current reliability investigation

Occasional few-second silences have been heard on one renderer or the other during otherwise synchronized two-node playback. Both nodes have their external antennas installed, and the root cause is currently unresolved.

v0.6.0's `/diagnostics` endpoint is intended to separate:

- one-client Snapcast timing/presence stalls;
- client disconnect/reconnect behavior;
- global Snapserver stream-state changes;
- cases where all server-side indicators remain clean, which would push investigation toward the ESP32 decoder/audio-buffer/I2S path.

The diagnostics feature is implemented and unit-tested. A real dropout capture is still pending.


## v0.6.1 final-track boundary fix

A real passive-radio reconnect exposed an MPD state-machine detail that the v0.4-v0.6 policy did not model correctly.

Observed field state:

- one renderer returned healthy/present;
- MPD was `pause` at **0.0 seconds** on the next queued track;
- Snapserver stream was `idle`;
- session policy still showed `pending_stop_cancelled_renderer_returned`.

This revealed that MPD 0.24 `single oneshot` can finish the departing session by reaching the next-track boundary in **Pause**, not necessarily transport `stop`.

v0.6.1 recognized this boundary but resumed the retained old queue. That historical resume choice is superseded by v0.6.2: completed drain means fresh idle under SESSION_BEHAVIOR §4, followed by a new default shuffle on passive arrival.

**Runtime result:** after installing v0.6.1 with the previously silent S3 still powered, playback resumed automatically. The renderer had already been healthy/present; the fix was entirely in the server session policy. The MPD boundary artifact and v0.6.1 sound recovery were field-proven. Initial v0.6.2 Pi/radio results are now recorded below; the manually selected old-queue-to-default case remains a separate field check.


## Planned runtime passive-default API

Android HOUSE mode now requires the passive-radio default folder to be controllable at runtime instead of only through the startup environment.

Initial allowed values:

- `MP3s`
- `Rap`

The control service needs a small persisted get/set contract for this value. Exact endpoint naming is still implementation detail, but the semantics are fixed:

- GET returns the current server-owned passive default;
- SET accepts only supported library-relative default folders;
- persistence survives service restart;
- changing the value has **no effect on the currently active/paused retained queue**;
- the selected value is consumed only by the next fresh passive-renderer auto-start;
- `PASSIVE_DEFAULT_FOLDER` remains the fallback/default when no persisted value exists.

This requirement supports the Android HOUSE Browser button that replaces the STANDALONE SMB button with an `MP3s` / `Rap` selector.


## v0.6.2 completed-drain and shuffle contract

A pending drain stores the final song's MPD id. Return while that song is still playing cancels the pending stop without Play/Seek/Clear or queue replacement. An ordinary pause on that unfinished song remains resumable. A stopped transport or a boundary pause on the next song completes the drain.

Completion sends Stop before restoring the saved Repeat/Single settings, then clears `pendingFinalStop`/`pendingSongId`. With nobody present, `lastAction` is `final_track_completed_session_idle`. This explicit stopped state prevents a processed boundary pause from being mistaken for a retained session after a later service restart. A return after completion starts a new default session (`lastAction: started_default_session`), including when return and completion are first observed together.

Each fresh default start queries the configured folder's MPD-indexed files recursively, shuffles them using `SystemRandom` (OS entropy), loads that order, enables Random + Repeat All with Single/Consume off, and starts shuffled item zero. There is no fixed seed or saved shuffle progress, and no rejection of a chance repeated first song/order. An empty default produces an error without clearing the old queue and can be retried when files become available.

Pending drains are resolved before a new Snapserver presence baseline, so a monitor outage does not revive a completed queue. MPD write failures keep policy recovery alive. Recovery after the service/Pi itself restarts during an unfinished/unprocessed drain is still an open edge; no broad reboot-persistence claim is made.

The former persistent default-rotation requirement has been removed. Persisting the **folder choice** (`MP3s`/`Rap`) remains planned; changing that setting must not affect an active or ordinary paused session. The completed session's order/progress is never a prerequisite for a fresh passive start.

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

There is no phone-selectable default yet: MP3s is the current deployment setting. The persisted MP3s/Rap runtime selector and controller/output presence are still upcoming HOUSE work. These tests do not represent an Android HOUSE build or an ESP32 firmware release.
