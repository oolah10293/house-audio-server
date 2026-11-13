# Plug-and-play CD takeover — source-only refinement (2026-11-12)

This refinement lives on branch `feature/cd-auto-takeover`, draft PR #20,
based on `feature/cd-house-playback` (installer v0.14.2-cd1). Companion
ESP32 source is `oolah10293/house-audio-esp32` branch
`feature/cd-auto-media-events`. **No updated firmware, Pi gateway,
installer, release package, or live deployment has been built/installed
for this refinement.** This is source development requested by the user;
packaging/flashing requires separate explicit authorization.

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
- To deploy later, the ESP firmware, **deployed** gateway script at
  `/home/ads-suck/gateway.py` (managed by
  `house-audio-input-gateway.service`), and House Music server must all
  be updated together. The existing installer does **not** update that
  independent gateway process.
- Existing CD installer, currently running Pi service and XIAO firmware
  have not been touched by source-only refinements.
- No real CD-to-MPD decoding/Snapcast transition or physical unplug tests
  have been demonstrated yet. GitHub unit tests validate state behavior
  only; the firmware changes need a separately authorized build and real
  Lite-On/XIAO validation.

## Regression scenarios

`tests/test_cd_auto.py` covers true boot takeover, no heartbeat restart,
eject and new disc, identical track count with new revision, original queue
and exact position restoration, manual radio/playlist overrides, audio
presence changes, suspended gateway, stopped/paused baseline restoration,
legacy firmware compatibility, and no-source fallback.

`tests/test_gateway.py` validates new session/revision registration fields.
**GitHub Actions verification:** 195 Python tests passed, Python source
compilation/syntax, installer shell syntax and browser JS regression checks
passed on commit `768ed6db6af571122fb01c13ff687334d87a9eef`, run
[43277239409](https://github.com/oolah10293/house-audio-server/actions/runs/43277239409).
The automatic CD installer creation/upload steps were **skipped**.
The new ESP32 sketch has **not** been compiled or tested on real hardware.
No physical MPD/Snapcast test has yet been performed.

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
