# House Music Server v0.14.6 — Sources webpage, Phase 1

**Status: authorized source implementation and release preparation. Pi installation
and physical acceptance are not yet verified.**

The user authorized **Phase A (Sources UI only)** on 2026-11-23.
The staged design and implementation checkpoint are in
[SOURCES_EXPANSION_PLAN_2026-11-23.md](SOURCES_EXPANSION_PLAN_2026-11-23.md).

## Change since the accepted v0.14.5 browser

- Sidebar navigation label changes from **CD Players** to **Sources**,
  retaining the existing `inputs` view and `GET /inputs` API.
- The Sources page groups **actually registered** input devices into CD
  players, Analog / Line Inputs, and Other, showing only populated groups.
  Unrecognized future kinds are visible under Other instead of disappearing.
- Selecting a CD device expands or collapses its familiar numbered track
  controls. The current-CD highlighting, track order, click action using
  `POST /inputs/play` with sourceId + trackNumber, and existing Resume
  Playlist behavior are preserved.
- Offline CD devices are identified and offer no tracks; connected CD devices
  without a readable disc are identified instead of showing fake tracks.
- Line-in and other non-CD devices are visible but **not playable**.
  Their disabled entries explicitly state that playback is not available yet.
- Limited CSS additions style source categories and expanded CD tracks in the
  existing dark-theme desktop/mobile layouts.

No changes to the Pi gateway, source registration contract, ESP32 CD node,
source auto-takeover, MPD/Snapcast audio flow, session policy or any firmware
are included. In the server program, only `SERVICE_VERSION` advances to
0.14.6 so the existing version-guarded Pi installer can distinguish releases.

## Verification and outstanding field checks

- Sources implementation GitHub PR #26 passed source syntax, full Python
  regression tests, and Node browser tests on its final commit.
- The browser test includes CD expand/collapse and unchanged track-select API
  payload, mixed CD/analog/unknown devices, unavailable CD devices, no sources,
  and all prior Library, Queue and Speakers regressions.
- The versioned installer checks that the deployed page contains the
  **Sources** navigation and grouped-device renderer, alongside existing
  byte-for-byte installed-file, health, CD gateway, Library and session
  checks. The installer has regression tests for rejecting the wrong UI.
- Tests do **not** establish actual physical Lite-On CD playback, visual
  layout acceptance, or Pi install success. These await user field feedback.

## Delivery and rollback

Package from **clean committed release source** using
`python3 tools/package_pi.py --output-dir dist`. The resulting
`HouseMusic_Server_v0.14.6_Pi.zip` includes the complete integrated
server, unchanged gateway and `install-cd.sh` backup/rollback logic.
Its `MANIFEST.json` and `SOURCE_SHA.txt` identify exact source files.

Run `sudo sh ./install-cd.sh` from the extracted
`HouseMusic_Server_v0.14.6` folder. The installer backs up the working
server/gateway, restarts their existing services, verifies deployed files and
health, and rolls back on a failed check. An installation interrupts current
playback; saved settings are preserved.

After installation, open `http://192.168.1.12:8787/`, hard-refresh if the
browser has cached old assets, open Sources, expand the Lite-On, test a track,
and verify existing automatic CD takeover/return-to-playlist still functions.
There is no need for new ESP32 firmware. Do not infer the physical system's
state from CI results alone.

**Phase B (live ADC/line-in audio playback) and Phase C (additional playback
types) are not authorized or implemented by this release.**
