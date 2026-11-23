# House Music — general Sources page and future analog inputs

**Status: proposal only, documented on 2026-11-23. No implementation authorization.**

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

**A. Sources UI only (small, browser-focused change).**
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

## Open design decisions before adding a live input

- Whether a line-in source is manually selected, automatically takes over
  on signal/activity, or supports both. **Do not inherit CD's automatic
  plug-in/disc behavior by default.**
- What should happen when the ADC source goes silent, disconnects, or
  returns, and when switching back to library, radio or CD.
- The audio format, stream transport, buffering/synchronization and desired
  latency; verify with a real ADC node before promising performance.
- Whether selection should preserve/restore the interrupted source and
  playback position, and how to handle multiple concurrently registered
  sources.
- How to handle source type/capability reporting cleanly and securely
  rather than treating every `line-in` registration as playable.

## Boundaries and checkpoint

This is **documentation only**. No HTML, CSS, JavaScript, server code,
gateway, CD node, ADC node firmware/YAML, dependencies, audio settings, or
installed Pi services have been changed. There is no authorization to
implement phase A, B, or C yet. Treat the running CD integration and House
Music v0.14.5 browser as the working baseline.

Before any future implementation, inspect GitHub main again and agree on
the specific phase and scope. Run targeted browser and server/gateway
regressions and verify physical playback separately after authorized
packaging/deployment.
