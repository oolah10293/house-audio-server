# house-audio-server v0.13.1

Prepared on 2026-10-18 after the user's explicit authorization to proceed with
the server fix. This corrects the Resume Playlist decoder-startup sequence;
installation and successful restore on the home Pi remain pending.

## Change

The first reported House Music v0.4.0 restore stopped at the saved-position seek
with `ACK [5@0] {seekcur} Not seekable`. The user confirmed that the same file
seeks normally when selected directly from Library. MPD v0.24.4 source contains
a startup race matching this failure; the precise timing on the Pi was not logged.

The server now keeps the restored track paused and waits for MPD to report its
decoder audio format before issuing the single position seek. It checks the
selected song ID, position in the queue, queue length, paused transport and MPD
errors while waiting. The readiness window is five seconds, with the existing
socket timeout on each read. Failed reads, timeout, changed selection or a real
seek rejection retain the existing uncertain state and error. Queue/seek writes
are never automatically replayed, and a rejected seek never silently resumes at
zero. Existing mute/presence policy and duplicate-request protection remain.

Keep **House Music v0.4.0**. The API is unchanged; this package updates the Pi's
control service. No Android or renderer firmware update is part of this fix.

## Verification

- All **129 local Python tests passed** against the versioned source.
- Delayed-decoder regressions produced the reported ACK with the old sequence
  and pass with the correction, including muted-only retention.
- Failure tests cover timeout, decoder error, changed selection/queue/transport,
  read loss, permanently unseekable input, and existing lost-ACK/duplicate cases.
- Python and shell syntax checks passed.
- The installer was exercised with redirected destinations and mocked system
  commands: all seven Python modules, three web assets and the service unit
  match source; existing configuration is preserved and a missing default is
  created; only `house-audio-server` is restarted.
- Release archive and CI evidence, source commit and checksum are recorded in
  [release PR #13](https://github.com/oolah10293/house-audio-server/pull/13).

These checks do not establish a real-MPD or home-Pi restore pass. The test model
uses production queue normalization but simulates decoder timing.

## Install and check

Use the commit-pinned commands provided with this release, or extract the ZIP
and run these commands from its `house-audio-server-v0.13.1` directory:

```sh
sudo sh install.sh
curl --retry 10 --retry-connrefused --retry-delay 1 --max-time 5 -fsS \
  http://127.0.0.1:8787/health
```

Confirm version `0.13.1`, `startup.ready: true`, and reachable MPD/Snapserver.
If startup is still pending, repeat the health check.

The installer preserves the existing runtime configuration and persistent
stations, settings, controller identities, listening history and subwoofer
configuration. It restarts the control service; it does not restart or
reconfigure MPD/Snapserver. The control-service startup policy intentionally
ends the current session and discards the queue and in-memory resume bookmark.
An already-present passive renderer may start the configured default folder.

After installation, create a fresh bookmark: play a folder track and note its
position, select a station, then tap **Resume Playlist** in House Music v0.4.0.
Check the restored track/position, continued playback, Sort returning, and no
unconfirmed-restore message. Station switches should retain the same bookmark.
Check that a manually muted phone remains muted and other eligible outputs play.

If the restore still fails, capture `source.resumePlaylist.lastError` from
`GET /state` before selecting another source, which clears that error.

## Rollback

The prior v0.13.0 release source is commit
`bc2ef5fedc0eb95158312bbc0f023273a29c5f36`. Install it from a separate checkout
using `sudo sh install.sh` if necessary. It retains the reported restore bug.
There is no persistent data migration to reverse; rollback also restarts the
control service and ends the listening session.
