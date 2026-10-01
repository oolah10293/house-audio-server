# HTTP control API

Current source/deployed version: **v0.8.2**, adding guarded in-place queue reordering for Android HOUSE Sort (90 local tests and GitHub CI pass). v0.8.2 is installed on the permanent Pi. Health/startup are good; Android v0.4.0 has successfully adopted the live MPD track in HOUSE after MPD's LAN listener was enabled. Physical controller/mute validation, queue-reorder device testing, and phone/S3 synchronization remain pending.

The companion [Android v0.4.1 correction release](https://github.com/oolah10293/smb-music-player/blob/main/docs/RELEASE_0.4.1.md) implements the Tailscale/routing, conditional playlist-mute and lower-strip output-control corrections found in the v0.4.0 field pass. Device acceptance remains pending; the server API and deployed v0.8.2 are unchanged.

**Requirements clarification, 2026-09-30:** [SESSION_BEHAVIOR.md §7](SESSION_BEHAVIOR.md) now requires Bluetooth-gated Android output; Play/Resume and queue commands must not independently unmute the phone. The older conditional auto-unmute rule is superseded. The deployed API schemas and server pause/retention policy are unchanged. Current Android field results live in [HOUSE_VALIDATION.md](https://github.com/oolah10293/smb-music-player/blob/main/docs/HOUSE_VALIDATION.md).

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

Returns the current autonomous house-session policy state, including:

- whether the policy is enabled;
- the last effective total-node, passive-renderer, controller, and audible counts seen by policy;
- automatic-pause status and reason;
- whether a final-track stop is armed;
- the MPD song id being allowed to finish;
- the most recent policy action.

Session-policy output includes `freshIdleAutoStartImplemented`, `freshSessionShuffleImplemented: true`, `defaultShufflePolicy: "new_each_fresh_session"`, `runtimeDefaultFolderImplemented: true` (v0.7.0), and `controllerPresenceImplemented: true` (v0.8.0). The obsolete `persistentDefaultShuffleImplemented` field is removed: saved order/progress across completed sessions is no longer a requirement. `defaultFolder` is the current runtime selection for the next fresh passive session, which can differ from the folder of the active queue.

### `GET /settings` — v0.7.0

Read the server-owned passive default, without contacting MPD or Snapserver:

```json
{
  "service": "house-audio-server",
  "version": "0.7.0",
  "settings": {
    "passiveDefaultFolder": "MP3s",
    "allowedPassiveDefaultFolders": ["MP3s", "Rap"]
  }
}
```

Reading settings does not register controller presence or start playback.

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

### `POST /settings` — v0.7.0

```json
{"passiveDefaultFolder": "Rap"}
```

Returns the same settings envelope as `GET /settings`, after saving the explicit value. Only the exact strings `MP3s` and `Rap` are accepted. Missing/extra fields, non-object bodies, and unsupported values return HTTP 400. Send an explicit value rather than a toggle command so retrying the same request is idempotent. Concurrent writes are serialized; the latest successful set determines the next selection. Clients should refresh from GET after reconnecting instead of replaying stale edits.

This endpoint never reads or writes MPD, changes the queue/position/transport, cancels a pending drain, or starts a session. It works even while MPD/Snapserver are unavailable. The policy reads the selection once when beginning a fresh passive session; a change after that read applies to the following fresh session. Active playback and ordinary retained pauses continue unchanged.

The value is saved atomically in `/var/lib/house-audio-server/settings.json` using the existing systemd `StateDirectory`. `HOUSE_AUDIO_SETTINGS_FILE` can override that path; its parent must already be writable by the service. `PASSIVE_DEFAULT_FOLDER` is used only when no saved file exists (default `MP3s`, supported fallback values `MP3s`/`Rap`). A saved setting wins over later environment changes. Install/update does not erase it. No queue, shuffle order, or progress is stored.

Storage errors return HTTP 503 with `error: "settings_write_failed"`. A failure before replacement preserves the old setting. If replacement succeeds but its directory sync fails, the response reports uncertain durability and GET shows the new value; read settings before retrying. Invalid/unreadable existing settings fail service startup rather than silently substituting another folder. A missing file uses the fallback without writing a file until the first SET.

Folder selection does not validate MPD's current index. If that folder has no indexed tracks when a fresh start is attempted, the existing empty-folder handling reports a policy error without clearing the queue and retries when music becomes available.

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

Use `/queue/replace` for a new folder/filtered PLAY LIST or selected-track queue. To sort the existing queue while preserving playback, use `/queue/reorder` below.

### `POST /queue/reorder` — v0.8.2

Reorder the existing queue by MPD entry ID, without replacing files or restarting/seeking playback:

```json
{"songIds": [42, 17, 38], "queueVersion": 12}
```

Read the IDs from `GET /queue` and the revision from `GET /state` (`mpd.queueVersion`). Supply every current ID exactly once in the desired order; duplicate file paths still have separate IDs. Only these two fields are accepted. IDs/revision must be nonnegative integers (not booleans).

The service serializes this operation with its other MPD writes, checks the revision before and after reading the queue, then uses only `moveid` commands. The current song ID, elapsed position, transport, Random/Repeat, automatic-pause ownership, and pending drain are preserved. Sorting an unchanged order is a no-op. Android rotates its sorted list so the current entry is first, as in its standalone UI.

Returns the updated `mpd` state. A stale revision or different ID set returns HTTP 409 `stale_queue` without a write; malformed input returns 400 `invalid_request`. Refresh before another deliberate sort. Startup gating returns 503 `startup_pending`, just like other MPD-changing endpoints. Native MPD clients writing outside the service are outside its concurrency boundary. A connection failure partway through the moves can leave a partially reordered queue; refresh authoritative state and do not automatically replay the request.

This endpoint sorts only the existing queue. It does not select new files or replace a PLAY LIST; those operations use `/queue/replace`.

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
- guarded in-place queue reorder (v0.8.2; deployed, dedicated device sort validation pending)
- persisted passive-default read/set (v0.7.0; permanent-Pi validation complete)
- controller attach/heartbeat/detach, output reports, and muted-controller session policy (v0.8.0; deployed baseline passes, physical controller transitions pending)
- Android local receiver mute/readiness reporting (v0.4.1 corrects first-phone-pass behavior; physical acceptance pending)

Implemented:

- fresh idle + passive renderer present/arrives -> load the configured default folder (`MP3s` or `Rap`), enable Random + Repeat All, and start playback;
- passive renderer joining active playback -> leave the existing queue untouched;
- passive renderer joining an ordinary paused/retained session resumes it without replacing its queue; a completed-drain boundary pause is excluded;
- final passive renderer leaves during playback -> finish the current track, then normalize MPD to stopped/fresh idle, including a pause-on-next-track boundary;
- renderer returns before track end -> cancel the pending stop and preserve the current song, position, and queue;
- renderer returns after completed drain -> new randomized queue of the configured passive default; no saved old rotation and no forced first-song difference;
- Snapserver outage -> never interpret it as all renderers leaving.

Still not implemented:

- general transport-command request deduplication/revision checks beyond the guarded `/queue/reorder` operation
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


## Runtime passive-default API — implemented in v0.7.0

Android HOUSE mode now requires the passive-radio default folder to be controllable at runtime instead of only through the startup environment.

Initial allowed values:

- `MP3s`
- `Rap`

`GET /settings` and `POST /settings` implement the persisted contract described above:

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

Pending drains are resolved before a new Snapserver presence baseline, so a monitor outage does not revive a completed queue. MPD write failures keep policy recovery alive. Service/Pi restart is now a hard fresh-session boundary under SESSION_BEHAVIOR §13, implemented in v0.8.1; unfinished drains are abandoned, not recovered.

The former persistent default-rotation requirement has been removed. v0.7.0 adds persistence of the **folder choice** (`MP3s`/`Rap`); changing that setting does not affect an active or ordinary paused session. The completed session's order/progress is never a prerequisite for a fresh passive start.

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

At the time of these v0.6.2 tests, MP3s was the deployment setting and no runtime selector existed. v0.7.0 provides the field-proven settings API; the Android button remains pending. Controller/output presence is deployed in v0.8.0 with baseline checks passing; physical controller transitions remain pending. These results do not represent an Android HOUSE build or an ESP32 firmware release.


## v0.7.0 runtime validation

The persisted passive-default API is now field-proven on the permanent Pi.

Observed sequence:

1. `GET /settings` returned:
   - service version `0.7.0`;
   - `passiveDefaultFolder: "MP3s"`;
   - allowed values `MP3s` and `Rap`.
2. `POST /settings` changed the default to `Rap` successfully.
3. The already-playing song continued unchanged during that write.
4. The last S3 was then unplugged for about ten minutes, allowing the old session to complete and become fresh idle.
5. On S3 power-up, the server started a fresh `Rap` session. The first observed track was Ludacris — *Southern Hospitality*.

This confirms the two key API semantics in real use: changing the future passive default is non-disruptive to the active session, and the saved choice is applied to the next fresh passive-radio session.

Controller presence and muted-phone/output-state policy remain the next server work.

## v0.8.0 controller presence and output contract

**Source/test/deployment status:** 73 tests pass locally and GitHub CI passes. v0.8.0 is installed on the permanent Pi. Initial health and controller-baseline checks pass; physical controller/output transition tests remain pending.

The controller API is shared by Android, Windows, and browser clients. No Android receiver or UI is implemented in this release. The existing MPD transport endpoints remain authoritative. Presence calls update bookkeeping; the policy applies resulting playback transitions on its next poll (normally within 0.5 seconds while dependencies are available).

### Read presence: `GET /controllers`

Returns `service`, `version`, and `presence`:

- `controllers`: controller id, associated renderer id (or null), `present`, `outputMuted`, `outputReady`, remaining lease seconds, and latest sequence; lease tokens are omitted.
- `renderers`: Snapcast renderer id, owning controller id (or null), passive classification, effective presence, and policy audibility.
- `controllerCount`: live controller leases; `passiveCount`: present unassociated radios.
- `presentCount`: passive radios plus unique controlling devices with a live lease or a still-audible output. A phone is counted once across its roles.
- `audibleCount`: passive audible outputs plus unique audible controlling devices.
- `passiveIds`, `snapserverReachable`, `heartbeatSeconds`, and `leaseSeconds`.

`GET /renderers` continues to expose raw normalized Snapcast observations. `GET /controllers` adds controller ownership and reported local output state. Counts from an unreachable Snapserver are incomplete; autonomous policy waits for trustworthy renderer evidence rather than treating its outage as departure.

### Attach: `POST /controllers/attach`

Register before starting the associated Snapcast receiver. Use stable application-generated identities, and associate only that device's own receiver:

```json
{
  "controllerId": "phone-example",
  "rendererId": "phone-example-audio",
  "outputMuted": true,
  "outputReady": false
}
```

`controllerId` is required. `rendererId` may be omitted/null for a control-only browser. IDs contain 1–128 ASCII letters, digits, underscores, dots, colons, or hyphens, beginning with a letter/digit. Output fields are optional booleans, defaulting to muted/not-ready. A control-only client must remain muted/not-ready.

The response includes service/version and:

```json
{
  "controller": {
    "controllerId": "phone-example",
    "rendererId": "phone-example-audio",
    "outputMuted": true,
    "outputReady": false,
    "present": true,
    "leaseRemainingSeconds": 15.0,
    "sequence": 0
  },
  "leaseId": "<opaque lease token>",
  "heartbeatSeconds": 5.0,
  "leaseSeconds": 15.0
}
```

A new attachment replaces that controller's previous lease. An old token cannot update or detach the replacement. A renderer already owned by another controller returns 409 `renderer_already_owned`.

Android must evaluate current Bluetooth audio eligibility on attachment/recovery and report effective local output state. No Bluetooth means muted; an already-connected Bluetooth output joins an already-playing HOUSE session without waiting for another route callback. Preserve local mute across ordinary network recovery, subject to the explicit Bluetooth-route rules in SESSION_BEHAVIOR §7; do not replay queued old output changes. Neither muted nor unmuted attachment starts a fresh session or overrides an ordinary explicit Pause.

### Heartbeat/output report: `POST /controllers/heartbeat`

Send every five seconds and immediately when output state changes:

```json
{
  "controllerId": "phone-example",
  "leaseId": "<token from attach>",
  "sequence": 1,
  "outputMuted": false,
  "outputReady": true
}
```

All five fields are required. Sequence is a positive integer strictly greater than the last accepted sequence for this lease. Out-of-order/duplicate reports return 409 `stale_controller_sequence` without renewing presence or changing output state. Old tokens return 409 `stale_controller_lease`; expired/detached leases return 409 `expired_controller_lease`. After expiry, attach again and use a new sequence starting at 1. These tokens prevent stale lifecycle writes; they are not authentication/pairing credentials.

`outputMuted` reports the effective local mute state, including user mute and enforced route ineligibility. `outputReady` says the receiver/output path is available to render, including while MPD is paused; it is not a claim that music is currently playing. A call, route failure, or receiver failure can report not-ready without discarding the mute preference. This API reports output state; the client must actually mute/unmute its own receiver. Under the approved Android rule, no Bluetooth audio means muted/ineligible even after Play/Resume; a transport command is not evidence of an eligible output.

A controlling output counts as audible only when it is reported unmuted/ready and its associated Snapcast renderer is effectively present and audible. An unmute report without a live receiver cannot resume an automatic pause. A real renderer can remain audible after its control lease expires; it remains a controlling output and never becomes a passive auto-starter.

Background/screen-off apps count while they continue renewing. Expiry is fifteen seconds after the last accepted attach/heartbeat using a monotonic clock; a suspended browser or dead process cannot hold a session indefinitely. Timings are configurable via `CONTROLLER_HEARTBEAT_SECONDS` and `CONTROLLER_LEASE_SECONDS` (heartbeat must be positive and less than expiry).

### Quit: `POST /controllers/detach`

```json
{"controllerId": "phone-example", "leaseId": "<token from attach>"}
```

Stop this device's receiver, stop its heartbeat loop, then detach. The response includes `controllerId` and `detached: true`. Detach is immediate/idempotent for the current token and suppresses lingering renderer-socket evidence for that device. It sends no global Stop/Clear directly. The policy observes the remaining nodes and applies the rules below.

### Session transitions

| Event | Policy result |
| --- | --- |
| Controller joins fresh idle, even unmuted | Stay stopped; wait for explicit Play/selection. |
| Controller joins/quits while a radio remains audible | Preserve shared queue, track, position, and transport. |
| Only controllers with muted/unavailable outputs remain | Pause exactly where playback is, retain queue, mark `autoPaused: true`. |
| Output becomes audible during that automatic pause | Resume retained playback. |
| Explicit HTTP Pause/Stop after an automatic pause | Clear automatic-resume ownership; controller arrival/unmute cannot undo it. A passive-radio arrival may still resume ordinary Pause. |
| Last controller leaves/expires during automatic pause | Stop without advancing; the session ends. Next passive arrival starts a new shuffled configured default. |
| All nodes leave while playing | Finish the current track, then become fresh idle. |
| Controller returns before drain completion | Cancel drain; keep playing if audible, otherwise pause/retain. |
| Controller returns after drain completion | Stay fresh idle; do not resurrect the old queue. |
| Passive radio returns after completed drain | Start the saved MP3s/Rap default with fresh randomness. |

Session snapshots now use `mode: "controllers_and_renderers"`, combined `presentCount`, separate `controllerCount`/`passiveCount`/`audibleCount`, `controllerPresenceImplemented: true`, and `autoPaused`/`pauseReason` (null or `no_audible_output`). These reflect the last successful policy poll.

### Persistence and remaining boundaries

Renderer-to-controller ownership is saved atomically in `/var/lib/house-audio-server/controllers.json` (override `HOUSE_AUDIO_CONTROLLERS_FILE`). Ownership survives expiry, Quit, and service restart so a known phone cannot later be mistaken for a passive radio. Registration must succeed before its receiver connects. Old associations remain classified as controlled; they are not reassigned to a different controller id. Missing files start empty; corrupt/unreadable files fail startup instead of guessing roles. A binding-write failure returns 503 `controller_storage_failed`; do not start the receiver until attach succeeds.

Live leases, reported mute/readiness, automatic-pause ownership, and pending-drain state are intentionally transient. The product rule is now that a `house-audio-server` restart ends the old listening session and returns the house to fresh idle; the server should not reconstruct pause reasons or unfinished drains. Durable renderer ownership and passive-default configuration still persist. A controller reconnecting first stays idle; a passive S3 present/arriving starts a new shuffled configured default. v0.8.1 implements this boundary and is deployed; restart with an already-present passive S3 is field-proven. Physical controller transitions remain pending.

Authentication/pairing, transport-command request deduplication/revision checks, and Android receiver/background-service implementation remain separate work. Do not automatically replay Next/queue writes after a lost HTTP response.


## v0.8.0 permanent-Pi baseline validation

v0.8.0 is installed and running on the permanent Pi.

Observed immediately after deployment:

- `GET /health`: version `0.8.0`, status `ok`;
- MPD reachable, protocol `0.24.0`;
- Snapserver reachable, version `0.31.0`;
- one S3 connected/present/audible and one remembered S3 offline/stale;
- `GET /controllers`: no live controllers, one present passive renderer, `presentCount: 1`, `audibleCount: 1`;
- the active S3 remained classified as passive, showing that the new controller registry did not misclassify existing radios.

This is installation/baseline proof only. Attach/heartbeat/detach, muted-controller automatic pause, audible-return resume, expiry, and last-controller session end still need real field validation.

## v0.8.1 startup readiness and restart boundary

Every service process starts with `startup.ready: false`. Before automatic session policy or an accepted playback write, the server stops MPD, clears the old queue, sets Single/Consume/Repeat/Random off, and verifies empty stopped state. Reset failure (including partial command success or failed verification) is retried at the policy poll interval. Snapserver availability is not a prerequisite. This reset also runs with `PASSIVE_SESSION_POLICY_ENABLED=false`, without enabling passive auto-start.

`GET /health` adds:

```json
{"startup": {"ready": true, "lastError": null}}
```

Readiness starts false and becomes true once for the process. `lastError` contains the latest reset failure while retrying and clears on success. Health remains HTTP 200 with `status: degraded` until startup is ready and both MPD/Snapserver are reachable. `GET /session` and the policy object in `GET /state` expose the same readiness object as `sessionPolicy.startup`. Reads of MPD state/queue while pending can still show old or partially cleared state; clients must not interpret that as a retained live session.

While pending, all MPD-changing POST endpoints return **503** with `error: startup_pending`: `/play`, `/pause`, `/stop`, `/next`, `/previous`, `/seek`, `/shuffle`, `/repeat`, `/queue/clear`, `/queue/replace`, and (from v0.8.2) `/queue/reorder`. Such a request has made no MPD change. Clients should show startup/reconnecting, refresh state after readiness, and accept a current deliberate command; do not replay an uncertain pre-restart queue or skip command.

Settings and controller attach/heartbeat/detach remain available. A lease issued by this new process while reset is pending remains valid under normal expiry rules. Only old-process live state is discarded. Durable passive-default selection and renderer ownership survive. An already-known phone renderer alone is never a passive auto-starter, even before its app reattaches.

After the reset, a passive S3 present/arriving starts a freshly shuffled configured default with Repeat All. A controller alone leaves the empty house idle until deliberate playback. Once startup is ready, ordinary dependency outages do not rerun this reset; same-process retained pauses and return-before-drain-completion behavior remain unchanged.

**Validation:** 85 tests passed for v0.8.1. v0.8.2 adds five reorder tests, bringing the suite to 90, and is now installed on the permanent Pi. The already-present-S3 restart path remains field-proven; physical controller transitions, queue-reorder device behavior, and the all-radios-off restart variant remain pending.


## v0.8.1 runtime restart validation

The permanent Pi is now running v0.8.1. The update itself restarted `house-audio-server` while one passive S3 was still powered.

Observed result:

- the old listening session did not continue;
- a fresh randomized `Rap` session started automatically;
- `GET /session` showed `startup.ready: true` and `lastError: null`;
- `lastAction` was `started_default_session`;
- the persisted default remained `Rap`;
- presence showed one passive/audible renderer and zero controllers;
- no stale automatic-pause or pending-drain state remained.

This validates the restart contract for the **radio already present at service restart** case. Restart with all passive radios off, followed by a later radio arrival, remains a separate field check. Controller attach/heartbeat/detach and muted-controller transition behavior are still awaiting physical/API acceptance tests.


## v0.8.2 deployment / Android integration note

v0.8.2 is installed on the permanent Pi and reports healthy startup/session state.

The first Android HOUSE field pass revealed an operational prerequisite and a client-side transport issue:

- MPD was initially bound only to loopback. HOUSE identity checks from LAN clients require MPD to listen on the configured home-LAN interface/address as well as localhost. The private deployment address remains local configuration.
- After the LAN listener was enabled, Android v0.4.0 entered HOUSE with Tailscale off and displayed the current MPD track.
- Enabling Tailscale caused the delivered app's HOUSE updates to stop, while the phone browser could still read this HTTP API over the Pi's LAN address.
- Therefore normal server reachability is intact; the Android explicit-`Network` transport binding is the blocker. The client will use physical non-VPN routes for HOME/HOUSE qualification and departure detection while ordinary HOUSE traffic uses normal Android routing.

The server API contract itself is unchanged by that client correction.
