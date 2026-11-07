# House Audio Server v0.14.0 — House Music Web

**Source prepared for review; not deployed to the home Raspberry Pi.**
This update builds on the existing v0.13.1 source, including its pending
Resume Playlist decoder-readiness correction.

## What is new

- Browser player served from `/` or `/house` on the existing HTTP service,
  with same-origin assets `/house.css` and `/house.js`.
- Library browsing follows the MPD directory structure, including the `MP3s`,
  `Rap`, and `CDs` folders. Select a track to replace the session queue with
  the folder's immediate files, starting at that track. The Play Folder action
  uses the same set of immediate files, not all descendant subfolders.
- Shared playback controls: play, pause, stop, previous/next, seek, shuffle,
  repeat, now playing and progress.
- Inspect and reorder the existing MPD queue, including A–Z sorting with the
  current track rotated to first. `POST /queue/play` selects a native MPD queue
  ID without rebuilding or sorting the queue. An obsolete ID fails 409 rather
  than playing another track.
- Radio stations: play, add a direct stream, rename, remove, and use the
  existing Resume Playlist bookmark. Live radio correctly disables seek,
  next/previous, shuffle and repeat.
- Persisted `MP3s`/`Rap` fresh-session default selector.
- Read-only Snapcast speaker presence/status with link to the existing
  `/subwoofers` settings page. Snapcast volume and DSP attenuation are
  deliberately unchanged.
- Controller-only browser lease with no local audio renderer. Each browser tab
  reports muted/not-ready; leaving attempts detach and stale leases expire.
- Responsive desktop/mobile layout, with no JavaScript packages, external
  stylesheets, frameworks or new services.

## Changes to the Pi

The existing `install.sh` copies three additional assets into
`/opt/house-audio-server/web/` and updates `house_audio_server.py`.
It restarts only `house-audio-server`. Existing MPD, Snapserver, ESP32
firmware, private radio bookmarks, session defaults, subwoofer settings and
Android applications are not modified.

The server restart is an intentional session boundary; the current track/queue
will be discarded. A present passive radio can begin the configured new session.

## Install from a cloned checkout

The repository is public. From a clean clone or suitable checkout that has the
`feature/house-music-web` branch:

```sh
git switch feature/house-music-web
git pull --ff-only
sudo sh install.sh
curl -fsS http://127.0.0.1:8787/health
```

Expected server version: `0.14.0`. Then visit
`http://192.168.1.12:8787/` from a browser on the home LAN.
That LAN address comes from the established deployment notes, not a current
connectivity check. Do **not** assume that `~/house-audio-server` already
exists on the Pi; verify the checkout location before running these commands.

## Verification / pending acceptance

- The browser JavaScript was syntax-checked in the code environment.
- New Python regression tests exercise static routes, successful existing-queue
  selection, malformed/stale IDs, and rejection during radio.
- GitHub Actions job for PR #15 completed successfully: Python compilation and
  **138 unit tests passed**, including five new browser/queue-selection tests.
  The final source-only changes require a new job result after they are committed.
- Still requires a live browser and Pi smoke test: root page loads without
  injected URL query parameters, folder navigation, selected-song queue playback,
  now playing progress, pause/seek/skip/shuffle, queue reorder, radio station
  changes/Resume Playlist, and radio-only transport restrictions.
- No server or app deployment is claimed by the source changes alone.
