# house-audio-server v0.13.0

Prepared on 2026-10-18 after authorization to package and deploy the server
update. The user installed it on the Pi and supplied terminal output confirming
a healthy v0.13.0 response later that day. The remote execution environment did
not perform the installation itself. Live playlist restoration and physical
audio acceptance remain pending.

**2026-10-18 field failure:** the first reported House Music v0.4.0 restore
stopped at `seekcur` with `Not seekable`. The startup-readiness source correction
and its test evidence are in [PLAYLIST_RESUME.md](PLAYLIST_RESUME.md#field-failure-and-unreleased-source-fix--2026-10-18).
The correction is not a packaged or deployed release yet.

## Pi deployment evidence — 2026-10-18

The supplied terminal output shows checkout of release commit `bc2ef5f`,
completion of `install.sh`, and the service restart. The first health request
was refused; a subsequent response reported:

- `service: house-audio-server`, `version: 0.13.0`, `status: ok`
- `startup.ready: true`, `startup.lastError: null`
- MPD reachable, reporting protocol version `0.24.0`
- Snapserver reachable, server version `0.31.0`
- Two clients connected, present and reported audible: Basement stereo and Bose Sub
- Default stream status `playing`

The user reported that it "seems ok". This establishes the installation/health
checkpoint. The submitted output contains no Resume Playlist request or queue/
position comparison, so it does not establish feature or acoustic acceptance.

## Changes

- Save the ordered library queue, duplicate-track occurrence, elapsed position,
  Shuffle/Repeat and other supported playback modes before switching to radio.
- Preserve that snapshot across station switches and controller reconnects.
- Add `POST /radio/resume-playlist` and `source.resumePlaylist` availability,
  snapshot ID, and status in state responses.
- Reject stale requests and prevent duplicate restores from rewinding playback.
- Check indexed files before interrupting radio. A partially acknowledged
  restore requires deliberate recovery rather than automatic replay.
- Preserve muted-controller retention and the existing fresh-session boundaries.

See [PLAYLIST_RESUME.md](PLAYLIST_RESUME.md) for the complete contract. This is
the server component; the House Music Resume Playlist button is separate work.

## Installation

Run the supplied commit-pinned update commands in the Pi terminal, or extract
the source archive and run these commands from its `house-audio-server-v0.13.0`
directory:

```sh
python3 -m py_compile house_audio_server.py
python3 -m unittest discover -s tests -v
sudo sh install.sh
curl --retry 10 --retry-connrefused --retry-delay 1 --max-time 5 -fsS \
  http://127.0.0.1:8787/health
```

Expect version `0.13.0`, `startup.ready: true`, and reachable MPD/Snapserver.
If startup is still pending, repeat the health request. A successful HTTP 200
alone does not establish healthy dependencies; inspect the JSON fields.

The installer copies all seven Python service modules and the three subwoofer
web assets. It preserves an existing `/etc/default/house-audio-server` and
does not rewrite `/var/lib/house-audio-server` settings, stations, controller
identity, listening history, or subwoofer configuration. It restarts only
`house-audio-server`; it does not restart or reconfigure MPD/Snapserver or flash
renderer firmware.

The control-service restart intentionally ends the current listening session
and clears its queue under [SESSION_BEHAVIOR §13](SESSION_BEHAVIOR.md#13-server-restart-is-a-fresh-session-boundary).
An already-present passive renderer may start a fresh configured default.

## Verification and remaining acceptance

The reviewed feature has 123 passing local tests. Release verification reruns
the same suite against the versioned source and the extracted archive, checks
Python/shell syntax, and exercises the installer with redirected destinations
and mocked service commands. Those checks do not establish a live deployment.

After installation, confirm `/health` and `/state` report v0.13.0 and that
`source.resumePlaylist` is present. With a folder already playing, select a
station and verify the snapshot is available; restore using its returned ID
and check the queue, selected track, position and modes. Switching stations
must preserve the original snapshot, and repeating a completed request must
not rewind the track. Check muted-phone behavior with a real renderer as part
of the physical acceptance pass.

Actual MPD decoding, stream-switch sound, renderer buffering, and phone-button
integration remain unverified by the local test model. Queue list order and
the Shuffle flag are preserved; MPD's private random-history permutation is not.

## Rollback

If needed, install the recorded v0.12.0 baseline from a separate checkout:
`1bd11d9d5a25c1a62140b4caa87cc68cb8ae1a95`, using its `sudo sh install.sh`.
That also restarts the control service and ends the listening session. The
update introduces no persistent Resume Playlist data migration.
