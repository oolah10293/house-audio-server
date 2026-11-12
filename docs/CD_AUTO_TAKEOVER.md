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
**GitHub Actions verification:** 194 Python tests passed, Python source
compilation/syntax, installer shell syntax and browser JS regression checks
passed on commit `af29f091cb21d25d2473d178d043f165e06e5072`, run
[43276800432](https://github.com/oolah10293/house-audio-server/actions/runs/43276800432).
The automatic CD installer creation/upload steps were **skipped**.
The new ESP32 sketch has **not** been compiled or tested on real hardware.
No physical MPD/Snapcast test has yet been performed.
