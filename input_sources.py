"""Read-only discovery of remote House Music audio inputs through the local gateway.

Never accept client-provided stream URLs. The gateway is fixed at loopback, and
all MPD URLs are composed from validated gateway source IDs and track numbers.
"""
import json
import re
import urllib.error
import urllib.request

SOURCE_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$")


class InputGatewayError(RuntimeError):
    pass


class InputGateway:
    def __init__(self, base_url="http://127.0.0.1:8768", timeout=2.0):
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout

    def sources(self):
        try:
            request = urllib.request.Request(self.base_url + "/sources",
                                             headers={"Accept": "application/json"})
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                if response.status != 200:
                    raise InputGatewayError("Audio input gateway returned HTTP " + str(response.status))
                data = json.loads(response.read(65536))
        except (OSError, ValueError, urllib.error.URLError) as exc:
            raise InputGatewayError("Audio input gateway unavailable: " + str(exc)) from exc
        sources = data.get("sources") if isinstance(data, dict) else None
        if not isinstance(sources, list):
            raise InputGatewayError("Audio input gateway returned an invalid source list")
        return sources

    def cd_selection(self, source_id, track_number):
        if not isinstance(source_id, str) or SOURCE_ID.fullmatch(source_id) is None:
            raise ValueError("Invalid audio source ID")
        if type(track_number) is not int or not 1 <= track_number <= 99:
            raise ValueError("Invalid CD track number")
        source = next((item for item in self.sources()
                       if isinstance(item, dict) and item.get("sourceId") == source_id), None)
        if source is None or not source.get("available"):
            raise InputGatewayError("CD player is offline")
        if source.get("kind") != "cd" or source.get("status") != "ready":
            raise InputGatewayError("CD player does not have a ready audio CD")
        tracks = source.get("tracks")
        if not isinstance(tracks, list) or not tracks or len(tracks) > 99:
            raise InputGatewayError("CD player reported an invalid track list")
        if (any(type(t) is not int or t < 1 or t > 99 for t in tracks)
                or len(set(tracks)) != len(tracks) or track_number not in tracks):
            raise InputGatewayError("Requested CD track is unavailable")
        tracks = sorted(tracks)
        name = source.get("name")
        if not isinstance(name, str) or not 1 <= len(name) <= 80:
            name = "CD Player"
        urls = [f"{self.base_url}/audio/{source_id}/{track}.wav" for track in tracks]
        index = tracks.index(track_number)
        return {"kind": "cd", "id": "cd-" + source_id, "sourceId": source_id,
                "name": name, "url": urls[index], "urls": urls,
                "trackNumbers": tracks, "trackIndex": index,
                "trackNumber": track_number}
