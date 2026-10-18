# Resume Playlist — unreleased server implementation

Implemented and tested in source on 2026-10-17 for the House Music requirement in
[app issue #14](https://github.com/oolah10293/smb-music-player/issues/14).
No installer, release package, version increment, or Pi deployment is part of
this change. The app button remains separate work.

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
unreleased source retains the deployed service version string. A missing field
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
requests, partial writes, and lost MPD/HTTP-state acknowledgments.

Before this change, full discovery ran 125 tests with 27 errors referencing
removed `ReturnHomeHandoffs` and `DiagnosticsRecorder` classes. Those historical
tests are retained unchanged, and their failures are not claimed as fixed.
Final verification: **21 new tests pass; full discovery runs 146 tests, with
119 passing and the same 27 pre-existing errors**. A comparison against an
isolated checkout of baseline `1bd11d9` confirmed identical failing test IDs and
no new failures. Python syntax compilation and `git diff --check` also pass.
Run the focused suite with:

```sh
python3 -m unittest discover -s tests -p test_playlist_resume.py -v
```

No live Pi/MPD/Snapcast audio acceptance was performed. Test results do not
establish actual audible transition timing or renderer-buffer behavior.

MPD command semantics reference:
https://mpd.readthedocs.io/en/stable/protocol.html
