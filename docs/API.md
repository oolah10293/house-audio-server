# HTTP control API

Current service version: **0.2.0**

This is the first usable MPD control layer for the house-audio project. It is intentionally small and exposes only allowlisted operations.

The service listens on port `8787` by default and talks to MPD locally on `127.0.0.1:6600`.

## Read endpoints

### `GET /health`

Reports service identity and whether MPD is reachable.

### `GET /state`

Returns the current MPD transport/session snapshot:

- play / pause / stop state
- elapsed and duration
- volume
- Repeat / Random / Single / Consume
- queue length and version
- selected queue position/id
- current file identity and available metadata

### `GET /queue`

Returns every current MPD queue entry in order, including MPD queue position/id and available metadata.

### `GET /browse?path=<relative-path>`

Uses MPD's indexed library to list one folder.

Examples:

```text
/browse
/browse?path=Rap
/browse?path=CDs/Some%20Album
```

Returned paths are library-relative and use forward slashes.

## Write endpoints

Every successful write returns the updated MPD state unless noted otherwise.

### `POST /play`

Resume/start the selected MPD item.

### `POST /pause`

Pause MPD.

### `POST /stop`

Stop MPD.

### `POST /next`

Advance to the next MPD queue item.

### `POST /previous`

Go to the previous MPD queue item.

### `POST /seek`

JSON body:

```json
{
  "seconds": 90.5
}
```

Seeks the current MPD item to an absolute elapsed position in seconds.

### `POST /shuffle`

Maps the app's Shuffle control to MPD Random.

```json
{
  "enabled": true
}
```

### `POST /repeat`

```json
{
  "enabled": true
}
```

### `POST /queue/clear`

Clears the current MPD queue.

### `POST /queue/replace`

Atomically from the API caller's perspective, replace the current MPD queue with an ordered list of library-relative files.

```json
{
  "tracks": [
    "Rap/Track A.mp3",
    "Rap/Track B.mp3",
    "Rap/Track C.mp3"
  ],
  "startIndex": 0,
  "play": true,
  "positionSeconds": 0
}
```

Rules:

- `tracks` is required and must be a non-empty array of relative MPD-library paths.
- `startIndex` defaults to `0`.
- `play` defaults to `true`.
- `positionSeconds` defaults to `0` and is applied only when `play` is true.
- absolute paths, backslashes, and `..` traversal are rejected.
- the API never accepts arbitrary MPD protocol commands from a client.

This endpoint is the intended primitive for Android/Windows `PLAY LIST`, search-filtered queues, selected-track-first rotation, and deliberate sorted-queue replacement.

## Current boundaries

Implemented now:

- health/state
- queue inspection
- folder/library browsing
- basic transport
- seek
- Shuffle/Repeat
- queue clear/replace

Not implemented yet:

- Snapserver client presence
- passive-node auto-start
- final-node finish-current-track logic
- muted-controller pause/retain behavior
- persistent default `MP3s` shuffle rotation
- controller attach/heartbeat/detach
- local renderer mute state
- Android/Windows authentication/pairing
- push state feed

For now, controllers can poll `/state`; the Android design intentionally does not require WebSockets for the first integration.
