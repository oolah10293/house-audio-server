# House Audio Server v0.14.4 — startup measurements and MPD batching

**Release candidate; not yet installed on the Pi.** The user's 2026-11-22
authorization covers packaging and installation of the previously approved
fresh-session startup timing and batched MPD transfer update.

## Changes since installed v0.14.3

- Preserve existing Python `SystemRandom` shuffle, selected track order and
  all passive/control/CD/radio session rules.
- Send the existing ordered fresh-default MPD commands in bounded 64-KiB
  `command_list_begin` / `command_list_end` chunks over one connection,
  awaiting one reply per chunk rather than one per song.
- Emit a monotonic `startup_trace` series for the fresh-session policy,
  directory listing, shuffle, MPD batch acknowledgments, playback confirmation,
  and first subsequent observed Snapserver `default` stream `playing` status.
  This is a sampled control observation, not an on-wire first-audio packet or
  physical ESP32 audible signal. Details and caveats are documented in
  [STARTUP_TIMING_AND_MPD_BATCHES.md](STARTUP_TIMING_AND_MPD_BATCHES.md).

No ESP32 firmware, MPD/Snapserver settings, buffer lengths, application
controls, or other server session behavior are intentionally modified.

## Package/installer

- Versioned package: `HouseMusic_Server_v0.14.4_Pi.zip`, built using
  `python3 tools/package_pi.py --output-dir dist` from clean committed source.
- `SOURCE_SHA.txt` and `MANIFEST.json` bind the source files to a precise
  commit/tree. Package builder verifies all committed file bytes and ZIP
  integrity. Do not reuse the older v0.14.3 ZIP.
- `install-cd.sh` retains the existing complete integrated installer,
  preinstall backup of server/systemd/CD gateway, and automatic rollback on a
  failed post-install health check. It checks version 0.14.4 and presence of
  `run_batched` / `startup_trace` before touching installed files.
- The existing Pi environment/default folder/state remain in place.
  Installation restarts the server and CD gateway services, interrupts playback
  and triggers the established fresh-session boundary.
- The underlying `install.sh` and its existing browser/subwoofer completeness
  guard have matching v0.14.4 identities.

## Verify after installation

```sh
curl -fsS http://127.0.0.1:8787/health
sudo journalctl -u house-audio-server -f -o cat | grep --line-buffered startup_trace
```

Power off all passive speakers and let the last track complete, then power one
on to start a truly fresh session. Capture the `startup_trace` lines and
the first audible sound time by observation. The trace starts at the *policy
decision*, not physical power-on. Verify expected track shuffle/Repeat All,
return-before-track-end, ordinary Pause resume, and joining an already-playing
session without changing its queue.

**Status:** GitHub regression and package verification are tracked separately
after publishing the release commit; physical Pi install/audio measurements are
not proven merely by passing CI. See the release checkpoint or user-provided
installation output for deployment confirmation.
