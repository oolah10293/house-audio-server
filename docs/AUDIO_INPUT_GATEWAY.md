# House Music: reusable audio source gateway — development checkpoint

**2026-11-11:** Authorized design, isolated from production server and synchronized Snapcast outputs. Source-node Arduino implementation lives in `house-audio-esp32` branch `feature/reusable-input-node`.

**New implementation:** `pi_input/gateway.py` is a Python 3-only HTTP service on port 8768. It accepts `POST /register` containing a stable source ID, name, capability/kind, status, track list and node port. The node IP comes from the actual TCP connection. Each heartbeat refreshes presence; expiry is 18 seconds. The registry is separate from Snapcast renderer presence.

The gateway serves `GET /sources` (JSON), `GET /` (simple status page), and `GET /audio/<sourceId>/<track>.wav` (transparent proxy of the finite WAV from the source node's `GET /track/<number>.wav`). No dynamic network script or compiled binaries required on Pi.

**Preserves production:** no modifications to `house_audio_server.py`, no MPD commands, queue clearing, source switching, Snapserver changes or renderer changes. Existing service on 8787 is not replaced. No install/deployment has happened.

**Tests run locally:** 8 tests passed across the development ZIP; gateway tests cover request validation, heartbeat expiry, HTTP WAV proxy, HEAD, registration and missing track. Python unit tests alone do NOT establish hardware or MPD compatibility. Run `python3 -m unittest discover -s tests -p test_gateway.py -v` to reproduce Python gateway tests.

**Initial trial:** copy `pi_input/gateway.py` and start `python3 gateway.py --host 0.0.0.0 --port 8768`; expose to trusted LAN only (no authentication). Then configure source node's Wi-Fi and Pi IP in its setup page. Navigate to `http://PI-IP:8768/` and try a WAV track URL from a computer first.

**Still unimplemented/unverified:** source selection in House Music browser/app, server-controlled MPD source switching with playlist snapshot/resume, analog line-in support, hardware optical drive WAV sector reads, streaming throughput, audio latency and synchronized room playback. All require further implementation/field testing before production deployment. This is a development milestone, not a release.

**Delivered user ZIP:** House_Music_Audio_Input_Arduino_and_Pi.zip, SHA256 `c810cd274c5cda000110c99a7fc5a9fa2d230efc6ca63cff6fdd67e358114cd2`. Code here corresponds to its Python gateway and gateway test paths.

## Build and test confirmation (2026-11-11, subsequent validation)

- **Arduino IDE compatible compile PASSED**: GitHub Actions run [43145250004](https://github.com/oolah10293/house-audio-esp32/actions/runs/43145250004). It installed Espressif Arduino core **2.0.14** and compiled the new `HouseAudioCDInput` for `esp32:esp32:XIAO_ESP32S3`. No binary uploaded to actual XIAO by this test.
- **Pi gateway CI PASSED**: GitHub Actions run [43145423131](https://github.com/oolah10293/house-audio-server/actions/runs/43145423131), Python 3.13 gateway unit tests. Additional local checks: 8 Python tests total, and a WAV proxied through gateway decoded as 44.1-kHz, 16-bit stereo PCM by ffprobe (synthetic test data, NOT actual optical drive audio).
- **Ready-to-use Arduino/Pi source ZIP** delivered as `House_Music_Audio_Input_v0.1_Arduino_Pi.zip`, SHA256 `b23ed9b5be728b58fa01cde11c5a290d88fecf2b1408bbd1ead7ce3540b7c2b7` (16 files). This replaces earlier ZIP `House_Music_Audio_Input_Arduino_and_Pi.zip` with documentation corrected after CI. Actual source files and six USB driver blobs are committed across these two feature branches, and hashes of source blobs checked against the ZIP originals.
- **Critical remaining work:** User's hardware/audio testing, MPD input stream acceptance and House Music centralized source selection + playlist resume integration; analog line-in capture not yet implemented. Neither repository production main branch nor Raspberry Pi installation has been changed.
