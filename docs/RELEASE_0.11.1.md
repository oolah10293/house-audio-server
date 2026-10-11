# House Audio Server v0.11.1 — WXDX metadata hotfix

The user's 2026-10-10 screenshot showed House Music v0.3.0 displaying WXDX's entire
structured metadata blob as a large song title. The intended display was
**Blue Monday** by **ORGY**. v0.11.0 forwarded MPD's Title tag as ordinary text;
the initial tests covered plain titles but missed this real station format.

## Fix

- Normalize the leading title/artist/album fields of structured radio metadata
  before `/state` or `/queue` reaches any controller. Keep station tracking IDs,
  URLs and other operational fields out of the display fields.
- Preserve quoted titles, commas, apostrophes and Unicode. Do not reinterpret
  ordinary combined titles or local-file tags.
- Apply the same normalization before history qualification and when loading old
  saved entries. Tracking-ID changes alone do not become fake song transitions.
- Keep the existing >10-second threshold, previous/current separation, history
  persistence, bookmarks, session policy and playback behavior.
- Install the new `radio_metadata.py` alongside the existing service modules.

**Keep House Music v0.3.0 installed. No APK or S3 update is needed.** Its existing
separate fields display the corrected server values, including Last played.

## Verification

All **184 Python tests pass** (ten new regression checks), plus Python compilation,
shell syntax and whitespace checks. The fixture reproduces the visible screenshot
metadata, including nested URL quotes. Tests exercise actual MPD state/queue
normalization, HTTP `/state` current and previous-song fields, tracking-ID changes,
plain/local metadata preservation, quotes/Unicode, malformed input, and old saved
history/checkpoint recovery. Physical Pi/phone acceptance remains pending.

## Update the Pi

Run from any directory in the Pi terminal:

```sh
house_radio_fix_dir="$(mktemp -d)" &&
git clone --branch server-v0.10.0-radio --single-branch https://github.com/oolah10293/house-audio-server.git "$house_radio_fix_dir" &&
(cd "$house_radio_fix_dir" && sudo sh install.sh) &&
curl --retry 10 --retry-connrefused --retry-delay 1 -fsS http://127.0.0.1:8787/health
```

The existing branch name is retained; health should report **0.11.1**. The service
restarts and follows its normal fresh-session rule, so select WXDX again afterward.
Now Playing should show readable title/artist fields, and Last played should be
readable after a qualifying song change. The shown Blue Monday fixture is a test
of the screenshot; the actual live station may already be playing another song.

Saved stations and settings remain. The new normalization also corrects readable
old structured history on load; there is no need to clear saved station/history files.
