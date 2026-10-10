# House Audio Server v0.11.2 — additional WXDX metadata format

v0.11.1 fixed the first screenshot's title/artist envelope, but missed WXDX's
space-separated `text=` format. The user's second screenshot still showed a
large protocol dump. A direct ICY read from the actual WXDX stream on 2026-10-10
reproduced that exact `Home Of The Penguins - text="105.9 the X" song_spot="T"`
message and its tracking fields.

## Changes

- Parse both title/artist envelopes and prefixed or standalone `text=` fields.
  Music uses separate clean title and artist values, including the artist-prefix
  format. Commas, Unicode, apostrophes, and quoted text remain supported.
- Display the captured station message as **Home Of The Penguins** underneath
  the existing **105.9 The X** station label, with no tracking fields or fake artist.
- Use the structured content marker to exclude marked announcements and breaks
  from Last played. A preceding song heard for more than ten seconds remains
  available while the announcement runs and the next song starts.
- Exclude old raw non-music history/checkpoints on load, retaining an older valid
  song when available. Already-clean records without a marker cannot be classified
  retrospectively. Settings, stations, queues and playback policy are unchanged.

**Keep House Music v0.3.0 installed. No APK or S3 update is needed.**

## Verification

All **192 Python tests pass**, plus compilation, installer shell syntax and
whitespace checks. A second direct live read captured **Don't Wanna Go Home Tonight**
by **Three Days Grace**, followed by **Home Of The Penguins**. Passing those actual
values through the server's normalization produced the expected title/artist and
non-music classification; both captures are retained as regression fixtures.

Regression tests cover both user screenshots, actual MPD state/queue conversion,
space-separated music metadata, quotes and Unicode, normalization idempotency,
content markers inside titles, old history recovery, and HTTP `/state` across
song/long-announcement/song transitions. The history test explicitly checks that
ten seconds is insufficient while eleven qualifies, and that announcements lasting
thirty seconds never replace the previous song.

**Field confirmation, 2026-10-10:** after receiving the v0.11.2 update, the user
reported: “ok, that seems to work pretty well. The UI looks really good.” This
confirms good observed behavior after the WXDX fix with the existing v0.3.0 app.
It does not separately confirm every history timing, app-closed or restart case;
those retain their automated evidence and remain available for targeted checks.

## Update the Pi

Run from any directory in the Pi terminal:

```sh
house_radio_fix_dir="$(mktemp -d)" &&
git clone --branch server-v0.10.0-radio --single-branch https://github.com/oolah10293/house-audio-server.git "$house_radio_fix_dir" &&
(cd "$house_radio_fix_dir" && sudo sh install.sh) &&
curl --retry 10 --retry-connrefused --retry-delay 1 -fsS http://127.0.0.1:8787/health
```

Health should report **0.11.2**. The existing branch name is retained. Select WXDX
again after the service restart, which follows the usual fresh-session behavior.
The station's current broadcast may differ from the captured message.
