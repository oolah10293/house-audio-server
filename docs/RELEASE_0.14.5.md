# House Music Server v0.14.5 — approved webpage layout

**Status: source changes implemented and versioned release prepared; Pi installation
and visual acceptance pending.** The user explicitly authorized the webpage
changes on 2026-11-23. The currently confirmed Pi installation is v0.14.4.

## Changed webpage behavior

The authoritative screenshot and agreed scope are recorded in
[WEBPAGE_UPDATE_PLAN_2026-11-23.md](WEBPAGE_UPDATE_PLAN_2026-11-23.md).

1. **Library:** move the existing `default-folder` selector from the sidebar
   immediately left of Display; rename its label **Default Playlist**.
   Preserve its saved `MP3s`/`Rap` choice and existing backend behavior.
   Keep all original controls and order them:
   Default Playlist → Display → Sort → Rescan → Filter → Play folder,
   with Play folder at the far right and the Library heading on the same
   horizontal level.
2. **Independent desktop scrolling:** left navigation and right content scroll
   separately. The right status header remains visible and the existing bottom
   playback bar remains fixed. On narrower mobile screens, retain the original
   responsive single-column page scroll.
3. **Queue:** on opening, scroll the *visible list* to the currently playing
   row at its top (allowing for the fixed status bar). Preserve the real MPD
   queue order, all existing queue controls and earlier songs above; do not
   auto-scroll during ordinary background queue updates.
4. **Speakers:** group currently audible nodes before inactive nodes without
   modifying status, playback or volume. Preserve stable order within groups.

The CD page, internet radio, subwoofer controls, and all other existing House
Music UI elements remain present. No ESP32 firmware, Snapcast/MPD settings,
buffer sizes, queue shuffle or house session policy were changed.

## Verification

- GitHub CI on the implementation PR #24 passed Python syntax, shell syntax,
  full Python regression tests and browser JavaScript/sorting tests.
- Browser regression tests now verify the Library control sequence, persistent
  default playlist selector, queue highlight/one-time scroll without MPD
  reorder, stable active/inactive speaker grouping, and separate desktop
  scrolling rules.
- Installer checks retain the full browser/CD/subwoofer baseline, and now
  also verify the presence of the relocated default playlist selector and
  the main scroll panel before installation and from the installed browser.
- The released Pi ZIP must be constructed from exact clean committed source;
  the builder verifies each source file against Git and verifies the ZIP
  integrity. `SOURCE_SHA.txt` and `MANIFEST.json` record its identity.
- As of this checkpoint, no field browser screenshots or on-Pi acceptance
  of the v0.14.5 changes have been supplied. GitHub CI is not physical
  deployment verification.

## Pi installation and rollback

Package name: `HouseMusic_Server_v0.14.5_Pi.zip` using
`python3 tools/package_pi.py --output-dir <output-dir>` from the exact
released commit. Unpack and run `sudo sh ./install-cd.sh` from its
`HouseMusic_Server_v0.14.5` folder (see `INSTALL_CD.txt`).

The existing full installer backs up the installed server and independent CD
gateway, runs matching-version checks and byte-for-byte installed-file checks,
restarts services, verifies health and browser/session API, and rolls back on
failed checks. Installation interrupts current playback and applies the
existing fresh-session boundary; persistent settings are preserved.

The user should confirm the page's actual layout, scrolling, Queue positioning
and speaker order in a browser after installation. If the browser shows
stale assets, perform a hard refresh before assessing layout. No automatic
remote installation is claimed.
