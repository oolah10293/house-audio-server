"""Listening totals measure intervals with advancing MPD and normal output."""
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import house_audio_server as h
import test_radio
from radio_listening import RadioListening
from subwoofers import Subwoofers
from test_house_audio_server import FakeClock


class ListeningTests(unittest.TestCase):
    def setUp(self):
        self.clock = FakeClock()
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.path = Path(directory.name) / 'listening.json'
        self.counter = RadioListening(self.path, clock=self.clock, wall_clock=lambda: 1234)
        self.station = {'id': 'station-a', 'name': 'Rock'}
        self.state = dict(transport='play', elapsedSeconds=0, songId=1, queueVersion=2)
        self.counter.observe(self.station, self.state, True)

    def advance(self, seconds=1, elapsed=None, audible=True, intent='play'):
        self.clock.advance(seconds)
        self.state['elapsedSeconds'] += seconds if elapsed is None else elapsed
        self.counter.observe(self.station, self.state, audible, intent)

    def total(self):
        return self.counter.totals.get(self.station['id'], 0)

    def test_advancing_time_counts_once_and_ignores_song_metadata(self):
        for n in range(20):
            self.state['song'] = {'title': 'Different metadata ' + str(n)}
            self.advance()
        self.assertEqual(self.total(), 20)

    def test_stalls_poll_gaps_seeks_and_queue_changes_do_not_add_time(self):
        self.advance()
        for seconds, elapsed in [(1, 0), (1, 0), (10, 10), (1, -5), (1, 30)]:
            self.advance(seconds, elapsed)
            self.assertEqual(self.total(), 1)
        self.state['queueVersion'] += 1
        self.advance(); self.assertEqual(self.total(), 1)
        self.advance(); self.assertEqual(self.total(), 2)
        self.state['songId'] += 1
        self.advance(); self.assertEqual(self.total(), 2)

    def test_pause_error_mute_and_no_output_break_interval(self):
        for change, audible, intent in [({'transport': 'pause'}, True, 'play'),
                ({'transport': 'stop'}, True, 'play'), ({'error': 'offline'}, True, 'play'),
                ({'volume': 0}, True, 'play'), ({}, False, 'play'), ({}, True, 'pause')]:
            with self.subTest(change=change, audible=audible, intent=intent):
                before = self.total()
                self.state.update(change)
                self.advance(audible=audible, intent=intent)
                self.state.update(transport='play', error=None, volume=100)
                self.advance()  # Cannot credit the unknown recovery interval.
                self.assertEqual(self.total(), before)
                self.advance()
                self.assertEqual(self.total(), before + 1)

    def test_station_switch_and_library_return_cannot_bridge_intervals(self):
        self.advance()
        self.station = dict(id='station-b', name='Other')
        self.advance(); self.assertEqual(self.total(), 0)
        self.advance(); self.assertEqual(self.total(), 1)
        self.counter.observe(None, self.state, True)
        self.advance(); self.assertEqual(self.total(), 1)

    def test_periodic_save_restart_rename_and_rank_with_alphabetic_ties(self):
        for _ in range(30): self.advance()
        restored = RadioListening(self.path, clock=self.clock)
        self.assertEqual(restored.totals, {'station-a': 30})
        self.assertEqual(restored.started, 1234)
        rows = restored.rank([dict(self.station, name='Renamed'), dict(id='b',name='Zulu'), dict(id='c',name='alpha')])
        self.assertEqual([r['name'] for r in rows], ['Renamed', 'alpha', 'Zulu'])
        self.assertEqual(rows[0]['listeningSeconds'], 30)
        self.assertIsNone(restored.anchor)
        restored.remove('station-a')
        self.assertEqual(RadioListening(self.path).totals, {})

    def test_failed_save_retries_and_preserves_previous_file(self):
        self.advance(); self.counter.suspend(flush=True)
        previous = self.path.read_bytes()
        self.advance(); self.advance()
        with patch('state_file.os.replace', side_effect=OSError('disk full')):
            self.counter.save(force=True)
        self.assertEqual(self.path.read_bytes(), previous)
        self.assertTrue(self.counter.dirty)
        self.assertIn('disk full', self.counter.snapshot()['persistenceError'])
        self.clock.advance(30); self.counter.save()
        self.assertIsNone(self.counter.error)
        self.assertEqual(RadioListening(self.path).totals, {'station-a': 2})

    def test_corrupt_totals_are_not_overwritten_and_do_not_block_playback(self):
        for text in ('{broken', json.dumps({'version':1, 'trackingStartedAtEpoch':1234,
                                         'totals': {'a':10**400}})):
            with self.subTest(text=text[:40]):
                self.path.write_text(text)
                broken = RadioListening(self.path, clock=self.clock)
                self.assertTrue(broken.read_only)
                broken.observe(self.station, self.state, True)
                self.clock.advance(1); self.state['elapsedSeconds'] += 1
                broken.observe(self.station, self.state, True)
                broken.save(force=True)
                self.assertEqual(self.path.read_text(), text)

    def test_service_sigterm_flushes_unsaved_listening_time(self):
        # Exercise the real main/signal path without touching MPD or Snapserver.
        code = '''
import sys
import house_audio_server as h
from radio_listening import RadioListening
h.HTTP_BIND, h.HTTP_PORT = "127.0.0.1", 0
h.SNAPCAST_MONITOR.start = lambda: None
h.PASSIVE_SESSION_POLICY.start = lambda: None
clock = [0]
h.RADIO.listening = RadioListening(sys.argv[1], clock=lambda: clock[0])
station = {"id":"test"}
h.RADIO.listening.observe(station, {"transport":"play","elapsedSeconds":0}, True)
clock[0] = 2
h.RADIO.listening.observe(station, {"transport":"play","elapsedSeconds":2}, True)
h.main()
'''
        process = subprocess.Popen([sys.executable, '-u', '-c', code, str(self.path)],
                                   cwd=Path(h.__file__).parent, stdout=subprocess.PIPE,
                                   stderr=subprocess.PIPE, text=True)
        try:
            self.assertIn('listening on', process.stdout.readline())
            self.assertFalse(self.path.exists())  # Not yet at the periodic save.
            process.terminate()
            output, error = process.communicate(timeout=5)
            self.assertEqual(process.returncode, 0, output + error)
            self.assertEqual(RadioListening(self.path).totals, {'test': 2})
        finally:
            if process.poll() is None:
                process.kill()
                process.communicate()


class ListeningHttpTests(unittest.TestCase):
    request = test_radio.RadioHttpTests.request
    add_station = test_radio.RadioHttpTests.add_station
    play_radio = test_radio.RadioHttpTests.play_radio

    def setUp(self):
        test_radio.RadioHttpTests.setUp(self)
        self.path = Path(self.directory.name) / 'listening.json'
        self.radio.listening = RadioListening(self.path, clock=self.clock)
        self.subs = Subwoofers(clock=self.clock)
        self.controllers.subwoofers = self.subs
        self.monitor.set('bose')
        self.monitor.set('kitchen')
        self.subs.save(dict(name='Bose', rendererId='bose', parentRendererIds=['kitchen']),
                       self.monitor.snapshot())

    def advance(self, seconds=1):
        for _ in range(seconds):
            self.clock.advance(1)
            self.mpd.state_data['elapsedSeconds'] += 1
            self.policy._tick()

    def total(self, station):
        return self.radio.listening.totals.get(station['id'], 0)

    def test_phone_closed_background_counts_once_with_multiple_outputs(self):
        station = self.play_radio()
        self.monitor.set('garage')
        self.policy._tick(); self.advance(12)
        self.assertEqual(self.total(station), 12)
        self.assertEqual(self.controllers.snapshot(self.monitor.snapshot())['controllerCount'], 0)
        self.monitor.set('kitchen', present=False)
        self.advance(4)
        self.assertEqual(self.total(station), 16)

    def test_disabled_presence_policy_still_tracks_but_sub_only_never_counts(self):
        self.policy.enabled = False
        station = self.play_radio()
        self.policy._tick(); self.advance(2)
        self.monitor.set('kitchen', present=False)
        self.advance(12)
        self.assertEqual(self.total(station), 2)
        self.assertEqual(self.mpd.state_data['transport'], 'play')

    def test_unavailable_snapserver_or_mpd_cannot_credit_outage(self):
        station = self.play_radio()
        self.policy._tick(); self.advance(2)
        self.monitor.reachable = False
        self.advance(12)
        self.monitor.reachable = True
        self.advance()
        self.assertEqual(self.total(station), 2)
        self.advance()
        with patch.object(self.mpd, 'state', side_effect=h.MpdError('offline')):
            self.advance(5)
        self.advance()
        self.assertEqual(self.total(station), 3)
        self.advance(); self.assertEqual(self.total(station), 4)

    def test_output_volume_and_controller_mute_stop_counting(self):
        station = self.play_radio()
        self.policy.enabled = False
        self.policy._tick(); self.advance(2)
        self.monitor.nodes['kitchen']['volumePercent'] = 0
        self.advance(4)
        self.assertEqual(self.total(station), 2)
        self.monitor.set('kitchen', present=False)
        self.monitor.set('phone-audio')
        lease = self.controllers.attach(dict(controllerId='phone',rendererId='phone-audio',
                                            outputMuted=True,outputReady=True))
        self.advance(4); self.assertEqual(self.total(station), 2)
        self.controllers.heartbeat(dict(controllerId='phone',leaseId=lease['leaseId'],sequence=1,
                                       outputMuted=False,outputReady=True))
        self.advance(3); self.assertEqual(self.total(station), 4)
        self.mpd.state_data['volume'] = 0
        self.advance(3); self.assertEqual(self.total(station), 4)

    def test_api_ranking_rename_delete_and_return_to_library(self):
        zulu = self.add_station(name='Zulu')
        alpha = self.add_station(url=test_radio.ALT_URL, name='Alpha')
        code, body = self.request('/radio/stations')
        self.assertEqual([s['id'] for s in body['stations']], [alpha['id'], zulu['id']])
        self.play_radio(zulu); self.policy._tick(); self.advance(4)
        code, body = self.request('/radio/stations')
        self.assertEqual(body['sort'], 'listening_time')
        self.assertEqual(body['stations'][0]['id'], zulu['id'])
        self.assertEqual(body['stations'][0]['listeningSeconds'], 4)
        code, _ = self.request('/radio/stations/rename', {'stationId':zulu['id'], 'name':'Renamed'})
        self.assertEqual(code, 200)
        self.play_radio(alpha); self.policy._tick(); self.advance(2)
        self.assertEqual(RadioListening(self.path).totals[zulu['id']], 4)
        code, _ = self.request('/radio/stations/delete', {'stationId':alpha['id']})
        self.assertEqual(code, 200)
        self.advance(4)
        self.assertNotIn(alpha['id'], self.radio.listening.totals)
        self.assertEqual(self.total(zulu), 4)


if __name__ == '__main__':
    unittest.main()
