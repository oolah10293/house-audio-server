# House Audio Server v0.14.4 — startup measurements and MPD batching

**Pi installation confirmed on 2026-11-22 from the user's complete terminal output.** Packaging and installation were authorized for the approved fresh-session startup timing and batched MPD transfer update. Actual startup timings and audible hardware acceptance remain pending.

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

## Confirmed Pi installation — 2026-11-22

The user ran a single command on `ads-suck@pihole` that cloned the repository,
checked out pinned release source `d657e783de379b275f93010b7c14318b980fb90b`,
created and verified the Pi ZIP locally, extracted it, and invoked
`sudo sh ./install-cd.sh`. The returned terminal output contained:

- Package: `/home/ads-suck/housemusic-v0.14.4.ig0XNS/HouseMusic_Server_v0.14.4_Pi.zip`
- Package bytes: `87711`, files: `27`
- Source SHA: `d657e783de379b275f93010b7c14318b980fb90b`
- ZIP SHA-256: `a4dd9b26a0c6a9be44a3e679ad380b316a7663533d2406dd4a4d3ab4e33a22e6`
- Backup: `/var/backups/house-audio-server/pre-cd-20261122-210324-3284964`
- Installer result: `Installed House Music 0.14.4; browser, service and session policy verified.`
- Completion: `House Music server 0.14.4 installed.`
- CD input gateway updated in its independent service on port 8768.

The successful installer output confirms its version, installed-file checks,
service health, and session-policy API check. It does **not** establish a real
startup latency improvement, first-audio transmission timing, or physical
listening acceptance.

The next field step is to run:
```sh
sudo journalctl -u house-audio-server -f -o cat | grep --line-buffered startup_trace
```
and power one ESP32 radio on from a genuinely fresh/fully drained session.
Capture complete `startup_trace` output and confirm audible playback.

## First measured fresh-session trace — Rap, 839 tracks (user-provided Pi journal)

Observed on the installed v0.14.4 service using
`sudo journalctl -u house-audio-server -f -o cat | grep --line-buffered startup_trace`.
All numbers are monotonic milliseconds relative to server policy beginning this
fresh passive session, **not** radio power-on or ESP32 audible output.

| Stage | Elapsed ms | Delta ms |
| --- | ---: | ---: |
| Fresh passive session detected (folder `Rap`) | 0.0 | 0.0 |
| MPD library query started | 0.1 | 0.0 |
| MPD library query completed (839 tracks) | 133.6 | 133.5 |
| Python shuffle completed | 134.5 | 0.9 |
| MPD queue transfer started (846 commands) | 134.8 | 0.3 |
| MPD command batch acknowledged (846/846) | 139.7 | 4.8 |
| MPD queue transfer completed | 139.8 | 0.2 |
| MPD playback state observed (`play`, 839 tracks) | 141.5 | 1.7 |
| Awaiting Snapserver stream observation | 141.5 | 0.0 |
| Snapserver `default` stream observed `playing` | 911.8 | 770.2 |

**Interpretation:** the 839-song batch transfer took approximately 5 ms and
the service observed MPD playing by approximately 142 ms after the new-session
policy decision. The 770.2 ms between that point and the Snapserver observation
is **not proven audio transport latency**: Snapserver is sampled by a monitor
configured to poll every 1 second; this event is not a timestamp of the actual
first audio packet, nor of audible playback on an ESP32. The listall query
(133.5 ms) is the largest directly timed server preparation step. The trace
does not prove a before/after speedup because the old v0.14.3 code had no
matching instrumentation.

**Field status:** these measurements verify the batched startup path reached
MPD `play` and the monitor subsequently reported Snapserver `playing`.
User has not yet reported audible start time or measured ESP32 power-on to
sound. Keep a separate follow-up for an accurate actual Snapserver audio onset
if needed. No additional changes authorized by this measurement.

## Second fresh-session trace and stopwatch observation — Rap, 839 tracks

The user timed approximately **15 seconds from switching on a radio to hearing
music** with a stopwatch. The concurrent fresh-session trace was:

| Event | Elapsed ms | Delta ms |
| --- | ---: | ---: |
| Fresh passive session detected | 0.0 | 0.0 |
| MPD library query started | 0.0 | 0.0 |
| Library query completed; 839 tracks | 118.4 | 118.4 |
| Python shuffle completed | 119.3 | 0.9 |
| Queue transfer started; 846 commands | 119.6 | 0.4 |
| Batch acknowledged; 846/846 commands | 124.5 | 4.9 |
| MPD queue transfer completed | 124.7 | 0.1 |
| MPD state confirmed play; queue 839 | 126.2 | 1.5 |
| Awaiting Snapserver observation | 126.2 | 0.0 |
| Snapserver default stream observed playing | 944.5 | 818.3 |

The stopwatch and the log measure **different boundaries**. The log starts at
the server's fresh-passive-session decision and ends at a *polled* Snapserver
stream status; it does not establish when the node powered up, first connected,
received packets, or produced audible sound. Consequently, the approximately
14-second discrepancy cannot be assigned to a specific stage (or solely to
ESP32 boot/network connection) from these observations.

**Diagnostic next step (without new firmware changes):** while one radio is
already playing, power on a second radio and time its power-on-to-audio delay.
Joining an existing session does not rebuild the default MPD queue. A similar
15-second result would point attention toward node boot/network/Snapclient
arrival/buffering, whereas a much shorter result would suggest investigating
fresh-session timing boundaries. This comparison is proposed, not yet measured.

## Additional observation — Motorola join into active playback

The user confirms **both comparisons concern the same Motorola radio**; it was the original motivation for the startup-delay investigation. The Motorola produces sound in approximately **2–3 seconds** when joining existing playback, but takes approximately **15 seconds** from power-on to audio for a fresh-session start. In the recorded fresh start, MPD state was observed playing after **126.2 ms** from the server's fresh-session decision, and Snapserver stream status was observed playing after **944.5 ms**. The difference therefore appears specific to fresh-session startup, not an inherent 15-second Motorola boot time. However, current logging does not timestamp physical radio power-on or directly timestamp first outgoing audio packets or first audible sound, so it **does not yet locate the missing time** before versus after the server policy's decision. The next discriminating evidence is a timestamped event between radio connection/presence recognition and the first `startup_trace`, and an observation of whether the logs appear early or just before sound. No code changes have been authorized or performed based on these measurements.
