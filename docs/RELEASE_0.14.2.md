# House Audio Server v0.14.2 — subwoofer fallback volume

Release baseline: browser-enabled `feature/house-music-web` commit
`a965b37bca592dc67df8feb73aad040cf036e196`, which includes the House
Music browser at `/`, Library filename/metadata sorting, and the song-title
copy fix. This is **not** the outdated `main` branch.

## Change

- Persist a separate `fallbackVolumePercent` (0–100, default **25**) for
  each registered subwoofer in the existing subwoofer settings file.
  Existing registrations transparently read as 25% without writing data
  until a user saves settings.
- Expose the setting on the existing `/subwoofers` page and registration form.
- When a healthy Snapserver snapshot first confirms that every assigned parent
  is absent and the dependent subwoofer itself is present, issue a single
  `Client.SetVolume` request for that subwoofer, preserving its existing mute
  state. This uses the per-client Snapcast volume, **not** Bose hardware
  attenuation or the Rio DSP gain.
- A remaining assigned parent inhibits the reset; a returning parent rearms
  the one-shot reset for its next departure. Do not continuously enforce the
  fallback while parents remain absent.
- Snapserver failures or stale observations are **unknown** and do not cause
  a volume change or rearm an already-applied fallback.
- No ESPHome node firmware, Home Assistant settings, MPD, or Snapserver service
  change is required.

## Regression safety

- Existing House Music `/`, `/house.js`, `/house.css` and the subwoofer
  assets remain served by the server. Real HTTP tests verify status 200 and
  expected content. The installer lists all six browser/subwoofer assets.
- The installer checks its source version, browser root route, fallback hook,
  required files, and Python compilation **before touching** the deployed
  directory. It refuses incomplete/wrong-branch source.
- The release ZIP is produced by GitHub Actions from the explicitly authorized
  `release/house-music-0.14.2` branch, after Python tests, JavaScript checks,
  and shell syntax checks. The archive's version and assets are checked.
- The test suite models Snapcast RPC replies. Hardware confirmation of
  the physical Bose/Rio and actual network loss requires field testing.

## Install

Only use the release branch or its verified ZIP — **never `main`**.
From the existing checkout on the Pi:

```sh
git fetch origin release/house-music-0.14.2
git switch --detach FETCH_HEAD
grep 'SERVICE_VERSION =' house_audio_server.py
grep -F '"/": ("house.html"' house_audio_server.py
sudo sh ./install.sh
```

Expected `SERVICE_VERSION = "0.14.2"`. `install.sh` restarts the
`house-audio-server` service; by system design this resets the ongoing HOUSE
session. It preserves persistent subwoofer configuration and other settings.

```sh
curl -fsS http://127.0.0.1:8787/health
curl -s -o /dev/null -w 'Browser HTTP: %{http_code}\n' http://127.0.0.1:8787/
curl -s -o /dev/null -w 'Subs HTTP: %{http_code}\n' http://127.0.0.1:8787/subwoofers
```

Expected `0.14.2` in health and HTTP **200** for both pages.
Check in a real browser that Library sort, song-title copying, and the
Subwoofers page are intact; each registered sub should show 25% fallback.

Confirm a parent departure resets only its assigned subwoofer's software
volume; another active assigned parent does not. Confirm a parent return
rearms the next departure and the Bose's HA attenuation remains at the
user's chosen value.

## Rollback (browser-enabled v0.14.1)

Use the known-good browser-enabled commit **not** `main`:

```sh
git fetch origin feature/house-music-web
git switch --detach e5c598224aa59d10cb37b58a9ff08927c3fe7932
sudo sh ./install.sh
```

This returns to the pre-integration browser source and does not delete
saved subwoofer settings. Its software simply ignores the additional
fallback field when reading old settings (the file is still version 1);
the extra field is accepted only on v0.14.2 settings writes, so do not
change sub assignments on v0.14.1 until you re-upgrade.
