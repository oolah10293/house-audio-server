# House Audio Server v0.10.0

Server support for the planned House Music Radio page: paste a direct stream
URL, save it under an automatic name, select a saved station, rename it or
delete its bookmark. The list belongs to the Pi and is shared by controllers.

This is a server release only. The new Android Radio page is the next app
integration step; no APK or ESP32/S3 firmware is included or required to run
the API checks below. MPD remains the only source/decoder and feeds the existing
FIFO → Snapcast → renderer path.

## Changes

- Adds `GET/POST /radio/stations`, station rename/delete endpoints and
  `POST /radio/play`. Add validates/probes a direct HTTP(S) media response
  and saves without touching playback. Duplicate URLs return the existing
  bookmark; playing is a separate deliberate action.
- Uses stream station names when available, a readable URL fallback otherwise,
  and preserves user names. Station names remain separate from current-song
  titles. Saves the list atomically in the existing service state directory.
- Adds live source, station, intent, status/error/retry and capability fields
  to `/state`. Radio Pause/Stop close the upstream connection; Play reconnects
  live. Live seek/skip/shuffle/repeat/sort requests are rejected.
- Retries stream errors/stalls with capped backoff while the same station is
  selected and Play is intended. Pause/Stop/source changes cancel reconnects.
  An external queue replacement is respected rather than overwritten.
- Extends muted-controller retention to live station pause. When nobody remains,
  radio stops/clears instead of waiting forever for a song boundary. A service
  restart still ends the session. Passive startup after idle/restart still uses
  the saved MP3s/Rap default; saved station bookmarks survive.
- Returning to files builds the newly selected folder queue and restores the
  pre-radio playback-mode settings. No old queue/song restoration is attempted.
  Existing independent-app, library-refresh and library-session behavior stays
  in place.

The full contract is in [API.md](API.md) and
[SESSION_BEHAVIOR §16](SESSION_BEHAVIOR.md#16-internet-radio-as-a-shared-house-source).
The trusted LAN API still has no authentication/pairing layer. New station
probes accept direct audio/HLS response types; web-page URLs, PLS and ordinary
M3U station directories need their underlying stream URLs instead.

## Verification and field status

- Python compilation, shell syntax checks and `git diff --check` pass.
- All **158 local tests pass**: 103 existing regressions, 19 station-store/probe
  tests and 36 radio integration tests. These cover durable bookmarks/naming,
  validation and redirects, live API/control behavior, retry cancellation,
  radio/library switching, controller/presence lifecycle and startup boundaries.
- Failure-path tests cover a failed station switch preserving ownership of the
  old queue, automatic resume retaining radio retry intent, and cleanup restoring
  library modes before clearing so a failed write can be retried safely.

These are local automated checks, not physical Pi/S3 acceptance.

**Pi deployment and v0.10.0 physical acceptance are pending.** A header probe
and successful MPD Play command do not prove decoding or audible output.
Live stop/pause latency through the existing synchronized buffer, automatic
reconnect, source switching, multi-node radio and last-node lifecycle all need
the physical checks below.

Existing evidence is narrower: the Pi is confirmed running v0.9.2, and the
2026-10-09 manual Radio Paradise test was audible on one S3 through the existing
MPD/Snapcast path. WXDX's remote public feed was verified, but Pi/S3 playback
was not explicitly confirmed. These results do not validate this new API;
see [the field test record](https://github.com/oolah10293/house-audio-server/issues/5#issuecomment-6093245333).

## Installation on the Pi

Run when playback can be interrupted. The installer restarts
`house-audio-server`; its established startup boundary stops/clears the old
session. A passive S3 already present then starts the configured default folder
with a fresh shuffle. Saved defaults, renderer ownership and station bookmarks
survive. MPD/Snapserver configuration and the separate audio-buffer trial are
unchanged.

```sh
house_radio_update_dir="$(mktemp -d)" &&
git clone --branch server-v0.10.0-radio https://github.com/oolah10293/house-audio-server.git "$house_radio_update_dir" &&
(cd "$house_radio_update_dir" && sudo sh install.sh) &&
curl --retry 10 --retry-connrefused --retry-delay 1 -fsS http://127.0.0.1:8787/health
```

Use **`server-v0.10.0-radio`**, not the repository default branch. This release
builds on the deployed v0.9.2 cleanup branch; its review targets
`server-v0.9.2-cleanup` so the radio diff does not duplicate that still-unmerged
cleanup. Neither parent nor default branch is changed by this release branch.

Confirm version `0.10.0`, `startup.ready: true`, status `ok`, and reachable
MPD/Snapserver. If startup is still resetting, repeat the health request.

The installer includes the new `radio_stations.py` module. Bookmarks live at
`/var/lib/house-audio-server/radio-stations.json`; the optional
`HOUSE_AUDIO_RADIO_STATIONS_FILE` override must point to a file whose parent is
writable by `houseaudio`. No manual state-file permission changes are needed
for the normal install.

## First radio check

Keep at least one audible S3 powered on. With no present nodes, the normal
policy immediately ends live playback. Run this on the Pi to save Radio
Paradise, capture its real generated ID, then play it:

```sh
house_radio_station_id="$(curl -fsS -X POST http://127.0.0.1:8787/radio/stations \
  -H 'Content-Type: application/json' \
  -d '{"url":"https://stream.radioparadise.com/rock-192"}' \
  | python3 -c 'import json,sys; print(json.load(sys.stdin)["station"]["id"])')" &&
curl -fsS -X POST http://127.0.0.1:8787/radio/play \
  -H 'Content-Type: application/json' \
  -d "{\"stationId\":\"$house_radio_station_id\"}"
```

Then check state and listen:

```sh
curl -fsS http://127.0.0.1:8787/state | python3 -m json.tool
```

Expect `source.type: "radio"`, `live: true`, the saved station, increasing
MPD elapsed time and eventually `source.status: "playing"`. A blank song title
is possible. Confirm audible sound; state alone is not enough. The initial
POST may report `connecting` while MPD opens the stream.

Try Pause, then Play, then Stop as separate commands, allowing each result to
be heard before running the next:

```sh
curl -fsS -X POST http://127.0.0.1:8787/pause -H 'Content-Type: application/json' -d '{}'
```

```sh
curl -fsS -X POST http://127.0.0.1:8787/play -H 'Content-Type: application/json' -d '{}'
```

```sh
curl -fsS -X POST http://127.0.0.1:8787/stop -H 'Content-Type: application/json' -d '{}'
```

Radio Pause should report logical `source.status: "paused"` with raw MPD
transport `stop`; Play returns to the live broadcast rather than the paused
moment. Stop cancels reconnect attempts. To return to files, select and play
a folder in the existing House Music app. It builds that folder's queue; it
does not restore the exact old song or queue.

## Remaining physical checks

1. Add a second station using the same add/play steps, such as
   `https://stream.revma.ihrhls.com/zc2033` (WXDX). Confirm station switching
   and audible output. Repeat an add to check that it does not duplicate or
   interrupt playback. List/rename/delete through the documented endpoints;
   deleting the playing bookmark should leave its current broadcast running.
2. Switch radio → library → radio. Confirm folder playback and its prior
   Shuffle/Repeat settings are restored, and the old station never restarts
   over the selected folder. Verify unsupported live controls return 409.
3. With radio playing, join a second S3 and check audible synchronization.
   Remove only one output and verify the other keeps playing.
4. Leave only a muted House controller: the stream closes while the station
   is retained. Return an audible output: it resumes live. Quit/detach the
   final controller or turn off all passive nodes: after presence expiry,
   verify Stop, empty queue and no reconnect. Later passive power-on should
   start the saved MP3s/Rap default.
5. Interrupt the upstream stream, verify retry/error state, restore it and
   check recovery. Repeat with Pause/Stop and a library selection during
   retry; the old station must not return. A Snapserver outage must not be
   interpreted as everybody leaving.
6. Restart the control service: bookmarks/names should remain, but radio
   playback intent should not. An already-present passive S3 should start
   a fresh default folder; controller-only return should stay idle.

The existing House Music APK has no Radio page or live-aware controls yet.
Use these HTTP checks for radio; its old seek/skip/shuffle/repeat buttons may
receive the intentional 409 response until the app integration is delivered.
