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

## Cleanup item 1 — CD Next/Previous (MPD delay isolated; source fix pending Pi acceptance)

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

**2026-11-13 physical Pi direct-MPD isolation (user-provided):**
With CD playing, the user measured normal MPD commands independently
of House Music's API:

```sh
for cmd in next prev; do
  a=$(date +%s%3N)
  mpc -h 127.0.0.1 -p 6600 "$cmd" >/dev/null
  result=$?
  b=$(date +%s%3N)
  printf '%s: %d ms, exit %d\\n' "$cmd" "$((b-a))" "$result"
done
```

```
next: 1652 ms, exit 0
prev: 1731 ms, exit 0
```

**Verified:** Direct MPD Next and Previous *both succeeded* but
acknowledged in ~1.65–1.73 seconds, closely matching the API's
~1.74–1.79 seconds. This isolates the long control delay to MPD's
direct skip path rather than exclusively to House Music HTTP handling.
The earlier directly measured **stop ~4 ms, play ~4 ms** test offers
a faster CD-only alternative. The exact internal reason MPD spends
~1.7 seconds in each command is not independently instrumented; WAV
stream unwinding is the leading hypothesis. No audible track correctness
report for this latest pair was provided.

**Source-only candidate committed to branch (not installed on Pi):**
- `05685152` updates the CD-only `/next` / `/previous` path to
  obtain the MPD current queue index and, **only while a middle CD
  track is actively playing** in ordinary ordered mode, issue
  `stop` then `play {adjacentIndex}`. It neither clears/replaces
  the queue nor recaptures the pre-CD bookmark. A pause, a missing
  current index, a first/last-track boundary, or unexpected MPD
  playback modes use the original `next` / `previous` command.
  Unrelated library/radio controls remain unchanged.
- `79fe0e42` asserts the exact command sequence for middle tracks,
  bookmark preservation, and native behavior at boundaries/paused or
  unexpected mode. Source tests/model checks alone are **not**
  validation of Pi/MPD/audio latency or full MPD skip semantics.

**Next acceptance:** Check CI result for `79fe0e42`; test the
changed Pi source **only with user authorization**, measure House
Music `/next` and `/previous` response durations again during
CD playback, and confirm the expected track actually plays.
Record outcomes before declaring the issue solved.

**Status:** OPEN — MPD-control delay isolated and source-only
candidate written. No Pi deployment, firmware build/flash, installer,
release, or new package was authorized or performed. Items 2–9 remain
OPEN/UNTOUCHED.

## Cleanup item 1 — Pi field timing after Next/Previous patch (2026-11-13)

**User-provided live measurements**, following the user-authorized
surgical Pi source edit and service restart instructions (the assistant
does not have direct shell access to verify the Pi's local file SHA):

```text
                              Before patch         After patch
POST /next         HTTP 200, 1.735032s    HTTP 200, 0.025442s
POST /previous     HTTP 200, 1.790452s    HTTP 200, 0.024527s
```

Both requests were accepted as **HTTP 200**. Compared with the original
API baseline, end-to-end request times decreased from approximately
1.7–1.8 seconds to approximately 25 milliseconds. This is a
**field-confirmed control-response latency improvement**; unlike source
tests alone, the numbers come from the user's Pi after applying the
patch. The previous direct-MPD skip-command baseline was ~1.65–1.73s.
The exact new Pi file hash and specific active CD track index were not
supplied, and the user has **not yet explicitly confirmed audible
Next/Previous correctness** for this run. HTTP success by itself
does not prove correct track audio or full-disc boundary behavior.

**Status:** #1 response-time defect = **PASS on user Pi**.
Remaining acceptance before closing #1: user confirms Next and
Previous actually played the expected tracks; first/last-track
native-command fallback is checked by model tests but not
instrumented on live Pi. If the user confirms expected audio,
record it before treating #1 closed. Items #2–9 remain OPEN and
untouched. No new firmware, build, package, release, or deployment
was initiated by the assistant in this checkpoint.

## Cleanup item 1 — acceptance-test sequencing correction (2026-11-13)

The user clarified that the previous field timing command executed
`/next` followed immediately by `/previous`; the second command
reversed the first before its new CD track could be heard.

**Conclusion:** 25ms HTTP command acknowledgements remain a valid
control-response latency measurement (both 200). However the
back-to-back test was **not** a valid audible Next/Previous test and
must not be treated as such. The reported rapid return to the original
track is expected from test sequencing, **not evidence that the fix
caused a playback regression**.

**Next single acceptance step:** With a middle CD track playing, use
House Music's **Next** button; listen to the next track for several
seconds; then press **Previous** and listen for the original track.
Only mark #1 fully accepted after the user reports correct audible
tracks. No shell script or firmware/server change needed for this step.
Items 2–9 remain open, untouched.

## Cleanup item 1 — acceptance result: normal Next/Previous PASS (2026-11-13)

**User-verified live result after authorized local Pi source patch:** The
user ran `/next`, let the selected track play for ten seconds, then ran
`/previous` and allowed ten more seconds to listen. Output:

```text
NEXT
HTTP 200, 0.011574s
Listening to next track for 10 seconds...
PREVIOUS
HTTP 200, 0.016043s
Listening to original track for 10 seconds...
Test complete.
```

The user's assessment was: **"seemed to work fine"**. This constitutes
positive user-reported audible acceptance of the normal middle-track
Next/Previous case, not a measured sample-level or full-disc guarantee.

**Outcome:** #1 primary defect **CLOSED / ACCEPTED for normal active
middle-track skipping**. HTTP acknowledgements improved from
1.735032s / 1.790452s to 0.011574s / 0.016043s in the
user's delayed listening test, and both requests returned 200.
Source fix `05685152`, regression test `79fe0e42`; GitHub test
workflow for that test commit passed. The change is in the Pi's
working installation per the user's supplied test, not yet merged to
default `main` or shipped via a release.

**Explicit residual limits:** First/last-track boundary handling,
paused/unusual playback modes, precise time-to-audible, and whole-disc
automatic advancement have *not* been physically accepted by this
test; keep them in the broader CD acceptance backlog rather than
quietly claiming they passed. The Pi on-disk hash/backup path was not
independently retrieved. Items #2–9 remain open; next focus is #2
(the old intermittent CD-readiness failure) once the user elects to
proceed. No build, package, release, installer, or firmware operation
authorized or performed.

## Cleanup item 1 — first/last-track hardware boundary result (2026-11-13)

**User-provided live Pi result:** With a 13-audio-track CD shown in
the House Music browser, pressing **Previous on track 1** raised an
MPD error toast:
`ACK [5@0] {previous} Decoder failed to seek`.
The user also confirmed **Next on the final track stops playback**.

**Verified source explanation:** In the previously installed
`house_audio_server.py`, a CD `/previous` when `songPosition == 0`
calculated target -1, then fell back to the native MPD `previous`
command. Unlike a seekable library file, the live CD WAV HTTP stream
does not support ordinary seeking. The screen shows a concrete
boundary failure, not a hypothetical risk. Whether MPD uses its
seek-to-start logic internally was not separately traced; the error
message directly identifies a failed decoder seek.

**Desired behavior:** On track 1, Previous restarts track 1 from
the beginning without a seek. On the last track, Next stops playback
without wrapping, as the user observed.

**Source-only fix (not yet installed on Pi):**
- `4f094365`: for active, normal ordered CD playback, clamp the
  `previous` target at zero and use `stop; play 0` to restart
  track 1; retain existing `stop; play {adjacentIndex}` for middle
  tracks. Last-track Next continues to use native MPD Next and stop.
  Non-CD controls, unusual modes and paused skip behavior are unchanged.
- `b4743a11`: source/model regression tests assert first-track
  `stop; play 0`, unchanged resume-playlist snapshot, track stays
  1, elapsed resets to 0, and last-track Next still stops.
  GitHub CI status must be checked independently; these tests are
  not physical CD acceptance.

**Status of #1:** The original middle-track latency issue remains
resolved on the Pi (12–16ms with audible user acceptance).
**Boundary sub-issue reopened** for first-track Previous. The proposed
source fix awaits user-authorized Pi installation and physical
acceptance; do not claim that the on-device error is repaired yet.
Last-track Next hardware result: PASS / stops. Items #2–9 remain
open and untouched. No firmware, build, installer, package, or
deployment done by this source/documentation update.

## Cleanup item 1 — final field acceptance (2026-11-13)

**User-confirmed physical outcome:** After applying the surgical
track-1 Previous fix on the Pi, the user reported **"confirmed fixed"**.
This follows the observed `ACK [5@0] {previous} Decoder failed to seek`
error and installation instructions that replace the first-track
target calculation with a clamp to zero, allowing `stop; play 0`.

**Status — #1 CLOSED for its agreed acceptance scope.**
- Active middle-track Next/Previous: user-heard correct transitions;
  post-fix API latency 0.011574s Next / 0.016043s Previous, HTTP 200.
- First-track Previous: **user-confirmed fixed on the Pi** after patch;
  no further failed-seek report in that confirmation. Exact new
  latency or Pi source checksum was not supplied.
- Last-track Next: **user-confirmed stops playback** without wrap.
- GitHub source change: `4f094365`, tests update `b4743a11`;
  GitHub `Test house-audio-server` run for `b4743a11`
  completed **success**, as did later documentation commit
  `36136f1`. The initial source-only run `4f094365` failed with
  the old boundary expectation still present; superseded by updated
  tests, not to be represented as a clean run.

**Limits:** These are user-reported field observations and passing
GitHub model tests, not an independent inspection of the live Pi's
on-disk file or a sample-level audio latency measurement. Unusual
paused/mode behavior and unattended full-disc transitions remain
separate acceptance work. PR #20 is still draft and no default-main
merge, release or package was authorized. Items #2–9 remain
OPEN/UNTOUCHED. Proceed to #2 only as the next separate discussion.

## Cleanup item 4 — user experience checkpoint (2026-11-13)

**User response:** When offered item #4 (unattended full-disc playback
and automatic track-to-track transitions), the user stated
**"4 seems fine. never noticed a problem there"**.

**Disposition:** Item #4 is **not currently a user-observed defect**.
Defer to monitoring and move along the cleanup list rather than
disrupting a working system for an artificial full-disc endurance
test. User's experience is favorable, but it is **not** evidence that
a controlled, uninterrupted full-disc hardware acceptance test was
performed; do not mark that separate test as passed.

**Historical context retained:** Earlier pre-3.3 work recorded WAV
stream stalls, intermittent silence, MPD elapsed-time stalling/looping,
gateway 502s and timeouts. Their precise resolution under the current
3.4 firmware plus Pi modifications has not been instrumented over an
entire disc. The most recent user report does not establish that those
old failures still occur. Keep a watchpoint only if symptoms recur.

**Next scope:** #5, the historical gateway 502/ESP32 reset and long
READ CD failures, should first ask whether the user has seen these
since current improvements, instead of assuming ongoing failures
or changing timing settings without evidence. No source changes,
tests, builds, releases, OTA, or Pi deployments under #4.

## Cleanup item 4 — two automatic CD track transitions field PASS (2026-11-13)

**User-supplied Pi terminal evidence** from a read-only Python
observer polling `http://127.0.0.1:8787/state` every five seconds:

```text
Monitoring automatic CD playback...
Waiting for two track transitions.

Track 6 playing
Track 7 playing
Automatic transition #1: 6 -> 7
Track 8 playing
Automatic transition #2: 7 -> 8

PASS: Two automatic track transitions observed.
Transitions observed: 2
```

The script watched `source.input.trackNumber`,
`mpd.transport`, `mpd.elapsedSeconds` and `mpd.error`
without sending any transport controls. It exited successfully
after two advancing track-number transitions. Its exit conditions
would report an MPD error, paused/stopped CD, source departure or
an observed lack of elapsed-time progress lasting over 30 seconds.
None were reported in the user's output.

**Verified scope from user output:** Automatic transition from
track 6 to 7 and 7 to 8; no error or stalled-playback condition
reported by this poller in its observed window. This validates
two contiguous track boundaries, not an entire disc, sample-perfect
uninterrupted audio, or absence of gateway 502s that might recover
within the polling interval. No logs or packet captures were
collected in this step.

**Disposition:** #4's ordinary automatic track-transition behavior
**FIELD PASS / accepted for two boundaries** and no current
user-observed issue; retain whole-disc endurance as an optional,
unperformed extended test rather than a presumed failure.
Update earlier 'monitoring only' disposition accordingly.
Proceed sequentially to item #5, with first question whether
the historical gateway 502/USB timeout errors have recurred on
current 3.4 firmware. Do not change hardware/firmware or server
based solely on historical errors.

No code edit, build, package, release, hardware installation or
transport command performed in this documentation-only checkpoint.

## Cleanup item 5 — suspected late-disc audio dropout, filtered journal snapshot (2026-11-13)

**User-reported symptom:** After automatic CD tracks 6 -> 7 -> 8
passed the five-second state poller, the user thought they heard
a short audio dropout resembling buffering/connection trouble near
the end of the CD. Exact time/track/duration not separately
established. Do not continue asserting uninterrupted audio or
whole-disc acceptance on the basis of the earlier state poller.

**User-supplied journal capture:** Pi command queried the
`house-audio-input-gateway`, `house-audio-server`, `mpd`
and `snapserver` services over a two-hour period, then grepped
for errors and applied `tail -n 150`. The *entire returned set*
of 150 journal lines, 2026-11-13 22:17:12 through 22:19:48 -05:00,
consisted of repeated Snapserver
`(ControlSessionTCP) Error while reading from control socket: End of file`.
The original broad filter matched `Error`, and the
`tail -n 150` likely displaced earlier MPD / CD gateway entries.
The repeated control-session EOF alone is **not evidence**
of an MPD stream underrun, CD read error, gateway 502, or actual
audio-client disconnect. Conversely, none of those can be ruled
out because the original command truncated the filtered output.

**Next minimum-effort, read-only check:** Re-run
`journalctl` over fixed local window
`2026-11-13 22:00:00` to `22:20:00` for gateway, MPD,
Snapserver; remove this specific control-socket EOF line first,
and then inspect remaining error/decoder/audio-WAV lines with
no post-filter tail truncation. Correlate any observed warnings
with track/time before changing Pi/ESP code, timeout or audio
buffers. If zero relevant records exist, obtain direct short
reproduction and timestamped monitoring rather than invent
a cause.

**Status:** #4 automatic track number progression field PASS,
but uninterrupted audio quality is **not established**; #5
audio-dropout/502/stream continuity investigation OPEN.
No code edits, builds, releases, deployments, or service
setting changes authorized or made under this diagnostic.

## Cleanup item 5 — filtered journal correlation near track 13 (2026-11-13)

**User-supplied second, more focused Pi journal output** queried
`house-audio-input-gateway.service`, `mpd.service`, and
`snapserver.service` in 22:00–22:20 local, excluded the repeated
`ControlSessionTCP ... End of file` lines, and searched relevant
errors and gateway CD GET entries. Specific results:

- Gateway `GET /audio/cd-7452ed8dbd44/10.wav` 200 at
  22:02:19; `/11.wav` 200 at 22:04:11;
  `/12.wav` 200 at 22:09:05; `/13.wav` 200
  at 22:11:14.
- MPD `player: played` messages for tracks 8 at 22:00:04,
  9 at 22:02:56, 10 at 22:04:53, 11 at 22:09:42,
  12 at 22:11:53, and 13 at 22:16:17.
- Snapserver `(ControlSessionTCP) Error while reading from control
  socket: Connection reset by peer`, followed by
  `Error in socket shutdown: Transport endpoint is not connected`,
  occurred at 22:11:51 and 22:16:23.
- No `CD stream failed`, `CD stream ended early`,
  HTTP 502 or MPD decoder failure in this *filtered output*.
  This does NOT independently prove complete WAV body receipt:
  gateway HTTP 200 is sent before the whole stream completes,
  and filtering omits lines not matching the pattern.

**Interpretation:** The Snapserver control-session resets are
temporally near end of track 12 and six seconds after MPD logged
completion of track 13. They identify a *control-socket* error,
not a confirmed PCM stream underrun or render-client dropout.
No direct evidence identifies the cause of the user-heard audio
glitch. A plausible connection between resets and audible glitch
needs stronger timestamped evidence, not an assumed causal link.

**Next smallest read-only check:** get complete (unfiltered except
repetitive control-session EOF) logs for 22:15:30–22:16:40 from
gateway, MPD and Snapserver. Check whether any renderer stream
client, MPD output, or gateway WAV message coincides with reported
dropout, and whether 22:16:23 followed normal last-track end.
No settings change, code modification, compilation, release or
deployment authorized from these findings.

## Cleanup item 5 — end-of-disc full journal and Snapcast monitor correlation (2026-11-13)

**User-supplied complete (except recurring control-session EOF) Pi logs**
for local 22:15:30–22:16:40, covering the last seconds of CD track 13:

- At `22:16:17`, MPD reported
  `player: played ".../13.wav"` and Snapserver reported
  `(AsioStream) No data since 120 ms, switching to idle`,
  `(PcmStream) State changed: default, state: playing => idle`,
  and `(Server) onStateChanged (default): idle`.
  These simultaneous events **are consistent with normal disc-end
  source cessation**. They do not establish a dropout before the
  end of track 13. At `22:16:23` a Snapserver
  `ControlSessionTCP` socket reported a peer reset and socket
  shutdown error, *after* the source entered idle.
- Throughout 22:15:30–22:16:40 there are approximately
  one-second-spaced Snapserver control connections from
  `::ffff:127.0.0.1`, with old sessions being removed.
  Independent GitHub **source inspection** of
  `house_audio_server.py` `SnapcastMonitor` establishes that
  the House Music service defaults
  `SNAPSERVER_POLL_SECONDS=1.0` and each
  `_fetch_status()` calls `socket.create_connection`
  to `SNAPSERVER_CONTROL_PORT=1705`, sends
  `Server.GetStatus`, then closes at the end of the scope.
  This matches the observed pattern and explains why frequent
  localhost control-session connects and EOFs are expected.
  It is not evidence of a renderer's audio-stream failure.
- An additional control connection from `192.168.1.136`
  at 22:16:12 appears, but its identity and relationship to
  audio are not established.
- ESP32 `192.168.1.175` sends successful
  `POST /register` gateway heartbeats every five seconds
  while the Pi polls `GET /sources` regularly. No relevant
  CD data-stream failure appears in this provided excerpt.

**Current diagnostic interpretation:** The 22:16:17 switch to idle
appears normal for completion of track 13; 22:16:23
control-socket reset should **not** be labeled as the user's
heard audio dropout. The actual transient dropout, if during
an earlier track, has no established timestamp or matching
decoder/gateway/Snapcast renderer-stream failure from this log.
Do not claim issue resolved or blame Snapcast control polling
for audio dropouts on the current evidence.

**Next smallest diagnostic distinction:** Determine whether the
audible glitch affected *all simultaneous renderer zones* or
just one speaker/node. All zones suggests a common path
(CD/USB/Wi-Fi/gateway/MPD/Snapserver source); one zone suggests
a node/network/render buffer issue. If the user cannot tell,
record a specific timestamp and zone on reproduction, then
narrow the corresponding logs without altering the runtime.

**Status:** Item #5 open; #4's two transitions still passed, but
uninterrupted whole-disc playback not verified. No settings,
source code, build, package, firmware release or deployment
changed in this checkpoint.

## Cleanup item 5 — user correction: audible glitches on tracks 12–13, multiple zones (2026-11-13)

**Correction from user (authoritative eyewitness report):** The
dropouts were perceived on **both speakers**, while the CD was
**still playing, on at least tracks 12 and 13**. The previous
investigation wrongly concentrated on the end-of-track-13
`22:16:17 playing => idle` and subsequent
`22:16:23` Snapserver control-socket reset. Those events
cannot explain glitches the user heard earlier during audio.
Do **not** frame the user report as end-of-disc silence or
as an isolated renderer fault.

**Existing timestamp evidence reinterpreted:** For the 13-track
disc, gateway reported `/12.wav` HTTP 200 at 22:09:05,
`/13.wav` HTTP 200 at 22:11:14; MPD logged finished
`/11.wav` at 22:09:42, finished `/12.wav` at
22:11:53, finished `/13.wav` at 22:16:17.
A Snapserver `ControlSessionTCP: Connection reset by peer`
was logged at **22:11:51**, two seconds before the MPD
track-12 completion, and another at 22:16:23 after the disc
ended. These are *control-socket* messages and are not
proof of common audio failure. User did not give an exact
glitch timestamp.

**Diagnostic priority:** Audible dropouts on two concurrent
speakers make the shared CD/USB/Wi-Fi -> gateway -> MPD
-> Snapserver PCM-input path more plausible than a failure
isolated to one speaker, without ruling out common
distribution issues. The previous 5s state monitor detected
track-number progression only; it cannot detect short
PCM starvation, MPD pauses shorter than the poll interval,
or renderer buffering.

**Next read-only evidence:** inspect *unfiltered non-control*
journals from `22:09:00` through `22:16:16`
for MPD, CD gateway and Snapserver, explicitly retaining
`(AsioStream) No data since...`, `(PcmStream) State changed...`
and other PCM stream warnings, while excluding spammy
`ControlServer`, `ControlSessionTCP`,
`GET /sources`, `POST /register` events. Those PCM
state events were not captured by the previous error-only
grep, so the lack of an earlier reported match cannot
rule out source starvation. Do not change buffering or
firmware without evidence. All changes in this
checkpoint are documentation only.

## Cleanup item 5 — real PCM feed idle gaps at transitions into tracks 12 and 13 (2026-11-13)

**Newly provided user Pi journal, exact chronology (source-derived):**

- 22:09:05 `GET /audio/.../12.wav HTTP/1.1` 200
- 22:09:40 Snapserver `(PcmStream) State changed: default, state: playing => idle` and Server `default: idle`
- 22:09:42 MPD `player: played ".../11.wav"`, Snapserver `(PcmStream) idle => playing`
- 22:11:14 `GET /audio/.../13.wav HTTP/1.1` 200
- 22:11:51 Snapserver `(PcmStream) playing => idle` and Server `default: idle`
- 22:11:53 MPD `player: played ".../12.wav"`, Snapserver `(PcmStream) idle => playing`

**Verified by the user-supplied journal (not direct hardware
inspection):** The shared Snapserver *PCM stream* entered idle for
approximately two seconds at *both* boundaries 11 -> 12 and
12 -> 13. This is much stronger evidence relevant to the two
speakers' reported dropout than the separate Snapserver
control-session socket resets. Both actual stream-idle
intervals begin two seconds before MPD's `played` log for
the outgoing track; the logs do not show a CD gateway
`502` or truncation error, although the gateway HTTP 200
is only header acceptance, not WAV body timing/proof.

**Hypothesis, explicitly unverified:** The audible glitch on
both speakers could be these approximately 2s shared PCM
input gaps at automatic track boundaries rather than an
individual renderer Wi-Fi issue. We have NOT established
that these boundaries account for every within-track
glitch the user heard on tracks 12/13, or identified the
cause of MPD's missing output (CD read, prefetch, decode,
gapless/PCM handoff, etc.). Do not claim the root cause
fixed or that two-second PCM idle is normal/acceptable.

**Minimum-effort next read-only test:** Compare PCM playing/idle
transitions across earlier track boundaries on the same
disc using `journalctl` from ~21:35 to 22:16 and only
`PcmStream State changed`, `AsioStream No data`,
and `mpd player: played`. If gaps recur
systematically, focus on MPD -> Snapserver handoff
at every HTTP WAV boundary, not just final tracks;
if only late tracks, investigate CD/wireless/gateway
timing near disc end. Keep all current runtime
untouched until evidence guides a targeted change.

**Scope:** #4 state-poller transition count PASS but continuous
PCM/audio across boundaries FAIL for this captured period.
#5 dropout investigation OPEN. Source documentation checkpoint
only; no build, packaging, deployment or firmware changes.

## Cleanup item 5 — correction: dropouts are within songs, not at boundaries (2026-11-13)

**User's explicit correction and stronger direct observation:**
The audible glitches on both speakers happened **during** tracks
12 and 13, with **one occurring within the final 30 seconds of
track 13**. The prior assistant incorrectly attributed the
symptom to the approximately two-second PCM idle/playing changes
at boundaries (11->12, 12->13); user explicitly says those
are NOT the dropouts they heard. Do not revive this mistaken
attribution without new evidence.

**New user-supplied whole-session log:** MPD `player: played`
and Snapserver `PcmStream playing => idle => playing` events
appear at many earlier track boundaries, including library MP3s
and CD tracks 5 through 13. Thus these boundary state events
are **not specific to the problematic late CD tracks**. The
report of audible mid-song interruptions is separate from the
observed track-boundary gaps. They may be additional usability
problems, but they do not explain the user's described symptom.

At the end of track 13, MPD `player: played ".../13.wav"`
and Snapserver `playing => idle` both occur at **22:16:17**.
If the reported event was in the last thirty seconds as stated,
the relevant historical interval is approximately
**22:15:47–22:16:17**. The previously supplied complete journal
for 22:15:30–22:16:40 has control-session polling and
periodic successful S3 `POST /register`, but no PCM state
change or CD/MPD error **during** that last half-minute.
This absence does not disprove a brief network/data
starvation: neither the five-second API poller nor
state-change journal is a byte-timing monitor.

**Evidence-based next step:** Instrument read-only network
packet timing of S3 HTTP WAV transfer `192.168.1.175:8080`
on the Pi while replaying track 13. Packet timestamps can
detect unusual transfer gaps/retransmits and be aligned to
the heard glitch, distinguishing upstream transport issues
from downstream MPD/Snapcast/client faults. A gap alone would
not establish audible dropout, and a complete packet stream
does not prove audio samples were correct. No firmware,
server code, settings, installs, releases or build changes
are authorized or made by this checkpoint. #5 remains open.

## Cleanup item 5 — corrected listening chronology and renderer attribution (2026-11-13)

**Clarification from user about the actual listening session (strongest
current eyewitness account):**

- User has listened to approximately **5–6 CDs** with the basement
  stereo, Motorola, and Emerson radio nodes, with **no previously
  noticed mid-song dropouts**.
- This was the **first CD playback through the music-room node**,
  with the basement stereo concurrently on but quieter.
- User clearly heard repeated dropout/recovery on **the music-room
  system upstairs**. They could **not reliably tell** whether the
  quieter basement stereo dropped out at the same time.
- User **turned off the music-room node**, went downstairs, and
  **thought they heard** an interruption on the basement stereo
  during the **last ~30 seconds of track 13**.
- Therefore **simultaneous dropout on both zones is NOT verified**.
  The historical notes above that characterize this as a confirmed
  both-speakers event or strongly classify it as a shared-source
  failure are superseded by this clarification.

**Other evidence remains distinct:** Earlier journal shows normal
or at least recurring PCM `playing => idle => playing` transitions
at track boundaries for library MP3s as well as CD songs. These
state changes occur separately from the clearly reported
*mid-song* upstairs glitches, and do not diagnose the cause.
No gateway `502` or CD-read truncation was established during the
reported glitches; lack of errors does not prove clean data.
The speculative late-track basement glitch has no precise timestamp
or simultaneous upstairs corroboration (upstairs node was off).

**Current possibilities (not demonstrated causes):**

1. Music-room ESP32/Snapcast renderer, local network or node audio
   path — *newly exercised listening node* is the clearest variable;
   an individual client glitch may not change Snapserver's shared
   PCM source status.
2. Intermittent shared CD/USB/ESP32 Wi-Fi -> gateway -> MPD
   -> Snapserver source disruption — plausible if the tentative
   basement symptom was real, but simultaneous two-zone playback
   loss **not established**.
3. Disc-specific read irregularity or infrequent stochastic
   failure (many other CDs were reportedly fine) remains possible.

**Decision:** Do not label a cause, make transport changes,
or insist on S3 packet capture as the *first* step based on
these observations alone. For an efficient discriminator,
repeat a late track with the two zones separately, then
together if needed; obtain a concrete timestamp and identify
which system actually stutters. The user is discussing details
rather than authorizing any build or installation.

**Status:** #5 audio dropout investigation OPEN; no changes to
firmware, server behavior, build, package, deployment or
live Pi configuration by this documentation checkpoint.

## Cleanup item 5 — live track-11 rerun, independent Bose/downstairs dropouts (2026-11-13)

**User's live hardware observation:** While replaying **track 11**,
the **Bose subwoofer renderer and downstairs stereo renderer**
both intermittently dropped out, **but not at the same times**.
This is stronger than the earlier uncertain basement report,
and disproves the prior assumption of exactly simultaneous
dropouts on all listening nodes. The dropouts were not described
as track-boundary silence.

**Interpretation (hypotheses, not established causes):**
Independent client/render network or buffering trouble
is plausible; a common CD/ESP32/gateway/MPD input stall
can also trigger client dropouts at distinct times if local
buffers or playout differ, so the source path is not
exonerated. Scratched-disc hypothesis is less directly
supported by the noncoincident symptoms; no physical disc
inspection or repeatability-at-sector measurement was made.
A passing presence/status poll is insufficient to prove
continuous audio samples at a renderer.

**Verified from current House Audio server source:** the
`/renderers`/SnapcastMonitor snapshot represents per-client
connected, present, audible, lastSeenAgeSeconds, latencyMs,
host/IP, group and stream; it is not a per-client audio
underrun metric. No grounds to call a disconnected node
without its own timestamped status.

**Next minimal discriminating comparison:** leave Bose sub and
basement stereo active and play ordinary locally stored
MP3 through the same Snapcast chain. If dropouts recur
outside CD source, investigate renderer/network/Snapcast
distribution. If local MP3 is stable while the CD repeats
the issue, prioritize CD/ESP32 USB/network/gateway/decoder
path. A single clean run cannot rule out intermittent failure.
Do not modify firmware, buffers or systemd settings yet.

No implementation changes, tests, releases, packages or
deployments made by this documentation checkpoint.

## Cleanup item 5 — mixed simultaneous/non-simultaneous dropout and MP3 A/B underway (2026-11-13)

**User correction:** During the track-11 replay, the Bose sub and
downstairs stereo dropped out **sometimes simultaneously and
sometimes at different times**. The preceding checkpoint's
statement that they did not drop out together was incomplete,
not a verified absence. Both shared-source interruptions and
individual client/network/buffer effects remain viable; cannot
identify cause from subjective timing alone.

**A/B test in progress:** User switched to locally stored MP3s with
the same listening nodes. At the time of report, only about
half of the first MP3 had played and **no dropout yet noticed**.
This is not a completed clean MP3 test or proof that CD path
is at fault. Continue listening through several tracks
without changing nodes/settings. If MP3 playback remains
clean while CD consistently stutters, target the CD input;
if MP3s also glitch, prioritize the shared distribution and
individual Snapcast clients. Audio log messages during
transition boundaries are separate from the reported in-song
events. No changes to firmware, Pi code, installations,
builds, packages or settings are authorized by this checkpoint.

## Cleanup item 5 — MP3 playback also fails; browser timer can coast (2026-11-13)

**New user-observed regression:** During the MP3 A/B comparison,
playback dropped out during the **second locally stored MP3**,
after approximately half of the first MP3 had played normally.
At the time of report the user heard **no audio** while the
House Music browser timer appeared to keep advancing.
User reports this is a **very recent development**.
The number of affected renderer zones at the exact moment of
complete silence has not been explicitly identified.

**Consequence:** The newly reproduced defect is **not unique
to the Lite-On CD/ESP32 transport**. That transport cannot be
the sole explanation for the MP3 dropout; a shared MPD output,
Snapserver input/distribution, renderer/presence control or
other common failure is now a leading diagnostic direction,
without claiming which component is at fault. It is possible
that the earlier CD observation and the MP3 observation have
different causes; don't prematurely merge them.

**Verified by direct GitHub source inspection:** The
`web/house.js` `tickClock()` function extrapolates the
displayed `elapsed` from the most recent polled MPD
`elapsedSeconds` using `Date.now() - stateReceivedAt`
(capped at 10 seconds), when the last known
`mpd.transport` equals `play`. Therefore **an advancing
browser timer alone does not demonstrate a live MPD PCM
output** or even an exact real-time MPD elapsed increase.
The current `house_audio_server.py` `/state` directly
queries MPD `status`/`currentsong` and `/renderers`
returns per-client state from the SnapcastMonitor snapshot.

**Next read-only live-failure test suggested:** While audio is
missing, query `/state` and `/renderers` three times
three seconds apart, printing MPD transport/elapsed/error,
current source, Snapserver reachable/streams and each
renderer connected/audible/lastSeen/IP. Also print
`mpc -h 127.0.0.1 -p 6600 outputs` to check active
MPD output. Do **not** restart services or change
buffering/presence while failure is observable.
Capture user output and compare with an audible state to
localize the break.

No runtime changes, code edits, firmware builds,
packaging or deployments have been authorized or
performed in this documentation checkpoint.

## Cleanup item 5 — MP3 no-sound live state: MPD progress and Snapserver active (2026-11-13)

**User-supplied three consecutive read-only Pi state samples
(23:45:24, :27, :30 local):**

- House Music `/state` reported source `library`, MPD
  `transport=play`, `error=None`, and **actual** MPD
  `elapsedSeconds` 166.073 -> 169.070 -> 172.088.
  Thus unlike the browser-side extrapolation, the
  MPD status itself advanced about six seconds.
- `/renderers` reported `snapserver.reachable=True`,
  default stream `status=playing`, URI
  `pipe:///tmp/snapfifo?chunk_ms=20&codec=flac&name=default&sampleformat=48000:16:2`.
- Basement stereo: connected/audible True, `lastSeen`
  ages 0.745 -> 0.808 -> 0.854 s; IP `192.168.1.117`.
  Bose Sub: connected/audible True, lastSeen
  0.053 -> 0.111 -> 0.196 s; IP `192.168.1.110`.
  A phone `192.168.1.136` and Rio sub
  `192.168.1.129` likewise reported current
  connected/audible. Music-room stereo `192.168.1.100`
  reported disconnected and not audible.
- `mpc outputs`: `Output 1 (Snapcast) is enabled`.

**Interpretation:** MPD clock is advancing and the output is
enabled; Snapserver knows a playing stream and has recent
client presence, but **none of these data establish active,
non-silent PCM through the pipe or actual audible output
at the nodes**. House Music sets client `audible` based on
fresh connected/present/unmuted status in
`house_audio_server.py`, not detected acoustic audio.
The user reported silent MP3 playback at this time, but
whether every listed active node was silent throughout
these exact samples has not been independently confirmed.
CD transport is not required for this MP3 symptom, so
do not claim a scratch or CD-specific hardware fault
caused the MP3 failure.

**Next discriminator:** verify which specific active
nodes are silent at the same instant, and if reproduced
compare Snapserver audio payload/data delivery to the
renderer node while MPD remains in play. No settings,
firmware, builds, package, deployment, or changes made.

## Cleanup item 5 — stereo + Bose completely silent while MPD plays; phone comparison (2026-11-13)

**User clarification of 23:45:24–23:45:30 state samples:** The
basement stereo and Bose sub were **both actually silent at
the time those samples were taken**. Sound **returned
spontaneously immediately afterward** (no manual recovery
reported). This completes the previous unresolved observation
about whether those particular nodes were silent
contemporaneously with advancing MPD elapsed and active
Snapserver statuses.

**Additional user observation:** Android phone Snapcast app
**does produce audible music if unmuted**. It is **not
yet established** whether the phone was actually heard
during the same exact seconds of joint hardware-node silence
versus after they recovered. Do not misrepresent this as
proof of continuously valid source PCM during the outage.

**GitHub-grounded status semantics:** House Music
`SnapcastMonitor._apply_status` defines `audible` as
connected AND lastSeen-fresh AND not client-muted AND not
group-muted; this reports presence/muting, not audio amplitude,
decoder buffer health or acoustic sound. Thus both users'
silent-but-`audible=True` observations are internally
consistent with monitor implementation.

**Diagnostic priority:** Have phone app unmuted as a reference
during any next dropout of the same stereo and Bose, and
note whether it too stops at the same time. If phone continues
with audio, focus on shared characteristics of hardware
renderers / network delivery rather than MPD or CD source.
If phone also stops, focus upstream of Snapcast distribution
and shared stream/audio path. This is a hypothesis
discriminator; neither outcome alone proves exact cause.
No new setup, code, build, install or firmware actions were
requested or performed.

## Cleanup item 5 — simultaneous phone audio validates common source during renderer silence (2026-11-13)

**Decisive user-perceived comparison:** The **phone's Snapcast app
remains audibly playing through the same observed dropouts** that
silence the physical hardware nodes (basement stereo and Bose
sub). This is stronger than the previous "phone can play when
unmuted" statement: the sound is actually concurrent.

**Interpretation with limits:** A functioning phone renderer
means Snapserver is distributing usable audio to at least one
client *during* hardware-node silence, greatly weakening
hypotheses of a general CD read failure, MPD source pause,
or total Snapserver source outage as the sole cause.
It does NOT fully distinguish the hardware clients' Wi-Fi
delivery, Snapcast receiver time-sync/buffer/decoder, I2S DAC,
or analog output causes. Differing buffering can mask
some brief upstream glitches, but the observed combination
makes client-specific paths the priority.

**Existing authoritative history now connected:** On
`house-audio-esp32` main,
[Issue #3](https://github.com/oolah10293/house-audio-esp32/issues/3)
was opened 2026-09-28 and remains open, describing
few-second, sometimes single-renderer silence with
spontaneous recovery on XIAO S3 + PCM5102A nodes. The
`house-audio-server` README and ESP32 README explicitly
track this unresolved issue. The earlier physical observations
are similar but DO NOT prove this event has identical cause.
Issue #3 records a proposed multi-second shared buffering
experiment; `docs/HOUSE_BUFFER_TRIAL.md` states the 3000-ms
trial was **prepared but not applied** in that recorded test.
Do not claim this trial or any current live firmware revision
has been verified in this session.

**Present diagnostic priority:** Catch the hardware-renderer
event in its ESPHome/Snapclient client-side logging, focusing
on Wi-Fi TCP delays/reconnect, Snapclient buffer underrun/
time-sync, FLAC decode/I2S path. Unlike the old v0.6.0
README, the current server README states its historical
`/diagnostics` endpoint is absent in current source; do
not prescribe that retired endpoint as if live. The Pi
`/renderers` connected/present/unmuted snapshot was already
seen healthy during silence and is insufficient by itself.

No firmware/software/builds/packages/runtime settings changed
or authorized; note-only checkpoint. No root cause has been
established.

## Cleanup item 5 — exact read-only ESPHome renderer log capture instructions (2026-11-13)

**Confirmed next method (not yet performed on user's hardware):**
For the existing Windows-hosted ESPHome dashboard/Device Builder,
select affected Bose Sub node -> **Logs**, choose **Wireless**
if supported. ESPHome's current official CLI docs confirm
`esphome logs <actual-node-yaml> --device 192.168.1.110`
for network logs *when the deployed device provides an ESPHome
native API, MQTT logging, or supported web_server logging*.
The GitHub `house-audio-esp32/main`
`firmware/bose-subwoofer-prototype.yaml` is only a prototype
and not proof of deployed configuration; it specifies
`logger: level: DEBUG; hardware_uart: USB_SERIAL_JTAG`
but **does not include `api:`, `mqtt:`, or
`web_server:`**. Under that exact prototype config,
wireless ESPHome log access is unavailable; USB serial via
a Windows COM port and `esphome logs <actual-node-yaml>
--device COM<number>` is the documented fallback.
Check Windows Device Manager for the actual assigned port;
do not assume COM3. Connecting serial can sometimes reset
a board; avoid disturbing separate power wiring.

The first log capture should cover an actual audible dropout,
with time, node ID, any Wi-Fi/TCP reconnect, Snapclient FLAC
decoder/buffer underrun, or I2S warnings. Capture a
comparison against phone stream if useful. `esphome logs`
only reads current firmware logs; it does not compile/upload.
Don't run `esphome run`, `compile`, `upload` or
`install` from the log workflow.

**Status:** The phone's audible uninterrupted stream while
Bose and basement stereo mute is user-verified in this session;
renderer-side failure mechanism remains unverified. Existing
`house-audio-esp32` Issue #3 remains open. This entry is
documentation-only; no source, firmware or live settings changed.

## Renderer dropout — AP affinity test (2026-11-14)

**User-reported live experiment:** User moved the affected renderer nodes to a different Wi-Fi access point and locked them to that AP. They are now observing whether spontaneous audio dropouts continue. The exact AP identity, signal measurements, and any result are not yet known. The earlier confirmed phone Snapcast app remained audible through physical-node audio loss; basement stereo and Bose sub could lose audio while MPD and Snapserver status remained playing. **Do not claim AP steering/roaming or RF interference caused the fault** until the new AP test has sufficient playback evidence. Keep other variables unchanged; compare MP3 and CD listening as practical. This is a network configuration trial performed by the user, not a firmware/software build or deployment by the assistant.

## Renderer dropout — AP-lock trial tracks 11–13 clean (2026-11-14)

**User-reported result:** After moving the affected hardware renderer nodes to a different access point and locking them to it, the user played **CD tracks 11 through 13 without noticing any audio dropouts**. Before this AP change, they heard repeated physical-node dropouts during CD and local MP3 playback, while the phone Snapcast app stayed audible during the node interruptions. This test supports investigating Wi-Fi AP conditions/steering as a possibility. It does NOT prove the original AP or roaming caused the issue because only one post-change listening run is reported and no RF/packet measurements are available.

**Current decision:** Preserve the new AP assignment and all other playback/firmware settings; continue listening for recurrence before attempting fixes. No software or firmware changes, build, release, packaging or deployment authorized or performed.

## Renderer dropout — Omada Guest room AP retry graph (2026-11-14)

**User-provided Omada screenshot:** Guest room AP (EAP653 (US) v1.0)
Statistics -> Retries, 1D chart, filter currently `All`.
Shows bursty **Tx Retries** (green) and **Rx Retries** (blue)
with graph axis topping at about **2.44e4** and multiple large
peaks, especially in a morning/late-morning period.
The axis/aggregation interval and exact timestamps/absolute
counts during the reported audio dropouts have **not** been
independently established. `All` is not a confirmed 2.4 GHz
only view, and the chart does not identify affected client(s).
The screenshot does not include a retry percentage or packet
denominator for a matching time interval, so large raw retry
counts alone do not establish a bad retry rate or root cause.
A link-layer packet retransmitted successfully is compatible
with previously observed `Dropped Packets=0` and
`Errors=17`; these are different counters.

**Smallest next read-only check:** On that AP, change the
Statistics left filter from `All` to **2.4 GHz**, if available,
and inspect/hover time periods matching the actual observed
audio interruptions, or compare against the new locked AP.
Do not claim observed daily retry spikes coincide with
the dropout timestamps without correlation. Preserve new AP
assignment while observing. No builds, firmware, deployment or
config changes made by this documentation checkpoint.

## Renderer dropout — Omada retries confirmed on 2.4 GHz (2026-11-14)

**Screenshot observed:** Guest room EAP653 Omada Statistics -> Retries with the left radio selector explicitly set to `2.4 GHz` (previously `All`), 1D view. Tx retries green and Rx retries blue still show significant bursts, especially around ~09:30–noon and ~18:30–19:30 in the graph's displayed local scale, and smaller evening activity. Therefore bursts occur on the 2.4-GHz radio, not exclusively due to the 5-GHz radio. The chart displays raw retry count by interval, not a percentage, does not identify individual clients, and has not been matched to timestamps of user-observed audio interruptions. Do not infer saturated bandwidth, specific AP fault, or causal link from this alone.

**Field outcome to retain:** moving and locking the affected hardware Snapcast nodes to another AP was followed by clean CD playback through tracks 11–13; repeatability remains open. Leave the working assignment unchanged; do not modify software, firmware or AP radio settings based on this screenshot.

## Renderer dropout — new music-room Bluetooth-board timing hypothesis (2026-11-14)

**User clarified onset chronology:** Music-room stereo's new House Audio
Wi-Fi renderer initially sounded fine. That stereo also has a separate
Bluetooth receiver board located physically close to its ESP32 node.
User took out a phone to **check Bluetooth availability only**;
the board was discoverable, **but the user did not connect/pair**.
At around that check the user noticed one brief Wi-Fi audio hiccup.
Playback seemed normal for several minutes, then dropouts got
progressively worse. This timing is user-observed correlation, not
proof of Bluetooth interference.

**Hypothesis, unverified:** RF interactions between close 2.4 GHz
Bluetooth and ESP32 Wi-Fi radios could affect local receiver
performance even without an active phone audio connection.
A local BT interference explanation does not, on its own,
account for basement renderer dropouts that occurred on the
same old AP, or establish a common AP issue. Other RF/AP/client
causes remain live possibilities. The new AP assignment later
gave a clean CD 11–13 run, still only one clean run.

**Best isolation proposed, not yet performed:** keep the music room
Wi-Fi renderer operational while disabling or removing power
only from the separate Bluetooth board, then compare under the
same access-point conditions (ideally with no other variables
changed). Do not power down the whole music-room stereo if
that also kills its Wi-Fi node; that would confound the test.
Need to confirm whether Bluetooth board has independently
switchable power before giving wiring instructions.
No hardware alterations, firmware, builds, packages,
releases or deployment performed in this checkpoint.

## Renderer dropouts persist after AP move with music-room node off (2026-11-14)

**User-reported new result (morning of 2026-11-14):** Audible dropouts are still occurring **after the AP reassignment experiment**, despite the **music-room stereo/node being switched off**. Currently being used / experiencing dropouts: **basement stereo and Motorola**, in the user's own words. Thus one clean CD tracks-11-to-13 trial on the new AP was not a sustained resolution. Music-room Bluetooth receiver activity or the new music-room ESP32 being powered is not **necessary** for recurrence; it cannot be singled out as the cause. Unclear whether the basement stereo and Motorola cut out simultaneously or independently, and whether the phone renderer continued playing on this morning's occurrences; ask one focused observation before narrowing to shared or per-node causes. Do not conflate the Motorola with the Bose subwoofer or assume independent Snapclients without verification. Current AP retry bursts only establish 2.4-GHz retries, not causation. No firmware/build/config changes authorized or made.

## Renderer dropout — independent cuts clustered in time (2026-11-14)

**User-reported direct observation:** With music-room node off and after the AP-lock move, basement stereo and Motorola hardware playback are still dropping out. Cutouts on the two endpoints occur **independently**, occasionally coinciding but with no reproducible simultaneity. **Timing clusters** are the only apparent correlation. Do not describe events as consistently simultaneous or shared-stream gaps.

**Interpretation (not proven):** independent per-node network jitter / buffered Snapclient decode underruns fit asynchronous audible failures, while shared periods of RF/traffic disturbance could explain clustering. A common renderer firmware buffering/decoding fault also remains possible. Previously the phone Snapcast player stayed audible through hardware dropouts while MPD was actually advancing and Snapserver status was healthy; that prior result should not be misrepresented as verified specifically during this morning's incidents. An AP-only, music-room Bluetooth-only, or single centralized audio-source explanation is not established by evidence.

**Suggested smallest next observational check:** compare retry spikes on the **new AP**, on its 2.4-GHz radio, at the precise time of a fresh dropout cluster rather than attributing old AP's historic retry graph to the new events. If no correlation, instrumentation of one ESP32's buffer/decoder/Wi-Fi path is the next line of inquiry. No firmware or AP configuration modifications authorized; no build, package or deploy performed.

## Renderer dropout — separate APs for affected nodes (2026-11-14)

**User correction / new direct observation:** Basement stereo and Motorola are connected to **different Wi-Fi access points** while experiencing intermittently clustered but generally independent dropouts. This supersedes the preceding suggestion to inspect a single shared "new AP" as if both affected nodes were associated with it. The previously observed 2.4-GHz retry spikes on the guest-room EAP653 cannot be assumed to explain dropouts on both APs.

**Interpretation, not yet verified:** A single-AP defect or one AP being overwhelmed is now a weak sole explanation. Shared Snapcast/ESP32 decoder-buffer behavior, LAN/Snapserver delivery, or RF conditions affecting more than one AP remain candidates. Earlier confirmed phone renderer remained audibly playing during hardware-only dropouts, but no new phone comparison was reported in the latest morning observation. Do not mislabel independent interruptions as synchronized shared silence.

**Next isolation idea:** collect read-only log information or buffer underrun behavior from one confirmed live hardware ESP32 renderer while a dropout occurs, preserving all configurations; establish the current actual firmware logging/API transport before prescribing commands. No build/flash/packaging/release authorized or performed.

## Dropout follow-up — source delta and read-only TCP capture (2026-11-14)

**User's new hypothesis:** audible clustered, independently timed hardware Snapclient dropouts have become conspicuous since the CD integration; user says MPD is clearly continuing and suspects renderer playout buffer starvation. Do not prematurely rule out new Pi load or delivery changes.

**GitHub code comparison verified:** comparing pre-CD `main` (baseline through `aa5e83c`, with one later unrelated documentation-only commit) and `feature/cd-auto-takeover` shows newly added `auto_cd.py` and `input_sources.py`, modifications to `house_audio_server.py`, and checked-in `pi_input/gateway.py`. A standalone gateway already existed/was deployed before CD integrated with the main controller; the CD installer updates its service. `AutoCdSource.refresh()` contacts the loopback gateway's `/sources` at most every **3 seconds**, from the policy worker and **outside** the MPD write lock (but still on that worker); `AutoCdSource.tick()` handles event transitions under the lock. The gateway HTTP WAV proxy is used for CD playback, not ordinary local MP3 source files. Repo `config/house-audio-server.default` is byte-identical on main and this feature branch. The code diff shows no modifications to the Snapserver stream or codec configuration in the repo, although the Pi's effective installed runtime config is not independently inspected.

**Important chronology:** ESP32 repository issue [#3](https://github.com/oolah10293/house-audio-esp32/issues/3) was created 2026-09-28 describing independent intermittent few-second dropouts well **before** current CD implementation. Therefore a class of renderer dropout was historically known; its recent worsening may nonetheless coincide with new server/gateway activity, CD-related network traffic, or an unrelated change. Treat cause as unresolved.

**Capture instructions suggested, not yet run:** while audio plays, loop once per second `date -Is`, `ss -tin '( sport = :1704 )'`, and `ps -C mpd,snapserver,python3 -o pid,comm,%cpu,%mem`; redirect to `~/house-audio-dropouts.txt`, stop with Ctrl-C after several gaps, transfer file from Pi to PC (e.g. `scp` or equivalent) then attach to chat. This should help assess TCP retransmission/RTT/queue symptoms and coarse Pi process load but **does not itself measure ESP32 decoder buffer depth**. Requires interpreting read-only sample with user-reported dropout timestamps, and then instrumenting actual ESP32 only if needed. No server config changes, builds, packages, releases or flash authorized or performed.
