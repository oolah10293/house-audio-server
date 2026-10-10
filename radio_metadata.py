"""Normalize the structured ICY title envelope observed on WXDX.

Some streams put title="...",artist="...",url="..." inside MPD's Title tag.
The URL tail may itself contain unescaped quotes and station tracking fields;
only the leading display fields are parsed. Ordinary song titles stay intact.
"""
from __future__ import annotations

import re


_ENVELOPE = re.compile(r'^\s*(?:title|artist|album)\s*=\s*["\']', re.IGNORECASE)
_KEY = re.compile(r'\s*([a-z][a-z0-9_]*)\s*=\s*(["\'])', re.IGNORECASE)
_NEXT = re.compile(r'\s*[,;]\s*(?=[a-z][a-z0-9_]*\s*=)', re.IGNORECASE)
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
            if separator or not raw[tail:].strip():
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
    if not isinstance(raw, str) or not _ENVELOPE.match(raw):
        return song
    fields = {}
    if len(raw) <= _MAX_ENVELOPE:
        position = 0
        while position < len(raw):
            key = _KEY.match(raw, position)
            if key is None:
                break
            name = key[1].lower()
            if name not in {'title', 'artist', 'album'}:
                break  # URL and tracking data are opaque, including nested quotes.
            parsed = _quoted_value(raw, key.end(), key[2])
            if parsed is None:
                break
            value, position = parsed
            fields.setdefault(name, value)
    result = dict(song)
    result['rawTitle'] = raw
    result['title'] = fields.get('title', '')
    for key in ('artist', 'album'):
        existing = song.get(key)
        if not isinstance(existing, str) or not existing.strip():
            result[key] = fields.get(key, '')
    return result
