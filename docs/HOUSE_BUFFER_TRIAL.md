# HOUSE Country Buffer trial — 2026-09-30

Prepared for Android v0.4.2; **not applied to the Pi and not field-validated**. House Audio Server remains v0.8.2. This changes Snapserver configuration only, with no MPD API/session-policy or ESP32 firmware update. The starting experiment is **3000 ms** shared buffer, keeping **20 ms** chunks. The production depth is still open.

## Apply and reverse on the Pi

From this repository on the Pi:

```sh
python3 tools/house_buffer.py
sudo python3 tools/house_buffer.py --apply
sudo systemctl restart snapserver
sudo systemctl status snapserver --no-pager
sudo journalctl -u snapserver -n 60 --no-pager
```

The first command only previews a diff. Inspect it before applying. The helper changes only active `[stream] buffer`, preserving source, codec, sample format, chunk size and unrelated sections. It refuses duplicate sections/settings, unrecognized buffer values and explicit chunk sizes other than 20 ms. `--config` selects a non-default file; verify the service actually uses that file and has no command-line buffer override. The apply step creates a timestamped backup next to the config and prints exact rollback commands. Restarting Snapserver briefly interrupts all outputs. Restoring the printed backup and restarting returns to the actual prior configuration, rather than assuming it was 1000 ms.

## What the source establishes

The deployed Snapserver version is recorded as 0.31.0. Android bundles Snapclient from the pinned [Snapcast commit cf2be071](https://github.com/badaix/snapcast/tree/cf2be07155b850fd3d660416164970688179ce5c).

- `client/controller.cpp` computes target buffering from shared `bufferMs` minus server client latency minus the local `--latency` adjustment. Positive local latency compensation advances playback; it is not a second shared buffer.
- `client/player/opensl_player.cpp` passes a predicted 50 ms output delay to the stream. That is not measured end-to-end phone/Bluetooth latency. The reported roughly one-second phone/S3 difference is still undiagnosed.
- In the FIFO reader, a reconnect resets the source timestamp. `Server::onResync` logs; `Server::onStateChanged` sends a control notification. Neither broadcasts an audio-buffer flush. A new codec header recreates the Android decoder/player/stream, but an ordinary MPD Next/Seek/queue replacement on the same FIFO does not promise such a header. **Do not claim old audio is immediately flushed on all renderers.** Control requests still execute immediately; already-distributed audio can remain audible for the playout delay. A synchronized discontinuity mechanism remains future work if this lag is unacceptable.
- The inspected [ESPHome Snapclient source](https://github.com/c-MM/esphome-snapclient/blob/ce51e2fd861348698a6b4b7462fdda038cb942c9/components/snapclient/decoder.cpp) reads `server_settings_message.buffer_ms` and `.latency` into client settings. This does not identify the deployed firmware revision or prove its memory capacity. Verify the real S3 reports 3000 ms and stays stable.

## Acceptance sequence

1. Keep Android's phone/wired and Bluetooth corrections at **0** initially. Record shared buffer and per-client server latency from the Android sync dialog (hold the output icon after unmuting) and from S3 logs. Preserve existing per-client offsets; do not adjust two places at once.
2. At 1000 ms, use an obvious beat/transient to measure phone versus S3 and S3 versus S3; record which output route is used. Repeat at 3000 ms. Record at least several comparisons rather than tuning to one event.
3. Test both S3s and phone together under ordinary and heavier LAN use. Capture time-stamped `/diagnostics` around audible dropouts; report frequency/duration, resets, allocation errors and sync drift. More buffering is an experiment, not a diagnosis of network contention.
4. Measure initial join/rejoin, Next, Seek, playlist replacement, Pause and Stop from button press to actual speaker change. Distinguish MPD/control response time from old buffered audio. Do not accept a claim of instant flushing without observing every renderer.
5. Only after measuring a repeatable phone offset, hold the Android output icon and adjust the current route profile in small steps. Positive means earlier, negative means later. Android limits advance to retain 200 ms beyond the server latency and clamps the UI range to ±2000 ms; before settings arrive, no positive advance is applied. Applying a change briefly restarts this phone's receiver, without changing MPD or the S3 timeline. Compare wired/phone and Bluetooth separately.
6. Roll back if dropouts/memory pressure worsen or control-to-speaker lag is unacceptable. Record the chosen value and evidence before calling it a production default.
