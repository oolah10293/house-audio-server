/* House Music: browser-only controller for the existing HOUSE HTTP API.
 * No audio decoding, native MPD socket, Snapcast player, or third-party scripts.
 */
"use strict";

const byId = (id) => document.getElementById(id);
const current = {
  view: "browse", folder: "", entries: [], search: "",
  displayMode: readBrowsePreferences().displayMode,
  sortModes: readBrowsePreferences().sortModes,
  state: null, stateReceivedAt: 0, queue: [], queueVersion: null,
  stations: [], speakers: [], busy: false, online: false,
  pollInFlight: false, browseRequest: 0, notifyTimer: null,
  lastRadioId: null, lastSongId: null
};

const BROWSE_PREF_KEY = "house_music_library_preferences_v1";
const SORT_CHOICES = {
  filename: [
    ["name_asc", "Name A–Z"],
    ["name_desc", "Name Z–A"],
    ["date_desc", "Modified New–Old"],
    ["date_asc", "Modified Old–New"],
    ["track", "Track Number"]
  ],
  metadata: [
    ["title_asc", "Title A–Z"],
    ["title_desc", "Title Z–A"],
    ["artist", "Artist"],
    ["album", "Album"],
    ["track", "Track Number"]
  ]
};
function readBrowsePreferences() {
  const fallback = { displayMode: "metadata", sortModes: { filename: null, metadata: null } };
  try {
    const saved = JSON.parse(window.localStorage.getItem(BROWSE_PREF_KEY) || "null");
    if (!saved || typeof saved !== "object") return fallback;
    const displayMode = saved.displayMode === "filename" ? "filename" : "metadata";
    const sortModes = {};
    for (const mode of ["filename", "metadata"]) {
      const requested = saved.sortModes && saved.sortModes[mode];
      sortModes[mode] = SORT_CHOICES[mode].some(([id]) => id === requested) ? requested : null;
    }
    return { displayMode, sortModes };
  } catch (_) { return fallback; }
}
function saveBrowsePreferences() {
  try {
    window.localStorage.setItem(BROWSE_PREF_KEY, JSON.stringify({
      displayMode: current.displayMode, sortModes: current.sortModes
    }));
  } catch (_) { /* Optional storage must not prevent library browsing. */ }
}
function activeBrowseSort() {
  const stored = current.sortModes[current.displayMode];
  if (SORT_CHOICES[current.displayMode].some(([id]) => id === stored)) return stored;
  // Album folders default to track order; playlists default to names/titles.
  if (current.folder.startsWith("CDs/")) return "track";
  return current.displayMode === "filename" ? "name_asc" : "title_asc";
}
const compareLibraryText = (a, b) =>
  String(a || "").localeCompare(String(b || ""), undefined, { numeric: true, sensitivity: "base" });
function tagNumber(value) {
  const found = String(value ?? "").trim().match(/^(\\d+)/);
  return found ? Number(found[1]) : null;
}
function albumPosition(entry) {
  const filename = entry.name || fileName(entry.path);
  // Disc prefixes used by some CD rips: 101, 102, ..., 201, 202, ...
  const numberedDisc = filename.match(/^([1-9])(\\d{2})(?=\\D|$)/);
  const numberedTrack = filename.match(/^(\\d{1,2})(?=\\D|$)/);
  const disc = tagNumber(entry.disc) ?? (numberedDisc ? Number(numberedDisc[1]) : 1);
  const track = tagNumber(entry.track) ??
    (numberedDisc ? Number(numberedDisc[2]) : numberedTrack ? Number(numberedTrack[1]) : null);
  return { disc, track };
}
function compareAlbumPosition(a, b) {
  const left = albumPosition(a);
  const right = albumPosition(b);
  const byDisc = left.disc - right.disc;
  if (byDisc) return byDisc;
  const byTrack = (left.track ?? Infinity) - (right.track ?? Infinity);
  return Number.isNaN(byTrack) ? 0 : byTrack;
}
function compareDates(a, b, descending) {
  const left = Date.parse(a.lastModified || "");
  const right = Date.parse(b.lastModified || "");
  const leftValid = Number.isFinite(left);
  const rightValid = Number.isFinite(right);
  if (!leftValid || !rightValid) return leftValid === rightValid ? 0 : leftValid ? -1 : 1;
  return descending ? right - left : left - right;
}
function compareLibraryFiles(a, b, mode) {
  const byName = () => compareLibraryText(a.name, b.name);
  if (mode === "name_desc") return -byName();
  if (mode === "date_desc" || mode === "date_asc")
    return compareDates(a, b, mode === "date_desc") || byName();
  if (mode === "title_asc" || mode === "title_desc") {
    const compared = compareLibraryText(a.title || a.name, b.title || b.name);
    return (mode === "title_desc" ? -compared : compared) || byName();
  }
  if (mode === "artist")
    return compareLibraryText(a.artist || a.albumArtist, b.artist || b.albumArtist) ||
      compareLibraryText(a.album, b.album) || compareAlbumPosition(a, b) || byName();
  if (mode === "album")
    return compareLibraryText(a.album, b.album) ||
      compareLibraryText(a.albumArtist || a.artist, b.albumArtist || b.artist) ||
      compareAlbumPosition(a, b) || byName();
  if (mode === "track") return compareAlbumPosition(a, b) || byName();
  return byName();
}
function sortedLibraryFiles() {
  const mode = activeBrowseSort();
  return current.entries.filter((entry) => entry.type === "file")
    .sort((a, b) => compareLibraryFiles(a, b, mode));
}
function librarySelect(label, selected, options, onChange) {
  const wrapper = node("label", "library-select");
  wrapper.append(node("span", "library-select-caption", label));
  const select = node("select", "action browse-select");
  select.setAttribute("aria-label", label + " library entries");
  for (const [value, text] of options) {
    const option = node("option", "", text);
    option.value = value;
    select.append(option);
  }
  select.value = selected;
  select.addEventListener("change", (event) => onChange(event.target.value));
  wrapper.append(select);
  byId("view-actions").append(wrapper);
}

function node(tag, className, text) {
  const result = document.createElement(tag);
  if (className) result.className = className;
  if (text !== undefined && text !== null) result.textContent = String(text);
  return result;
}
function button(label, className, handler, disabled = false) {
  const result = node("button", className, label);
  result.type = "button";
  result.disabled = disabled;
  result.addEventListener("click", handler);
  return result;
}
function caption(text) { return node("span", "item-detail", text); }
function fileName(path) { return String(path || "").split("/").pop() || ""; }
function niceName(song) {
  return (song && (song.title || song.name)) || fileName(song && (song.file || song.path)) || "Unknown track";
}
function formatTime(seconds) {
  if (!Number.isFinite(Number(seconds)) || Number(seconds) < 0) return "0:00";
  const value = Math.floor(Number(seconds));
  return Math.floor(value / 60) + ":" + String(value % 60).padStart(2, "0");
}
function status(online) {
  current.online = online;
  const display = byId("connection");
  display.className = "connection " + (online ? "online" : "offline");
  display.lastChild.textContent = online ? " Connected" : " Offline";
}
function notice(message) {
  const display = byId("notice");
  display.textContent = message;
  display.hidden = false;
  clearTimeout(current.notifyTimer);
  current.notifyTimer = setTimeout(() => { display.hidden = true; }, 5500);
}
async function request(url, body) {
  const controller = new AbortController();
  const timeoutMs = url === "/queue/replace" || url === "/queue/reorder" ? 120000 : 15000;
  const timeout = setTimeout(() => controller.abort(), timeoutMs);
  try {
    const response = await fetch(url, {
      method: body === undefined ? "GET" : "POST",
      headers: body === undefined ? undefined : { "Content-Type": "application/json" },
      body: body === undefined ? undefined : JSON.stringify(body),
      cache: "no-store",
      credentials: "same-origin",
      signal: controller.signal
    });
    const data = await response.json();
    if (!response.ok) {
      const reason = data.detail || data.error || ("HTTP " + response.status);
      throw new Error(reason);
    }
    return data;
  } finally {
    clearTimeout(timeout);
  }
}
async function command(path, body = {}) {
  if (current.busy) return false;
  current.busy = true;
  try {
    await request(path, body);
    await refreshState();
    if (current.view === "queue") await loadQueue();
    if (current.view === "radio") await loadStations();
    return true;
  } catch (error) {
    notice(error.message || "Could not complete the request");
    await refreshState();
    return false;
  } finally {
    current.busy = false;
    renderPlayer();
  }
}
function clearView(kicker, title) {
  byId("view-kicker").textContent = kicker;
  byId("view-title").textContent = title;
  byId("breadcrumbs").replaceChildren();
  byId("view-actions").replaceChildren();
  byId("items").replaceChildren();
}
function action(label, handler, primary = false, disabled = false) {
  const control = button(label, "action" + (primary ? " primary" : ""), handler, disabled);
  byId("view-actions").append(control);
  return control;
}
function empty(title, description) {
  const container = node("div", "empty");
  container.append(node("strong", "", title), node("p", "", description));
  byId("items").append(container);
}
function row(icon, title, detail, onClick, options = {}) {
  const item = node("div", "item" + (options.playing ? " playing" : ""));
  const main = button("", "item-main", onClick, options.disabled || false);
  const symbol = node("span", "item-icon " + (options.iconClass || ""), icon);
  symbol.setAttribute("aria-hidden", "true");
  const text = node("span", "item-primary");
  text.append(node("span", "item-title", title), caption(detail));
  main.append(symbol, text);
  item.append(main);
  if (options.right) item.append(node("span", "item-side", options.right));
  if (options.controls) {
    const controls = node("div", "item-controls");
    for (const control of options.controls) {
      const b = button(control.text, "mini-button", control.click, control.disabled);
      b.title = control.label;
      b.setAttribute("aria-label", control.label);
      controls.append(b);
    }
    item.append(controls);
  }
  byId("items").append(item);
}
async function refreshState() {
  if (current.pollInFlight) return;
  current.pollInFlight = true;
  try {
    const data = await request("/state");
    const old = current.state;
    current.state = data;
    current.stateReceivedAt = Date.now();
    status(true);
    byId("speaker-count").textContent =
      "◈ " + (data.renderers?.presentCount ?? "—") + " speakers";
    byId("queue-count").textContent = String(data.mpd?.queueLength ?? 0);
    renderPlayer();
    const queueChanged = (data.mpd?.queueVersion !== old?.mpd?.queueVersion);
    const songChanged = (data.mpd?.songId !== old?.mpd?.songId);
    const stationChanged = (data.source?.station?.id !== old?.source?.station?.id);
    if (current.view === "queue" && (queueChanged || songChanged)) {
      loadQueue().catch((error) => notice(error.message));
    } else if (current.view === "radio" && stationChanged) {
      renderRadio();
    }
  } catch (error) {
    status(false);
    renderPlayer();
  } finally {
    current.pollInFlight = false;
  }
}
function renderPlayer() {
  const data = current.state;
  const mpd = data?.mpd || {};
  const source = data?.source || {};
  const radio = source.type === "radio";
  const playing = mpd.transport === "play";
  const song = mpd.song || {};
  const radioName = source.station?.name || "Internet radio";
  byId("source-label").textContent = radio ? "LIVE RADIO · " + radioName : "YOUR COLLECTION";
  byId("track-title").textContent = radio
    ? (song.title || radioName)
    : niceName(song) === "Unknown track" ? "Nothing playing" : niceName(song);
  byId("track-subtitle").textContent = radio
    ? (song.artist || radioName)
    : (song.artist || song.album || (song.file ? fileName(song.file) : "Choose a folder or a radio station"));
  const play = byId("play-pause");
  play.textContent = playing ? "Ⅱ" : "▶";
  play.setAttribute("aria-label", playing ? "Pause" : "Play");
  byId("playback-state").textContent = String(mpd.transport || "stopped").toUpperCase();
  const canSkip = Boolean(!radio && source.canSkip !== false && mpd.queueLength);
  const canSeek = Boolean(!radio && source.canSeek !== false && mpd.song &&
    Number.isFinite(Number(mpd.durationSeconds)) && Number(mpd.durationSeconds) > 0);
  byId("previous").disabled = !canSkip || current.busy;
  byId("next").disabled = !canSkip || current.busy;
  byId("shuffle").disabled = radio || source.canShuffle === false || current.busy;
  byId("repeat").disabled = radio || source.canRepeat === false || current.busy;
  byId("shuffle").classList.toggle("active", Boolean(mpd.random) && !radio);
  byId("repeat").classList.toggle("active", Boolean(mpd.repeat) && !radio);
  byId("stop").disabled = mpd.transport === "stop" || current.busy;
  play.disabled = current.busy || (mpd.queueLength === 0 && !radio);
  const progress = byId("seek");
  progress.disabled = !canSeek || current.busy;
  const resume = source.resumePlaylist;
  byId("resume").hidden = !(radio && resume?.available && resume?.snapshotId);
  byId("resume").disabled = current.busy;
  tickClock();
}
let scrubbingSeek = false;
function tickClock() {
  const mpd = current.state?.mpd || {};
  const source = current.state?.source || {};
  const live = source.type === "radio";
  const length = live ? 0 : Math.max(0, Number(mpd.durationSeconds) || 0);
  let elapsed = Math.max(0, Number(mpd.elapsedSeconds) || 0);
  if (mpd.transport === "play" && current.stateReceivedAt) {
    elapsed += Math.min((Date.now() - current.stateReceivedAt) / 1000, 10);
  }
  if (length) elapsed = Math.min(length, elapsed);
  if (!scrubbingSeek) {
    byId("seek").value = String(length ? Math.round(elapsed / length * 1000) : 0);
  }
  byId("elapsed").textContent = live ? "LIVE" : formatTime(elapsed);
  byId("duration").textContent = live ? "RADIO" : formatTime(length);
}
async function loadBrowse(path = "") {
  const ticket = ++current.browseRequest;
  current.folder = path;
  current.search = "";
  current.entries = [];
  drawBrowse();
  try {
    const data = await request("/browse?path=" + encodeURIComponent(path));
    if (ticket !== current.browseRequest) return;
    current.entries = data.entries || [];
    drawBrowse();
  } catch (error) {
    if (ticket === current.browseRequest) {
      current.entries = [];
      drawBrowse();
      notice(error.message);
    }
  }
}
function drawBrowse() {
  if (current.view !== "browse") return;
  clearView("FOLDER-FIRST LIBRARY", current.folder ? fileName(current.folder) : "Library");
  const bread = byId("breadcrumbs");
  const root = button("Music", "", () => loadBrowse(""));
  bread.append(root);
  const chunks = current.folder.split("/").filter(Boolean);
  let parent = "";
  for (const chunk of chunks) {
    parent += (parent ? "/" : "") + chunk;
    const path = parent;
    bread.append(node("span", "crumb-separator", "/"),
      button(chunk, "", () => loadBrowse(path)));
  }
  librarySelect("Display", current.displayMode,
    [["filename", "Filename"], ["metadata", "Metadata"]], (value) => {
      if (value !== "filename" && value !== "metadata") return;
      current.displayMode = value;
      saveBrowsePreferences();
      drawBrowse();
    });
  librarySelect("Sort", activeBrowseSort(), SORT_CHOICES[current.displayMode], (value) => {
    if (!SORT_CHOICES[current.displayMode].some(([id]) => id === value)) return;
    current.sortModes[current.displayMode] = value;
    saveBrowsePreferences();
    drawBrowseList();
  });
  const playable = current.entries.some((entry) => entry.type === "file");
  action("▶ Play folder", () => playFiles(sortedLibraryFiles(), 0), true, !playable);
  action("⟳ Rescan", async () => {
    const ok = await command("/library/update", { force: true });
    if (ok) { notice("Library scan started"); await loadBrowse(current.folder); }
  });
  const search = node("input");
  search.type = "search";
  search.placeholder = "Filter this folder";
  search.setAttribute("aria-label", "Filter this folder");
  search.className = "action";
  search.style.width = "175px";
  search.value = current.search;
  search.addEventListener("input", () => { current.search = search.value; drawBrowseList(); });
  byId("view-actions").append(search);
  drawBrowseList();
}
function drawBrowseList() {
  const items = byId("items");
  items.replaceChildren();
  const filter = current.search.trim().toLocaleLowerCase();
  const matches = (entry) => !filter ||
    (entry.name + " " + (entry.title || "") + " " + (entry.artist || ""))
      .toLocaleLowerCase().includes(filter);
  const folders = current.entries.filter((entry) => entry.type === "directory" && matches(entry))
    .sort((a, b) => compareLibraryText(a.name, b.name));
  const allFiles = sortedLibraryFiles();
  const files = allFiles.filter(matches);
  if (!folders.length && !files.length) {
    empty(current.search ? "No matches" : "Nothing here", current.search ?
      "Try another search term." : "Open another folder or rescan the music library.");
    return;
  }
  for (const entry of folders) {
    row("▤", entry.name, "Folder", () => loadBrowse(entry.path), { iconClass: "folder" });
  }
  for (const entry of files) {
    const index = allFiles.findIndex((file) => file.path === entry.path);
    const title = current.displayMode === "filename" ? entry.name : (entry.title || entry.name);
    const detail = [entry.artist, entry.album].filter(Boolean).join(" · ") || entry.path;
    row("♫", title, detail, () => playFiles(allFiles, index),
      { right: entry.durationSeconds ? formatTime(entry.durationSeconds) : "" });
  }
}
async function playFiles(files, startIndex) {
  if (!files.length) return;
  const payload = { tracks: files.map((item) => item.path), startIndex, play: true };
  if (JSON.stringify(payload).length > 1900000) {
    notice("This folder is too large for a single queue request.");
    return;
  }
  const ok = await command("/queue/replace", payload);
  // Album folders should advance in track order, even if the previous playlist was shuffled.
  if (ok && current.folder.startsWith("CDs/") && current.state?.mpd?.random)
    await command("/shuffle", { enabled: false });
}
async function loadQueue() {
  if (current.view !== "queue") return;
  try {
    const data = await request("/queue");
    if (current.view !== "queue") return;
    current.queue = data.queue || [];
    current.queueVersion = current.state?.mpd?.queueVersion;
    drawQueue();
  } catch (error) { notice(error.message); }
}
function drawQueue() {
  if (current.view !== "queue") return;
  clearView("CURRENT SESSION", "Queue");
  action("A–Z Sort", () => sortQueue(), false, current.queue.length < 2);
  action("Clear queue", async () => {
    if (window.confirm("Clear the entire House Music queue?")) {
      await command("/queue/clear");
    }
  }, false, current.queue.length === 0);
  const count = current.queue.length;
  byId("breadcrumbs").textContent = count + (count === 1 ? " song" : " songs") + " in the shared MPD queue";
  if (!count) { empty("Queue is empty", "Play a folder or choose a radio station to start listening."); return; }
  current.queue.forEach((song, index) => {
    const playing = song.id === current.state?.mpd?.songId;
    row(playing ? "♫" : String(index + 1), niceName(song),
      [song.artist, song.album].filter(Boolean).join(" · ") || song.file,
      () => playQueueId(song.id),
      { playing, right: formatTime(song.durationSeconds),
        controls: [
          { text: "↑", label: "Move " + niceName(song) + " up", disabled: index === 0,
            click: () => moveQueue(index, index - 1) },
          { text: "↓", label: "Move " + niceName(song) + " down", disabled: index === count - 1,
            click: () => moveQueue(index, index + 1) }
        ] });
  });
}
async function moveQueue(from, to) {
  const ids = current.queue.map((song) => song.id);
  const [id] = ids.splice(from, 1);
  ids.splice(to, 0, id);
  await reorder(ids);
}
async function sortQueue() {
  const sorted = [...current.queue].sort((a, b) =>
    niceName(a).localeCompare(niceName(b), undefined, { numeric: true, sensitivity: "base" }));
  const playingId = current.state?.mpd?.songId;
  const index = sorted.findIndex((song) => song.id === playingId);
  if (index > 0) sorted.push(...sorted.splice(0, index));
  await reorder(sorted.map((song) => song.id));
}
async function reorder(songIds) {
  const version = current.queueVersion;
  if (!Number.isInteger(version)) { notice("Queue version unavailable. Refresh before sorting."); return; }
  const ok = await command("/queue/reorder", { songIds, queueVersion: version });
  if (!ok) await loadQueue();
}
async function playQueueId(songId) {
  await command("/queue/play", { songId });
}
async function loadStations() {
  try {
    const data = await request("/radio/stations");
    current.stations = data.stations || [];
    if (current.view === "radio") drawRadio();
  } catch (error) { if (current.view === "radio") notice(error.message); }
}
function renderRadio() { drawRadio(); }
function drawRadio() {
  if (current.view !== "radio") return;
  clearView("STREAM THE WORLD", "Internet radio");
  action("+ Add station", addStation, true);
  const liveStation = current.state?.source?.station?.id;
  byId("breadcrumbs").textContent = current.stations.length + " saved stations · ranked by listening time";
  if (!current.stations.length) {
    empty("No stations saved", "Add a direct HTTP or HTTPS audio stream to listen through the house.");
    return;
  }
  for (const station of current.stations) {
    const playing = liveStation === station.id;
    row("◉", station.name || "Unnamed station",
      (playing ? "Currently selected · " : "") + (station.url || ""),
      () => command("/radio/play", { stationId: station.id }),
      { iconClass: "radio", playing,
        controls: [
          { text: "✎", label: "Rename " + station.name, click: () => renameStation(station) },
          { text: "×", label: "Remove " + station.name, click: () => deleteStation(station) }
        ] });
  }
}
async function addStation() {
  const url = window.prompt("Paste the direct audio stream URL (http:// or https://):");
  if (url === null || !url.trim()) return;
  const name = window.prompt("Station name (optional; leave blank to detect it):", "");
  if (name === null) return;
  const body = { url: url.trim() };
  if (name.trim()) body.name = name.trim();
  const ok = await command("/radio/stations", body);
  if (ok) { notice("Station saved"); await loadStations(); }
}
async function renameStation(station) {
  const name = window.prompt("Rename station:", station.name);
  if (name === null || !name.trim() || name.trim() === station.name) return;
  const ok = await command("/radio/stations/rename", { stationId: station.id, name: name.trim() });
  if (ok) await loadStations();
}
async function deleteStation(station) {
  if (!window.confirm("Remove saved station \"" + station.name + "\"?")) return;
  const ok = await command("/radio/stations/delete", { stationId: station.id });
  if (ok) await loadStations();
}
async function loadSpeakers() {
  try {
    const data = await request("/renderers");
    current.speakers = data.snapserver?.clients || [];
    if (current.view === "speakers") drawSpeakers();
  } catch (error) { if (current.view === "speakers") notice(error.message); }
}
function drawSpeakers() {
  if (current.view !== "speakers") return;
  clearView("WHO'S LISTENING", "Speakers");
  action("⟳ Refresh", loadSpeakers);
  action("Subwoofer settings ↗", () => { window.location.href = "/subwoofers"; });
  byId("breadcrumbs").textContent = "Read-only Snapcast status · playback controls affect the whole house";
  if (!current.speakers.length) {
    empty("No speakers reported", "Turn on a renderer or check the connection to Snapserver.");
    return;
  }
  current.speakers.forEach((speaker) => {
    const detail = [speaker.present ? "Present" : speaker.connected ? "Connected (not recently seen)" : "Disconnected",
      speaker.muted || speaker.groupMuted ? "Muted" : "Unmuted",
      Number.isFinite(Number(speaker.volumePercent)) ? speaker.volumePercent + "% Snapcast" : ""].filter(Boolean).join(" · ");
    row("◈", speaker.name || speaker.id, detail, () => {},
      { disabled: true, right: speaker.audible ? "AUDIBLE" : "INACTIVE" });
  });
}
async function loadSettings() {
  try {
    const data = await request("/settings");
    if (data.settings?.passiveDefaultFolder) byId("default-folder").value = data.settings.passiveDefaultFolder;
  } catch (_) { /* Playback works even if settings are unavailable. */ }
}
async function selectView(view) {
  if (!["browse", "queue", "radio", "speakers"].includes(view)) return;
  current.view = view;
  document.querySelectorAll("[data-view]").forEach((control) => {
    const active = control.dataset.view === view;
    control.classList.toggle("active", active);
    control.setAttribute("aria-current", active ? "page" : "false");
  });
  if (view === "browse") drawBrowse();
  if (view === "queue") await loadQueue();
  if (view === "radio") await loadStations();
  if (view === "speakers") await loadSpeakers();
}

const identity = "web-" + Date.now().toString(36) + "-" + Math.random().toString(36).slice(2, 11);
let lease = null;
let heartbeatInFlight = false;
async function attachController() {
  try {
    const data = await request("/controllers/attach", {
      controllerId: identity, rendererId: null, outputMuted: true, outputReady: false
    });
    lease = { token: data.leaseId, sequence: 0 };
  } catch (_) { lease = null; }
}
async function heartbeat() {
  if (heartbeatInFlight) return;
  heartbeatInFlight = true;
  try {
    if (!lease) { await attachController(); return; }
    const sequence = lease.sequence + 1;
    await request("/controllers/heartbeat", {
      controllerId: identity, leaseId: lease.token, sequence,
      outputMuted: true, outputReady: false
    });
    lease.sequence = sequence;
  } catch (_) { lease = null; }
  finally { heartbeatInFlight = false; }
}
function detach() {
  if (!lease) return;
  const body = JSON.stringify({ controllerId: identity, leaseId: lease.token });
  // pagehide cannot reliably await a fetch; the short server lease also expires.
  fetch("/controllers/detach", {
    method: "POST", headers: { "Content-Type": "application/json" },
    body, keepalive: true, credentials: "same-origin"
  }).catch(() => {});
  lease = null;
}
document.querySelectorAll("[data-view]").forEach((control) =>
  control.addEventListener("click", () => selectView(control.dataset.view)));
byId("default-folder").addEventListener("change", async (event) => {
  const wanted = event.target.value;
  try {
    await request("/settings", { passiveDefaultFolder: wanted });
  } catch (error) { notice(error.message); await loadSettings(); }
});
byId("play-pause").addEventListener("click", () => command(
  current.state?.mpd?.transport === "play" ? "/pause" : "/play"));
byId("stop").addEventListener("click", () => command("/stop"));
byId("next").addEventListener("click", () => command("/next"));
byId("previous").addEventListener("click", () => command("/previous"));
byId("shuffle").addEventListener("click", () =>
  command("/shuffle", { enabled: !current.state?.mpd?.random }));
byId("repeat").addEventListener("click", () =>
  command("/repeat", { enabled: !current.state?.mpd?.repeat }));
byId("seek").addEventListener("pointerdown", () => { scrubbingSeek = true; });
byId("seek").addEventListener("pointerup", () => { scrubbingSeek = false; });
byId("seek").addEventListener("change", (event) => {
  scrubbingSeek = false;
  const length = Number(current.state?.mpd?.durationSeconds) || 0;
  if (length > 0 && current.state?.source?.canSeek !== false &&
      current.state?.source?.type !== "radio") {
    command("/seek", { seconds: Number(event.target.value) / 1000 * length });
  }
});
byId("resume").addEventListener("click", () => {
  const bookmark = current.state?.source?.resumePlaylist;
  if (bookmark?.available && bookmark.snapshotId) {
    command("/radio/resume-playlist", { snapshotId: bookmark.snapshotId });
  }
});
window.addEventListener("pagehide", detach);
document.addEventListener("visibilitychange", () => {
  if (!document.hidden) { refreshState(); heartbeat(); }
});
selectView("browse");
loadBrowse("");
loadSettings();
attachController();
refreshState();
setInterval(refreshState, 2500);
setInterval(tickClock, 1000);
setInterval(heartbeat, 5000);
