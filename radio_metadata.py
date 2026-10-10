"""Normalize structured ICY titles observed on WXDX/iHeart streams.

Some streams put title="...",artist="...",url="..." inside MPD's Title tag.
The URL tail may itself contain unescaped quotes and station tracking fields;
only display fields and the content marker are used. Another format embeds
space-separated text="..." fields after an artist or station-message prefix.
Ordinary song titles stay intact.
"""
from __future__ import annotations

import re


_ENVELOPE = re.compile(r'^\s*(?:title|artist|album|text)\s*=\s*["\']', re.IGNORECASE)
_PREFIX = re.compile(r'^\s*(.*?)\s+[-|]\s+(?=text\s*=\s*["\'])', re.IGNORECASE)
_KEY = re.compile(r'\s*([a-z][a-z0-9_]*)\s*=\s*(["\'])', re.IGNORECASE)
_NEXT = re.compile(r'\s*[,;]?\s*(?=[a-z][a-z0-9_]*\s*=)', re.IGNORECASE)
_END = re.compile(r'\s*[,;]?\s*$')
_SPOT = re.compile(r'(?:^|[\s,;"\'])song_spot\s*=\s*(["\'])([a-z])\1', re.IGNORECASE)
_MAX_ENVELOPE = 16384


def _quoted_value(raw, start, quote):
    """Find a field boundary, allowing escaped/doubled/ordinary internal quotes."""
    position = start
    while position < len(raw):
        if raw[position] == '\\' and position + 1 < len(raw):
            position += 2
            continue
        if raw[position] == quote:
            tail = position + 1
            separator = _NEXT.match(raw, tail)
            if separator or _END.fullmatch(raw, tail):
                value = raw[start:position]
                # Decode only quoting escapes; don't interpret arbitrary stream
                # text as JSON, Python literals, URLs or executable expressions.
                value = re.sub(r'\\([\\"\'])', r'\1', value).replace(quote * 2, quote)
                return value.strip(), separator.end() if separator else len(raw)
        position += 1
    return None


def normalize_radio_metadata(song):
    """Return normalized display fields without mutating the supplied record.

    Call only for radio/HTTP(S) streams. Explicit MPD artist/album tags win over
    embedded values. Raw data is retained in rawTitle for API troubleshooting,
    never used as a fallback display title for a recognized malformed envelope.
    """
    raw = song.get('title')
    if not isinstance(raw, str) or isinstance(song.get('rawTitle'), str):
        return song
    prefix_match = _PREFIX.match(raw)
    if not _ENVELOPE.match(raw) and prefix_match is None:
        return song
    prefix = prefix_match[1].strip() if prefix_match else ''
    fields = {}
    content_type = None
    if len(raw) <= _MAX_ENVELOPE:
        position = prefix_match.end() if prefix_match else 0
        while position < len(raw):
            key = _KEY.match(raw, position)
            if key is None:
                break
            name = key[1].lower()
            if name not in {'title', 'artist', 'album', 'text'}:
                break  # URL and tracking data are opaque, including nested quotes.
            parsed = _quoted_value(raw, key.end(), key[2])
            if parsed is None:
                break
            value, position = parsed
            fields.setdefault(name, value)
        # Read the marker only from the tracking tail, never from song lyrics
        # inside a successfully parsed title. url may contain unescaped quotes.
        spot = _SPOT.search(raw[position:])
        if spot:
            marker = spot[2].upper()
            if marker in {'T', 'O'}:
                content_type = 'nonMusic'
            elif marker in {'M', 'F'}:
                content_type = 'music'
    result = dict(song)
    result['rawTitle'] = raw
    result['title'] = fields.get('title', fields.get('text', ''))
    for key in ('artist', 'album'):
        existing = song.get(key)
        if not isinstance(existing, str) or not existing.strip():
            result[key] = fields.get(key, prefix if key == 'artist' else '')
    if content_type is not None:
        result['radioContentType'] = content_type
    if content_type == 'nonMusic':
        # The prefix is a station slogan/program message, not a musical artist.
        result['title'] = prefix or result['title']
        result['artist'] = ''
        result['album'] = ''
        result['albumArtist'] = ''
    return result
