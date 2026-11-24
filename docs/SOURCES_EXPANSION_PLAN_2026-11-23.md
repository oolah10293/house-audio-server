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

## ADC node capture and transmission buffer (design recommendation, 2026-11-23)

**Record of discussion, not implementation authorization.** Include a circular
PCM ring buffer on the ESP32-S3 between I²S/DMA ADC capture and its Wi-Fi
streaming task. Capture should run independently of network writes; short
Wi-Fi stalls should not block sampling.

- **Initial suggested target:** 250 ms worth of samples, adjustable in
  firmware source for tuning after real-hardware tests. Consider 500 ms
  capacity if measurements justify it; avoid adding intentional latency
  simply to fill the entire buffer.
- **Illustrative sizing only:** 48,000 samples/sec × stereo × 16 bits per
  sample = 192,000 bytes/sec: 100 ms = 19,200 B; 250 ms = 48,000 B;
  500 ms = 96,000 B; one second = 192,000 B. Exact stream representation,
  ADC format, available heap and Wi-Fi performance remain to be verified.
- Use the S3 I²S DMA capture path and a separate transport task. Buffer
  occupancy, underruns/overruns, dropped sample counts and reconnects
  should be observable for troubleshooting.
- On overflow, don't allow an endlessly growing stream delay. Discard
  stale audio and resynchronize to the live edge rather than continuing
  to play increasingly old samples. The exact drop/resync and framing
  approach must be designed and tested to avoid corrupting stream format.
- A buffer can smooth transient Wi-Fi pauses but cannot fix sustained
  throughput below capture rate, and cannot eliminate latency introduced
  by the Pi, MPD or Snapcast.
- Keep the node's latest-power-on takeover semantics independent of
  whether the analog input is silent or buffered audio exists.

**Still unresolved:** ADC sample width/format, end-to-end transport,
effective buffered latency, memory utilization, and frame-aligned
overflow/resynchronization strategy. Confirm via hardware measurement
before treating 250 ms as final. No code, firmware, YAML, stream or server
changes are authorized by this documentation.

## ADC S3 prototype hardware — candidate design (2026-11-23)

**Proposal for discussion, NOT a final wiring authorization or permission
to modify firmware.** Hardware specification is still subject to the actual
module's PCB/pin markings and the type/level of analog output being connected.

### Components and topology

- Existing Seeed XIAO ESP32-S3 on USB 5 V power (initial firmware bootstrap
  by USB, subsequent updates by the established Pi-origin OTA method).
- One low-cost **PCM1808 24-bit stereo analog-to-I²S breakout**. Prefer a
  board with clearly labelled L/R inputs, SCK, BCK, LRC, OUT, MD0, MD1, FMT,
  GND, 5V and 3.3V pins. Avoid confusing an I²S DAC with an ADC.
- One stereo 3.5 mm input jack **or** two RCA input connectors; choose
  according to the intended analog source. No amplifier or local speaker
  output is required.
- Keep the ADC clock/data conductors short; mount the ADC near the S3 and
  keep sensitive analog leads away from the Wi-Fi antenna and fast clocks.

**Proposed preliminary signal assignment** (Seeed XIAO S3 pin mapping;
validate GPIO routing under the existing Arduino ESP32 2.0.14 build):

| PCM1808 module label | XIAO S3 pin | Meaning |
| --- | --- | --- |
| SCK / SCKI | D0 = GPIO1 | S3 master clock output |
| BCK | D1 = GPIO2 | S3 bit clock output |
| LRC / LRCK | D3 = GPIO4 | S3 left/right clock output |
| OUT / DOUT | D4 = GPIO5 | S3 I²S data input |
| +5V | USB-supplied 5V | ADC analog power |
| 3.3V | S3 3V3 | ADC digital power |
| GND | S3 GND | Common digital/analog reference |

Do not assume all breakout modules have the same onboard regulation,
coupling, mode strapping, or clock generation. Check the selected module's
actual connections before applying voltage. Configure PCM1808 in **slave
I²S mode** (nominal MD1/MD0 low, FMT low, if the board exposes these pins
and there is no conflicting onboard strap). ESP32-S3 provides all clocks:
at 48 kHz sample rate, proposed SCKI/MCLK=12.288 MHz (256×fS),
BCK=3.072 MHz (64×fS) and LRC=48 kHz. The PCM1808 outputs 24-bit
samples in 32-bit I²S slots; conversion to the house stream's
48 kHz/16-bit/stereo format can happen after I²S capture. Actual MCLK
signal and bit timing need bench verification before assuming audio works.

**Analog input:** PCM1808 full scale is about **3 Vp-p**, or
**1.06 Vrms for a sine wave**. A typical ~2 Vrms modern line output
can clip this ADC; provision for switchable approximately -6 dB
stereo attenuation on the input, verified against the selected board's
input network. Inspect AC-coupling capacitors on the chosen module before
adding duplicates. Do not connect speaker/amplifier outputs directly to
line-level ADC inputs; speaker-level capture needs a separately designed
attenuator and ground/isolation review.

**Buffer and power:** The existing 250 ms ring-buffer recommendation
represents ~48 kB if converted to 16-bit stereo at 48 kHz; raw 32-bit
capture would use ~96 kB for the same span. Keep DMA capture and Wi-Fi
transmission independent. USB 5V supply with 3V3 derived by the S3 is
the starting point; verify the particular ADC board's power consumption
and pin voltage expectations before soldering.

### Decisions and tests before implementation

1. Confirm what analog signal is being connected: fixed line-out RCA,
   headphone output, or a vintage radio's speaker-level signal.
2. Pick the **exact** ADC module/PCB; verify its wiring, ground,
   coupling, power rails and slave-mode clock strapping. Do not copy
   a generic internet pinout without matching it to that board.
3. Bench-measure 48 kHz stereo sample capture with a known signal,
   correct channels, clipping level, noise floor and stable clocking.
4. Only after hardware capture is proven, implement streaming, the agreed
   250 ms ring buffer and latest-physical-event-wins source arbitration,
   under separate explicit firmware/server implementation authorization.

Technical references: TI PCM1808 product/datasheet,
https://www.ti.com/product/PCM1808 ; Espressif ESP32-S3 I²S
documentation, https://docs.espressif.com/projects/esp-idf/en/release-v4.4/esp32s3/api-reference/peripherals/i2s.html ;
Seeed XIAO ESP32-S3 pin table,
https://wiki.seeedstudio.com/xiao_esp32s3_pin_multiplexing/.

## Open design decisions before adding a live input

- **Decided:** ADC S3 power-on is a takeover event, even with silent
  analog input. Source power-on and CD insertion use latest-event-wins
  arbitration, with no permanent CD priority.
- **Still open:** Precise disconnect/fallback/return policy, CD power-on
  without playable media and the user-visible waiting state, and recovery
  from an interrupted live stream without false new power-on events.
- **Proposed starting point:** S3 I²S DMA capture plus a 250 ms PCM
  circular transmission buffer, configurable in source; verify the actual
  latency, stream framing and network resilience on hardware. Audio format,
  transport and overflow/resync strategy are still open.
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
