"""Regression coverage for bounded MPD command lists and startup stage timing."""
import contextlib
import io
import socket
import threading
import unittest

import house_audio_server as h


class RecordingMpd:
    """Minimal loopback MPD protocol peer; no real MPD process or music needed."""

    def __init__(self, reject_batch=None):
        self.reject_batch = reject_batch
        self.batches = []
        self.listener = socket.socket()
        self.listener.bind(("127.0.0.1", 0))
        self.listener.listen(1)
        self.port = self.listener.getsockname()[1]
        self.thread = threading.Thread(target=self.serve, daemon=True)
        self.thread.start()

    def serve(self):
        try:
            conn, _ = self.listener.accept()
            with conn:
                conn.sendall(b"OK MPD 0.24.4\n")
                stream = conn.makefile("rb", buffering=0)
                active = None
                while True:
                    raw = stream.readline()
                    if not raw:
                        return
                    command = raw.decode("utf-8").rstrip("\n")
                    if command == "command_list_begin":
                        if active is not None:
                            raise AssertionError("Nested command list")
                        active = []
                    elif command == "command_list_end":
                        if active is None:
                            raise AssertionError("End without begin")
                        self.batches.append(active)
                        if self.reject_batch == len(self.batches):
                            conn.sendall(b"ACK [50@0] {add} simulated add failure\n")
                            return
                        conn.sendall(b"OK\n")
                        active = None
                    else:
                        if active is None:
                            raise AssertionError("Non-batched MPD command")
                        active.append(command)
        finally:
            self.listener.close()

    def close(self):
        self.thread.join(timeout=2)
        if self.thread.is_alive():
            raise AssertionError("MPD test server did not exit")


class MpdBatchTests(unittest.TestCase):
    def test_order_preserved_across_bounded_batches_and_single_connection(self):
        peer = RecordingMpd()
        commands = ["random 0", "clear"]
        commands += [f'add "MP3s/Song {i:03d}.mp3"' for i in range(60)]
        commands += ["repeat 1", "single 0", "consume 0", "random 1", "play 0"]
        acks = []
        client = h.MpdClient(host="127.0.0.1", port=peer.port)
        client.run_batched(*commands, max_batch_bytes=190,
                           on_batch=lambda done, total: acks.append((done, total)))
        peer.close()
        self.assertGreater(len(peer.batches), 2)
        self.assertEqual([cmd for batch in peer.batches for cmd in batch], commands)
        self.assertEqual(acks[-1], (len(commands), len(commands)))
        self.assertEqual([n for n, _ in acks], sorted(n for n, _ in acks))
        self.assertTrue(all(0 < n <= len(commands) for n, _ in acks))

    def test_ack_failure_stops_later_batches_without_replay(self):
        peer = RecordingMpd(reject_batch=2)
        commands = [f'add "MP3s/{i:03d}.mp3"' for i in range(40)]
        client = h.MpdClient(host="127.0.0.1", port=peer.port)
        with self.assertRaisesRegex(h.MpdError, "simulated add failure"):
            client.run_batched(*commands, max_batch_bytes=90)
        peer.close()
        self.assertEqual(len(peer.batches), 2)
        self.assertLess(sum(map(len, peer.batches)), len(commands))

    def test_reject_newline_commands_and_size_limit(self):
        peer = RecordingMpd()
        client = h.MpdClient(host="127.0.0.1", port=peer.port)
        with self.assertRaisesRegex(ValueError, "newline"):
            client.run_batched("play\nstop")
        peer.close()


class StartupTimelineTests(unittest.TestCase):
    def test_events_are_monotonic_and_stop_at_observed_stream(self):
        trace = h.StartupTimeline()
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            trace.begin("MP3s")
            trace.mark("mpd_library_query_completed", tracks=12)
            trace.observe_streams([{"id": "default", "status": "playing"}])
            trace.await_stream()
            trace.observe_streams([{"id": "default", "status": "idle"}])
            trace.observe_streams([{"id": "default", "status": "playing"}])
            trace.observe_streams([{"id": "default", "status": "playing"}])
        lines = output.getvalue().splitlines()
        self.assertEqual(len(lines), 4)
        self.assertIn("fresh_passive_session_detected", lines[0])
        self.assertIn("tracks=12", lines[1])
        self.assertIn("waiting_for_snapserver_stream", lines[2])
        self.assertIn("snapserver_stream_playing_observed", lines[3])
        self.assertTrue(all("elapsed_ms=" in line and "delta_ms=" in line for line in lines))


if __name__ == "__main__":
    unittest.main()
