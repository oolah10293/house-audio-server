# Agreed house-audio behavior

**Status: authoritative product behavior.** Some rules below are now implemented and runtime-proven while others remain future work. This document defines the intended behavior; implementation/validation status is tracked in the repository README, API docs, and issues.

The 2026-09-29 clarification makes §4 authoritative over the former §11 resume behavior and replaces cross-session shuffle persistence with §6 fresh-session randomness.

The 2026-09-30 clarification makes Bluetooth audio a requirement for Android phone renderer eligibility (§7). Play/Resume and queue commands do not independently unmute the phone. This supersedes the former pre-command auto-unmute rule; the existing server pause/retention and final-node rules remain in force. It records approved behavior, not a new release or a claim of implementation.

**2026-10-01 decision: Android SMB Music and HOUSE Music are completely independent apps (§8).** This supersedes all earlier Android home/away handoff requirements, including the 2026-09-30 active-SMB return-home transfer and the subsequent transition-coordinator proposal. Do not implement automatic or manual transfer, shared queue/song/position, SMB-triggered HOUSE queue reset, or cross-app coordination. SMB Music v0.5.0 and House Music v0.1.0 now implement this split in separate Android packages. Their release records distinguish build verification from pending phone acceptance. Server v0.9.0 is unchanged.

**2026-11-21 decision: only an unmuted phone/controller can hold a session (§5).** Muted controllers remain connected for controls but do not prevent last-node shutdown. This supersedes the earlier muted-controller retention rule. The physical CD-source exception in [CD_AUTO_TAKEOVER.md](CD_AUTO_TAKEOVER.md) remains in effect.

**2026-11-24 latest CD-eject decision (implemented in v0.14.8 source; Pi acceptance pending):** A confirmed disc eject is a special session boundary, governed by [CD_AUTO_TAKEOVER.md](CD_AUTO_TAKEOVER.md). Eject must **never auto-resume** the prior music. With a session-retaining node still connected, restore prior playlist/radio and exact playlist playback position but leave it **paused** until deliberate Play. With none connected, **clear the session and enter fresh idle**. A subsequently powered-on **passive** node loads the configured default folder with a **new shuffle**; a controller merely connecting does not auto-start. This supersedes the v0.14.7 eject-triggered restore-and-resume behavior, without changing other existing disconnect, final-track-drain, or CD takeover rules. The owner explicitly authorized the scoped server implementation on 2026-11-24; it is now merged into `main` as [d4bc66a2](https://github.com/oolah10293/house-audio-server/commit/d4bc66a2bb2c22794b84bb5261fa230119458661), source-verified in [server PR #29](https://github.com/oolah10293/house-audio-server/pull/29), with [v0.14.8 versioned installation source](RELEASE_0.14.8.md) now merged and physical Pi acceptance still pending. This does not authorize ADC integration, ESP32 firmware, gateway, or unrelated changes.

These rules supersede earlier suggestions of an always-playing private radio station, starting music whenever any controller opens, and treating every failed server request as permission to switch to standalone playback.

## 1. One house session; separate control and sound

The Raspberry Pi remains the permanent host. MPD owns the current house queue and transport state; Snapserver supplies the synchronized stream. Music stays at `/mnt/sharedrive/John/Shared Music`. **Folders are playlists.**

The approved Android direction is separate SMB Music and HOUSE Music apps under §8. HOUSE session authority and a device's local sound output are independent decisions:

- **HOUSE:** the app controls the Pi/MPD session and displays the Pi's current track, position, queue, shuffle, and repeat state. An enabled phone/PC output receives the synchronized house stream; it must not play an independent copy of the file through its standalone engine.
- **SMB Music (Android):** a separate independent SMB/Tailscale player with its own queue, position, buffering, recovery, and Bluetooth behavior. Its playback does not control or reset HOUSE.
- **Windows standalone:** retains its existing local/mapped-drive/UNC player; the Android app split makes no new Windows product decision.
- **Output muted or unmuted:** controls whether that device produces sound. It does not transfer ownership of the queue.

A **passive node** is an output such as an ESP32 radio with no playlist-control interface. A **controlling node** is the Android HOUSE app, Windows player, or browser interface. A controller can also be an audio output, but those roles must not be conflated.

The browser controller is an accepted part of the plan alongside Android HOUSE and the Windows player. It should use the same folder-first control service. Browser audio rendering is not an agreed requirement.

## 2. Starting a fresh listening session

With no nodes connected and no final track still finishing, nothing is playing. The Pi waits rather than advancing a playlist through an empty house.

**Only a passive node automatically starts a new default session from this idle state.** A radio being powered on should start the currently configured passive default folder (`MP3s` or `Rap`) with **Shuffle and Repeat All**, using a newly randomized order for this fresh session (§6). Independent SMB listening does not suppress or replace that startup.

A phone, PC player, or browser merely connecting first does **not** automatically start a track, even when its output is unmuted. It waits for an explicit Play action. Do not restore a controller's stale former private queue merely because it connected. SMB Music playback does not create HOUSE play intent.

This fresh-session rule is different from a paused retained/active session. **A passive radio arriving into an ordinary paused/retained session resumes that session.** A completed-drain boundary pause is excluded: it is fresh idle under §4/§11. Passive-node arrival is therefore allowed to override Pause; controller arrival alone is not.

## 3. Joining and controlling an existing session

A controller joining while music is already playing adopts and displays the **existing** song and playlist. Its unmuted output joins the same current house playback; its muted output stays silent. Joining must not restart the song or replace the queue.

The controller can then browse folders, select tracks or a folder, and use the usual playback controls. These deliberate HOUSE commands change MPD's shared session, and all participating outputs follow it.

If the controller disconnects while other nodes remain, its selected playlist stays in effect. It is now the house session's queue, not a queue that requires that controller to remain present.

## 4. All nodes leave: finish the current track, then stop

When the last node disconnects during playback, the Pi lets the current track **finish**, then stops. The pending stop takes precedence over Repeat All; the queue must not roll into another song with nobody connected.

**A passive node or unmuted controller reconnecting before that final track ends cancels the pending stop and preserves the existing playlist.** An audible node continues the current track/session without a restart or fresh default shuffle. A muted controller reconnecting or sending heartbeats does not cancel the pending stop. An unmuted controller whose output is not yet ready can hold an automatic pause until its receiver is audible.

If the track reaches its end with nobody reconnected, the listening session ends. The house becomes **fresh idle**. The next passive-node startup loads the currently configured default folder (`MP3s` or `Rap`) with a **new random shuffle**, not the previous session's temporary queue or saved shuffle progress. MPD retaining the old queue in `pause @ 0.0` does not keep the session alive.

There is therefore one intentional exception to "no connected nodes means no playback": the final-track finishing period.

**2026-11-25 source audit — outstanding session-cleanup defect (not implemented):** Owner's live v0.14.8 `/state` reported `transport=stop`, `queueLength=837`, and `sessionPolicy.lastAction=final_track_completed_session_idle` with no session-retaining nodes (the two audible devices were dependent subwoofers). In `house_audio_server.py`, `PassiveSessionPolicy._complete_final_stop()` sends MPD `stop`, restores playback modes, and clears the library-resume bookmark, but **does not clear the MPD queue**. The `/play` API passes `play` directly to MPD, so the stale queue can be played again by deliberate Play even though the session was marked fresh idle. A new passive-node arrival replaces the queue with a fresh default-folder shuffle, which hides but does not eliminate the inconsistency. The same stop-without-clear concern applies to immediate last-participant termination of a paused library session. **Proposed, not authorized:** clear the MPD queue only after an irrevocably completed session (end-of-final-track or end of a paused last-participant session); preserve the queue during unfinished final-track drain, retained/manual Pause, source takeover, and normal current playback. Add regression coverage for stale-queue Play after completed idle and preserve the documented session/ADC boundaries. No source code, configuration, or firmware changes are authorized by this audit.

## 5. Only an unmuted controller holds a session

**Authorized 2026-11-21:** a phone may keep the house session open only while its output is unmuted. Pause and local mute remain separate.

- An unmuted phone holds the playlist, track and position while playback is paused. Background/screen-off heartbeats continue to count. Receiver readiness is separate from mute: an unmuted phone with a temporarily unavailable receiver still holds the session.
- A muted phone stays registered and can browse and control the house. Its heartbeat and stale Snapcast socket do not hold the session or cancel a final-track drain.
- Other house nodes continue to hold the session normally. Muting a phone must not disturb their playback or an explicit Pause while they remain present.
- When only muted controllers remain during library playback, finish the current track and then enter fresh idle. If that controller-held session is already paused (manually or automatically), end it without advancing the song. A muted controller arriving at an existing pause cannot hold it.
- A passive node or unmuted phone returning before the final track ends can retain the session. A muted phone returning cannot. After session end, merely unmuting does not restart playback; a later passive-node arrival starts the configured default with a new shuffle.
- Automatic pause/recovery remains available for an **unmuted** controller whose receiver is unavailable. Actual audible output must return before automatic resume. Explicit Pause/Stop still overrides that automatic resume.

The same output-state rule applies to PC/browser controllers using this API. A control-only browser has no unmuted renderer and cannot hold a session by itself. Live radio releases its station and saved playlist when the last session participant leaves. A plugged-in physical CD retains its existing no-speaker pause/bookmark behavior under [CD_AUTO_TAKEOVER.md](CD_AUTO_TAKEOVER.md); that retention belongs to the CD source, not the muted phone. When CD removal restores a library with only muted controllers present, the restored session ends.

Controller leases still use one heartbeat every five seconds and expire after fifteen seconds without renewal. Explicit Quit detaches immediately, clears the phone's local presentation, and sends no global Stop/Clear directly. An audible renderer may outlive its control lease and is counted once across its roles. Muted controllers remain visible in `controllerCount`; `unmutedControllerCount` and effective `presentCount` identify session participation. See [API.md](API.md).

## 6. New shuffle for every fresh passive session

**Every genuinely fresh passive listening session loads the currently configured default folder (`MP3s` or `Rap`) with a newly randomized queue order, Shuffle, and Repeat All.** Use fresh randomness, not a fixed/reset seed or a saved order from a completed session.

The same first song may occur naturally by chance. Do not exclude the previous first song, compare orders to force a difference, or reroll a valid shuffle. A one-track folder necessarily starts with that track.

This replaces the older requirement to persist/continue an exact default rotation or bookmark across completed sessions. Only the chosen default **folder setting** is intended to persist; the completed session's shuffled order/progress is not.

- A return before the final track ends continues the existing queue, track, and position without reshuffling.
- An ordinary paused/retained session remains resumable; it has not completed a drain.
- Once the final track finishes with nobody present, that session is over. The next passive power-on creates a fresh default session, even if MPD retained the old queue internally.
- Each fresh start reads the folder's current MPD-indexed contents, naturally incorporating indexed additions/removals.
- Controller-selected CDs and queues retain their familiar Shuffle/Repeat controls. The passive defaults are not imposed on those active sessions.

## 7. HOUSE output mute and transport controls

**Queued correction, not yet implemented:** the user has requested that manual Mute/Unmute work with the current output, including a headphone jack, while retaining Bluetooth automation. That fix is deferred in the Android roadmap (§9). The Bluetooth-only manual restriction described below records the shipped policy and must not override this queued correction.

The phone should show a **Mute output** control in HOUSE mode, switching to **Unmute output** when muted. This is a local output control, not MPD Pause or global mute. On Android it belongs in the lower Media3 Now Playing control strip beside transport, Shuffle/Repeat, and track time; a separate standalone button is not the desired UI.

If MPD is still playing for other rooms, an eligible phone unmuting joins the **current** house position rather than replaying the point when that phone was muted. If the session was automatically paused for an unmuted phone with an unavailable receiver, audible output return resumes that retained session. Muted-only controllers do not hold that pause.

Preserve the phone's mute choice across a reconnect. A network event must not unexpectedly make a muted remote controller start sounding.

**Android phone renderer eligibility requires a connected Bluetooth audio output.** Without one, the phone is ineligible and must remain muted, including after Play/Resume, song/PLAY LIST selection, queue changes, app reopening, network recovery, and node joins/leaves. The handset speaker is not an automatic HOUSE fallback. A paired device or input-only watch does not qualify as a connected Bluetooth audio output.

Play/Resume and queue selection control the shared session; they never independently unmute the phone. Pause then Resume must restore node playback while keeping a phone with no Bluetooth muted. Preserve an explicit local mute across transport commands; a local Unmute request also requires Bluetooth eligibility. Eligibility permits rendering, but actual sound still requires a ready receiver, an unmuted local output, and a playing HOUSE session. This phone-specific Bluetooth requirement does not apply to passive S3 or PC outputs.

Deliberate HOUSE Play/Pause/Next/Seek commands control MPD. Local output muting is distinct. Local audio-route selection, volume, and interruption handling must be kept separate from deliberately changing the house transport.

### Bluetooth phone-output policy

Bluetooth audio route state determines **phone renderer eligibility**, not normal HOUSE transport.

- Bluetooth audio disconnect mutes the phone renderer/output report and never directly sends MPD Pause/Stop.
- If another house output remains audible, MPD and those outputs continue.
- If only muted controllers remain, library playback finishes the current track and ends; an already-paused controller-held session ends without advancing. There is no need to wait for Quit or lease expiry.
- Bluetooth audio connect while HOUSE is already playing auto-unmutes the phone and joins the current synchronized stream, overriding a prior manual phone mute.
- HOUSE attach/reopen must evaluate **current route state**. If Bluetooth is already connected and HOUSE is already playing, join unmuted; do not require a new Bluetooth-connect callback.
- Bluetooth connect or pre-existing Bluetooth alone does not issue Play against fresh idle or deliberate Pause/Stop. The phone is eligible; if the user subsequently starts music, a ready, unmuted Bluetooth output can render without treating Play as an unmute command.
- Bluetooth reconnect/unmute can retain an unfinished final-track drain, but cannot resurrect a completed session. An automatic pause held by an unmuted phone can resume once an output is audible.

The client reports its actual local mute/readiness state; the server remains the authority for zero-audible-output pause/retention under §5. Session transport and phone eligibility are separate: the phone cannot bypass the Bluetooth requirement to satisfy a Play command.

Standalone Bluetooth output behavior is Android-local and is defined in [smb-music-player/docs/CENTRAL_PLAYBACK.md](https://github.com/oolah10293/smb-music-player/blob/main/docs/CENTRAL_PLAYBACK.md). It does not alter server policy.

## 8. Independent Android SMB Music and HOUSE Music

**Confirmed 2026-10-01: do not tie the two Android apps together in any way.**

- SMB Music owns only its independent SMB/Tailscale playback. Keep its folder-first browsing, queue, Country Buffer, outage recovery, and Bluetooth behavior.
- HOUSE Music controls and optionally renders only the authoritative Pi/MPD/Snapcast session. It adopts the current HOUSE state when it connects; opening it alone does not start fresh playback.
- There is no automatic or manual handoff in either direction, no queue/song/position/settings sharing, no "Play this at home" action, and no SMB-triggered reset of the HOUSE queue.
- Neither app launches, stops, or otherwise coordinates the other app. The user chooses which app to use. Normal Android audio focus is not a project-specific cross-app integration.
- Changing networks changes connection availability within each app; it does not select another app or transfer playback. HOUSE connection loss remains HOUSE reconnecting/unavailable. It never starts the SMB engine.
- Independent SMB listening has no effect on the HOUSE session or passive default folder. Passive S3 startup, existing-session adoption, unmuted-controller retention, final-node drain, and server restart continue to follow §§2–6 and §13.

### HOUSE connection and recovery

Retain physical non-VPN Wi-Fi/Ethernet LAN qualification and expected Pi identity checks for HOUSE availability. Tailscale/VPN-only reachability does not establish physical home presence. HOUSE HTTP/MPD/Snapcast traffic uses normal platform routing; connection failure is a HOUSE availability problem, not a request for independent file playback.

Begin reconnecting when a failure is detected. Distinguish a failed control connection from failed audio reception where possible (for example, Audio reconnecting versus House server unavailable). Recovered HOUSE audio joins the current synchronized position rather than playing an old backlog behind the other rooms. Keep HOUSE background controller presence and local renderer state truthful under §5/§7.

### Superseded implementation

Android v0.4.3 and server v0.9.0 were built for the former automatic home/away contract. The 2026-10-01 field report recorded failures in both directions. Independent SMB Music v0.5.0 and House Music v0.1.0 now remove those Android transfer paths; their physical acceptance remains separate from source/build verification. The existing server handoff endpoints remain part of the shipped API until an explicit implementation removes them; the independent apps must not use them. Their reservation/commit behavior is documented as legacy functionality in [API.md](API.md#return-home-handoff-v090). Preserve historical release and field evidence without treating it as the current product requirement.

**Server cleanup remains pending:** after migrating phones off the combined app, remove the return-home handoff endpoints, transient reservations/receipts, transfer-only controller lease extensions, and handoff hooks that block passive-session policy or cancel transfers on ordinary commands. Keep controller attach/heartbeat/detach, renderer ownership, normal MPD write serialization, passive default startup, unmuted-controller retention, final-track drain and startup reset. Existing combined v0.4.3 phones can still call the legacy endpoints when returning home during SMB playback; deleting them before migration can strand that old transition. House Music v0.1.0 needs none of them and requires no server update.

## 9. Implementation notes and unresolved edges

The rules above describe the desired product, not a completed implementation. Proposed engineering details must remain distinguishable from user decisions.

The Pi tracks controller presence and renderer/output state separately, retains active/paused sessions, persists the selected passive default folder, and distinguishes an unfinished drain from a completed session. Default shuffled order/progress does not survive completed sessions. A stale TCP socket alone is not proof that a powered-off node is present. Controller heartbeats and expiry follow §5. App-local connection recovery must not reintroduce the superseded cross-app handoff.

Controller arrival must not be used as a blanket override of explicit transport commands. **Passive-radio arrival is the deliberate exception:** powering on a passive radio should resume an existing paused MPD session, including a deliberate Pause. This excludes the completed-drain artifact described in §11.

The independent Android apps replace the previous unresolved whole-queue departure and return-transfer work; those are no longer implementation goals. The manual output-mute fix remains queued/deferred in the [Android roadmap](https://github.com/oolah10293/smb-music-player/blob/main/docs/ROADMAP.md): manual Unmute must work with the current output, including a headphone jack, while retaining Bluetooth automation. This decision does not claim that the Bluetooth-only manual restriction in the shipped app is fixed.

No pause/drain reconstruction is required after a `house-audio-server` restart: restart is a fresh-session boundary (§13). Source implementation and field-validation status are recorded separately in the API docs and README.

### Resume Playlist during radio — 2026-10-17

The requested House Music button returns from radio to the preceding folder
queue, preserving list order, selected track/occurrence, position, Shuffle, and
Repeat. The Pi owns that bookmark across station changes and controller reopen;
it is not a second playing queue or a phone-owned session. An explicit Resume
requests playback without changing local output mute/eligibility.

Server implementation and testing were authorized on 2026-10-17. The source/API
and failure behavior are documented in [PLAYLIST_RESUME.md](PLAYLIST_RESUME.md).
The existing completed-session and restart boundaries still discard the prior
bookmark. Packaging/deployment preparation for server v0.13.0 was authorized
on 2026-10-18. User-provided terminal output then confirmed installation and
healthy startup; [release notes](RELEASE_0.13.0.md) distinguish that deployment
check from pending live playlist-restore acceptance. The app update is separate work.

## 11. MPD boundary artifact; §4 remains authoritative

The useful v0.6.1 discovery is preserved: MPD 0.24 `single oneshot` can finish the final track and land in **Pause at 0.0 seconds on the next old-queue track**, rather than reporting transport `stop`.

That state is evidence that the previous session drained successfully. It is **not** an ordinary retained paused session to resume on a later radio power-on. Section 4 is authoritative:

1. Return before the final song ends: cancel the pending stop and continue the existing session unchanged.
2. Final song ends with nobody present: end that listening session and enter fresh idle.
3. Restore temporary Repeat/Single options and normalize the boundary artifact to stopped.
4. Next passive-radio power-on: load the currently configured default folder with a new shuffle under §6, never resurrect the completed CD/Rap queue.

This also applies when completion and radio return are first observed in the same policy poll. The boundary already ended the old session.

Example: a controller selected a CD, the last radio was switched off, and the song finished with nobody listening. Hours later a radio starts the configured `MP3s` or `Rap` default with fresh randomness; it does not resume yesterday's CD.

**Implementation history:** v0.6.1 successfully restored audible output on the permanent Pi, but did so by resuming the old queue. That resume choice is superseded by this clarification. v0.6.2 implements and unit-tests the completed-drain/fresh-session distinction and is now running on the permanent Pi. On 2026-09-29 the user reported same-song return after about 10 seconds unplugged and a different song after about five minutes unplugged. The captured `/session` action confirms the early-return branch; exact old-CD/Rap-queue replacement after completed drain remains a separate field check. See the evidence in [API.md](API.md). Normalizing a processed drain to stopped also prevents a later control-service restart from treating it as an ordinary pause. Section 13 now settles service/Pi restart during any unfinished/unprocessed drain: abandon that session and normalize to fresh idle.

## 12. Runtime-selectable passive default folder

The passive-radio default is no longer a fixed deployment-only choice. The Android HOUSE UI will expose a compact selector for the server-owned default folder.

Initial supported values:

- `MP3s`
- `Rap`

Required behavior:

- the selected value is persisted on the Pi;
- it survives phone disconnects and service restarts;
- it is readable by controllers so the UI can show the current value;
- changing it does **not** replace, restart, reshuffle, seek, or otherwise disturb the current active queue;
- it applies only when the house later enters a genuinely fresh passive-renderer auto-start session;
- passive-radio arrival into an already-playing or ordinary retained paused session still resumes/joins that session; a completed drain is fresh idle and loads the selected default with a new shuffle.

The existing `PASSIVE_DEFAULT_FOLDER` environment value remains the install-time fallback when no saved setting exists. v0.7.0 implements `GET /settings` and `POST /settings` with a persisted `passiveDefaultFolder` (`MP3s` or `Rap`); see [API.md](API.md). The service reads this value once per fresh passive start. No queue/order/progress is persisted, and setting it does not change any active/retained session or pending drain. The server setting is field-proven in v0.7.0, as recorded below. Android v0.4.0 implements the selector; its phone acceptance remains pending.

## 13. Server restart is a fresh-session boundary

A `house-audio-server` restart is intentionally treated as a **hard listening-session boundary**. Do not attempt to reconstruct or resume the previous live house session.

On restart:

- discard live controller leases;
- discard reported mute/output-ready state;
- discard automatic-pause ownership/reason;
- discard pending final-track drain state;
- abandon the previous queue/session as live session state and normalize MPD to fresh idle;
- preserve durable configuration/identity only, including the selected passive default folder and controller↔renderer ownership.

After restart:

- a phone/PC/browser merely reconnecting first does not auto-start playback; independent SMB playback does not restore or replace the discarded HOUSE session;
- a passive S3 that is already present or subsequently arrives may start a genuinely fresh session from the configured `MP3s`/`Rap` default with a new shuffle;
- no attempt is made to infer whether an old MPD Pause was deliberate or automatic.

This decision replaces the earlier idea of restoring the automatic-pause reason or unfinished drain across a control-service restart. A Pi reboot naturally falls on the same side of the boundary.

**Implementation status:** v0.8.1 implements this boundary. Before new playback commands or presence-driven playback, it stops MPD, clears the old queue, disables leftover Single/Consume/Repeat/Random modes, and verifies empty stopped state. A failed reset retries until MPD is available, independently of Snapserver availability. Disabling automatic presence policy does not disable the restart boundary. Once completed, routine MPD/Snapserver reconnections do not repeat the reset. Controller attachment and saved settings remain available while startup is pending; new attachments are not discarded by a delayed reset. See [API.md](API.md) for readiness and command gating. v0.8.1 is installed and the already-present-S3 restart path is field-proven below; the all-radios-off variant and physical controller transitions remain pending.

## 15. HOUSE Country Buffer

The shared house stream should use a deliberately generous **multi-second synchronized playout buffer**. This extends the standalone player's Country Buffer philosophy to LAN playback, but on a much shorter time scale.

Product/architecture rules:

- Snapcast source chunk size and renderer playout-buffer depth are independent. Keep small chunks (currently about `20 ms`) for normal stream cadence; a larger playout buffer must not be implemented by making giant packets.
- Increase the shared HOUSE buffer above the current ~`1000 ms` baseline and tune the final value empirically. "Several seconds" is the approved direction; the exact production number is not yet fixed.
- The buffer exists to hide short Wi-Fi/LAN contention and to provide timing headroom for client-specific output latency correction.
- A larger synchronized buffer may increase initial radio join/rejoin time and end-to-end MPD-to-speaker latency. Those costs are acceptable within reason for music playback if continuity improves materially.
- Deliberate transport/queue actions must remain semantically immediate at the control layer. The desired audible behavior is to discard/rebase obsolete queued audio rather than intentionally play the entire pre-command buffer before honoring a Next/Seek/playlist change.
- The actual Snapcast discontinuity/flush mechanism must be verified before implementation claims are made. If upstream behavior cannot invalidate stale playout cleanly, the implementation must explicitly account for that tradeoff rather than assuming it away.
- Per-client latency compensation is distinct from the shared buffer. The shared buffer supplies timing margin; renderer-specific offsets compensate repeatable output-path latency.

Field motivation, not diagnosis: brief S3 dropouts have been noticed around periods of heavier LAN/Internet traffic. Increasing synchronized buffer depth is an approved reliability experiment; it does not establish that network contention is the root cause.


## Status and evidence references

This document is the **normative behavior contract**, not the release/field log. Server implementation/runtime evidence belongs in [README.md](../README.md) and [API.md](API.md). Android device acceptance belongs in the Android repository's `HOUSE_VALIDATION.md`. ESP32 renderer evidence belongs in the ESP32 repository.
