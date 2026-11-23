"use strict";
// Run with: node tests/test_house_browse.js
const assert = require("node:assert/strict");
const fs = require("node:fs");
const vm = require("node:vm");

function element(tag = "div") {
  return {
    tagName: tag, textContent: "", className: "", children: [], style: {},
    attrs: {}, listeners: {},
    setAttribute(name, value) { this.attrs[name] = value; },
    addEventListener(name, callback) { this.listeners[name] = callback; },
    append(...children) { this.children.push(...children); },
    replaceChildren(...children) { this.children = [...children]; },
    scrollIntoView(options) { this.scrollCall = options; },
    classList: { toggle() {} }
  };
}
const ids = new Map();
const preferences = new Map();
const context = vm.createContext({
  window: {
    localStorage: {
      getItem: (name) => preferences.get(name) ?? null,
      setItem: (name, value) => preferences.set(name, value)
    }
  },
  document: {
    createElement: element,
    getElementById: (id) => {
      if (!ids.has(id)) ids.set(id, element());
      return ids.get(id);
    }
  },
  setTimeout, clearTimeout, AbortController,
  fetch: async () => { throw Error("Live HTTP access must not occur in this test."); }
});
const source = fs.readFileSync("web/house.js", "utf8");
const startup = source.indexOf("const identity = ");
assert.ok(startup > 0, "Keep browser startup separate from pure browsing tests.");
vm.runInContext(source.slice(0, startup), context, { filename: "house.js" });
const run = (script) => vm.runInContext(script, context);

async function test() {
  const files = [
    {type:"file",name:"202-disc2-track2.mp3",path:"CDs/album/202-disc2-track2.mp3",track:"2"},
    {type:"file",name:"101-disc1-track1.mp3",path:"CDs/album/101-disc1-track1.mp3",track:"1"},
    {type:"file",name:"201-disc2-track1.mp3",path:"CDs/album/201-disc2-track1.mp3",track:"1"},
    {type:"file",name:"102-disc1-track2.mp3",path:"CDs/album/102-disc1-track2.mp3",track:"2"}
  ];
  context.sample = files;
  run('current.entries = sample; current.folder = "CDs/album"; current.displayMode = "metadata"');
  assert.equal(run("activeBrowseSort()"), "track", "CD albums should default to track ordering");
  assert.deepEqual(JSON.parse(run("JSON.stringify(sortedLibraryFiles().map(x=>x.name))")), [
    "101-disc1-track1.mp3", "102-disc1-track2.mp3",
    "201-disc2-track1.mp3", "202-disc2-track2.mp3"
  ], "Disc-prefixed multi-disc albums must not interleave tracks.");

  run('current.entries = [{type:"file",name:"z.mp3",path:"CDs/album/z.mp3",track:"2",disc:"2"},'+
      '{type:"file",name:"a.mp3",path:"CDs/album/a.mp3",track:"1",disc:"1"},'+
      '{type:"file",name:"b.mp3",path:"CDs/album/b.mp3",track:"1",disc:"2"}]');
  assert.deepEqual(JSON.parse(run("JSON.stringify(sortedLibraryFiles().map(x=>x.name))")), [
    "a.mp3", "b.mp3", "z.mp3"
  ], "Real MPD disc tags must take precedence over filenames.");

  run('current.entries = [{type:"file",name:"unknown.mp3",path:"CDs/album/unknown.mp3"},'+
      '{type:"file",name:"03-third.mp3",path:"CDs/album/03-third.mp3",track:"3"},'+
      '{type:"file",name:"01-first.mp3",path:"CDs/album/01-first.mp3"},'+
      '{type:"file",name:"02-second.mp3",path:"CDs/album/02-second.mp3",track:"2"}]');
  assert.deepEqual(JSON.parse(run("JSON.stringify(sortedLibraryFiles().map(x=>x.name))")), [
    "01-first.mp3", "02-second.mp3", "03-third.mp3", "unknown.mp3"
  ], "Track metadata should fall back to numbered filenames, then to names.");

  run('current.folder = "MP3s"; current.displayMode = "filename"; current.sortModes.filename = "date_desc";'+
      'current.entries = [{type:"file",name:"old.mp3",path:"MP3s/old.mp3",lastModified:"2021-10-01T01:00:00Z"},'+
      '{type:"file",name:"no-date.mp3",path:"MP3s/no-date.mp3"},'+
      '{type:"file",name:"new.mp3",path:"MP3s/new.mp3",lastModified:"2026-11-01T01:00:00Z"}]');
  assert.deepEqual(JSON.parse(run("JSON.stringify(sortedLibraryFiles().map(x=>x.name))")), [
    "new.mp3", "old.mp3", "no-date.mp3"
  ], "New–Old means lastModified; unknown dates sort last.");
  run('current.sortModes.filename = "date_asc"');
  assert.deepEqual(JSON.parse(run("JSON.stringify(sortedLibraryFiles().map(x=>x.name))")), [
    "old.mp3", "new.mp3", "no-date.mp3"
  ], "Old–New uses the same lastModified field.");

  run('current.displayMode = "metadata"; current.sortModes.metadata = "title_asc";'+
      'current.entries = [{type:"file",name:"a.mp3",title:"Zoo",path:"MP3s/a.mp3"},'+
      '{type:"file",name:"z.mp3",title:"Apple",path:"MP3s/z.mp3"}]');
  assert.deepEqual(JSON.parse(run("JSON.stringify(sortedLibraryFiles().map(x=>x.name))")), [
    "z.mp3", "a.mp3"
  ], "Metadata Title A–Z sorts metadata rather than filenames.");
  run("drawBrowse()");
  let controls = ids.get("view-actions").children;
  assert.equal(controls[0], ids.get("default-playlist-control"),
    "Original default playlist selector stays mounted in the Library toolbar.");
  assert.equal(controls[0].hidden, false);
  assert.equal(controls[1].children[1].value, "metadata");
  assert.equal(controls[2].children[1].value, "title_asc");
  assert.ok(!controls[2].children[1].children.some(item => item.value === "date_desc"),
    "Metadata display hides filename-only date sorting.");
  assert.equal(controls[3].textContent, "⟳ Rescan");
  assert.equal(controls[4].placeholder, "Filter this folder");
  assert.equal(controls[5].textContent, "▶ Play folder",
    "Play Folder is the rightmost Library action.");
  controls[1].children[1].listeners.change({target:{value:"filename"}});
  controls = ids.get("view-actions").children;
  assert.ok(controls[2].children[1].children.some(item => item.value === "date_desc"),
    "Filename display includes modified-date sorting.");
  run("saveBrowsePreferences()");
  assert.equal(JSON.parse([...preferences.values()][0]).displayMode, "filename");

  // Selecting a song or Play Folder sends the same visible order to MPD.
  const calls = [];
  context.calls = calls;
  run('command = async (path, payload) => { calls.push({path, payload}); return true; };');
  run('current.folder = "CDs/album"; current.displayMode = "metadata"; current.sortModes.metadata = "track";'+
      'current.state = {mpd:{random:false}}; current.entries = sample');
  await run("playFiles(sortedLibraryFiles(), 2)");
  assert.deepEqual(Array.from(calls[0].payload.tracks), [
    "CDs/album/101-disc1-track1.mp3", "CDs/album/102-disc1-track2.mp3",
    "CDs/album/201-disc2-track1.mp3", "CDs/album/202-disc2-track2.mp3"
  ]);
  assert.equal(calls[0].payload.startIndex, 2);
  run("current.state.mpd.random = true");
  await run("playFiles(sortedLibraryFiles(), 0)");
  assert.equal(calls.at(-1).path, "/shuffle");
  assert.equal(calls.at(-1).payload.enabled, false,
    "Starting a CD album must disable shuffle after queue replacement.");

  // CD input view is separate from ripped-CD library browsing and never
  // supplies an arbitrary URL; selection sends only the source ID and track.
  run('current.view = "inputs"; current.inputs = ' +
      '[{sourceId:"cd-abc123",name:"LiteOn CD",kind:"cd",available:true,status:"ready",tracks:[3,1,2]}];' +
      'current.state = {source:{type:"cd", input:{sourceId:"cd-abc123",name:"LiteOn CD",trackNumber:2,' +
      'trackNumbers:[1,2,3]}, canSeek:false, resumePlaylist:{available:true,snapshotId:"abc"}}' +
      ',mpd:{transport:"play",queueLength:3,song:{file:"http://127.0.0.1:8768/audio/cd-abc123/2.wav"},' +
      'durationSeconds:170,elapsedSeconds:10}}; drawInputs(); renderPlayer();');
  const cdItems = ids.get("items").children;
  assert.equal(cdItems.length, 4, "One source heading and three CD tracks");
  assert.equal(cdItems[1].children[0].children[1].children[0].textContent, "Track 1");
  assert.equal(cdItems[2].children[0].children[1].children[0].textContent, "Track 2");
  assert.equal(cdItems[2].className, "item playing", "Playing CD track is highlighted");
  await cdItems[1].children[0].listeners.click();
  assert.equal(calls.at(-1).path, "/inputs/play");
  assert.equal(calls.at(-1).payload.sourceId, "cd-abc123");
  assert.equal(calls.at(-1).payload.trackNumber, 1);
  assert.equal(ids.get("track-title").textContent, "Track 2");
  assert.equal(ids.get("seek").disabled, true, "CD HTTP WAV does not advertise seeking");
  assert.equal(ids.get("resume").hidden, false, "CD source exposes Resume Playlist");

  // The Queue view positions the playing row at the top of the visible
  // scroller without rotating MPD order or repeatedly jumping on refresh.
  run('current.view = "queue"; current.queue = ' +
      '[{id:11,title:"First"},{id:22,title:"Second"},{id:33,title:"Third"}];' +
      'current.state = {mpd:{songId:22,queueVersion:8}};');
  context.document.getElementById("main-panel").scrollTop = 300;
  run("drawQueue({alignPlaying:true})");
  let queueRows = ids.get("items").children;
  assert.equal(queueRows.length, 3);
  assert.equal(queueRows[1].className, "item playing");
  assert.equal(queueRows[1].scrollCall.block, "start",
    "Entering the queue scrolls the playing song to the top.");
  assert.equal(run("JSON.stringify(current.queue.map(x=>x.id))"), "[11,22,33]",
    "Changing the visible queue position does not reorder MPD songs.");
  context.document.getElementById("main-panel").scrollTop = 475;
  run("drawQueue()");
  queueRows = ids.get("items").children;
  assert.equal(queueRows[1].scrollCall, undefined,
    "Background queue redraw must not auto-scroll.");
  assert.equal(context.document.getElementById("main-panel").scrollTop, 475,
    "Queue redraw preserves the user's scroll position.");

  // Speaker grouping is stable: all audible speakers first, then inactive.
  run('current.view = "speakers"; current.speakers = ' +
      '[{id:"quiet-1",audible:false},{id:"active-1",audible:true},' +
      '{id:"quiet-2",audible:false},{id:"active-2",audible:true}]; drawSpeakers();');
  const speakerRows = ids.get("items").children;
  const speakerNames = speakerRows.map(item => item.children[0].children[1].children[0].textContent);
  assert.deepEqual(speakerNames, ["active-1","active-2","quiet-1","quiet-2"]);
  assert.equal(run("JSON.stringify(current.speakers.map(x=>x.id))"),
    '["quiet-1","active-1","quiet-2","active-2"]',
    "Grouping should not mutate server order.");

  const html = fs.readFileSync("web/house.html", "utf8");
  const css = fs.readFileSync("web/house.css", "utf8");
  assert.equal((html.match(/id="default-folder"/g) || []).length, 1);
  assert.ok(html.indexOf('id="view-actions"') < html.indexOf('id="default-folder"'),
    "The default playlist dropdown belongs to the header, not the sidebar.");
  assert.ok(html.includes('id="main-panel"'));
  assert.ok(css.includes(".sidebar,.main{min-height:0;overflow-y:auto"),
    "Independent desktop scrolling must be configured.");
  run('current.view = "radio"; clearView("Radio", "Radio")');
  assert.equal(ids.get("default-playlist-control").hidden, true,
    "Default Playlist is shown on Library only.");
  assert.equal(ids.get("view-actions").children[0], ids.get("default-playlist-control"),
    "The persistent select remains mounted when changing views.");

  console.log("House Music Library, queue, speakers, and CD browser regressions passed.");
}
test().catch(error => { console.error(error); process.exitCode = 1; });
