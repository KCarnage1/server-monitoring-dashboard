(function () {
  "use strict";
  const L = window.MonitorLib;
  const $ = id => document.getElementById(id);
  const css = n => getComputedStyle(document.documentElement).getPropertyValue(n).trim();

  let info = { interval: 2, history: 150, thresholds: { warn: 75, crit: 90 } };
  let history = [];
  let paused = false, receivedAny = false, lastRecv = 0, windowSec = 300;
  let token = new URLSearchParams(location.search).get("token") || "";
  let lastAlertKey = null, lastNoteAt = 0;
  if (token) stripTokenFromAddress();

  function stripTokenFromAddress() {
    const u = new URL(location.href);
    u.searchParams.delete("token");
    window.history.replaceState(null, "", u.pathname + u.search + u.hash);
  }

  const api = path => path + (token ? (path.includes("?") ? "&" : "?") + "token=" + encodeURIComponent(token) : "");

  async function getJSON(path) {
    const r = await fetch(api(path), { cache: "no-store" });
    if (r.status === 401) throw Object.assign(new Error("auth"), { auth: true });
    if (!r.ok) throw new Error("HTTP " + r.status);
    return r.json();
  }

  function setConn(state, text) {
    document.querySelector("#conn .led").dataset.state = state;
    $("conn-text").textContent = text;
  }

  function updateConn() {
    const sinceLastMs = performance.now() - lastRecv;
    const state = L.connectionState({ paused, receivedAny, sinceLastMs, intervalMs: info.interval * 1000 });
    const label = { paused: "Paused", connecting: "Connecting", stale: "Stale: no new reading", live: "Live" }[state];
    setConn(state, label);
    $("conn-age").textContent = receivedAny ? "last reading " + L.formatAgo(sinceLastMs) + ", every " + info.interval + "s" : "";
  }

  function showError(err) {
    const msg = err && err.auth
      ? "This monitor needs a token. Open the address with ?token=YOUR_TOKEN on the end."
      : "Can't reach the monitor. It will keep trying.";
    $("alerts-empty").textContent = msg;
    $("alerts-empty").hidden = false;
    $("alerts").dataset.level = "crit";
    setConn("stale", err && err.auth ? "Token needed" : "Disconnected");
  }

  /* ---------- drawing ---------- */
  function drawChart(canvas, series, { max, lines = [] }) {
    const dpr = window.devicePixelRatio || 1, w = canvas.clientWidth, h = canvas.clientHeight;
    if (!w || !h) return;
    canvas.width = Math.round(w * dpr); canvas.height = Math.round(h * dpr);
    const ctx = canvas.getContext("2d");
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    ctx.clearRect(0, 0, w, h);
    ctx.strokeStyle = "#334155"; ctx.lineWidth = 1; ctx.font = "11px " + css("--mono"); ctx.fillStyle = "#94a3b8";
    const padL = 0, plotH = h - 4;
    for (let i = 0; i <= 4; i++) {
      const y = Math.round(2 + (plotH * i) / 4) + .5;
      ctx.beginPath(); ctx.moveTo(padL, y); ctx.lineTo(w, y); ctx.stroke();
    }
    ctx.textBaseline = "top"; ctx.textAlign = "left";
    ctx.fillText(series.fmt(max), 4, 4);
    ctx.textBaseline = "bottom"; ctx.fillText(series.fmt(0), 4, h - 3);
    for (const ln of lines) {
      const y = 2 + plotH - (ln.at / max) * plotH;
      ctx.setLineDash([4, 4]); ctx.strokeStyle = ln.color; ctx.beginPath(); ctx.moveTo(0, y); ctx.lineTo(w, y); ctx.stroke(); ctx.setLineDash([]);
    }
    for (const s of series.lines) {
      const pts = L.seriesPoints(s.values, w, plotH, max).map(([x, y]) => [x, y + 2]);
      if (!pts.length) continue;
      ctx.beginPath(); pts.forEach(([x, y], i) => (i ? ctx.lineTo(x, y) : ctx.moveTo(x, y)));
      ctx.strokeStyle = s.color; ctx.lineWidth = 2; ctx.lineJoin = "round"; ctx.stroke();
      if (s.fill) {
        ctx.lineTo(pts[pts.length - 1][0], h - 2); ctx.lineTo(pts[0][0], h - 2); ctx.closePath();
        ctx.globalAlpha = .18; ctx.fillStyle = s.color; ctx.fill(); ctx.globalAlpha = 1; ctx.fillStyle = "#94a3b8";
      }
    }
  }

  function stateText(el, state) {
    el.innerHTML = "";
    const led = document.createElement("span"); led.className = "led"; led.dataset.state = state; led.setAttribute("aria-hidden", "true");
    el.append(led, document.createTextNode({ ok: "OK", warn: "WARN", crit: "CRIT" }[state]));
  }

  function render() {
    if (!history.length) return;
    const s = history[history.length - 1];
    const view = L.windowSlice(history, windowSec, info.interval);
    const t = info.thresholds, ok = css("--ok"), blue = css("--info"), warn = css("--warn"), crit = css("--crit");

    $("host").textContent = info.hostname || "Server monitor";
    $("hostmeta").textContent = [info.os, info.cores + " cores", "up " + L.formatDuration(s.uptime)].filter(Boolean).join(" · ");

    $("cpu-v").textContent = s.cpu.percent.toFixed(0) + "%";
    $("cpu-sub").textContent = s.cpu.load ? "load " + s.cpu.load.map(v => v.toFixed(2)).join(" ") : "";
    stateText($("cpu-state"), s.cpu.status);
    $("mem-v").textContent = s.memory.percent.toFixed(0) + "%";
    $("mem-sub").textContent = L.formatBytes(s.memory.used) + " of " + L.formatBytes(s.memory.total) + (s.memory.swap_percent ? ", swap " + s.memory.swap_percent.toFixed(0) + "%" : "");
    stateText($("mem-state"), s.memory.status);
    $("net-in").textContent = L.formatRate(s.net.in); $("net-out").textContent = L.formatRate(s.net.out);
    $("io-r").textContent = L.formatRate(s.diskio.read); $("io-w").textContent = L.formatRate(s.diskio.write);

    const pct = fmt => v => Math.round(v) + "%";
    const cpuV = view.map(x => x.cpu.percent), memV = view.map(x => x.memory.percent);
    drawChart($("c-cpu"), { fmt: pct(), lines: [{ values: cpuV, color: blue, fill: true }] }, { max: 100, lines: [{ at: t.crit, color: crit }] });
    drawChart($("c-mem"), { fmt: pct(), lines: [{ values: memV, color: ok, fill: true }] }, { max: 100, lines: [{ at: t.crit, color: crit }] });
    const inV = view.map(x => x.net.in), outV = view.map(x => x.net.out), rV = view.map(x => x.diskio.read), wV = view.map(x => x.diskio.write);
    drawChart($("c-net"), { fmt: L.formatRate, lines: [{ values: inV, color: blue }, { values: outV, color: warn }] }, { max: L.niceMax(Math.max(...inV, ...outV, 1024)) });
    drawChart($("c-io"), { fmt: L.formatRate, lines: [{ values: rV, color: blue }, { values: wV, color: warn }] }, { max: L.niceMax(Math.max(...rV, ...wV, 1024)) });

    // chart text alternatives change at most every 5 seconds so screen readers aren't flooded
    if (performance.now() - lastNoteAt > 5000) {
      lastNoteAt = performance.now();
      const c = L.summarize(cpuV), m = L.summarize(memV), ni = L.summarize(inV), no = L.summarize(outV), ir = L.summarize(rV), iw = L.summarize(wV);
      const span = view.length * info.interval >= 90 ? Math.round(view.length * info.interval / 60) + " min" : Math.round(view.length * info.interval) + "s";
      const note = (id, canvas, text) => { $(id).textContent = text; $(canvas).setAttribute("aria-label", text); };
      note("cpu-note", "c-cpu", "Last " + span + ": average " + c.avg.toFixed(0) + "%, peak " + c.max.toFixed(0) + "%. Dashed line marks " + t.crit + "%.");
      note("mem-note", "c-mem", "Last " + span + ": average " + m.avg.toFixed(0) + "%, peak " + m.max.toFixed(0) + "%.");
      note("net-note", "c-net", "Last " + span + ": in peaked at " + L.formatRate(ni.max) + " (blue), out at " + L.formatRate(no.max) + " (amber).");
      note("io-note", "c-io", "Last " + span + ": reads peaked at " + L.formatRate(ir.max) + " (blue), writes at " + L.formatRate(iw.max) + " (amber).");
    }

    const cores = $("cores");
    if (cores.children.length !== s.cpu.cores.length) cores.innerHTML = s.cpu.cores.map(() => '<li><span></span><div class="meter"><i></i></div></li>').join("");
    s.cpu.cores.forEach((v, i) => {
      const li = cores.children[i], st = v >= t.crit ? "crit" : v >= t.warn ? "warn" : "ok";
      li.firstChild.textContent = "core " + i + "  " + v.toFixed(0) + "%";
      const bar = li.querySelector("i"); bar.style.width = Math.min(100, v) + "%"; bar.dataset.state = st;
    });

    const disks = $("disks");
    disks.innerHTML = s.disks.length ? s.disks.map(d =>
      '<li><div class="row"><span>' + esc(d.mount) + "</span><span>" + L.formatBytes(d.used) + " of " + L.formatBytes(d.total) + " (" + d.percent.toFixed(0) + "%)" + (d.readonly ? ", read-only" : "") +
      '</span></div><div class="meter" role="meter" aria-label="' + esc(d.mount) + ' used" aria-valuemin="0" aria-valuemax="100" aria-valuenow="' + d.percent.toFixed(0) + '"><i data-w="' +
      Math.min(100, d.percent) + '" data-state="' + d.status + '"></i></div></li>').join("") : '<li class="meta">No writable disks found.</li>';
    // widths go through the DOM because the page's CSP forbids inline style attributes
    disks.querySelectorAll("i[data-w]").forEach(i => { i.style.width = i.dataset.w + "%"; });

    const tbody = $("procs");
    if (!info.processes) $("u-proc").hidden = true;
    else tbody.innerHTML = s.processes.length ? s.processes.map(p => "<tr><td>" + esc(p.name) + '</td><td class="num">' + p.pid + '</td><td class="num">' + p.cpu.toFixed(1) + '%</td><td class="num">' + p.mem.toFixed(1) + "%</td></tr>").join("") :
      '<tr><td colspan="4" class="meta">No process data.</td></tr>';

    renderAlerts(s);
  }

  function renderAlerts(s) {
    const box = $("alerts"), list = $("alert-list"), empty = $("alerts-empty");
    const worst = s.alerts.some(a => a.level === "crit") ? "crit" : s.alerts.length ? "warn" : "ok";
    box.dataset.level = worst;
    const key = s.alerts.map(a => a.metric + a.level).join("|");
    if (key === lastAlertKey) return;
    lastAlertKey = key;
    list.innerHTML = s.alerts.map(a => '<li><span class="tag ' + a.level + '">' + a.level.toUpperCase() + "</span><span>" + esc(a.message) + "</span></li>").join("");
    empty.hidden = s.alerts.length > 0;
    empty.textContent = "All clear. Nothing is over its limit.";
  }

  const esc = s => String(s).replace(/[&<>"']/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));

  /* ---------- data ---------- */
  function accept(snap) {
    receivedAny = true; lastRecv = performance.now();
    if (history.length && history[history.length - 1].seq === snap.seq) return;
    history.push(snap);
    if (history.length > info.history) history.shift();
    if (!paused) render();
    updateConn();
  }

  function stream() {
    const es = new EventSource(api("/api/stream"));
    es.onmessage = e => accept(JSON.parse(e.data));
    es.onerror = () => { if (es.readyState === EventSource.CLOSED) setTimeout(stream, 3000); updateConn(); };
  }

  async function start() {
    try {
      info = Object.assign(info, await getJSON("/api/info"));
      const past = await getJSON("/api/history");
      history = past.slice(0, -1);
      if (past.length) accept(past[past.length - 1]);
      stream();
    } catch (e) { showError(e); setTimeout(start, 3000); }
  }

  /* ---------- controls ---------- */
  $("pause").addEventListener("click", e => {
    paused = !paused;
    e.currentTarget.setAttribute("aria-pressed", paused);
    e.currentTarget.textContent = paused ? "Resume" : "Pause";
    if (!paused) render();
    updateConn();
  });
  document.querySelectorAll("[data-window]").forEach(b => b.addEventListener("click", () => {
    windowSec = +b.dataset.window;
    document.querySelectorAll("[data-window]").forEach(x => x.setAttribute("aria-pressed", x === b));
    lastNoteAt = 0; render();
  }));
  document.addEventListener("keydown", e => { if (e.key === "p" && !e.ctrlKey && !e.metaKey && !e.altKey && !/input|textarea/i.test(e.target.tagName)) $("pause").click(); });
  window.addEventListener("resize", () => { if (!paused) render(); });
  setInterval(updateConn, 1000);
  start();
})();
