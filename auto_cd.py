"""Automatic, event-driven CD takeover for the single House Music MPD session.

Only the server writes MPD. The ESP32 sends identity/medium events via the
existing gateway; ordinary heartbeats and brief missing media never restart
track one. Call tick() under the global MPD write lock, after startup reset.
"""
import time

from input_sources import InputGatewayError


class AutoCdSource:
    def __init__(self, gateway, clock=time.monotonic, poll_seconds=3.0):
        self.gateway = gateway
        self.clock = clock
        self.poll_seconds = poll_seconds
        self.last_poll = None
        self.seen = {}             # sourceId -> (bootSession, discRevision)
        self.active = None         # (sourceId, bootSession, discRevision)
        self.prior_radio = None    # station selected before automatic takeover
        self.prior_transport = None
        self.disc_ejected = False
        self.last_error = None
        self.last_action = "waiting_for_cd"

    @staticmethod
    def token(source):
        if not isinstance(source, dict) or source.get("kind") != "cd":
            return None
        sid, boot, rev = source.get("sourceId"), source.get("bootSession"), source.get("discRevision")
        if not (isinstance(sid, str) and isinstance(boot, str) and len(boot) == 16
                and all(c in "0123456789abcdef" for c in boot)
                and type(rev) is int and 0 < rev <= 0xffffffff):
            return None
        return sid, boot, rev

    @staticmethod
    def ready(source):
        return (source is not None and source.get("available") is True
                and source.get("status") == "ready"
                and isinstance(source.get("tracks"), list) and bool(source["tracks"]))

    def snapshot(self):
        return {"enabled": True, "active": self.active is not None,
                "sourceId": self.active[0] if self.active is not None else None,
                "discEjected": self.disc_ejected,
                "lastAction": self.last_action, "lastError": self.last_error}

    def _end_ownership(self):
        self.active = None
        self.prior_radio = None
        self.prior_transport = None
        self.disc_ejected = False

    def _restore(self, mpd, radio, policy, presence):
        # Called only while the selected CD queue is still ours. Never
        # overwrite another writer's intentional source selection.
        saved_radio = self.prior_radio
        previous_transport = self.prior_transport
        self._end_ownership()
        if saved_radio is not None:
            policy.explicit_transport(mpd)
            radio.select(mpd, saved_radio)
            if previous_transport == "pause":
                radio.pause(mpd)
            elif previous_transport == "stop":
                radio.stop(mpd)
            self.last_action = "cd_disconnected_returned_to_radio"
            return
        bookmark = radio.playlist_resume.view()
        if bookmark["available"] and bookmark["snapshotId"]:
            snap = policy.monitor.snapshot()
            hold = (previous_transport == "pause" or
                    (policy.enabled and bool(snap.get("reachable"))
                     and presence["controllerCount"] > 0 and presence["audibleCount"] == 0))

            def retained(state):
                policy._auto_paused_state = policy._session_identity(state)

            radio.resume_playlist(mpd, bookmark["snapshotId"],
                                  lambda: policy.explicit_transport(mpd), hold,
                                  retained if hold and previous_transport != "pause" else None)
            self.last_action = "cd_disconnected_restored_library"
        else:
            policy.explicit_transport(mpd)
            radio.release(mpd, clear=True)
            self.last_action = "cd_disconnected_stopped"

    def _play_new(self, mpd, radio, policy, source, token, preserve_previous):
        # One physical event can only claim the house once. If an MPD
        # decoder fails, do not restart it every time an ESP heartbeat arrives.
        self.seen[token[0]] = token[1:]
        if radio.playlist_resume.status == "uncertain":
            self.last_action = "cd_skipped_uncertain_restore"
            return

        radio.reconcile(mpd, mpd.state())
        if not preserve_previous and radio.station is not None and radio.station.get("kind") == "cd":
            # The user may have explicitly selected this disc already. In that
            # case leave the session alone rather than restarting track one.
            if radio.station.get("sourceId") == token[0] and not self.disc_ejected:
                self.last_action = "cd_already_manually_selected"
                return

        selected = self.gateway.cd_selection(token[0], min(source["tracks"]),
                                             source_snapshot=[source])
        if not preserve_previous:
            current = mpd.state()
            self.prior_transport = current.get("transport")
            self.prior_radio = (dict(radio.station) if radio.station is not None and
                                radio.station.get("kind") != "cd" else None)
        policy.explicit_transport(mpd)
        radio.select(mpd, selected)
        self.active = token
        self.disc_ejected = False
        self.last_action = "cd_new_disc_started" if preserve_previous else "cd_connected_autoplay"
        self.last_error = None

    def tick(self, mpd, radio, policy, presence):
        now = self.clock()
        if self.last_poll is not None and now - self.last_poll < self.poll_seconds:
            return
        self.last_poll = now
        if radio.playlist_resume.status == "uncertain":
            return
        try:
            sources = self.gateway.sources()
        except InputGatewayError as exc:
            # A gateway outage is not evidence that an ESP has disconnected.
            self.last_error = str(exc)
            return

        by_id = {src.get("sourceId"): src for src in sources if isinstance(src, dict)}
        if self.active is not None:
            current = by_id.get(self.active[0])
            current_token = self.token(current)
            station = radio.station
            if (station is None or station.get("kind") != "cd"
                    or station.get("sourceId") != self.active[0]
                    or not radio.reconcile(mpd, mpd.state())):
                # Explicit library/radio selection (or out-of-band MPD change)
                # defeats takeover until a genuinely new medium/boot event.
                self._end_ownership()
                self.last_action = "cd_manual_source_override"
            elif current is None or current.get("available") is False:
                self._restore(mpd, radio, policy, presence)
                return
            elif not self.ready(current):
                if not self.disc_ejected:
                    radio.eject_cd(mpd)
                    self.disc_ejected = True
                    self.last_action = "cd_ejected_waiting_for_disc"
                return
            elif current_token == self.active:
                return
            elif current_token is not None:
                # Next disc replaces the CD queue, but retains the ORIGINAL
                # library bookmark/radio source, never bookmarks another CD.
                try:
                    self._play_new(mpd, radio, policy, current, current_token, True)
                except (OSError, ValueError, InputGatewayError) as exc:
                    self.last_error = str(exc)
                    self.last_action = "cd_disc_change_failed"
                return
            return

        for source in sources:
            token = self.token(source)
            if token is None or not self.ready(source):
                continue
            if self.seen.get(token[0]) == token[1:]:
                continue
            try:
                self._play_new(mpd, radio, policy, source, token, False)
            except (OSError, ValueError, InputGatewayError) as exc:
                self.last_error = str(exc)
                self.last_action = "cd_autoplay_failed"
            return
