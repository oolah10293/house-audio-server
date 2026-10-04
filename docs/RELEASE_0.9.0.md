# Server v0.9.0 — active SMB return-home handoff

**Historical release only. Superseded by the independent apps and removed from the server in v0.9.2. Do not use this as a current setup guide.**

Source release dated 2026-10-01. Last confirmed Pi deployment is v0.8.2; deployment and physical phone/S3 acceptance remain pending.

## Why this changes

The phone previously remained on private SMB playback after returning home. A subsequently powered S3 could start a different HOUSE default queue. The returning live phone session must instead become the idle house's one authoritative session.

## Implementation

- Reserve idle HOUSE immediately after home identity succeeds. Hold passive default startup while the phone attaches and prepares its snapshot.
- Commit the exact queue order, current track/position, Shuffle, and Repeat once. Status receipts reconcile timeouts without replaying a queue replacement or seek.
- Preserve HOUSE already playing or holding an ordinary retained pause. Controller attachment by itself still never starts playback.
- Retain the existing muted-only automatic pause, final-node drain, default-folder startup, and fresh-idle restart behavior. There is no silent-playback exception.
- Preserve the attached controller lease across a long commit that blocks heartbeat requests, without changing actual output eligibility/readiness.
- Cancel stale reservations on explicit transport. Expire abandoned reservations after 15 seconds. Quarantine partial MPD write failures until deliberate recovery.

The [archived v0.9.0 API contract](https://github.com/oolah10293/house-audio-server/blob/c57116e1a05bcc42f8e956b99055acbc452dad63/docs/API.md#return-home-handoff-v090) records its former schemas and failure handling. No ESP32 firmware change is required for this server coordination.

## Verification

Implementation commit: [`d19e2694`](https://github.com/oolah10293/house-audio-server/commit/d19e2694e9c1222e5eadf88607511130a4a0cb7a). [GitHub CI run 36814563574](https://github.com/oolah10293/house-audio-server/actions/runs/36814563574) passed compilation and all 119 tests.

`python -m py_compile house_audio_server.py` and `python -m unittest discover -s tests -q`: **119 tests pass**. Coverage includes queue/order/selection/position/options, passive arrival during transfer, existing-session protection, long-operation lock serialization, acknowledgement loss, repeat commit, invalid/indexed-path validation, renew/expiry, partial failures/cancel, explicit Pause, muted-only retention, final-controller departure, and startup reset guarding.

This release has not been tested on the permanent Pi or Android/S3 hardware.

## Subsequent outcome

The server was later confirmed deployed at v0.9.0, but Android v0.4.3 handoffs failed in both directions. The independent-app decision superseded this design. Current installation and checks are in [RELEASE_0.9.2.md](RELEASE_0.9.2.md); the original transfer checklist remains available in Git history.
