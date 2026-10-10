"""Regression from the user's 2026-10-10 WXDX/Blue Monday screenshot."""
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import house_audio_server as h
from radio_history import RadioHistory
from radio_metadata import normalize_radio_metadata
from test_house_audio_server import FakeClock


# Literal supplied shape: nested quotes in url's value are deliberately NOT
# valid JSON or ordinary CSV. Tracking IDs are irrelevant to song identity.
WXDX = ('title="Blue Monday",artist="ORGY",url="song_spot="F" '
        'MediaBaseId="0" itunesTrackId="0" amgTrackId="-1" amgArtistId="0" '
        'TAID="0" TPID="1054833" cartcutId="0" amgArtworkURL="null" '
        'length="00:04:22" unsID="-1" '
        'spotInstanceId="5adc1099-7acf-4396-a115-d7d965f6d713""')
URL = 'https://stream.revma.ihrhls.com/zc2033'
STATION = dict(id='wxdx', name='105.9 The X', url=URL)


class RadioMetadataTests(unittest.TestCase):
    def test_screenshot_payload_becomes_separate_title_and_artist(self):
        original = dict(title=WXDX, artist=None, album=None, stationName='105.9 The X')
        song = normalize_radio_metadata(original)
        self.assertEqual(song['title'], 'Blue Monday')
        self.assertEqual(song['artist'], 'ORGY')
        self.assertEqual(song['album'], '')
        self.assertEqual(song['stationName'], '105.9 The X')
        self.assertEqual(song['rawTitle'], WXDX)
        self.assertEqual(original['title'], WXDX)

    def test_mpd_state_and_queue_expose_normalized_fields(self):
        mpd = h.MpdClient()
        status = ['state: play', 'songid: 17', 'playlist: 7', 'playlistlength: 1', 'elapsed: 12.0']
        current = ['file: '+URL, 'Id: 17', 'Title: '+WXDX, 'Name: 105.9 The X']
        with patch.object(mpd, 'run', return_value=('0.24.0', [h.MpdResponse(status), h.MpdResponse(current)])):
            song = mpd.state()['song']
        self.assertEqual(song['title'], 'Blue Monday')
        self.assertEqual(song['artist'], 'ORGY')
        with patch.object(mpd, 'run', return_value=('0.24.0', [h.MpdResponse(current)])):
            song = mpd.queue()[0]
        self.assertEqual(song['title'], 'Blue Monday')
        self.assertEqual(song['artist'], 'ORGY')

    def test_local_metadata_and_plain_radio_titles_remain_unchanged(self):
        local = h.normalize_song({'file':'MP3s/example.mp3', 'title':WXDX, 'artist':'Local artist'})
        self.assertEqual(local['title'], WXDX)
        self.assertEqual(local['artist'], 'Local artist')
        for title in ('ORGY - Blue Monday', 'Love, Hate and Everything', 'Song title="a lyric"', '', None):
            song = dict(title=title, artist='Artist', album='Album')
            self.assertEqual(normalize_radio_metadata(song), song)

    def test_explicit_mpd_fields_win_and_album_is_supported(self):
        raw = 'title="Song",artist="Embedded artist",album="Embedded album",url="tracking"'
        song = normalize_radio_metadata(dict(title=raw, artist='MPD artist', album='MPD album'))
        self.assertEqual((song['title'], song['artist'], song['album']), ('Song','MPD artist','MPD album'))
        song = normalize_radio_metadata(dict(title=raw))
        self.assertEqual(song['album'], 'Embedded album')

    def test_quotes_commas_apostrophes_and_unicode_survive(self):
        cases = [
            ('title="Hello, Goodbye",artist="Band"', 'Hello, Goodbye', 'Band'),
            ('title="She Said \\"Hello\\"",artist="Band"', 'She Said "Hello"', 'Band'),
            ('title="She Said ""Hello""",artist="Band"', 'She Said "Hello"', 'Band'),
            ('title="She Said "Hello"",artist="Band"', 'She Said "Hello"', 'Band'),
            ('title="Don\'t Stop",artist="Guns N\' Roses"', "Don't Stop", "Guns N' Roses"),
            (' Artist = "Björk" ; TITLE = "Jóga" ; url="tail"', 'Jóga', 'Björk'),
            ("title='Don\\'t Stop',artist='Band'", "Don't Stop", 'Band'),
        ]
        for raw,title,artist in cases:
            with self.subTest(raw=raw):
                song = normalize_radio_metadata(dict(title=raw))
                self.assertEqual((song['title'],song['artist']), (title,artist))

    def test_missing_or_malformed_fields_do_not_display_the_protocol_dump(self):
        cases = [('title="Song",url="nested="quotes""','Song',''),
                 ('title="Song",artist="unterminated','Song',''),
                 ('title="unterminated','',''),
                 ('title="",artist="",url="song_spot="M""','',''),
                 ('artist="Band",url="stuff"','','Band'),
                 ('title="'+'x'*17000+'",artist="Band"','','')]
        for raw,title,artist in cases:
            with self.subTest(raw=raw[:100]):
                song = normalize_radio_metadata(dict(title=raw))
                self.assertEqual((song['title'],song['artist']), (title,artist))

    def test_url_tracking_changes_are_not_song_changes_and_history_stays_clean(self):
        clock = FakeClock()
        history = RadioHistory(clock=clock)
        for second in range(12):
            clock.advance(1)
            raw = WXDX.replace('1054833', str(second))
            history.observe(STATION, dict(transport='play', elapsedSeconds=second, song=dict(title=raw)))
        self.assertIsNone(history.snapshot()['lastPlayed'])
        history.observe(STATION, dict(transport='play', elapsedSeconds=12,
                        song=dict(title='title="Next song",artist="Next artist",url="opaque"')))
        last = history.snapshot()['lastPlayed']
        self.assertEqual((last['title'],last['artist']), ('Blue Monday','ORGY'))
        self.assertGreater(last['playedSeconds'],10)
        self.assertNotIn('rawTitle', last)

    def test_old_persisted_records_and_checkpoint_are_cleaned_on_read(self):
        for checkpoint in (False, True):
            with self.subTest(checkpoint=checkpoint), tempfile.TemporaryDirectory() as directory:
                path = Path(directory)/'history.json'
                record = dict(title=WXDX, artist='', album='', station=STATION,
                              startedAtEpoch=1791658800, playedSeconds=11)
                path.write_text(json.dumps(dict(version=1,lastPlayed=None if checkpoint else record,
                                qualifiedCurrent=record if checkpoint else None)))
                history = RadioHistory(path)
                last = history.snapshot()['lastPlayed']
                self.assertEqual((last['title'],last['artist']), ('Blue Monday','ORGY'))
                history.end()
                # A new qualifying record is always stored in normalized form.
                history.current = dict(last)
                history.seconds = 12
                history.end()
                self.assertNotIn('song_spot',path.read_text())

    def test_malformed_checkpoint_does_not_erase_a_valid_previous_song(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)/'history.json'
            record = dict(title='Previous song',artist='Artist',album='',station=STATION,
                          startedAtEpoch=1791658800,playedSeconds=11)
            path.write_text(json.dumps(dict(version=1,lastPlayed=record,
                            qualifiedCurrent=dict(record,title='title="broken'))))
            self.assertEqual(RadioHistory(path).snapshot()['lastPlayed']['title'],'Previous song')
