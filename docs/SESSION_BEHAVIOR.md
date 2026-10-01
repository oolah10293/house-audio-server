# Agreed house-audio behavior

**Status: authoritative product behavior.** Some rules below are now implemented and runtime-proven while others remain future work. This document defines the intended behavior; implementation/validation status is tracked in the repository README, API docs, and issues.

The 2026-09-29 clarification makes §4 authoritative over the former §11 resume behavior and replaces cross-session shuffle persistence with §6 fresh-session randomness.

The 2026-09-30 clarification makes Bluetooth audio a requirement for Android phone renderer eligibility (§7). Play/Resume and queue commands do not independently unmute the phone. This supersedes the former pre-command auto-unmute rule; the existing server pause/retention and final-node rules remain in force. It records approved behavior, not a new release or a claim of implementation.

The later 2026-09-30 return-home clarification settles §8: a phone actively playing SMB when it returns to an idle HOUSE transfers that live session to the Pi, creating the authoritative HOUSE session. A subsequently powered S3 joins that session; it must not start a second/default session. This supersedes the earlier prohibition on promoting active standalone playback into idle HOUSE.

These rules supersede earlier suggestions of an always-playing private radio station, starting music whenever any controller opens, and treating every failed server request as permission to switch to standalone playback.

## 1. One house session; separate control and sound

The Raspberry Pi remains the permanent host. MPD owns the current house queue and transport state; Snapserver supplies the synchronized stream. Music stays at `/mnt/sharedrive/John/Shared Music`. **Folders are playlists.**

A device's playback authority and its local sound output are independent decisions:

- **HOUSE:** the app controls the Pi/MPD session and displays the Pi's current track, position, queue, shuffle, and repeat state. An enabled phone/PC output receives the synchronized house stream; it must not play an independent copy of the file through its standalone engine.
- **STANDALONE:** the app controls its existing independent player. Android retains SMB/Tailscale playback and its existing buffering/recovery behavior. Windows retains its existing local/mapped-drive/UNC player.
- **Output muted or unmuted:** controls whether that device produces sound. It does not transfer ownership of the queue.

A **passive node** is an output such as an ESP32 radio with no playlist-control interface. A **controlling node** is the Android app, Windows player, or browser interface. A controller can also be an audio output, but those roles must not be conflated.

The browser controller is an accepted part of the plan, alongside the two existing players, not a replacement for them. It should use the same folder-first control service. Browser audio rendering is not an agreed requirement.

## 2. Starting a fresh listening session

With no nodes connected and no final track still finishing, nothing is playing. The Pi waits rather than advancing a playlist through an empty house.

**Only a passive node automatically starts a new default session from this idle state.** A radio being powered on should start the currently configured passive default folder (`MP3s` or `Rap`) with **Shuffle and Repeat All**, using a newly randomized order for this fresh session (§6), unless the returning phone is transferring its already-playing session under §8.

A phone, PC player, or browser merely connecting first does **not** automatically start a track, even when its output is unmuted. It waits for an explicit Play action. Do not restore a controller's stale former private queue merely because it connected. A returning phone's currently playing SMB session is different: §8 transfers that ongoing playback into idle HOUSE automatically, preserving the user's existing play intent.

This fresh-session rule is different from a paused retained/active session. **A passive radio arriving into an ordinary paused/retained session resumes that session.** A completed-drain boundary pause is excluded: it is fresh idle under §4/§11. Passive-node arrival is therefore allowed to override Pause; controller arrival alone is not.

## 3. Joining and controlling an existing session

A controller joining while music is already playing adopts and displays the **existing** song and playlist. Its unmuted output joins the same current house playback; its muted output stays silent. Joining must not restart the song or replace the queue.

The controller can then change the playlist in the normal SMB Player manner: browse folders, select tracks or a folder, and use the usual playback controls. In HOUSE mode those deliberate commands change MPD's shared session, and all participating outputs follow it.

If the controller disconnects while other nodes remain, its selected playlist stays in effect. It is now the house session's queue, not a queue that requires that controller to remain present.

## 4. All nodes leave: finish the current track, then stop

When the last node disconnects during playback, the Pi lets the current track **finish**, then stops. The pending stop takes precedence over Repeat All; the queue must not roll into another song with nobody connected.

**Any node reconnecting before that final track ends cancels the pending stop and preserves the existing playlist.** An audible node continues the current track/session without a restart or fresh default shuffle. If the returning device is only a muted controller, apply the muted-controller pause rule rather than keeping inaudible music running.

If the track reaches its end with nobody reconnected, the listening session ends. The house becomes **fresh idle**. The next passive-node startup loads the currently configured default folder (`MP3s` or `Rap`) with a **new random shuffle**, not the previous session's temporary queue or saved shuffle progress. MPD retaining the old queue in `pause @ 0.0` does not keep the session alive.

There is therefore one intentional exception to "no connected nodes means no playback": the final-track finishing period.

## 5. A muted phone holds a paused session

A muted phone is still a connected controller. It can keep the current session and playlist alive **without keeping the music running**.

The agreed case is:

1. Music is playing and a phone remains connected with its output muted.
2. The last unmuted node disconnects.
3. MPD **pauses and retains the playlist, current track, and exact position**. It does not finish the track and discard the session as though all nodes had gone away.
4. An unmuted node reconnecting, the phone being unmuted, **or a passive radio being powered on** resumes that retained session from the paused position.

Muting the phone while other audible nodes remain must not pause those nodes. An existing retained session is not a fresh idle session, so resuming it is not permission for a controller connecting to an otherwise idle house to start a new track.

The same rule applies to PC/browser controllers. **If the last controller leaves an already automatically paused session, end the session without advancing the song.** The next passive radio starts a fresh session using the configured default. This choice was confirmed on 2026-09-29.

**Background apps and screen-off phones remain present while heartbeats continue.** The agreed initial timing is one heartbeat every five seconds, expiring after fifteen seconds without renewal. Explicit Quit detaches immediately rather than waiting for expiry. Quit also clears client-local HOUSE presentation/cache; it does not send global MPD Stop/Clear merely to clean up the client UI. A still-audible renderer can remain an output if its control connection alone is lost; its roles still belong to one device. Android must maintain its background heartbeat through the appropriate service lifecycle. Browser timer suspension is a lost heartbeat, not proof of presence. These choices were confirmed on 2026-09-29.

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

The phone should show a **Mute output** control in HOUSE mode, switching to **Unmute output** when muted. This is a local output control, not MPD Pause or global mute. On Android it belongs in the lower Media3 Now Playing control strip beside transport, Shuffle/Repeat, and track time; a separate standalone button is not the desired UI.

If MPD is still playing for other rooms, an eligible phone unmuting joins the **current** house position rather than replaying the point when that phone was muted. If the session was automatically paused because only the muted phone remained, eligible output return resumes that retained session.

Preserve the phone's mute choice across a reconnect. A network event must not unexpectedly make a muted remote controller start sounding.

**Android phone renderer eligibility requires a connected Bluetooth audio output.** Without one, the phone is ineligible and must remain muted, including after Play/Resume, song/PLAY LIST selection, queue changes, app reopening, network recovery, and node joins/leaves. The handset speaker is not an automatic HOUSE fallback. A paired device or input-only watch does not qualify as a connected Bluetooth audio output.

Play/Resume and queue selection control the shared session; they never independently unmute the phone. Pause then Resume must restore node playback while keeping a phone with no Bluetooth muted. Preserve an explicit local mute across transport commands; a local Unmute request also requires Bluetooth eligibility. Eligibility permits rendering, but actual sound still requires a ready receiver, an unmuted local output, and a playing HOUSE session. This phone-specific Bluetooth requirement does not apply to passive S3 or PC outputs.

Deliberate HOUSE Play/Pause/Next/Seek commands control MPD. Local output muting is distinct. Local audio-route selection, volume, and interruption handling must be kept separate from deliberately changing the house transport.

### Bluetooth phone-output policy

Bluetooth audio route state determines **phone renderer eligibility**, not normal HOUSE transport.

- Bluetooth audio disconnect mutes the phone renderer/output report and never directly sends MPD Pause/Stop.
- If another house output remains audible, MPD and those outputs continue.
- If the phone becomes the only remaining muted controller, the existing server policy auto-pauses and retains the exact queue/song/position.
- If that final muted controller later disconnects/expires, the existing last-controller rule ends the retained session without advancing it.
- Bluetooth audio connect while HOUSE is already playing auto-unmutes the phone and joins the current synchronized stream, overriding a prior manual phone mute.
- HOUSE attach/reopen must evaluate **current route state**. If Bluetooth is already connected and HOUSE is already playing, join unmuted; do not require a new Bluetooth-connect callback.
- Bluetooth connect or pre-existing Bluetooth alone does not issue Play against fresh idle or deliberate Pause/Stop. The phone is eligible; if the user subsequently starts music, a ready, unmuted Bluetooth output can render without treating Play as an unmute command.
- If the server had automatically paused a retained session because the muted phone was the only remaining node, Bluetooth reconnect/unmute allows the existing automatic-pause rule to resume that retained session.

The client reports its actual local mute/readiness state; the server remains the authority for zero-audible-output pause/retention under §5. Session transport and phone eligibility are separate: the phone cannot bypass the Bluetooth requirement to satisfy a Play command.

Standalone Bluetooth output behavior is Android-local and is defined in [smb-music-player/docs/CENTRAL_PLAYBACK.md](https://github.com/oolah10293/smb-music-player/blob/main/docs/CENTRAL_PLAYBACK.md). It does not alter server policy.

## 8. Automatic home/away selection and phone handoff

### Home detection

Home identity is determined by **physical non-VPN LAN presence**, while ordinary HOUSE packet routing is a separate concern. Both Ethernet and Wi-Fi count.

The revised Android mechanism, based on the first v0.4.0 field test, is:

1. choose a non-VPN Wi-Fi/Ethernet Android network;
2. inspect that physical network's directly connected routes; the configured/reserved house LAN address must fall on one of those routes;
3. using normal Android routing, verify the expected Pi identity at that LAN address (for example MPD's `OK MPD ` greeting and/or the expected `house-audio-server` identity);
4. only the combination of a qualifying physical route plus expected Pi identity classifies the phone as HOUSE;
5. continue watching that physical network; loss of the qualifying network/route is the departure signal, subject to the still-undecided grace policy.

Once HOUSE is selected, MPD/HTTP/Snapcast traffic should use normal platform routing rather than being forcibly pinned to the physical Android `Network`. This allows Tailscale to remain connected at home while the physical network, not VPN reachability, remains authoritative for HOUSE/STANDALONE state.

Tailscale/VPN-only reachability must never classify a remote phone or laptop as home. Away from home, a VPN route to the Pi cannot substitute for the missing directly connected physical-LAN route. An unrelated LAN that happens to use a similar private subnet must still pass the expected Pi identity check.

The Pi must expose the MPD identity probe on its configured home-LAN listener as well as localhost. The first Android field test found MPD listening on loopback only; no remote HOUSE probe could succeed until the LAN listener was enabled. Deployment-specific private addresses remain local configuration and must not be committed.

Do not add mDNS/DNS-SD, SSID matching, GPS, a separate discovery daemon, or a custom handshake unless later testing demonstrates a real need.

A temporary failure while at home is **HOUSE reconnecting**, not an instruction to start a competing local playlist. Leaving the home LAN transitions to STANDALONE after the transition policy distinguishes departure from a brief interruption. Exact grace periods and ambiguous-network handling remain to be specified.

### Leaving home while listening

**Confirmed: an unmuted phone that was hearing playing house music should automatically continue the same song through its standalone SMB/Tailscale player when it leaves home.** Continue from where the phone stopped hearing the track, not from the beginning.

A muted phone stays silent. A paused or stopped session must not begin playing just because the phone changed networks or was technically unmuted.

The app needs to cache the library-relative file identity and its last heard playback position while connected. It cannot depend on asking an unreachable Pi which file to open after departure. Match by file identity/path, not by title/artist text.

The handoff does not send a stop, seek, or queue replacement to MPD. The house remains governed by its own connected-node rules, so other listeners continue unaffected. If this phone was the last node, the ordinary final-track completion rule applies on the Pi.

Automatic continuation is required; a completely gapless switch has **not** been demonstrated or promised. Standalone buffering/reconnection can introduce a pause.

Whether to copy the whole house queue into the away player, rather than just continuing the current song, has not been decided.

### Returning home

**Confirmed 2026-09-30: if the phone is actively playing SMB and HOUSE is idle, automatically transfer that phone session to the Pi and make it the authoritative HOUSE session.** Preserve the current playlist/queue order, track, playback position, and Shuffle/Repeat settings. Continue the song instead of restarting it or choosing a passive default. The phone then controls/renders the shared session according to its Bluetooth eligibility; its private SMB playback must stop as authority transfers.

The reported sequence is not correctly resolved by leaving the phone on SMB until an S3 starts a different HOUSE queue and then switching the phone to that queue. The second/default session must never be started in this return-home case. An S3 powered on after return joins the transferred phone session at its current track/position without replacing or reshuffling it.

Regaining a qualifying physical home LAN must trigger an immediate real identity/control probe rather than waiting for an old retry timer. A network-gain event is only a reason to probe; successful Pi identity still decides HOUSE availability.

Keep the existing distinctions:

- A HOUSE session already active before the phone returns remains authoritative under §3. Adopt it without overwriting its queue; the failed handoff's newly spawned default is not evidence of such a pre-existing session.
- Merely opening/attaching a controller, connecting Bluetooth, or returning with paused/stopped playback does not create Playing intent or revive a stale queue.
- Bluetooth eligibility, explicit Pause/Stop/Quit, muted-controller retention, final-node drain, and server-restart rules still apply. Transferring the session is not permission to unmute an ineligible phone.

The implementation must coordinate return-home transfer with passive-node auto-start so an S3 arriving during the handoff cannot start a competing default queue. Reconcile server state if a transfer acknowledgement is lost; do not blindly replay a queue replacement. The exact concurrency/API mechanism and transition timing remain implementation work. Automatic continuation is required; gapless switching is not promised.

### Reconnection status

Begin reconnecting when a failure is detected, not only after a buffer empties. Distinguish a failed control connection from failed audio reception where possible (for example, Audio reconnecting versus House server unavailable). Recovered HOUSE audio joins the current synchronized position rather than playing an old backlog behind the other rooms.

The short HOUSE synchronization buffer and the existing large standalone SMB buffer serve different purposes. Buffer drain duration and gapless recovery are not established by these behavior decisions.

## 9. Implementation notes and unresolved edges

The rules above describe the desired product, not a completed implementation. Proposed engineering details must remain distinguishable from user decisions.

The Pi tracks controller presence and renderer/output state separately, retains active/paused sessions, persists the selected passive default folder, and distinguishes an unfinished drain from a completed session. Default shuffled order/progress does not survive completed sessions. A stale TCP socket alone is not proof that a powered-off node is present. Controller heartbeats and expiry follow §5; the phone's home/away network grace period remains separate and undecided.

Controller arrival must not be used as a blanket override of explicit transport commands. **Passive-radio arrival is the deliberate exception:** powering on a passive radio should resume an existing paused MPD session, including a deliberate Pause. This excludes the completed-drain artifact described in §11.

Remaining client decisions and settled restart boundary:

- Whole-queue HOUSE -> away continuation remains undecided. The reverse direction is now settled in §8: the phone's currently playing standalone session transfers into idle HOUSE. Phone output follows the Bluetooth eligibility rule in §7; Play/Resume/PLAY LIST never independently unmute it or bypass that rule.
- No pause/drain reconstruction is required after a `house-audio-server` restart: restart is now explicitly a fresh-session boundary (§13).

These gaps do not cancel the confirmed rules. They are intentionally not filled with invented decisions. Source implementation and field-validation status are recorded separately in the API docs and README.

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

- a phone/PC/browser merely reconnecting first does not auto-start playback; once startup is ready, a distinct live standalone handoff follows §8 and must not be confused with restoring the discarded HOUSE session;
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
