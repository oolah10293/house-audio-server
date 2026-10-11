# Subwoofer settings and node API — v0.12.0

## Open the settings page

On a phone or computer connected to the home network, open:

**http://192.168.1.12:8787/subwoofers**

Bookmark it in your browser. The Pi serves this page directly; there is no extra
app to install and Home Assistant is not required. If the Pi address changes,
replace the address in the bookmark, keeping port `8787` and `/subwoofers`.

## Register the Bose sub

1. Power on the Bose S3 and the normal speakers it should follow. Let them connect
   to Snapserver so their device names and stable IDs appear.
2. Open the page and select **+ Add subwoofer**.
3. Enter a name, such as **Bose Sub**. Select the Bose from **Subwoofer device**;
   check the ID below/alongside the name when device names are duplicated.
4. Check one or more **Speakers that activate this sub**. These are normal outputs,
   such as a room speaker, not another sub.
5. Select **Register subwoofer**. The new card lists its assigned speakers, Pi
   demand and any node feedback. Reloading the page must retain the assignment.

Registration immediately makes that device a **dependent sub** in HOUSE session
policy. It can no longer start/resume or keep playback going by itself, even if
its current firmware keeps it connected and awake. No devices are automatically
registered or identified from a name/IP address.

**During the staged update:** old Bose firmware does not consume the new demand
or send status. It continues its existing amplifier behavior. **Unknown — no
report yet** is expected until the Bose firmware update, which is the next step.
The server alone does not switch the amplifier into standby.

## Edit the speakers or name later

1. Open the same page and expand **Edit assigned speakers** on the correct card.
2. Tick/untick speakers and optionally change the name.
3. Select **Save assignments**. Wait for **Assignments saved**.

Assignments apply without restarting the server and persist across restarts.
Status refreshes every three seconds without replacing unsaved form edits. At
least one normal speaker is required. Power on a newly added device before
selecting it. Previously saved devices remain editable if temporarily offline.
The sub ID and its own renderer ID stay fixed when renaming or editing parents.

To add another sub, repeat **+ Add subwoofer**. Multiple subs may follow the same
speaker or different sets. **Any** eligible selected speaker makes its sub needed;
all selected speakers must be absent before demand becomes not needed. One sub
cannot activate itself or another sub. The initial page supports registration
and assignment/name edits; it does not offer removal or device-ID replacement.

## Read the three separate facts

| Field | Meaning |
| --- | --- |
| Mode | Node-reported Auto, Force standby or Force awake. Manual mode controls remain on the sub's existing control surface. |
| Pi demand | Needed, Not needed, or Unknown, based on the selected normal speakers. |
| Status feedback | The latest fresh report from the sub, independent of what the Pi wants. |

Examples:

- **Standby in 24 seconds…** uses a fresh node-reported countdown. Reaching zero
  on the page means **Waiting for standby report**, not confirmed standby.
- **Standby requested** means the node reports standby without an acknowledgment.
- **Standby — acknowledged** means the node reports a validated command-acceptance
  acknowledgment. It does **not** mean measured physical amplifier power.
- **Unknown — no report yet** / **Unknown — not responding** means missing or stale
  feedback. Reports expire after 15 seconds, including previously successful ones.
- **Server not responding** means this browser cannot get current information.

An awake sub can show **Not needed** during its grace period or in Force awake.
A sub in Force standby can show **Needed**. Those combinations are valid.
No LED is needed. The later Bose update also puts status beside its existing Home
Assistant controls and preserves the existing tuning controls.

## Presence and failure rules

Demand follows effective presence, not the current music transport. A paused or
muted normal passive speaker still demands its assigned sub. A controller-only,
muted, unready or detached phone does not count as an eligible selected output.
Raw Snapcast `connected` alone is insufficient; existing stale-client checks apply.

Snapserver failure or stale monitor observations mean **Unknown**, never false
absence. In Auto the node holds its current amplifier state while retrying. At
cold boot it stays in standby until it gets valid demand. A node owns the
**30-second** grace after the last selected speaker leaves and cancels it when
one returns. Forced modes override Auto until Auto is selected again; Force awake
cannot preserve/start the HOUSE playback session.

Raw `/renderers` and `/health` diagnostics still include connected subs. Effective
controller/session counts exclude them. A zero effective normal-listener count
ends radio immediately; local music follows the existing final-track drain.

## Firmware integration contract

These are implemented server routes. Automatic amplifier control and validated
Bose acknowledgment reporting still require the separate firmware update.
All bodies are JSON objects; send `Content-Type: application/json`. These routes
use the existing trusted home-LAN model, with no new credentials or CORS. Browser
edits must originate from this server. ESP32 requests need no `Origin` header.

### Registry and discovery

`GET /api/subwoofers` returns `service`, `version`, `snapserverReachable`,
`statusStaleAfterSeconds` (15), `renderers`, and `subwoofers`.

A renderer has `id`, `name`, `present` (boolean or null when unknown), `audible`
and `dependent`. Each sub includes `{id,name,rendererId,parentRendererIds,demand,status}`.
Firmware can locate its stable sub ID by matching its own Snapcast renderer ID.
Use the ID, never a name/IP heuristic, for subsequent requests. Unknown ID gives 404.

`POST /api/subwoofers` registers using `{name,rendererId,parentRendererIds}` or edits
using the same fields plus its `id`. Success is HTTP 200 with `{service,version,subwoofer}`.
Maximum 32 subs, 256 parents per sub, 120-character names. IDs must be discovered
by Snapserver (previous assignments remain accepted offline). Renderer identity
is immutable after registration. Validation/conflicts return 400/409;
failed persistence returns 503. On an uncertain save, reload before retrying.

### Demand

`GET /api/subwoofers/{id}/demand`, for example:

```json
{
  "service": "house-audio-server",
  "version": "0.12.0",
  "id": "sub-example",
  "rendererId": "example-sub",
  "known": true,
  "needed": false,
  "matchingRendererIds": [],
  "standbyDelaySeconds": 30,
  "pollSeconds": 2,
  "validForSeconds": 10,
  "reason": "no_assigned_speakers"
}
```

These example IDs are illustrative, not the user's hardware IDs. `needed` is a
boolean only when `known` is true. Unknown responses have `known:false`,
`needed:null`, an empty matching list and `reason:"snapserver_unavailable"`.
Known true demand uses `reason:"assigned_speaker_present"`.

Firmware should poll every two seconds, validate response identity/schema and
honor the ten-second maximum freshness from receipt using its monotonic clock.
Failed requests, unknown demand, malformed responses and expired observations
must not become false absence. In Auto, discard a pending absence countdown on
unknown and hold current state. When fresh not-needed returns, begin a new full
30 seconds. While counting down, continuing fresh not-needed samples must **not**
restart the timer. Fresh needed cancels it immediately. Require fresh known
not-needed before acting at expiry. Never repeat an unchanged hardware command.
Manual overrides take precedence, and the node must keep networking powered.

### Node status

`POST /api/subwoofers/{id}/status`, for example:

```json
{
  "rendererId": "example-sub",
  "mode": "auto",
  "state": "standby_pending",
  "standbyRemainingSeconds": 24,
  "acknowledged": false,
  "detail": "Waiting after last assigned speaker left"
}
```

Required: matching `rendererId`, `mode` (`auto`, `force_standby`, `force_awake`),
`state` (`awake`, `standby`, `standby_pending`, `waking`, `unknown`, `error`).
`standby_pending` requires Auto and a finite 0–30-second remaining time; other
states must omit the countdown or use null. `acknowledged` defaults false and
can be true only for `awake`/`standby`. `detail` is optional printable text up to
240 characters. Do not put credentials or raw debug payloads in it.

Report on transitions and at least every five seconds; receive HTTP 200 with
`{service,version,status}`. The Pi timestamps receipt, expires feedback after 15
seconds, and forgets reports at server restart. No client wall-clock is trusted.
It accepts reports independently of MPD availability. It never changes demand
based on reported/forced amplifier state.

Only set `acknowledged:true` after the firmware validates a relevant hardware
response. Do not assume the existing Bose protocol proves physical power; verify
that response during the firmware work. A lost response must remain requested or
unknown/error as appropriate. This API provides no command endpoint for manual
mode; that remains a node responsibility.

## Saved files and recovery

`/var/lib/house-audio-server/subwoofers.json` holds assignments, with atomic file
replacement. The systemd service owns this state directory. The installer keeps
existing state and environment settings. The optional environment override is
`HOUSE_AUDIO_SUBWOOFERS_FILE`; custom parent directories need service write access.

Use the page for ordinary edits. Back up this file with other server settings.
Malformed/unreadable assignments deliberately fail startup rather than silently
reclassifying a dependent sub as a normal speaker. Restore a known-good backup
and restart `house-audio-server`; use `sudo journalctl -u house-audio-server -n 80 --no-pager` for diagnosis. Do not broaden file permissions to work around ownership.
