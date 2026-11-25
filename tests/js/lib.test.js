const test = require("node:test");
const assert = require("node:assert/strict");
const L = require("../../monitor/web/lib.js");

test("formatBytes picks readable units", () => {
  assert.equal(L.formatBytes(0), "0 B");
  assert.equal(L.formatBytes(1023), "1023 B");
  assert.equal(L.formatBytes(1536), "1.50 KB");
  assert.equal(L.formatBytes(10 * 1024 * 1024), "10.0 MB");
  assert.equal(L.formatBytes(250 * 1024 ** 3), "250 GB");
  assert.equal(L.formatBytes(-1), "n/a");
  assert.equal(L.formatBytes(NaN), "n/a");
  assert.equal(L.formatRate(2048), "2.00 KB/s");
});

test("formatDuration and formatAgo", () => {
  assert.equal(L.formatDuration(59), "0m 59s");
  assert.equal(L.formatDuration(3700), "1h 1m");
  assert.equal(L.formatDuration(90000), "1d 1h");
  assert.equal(L.formatAgo(500), "just now");
  assert.equal(L.formatAgo(4000), "4s ago");
  assert.equal(L.formatAgo(125000), "2m ago");
});

test("niceMax rounds up to a tidy axis limit", () => {
  assert.equal(L.niceMax(0), 1);
  assert.equal(L.niceMax(1), 1);
  assert.equal(L.niceMax(1.1), 2);
  assert.equal(L.niceMax(3), 5);
  assert.equal(L.niceMax(730), 1000);
  assert.equal(L.niceMax(1500000), 2000000);
});

test("seriesPoints maps values into the plot and clamps", () => {
  assert.deepEqual(L.seriesPoints([], 100, 50, 10), []);
  assert.deepEqual(L.seriesPoints([0, 5, 10], 100, 50, 10), [[0, 50], [50, 25], [100, 0]]);
  assert.deepEqual(L.seriesPoints([-5, 20], 100, 50, 10), [[0, 50], [100, 0]]);
  assert.deepEqual(L.seriesPoints([5], 100, 50, 10), [[100, 25]]);
});

test("summarize", () => {
  assert.deepEqual(L.summarize([]), { min: 0, max: 0, avg: 0, last: 0 });
  assert.deepEqual(L.summarize([2, 8, 5]), { min: 2, max: 8, avg: 5, last: 5 });
});

test("connectionState says live only while readings keep arriving", () => {
  const base = { paused: false, receivedAny: true, intervalMs: 2000 };
  assert.equal(L.connectionState({ ...base, sinceLastMs: 1500 }), "live");
  assert.equal(L.connectionState({ ...base, sinceLastMs: 7000 }), "live");
  assert.equal(L.connectionState({ ...base, sinceLastMs: 7001 }), "stale");
  assert.equal(L.connectionState({ ...base, receivedAny: false, sinceLastMs: 0 }), "connecting");
  assert.equal(L.connectionState({ ...base, paused: true, sinceLastMs: 99999 }), "paused");
});

test("windowSlice keeps the newest samples for the chosen window", () => {
  const items = Array.from({ length: 200 }, (_, i) => i);
  assert.equal(L.windowSlice(items, 60, 2).length, 30);
  assert.equal(L.windowSlice(items, 300, 2)[0], 50);
  assert.equal(L.windowSlice([1], 60, 2).length, 1);
});
