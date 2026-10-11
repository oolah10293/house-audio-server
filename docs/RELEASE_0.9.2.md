# House Audio Server v0.9.2

The independent House Music and SMB Music apps no longer need the former
combined-app transfer system. The user also requested removal of the RAM
diagnostics recorder because it provided no useful benefit.

## Changes

- Removes the four `/session/handoff/*` endpoints, reservations, receipts,
  retry/failure bookkeeping, transfer-only controller lease extensions, and
  transfer hooks in ordinary playback/queue commands and passive radio startup.
- Removes the RAM event recorder, background recording loop, `/diagnostics`
  endpoint, event/stall history and four `DIAGNOSTICS_*` settings. The installer
  removes those four obsolete assignments from an existing config while
  preserving other local settings. No replacement logging feature is added.
- Retains v0.9.1 file-index refresh: manual pull-down refresh, automatic request
  coalescing across phones and scan status in the browser. Indexing sends no
  transport/queue commands; MPD may remove queue entries for deleted files.
- Preserves controller attach/heartbeat/detach, renderer ownership, live health,
  radio auto-start, mute/presence policy, retained pause, final-track drain,
  fresh-idle restart, queue sorting and MPD write serialization.

Removed endpoints now return `404 not_found`, including during startup, without
writing to MPD. Use the independent House Music app; automatic transfer from the
combined Android v0.4.x app is no longer supported. House Music v0.1.0/v0.1.1
uses the retained APIs; v0.1.1 already supports library refresh. This server
cleanup requires no new APK or ESP32/S3 firmware. The House app's local connection
log is separate from the server RAM recorder and is not changed by this release.

## Verification

- Python compilation and all **103 local tests pass**. The count includes five
  new HTTP/installer checks and excludes 27 tests for the deleted handoff and
  recorder features from the previous 125-test suite.
- HTTP tests verify all removed paths reject requests without changing music or
  blocking the next passive start, even before startup completes. Playback,
  sorting and explicit cancellation of final-track drain still work.
- Existing tests cover presence/lease expiry, muted-controller pause/resume,
  final-node drain/return, startup reset, saved settings, queue behavior, library
  refresh and the separate Country Buffer trial.
- Installer tests run the real shell script against temporary paths, with host
  account/ownership and systemctl calls stubbed. Fresh installation and repeated
  upgrade preserve local settings, config mode and service restart behavior.
- House Music v0.1.1 source calls the retained controller APIs and does not call
  the deleted server endpoints.

**Deployment update:** v0.9.2 is installed on the permanent Pi. The user's
subsequent `/state` identified version `0.9.2`, ready startup and an active
audible S3 session. On 2026-10-09, manual MPD playback of Radio Paradise was
also confirmed audible through that S3; see the [field test record](https://github.com/oolah10293/house-audio-server/issues/5#issuecomment-6093245333).
These observations do not mark every phone/radio acceptance check below as
complete. This release does not claim to resolve ESP32 audio dropouts.

## Installation

Run on the Pi when playback can be interrupted. The installer restarts only
`house-audio-server`; its established startup rule ends the previous session and
clears the old queue. A passive radio already present starts a newly shuffled
default session. Saved default-folder selection and renderer ownership persist.
MPD/Snapserver configuration and the separate audio-buffer trial are unchanged.

```sh
house_update_dir="$(mktemp -d)" &&
git clone --branch server-v0.9.2-cleanup https://github.com/oolah10293/house-audio-server.git "$house_update_dir" &&
(cd "$house_update_dir" && sudo sh install.sh) &&
curl --retry 10 --retry-connrefused --retry-delay 1 -fsS http://127.0.0.1:8787/health
```

This release is provided on the `server-v0.9.2-cleanup` review branch. The default
branch is not changed by this delivery; use the branch above to install it.

Confirm version `0.9.2` (or a later compatible release), status `ok`,
`startup.ready: true`, and reachable MPD/Snapserver. If health is initially
`degraded`, allow the startup reset to finish and repeat the health request.

## Phone and radio check

1. Power on a radio from idle: the saved default folder starts a fresh shuffle.
2. Join from House Music: it adopts the current house session. Check Play/Pause,
   Next/Previous, sorting and the existing phone mute behavior.
3. Leave only a muted phone: playback pauses and retains position. Return an
   audible output: it resumes. With everyone gone, final-track/idle rules hold.
4. Add music and pull down on the file list; after indexing, the new file appears
   without a playback skip or queue replacement.
5. Use SMB Music independently: it cannot transfer into or reset the house queue.
