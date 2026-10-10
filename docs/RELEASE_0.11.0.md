# House Audio Server v0.11.0

The Pi now keeps the previous identified radio song heard for more than 10 seconds,
including station and time. This addresses issue #9 and supports House Music v0.3.0.
The daemon observes MPD without requiring the app to be open. Current metadata
already supplied by MPD continues to appear in `/state`.

History is descriptive only. Saved stations, default folder, renderer ownership,
radio/library switching and the fresh-session startup policy retain their behavior.
MPD still decodes the stream; Snapcast still distributes it.

## Validation

174 Python tests pass, including 16 added history/unit/integration checks. They
cover the strict threshold, unchanged-queue metadata updates, short/blank titles,
station/local changes, pauses/stalls/reconnect resets, disk failures, checkpoint
recovery and passive playback without controller/API polling. Python compilation,
shell syntax and whitespace checks pass. Physical v0.11.0 acceptance is pending.
The user's v0.10.0 installation, audible radio and return to local music were
confirmed; those observations are not claimed as tests of this revision.

## Install on the Pi

This restarts the control service and ends its current session. A present passive
renderer then starts the configured local folder using the existing policy.

From the delivered archive, extract it, enter its `house-audio-server-v0.11.0`
folder and run:

```sh
sudo sh install.sh
curl --retry 10 --retry-connrefused --retry-delay 1 -fsS http://127.0.0.1:8787/health
```

Or install the updated radio review branch without downloading the archive:

```sh
house_history_update_dir="$(mktemp -d)" &&
git clone --branch server-v0.10.0-radio --single-branch https://github.com/oolah10293/house-audio-server.git "$house_history_update_dir" &&
(cd "$house_history_update_dir" && sudo sh install.sh) &&
curl --retry 10 --retry-connrefused --retry-delay 1 -fsS http://127.0.0.1:8787/health
```

The branch name is retained to update the existing radio PR; the installed version
must report **0.11.0** with `startup.ready: true`. The installer includes the new
`radio_history.py` module. It preserves existing configuration and saved stations.
No MPD/Snapserver reconfiguration, manual permission change or S3 update is required.

Install HouseMusic-v0.3.0.apk on the phone. Start a station with song metadata,
let a song play beyond ten seconds, then wait for the next song: Last played should
show the outgoing one. Short snippets should not replace it. Close the phone app
while an S3 keeps playing and reopen it after a song change to confirm Pi-owned
history. Station/local switching and a control-service restart must retain the
record while the existing startup policy remains unchanged.

See [API.md](API.md#radio-song-history-v0110) for the precise optional API fields,
conservative timing and metadata limitations. The record starts accumulating after
this update; it cannot recover earlier songs that were never observed.
