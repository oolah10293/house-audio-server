# Resume Playlist — server v0.13.1

Implemented and tested in source on 2026-10-17 for the House Music requirement in
[app issue #14](https://github.com/oolah10293/smb-music-player/issues/14).
Packaging and deployment preparation were authorized on 2026-10-18.
See [v0.13.0 release/install notes](RELEASE_0.13.0.md). User-provided terminal
output confirmed Pi installation and healthy startup on 2026-10-18. Live
playlist-restore/audio acceptance remains pending. House Music v0.4.0 delivered
the button on 2026-10-18; its first reported live restore failed as documented below.

## Field failure and v0.13.1 correction — 2026-10-18

The user reported a failed restore from House Music v0.4.0. The Pi's `/state`
response showed a 1,666-entry bookmark, source `unknown`, transport `pause`,
resume status `uncertain`, and the exact error
`ACK [5@0] {seekcur} Not seekable`. This establishes the failed MPD operation,
not by itself whether that particular file is permanently unseekable. The user
subsequently confirmed that the same song seeks normally when selected directly
from Library (2026-10-18, 11:15 America/New_York).

The v0.13.0 restore issued Play, Pause and Seek without waiting for decoder
startup. Review of upstream MPD v0.24.4 (commit
`72077d76f11ffba21f6705fe912f47b0b5dc1d6e`) found a matching race:
`DecoderControl::Start` retains the preceding decoder's seekability until
`SetReady`; `IsUnseekableCurrentSong` also checks a decoder in `START` state.
`Player::SeekDecoder` can therefore reject an early seek using the preceding
radio stream's unseekable flag. `Player::ActivateDecoder` clears `status.audio`,
which `CheckDecoderStartup` fills after initialization. This is a plausible
explanation of the field error, strengthened by the successful direct-seek test;
the Pi's decoder timing during the failed restore has not been measured.

The v0.13.1 fix keeps the selected file paused and polls read-only MPD state
for its audio format before the single position seek. It checks the selected
song ID, queue position/length, paused transport and decoder errors on each
read. The readiness window is five seconds; each MPD read also retains the
existing socket timeout. Timeout, changed selection, decoder failure, read loss,
or an actual rejected seek leave the attempt uncertain with its error retained.
No queue/seek write is retried and there is no silent fallback to position zero.

Two delayed-decoder regressions reproduced the exact reported ACK with the old
implementation and pass with the fix, including muted-only retention. Additional
tests cover permanently unseekable input, readiness timeout, changed selection,
decoder error and read loss. All 129 Python tests passed locally. The MPD model
now uses the production queue normalizer so field names match the protocol.
These are modeled tests, not real-MPD or home-Pi acceptance.

The user authorized packaging the server correction on 2026-10-18 at 11:35
America/New_York. See [v0.13.1 release/install notes](RELEASE_0.13.1.md).
Installation and successful restore on the Pi remain pending.

## Behavior

On the first library-to-radio switch, the Pi captures the ordered queue (including
duplicate entries), selected occurrence/index, elapsed position, Random/Shuffle,
Repeat, Single, and Consume. Playing files are briefly paused before measuring
position, so the bookmark precedes clearing MPD for the station. A paused queue
retains its position; a stopped queue with no selection resumes from its first
entry. An empty queue has no bookmark.

Station changes, radio Play/Pause/Stop, phone disconnect/reopen while the session
continues, and bookmark rename/delete do not replace the library bookmark.
Explicit folder replacement/queue clear, observed external MPD queue replacement,
session completion, or service restart discard it. It is in memory only, in
accordance with the existing fresh-session restart rule. Resuming does not alter
any controller's local mute/eligibility report.

The queue's list order and Shuffle flag are restored. MPD's private random-play
history/permutation and original song IDs are not preserved across queue clear.
Position is the MPD position at the switch, not a measurement of the last audio
sample heard through a renderer's buffer. Audible transition timing remains a
physical acceptance check.

## API for the companion app

`GET /state` and ordinary source-bearing transport responses add:

```json
{
  "source": {
    "type": "radio",
    "resumePlaylist": {
      "available": true,
      "snapshotId": "server-generated-32-character-id",
      "status": "ready",
      "lastError": null,
      "trackCount": 776
    }
  }
}
```

The count above is illustrative. Use the returned ID verbatim; never create one
on the phone. Availability and the opaque ID are the capability check; this
server release identifies itself as v0.13.1 (the API was introduced in v0.13.0). A missing field
on an older server means the feature is unsupported.

`POST /radio/resume-playlist` accepts exactly:

```json
{"snapshotId": "the-id-returned-by-the-server"}
```

A successful response is the normal complete state response. The Pi validates
the snapshot and indexed files, stops radio, loads the saved entries, selects
and seeks the saved occurrence, restores modes, and requests playback. With a
known muted-only controller session it holds the queue paused and assigns the
existing policy's automatic-pause ownership; eligible audio return resumes it.
Unknown renderer presence is not treated as proof that every output is muted.

The status becomes `completed` and `available` becomes false. Repeating the same
ID returns current state without a playback, seek, or queue write, including
after manual Pause. A new radio visit creates a new ID; old requests cannot
restore over its queue. The MPD write lock serializes concurrent requests and
presence decisions.

## Failure and retry contract

| Condition | Result |
| --- | --- |
| Malformed/missing/extra request fields | 400 `invalid_request`; no playback writes |
| Startup not ready | 503 `startup_pending`; no playback writes |
| Expired or replaced ID | 409 `stale_playlist_resume`; refresh state |
| Radio queue externally replaced | 409 `no_saved_playlist`; adopt current state |
| A saved track is absent from MPD's index | 409 `playlist_tracks_missing`; station and bookmark remain intact |
| MPD index cannot be read before restore | 503 `mpd_unavailable`; station and bookmark remain intact; retry is safe |
| MPD failure during restore, including a lost acknowledgment | 503 `mpd_unavailable`; bookmark status becomes `uncertain` |
| Repeated attempt after an uncertain write | 409 `playlist_resume_uncertain`; no replay |

An uncertain attempt retains the saved library data but blocks automatic radio
retry and passive autoplay. State reports `source.type: "unknown"`, the actual
MPD state, and `resumePlaylist.lastError`. Pause/Stop remain available. Explicit
station selection starts a fresh attempt ID while keeping the original library
bookmark, allowing a deliberate Resume retry; selecting a folder/clearing or
ending the session discards it. A completed restore whose HTTP/state response
was lost remains safely repeatable with the original ID.

Preflight checks cannot prevent a file disappearing between validation and MPD
loading it; that is handled as an uncertain write, not by silently dropping
entries. A failure during initial capture leaves the library queue intact and
may leave it paused; no radio queue replacement has occurred yet. Native MPD
writers outside this service do not participate in its write lock.

The app should use `available` when deciding whether to offer Resume Playlist.
The exact empty-state presentation remains an app decision; this server change
does not add or hide any Android controls.

## Verification

The new tests use the real HTTP handler and session policy with a stateful MPD
model, covering queue/position/mode restore, duplicates, station switches,
phone reopen, paused/stopped/empty queues, invalid/missing files, external queue
replacement, session/restart boundaries, muted output, concurrent/double
requests, partial writes, and lost MPD/HTTP-state acknowledgments. Review added
cases for an unavailable MPD index before any write and an incomplete restored
queue that must stay stopped and cannot be blindly replayed.

Before this change, full discovery ran 125 tests with 27 errors referencing
removed `ReturnHomeHandoffs` and `DiagnosticsRecorder` classes in baseline
`1bd11d9`. The 2026-10-18 review removed those obsolete tests and replaced them
with HTTP checks that the four historical handoff routes and `/diagnostics`
return 404 without calling MPD. No runtime endpoints were removed by this PR;
the v0.12.0 source baseline already lacks them. Older implementation tests remain
available in Git history.

Local verification on 2026-10-18: **all 123 tests pass**, comprising 98 unchanged
current-behavior tests, 23 Resume Playlist tests, and two removed-route tests.
Python syntax compilation and `git diff --check` also pass. Run the full suite
and focused suite with:

```sh
python3 -m unittest discover -s tests -v
python3 -m unittest discover -s tests -p test_playlist_resume.py -v
```

No live Pi/MPD/Snapcast audio acceptance was performed. Test results do not
establish actual audible transition timing or renderer-buffer behavior.

MPD command semantics reference:
https://mpd.readthedocs.io/en/stable/protocol.html
