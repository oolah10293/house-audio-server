# Unmuted controller session retention — 2026-11-21

The user explicitly authorized implementation and testing of the server policy:
only an unmuted phone holds the house session, including while paused. A muted
phone stays connected for controls but does not keep that session open. Other
house nodes retain their existing behavior.

Base: main commit `082c9feac117741ad091ba9765a975b2d2f7dd9a`, the integrated
`0.14.2-cd1` source. This update contains source, tests and documentation;
packaging and installation were subsequently authorized for v0.14.3; see
[release status](RELEASE_0.14.3.md) for preparation and deployment evidence.

## Implementation

- Controller leases, authorization and background heartbeats remain active when
  muted. `controllerCount` still includes them. Effective `presentCount` includes
  only unmuted controllers plus passive nodes and still-audible controlled
  outputs. New `unmutedControllerCount` exposes the distinction in controller
  presence and session-policy responses. Raw Snapcast presence is unchanged.
- Receiver readiness is independent: an unmuted controller can retain a paused
  playlist while its receiver is unavailable. Audible recovery resumes only an
  automatic pause, respecting explicit Pause/Stop.
- Only muted controllers remaining during library playback arms the existing
  final-track drain. Their heartbeats/return cannot cancel it. A passive node or
  unmuted controller returning before completion can preserve that session.
- A paused controller-held session ends when no session participant remains,
  including muted-only first observations and manual Pause during a drain. MPD
  write failures retry through the existing policy loop. A later Unmute does not
  restart an ended session; passive arrival starts the configured fresh shuffle.
- Live radio releases its station and saved playlist with no session
  participants. Resume Playlist can reconstruct silently for a muted controller,
  but that controller cannot retain the resulting pause.
- A plugged-in physical CD still pauses and keeps its source/bookmark with no
  speakers, as required by its separate source policy. If removal restores a
  library while only muted controllers remain, the library session ends. Existing
  passive-only startup/recovery and CD restoration without controllers are
  preserved.

Runtime files changed: `house_audio_server.py` and `auto_cd.py`. Existing Android
mute reports supply the required state; this policy needs no app protocol change.

## Verification

- Local `python -m unittest discover -s tests`: **213 tests passed**, including
  HTTP handler tests and stateful MPD/gateway models.
- Python syntax, installer shell syntax, browser JavaScript syntax, browser
  sorting/CD-source regressions and `git diff --check` passed.
- Added coverage for manual/automatic paused phone mute, background unmuted
  retention, mixed muted/unmuted controllers and expiry, muted-only first sample,
  final-track completion despite heartbeats, later fresh shuffle, muted return
  during drain, manual-pause cleanup retry, radio release/bookmark cleanup, and
  continued transport control over other speakers from a muted phone.
- Updated earlier retention fixtures to use an unmuted, unavailable receiver
  where retention remains intended. The startup/reconnect regression now retains
  its manual pause with an unmuted phone instead of a control-only browser.
- Physical phone, Pi/MPD and speaker acceptance has not been performed for this
  change. No new package or live installation is claimed.

After installation, check a lone paused phone both unmuted and muted; switch off
the last room node with a muted phone connected; let the final track finish and
power a room node back on; verify other rooms continue when the phone is muted.
