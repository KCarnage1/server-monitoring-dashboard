(function (root, factory) {
  if (typeof module === "object" && module.exports) module.exports = factory();
  else root.MonitorLib = factory();
})(typeof self !== "undefined" ? self : this, function () {
  const UNITS = ["B", "KB", "MB", "GB", "TB"];

  function formatBytes(n) {
    if (!Number.isFinite(n) || n < 0) return "n/a";
    let i = 0;
    while (n >= 1024 && i < UNITS.length - 1) { n /= 1024; i++; }
    return (i === 0 || n >= 100 ? Math.round(n) : n.toFixed(n >= 10 ? 1 : 2)) + " " + UNITS[i];
  }

  const formatRate = n => formatBytes(n) + "/s";

  function formatDuration(sec) {
    if (!Number.isFinite(sec) || sec < 0) return "n/a";
    const d = Math.floor(sec / 86400), h = Math.floor((sec % 86400) / 3600), m = Math.floor((sec % 3600) / 60);
    if (d > 0) return d + "d " + h + "h";
    if (h > 0) return h + "h " + m + "m";
    return m + "m " + Math.floor(sec % 60) + "s";
  }

  function formatAgo(ms) {
    if (ms < 1500) return "just now";
    const s = Math.round(ms / 1000);
    return s < 60 ? s + "s ago" : Math.round(s / 60) + "m ago";
  }

  // Round a maximum up to a tidy axis limit (1, 2, 5 times a power of 1024-ish ten).
  function niceMax(v) {
    if (!(v > 0)) return 1;
    const p = Math.pow(10, Math.floor(Math.log10(v)));
    const f = v / p;
    return (f <= 1 ? 1 : f <= 2 ? 2 : f <= 5 ? 5 : 10) * p;
  }

  function seriesPoints(values, width, height, max) {
    const n = values.length;
    if (n === 0) return [];
    return values.map((v, i) => [n === 1 ? width : (i / (n - 1)) * width, height - Math.min(Math.max(v, 0), max) / max * height]);
  }

  function summarize(values) {
    if (!values.length) return { min: 0, max: 0, avg: 0, last: 0 };
    let min = Infinity, max = -Infinity, sum = 0;
    for (const v of values) { if (v < min) min = v; if (v > max) max = v; sum += v; }
    return { min, max, avg: sum / values.length, last: values[values.length - 1] };
  }

  // paused > connecting (nothing received yet) > stale (nothing for 3 sample intervals) > live
  function connectionState({ paused, receivedAny, sinceLastMs, intervalMs }) {
    if (paused) return "paused";
    if (!receivedAny) return "connecting";
    return sinceLastMs > intervalMs * 3 + 1000 ? "stale" : "live";
  }

  function windowSlice(items, seconds, intervalSec) {
    const n = Math.max(2, Math.round(seconds / intervalSec));
    return items.slice(-n);
  }

  return { formatBytes, formatRate, formatDuration, formatAgo, niceMax, seriesPoints, summarize, connectionState, windowSlice };
});
