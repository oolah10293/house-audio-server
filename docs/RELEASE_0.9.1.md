# house-audio-server v0.9.1

**Superseded by v0.9.2. Its library indexing is retained in the cleanup release; use [RELEASE_0.9.2.md](RELEASE_0.9.2.md) for installation.**

Adds library indexing for independent House Music v0.1.1. `POST /library/update` initiates a full-root incremental MPD update, coalesces automatic scans for 60 seconds across phones, and reuses any active scan. Pull-down refresh passes `force: true`. `GET /browse` adds scan status so the app keeps refreshing until indexing finishes. The new code sends no queue, playback, default-folder, or handoff command. Existing API behavior is retained.

Verification: all 125 Python tests pass, including MPD command construction, active-scan and multi-phone coalescing, forced refresh, failed-start recovery, HTTP validation, and new entries appearing after an index update. No Pi deployment or real SMB filesystem scan has been performed for this release.

With House Music v0.1.1, add a file to Shared Music. Pull down on the browser list, wait for scanning to finish, and confirm the new entry. Repeat with two phones and during playback; refresh must not independently start, skip or replace music.
