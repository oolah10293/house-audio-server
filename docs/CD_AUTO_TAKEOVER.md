# Plug-and-play CD takeover — field status and source integration

**Status as of 2026-11-13:** The CD-to-House-Music integration is running
on the user's Pi with the Lite-On ESP32-S3 CD node. The Pi server has
a field-tested **stop-before-play** change for same-disc track selection:
four consecutive normal-controller requests returned HTTP 200 after the
change, following earlier intermittent two-second MPD timeouts. This
supports resolution of the reported control timeout; long-run reliability,
actual switch-to-audio latency, and full-disc playback remain unverified.
The ESP32's working firmware 3.3 and the MPD timeout were not altered
for this fix. **No new release or installer was authorized.**

Authoritative source: `oolah10293/house-audio-server`, branch
`feature/cd-auto-takeover`, draft PR #20, based on
`feature/cd-house-playback` (previous installer v0.14.2-cd1).
Companion CD node source: `oolah10293/house-audio-esp32`.
Server fix commit: `861a40d2`; regression test commit: `5f77e467`.
The current Pi installation was directly patched for a controlled field
test, not delivered as a new package. Source and field-test detail follow
chronologically in the older investigation notes. Future builds, packaging
and releases require explicit authorization.

## Agreed behavior

| Physical action / control | Server behavior |
| --- | --- |
| Plug in XIAO/Lite-On with playable CD | After Pi gateway sees a valid boot session + disc revision, save current HOUSE library session or radio station; select ordered CD queue; play track 1 |
| Routine five-second ESP heartbeats | Do nothing; keep current track and transport; never restart track 1 |
| Eject CD with XIAO still connected | Stop CD, clear stale MPD WAV queue, preserve the original library bookmark or prior radio |
| Insert a new CD | Start new disc at track 1, preserve **original** pre-CD source/bookmark, never bookmark another CD |
| Power off/unplug CD player | Wait for gateway source liveness timeout (currently 18s from last heartbeat), then restore the prior radio or library and its saved position/modes; restore earlier stopped/paused state when applicable |
| Manually select radio, library, or Resume Playlist while CD is plugged in | Manual selection wins. Ignore further heartbeats or return of the same disc; **only an actual new disc revision or a new boot session** can trigger takeover again |
| Manually Pause/Stop CD | Respect transport; heartbeat cannot restart; playback controls remain usable if disc present |
| All speakers depart | Hold CD paused with original bookmark; restart when an audible output returns unless the user manually paused/stopped |
| Gateway request fails | Preserve existing state; an unreachable gateway is not proof of an unplug |

## Event identity

The XIAO emits `bootSession` (new 16-hex-digit random nonce per power-up)
and positive `discRevision` when a readable disc is discovered. The
registration payload is backwards compatible with the gateway's old fields.
The Pi's gateway validates, stores and reports both attributes, and only
sources reporting this new version can auto-takeover. Old ESP firmware
remains selectable manually. Discovery still uses the **fixed local
gateway**, not arbitrary URLs from clients.

`discRevision` increments at initial recognition, upon a SCSI
MEDIUM NOT PRESENT observed between discs, on USB drive reattachment, or
when a later READ TOC fingerprint differs. Manual Rescan of the identical
disc does not create an automatic takeover. A 15-second TOC recheck while
WAV transport is idle catches rapid swaps between 2-second readiness
checks, but two different CDs with identical TOC data and an undetected
eject cannot be distinguished. The node does not try to read the TOC
during an ongoing WAV transfer.

## Safety and deployment boundary

- Only the Pi is authorized by software design to change MPD's shared
  queue. The ESP node never issues a play command to MPD.
- Existing radio retries, source control, playlist bookmark and
  passive-renderer session logic remain intact for non-CD sources.
- The gateway's 18-second liveness window protects against small heartbeat
  losses; real power-off detection is therefore not instantaneous. If the
  gateway registry briefly empties during a service restart, a separate
  18-second missing-record grace avoids treating that as a CD unplug.
- No automatic repeated MPD writes on a failed play; a selected physical
  event is marked handled before attempting the write.
- Uncertain playlist restoration is not automatically retried; errors are
  held for diagnosis, preventing a second unverified overwrite.
- Gateway HTTP discovery runs outside the MPD write lock so an offline
  gateway cannot freeze user playback controls. The actual observed CD
  event and its MPD writes are serialized with deliberate controller
  commands under the existing lock. Manual source selections suppress
  any already-observed, still-pending CD takeover.
- The Pi gateway is a separate running service with its script at
  `/home/ads-suck/gateway.py`, managed by
  `house-audio-input-gateway.service`. The source `install-cd.sh`
  includes an explicit gateway update and backup/rollback handling.
  Keep independent gateway changes coordinated with the Pi server; do
  not reflash the working ESP32 firmware for source-only cleanup.
- Initial Pi integration, automatic CD takeover and unplug restoration
  were subsequently observed on physical hardware (see the 2026-11-12
  checkpoint below). Four CD track selections through the normal UI
  returned HTTP 200 after the server stop-before-play fix (see 18:20
  checkpoint). Eject while powered, full-disc playback and sustained
  reliability remain unaccepted. GitHub unit tests do not substitute
  for physical audio checks.

## Regression scenarios

`tests/test_cd_auto.py` covers true boot takeover, no heartbeat restart,
eject and new disc, identical track count with new revision, original queue
and exact position restoration, manual radio/playlist overrides, audio
presence changes, suspended gateway, stopped/paused baseline restoration,
legacy firmware compatibility, and no-source fallback.

`tests/test_gateway.py` validates new session/revision registration fields.
**Historical source verification (2026-11-12):** 195 Python tests,
Python source syntax, installer shell syntax, and browser JavaScript
checks passed on commit `768ed6db6af571122fb01c13ff687334d87a9eef`,
run [43277239409](https://github.com/oolah10293/house-audio-server/actions/runs/43277239409).
The subsequently added stop-before-play regression also passed in
[run 43525955013](https://github.com/oolah10293/house-audio-server/actions/runs/43525955013).
The earlier lack of physical testing has been superseded by the dated
field-test checkpoints below. The separate CD package workflow was
changed to require an explicit manual trigger and authorization phrase,
rather than packaging on pull-request updates; no package was requested
as part of cleanup.

## Hardware acceptance checkpoint — 2026-11-12 (user-reported, not independently instrumented)

The user installed the paired House Music Pi integration (`0.14.2-cd1`) and separately updated LiteOn CD player ESP32 firmware. Installation output confirmed House Music restart, HTTP health/browser verification, independent gateway update, and rollback backup at `/var/backups/house-audio-server/pre-cd-20261112-173211-155969`.

**Observed on physical system:** With prior House Music library playback active, inserting/starting the LiteOn CD input caused automatic playback to start from CD track 1. Powering off the ESP32 caused House Music to resume the previous playlist. Powering it back on caused a new automatic takeover starting again at CD track 1. The ESP32 webpage reported 18 tracks, bootSession and discRevision 1, and HTTP 200 Pi registration. The user downloaded a WAV track and reported that the sound was perfect.

**Accepted:** initial takeover, disconnect restoration to previous playlist, repeat takeover on reboot, CD recognition, and WAV audible quality by user's hardware observation. Exact restored playback position was not independently measured. Disc eject while powered, manual override precedence, uninterrupted complete-disc playback through Snapcast, and throughput margins have not been hardware-accepted. The user observed approximately 250 KB/s WAV transfer; the 1500 ms USB BOT wrapper/status timeout remains provisional and must be revisited separately with measured timing before another firmware flash. Do not replace or reflash a working CD firmware merely for cleanup.

This checkpoint supersedes earlier source-only/no-hardware-testing status statements **for these specific tested behaviors only**. GitHub remains the software source of record; this section records the user's real-world acceptance evidence.


## Live track-selection timeout diagnostics — 2026-11-13 (user-reported)
- On the Pi, setting `PYTHONUNBUFFERED=1` for `house-audio-server` via a systemd drop-in made API access logs appear promptly (user supplied logs). This logging-only configuration is a local runtime change, not a repository code change.
- Track clicks at 17:07:16 and 17:07:21 returned `POST /inputs/play 200`; clicks at **17:07:26 and 17:07:37 returned `POST /inputs/play 503`**. User reports **each 503 coincided with a visible `timed out` message**. A 17:07:32 click returned 200.
- Code at this checkpoint: `house_audio_server.py` reads `MPD_TIMEOUT` from the environment, default `2.0` seconds; `MpdClient.run` sets socket connect/read timeouts accordingly. The `/inputs/play` handler operates on the MPD command path, and the server maps `OSError` or `MpdError` to HTTP 503 with JSON `error=mpd_unavailable` and `detail=str(exc)`.
- **Confirmed:** the visible timeout correlates with the *control API's 503*, not merely a gateway-side `Errno 32 Broken pipe` or `Errno 104 Connection reset by peer` log. **Not yet confirmed:** whether the actual deployed `MPD_TIMEOUT` is its 2.0 s default (could be overridden by service environment), which MPD operation timed out, whether MPD itself was busy on stream teardown, and whether increasing the timeout resolves symptoms.
- Investigate by verifying effective runtime `MPD_TIMEOUT`, capturing the 503 response detail or instrumenting command-stage timing, then run a deliberately controlled timeout adjustment **only with user approval**. Preserve deployed firmware 3.3 and avoid equating error-log cleanup with solving user-visible timeouts.

## MPD stalls during CD track selection — 2026-11-13 (investigation checkpoint)

**User-measured, physical Pi evidence:** The `/inputs/play` 503 response was captured as
`{"error":"mpd_unavailable","detail":"timed out"}`. Direct `mpc -h 127.0.0.1
-p 6600 status` probes normally returned in 3–5 ms, but showed 1026 ms, 1725 ms,
1916 ms, and one 3003 ms probe terminated by an external three-second `timeout`
(exit 124). This proves intermittent slow/unresponsive MPD control independently of
House Music's HTTP endpoint. Concurrent MPD journal messages included
`ffmpeg/wav: Packet corrupt (stream = 0, dts = NOPTS).` at 17:17:46,
17:17:52 and 17:17:57. These facts do **not** establish whether MPD is
waiting on HTTP input shutdown, decoding, output, or another lock.

**Verified source relationship:** The `/inputs/play` same-disc path performs
multiple MPD interactions: initial `mpd.state()`, CD queue validation,
`play <trackIndex>`, another `mpd.state()`, then the HTTP
`_mpd_state_response()` performs another `mpd.state()` and reconciliation.
The client socket has a per-operation timeout of `MPD_TIMEOUT`; source and
default configuration both specify 2.0 seconds. The *effective deployed*
environment value has not been read. A 503 can therefore occur after a
successful track-switch command, during a later status read; the user-visible
503 alone does not prove playback failed.

**Companion firmware verification:** The installed LiteOn 3.3 binary is
identified in `house-audio-esp32` as compiled source
`8fe9715801497399eafacd8331a9ccf1ee1e15b4`, reported running after Pi
OTA with MD5 `0e0e4da19597308057924aaf8a65d770`. Its
`HouseAudioCDInput.ino` holds `cdIoMutex` for the duration of an active
WAV transfer; the background disc task skips readiness/TOC commands while the
lease is held. Do **not** resurrect the obsolete hypothesis that TEST UNIT
READY runs concurrently with reads. Its `HouseAudioInputTransport.h`
deliberately cuts off the old length-advertised WAV stream when a complete
new GET arrives. That cancellation could account for FFmpeg's truncated/corrupt
WAV warnings during deliberate track changes, but no packet-level
cause/timing for these particular warnings has been independently proven.

**Updated next step after user feedback (2026-11-13):** The user explicitly
rejected increasing MPD timeout to 5 seconds as an acceptable solution;
track switching should be fast, not simply permitted to block longer.
The source indicates that even a same-disc selection calls multiple MPD
operations (queue verification, `play <index>`, state reads) and the HTTP
response takes additional state readings. Measure precisely which command's
ACK or response is slow during a failed selection before changing policy or
timeouts. A read-only loopback MPD command/response trace during deliberate
track clicks is the smallest discriminating next experiment. Check whether
a switch succeeds despite an error in the final read. The WAV packet-corrupt
messages may result from the intentionally interrupted old stream, but are
not proof of root cause. **No server or S3 changes without specific evidence.**

**Change/deployment status:** This checkpoint is documentation only. No Pi
configuration was changed; no firmware, code, compiler, GitHub workflow,
packaging, installer, or deployment was invoked.

## Exact MPD command timing — 2026-11-13, 17:40 (user's read-only tcpdump)

The Pi loopback MPD command capture identified the actual hanging requests:
- `17:40:00.102890 play 10`: MPD gave no command response before client closed after **2001 ms**.
- `17:40:05.378733 play 2`: MPD gave no command response before client closed after **2002 ms**.
- `17:40:10.662524 play 5`: first MPD response payload after **1884 ms**.
- `17:40:19.240334 play 7`: capture ended before a response; cannot classify as stalled.

The ordinary `status`, `currentsong` and `playlistinfo` requests shown in the
capture completed in milliseconds. This **proves the observed 503 timeouts
were associated with slow `play N` command acknowledgments**, not merely
with a later HTTP state-refresh read. A TCP ACK of a `play` packet does
not mean MPD finished executing the command.

The exact wait inside MPD has **not** been identified; possibilities
include old WAV stream shutdown, new stream startup through the Pi
gateway and ESP32, or decoder synchronization. Do not arbitrarily increase
the timeout as the user rejects five-second track switching. Next
discriminating evidence should align those command timestamps with the
already-recorded `house-audio-input-gateway.service` journal at
17:39:58–17:40:15; only request a new multi-leg packet capture if the
journal lacks sufficient timing evidence. This is a documentation-only
checkpoint, **not a demonstrated code or runtime fix**.

## Multi-service timing correlation — 2026-11-13, 17:40 (physical Pi)
User provided the precise, time-aligned `journalctl` output for
`house-audio-server`, `house-audio-input-gateway`, and `mpd`.
Correlated with the existing MPD TCP trace:

| MPD command started | Previous WAV disconnect or decoder warning | New WAV GET reported 200 | MPD command / browser result |
| --- | --- | --- | --- |
| 17:40:00.102890 `play 10` | old track 2 reset at .103316 | new track 11 at .228864 (126 ms after play) | MPD command socket closed after 2001 ms; `/inputs/play` 503 at 17:40:02.103927 |
| 17:40:05.378733 `play 2` | old track 11 broken pipe at .391150; FFmpeg WAV warning at .378869 | new track 3 at .401502 (23 ms after play) | MPD command socket closed after 2002 ms; `/inputs/play` 503 at 17:40:07.380108 |
| 17:40:10.662524 `play 5` | old track 3 broken pipe at .664012; FFmpeg WAV warning at .662679 | new track 6 at .676291 (14 ms after play) | MPD first response at 1884 ms; `/inputs/play` 200 at 17:40:12.556600 |

**Verified:** The gateway *began responding with HTTP 200 for the requested
new track* within 14–126 milliseconds in these instances; MPD's `play N`
acknowledgment continued to take ~1.9–2.0 seconds. Track indices are
zero-based: `play 10` requests track 11, etc. The logs are consistent
with deliberate old stream interruption; an FFmpeg corrupt-WAV report
at the moment of switching does not independently prove defective PCM.
**Important limitation:** A gateway `GET ... 200` journal line records
HTTP response initiation, **not actual audio payload transfer time or
MPD decoder readiness**. It does not establish that the gateway or ESP32
delivered the *first PCM bytes promptly*. The remaining unresolved wait
is between MPD's command processing, upstream audio delivery, and decoder
startup. The user's goal is fast track selection, **not** increasing the
MPD timeout. Next targeted measurement: time the new TCP/WAV response
headers and first substantial PCM payload from the ESP32 through port 8080
to gateway 8768, aligned with MPD `play` and its `OK` on 6600.
No runtime configuration or firmware changes have been performed.

## ESP32 first WAV payload timing — 2026-11-13, 17:56
The user captured TCP payloads on the Pi from `192.168.1.175:8080` to
the Pi's ephemeral port `43240`:
- `17:56:23.270673`: S3 sent HTTP `200 OK` response headers (110 bytes).
- `17:56:23.273603`: S3 sent 44-byte WAV header (~2.93 ms later).
- `17:56:23.646901`: first 2872-byte audio payload (~373.3 ms after WAV header), followed by additional audio at .647433, .650251, .651509.

**Critical collection bug:** the suggested awk keyed `n[$6]`, but `$6`
is the *fixed S3 source address/port*, not the unique connection. It
therefore emitted only six packets from one TCP stream, not six from each
track change. To sample each stream use destination `$8` (Pi ephemeral
port) as the grouping key, e.g. `if (++n[$8] <= 6) print`.

**Interpretation:** First CD PCM appeared within ~0.37 seconds after
WAV headers for this one stream; it has not been verified how long MPD
takes to accumulate and decode enough audio to acknowledge `play N`,
and gateway `HTTP 200` alone does not prove the PCM is timely.
Historical MPD 0.21 notes say the old `buffer_before_play` option was
deprecated and playback prebuffer made a fixed one second; verify the
behavior of the *actual MPD 0.24 binary* before using that as a causal
explanation. Do not suggest editing obsolete `buffer_before_play`
without proof. No runtime or firmware changes.

## Second ESP32 first-PCM timing and MPD buffering lead — 2026-11-13, 18:00

User's second physical Pi capture shows a new S3-to-Pi TCP stream:
- 18:00:29.029435: S3 sent HTTP 200 headers, 110 bytes.
- 18:00:29.032319: 44-byte WAV header.
- 18:00:29.737039: first PCM payload, 1436 bytes.
- Therefore this stream took **704.72 ms from WAV header to first PCM**,
  versus **373.30 ms** for the prior 17:56:23 sample, varying ~331 ms.
  These were different captures and are not synchronized with the earlier
  17:40 MPD play-command traces.
- The suggested capture awk key was wrong again: for
  `tcpdump -i eth0 -nn -tttt` output, `$6` is the Pi destination
  IP:ephemeral-port, whereas `$8` contains the TCP flags. The output
  therefore had up to four packets *per flag type*, not per connection.

**Relevant upstream research:** MPD 0.21 removed the configurable
`buffered_before_play` and replaced percentage-of-buffer startup with
a duration-based prebuffer target. Discussion of MPD 0.23 reports a fixed
~1 second minimum prebuffer. This makes MPD startup buffering a plausible
additional cost atop delayed initial CD PCM delivery, but the *installed
MPD 0.24.x binary's exact behavior and decoder interactions are not
directly traced*, so no precise 1-second contribution is verified.

**Design lead, not a fix:** CD PCM for 44.1 kHz/16-bit/stereo needs
176,400 bytes/s. Prior user-reported 250 KB/s WAV extraction gives
modest throughput headroom and does not prove reliable subsecond startup.
To reduce actual time to sound, investigate first READ CD batch latency,
failed/retried SCSI reads, sustained USB throughput, and MPD startup
prebuffering before proposing changes. Extending the API timeout would
only suppress 503, not accelerate playback.
No firmware, service, or installer changes occurred.

## Stopped-state versus active-stream play timing — 2026-11-13, 18:06

User ran a 30-second read-only MPD loopback tcpdump capture while issuing
`mpc stop` and selecting CD track 5 in the House Music UI.

**New field result:** The capture shows `stop` receiving MPD `OK` at
18:06:44.771953, with subsequent repeated `status` responses reporting
`state: stop`. At **18:06:48.736130**, the server sent MPD
`play 4`; MPD sent `OK` at **18:06:48.736176**, an elapsed
**0.046 milliseconds** (46 microseconds). Thus MPD acknowledged
`play` immediately with playback previously stopped. Note that the
test has an approximately **4-second gap** after stop before play:
it does NOT prove that `stop; play 4` back-to-back while a stream is
active would be equally fast. The supplied excerpt has only one
`play` command; a second playing-to-playing change was not captured.

**Comparison:** Prior active CD stream transitions `play 10` and
`play 2` received no MPD response and closed after 2001/2002 ms;
`play 5` returned in 1884 ms. This stark difference shifts the
leading hypothesis from MPD always waiting on prebuffer/new-CD startup
to **MPD waiting on or coordinating active old-stream teardown during
a direct `play N` transition**. An immediate `play` ACK from stop
does not measure time-to-audible audio, so it cannot rule out a
separate ~0.37–0.70-second CD first-PCM delay or decoder buffering
*after* the ACK.

**Smallest next discriminating trial:** While the CD is actively
playing, time `mpc stop` and a new `mpc play <zero-based-index>`
issued immediately afterward from the Pi. Compare individual command
durations and whether the user-selected track actually plays. If
fast and correct, a source-only candidate is to send a `stop`
command **before** `play N` in the same-disc CD selection path,
preserving queue, session/bookmark and other source behavior; this
requires tests and separately authorized Pi deployment before any
hardware-validated fix claim. Avoid timeout increases and changing
working ESP32 3.3 firmware.

**Status:** No runtime changes, source implementation changes,
firmware builds, packages, installers, releases or deployments.

## Immediate stop-then-play timing — 2026-11-13, 18:11

User ran the prescribed back-to-back shell timing test on the Pi:

```sh
a=$(date +%s%3N)
mpc -h 127.0.0.1 -p 6600 stop >/dev/null
b=$(date +%s%3N)
mpc -h 127.0.0.1 -p 6600 play 5 >/dev/null
c=$(date +%s%3N)
printf 'STOP: %d ms\nPLAY: %d ms\n' "$((b-a))" "$((c-b))"
```

Reported `STOP: 4 ms`, `PLAY: 3 ms`; total shell timing **7 ms**. The
test sequence was requested while CD playback was active, but the
user provided only the command output, not confirmation of audible
playback or proof that MPD was actively playing at invocation.
The command exit codes were masked by the subsequent assignments and
printf, so the presented timings alone do not establish that the new
track actually played. Command timing includes client startup overhead
and only has integer-ms resolution.

**Evidence-supported hypothesis:** The command path `stop` then
`play` is dramatically faster to acknowledge than direct
`play N` during documented active CD transitions (~1.9–2.0 s).
The next narrowly scoped source-only candidate in
`house_audio_server.py` `RadioController.select()` / same-CD
selection branch is to send `stop` before `play {trackIndex}`,
without flushing/replacing the queue or recapturing bookmarks.
Test playback and state persistence. Do not alter the unrelated
radio or playlist transitions. User requests no timeout increase and
no firmware changes. No software code, builds, packages,
releases or deployments authorized or done as of this checkpoint.


## Active-stream stop-then-play confirmed — 2026-11-13, ~18:11

The user explicitly confirmed the CD was **already playing** before
executing the corrected **one-line** shell command on the Pi:

```sh
a=$(date +%s%3N); mpc -h 127.0.0.1 -p 6600 stop >/dev/null; b=$(date +%s%3N); mpc -h 127.0.0.1 -p 6600 play 5 >/dev/null; c=$(date +%s%3N); printf 'STOP: %d ms\nPLAY: %d ms\n' "$((b-a))" "$((c-b))"
```

Reported command timings: **STOP 4 ms**, **PLAY 4 ms**, **8 ms combined**.
These include shell/client overhead and integer-ms timestamp rounding;
they are not time-to-audible-audio measurements. The earlier pasted
multi-line second trial included accidentally copied shell prompt text
and produced `STOP: 48563 ms`; it is INVALID because the timestamp
variable was stale. Disregard it.

**Strong evidence:** MPD's old-active-CD transition by `stop` then
`play` completes commands quickly in this live test versus old
direct `play N` command taking 1884 ms or timing out at ~2000 ms.
This supports a narrowly scoped same-CD-track switch adjustment in
`house_audio_server.py`: send `stop` before `play {trackIndex}`.
It is **not yet implemented** and GUI end-to-end behavior is untested;
the user has not authorized a build/release/installer/deployment.
Do not alter timeout or the 3.3 ESP32 firmware.

## Pi live test of stop-before-play CD switching — 2026-11-13, 18:20

The user applied the narrowly scoped same-disc `stop` before `play N`
server change to the Raspberry Pi for an expressly requested live test.
The source implementation is commit `861a40d2`; the model regression
test is commit `5f77e467`. The standard GitHub regression workflow
completed successfully at
https://github.com/oolah10293/house-audio-server/actions/runs/43525955013.

After the user restarted the Pi service and switched through CD tracks
in the House Music interface, the user supplied the server journal:

```
Nov 13 18:20:01 pihole python3[788480]: 192.168.1.125 - "POST /inputs/play HTTP/1.1" 200 -
Nov 13 18:20:07 pihole python3[788480]: 192.168.1.125 - "POST /inputs/play HTTP/1.1" 200 -
Nov 13 18:20:12 pihole python3[788480]: 192.168.1.125 - "POST /inputs/play HTTP/1.1" 200 -
Nov 13 18:20:22 pihole python3[788480]: 192.168.1.125 - "POST /inputs/play HTTP/1.1" 200 -
```

**Verified:** Four consecutive GUI CD track selections returned HTTP 200
with **zero HTTP 503 among those four** after the patch. The user
reported "seems solved." This is strong physical evidence that the
reported track-selection control timeout was resolved by stop-before-play.
**Limit:** These four requests do not establish absolute absence of
future 503s or measure audible switch latency or complete-disc playback.
The ESP32 firmware 3.3, gateway and MPD timeout were not altered in
this iteration. This was a direct Pi source edit and service restart for
field validation, not a firmware rebuild, package, installer or release.


## Cross-conversation cleanup audit — 2026-11-13

Audit scope: current Lite-On CD integration conversation AND the prior
filled-up conversation, compared to `oolah10293/house-audio-server`
and the pinned compiled ESP32 3.3 source. This is a **finding list**,
NOT a set of fixes, not build authorization, and not a claim of hardware
failure where the source merely suggests a risk.

### High-priority unverified behavior

1. **CD Next/Previous control bypasses the proven stop-before-play
   workaround.** In `house_audio_server.py`, `/inputs/play` same-disc
   selection now executes `stop; play N`, but the CD-specific
   `/next` and `/previous` handler (approximately lines 2577–2580)
   still calls MPD `next` or `previous` directly. Those operations
   may have the same active-stream teardown delay. **This is verified
   source asymmetry, NOT a measured Next/Previous timeout.**
   First test Next/Previous with timed control requests while CD is
   playing; if reproduced, test a bounded same-CD-only stop/targeted
   play design that preserves boundary behavior and bookmarks.
2. **Passive end-of-track auto advance and complete-disc playback**:
   no independent hardware acceptance of consecutive unclicked track
   progression, entire disc streaming, Snapcast timing, or end-of-disc.
   The four 18:20 HTTP 200 results exercise explicit track selections
   only.
3. **Actual audible change latency**: HTTP 200 and fast MPD command
   acknowledgement do not measure audio reaching house renderers.
   Earlier ESP32 first-PCM network timing varied ~373–705 ms.
   Physical audio start-to-sound/end-to-sound was not measured.
4. **Gateway 502 / ESP32 TCP RST regression**: Firmware 3.2 had a
   packet-confirmed new-track RST and 502; 3.3 substantially improved
   user-reported switching, but gateway/ESP32-level RST/502 absence
   after 3.3 plus Pi hotfix is NOT independently verified. Keep useful
   gateway diagnostics until such a check; do not conflate expected
   old-track `Broken pipe` with a failed new-track GET.
5. **Disc change / restoration acceptance**: Physical eject while
   powered, replace with a different disc, identical-track-count
   disc swap, real manual radio/library override while CD remains
   inserted, and exact prior playlist **position/modes** restoration
   remain independently unaccepted. A prior power-off/reboot restored
   the previous playlist, but not an instrumented precise position.
6. **CD UI usability**: User reported "there is no stop" when asked
   to stop the CD from the House Music UI. Current browser HTML has a
   generic Now Playing Stop button; user experience and CD tab
   discoverability must be checked rather than claiming the button
   is missing. Server supports CD Play/Pause/Stop/Next/Previous;
   Android CD-specific UI remains documented as not implemented in
   `docs/CD_AUDIO_INPUT_INTEGRATION.md`. CD seeking is explicitly
   unsupported because gateway WAV transport lacks HTTP Range.
   The earlier conversation discussed **physical** eject detection,
   not a confirmed requirement for a software Eject button.

### Operational and release hygiene

7. The Pi is live with the narrowly scoped source hotpatch to
   `/opt/house-audio-server/house_audio_server.py`, while PR #20
   remains draft; verify the installed file and the backup
   `house_audio_server.py.before-cd-stop` before any future install,
   and ensure a future authorized installer preserves the change.
   GitHub source is the canonical implementation. No claim that a
   fresh install from default `main` would contain this PR.
8. User explicitly requested retaining diagnostics after 3.3
   improvements to resolve the timeout messages. The server's
   `PYTHONUNBUFFERED=1` drop-in is live configuration reported in
   the log investigation but has not been independently compared
   against systemd source defaults. Preserve useful journald
   diagnostics until remaining cases are resolved. Temporary packet
   captures and their deletion are a separate cleanup decision.
9. Historical CD integration documents and installer/package names
   mix source-only 2026-11-12 claims with later 2026-11-13
   hardware acceptance. Main acceptance document has a current-status
   preamble; older date-specific notes remain historical. Do not
   silently rewrite historical evidence or package on a cleanup PR.
10. Firmware-side verification still pending in companion ESP32 repo:
    provisional 1,500-ms USB CBW/CSW timeout, intermittent SCSI 0x2
    readiness recovery, CD READ retries/terminal truncation behavior,
    actual 4x speed hint compliance, and streaming throughput under
    sustained load. See its `USB_TIMEOUT_FOLLOWUP.md`.

### Already addressed (do not redo)

- Direct same-disc GUI track-selection MPD timeout: stop-before-play
  source patch and four consecutive HTTP 200 hardware requests.
- ESP32 3.3 OTA installed and verified by exact compiled commit.
- Ordinary CD boot auto-takeover and power-off return to the previous
  library playlist observed on hardware at least once.
- PR-triggered CD Pi installer creation was disabled: packaging requires
  separately authorized manual dispatch.
- Do not reflash working ESP32 3.3, increase MPD's timeout, or
  rebuild/package without the user's separate explicit authorization.

## Cleanup item 1 — CD Next/Previous (slow control responses measured; MPD isolation pending)

**Scope:** This is item 1 of the user's nine-item cleanup audit. Work
through the list sequentially, writing each outcome to this repository
before advancing. Items 2–9 remain OPEN/UNTOUCHED.

**Verified from the current source** (`house_audio_server.py`):
- Clicking a CD track in the browser uses `POST /inputs/play`,
  `RadioPlayback.select()`, and the tested same-disc `stop` then
  `play {trackIndex}` sequence. The normal Pi hardware test yielded
  four HTTP 200 responses, and the GitHub model regression passed.
- The CD-specific `POST /next` and `POST /previous` handler executes
  `mpd.command("next")` or `mpd.command("previous")` directly while
  CD is selected. It does **not** execute `stop` first.
- `tests/test_cd_inputs.py` checks Next/Previous selection and bookmark
  preservation with a model; it does **not** exercise the live MPD
  WAV stream teardown timing.

**Inference, not yet a defect:** The direct Next/Previous commands
_may_ exhibit the 2-second active-stream MPD stalls formerly seen with
direct `play N`. Do not change them solely due to code asymmetry:
the MPD commands have different semantics, including end-of-disc
and Previous behavior depending on elapsed playback position.

**Next experiment:** During playback of a middle CD track on the Pi,
send one `/next` and one `/previous` HTTP POST (empty JSON body) to
local House Audio port 8787, record HTTP status and total round-trip
time, and confirm the audible track/result. Check Pi journal for
`POST /next` and `POST /previous` 200 vs 503. No code edit, build,
package, firmware, or service restart is needed.

**2026-11-13 physical Pi HTTP test (user-provided command/output):**
While instructed to have a middle CD track playing, user executed:

```sh
for path in next previous; do
  curl -sS -o /dev/null \
    -w "$path: HTTP %{http_code}, %{time_total}s\\n" \
    -H "Content-Type: application/json" \
    -d '{}' "http://127.0.0.1:8787/$path"
done
```

Output:

```
next: HTTP 200, 1.735032s
previous: HTTP 200, 1.790452s
```

**Verified observation:** Both HTTP requests returned 200, but each
took roughly 1.7–1.8 seconds end to end. This is slower than the
earlier ~4ms + ~4ms direct MPD `stop` and `play` command timing.
**Not established:** Which part of HTTP handling caused the delay;
whether direct MPD `next` and `previous` individually consumed that
time; whether the intended tracks were audible/correct (not yet
reported). Distinguish HTTP response timing from MPD command
acknowledgement and audio arrival time. Two successful requests do
not prove timeout-free Next/Previous transitions.

**Next isolated experiment:** with CD still playing, measure direct
Pi MPD `next` and `prev` command durations using `mpc`, and confirm
that each selects the intended track. No software edit required.
If MPD command latency reproduces the delay, design a *CD-only*
stop-before-targeted-play solution that preserves queue, bookmark,
last-track behavior and standard MPD Previous semantics; add
regression tests before a separately authorized Pi deployment.
Do NOT blindly prepend `stop` to an MPD `next`/`previous` command.

**Status:** OPEN — slow HTTP Next/Previous response reproducible in
one pair of requests; direct-MPD timing and audible correctness still
need confirmation. Items 2–9 remain OPEN/UNTOUCHED.
