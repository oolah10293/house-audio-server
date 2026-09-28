# HTTP control API

Current service version: **0.5.1**

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

The service keeps a long-lived Snapserver control connection, takes an initial `Server.GetStatus` snapshot, listens for connect/disconnect/volume/group/stream notifications, and refreshes the full authoritative snapshot after relevant changes.

### `GET /session`

Returns the current autonomous passive-renderer policy state, including:

- whether the policy is enabled;
- the last effective passive-renderer count seen by policy;
- whether a final-track stop is armed;
- the MPD song id being allowed to finish;
- the most recent policy action.

v0.4.0 intentionally reports that fresh-idle auto-start and controller presence are still unimplemented.

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
- passive renderer joining a paused session resumes that existing session without replacing its queue;
- final passive renderer leaves during playback -> finish the current track, then stop;
- renderer returns before track end -> cancel the pending stop and keep the session playing;
- Snapserver outage -> never interpret it as all renderers leaving.

Still not implemented:

- durable saved progress/order for the default `MP3s` shuffle rotation
- muted-controller pause/retain behavior
- persistent default `MP3s` shuffle rotation
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

This phase deliberately does **not** auto-start the default `MP3s` rotation yet. Persistent default shuffle/bookmark state is the next autonomous-session chunk.


## v0.5.0 passive-node auto-start

This version implements the appliance behavior the passive radios need:

**power on a passive renderer in a fresh-idle house -> music starts automatically.**

When effective renderer presence is already positive at service startup, or changes from zero to positive, the policy checks MPD:

- if MPD is stopped, the service rebuilds the configured default folder queue, sets Repeat on, Single off, Consume off, enables Random, and starts playback;
- if MPD is already playing, the renderer simply joins the existing session;
- if MPD is paused, passive-radio arrival resumes the existing paused session from its current track/position instead of replacing the queue with default `MP3s`.

This override applies specifically to passive-radio arrival. A phone/PC/browser controller merely connecting still must not override Pause.

The default folder is configurable with `PASSIVE_DEFAULT_FOLDER` and defaults to `MP3s`.

The queue rebuild explicitly toggles Random off before loading and back on afterward so MPD creates a fresh randomized play order over the complete default queue. Durable cross-session shuffle progress is still a later milestone.


## v0.5.1 pause-resume fix

Real-world leave-and-return testing exposed the expected v0.5.0 edge case: after both passive renderers had been unplugged long enough for the prior session to settle, powering them back on found MPD in `pause` at the start of a queued track. Both renderers were healthy/present, but the v0.5.0 policy intentionally left paused sessions alone, so no audio flowed.

v0.5.1 implements the approved behavior: passive-radio arrival while MPD is paused sends `play` to resume the existing house queue and position. It does **not** rebuild the queue or load default `MP3s`. This preserves session continuity while satisfying the appliance rule that powering on a passive radio should produce music.
