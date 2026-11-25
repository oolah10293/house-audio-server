# House Music Server v0.14.8 — paused-or-idle CD-eject behavior

**Scope:** release of the user's explicitly authorized CD-eject policy, implemented and source-tested in [PR #29](https://github.com/oolah10293/house-audio-server/pull/29) and merged as `d4bc66a2`. This release does not implement ADC streaming or combined CD/ADC arbitration. No ESP32 firmware, input gateway source, MPD configuration, Snapcast configuration, web UI, or audio routing changes.

## Behavior on confirmed CD removal

- When a House Music session participant remains, an auto-owned CD eject restores the original library queue (exact track, duplicate occurrence, elapsed position and modes), or prior radio station, **without automatically playing**. The listener must explicitly press Play.
- Restore of an internet radio station does not start its audio stream even briefly before pausing.
- If **no session-retaining nodes** remain and Snapserver presence is known, eject ends ownership, clears the queue/bookmark and enters fresh idle. A muted-only controller does not keep the session.
- After fresh idle, the next passive node powering on starts the currently configured default folder with a freshly randomized queue and Repeat All. A controller merely connecting does not auto-start.
- A genuine later disc insertion still starts CD playback at track 1. Repeated eject/reinsert cycles preserve the original pre-CD music when there are session-retaining nodes.
- Routine heartbeats and old disc tokens cannot retake playback; a manual source override still wins. Physical CD-player power-off/disconnection retains its existing separate restoration policy.
- When Snapserver is unreachable, zero presence is not treated as proof that every listener has left. Preserve the recoverable paused source rather than erase an uncertain session.

**Architecture boundary:** CD-eject changes act only on the currently auto-owned CD. The existing Sources webpage remains ready to display registered future analog/line-in nodes. The future latest-power-on/insert-event-wins CD/ADC arbitration and its fallback rules are still only a documented design, not implemented.

## Verification and field acceptance

- Server change [PR #29](https://github.com/oolah10293/house-audio-server/pull/29), [GitHub Actions 45514245458](https://github.com/oolah10293/house-audio-server/actions/runs/45514245458): **PASS**, 234 Python tests plus syntax, installer shell, and browser tests.
- v0.14.8 release branch updates only the version identifier, installer/check guards, release documentation and packaging manifest / artifact name. Its final GitHub CI status should be checked against the exact merge/packaging commit.
- **Not yet physically verified:** Pi installation, real CD eject/insert, retaining paused radio/library after eject, no-node fresh start, and restoring after repeated discs.

## Pi installation and rollback

The complete Pi installer is still `install-cd.sh` (historical name). It refuses a mismatched version or source without the revised CD-eject implementation. It validates service health, browser content, session endpoint, installed file hashes and gateway file, and keeps a complete rollback snapshot of the current server and gateway before touching files.

This installer restarts House Music **and the existing independent CD gateway service**, even though the gateway source is unchanged. Installation interrupts current playback and the existing service-restart boundary resets the current listening session. It does **not** flash any S3 devices, replace MPD/Snapserver, or change saved default playlist settings.

Run from the extracted versioned package or from the exact GitHub commit's source archive:

```sh
sudo sh ./install-cd.sh
```

The rollback script is `sudo sh ./rollback-cd.sh` from the same extracted directory. Record the output backup path and use it if the physical acceptance check fails.

## Suggested real-hardware checks

1. With a speaker connected and a library song playing, capture its position. Insert a CD; eject with the LiteOn powered. Confirm the library returns at that position **paused**; then press Play.
2. Start from radio, insert and eject a CD; confirm radio is selected but remains silent until Play.
3. Reinsert another CD and eject it. The original library/radio should be restored paused again.
4. Turn off every session-retaining output while the CD is selected, then eject. Verify the queue is empty/idle; power on a passive speaker and verify a fresh default-folder shuffle begins.
5. Check normal CD-player power-off, manual source switching, heartbeats, Sources browser controls, and unrelated library/radio playback.

Do not mark real-hardware behavior field-proven from CI alone.
