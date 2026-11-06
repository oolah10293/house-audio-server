# Rio subwoofer presence and follow status (source change)

Rio's page previously displayed **Unknown — no report yet** even when its
Snapcast renderer was `connected: true`, `present: true`. Those are two
different signals:

- **Snapcast presence** indicates whether the network renderer is on the Pi
  and responding. It already exists in `GET /api/subwoofers` as a
  `renderers[].present` field, keyed by Snapcast renderer ID.
- **Subwoofer feedback** is an optional status report sent by the node
  to `POST /api/subwoofers/<id>/status`. The old Rio YAML never sent it,
  unlike the Bose subwoofer's SmartSpeaker controller.

The UI now displays **Connected — no control report** when Rio is present
but no fresh firmware status has been received, or **Offline — no control
report** when Snapcast marks it absent. It leaves Bose's acknowledged
wake/standby display unchanged.

The status API also accepts two additional, explicitly software-only
states: `audio_active` and `audio_muted`. Both are unacknowledged and
the UI clearly identifies that the amplifier remains powered. The current
20 V Rio hardware has no wired mute/standby power control. These new states
are for the companion firmware source in the
`house-audio-esp32` `feature/rio-audio-follow` branch; they must not
be presented as physical TPA3118 standby.

The Pi's existing parent-assignment, demand eligibility, 30-second
physical-standby policy for Bose, renderer presence, and media/session logic
are not changed. Rio's new firmware will mute DSP audio *immediately* when
no assigned parent is eligible, and hold state rather than infer absence
when Pi demand is unknown. Pi-based demand polling remains independent of
Home Assistant.

## Rollout

Source change only: **not deployed** to the Pi or S3 and no release was
built. Rollout order when separately authorized:
1. Update Pi web/status API so it accepts the software-only Rio status
   values.
2. Confirm the existing Rio page now shows Connected using live Snapcast
   presence, even with the old firmware.
3. Flash and validate Rio's firmware follow change when authorized;
   verify parent arrival/departure, network outages, and audible audio.
4. To play Rio with a phone instead of Motorola Radio, add the phone's
   renderer to Rio's assigned speakers. The new follow logic intentionally
   obeys saved Pi assignments.
