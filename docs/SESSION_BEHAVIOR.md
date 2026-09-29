# Agreed house-audio behavior

**Status: authoritative product behavior.** Some rules below are now implemented and runtime-proven while others remain future work. This document defines the intended behavior; implementation/validation status is tracked in the repository README, API docs, and issues.

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

**Only a passive node automatically starts music from this idle state.** A radio being powered on should start the default `MP3s` folder with **Shuffle and Repeat All**, continuing the saved default rotation described below.

A phone, PC player, or browser connecting first does **not** automatically start a track, even when its output is unmuted. It waits for an explicit Play action. Do not restore a controller's former private queue into MPD merely because the controller connected.

This fresh-session rule is different from a paused retained/active session. **A passive radio arriving while MPD is paused should resume that existing paused session rather than leave it paused or replace it with the default MP3s queue.** Passive-node arrival is therefore allowed to override Pause; controller arrival alone is not.

## 3. Joining and controlling an existing session

A controller joining while music is already playing adopts and displays the **existing** song and playlist. Its unmuted output joins the same current house playback; its muted output stays silent. Joining must not restart the song or replace the queue.

The controller can then change the playlist in the normal SMB Player manner: browse folders, select tracks or a folder, and use the usual playback controls. In HOUSE mode those deliberate commands change MPD's shared session, and all participating outputs follow it.

If the controller disconnects while other nodes remain, its selected playlist stays in effect. It is now the house session's queue, not a queue that requires that controller to remain present.

## 4. All nodes leave: finish the current track, then stop

When the last node disconnects during playback, the Pi lets the current track **finish**, then stops. The pending stop takes precedence over Repeat All; the queue must not roll into another song with nobody connected.

**Any node reconnecting before that final track ends cancels the pending stop and preserves the existing playlist.** An audible node continues the current track/session without a restart or fresh default shuffle. If the returning device is only a muted controller, apply the muted-controller pause rule rather than keeping inaudible music running.

If the track reaches its end with nobody reconnected, the listening session ends. The next fresh passive-node startup uses the saved default `MP3s` rotation, not a previous session's temporary Rap folder or CD selection.

There is therefore one intentional exception to "no connected nodes means no playback": the final-track finishing period.

## 5. A muted phone holds a paused session

A muted phone is still a connected controller. It can keep the current session and playlist alive **without keeping the music running**.

The agreed case is:

1. Music is playing and a phone remains connected with its output muted.
2. The last unmuted node disconnects.
3. MPD **pauses and retains the playlist, current track, and exact position**. It does not finish the track and discard the session as though all nodes had gone away.
4. An unmuted node reconnecting, the phone being unmuted, **or a passive radio being powered on** resumes that retained session from the paused position.

Muting the phone while other audible nodes remain must not pause those nodes. An existing retained session is not a fresh idle session, so resuming it is not permission for a controller connecting to an otherwise idle house to start a new track.

The mute-only controller rule is a useful model for PC/browser integration as well; the exact presence/keep-alive behavior of a background app or browser tab still needs implementation definition.

## 6. Persistent default MP3s shuffle

The purpose of the saved default rotation is to avoid hearing the same first song and sequence every time a radio is switched on.

For the default `MP3s` rotation, preserve the shuffled order and progress through it across listening sessions. Continue through the remaining tracks instead of regenerating or restarting the order on every startup.

After the entire folder has played, generate a **fresh shuffled order**, avoiding an immediate repeat of the last track at the cycle boundary.

Example: if the saved order is `C -> F -> A -> D -> B -> E` and one session completes C, F, and A, the next default session begins with D, not C.

The default rotation's progress is saved **separately from the currently selected house queue**. A controller selecting Rap or a CD must not erase the default MP3s rotation. This is a saved bookmark/order, not a second simultaneous playback engine.

- If the current default MP3 track finishes during the normal end-of-session drain, next startup advances to the next track in the saved order.
- A genuinely unfinished default track can retain its position, including when a controller interrupts the default rotation to select a different folder.
- Do not replay an already-completed track solely because it was the last one selected.
- Do not impose this default folder's Shuffle/Repeat All choice on every controller-selected CD or queue; those remain under the familiar controls.

## 7. HOUSE output mute and transport controls

The phone should show a **Mute output** button in HOUSE mode, switching to **Unmute output** when muted. This is a local output control, not MPD Pause or global mute.

If MPD is still playing for other rooms, unmuting joins the **current** house position rather than replaying the point when that phone was muted. If the session was automatically paused because only the muted phone remained, unmuting resumes that retained session.

Preserve the phone's mute choice across a reconnect. A network event must not unexpectedly make a muted remote controller start sounding.

Deliberate HOUSE Play/Pause/Next/Seek commands control MPD. Local output muting is distinct. Local audio-route selection, volume, and interruption handling must be kept separate from deliberately changing the house transport.

## 8. Automatic home/away selection and phone handoff

### Home detection

Detect home by testing the actual condition that matters: the client must reach the house MPD instance **through a real non-VPN home-LAN path**. Both Ethernet and Wi-Fi count.

The approved simple mechanism is:

1. choose a non-VPN Wi-Fi/Ethernet network/interface;
2. through that specific network, connect to the client's configured/reserved house LAN address on MPD port `6600`;
3. require MPD's normal greeting beginning `OK MPD ` (optionally followed by `ping` / `OK`);
4. only then classify the client as HOUSE.

This MPD connection is only a small LAN-presence/identity probe. It does not replace the shared `house-audio-server` control layer used for session policy and normal controller commands.

Do not add mDNS/DNS-SD, SSID matching, GPS, a separate discovery daemon, or a custom handshake unless later testing demonstrates a real need. Tailscale/VPN-only reachability must never classify a remote phone or laptop as home because the probe is explicitly bound to the non-VPN LAN path.

A temporary failure while at home is **HOUSE reconnecting**, not an instruction to start a competing local playlist. Leaving the home LAN transitions to STANDALONE after the transition policy distinguishes departure from a brief interruption. Exact grace periods and ambiguous-network handling remain to be specified.

### Leaving home while listening

**Confirmed: an unmuted phone that was hearing playing house music should automatically continue the same song through its standalone SMB/Tailscale player when it leaves home.** Continue from where the phone stopped hearing the track, not from the beginning.

A muted phone stays silent. A paused or stopped session must not begin playing just because the phone changed networks or was technically unmuted.

The app needs to cache the library-relative file identity and its last heard playback position while connected. It cannot depend on asking an unreachable Pi which file to open after departure. Match by file identity/path, not by title/artist text.

The handoff does not send a stop, seek, or queue replacement to MPD. The house remains governed by its own connected-node rules, so other listeners continue unaffected. If this phone was the last node, the ordinary final-track completion rule applies on the Pi.

Automatic continuation is required; a completely gapless switch has **not** been demonstrated or promised. Standalone buffering/reconnection can introduce a pause.

Whether to copy the whole house queue into the away player, rather than just continuing the current song, has not been decided.

### Returning home

Adopt the existing house session without replacing it with the phone's away queue. If the house is playing, an unmuted phone joins that stream and a muted phone remains a silent controller.

The fresh-idle rule still applies: returning with a controller alone must not automatically start a new house track. Handling the phone's still-playing private audio at that exact idle-house boundary needs to respect that rule; do not silently promote it into a new house session.

### Reconnection status

Begin reconnecting when a failure is detected, not only after a buffer empties. Distinguish a failed control connection from failed audio reception where possible (for example, Audio reconnecting versus House server unavailable). Recovered HOUSE audio joins the current synchronized position rather than playing an old backlog behind the other rooms.

The short HOUSE synchronization buffer and the existing large standalone SMB buffer serve different purposes. Buffer drain duration and gapless recovery are not established by these behavior decisions.

## 9. Implementation notes and unresolved edges

The rules above describe the desired product, not a completed implementation. Proposed engineering details must remain distinguishable from user decisions.

The Pi service will need to track controller presence and renderer presence/output state separately, retain the active queue, preserve default shuffle progress durably, and distinguish a pending finish-track stop from a paused retained session. A stale TCP socket alone must not be treated as proof that a powered-off node is still present. Heartbeats, disconnect grace periods, and storage format have not been chosen.

Controller arrival must not be used as a blanket override of explicit transport commands. **Passive-radio arrival is the deliberate exception:** powering on a passive radio should resume an existing paused MPD session, including a deliberate Pause, rather than remaining silent or replacing the queue.

Still to settle before coding the affected edges:

- What happens when the last muted controller disconnects from an already auto-paused session: silently finish that retained track or end the session without advancing it? The discussed finish-current-track case involved music that was still playing.
- Precisely when a background mobile app or open browser counts as a connected controller; how missed heartbeats and brief network interruptions are handled.
- Initial/default output-mute setting, and whole-queue continuation for the phone away from home.
- How additions/removals in `MP3s` are reconciled with a saved shuffle cycle, and exact recovery behavior after a Pi restart.

These gaps do not cancel the confirmed rules. They are intentionally not filled with invented decisions. No runtime code or Pi configuration is changed by recording this document.


## 10. Current proven passive-renderer behavior

The following implementation facts are now proven on the permanent Pi and real hardware; they do not replace the normative rules above:

- a passive ESP32 radio powering on into fresh idle automatically starts the default house music with no phone/controller required;
- a passive radio joining an already-playing session joins the same song rather than rebuilding/restarting the queue;
- after a hard power-off lasting more than ten seconds, a renderer can power back on and rejoin the still-active song; one observed return to audible output took about six seconds;
- two independent XIAO ESP32-S3 + PCM5102A renderers have produced audibly synchronized output through different downstream amplifier/speaker systems;
- passive-radio arrival now resumes an existing paused MPD session. This was field-proven after a leave-and-return case where both radios were present but v0.5.0 had left MPD paused and silent;
- effective renderer presence uses Snapcast `lastSeen` freshness, not the raw Snapserver `connected` flag, because a hard-powered-off node can leave a stale TCP connection looking established.

An unresolved reliability issue remains: occasional few-second silence has been heard on one renderer or the other during two-node playback. Both nodes have external antennas installed. The cause is not yet known; v0.6.0 adds unattended diagnostics so the next occurrence can be correlated with Snapcast timing/presence and global stream state.


## 11. Proven final-track boundary behavior

The real MPD 0.24 implementation adds one important detail to the normative "finish current track, then stop/idle" rule:

- while the final-renderer departure policy is armed with `single oneshot`, MPD may complete the current track and retain the queue as **Pause at 0.0 seconds on the next track**, rather than reporting transport `stop`;
- that boundary state still means the previous listening session has drained successfully;
- when a passive radio later appears, its power-on intent resumes the retained next-track position;
- the server must restore the temporary Repeat/Single overrides before resuming;
- it must not mistake the retained boundary pause for an explicit controller Pause that should remain silent.

This edge was discovered from the v0.6 diagnostics and fixed in v0.6.1. Installing v0.6.1 with the previously silent passive S3 still present caused music to resume automatically, proving the behavior on the permanent Pi.


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
- passive-radio arrival into an already-playing or retained paused session still resumes/joins that existing session rather than loading the selected default.

The existing `PASSIVE_DEFAULT_FOLDER` environment value remains a sensible install-time/default fallback, but controller changes require a runtime persisted setting rather than editing service configuration.
