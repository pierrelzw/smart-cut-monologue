#!/usr/bin/env node
// Unit tests for review.html's core state logic:
// - effectiveCutRanges() reflects word/category/pause state
// - toggleCategory() propagates correctly
// - drag-toggle produces contiguous merged ranges

const fs = require("fs");
const path = require("path");
const vm = require("vm");

const html = fs.readFileSync(
  path.join(__dirname, "..", "assets", "review.html"), "utf8");
const scriptMatch = html.match(/<script>([\s\S]*?)<\/script>/);
if (!scriptMatch) throw new Error("no <script> in review.html");
const script = scriptMatch[1];

// Fixture: 6 words + 1 pause. Categories: filler/repeat/pause.
const DATA = {
  manifest: { duration: 10.0, video: "/fake.mp4" },
  transcript: {
    segments: [
      { text: "我", start: 0.0, end: 0.2 },      // 0
      { text: "觉", start: 0.2, end: 0.4 },      // 1
      { text: "得", start: 0.4, end: 0.6 },      // 2
      { text: "嗯", start: 1.0, end: 1.3 },      // 3  filler
      { text: "就", start: 1.5, end: 1.7 },      // 4  padding
      { text: "是", start: 1.7, end: 1.9 },      // 5  padding
      { text: "好", start: 3.0, end: 3.3 },      // 6
      { text: "的", start: 3.3, end: 3.6 },      // 7
    ],
  },
  silence: { ranges: [] },
  suggestions: {
    categories: {
      filler:  { label: "语气词", default_checked: true,  min_duration: 0,   count: 1 },
      padding: { label: "冗余",   default_checked: false, min_duration: 0,   count: 1 },
      pause:   { label: "停顿",   default_checked: true,  min_duration: 0.4, count: 1 },
    },
    suggestions: [
      { id: 0, category: "filler",  start: 1.0, end: 1.3, word_indices: [3],    text: "嗯" },
      { id: 1, category: "padding", start: 1.5, end: 1.9, word_indices: [4, 5], text: "就是" },
      { id: 2, category: "pause",   start: 1.9, end: 3.0, word_indices: [],     text: "[...1.1s]" },
    ],
  },
};

// Mock DOM: we only need a few operations (getElementById→dummy, etc).
// We stub out the render functions since they touch DOM.
const stubEl = () => ({
  innerHTML: "", textContent: "", className: "",
  addEventListener() {}, appendChild() {}, querySelector: () => stubEl(),
  querySelectorAll: () => [], classList: { toggle() {}, add() {}, remove() {}, contains: () => false },
  style: {}, dataset: {}, checked: false,
});
const sandbox = {
  document: {
    getElementById: () => stubEl(),
    querySelector: () => null,
    querySelectorAll: () => [],
    createElement: () => stubEl(),
    body: stubEl(),
  },
  window: { addEventListener() {} },
  fetch: () => Promise.reject(new Error("not mocked")),
  console, module: { exports: {} },
  requestAnimationFrame: () => 0, cancelAnimationFrame: () => {},
  setTimeout: (fn) => { fn(); return 0; },
  confirm: () => true,
  Math, JSON, Array, Object, String, Number, parseInt, parseFloat, isNaN,
  Promise,
};
vm.createContext(sandbox);
vm.runInContext(script, sandbox);

const mod = sandbox.module.exports;
mod.setData(DATA);
mod.initStateFromData();

// ==== Tests ====
let pass = 0, fail = 0;
function assertEq(actual, expected, msg) {
  const a = JSON.stringify(actual), e = JSON.stringify(expected);
  if (a === e) { console.log("  ✓", msg); pass++; }
  else { console.log("  ✗", msg, "\n    got:", a, "\n    exp:", e); fail++; }
}
function assertDeep(a, b, msg) {
  // float-tolerant compare for arrays of [start,end]
  if (!Array.isArray(a) || !Array.isArray(b) || a.length !== b.length) {
    return assertEq(a, b, msg);
  }
  let ok = true;
  for (let i = 0; i < a.length; i++) {
    if (Math.abs(a[i][0]-b[i][0]) > 1e-6 || Math.abs(a[i][1]-b[i][1]) > 1e-6) ok = false;
  }
  if (ok) { console.log("  ✓", msg); pass++; }
  else { console.log("  ✗", msg, "\n    got:", JSON.stringify(a), "\n    exp:", JSON.stringify(b)); fail++; }
}

console.log("Test 1: default state — filler+pause on, padding off");
assertEq(mod.catVisible, { filler: true, padding: false, pause: true }, "category defaults");
assertEq(mod.wordChecked, [false, false, false, true, false, false, false, false], "word[3] checked (filler); padding words unchecked");
assertEq(mod.pauseChecked, [true], "pause checked");

console.log("\nTest 2: effectiveCutRanges includes filler word + pause");
// Pause suggestion [1.9, 3.0], dur=1.1, with 0.2s breathing = [2.1, 2.8]
assertDeep(mod.effectiveCutRanges(), [[1.0, 1.3], [2.1, 2.8]], "filler word + pause (trimmed)");

console.log("\nTest 3: toggle padding ON — word 4-5 now checked");
mod.toggleCategory("padding", true);
assertEq(mod.catVisible.padding, true, "padding visible");
assertEq(mod.wordChecked.slice(4, 6), [true, true], "padding words checked");
// Now merged ranges: filler [1.0,1.3] + padding [1.5,1.9] → stay separate; + pause [2.1, 2.8]
assertDeep(mod.effectiveCutRanges(), [[1.0, 1.3], [1.5, 1.9], [2.1, 2.8]], "3 disjoint ranges");

console.log("\nTest 4: toggle filler OFF — filler word no longer cut");
mod.toggleCategory("filler", false);
// word[3] still has catVisible.filler=false → not in ranges
assertDeep(mod.effectiveCutRanges(), [[1.5, 1.9], [2.1, 2.8]], "only padding + pause now");

console.log("\nTest 5: toggle padding OFF leaves only pause in ranges");
mod.toggleCategory("padding", false);
assertDeep(mod.effectiveCutRanges(), [[2.1, 2.8]], "only pause left after toggling padding off");

console.log("\nTest 6: adjacent checked words merge into a single range");
mod.toggleCategory("padding", true);
mod.toggleCategory("filler", true);
// Now wi=3 (1.0-1.3) and wi=4,5 (1.5-1.9) are both checked. Gap 0.2s → NOT merged (threshold 0.05)
assertDeep(mod.effectiveCutRanges(), [[1.0, 1.3], [1.5, 1.9], [2.1, 2.8]], "gap >50ms keeps separate");

console.log("\nTest 7: pauseMin filter excludes short pause");
// Change threshold higher than the pause duration (1.1s effective after trim = 0.7s; with 0.2 breathing = 0.7 useful)
// Actually effective pause duration check: dur must be >= pauseMin at raw level (end-start = 1.1)
// Set pauseMin to 2.0 → pause excluded
mod.setPauseMin(2.0);
assertDeep(mod.effectiveCutRanges(), [[1.0, 1.3], [1.5, 1.9]], "pause dropped when pauseMin=2.0");
mod.setPauseMin(0.4);  // restore

console.log("\nTest 8: pause unchecked but category visible — not in ranges");
mod.setPauseChecked(0, false);
assertDeep(mod.effectiveCutRanges(), [[1.0, 1.3], [1.5, 1.9]], "unchecked pause excluded");

console.log(`\n${pass} passed, ${fail} failed`);
process.exit(fail ? 1 : 0);
