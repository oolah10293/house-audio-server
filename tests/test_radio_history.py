import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from radio_history import RadioHistory
from test_house_audio_server import FakeClock


class RadioHistoryTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.path = Path(self.directory.name) / 'history.json'
        self.clock = FakeClock()
        self.history = RadioHistory(self.path, self.clock, lambda: 1791638000 + self.clock())
        self.station = dict(id='rock', name='Rock radio', url='https://example.com/rock')
        self.elapsed = 0.0

    def sample(self, title='Song A', advance=0, transport='play', error=None, elapsed=None, artist='Artist'):
        self.clock.advance(advance)
        self.elapsed = self.elapsed + advance if elapsed is None else elapsed
        self.history.observe(self.station, dict(transport=transport, error=error, elapsedSeconds=self.elapsed,
                             song=dict(title=title, artist=artist, album='Album')), 'play')

    def play(self, title='Song A', seconds=11):
        self.sample(title)
        for _ in range(seconds):
            self.sample(title, 1)

    def previous(self):
        return self.history.snapshot()['lastPlayed']

    def test_strict_threshold_and_previous_does_not_become_current(self):
        for duration in (9, 10, 11):
            with self.subTest(duration=duration):
                self.history = RadioHistory(clock=self.clock)
                self.play(seconds=duration)
                self.assertIsNone(self.previous())
                self.sample('Song B', 1)
                self.assertEqual(self.previous()['title'] if self.previous() else None,
                                 'Song A' if duration > 10 else None)

    def test_short_and_blank_metadata_never_erase_previous(self):
        self.play()
        self.play('Song B', 3)
        self.sample('Song C', 1)
        self.sample(' - ', 1)
        self.assertEqual(self.previous()['title'], 'Song A')
        self.play('Song D', 11)
        self.assertEqual(self.previous()['title'], 'Song A')
        self.sample('Song E', 1)
        self.assertEqual(self.previous()['title'], 'Song D')

    def test_duplicates_and_no_progress_do_not_qualify(self):
        self.sample()
        for _ in range(30):
            self.sample()
            self.sample(advance=1, elapsed=0)
        self.history.end()
        self.assertIsNone(self.previous())

    def test_pause_stop_errors_reset_accounting_baseline(self):
        for kind in ('pause', 'stop', 'error'):
            with self.subTest(kind=kind):
                self.history = RadioHistory(clock=self.clock)
                self.play(seconds=5)
                for _ in range(20):
                    self.sample(advance=1, transport=kind if kind != 'error' else 'play',
                                error='connection lost' if kind == 'error' else None)
                self.sample(advance=1)  # first resumed sample establishes a baseline
                for _ in range(5):
                    self.sample(advance=1)
                self.history.end()
                self.assertIsNone(self.previous())

    def test_long_observation_gap_elapsed_reset_and_buffer_catchup_do_not_count(self):
        self.play(seconds=5)
        self.sample(advance=60)
        self.sample(advance=1, elapsed=0)  # reconnect reset
        self.sample(advance=1, elapsed=100)  # buffer/counter jump
        self.history.end()
        self.assertIsNone(self.previous())

    def test_suspend_drops_reconnect_interval(self):
        self.play(seconds=10)
        self.history.suspend()
        self.sample(advance=2)
        self.history.end()
        self.assertIsNone(self.previous())

    def test_station_change_promotes_outgoing_even_with_same_song(self):
        self.play()
        self.station = dict(id='other', name='Other station', url='https://example.com/other')
        self.sample(advance=1)
        self.assertEqual(self.previous()['station']['name'], 'Rock radio')

    def test_unidentified_station_metadata_is_not_a_song(self):
        for title in ('', ' - ', 'unknown', 'Rock radio', None):
            with self.subTest(title=title):
                self.play(title)
                self.history.end()
                self.assertIsNone(self.previous())

    def test_combined_title_and_artist_changes_are_preserved(self):
        self.play('Collective Soul - Where The River Flows')
        self.sample('Collective Soul - Where The River Flows', 1, artist='Different supplied artist')
        self.assertEqual(self.previous()['title'], 'Collective Soul - Where The River Flows')
        self.assertEqual(self.previous()['artist'], 'Artist')

    def test_qualified_current_is_checkpointed_once_and_promoted_on_restart(self):
        with patch.object(self.history, '_save', wraps=self.history._save) as save:
            self.play()
            disk = self.path.read_bytes()
            for _ in range(10):
                self.sample(advance=1)
            self.assertEqual(disk, self.path.read_bytes())
        self.assertIsNone(self.previous())
        restarted = RadioHistory(self.path, self.clock)
        self.assertEqual(restarted.snapshot()['lastPlayed']['title'], 'Song A')
        self.assertIsNone(restarted.current)
        self.assertIsNone(restarted.anchor)
        restarted.end()  # flush promotion, never restore playback intent
        self.assertIsNone(json.loads(self.path.read_text())['qualifiedCurrent'])

    def test_completed_record_survives_restart_and_snapshot_is_detached(self):
        self.play()
        self.history.end()
        recovered = RadioHistory(self.path)
        record = recovered.snapshot()['lastPlayed']
        self.assertEqual(record['album'], 'Album')
        self.assertGreater(record['playedSeconds'], 10)
        record['station']['name'] = 'changed'
        self.assertEqual(recovered.snapshot()['lastPlayed']['station']['name'], 'Rock radio')

    def test_disk_failure_keeps_record_in_memory_and_retries_without_stopping_audio(self):
        with patch('radio_history.os.replace', side_effect=PermissionError('test read-only')):
            self.play()
            self.history.end()
        self.assertEqual(self.previous()['title'], 'Song A')
        self.assertIn('Could not save', self.history.snapshot()['persistenceError'])
        self.clock.advance(5)
        self.history.end()
        self.assertIsNone(self.history.snapshot()['persistenceError'])
        self.assertEqual(RadioHistory(self.path).snapshot()['lastPlayed']['title'], 'Song A')
        self.assertEqual(list(self.path.parent.glob('.radio-history-*')), [])

    def test_corrupt_history_is_nonfatal_and_reported(self):
        self.path.write_text('{bad json')
        history = RadioHistory(self.path)
        self.assertIsNone(history.snapshot()['lastPlayed'])
        self.assertIn('Could not read', history.snapshot()['persistenceError'])
