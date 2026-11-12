# CD source integration — development checkpoint (2026-11-12)

**Scope authorized:** user said “ok lets proceed” to developing CD playback through House Music, with existing synchronized Snapcast renderers and playlist return. This authorizes source development and tests; no installation, deploy, package, software release or installer build has been authorized by this turn.

**Baseline:** production `main` at `aa5e83cbd270cf737740cf0e96f3815b1b012b6d`, existing version `0.14.2`, including browser controller, radio source, and in-memory radio playlist bookmark. Work isolated in `feature/cd-house-playback`. Existing gateway runs separately on user's Pi as systemd service `house-audio-input-gateway` (port 8768), per user's supplied `active (running)` terminal output.

**User-verified hardware:** Lite-On USB CD drive, XIAO ESP32-S3 v0.2 firmware: disc 11 tracks detected; automatic SCSI drive-start recovery performed; file from track 1 downloaded via gateway and earlier v0.1 file played normally on PC. Pi audio gateway receives ESP32 registration heartbeats with HTTP 200. **Not yet verified:** MPD decoding streamed CD WAV, synchronized playback through Snapserver, source switching under real multiroom load, playlist restoration on live Pi.

## Source-only changes under review

- `input_sources.py`: read-only fixed loopback gateway catalog; never accepts a URL from a browser, constructs validated MPD WAV track URIs using local gateway only. Requires available/ready CD source and validates requested track.
- `house_audio_server.py`: extends the existing RadioPlayback ownership/bookmark path to finite CD input queues. Loads *all* available disc tracks as an ordered MPD URL queue; starts selected track; allows track-to-track Next/Previous and regular Play/Pause/Stop. No CD seeking until gateway supports HTTP range requests. CD tracks end normally without radio reconnect/retry loops.
- `GET /inputs`, `POST /inputs/play`, `POST /inputs/resume-playlist` accessible on House Music port 8787. Radio and library flows preserved. Existing `PlaylistResume` retains queue, selected occurrence, paused elapsed, Random/Repeat/Single/Consume settings, with read-only index preflight and uncertain-write precautions.
- `web/house.html`, `web/house.js`, `web/house.css`: CD players tab and track selections, CD Now Playing label, Resume Playlist button for CD.
- `install.sh`: source manifest includes `input_sources.py` for a *future explicitly authorized* install. Installer version gate remains pinned to production v0.14.2; do not run or package while development is incomplete.
- `tests/test_cd_inputs.py`: isolated gateway input checks and real HTTP handler tests backed by the existing MPD model.

## Verification and constraints

GitHub Actions PR checks are allowed to run **source syntax/unit tests only**. Workflow packaging steps are gated to the existing release branch and do not run on draft PRs. Do not merge into `main`, deploy to Pi, or package an installer without explicit user authorization.

**CI verification:** Draft PR #19, source-only GitHub Actions on 2026-11-12, ran Python syntax checks, all Python unit tests (including CD selection and gateway proxy), Node JavaScript syntax, and browser regression checks. Packaging and artifact-upload steps were **skipped**; no release was produced. The exact final commit's latest CI result must be checked separately.

**Gateway preservation:** The previously tested `pi_input/gateway.py` and `tests/test_gateway.py` from the existing `feature/audio-input-gateway` branch have been copied unchanged into this branch, so source discovery, audio proxy, and House Music integration are versioned together. The user has already installed the standalone Python gateway in their home directory and registered it as a separate systemd service; these source changes do **not** alter that deployed process.

**Integration caveats:** The XIAO currently serves one finite WAV HTTP connection at a time and lacks HTTP byte-range seeking. The UI therefore disables CD seeking. Consecutive MPD track transitions and Snapcast timing on the physical Pi must be tested: a successful browser WAV download is not equivalent to verified MPD decoding. The browser includes CD source controls; Android House Music app UI is not yet updated.

Next acceptance on Pi (only after explicit install authorization): verify MPD can play a selected HTTP WAV via gateway through Snapserver; confirm correct track order and clean transitions; verify library resume restores original queue, occurrence, position and playback modes. If MPD fails to decode WAV or stream, preserve user's current production installation and investigate with actual MPD errors rather than assuming success.

