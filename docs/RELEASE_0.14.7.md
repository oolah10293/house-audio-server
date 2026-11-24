# House Music Server v0.14.7 — restore previous source on CD eject

**Scope:** explicitly authorized by owner on 2026-11-24 following a
confirmed field failure with server v0.14.6. Server-side CD auto-source
policy correction only. No ESP32 firmware, gateway transport, MPD,
Snapserver, audio routing, web layout or ADC implementation changes.

## Reported failure and confirmation

The user's live Pi `GET /state` reported MPD `transport=stop`, empty
queue, `source.type=cd`, `source.status=no_disc`, automatic CD ownership
active, and action `cd_ejected_waiting_for_disc`. This left House Music
unable to play from the parked CD session. The user physically powered
off/unplugged the LiteOn ESP32-S3; the pre-CD session returned and normal
playback recovered (user field report).

## Authorized correction

When the **currently auto-owned** CD source reports an explicit
`status=no_disc`, `auto_cd.py` releases CD ownership and invokes the
same prior-source restore used by a genuine CD source disconnect. This
restores the previous radio or library position/options and original
play/pause/stop state, or stops cleanly if there was no previous program.

The old token is recorded as handled. Continued heartbeats cannot steal
back playback. A genuinely new boot session or disc revision with a ready
CD may start a new automatic CD session. Manual source selection takes
precedence. Unchanged: gateway outage protection, disconnect grace,
temporary non-`no_disc` readiness states, paused CD policy, and the
existing source/web workflow.

## Verification and remaining hardware check

- Expanded `tests/test_cd_auto.py` to cover library restore on eject,
  original position, next-disc takeover, no heartbeat retake, radio
  restore, paused library, empty previous queue, and eject-then-unplug
  idempotence. Existing disconnect/manual override suites are retained.
- GitHub CI (Python 3.13 syntax, all unit tests, shell checks and browser
  regression) must pass before merge/deployment. CI success is not
  physical device verification.
- **Not yet tested on Pi:** eject an active CD with the ESP32 still powered;
  verify that House Music returns to the previous source; insert a new
  disc and verify takeover; verify manual source selection and playback.

## Installer

The existing full Pi installer, `install-cd.sh`, is version-pinned to
0.14.7 with backup and rollback. It restarts the server and the existing
CD gateway, and verifies installed hashes, service health and pages.
Existing Pi settings are preserved. The installer restart can interrupt
live playback and reset the current queue.

`tools/package_pi.py` includes these notes in a SHA-verified ZIP.
The package is produced only after the explicitly gated release workflow
or equivalent verification from an exact committed tree. Until hardware
verification, do not label the new eject path field-proven.
