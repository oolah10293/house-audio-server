# House Audio Server v0.14.1 — Library display, sorting, and Resume position

This update continues the v0.14.0 House Music browser controller.

## Browser Library changes

- Two selectors above the list: **Display** (Filename / Metadata) and **Sort**.
- Filename sort options: Name A–Z, Name Z–A, Modified New–Old, Modified Old–New, Track Number.
- Metadata sort options: Title A–Z, Title Z–A, Artist, Album, Track Number.
- Changing the display selection updates the Sort choices. The last manually selected sort for each display mode is retained in browser local storage; failures to access storage do not prevent playback.
- CD album folders with no explicit sort choice initially use Track Number; other folders default to Title A–Z in Metadata mode or Name A–Z in Filename mode.
- The Library list and `POST /queue/replace` both use the same sorted files. Selecting a particular track preserves its position in the sorted queue. Play Folder includes the immediate files, not descendant directories; filtering the list does not silently change the folder's playback scope.
- Track Number uses MPD track and disc tags (the API now exposes the `disc` field). For missing disc tags, leading filename prefixes like `101`, `201`, `301` can distinguish the discs. For missing track tags, the leading filename number is used when present, otherwise the filename supplies a stable fallback. Unnumbered tracks sort after numbered ones.
- **Modified** date sorting uses MPD `lastModified`, matching House Music Android's date field; unavailable dates go last. It does not claim to show filesystem creation/birth time.
- Album playback under `CDs/` switches off MPD shuffle after replacing the queue, so the configured track order is heard. MP3s/Rap do not have their shuffle mode silently changed.
- Resume Playlist button moves leftward into the marked spot in the desktop bottom bar. On mobile its existing positioning rules remain.

## Scope

Same Python service and assets as v0.14.0, no new dependencies, no new daemon, and no Android or Snapcast firmware change. The standard `install.sh` handles the server source and its browser assets.

## Verification

The automated GitHub Actions tests for this branch run Python compilation, the existing Python unittest suite, JavaScript syntax checking, and the new `tests/test_house_browse.js` browser sorting/playback regression checks. A new Python regression verifies the browser API exposes MPD disc and track metadata.

These tests verify the source and simulated playback requests. Final browser appearance on the user's actual screen and playback on the Pi require checking after installation.

## Installation

Use the versioned source ZIP provided with the GitHub Actions build, or an archive of the verified source commit for this update. Extract it into a directory on the Pi, enter that directory, and run:

```sh
sudo sh install.sh
curl -fsS http://127.0.0.1:8787/health
```

The installer copies the updated `house_audio_server.py` and `web/house.*` files and restarts the `house-audio-server` service. Restarting the control service resets the current HOUSE playback session.

Expected server version: `0.14.1`.

## First on-Pi checks

- Open Library, choose Filename and verify that file names (including prefixes) appear.
- Choose Metadata and verify that tag titles appear where available.
- Confirm Sort changes appropriately and persists separately per display mode.
- Browse an album under `CDs/`, check track ordering including multi-disc albums, and play it with shuffle off.
- Choose an individual middle album track and verify the queue order and selected starting track.
- Compare Modified New–Old and Old–New on `MP3s` or `Rap`.
- Select a radio station and check the Resume Playlist button placement without changing radio behavior.
