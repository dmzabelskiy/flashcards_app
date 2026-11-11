import test from "node:test";
import assert from "node:assert/strict";
import { createRequire } from "node:module";
import { readFileSync } from "node:fs";
import vm from "node:vm";

const require = createRequire(import.meta.url);
const { timedCues, overlappingCues, currentText, setReferenceText, sourceSelection } =
  require("../video_captions.js");

const english = timedCues([
  { text: "One English sentence.", start: 0.5, duration: 3.5 },
  { text: "A second", start: 5, duration: 1 },
  { text: "sentence.", start: 6, duration: 1 },
]);

test("aligns independently segmented languages by strict time overlap", () => {
  const dutch = timedCues([
    { text: "Een zin.", start: 0, duration: 2 },
    { text: "Nog een zin.", start: 2, duration: 2 },
    { text: "Een tweede zin.", start: 5, duration: 2 },
  ]);
  assert.deepEqual(dutch.map(cue => overlappingCues(english, cue.start, cue.end).map(c => c.text)), [
    ["One English sentence."], ["One English sentence."], ["A second", "sentence."],
  ]);
  assert.deepEqual(overlappingCues(english, 4, 5), []);
});

test("playback, backward seeks and paused seeks use each track's own clock", () => {
  const dutch = timedCues([{ text: "Nederlands", start: 0, duration: 2 }]);
  assert.equal(currentText(dutch, 0.25), "Nederlands");
  assert.equal(currentText(english, 0.25), "");
  assert.equal(currentText(dutch, 2.5), "");
  assert.equal(currentText(english, 2.5), "One English sentence.");
  assert.equal(currentText(english, 6.5), "sentence.");
  assert.equal(currentText(english, 1), "One English sentence.");
  assert.equal(currentText(english, 1), "One English sentence."); // paused clock
  assert.equal(currentText(english, 5.5), "A second"); // seek while paused
});

test("clears captions in gaps and at exact ends without dropping overlaps", () => {
  const cues = timedCues([
    { text: "later", start: 2, duration: 2 },
    { text: "early", start: 0, duration: 3 },
    { text: "empty", start: 5, duration: 0 },
    { text: "invalid", start: "bad", duration: 1 },
  ]);
  assert.equal(currentText(cues, 2), "early later");
  assert.equal(currentText(cues, 3), "later");
  assert.equal(currentText(cues, 4), "");
  assert.equal(currentText(cues, 5), "");
  assert.equal(currentText(cues, 100), "");
  assert.equal(currentText(cues, -1), "");
});

test("English markup is written as plain reference text", () => {
  const element = {
    textContent: "",
    set innerHTML(_value) { throw new Error("Caption markup must never be parsed"); },
  };
  const text = '<img src=x onerror="alert(1)"> & <script>danger</script>';
  setReferenceText(element, text);
  assert.equal(element.textContent, text);
  setReferenceText(element, "");
  assert.equal(element.textContent, "");
});

function selectionFixture({ anchorSource = true, focusSource = true, intersectsEnglish = false } = {}) {
  const source = {};
  const reference = {};
  const parent = isSource => ({ closest: () => isSource ? source : null });
  const selection = {
    isCollapsed: false,
    rangeCount: 1,
    anchorNode: { nodeType: 3, parentElement: parent(anchorSource) },
    focusNode: { nodeType: 3, parentElement: parent(focusSource) },
    getRangeAt: () => ({ intersectsNode: node => node === reference && intersectsEnglish }),
  };
  const list = { contains: node => node === source, querySelectorAll: () => [reference] };
  return { selection, list };
}

test("Dutch-only selection remains usable for dictionary lookup", () => {
  const { selection, list } = selectionFixture();
  assert.equal(sourceSelection(selection, list), true);
  selection.isCollapsed = true;
  assert.equal(sourceSelection(selection, list), false);
  assert.equal(sourceSelection(null, list), false);
});

test("English-only and mixed endpoints never reach Dutch translation", () => {
  for (const options of [
    { anchorSource: false, focusSource: false },
    { anchorSource: true, focusSource: false },
    { anchorSource: false, focusSource: true },
  ]) {
    const { selection, list } = selectionFixture(options);
    assert.equal(sourceSelection(selection, list), false);
  }
});

test("Dutch endpoints enclosing an English reference row are rejected", () => {
  const { selection, list } = selectionFixture({ intersectsEnglish: true });
  assert.equal(sourceSelection(selection, list), false);
});

test("workspace renders safe references and keeps synchronization active during paused seeks", () => {
  class Element {
    constructor() {
      this.dataset = {};
      this.children = [];
      this.textContent = "";
      this.scrollHeight = this.clientHeight = 0;
      this.classList = { add() {}, remove() {}, toggle() {}, contains() { return false; } };
    }
    append(...children) { this.children.push(...children); }
    appendChild(child) { this.append(child); }
    addEventListener() {}
    setAttribute() {}
    closest() { return null; }
    querySelector() { return null; }
  }
  const ids = new Map();
  const document = {
    getElementById(id) {
      if (!ids.has(id)) ids.set(id, new Element());
      return ids.get(id);
    },
    createElement: () => new Element(),
    createDocumentFragment: () => new Element(),
    createTextNode: text => ({ textContent: text }),
    addEventListener() {},
    body: new Element(),
  };
  const app = document.getElementById("video-study-app");
  app.dataset = { videoId: "fixture", sourceLanguage: "nl", bilingual: "true" };
  document.getElementById("video-transcript-data").textContent = JSON.stringify([
    { text: "Hallo.", start: 0, duration: 1 },
    { text: "Wereld.", start: 2, duration: 1 },
  ]);
  const maliciousText = "<img src=x onerror=alert(1)>";
  document.getElementById("video-english-transcript-data").textContent = JSON.stringify([
    { text: maliciousText, start: 0.5, duration: 3 },
  ]);
  let seconds = 0;
  let events;
  const timers = new Map();
  const window = {
    VideoCaptions: require("../video_captions.js"),
    YT: {
      Player: function (_id, options) {
        events = options.events;
        return { getCurrentTime: () => seconds };
      },
      PlayerState: { PLAYING: 1, PAUSED: 2 },
    },
    matchMedia: () => ({ matches: false }),
    requestAnimationFrame: callback => callback(),
    setInterval: callback => { timers.set(1, callback); return 1; },
    clearInterval: id => timers.delete(id),
  };
  vm.runInNewContext(readFileSync(new URL("../video_study.js", import.meta.url), "utf8"), {
    document, window, Node: { ELEMENT_NODE: 1 },
    fetch: () => { throw new Error("Unexpected external request"); },
  });
  const rows = document.getElementById("transcript-list").children[0].children;
  assert.equal(rows.length, 2);
  for (const row of rows) {
    const reference = row.children.find(child => child.className === "transcript-reference");
    assert.equal(reference.textContent, maliciousText);
    assert.equal(reference.children.length, 0); // reference has no word buttons or parsed HTML
    const source = row.children.find(child => child.className === "transcript-text");
    assert.ok(source.children.some(child => child.className === "transcript-word"));
  }
  events.onReady();
  assert.equal(timers.size, 1);
  assert.equal(document.getElementById("current-dutch-caption").textContent, "Hallo.");
  assert.equal(document.getElementById("current-english-caption").textContent, "");
  seconds = 0.75;
  events.onStateChange({ data: window.YT.PlayerState.PAUSED });
  assert.equal(timers.size, 1);
  assert.equal(document.getElementById("current-english-caption").textContent, maliciousText);
  seconds = 1.5; // seek inside a Dutch gap while still paused
  timers.get(1)();
  assert.equal(document.getElementById("current-dutch-caption").textContent, "");
  assert.equal(document.getElementById("current-english-caption").textContent, maliciousText);
  seconds = 4;
  timers.get(1)();
  assert.equal(document.getElementById("current-english-caption").textContent, "");
});
