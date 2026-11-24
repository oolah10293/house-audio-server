# House Music — general Sources page and future analog inputs

**Status: Phase A (Sources webpage) explicitly authorized and implemented; phases B/C remain proposals and are NOT authorized.**

The user wants to rename the sidebar's **CD players** entry to **Sources** and
have that page provide a way to choose among CD players, an analog-to-digital
(ADC) / line input, and additional future source types. This document records
the discussed direction without changing the installed webpage, server,
gateway, firmware, or audio behavior.

## Current architecture (verified against main on 2026-11-23)

- `web/house.html` exposes the sidebar entry `CD players` with
  `data-view="inputs"`.
- `web/house.js` already fetches `GET /inputs` and saves the returned
  `sources` array, but `drawInputs()` filters it to `kind === "cd"`
  and displays track controls only for those sources.
- `input_sources.py` obtains all reported sources from the existing local
  input gateway at `http://127.0.0.1:8768/sources`. Its implemented
  `cd_selection()` accepts **only** valid ready CD sources with a selected
  numbered track.
- `pi_input/gateway.py` already validates the registered `kind` values
  `cd`, `line-in`, and `other`, and returns all registered sources through
  `GET /sources`.
- The gateway's implemented media proxy is a **CD-track WAV endpoint**
  (`/audio/<sourceId>/<track>.wav`), upstreaming
  `/track/<number>.wav`; it is not a general continuous analog-audio
  stream gateway.
- The server's `POST /inputs/play` route currently requires
  `sourceId` **and** `trackNumber`, invokes `cd_selection()`, and is
  CD-specific. `auto_cd.py` deliberately handles `kind === "cd"` physical
  events; it is not a general source auto-takeover engine.

**Conclusion:** The system already supports multi-kind **discovery/registry**.
It does **not** yet support playback for non-CD sources. Relabeling the
navigation and organizing discovered sources is a relatively contained
browser/UI change; actually playing ADC/line input requires targeted new
source-node, gateway and server integration. It is not a from-scratch
replacement of MPD, Snapserver or the house speaker network.

## Proposed user experience

1. Change the sidebar label **CD players** to **Sources**, preserving the
   existing input view/route wiring where practical.
2. A Sources page presents **actual registered devices**, categorized by their
   reported type and availability instead of hardcoded fictitious devices.
   Example groups: CD players, Analog / Line Inputs, Other.
3. Selecting a registered CD player opens the same familiar CD-track
   controls currently available; preserve the existing selection and auto
   takeover behavior.
4. A future registered ADC/line-in node appears in its own source group.
   Until streaming playback support is explicitly implemented, show its
   honest availability and **not-yet-playable** status, without a fake Play
   action.
5. Leave room for additional input device types later without expanding the
   sidebar each time. Continue to distinguish registration/discovery from
   actual playable capability.

These are design intentions rather than implemented behavior. Exact
expanded-device navigation (inline expansion versus a drill-down page) and
the visual treatment of disconnected devices can be selected when UI work
is authorized.

## Proposed incremental sequence (each stage needs separate explicit approval)

**A. Sources UI only (authorized, implemented in browser source).**
Rename the navigation/page headings, show the actual registered source
categories, and expose existing CD controls under their CD source. Preserve
CD functionality and display line-in/other as information-only until supported.
Test mixed registrations, no sources, offline devices and responsive layout.

**B. ADC / live line-in playback (additional backend and source-node work).**
Define an actual node-generated, continuously decodable audio stream.
Specify the validated gateway route and proxy format/lifetime (continuous
streams are not bounded CD-track WAV files); extend the server's explicit
selection and MPD playback path without allowing browser-supplied arbitrary
stream URLs or bypassing the existing single-session MPD lock. Preserve
switching away from live input and the preexisting queue/station bookmark
where appropriate. Test stream loss, disconnect/reconnect, stopped/paused
transport, source switching, and failure/timeout behavior.

**C. Further input types (future).**
Integrate one additional device category at a time using the existing
source identity/availability contract and type-specific playback handling.
Do not assume every source follows CD track or ADC live-stream semantics.

## Agreed ADC/CD takeover rules — latest event wins (corrected 2026-11-23)

**Design specification only — no Phase B implementation authorization.**

**There is NO fixed source-type priority.** The user's rule is:
**whichever source most recently powers on takes over House Music, regardless
of which source is currently playing. Inserting a CD is a fresh takeover event
with exactly the same precedence as power-on.** Event order determines the
winner, not whether the source is an ADC or CD player.

- **ADC powered on**: take over immediately, including if the ADC signal is
  silent or the CD player is currently playing. "Plug in" means power to the
  ADC S3, not audio-cable insertion or detected signal amplitude.
- **CD player powered on**: claim source selection even if the ADC currently
  owns playback. If there is no playable audio disc, the CD has no tracks to
  play; inserting a playable CD later is another takeover event.
- **CD inserted**: claim source selection and play the new audio disc when
  valid, including if ADC/radio/library was selected more recently. If another
  source powers on *after* the insertion, that new power-on wins.
- **Manual source choice**: changes playback immediately. Subsequent genuine
  power-on or CD-insertion events still take over; simply being present or
  sending ordinary heartbeats does not constitute a new event.
- **Silence is not an event**: a powered ADC remains selected despite no
  analog signal. A new ordinary heartbeat must not repeatedly restart it.
- **Temporary network interruptions and gateway restarts** must not
  masquerade as actual new source power-on events. Track node boot identity
  and new CD-medium identity to avoid spurious source switches.
- **Disconnect and restoration** should affect playback only if the
  disappearing node currently owns the session; do not overwrite an
  intervening manual or automatic selection. The exact fallback policy,
  including whether to restore the latest still-connected source or a saved
  library/radio bookmark, remains to be worked out separately.
- **Ejecting a disc** is not the same as inserting one. No change to the
  existing CD ejection behavior is authorized by this specification.

Examples:
1. ADC powers on → **ADC**; CD with disc powers on → **CD**; ADC is
   power-cycled afterward → **ADC**.
2. CD is playing → ADC powers on → **ADC**; new CD inserted → **CD**.
3. ADC is silent but powered → CD powers on → **CD**; ADC remains online
   and sends heartbeats → **CD stays selected**.

Current `auto_cd.py` implements CD-only event ownership and restoration.
Phase B must coordinate CD and ADC events through a single arbitration
policy rather than independent competing auto-takeover loops. No server,
gateway, web code or S3 firmware is changed by this documentation update.

## Open design decisions before adding a live input

- **Decided:** ADC S3 power-on is a takeover event, even with silent
  analog input. Source power-on and CD insertion use latest-event-wins
  arbitration, with no permanent CD priority.
- **Still open:** Precise disconnect/fallback/return policy, CD power-on
  without playable media and the user-visible waiting state, and recovery
  from an interrupted live stream without false new power-on events.
- The audio format, stream transport, buffering/synchronization and desired
  latency; verify with a real ADC node before promising performance.
- Whether selection should preserve/restore the interrupted source and
  playback position, and how to handle multiple concurrently registered
  sources.
- How to handle source type/capability reporting cleanly and securely
  rather than treating every `line-in` registration as playable.

## Boundaries and checkpoint

**Implementation checkpoint:** On 2026-11-23 the user explicitly authorized
only Phase A, the Sources webpage update. The implementation branch updates:

- `web/house.html`: navigation renamed to **Sources**, keeping `data-view="inputs"`.
- `web/house.js`: grouped registry devices under CD players, Analog / Line
  Inputs, or Other, showing categories only when actual devices exist. Click a
  CD player to expand or collapse its existing numbered track controls.
  Line-in and other devices are clearly marked **Playback not available yet**
  and are noninteractive.
- `web/house.css`: category/expanded-track styling at desktop/mobile sizes.
- `tests/test_house_browse.js`: mixed source kinds, unknown kinds, unavailable
  devices, expandable CD controls, unchanged `/inputs/play` payload and
  existing browser regressions.

GitHub Actions passed Python unit, shell syntax and browser regression checks
for the Phase A implementation branch. There has been **no field acceptance
or on-Pi installation evidence** for this new UI as of this checkpoint.

No ADC stream or other input playback has been implemented; Phase B/C still
require separate explicit authorization. No ESP32 firmware, gateway, MPD
control/session logic, physical CD behavior or audio settings changed.

Before any future Phase B/C implementation, inspect GitHub main again and
agree on that phase's specific scope, then test it independently.
