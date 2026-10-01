# Server v0.9.0 — active SMB return-home handoff

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

The [API contract](API.md#return-home-handoff-v090) is authoritative for schemas and failure handling. No ESP32 firmware change is required for this server coordination.

## Verification

Implementation commit: [`d19e2694`](https://github.com/oolah10293/house-audio-server/commit/d19e2694e9c1222e5eadf88607511130a4a0cb7a). [GitHub CI run 36814563574](https://github.com/oolah10293/house-audio-server/actions/runs/36814563574) passed compilation and all 119 tests.

`python -m py_compile house_audio_server.py` and `python -m unittest discover -s tests -q`: **119 tests pass**. Coverage includes queue/order/selection/position/options, passive arrival during transfer, existing-session protection, long-operation lock serialization, acknowledgement loss, repeat commit, invalid/indexed-path validation, renew/expiry, partial failures/cancel, explicit Pause, muted-only retention, final-controller departure, and startup reset guarding.

This release has not been tested on the permanent Pi or Android/S3 hardware.

## Deployment and hardware checks

Use the repository's existing Pi update/install procedure, then verify `/health` identifies `house-audio-server` version `0.9.0` with startup ready. Updating/restarting the service intentionally ends the previous session under the established fresh-idle restart rule.

1. With HOUSE stopped and all passive radios off, play SMB away from home; return with Bluetooth connected. Confirm the phone's queue, track, and position become HOUSE without a second/default queue.
2. Power an S3 during the return transfer and after it. It must join the transferred song; the configured passive default must not replace that queue.
3. Return while HOUSE already plays another session. Confirm the existing HOUSE queue wins unchanged.
4. Transfer without an eligible phone output. Confirm ordinary muted-only pause/retention; power a radio and confirm retained playback resumes.
5. Interrupt the transfer response, then recover the network. Confirm a committed queue is not reinstalled or rewound.
6. Repeat existing last-node drain, muted-controller retention, explicit Pause/Resume, passive fresh default, and restart acceptance checks.

Record observed hardware results in the Android `HOUSE_VALIDATION.md` and server README; passing local tests does not establish field acceptance.
