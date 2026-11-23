# House Music webpage layout update — planning checkpoint (2026-11-23)

**Status: design requirements recorded; implementation NOT authorized yet.**
No HTML/CSS/JavaScript, firmware, service configuration, or playback logic
is changed by this document. The currently installed, working Pi server
remains v0.14.4.

## Reference and scope

Use the user's screenshot of the current **desktop Library page** as the
visual reference, together with the actual repository source
(`web/house.html`, `web/house.css`, `web/house.js`). Do not substitute
simplified mockups that omit controls. Retain the existing dark theme, sidebar
navigation, Library title and Music breadcrumb, top speaker/connection status,
bottom Now Playing/transport controls, and all existing UI controls and behavior
except for the explicitly requested layout and presentation changes below.

## 1. Library controls: move and reorder only

- Move the existing **New-session playlist** selector out of the sidebar
  into the **same horizontal control row as Display**, immediately to the
  **left** of Display. Change its visible label to **Default Playlist**.
- Preserve current `MP3s` / `Rap` choices, the `default-folder` control
  identity, the server's existing `/settings` behavior, and the fact that
  the selection applies to **the next fresh passive session only**.
- Desktop: the Library title is on the same horizontal level as the control
  row, **not stacked in a new row above the selectors**. The Music
  breadcrumb remains under Library.
- New left-to-right order of the existing Library controls:
  **Default Playlist | Display | Sort | Rescan | Filter this folder | Play folder**.
  Play Folder should be the far-right action; Rescan and Filter move left
  of it. No other controls should be omitted.
- Keep the existing Subwoofer settings link in the sidebar when removing
  the old default playlist selector and descriptive text.
- Preserve the responsive/mobile behavior of the *same webpage*; no
  separate mobile page or app is required. Adapt the control row without
  clipping inaccessible actions on narrower screens.

## 2. Independent desktop scrolling

- Left sidebar/navigation and right main content area each scroll
  vertically **independently** when their content overflows.
- Keep the existing top status/header strip and bottom Now Playing/
  transport bar accessible without having to scroll either content area.
- Avoid relying on a single whole-document scrollbar to navigate both.
- On narrow/mobile layouts, retain practical single-column page scrolling
  and existing responsive navigation, rather than forcing unusable
  side-by-side scroll panes.

## 3. Queue page: position the active song at top of visible list

- On opening Queue, show the **currently playing song at the top of the
  visible queue viewport**, with upcoming tracks below.
- The discussed interpretation is **scroll to the current row** when
  opening Queue rather than reorder the actual MPD queue: played tracks
  remain accessible by scrolling upward and playback order/shuffle are
  unchanged. Do not continually auto-scroll while the user manually
  explores the list.
- Preserve current A–Z Sort, Clear Queue, per-track click and up/down
  controls, queue IDs and actual MPD ordering.
- If implementation details reveal ambiguity about whether the current
  track should instead be *visually rotated* to the first row, confirm
  before changing semantics. The agreed user-facing requirement is that
  the current song is at the top upon entering Queue.

## 4. Speakers page: active first

- Group the existing **active/audible** speakers above inactive speakers.
- Below, retain non-audible entries (silent, standby, or disconnected);
  preserve stable existing order **within** each group.
- Statuses should update naturally with the existing refresh flow.
  This is display-only: do not change node mute, volume, session
  retention, Snapcast, or audio behavior.

## Implementation boundary and verification (pending authorization)

- Change only relevant webpage HTML/CSS/JS. Do not change ESP32 YAML/firmware,
  MPD startup, server session policy or other audio paths.
- Preserve DOM IDs required by existing JavaScript handlers, including
  `default-folder`, and keep all current controls functional.
- Review responsive sizing and scrolling against the supplied desktop
  screenshot and mobile-width breakpoints.
- Run browser JavaScript syntax/sorting checks and other relevant GitHub
  regressions before proposing a new packaged server release.
- Do not claim this is deployed on the Pi until user-supplied installation
  output verifies it.

This checkpoint captures the user's requested layout; it does **not**
constitute permission to implement source changes.
