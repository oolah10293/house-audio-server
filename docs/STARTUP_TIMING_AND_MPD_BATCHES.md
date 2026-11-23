# Fresh passive-session timing and MPD command-list optimization

Status: source implementation and GitHub CI verification complete. **Not yet installed or measured on the Raspberry Pi.**

## Scope

This update preserves the server's existing Python `SystemRandom` shuffle,
the existing MPD command ordering, passive session lifecycle, CD integration,
and Snapcast/ESP32 settings. Only the **fresh passive default-session** queue
transfer uses bounded MPD command lists.

The `MpdClient.run_batched` transport reuses one connection, sends ordered
commands in command lists no larger than 64 KiB, and waits for one result per
list rather than one result per song. It propagates `ACK` errors without
silently retrying or masking partial MPD queue writes. A longer per-read
timeout (at least 30 seconds) applies only to this potentially large batch
path, not to normal MPD queries.

## Trace

Each fresh default start logs lines prefixed `startup_trace` to the
house-audio-server process logs. Every line has:

- `event`: what completed or began
- `elapsed_ms`: monotonic elapsed time since the server began processing
  the fresh passive session
- `delta_ms`: elapsed time since the previous trace event
- Optional counts/status fields

Events include:

1. `fresh_passive_session_detected` (the policy begins the fresh session;
   **not** the moment power was applied to the ESP32)
2. `mpd_library_query_started`
3. `mpd_library_query_completed` with track count
4. `python_shuffle_completed`
5. `mpd_queue_transfer_started`
6. `mpd_command_batch_acknowledged` with cumulative command count
7. `mpd_queue_transfer_completed`
8. `mpd_playback_state_observed` (the verified MPD transport state)
9. `waiting_for_snapserver_stream`
10. `snapserver_stream_playing_observed`, if Snapserver's polled `default`
    stream status becomes `playing` within 20 seconds; otherwise
    `snapserver_stream_not_observed_within_20s`.

An MPD error in the policy tick emits `startup_failed`. Only fresh passive
default starts are traced; a node joining existing playback does not rebuild
the queue and is not a fresh-start event.

**Measurement limit:** Snapserver status is polled, and `playing` can
occasionally predate this specific startup. The final trace event is the
first observation of that status after MPD confirms playback, **not** a
direct first-packet transmission timestamp or measured ESP32 audible time.
The monitored stream is `default`, the project's MPD-to-Snapserver stream.

## Field check

On the Pi after installing a release that contains this change:

```sh
sudo journalctl -u house-audio-server -f -o cat | grep --line-buffered startup_trace
```

Turn off all passive speakers and let any final track drain. Power up one
passive radio into a genuinely fresh session and capture the complete trace.
Repeat several times with the existing `MP3s` and `Rap` defaults if
appropriate. Compare total timing and song/queue order to the established
session contract; verify a radio joining an ongoing session still does not
replace the playlist. A prior instrumented baseline on the installed
v0.14.3 release does not exist, so a precise on-Pi before/after speedup
cannot be claimed from source tests alone.

## Verification and deployment

- Automated protocol tests exercise multiple bounded MPD command lists over
  one socket, exact command order, a failing `ACK` and timing stage output.
- Existing modeled passive/session tests retain the same shuffle/MPD order.
- GitHub Actions test job passed for PR #22 after the source and tests.
- No live Pi, real MPD decoder/FIFO output or physical ESP32 acceptance
  has been performed for this change.
- The server's shipped version and installer package identity remain
  `0.14.3`. An updated release/package and Pi install are separate
  deployment work; do not misidentify the pre-change 0.14.3 archive as
  containing these changes.
