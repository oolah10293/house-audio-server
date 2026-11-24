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
to modify firmware.** The user has now confirmed **line-level audio outputs**
and shown the exact purple PCM1808 breakout listing. Wiring, coupling
and mode straps remain subject to physical PCB verification.

### Bench-build checklist — consolidated as discussed

**Recorded design, not proven final wiring.** This checklist uses only
parts already reported or simple hookup materials; it does **not** mean
the owner has authorized any firmware changes or PCB rework.

| Part | Qty | Status / purpose |
| --- | --- | --- |
| Seeed XIAO ESP32-S3 | 1 | Existing board; Wi-Fi capture and OTA |
| Purple PCM1808 24-bit stereo I²S ADC module | 1 | Owner purchased six; use one for prototype |
| USB-C power lead | 1 | Powers S3; S3 board provides USB 5 V and regulated 3.3 V to ADC, subject to supply verification |
| Analog stereo line-level cable/connector | 1 | Stereo 3.5 mm **or** 2× RCA, not yet chosen; source ground shared |
| Short hookup wires / breadboard or perfboard | As needed | Prototype wiring; keep clocks/data short |
| External attenuator / DPDT switch | Optional | **Defer** until actual line-output levels and ADC input network are tested |
| Replacement SMD resistors/capacitors | 0 planned | Owner does not want to buy or solder SMD replacements |

**Preliminary full module hookup** (read the silkscreen on the actual board
first; *not yet validated by a completed I²S hardware test*):

| Module pad/label | Proposed connection | Notes |
| --- | --- | --- |
| RIN | Right line-level signal | Top edge |
| LIN | Left line-level signal | Top edge |
| Top-edge GND (if confirmed) | Audio source ground | Check continuity/marking before use |
| SCK | XIAO D0 / GPIO1 | MCLK, target 12.288 MHz at 48 kHz |
| BCK | XIAO D1 / GPIO2 | Target BCLK 3.072 MHz |
| LRC | XIAO D3 / GPIO4 | LRCLK 48 kHz |
| OUT | XIAO D4 / GPIO5 | PCM1808 serial data **into** S3 |
| +5V | S3 USB-powered 5 V pin | Analog rail; check PCB and available current |
| 3.3 / 3.3V | S3 3V3 pin | Digital rail; duplicate module 3.3 labels need continuity check, not two separate supplies |
| GND | S3 GND | Common power and signal reference |
| MD0 | Leave unconnected **provisionally** | TI PCM1808 defaults to low via internal pulldown: slave mode |
| MD1 | Leave unconnected **provisionally** | TI defaults low via internal pulldown: slave mode |
| FMY (PCB label; FMT on chip) | Leave unconnected **provisionally** | TI defaults low via internal pulldown: I²S format |

**Hardware prerequisites:** Confirm the +5 V and 3.3 V labels are
actual input rails and that the board has no conflicting ties or
regulators; check slave/FMT strapping before first power-on. Verify
that the selected Arduino ESP32-S3 I²S implementation can generate the
MCLK signal on the proposed GPIO before treating that pin allocation
as final. No extra dedicated level converters are planned for the
PCM1808-to-S3 interface: both use a nominal 3.3 V digital domain.

**Possible incorrect factory input filter:** If this exact PCB
is confirmed to have the alleged 10 kΩ / 100 nF per-channel low-pass,
the user prefers **not buying SMD replacement components**. A possible
repair is to identify and remove the offending **shunt** filter
capacitor on each channel while leaving the original series resistors.
This is only a test idea, not a confirmed/authorized physical change.
First inspect and verify the actual analog network; do not remove a
coupling or bypass capacitor by mistake. This prospective repair may
still affect the eventual input pad and noise/alias performance.

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

**Analog input — line level only, confirmed:** PCM1808 full
scale is **3 Vp-p**, or about **1.06 Vrms for a sine wave** (TI data sheet,
https://www.ti.com/product/PCM1808). A typical ~2 Vrms modern line
output would clip it, so prototype with provision for approximately
-6 dB or greater **stereo attenuation**, chosen after checking output
level and the actual board's input impedance. Some vintage/consumer
line-outs may be lower and can connect directly if within full scale.
Inspect the PCB's input coupling and DC bias before adding components.
No speaker-level capture or cable-insertion detector is planned.

**Buffer and power:** The existing 250 ms ring-buffer recommendation
represents ~48 kB if converted to 16-bit stereo at 48 kHz; raw 32-bit
capture would use ~96 kB for the same span. Keep DMA capture and Wi-Fi
transmission independent. USB 5V supply with 3V3 derived by the S3 is
the starting point; verify the particular ADC board's power consumption
and pin voltage expectations before soldering.

### IMPORTANT: possible defective factory input RC filter on purple PCM1808 boards

**New evidence and engineering review (2026-11-23; board itself NOT tested).**
The user provided a review alleging that low-cost purple PCM1808 breakouts
contain a **10 kΩ input series resistor and 100 nF shunt capacitor** per
channel, forming an unintended ~159 Hz low-pass. The RC calculation is
correct for that topology: `1/(2π × 10,000 Ω × 100 nF) ≈ 159 Hz`.
An ideal single-pole response would attenuate ~1 kHz by 16 dB and 20 kHz
by 42 dB. Independent hobbyist reports describe visually similar boards
with a mistaken 100 nF component, loss of treble, and restoration after
filter repair:
https://www.reddit.com/r/diyaudio/comments/1rgl530/warning_about_fake_pcm1808_adc_chips_on_modules/
https://www.reddit.com/r/diyelectronics/comments/1rl8s9t/pcm1808_modules_from_china_the_drama_continues/
A separate report describes the same input-capacitor fault:
https://www.diyaudio.com/community/threads/esp32-s3-touchscreen-parametric-eq-2-way-crossover-project-guition-jc4827w543c.439631/

**CAUTION:** These reports **do not prove** the components installed on
the user's exact purchased boards or the circuit connection. Inspect
the physical PCB and identify the two channel-specific RC paths before
soldering, shorting or removing SMD parts. Do not confuse input filter
capacitors with power-supply decouplers or large electrolytic AC-couplers.

**Proposed fix if confirmed:** Replace each channel's faulty filter with
~1 kΩ series resistance and ~1 nF shunt capacitance (calculated ~159 kHz
corner), consistent with TI's typical suggested external filter range
(100 Ω/10 nF to 1 kΩ/1 nF). The review's alternative
1 kΩ/3.3 nF has a ~48 kHz corner and an ideal ~0.69 dB drop at 20 kHz;
1 kΩ/1 nF has ~0.07 dB drop at 20 kHz. Avoid a blanket short/bypass
of the filter without considering EMI/aliasing. The PCM1808 also has
internal filtering. See TI: https://www.ti.com/product/PCM1808

**Impact on previously proposed attenuation:** The 10 kΩ/12 kΩ
external stereo pad below is only a preliminary calculation that assumed
the breakout's internal input network was benign. Its source impedance
could interact with the repaired or defective filter and cause additional
high-frequency attenuation; do not install it as-is until the actual
PCB network is verified and the *combined* response/loading is checked.
No software gain or digital EQ can recreate treble attenuated before
the ADC. Address/measure the filter before finalizing attenuation.

**Status:** research/design note only; no actual board testing,
SMD rework, firmware, server or PCB layout modification authorized.

### Proposed analog line-level attenuation (not yet built)

**Two matched channels, 0 dB direct / nominal -6 dB option.**
The PCM1808 silicon's full-scale analog sine input is 3 Vp-p
(~1.06 Vrms), with nominal 60 kΩ input impedance (TI PCM1808 datasheet).
A 2 Vrms line output could otherwise clip the ADC.

For **each** stereo channel, provision a divider **before the module's
AC-coupling capacitor**, after verifying the actual purple PCB routing:

```text
L or R line out --- 10 kΩ series ----+---- module LIN or RIN
                                     |
                                    12 kΩ
                                     |
                                signal GND
```

Nominally, 12 kΩ in parallel with 60 kΩ gives 10 kΩ, so the
series 10 kΩ results in 0.50 gain (~-6.02 dB): a 2 Vrms
line signal yields approximately 1 Vrms at the ADC. This is
a **calculated starting point**, not a bench-measured attenuation for the
specific breakout, whose input coupling/resistor network still needs
inspection. Use 1% resistors and identical component values for L/R.

**Bypass / switching:** Initially test direct 0 dB wiring on known
low-level line outputs; if a source exceeds ADC full-scale, use the
attenuator. Provide either two sets of reversible solder jumpers or
a DPDT ON-ON selector to route both channels through the pad or
around it. In the direct position, don't leave the shunt loading
in place unless the source can reliably drive the extra ~12 kΩ load.
No software gain reduction can recover a waveform clipped *before*
the ADC.

**Safety/electrical caveat:** The PCM1808 chip's input is internally
biased; do **not** attach an external shunt-to-ground to a chip-side
DC-biased input. Confirm that the breakout's LIN/RIN connection is on
the **line/source side** of proper AC-coupling capacitors first.
If not, adapt the input coupling/filter circuit per the TI datasheet
before installing the divider. Ground the stereo source and module
appropriately and do not use this line pad on speaker-level outputs.

The attenuation selector, resistor layout and actual signal levels
are proposals, not approved or implemented hardware/firmware changes.

### Decisions and tests before implementation

1. **Confirmed by user, 2026-11-23:** this node is fed by ordinary
   **line-level outputs**, not amplifier/speaker outputs. Do not design
   a speaker-level frontend or assume cable-insertion detection.
2. **Module identified by user image:** purple PCM1808 24-bit stereo ADC
   breakout from an Amazon listing for a six-piece pack (purchased
   October 28, 2026, according to the screenshot). Visible PCB top
   terminals are **RIN** and **LIN**. Right edge, top-to-bottom:
   **BCK, OUT, LRC, SCK, GND, 3.3**. Left edge: apparently **FMY**
   (likely intended **FMT**, needs verification), **MD1, MD0, GND,
   3.3, +5V**. The 3.3 pins appear duplicated; test continuity.
   Verify power rails and mode strapping on the physical board before
   connecting. The TI silicon requires separate nominal 5 V analog
   and 3.3 V digital rails; do not assume a regulator on this module.
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
