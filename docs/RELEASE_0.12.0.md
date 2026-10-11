# v0.12.0 — subwoofer settings and station listening order

This is the **Pi/server step** of [the three-component plan](https://github.com/oolah10293/house-audio-server/issues/10).
It does not flash the Bose S3 or release an Android APK. Keep House Music v0.3.0
and existing node firmware installed for this step.

## What changes

- A Pi page at **http://192.168.1.12:8787/subwoofers** registers subs, edits names
  and assigned speaker checkboxes, and shows demand separately from node feedback.
  The permanent [subwoofer guide](SUBWOOFERS.md) records exact edit/save/add steps
  and the firmware API contract.
- Registered subs are dependent outputs. They cannot start, resume or preserve a
  HOUSE session, and cannot activate themselves or each other. Multiple subs and
  overlapping assignments are supported. Any eligible assigned normal output
  demands its sub, even when playback is paused. Unknown presence stays unknown.
- The server exposes the agreed **30-second** standby delay and accepts fresh
  node status, countdowns, mode and acknowledgment evidence. The sub's later
  firmware update owns the timer, amplifier commands and manual overrides.
- The Pi accumulates radio listening seconds with the phone closed. Saved stations
  return most-listened first, with alphabetic ties. Renaming/restarting retains
  totals. Old listening time cannot be reconstructed; tracking starts now.

v0.11.2's metadata parsing and >10-second Last played behavior are retained.
Listening totals belong to stations, not individual songs. No tuning controls,
regular S3 firmware or standalone SMB Music are changed.

## Counting and persistence

Counting requires advancing MPD radio playback, play intent, no MPD error and at
least one effectively audible normal output. MPD/client zero volume is excluded.
Multiple outputs count once. Subs alone, pauses, observed stalls, reconnect
jumps, missing MPD/Snapserver observations and long sample gaps earn no time.
This is a conservative playback estimate, not an acoustic measurement of whether
someone heard the amplifier. Metadata/announcement changes do not reset a station's
counter. Deleting a bookmark removes its total; continued playback of that removed
bookmark does not recreate the counter.

Totals save atomically in `/var/lib/house-audio-server/radio-listening.json` about
every 30 seconds while dirty, on source/transport transitions, and at graceful
service shutdown. Abrupt power loss can lose the last unsaved interval (normally
up to 30 seconds). A failed save retains in-memory totals and retries, exposing
`listening.persistenceError` on `GET /radio/stations`. An invalid/unreadable totals
file is preserved and reported with `readOnly:true`; playback continues but new
totals are only in memory until the file is repaired/restored and the server
restarted. The optional override is `HOUSE_AUDIO_RADIO_LISTENING_FILE`.

## Install on the Pi

Run from any directory. This updates the existing service; it does not install or
restart MPD, Snapserver or Home Assistant:

```sh
house_server_update_dir="$(mktemp -d)" &&
git clone --branch server-v0.10.0-radio --single-branch https://github.com/oolah10293/house-audio-server.git "$house_server_update_dir" &&
(cd "$house_server_update_dir" && sudo sh install.sh) &&
curl --retry 10 --retry-connrefused --retry-delay 1 -fsS http://127.0.0.1:8787/health
```

Expect version **0.12.0**, MPD/Snapserver reachable and startup ready. Retry handles
the brief interval between systemd starting the process and the HTTP port opening.
The branch retains its historical name. This command uses its current head; the
delivery message identifies the exact tested commit. Existing local configuration,
stations, history, assignments and counters are retained. A server restart follows
the existing fresh-session rule; it does not resume the prior radio/queue.

Open the page, add the Bose device, select its normal speakers, then save. Verify
its name/assignments survive a page reload and the next ordinary server restart.
Registering it excludes it from session decisions immediately. Old firmware remains
awake/controlled as before; **Unknown — no report yet** is expected until step 2.

## Delivery order and temporary limits

1. **This server update:** page, registry/demand/status API and listening totals.
2. **Bose S3:** consume demand and report state; implement Auto / Force standby /
   Force awake, 30-second grace/cancellation and the nonblocking 404 ms wake fix.
   Show status beside existing Home Assistant controls; preserve tuning. No LED.
3. **House Music:** consecutive-swipe fix and station-list visit behavior. The
   server already supplies ranked stations, but current v0.3.0 can re-sort on its
   periodic refresh and appends a just-added station at the bottom. Keeping new
   stations at the top for that visit and freezing row order until reopening
   Radio require the later app update. Add & Play continues working.

No elaborate compatibility layer is introduced. See [server #6](https://github.com/oolah10293/house-audio-server/issues/6),
[Bose #4](https://github.com/oolah10293/house-audio-esp32/issues/4),
[sorting #12](https://github.com/oolah10293/smb-music-player/issues/12) and
[swipes #9](https://github.com/oolah10293/smb-music-player/issues/9).

## Verification and Pi acceptance

Local automated checks: **222 Python tests pass**, including HTTP requests,
registry persistence/write failure, multiple/overlapping assignments, self/cross
rejection, passive/controlled eligibility, sub-only idle/pause/drain/radio cases,
Snapserver failure/stale observation, feedback expiry and acknowledgment handling.
Listening tests cover phone-closed/multiple outputs, sub-only presence, disabled
presence policy, metadata changes, pause/stall/outage/mute, periodic persistence,
restart/rename/deletion, real-process SIGTERM flushing, ranking and failed/corrupt
storage. Installer tests run
the real script against a temporary filesystem and check new modules/assets and
preserved local settings. Compilation, JavaScript/shell syntax and whitespace are
also checked. These use fake MPD/Snapserver, not physical audio.

A DOM simulation against the real HTTP handler also passed registration, own-device
exclusion, multi-speaker save, unsaved edits surviving polling, external-edit
refresh, independent mode/demand/ACK, countdown/expiry labels and connection-loss
display without script errors. Browser rendering was unavailable in the build
environment; desktop/mobile visual acceptance remains pending.

Physical Pi/Bose/phone acceptance is **pending**. On the Pi, verify the page saves,
normal local/radio playback still works, station totals advance while the phone
is closed, pause stops accumulation, and station order reflects totals. After
registration verify a lone connected Bose cannot start/preserve playback. After
the firmware step, verify the full 30-second grace/cancellation, cold boot,
unknown-demand hold, manual mode precedence and honest feedback/ACK semantics.
