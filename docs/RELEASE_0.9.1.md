# house-audio-server v0.9.1

Adds library indexing for independent House Music v0.1.1. `POST /library/update` initiates a full-root incremental MPD update, coalesces automatic scans for 60 seconds across phones, and reuses any active scan. Pull-down refresh passes `force: true`. `GET /browse` adds scan status so the app keeps refreshing until indexing finishes. The new code sends no queue, playback, default-folder, or handoff command. Existing API behavior is retained.

Verification: all 125 Python tests pass, including MPD command construction, active-scan and multi-phone coalescing, forced refresh, failed-start recovery, HTTP validation, and new entries appearing after an index update. No Pi deployment or real SMB filesystem scan has been performed for this release.

Install on the Pi using a fresh checkout:

```sh
house_update_dir="$(mktemp -d)"
git clone https://github.com/oolah10293/house-audio-server.git "$house_update_dir" &&
(cd "$house_update_dir" && sudo sh install.sh)
curl --retry 10 --retry-connrefused --retry-delay 1 -fsS http://127.0.0.1:8787/health
```

Confirm version 0.9.1, startup ready and dependencies reachable. This restarts only house-audio-server. Existing MPD startup policy can affect the live queue during service restart, so install when playback can be interrupted. MPD/Snapserver configuration and S3 firmware are unchanged. Existing `/etc/default/house-audio-server` is preserved by the installer.

Then install House Music v0.1.1 and add a file to Shared Music. Pull down on the browser list, wait for scanning to finish, and confirm the new entry. Repeat with two phones and during playback; refresh must not independently start, skip or replace music.
