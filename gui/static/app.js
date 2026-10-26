"use strict";
// Bench diagnosis dashboard: hash router, server-sent events, SVG charts. No libraries, no network
// access beyond this laptop's server. All text is inserted with textContent (never innerHTML).

// ------------------------------------------------------------------ helpers
const $ = (sel, el = document) => el.querySelector(sel);
const SVGNS = "http://www.w3.org/2000/svg";

function build(el, attrs, kids) {
  for (const [k, v] of Object.entries(attrs || {})) {
    if (v == null || v === false) continue;
    if (k.startsWith("on") && typeof v === "function") el.addEventListener(k.slice(2), v);
    else if (k === "value") el.value = v;
    else if (k === "checked") el.checked = !!v;
    else el.setAttribute(k, v === true ? "" : v);
  }
  for (const kid of kids.flat(Infinity)) {
    if (kid == null || kid === false) continue;
    el.append(kid instanceof Node ? kid : document.createTextNode(String(kid)));
  }
  return el;
}
const h = (tag, attrs, ...kids) => build(document.createElement(tag), attrs, kids);
const s = (tag, attrs, ...kids) => build(document.createElementNS(SVGNS, tag), attrs, kids);

async function api(path, { method = "GET", body } = {}) {
  const r = await fetch(path, {
    method, headers: body ? { "Content-Type": "application/json" } : {},
    body: body ? JSON.stringify(body) : undefined,
  });
  const data = await r.json().catch(() => ({}));
  if (!r.ok) throw new Error(data.error || `${r.status} ${r.statusText}`);
  return data;
}

function toast(msg, kind = "") {
  const t = h("div", { class: `toast ${kind}` }, msg);
  $("#toasts").append(t);
  setTimeout(() => t.remove(), kind === "error" ? 8000 : 3500);
}
const fail = (e) => toast(e.message || String(e), "error");

// Every timestamp is shown in Africa/Lagos time (WAT), whatever the laptop's time zone.
const LAGOS = "Africa/Lagos";
const tfmt = new Intl.DateTimeFormat("en-GB", { timeZone: LAGOS, hour: "2-digit", minute: "2-digit", second: "2-digit", hourCycle: "h23" });
const dfmt = new Intl.DateTimeFormat("en-GB", { timeZone: LAGOS, day: "numeric", month: "short", year: "numeric", hour: "2-digit", minute: "2-digit", hourCycle: "h23" });
const time = (iso) => (iso ? tfmt.format(new Date(iso)) : "");
const when = (iso) => (iso ? `${dfmt.format(new Date(iso))} WAT` : "unknown");
const pct = (x) => (x == null ? "n/a" : `${Math.round(x * 100)}%`);
const num = (x, d = 1) => (x == null ? "n/a" : Number(x).toFixed(d));
const short = (c) => (c ? String(c).slice(0, 7) + (String(c).endsWith("+dirty") ? " (dirty)" : "") : "n/a");
const srcBadge = (src) => h("span", { class: `src ${src}`, title: src === "hardware" ? "Real bench" : src === "sim" ? "Simulation" : "Mixed sources" }, src);

function fmtArgs(args) {
  return Object.entries(args || {}).map(([k, v]) => `${k}=${typeof v === "object" ? JSON.stringify(v) : v}`).join(", ");
}
function fmtData(data) {
  if (data == null) return "";
  if (typeof data !== "object") return String(data);
  const out = Object.entries(data).map(([k, v]) =>
    `${k} ${typeof v === "number" ? +v.toFixed(3) : typeof v === "object" ? JSON.stringify(v) : v}`).join(", ");
  return out.length > 180 ? out.slice(0, 177) + "..." : out;
}
const table = (head, rows, attrs = {}) => h("div", { class: "table-wrap" },
  h("table", attrs, h("thead", {}, h("tr", {}, head.map((c) => (c instanceof Node ? c : h("th", {}, c))))), h("tbody", {}, rows)));
const th = (label, cls) => h("th", { class: cls }, label);
const empty = (...msg) => h("div", { class: "empty" }, ...msg);

// ------------------------------------------------------------------ global state
const S = { options: null, status: null, sse: "connecting", live: newLive(null), page: null };

function newLive(start) {
  return { start, id: start ? start.id : null, steps: [], engine: [], residuals: [], misfit: false, feed: [],
           probe: null, end: null, remaining: start ? start.budget : null, colors: new Map() };
}

// ------------------------------------------------------------------ server-sent events
// The browser reconnects by itself and sends Last-Event-ID; the server replays what was missed.
function connectEvents() {
  const es = new EventSource("/api/events");
  es.onopen = () => { S.sse = "ok"; renderTop(); refreshStatus(); };
  es.onerror = () => { S.sse = "lost"; renderTop(); };
  es.onmessage = (m) => {
    try { applyEvent(JSON.parse(m.data)); } catch (e) { console.error("bad event", e, m.data); }
  };
}

function applyEvent(ev) {
  const p = ev.payload || {};
  if (ev.kind === "bench") {
    onBench(p, ev);
  } else if (ev.kind === "session_start") {
    S.live = newLive({ ...p, t: ev.t });
  } else {
    const L = S.live;
    if (!L.id || ev.session_id !== L.id) return; // belongs to an older session
    switch (ev.kind) {
      case "step":
        L.steps.push({ ...p, t: ev.t });
        if (p.remaining != null) L.remaining = p.remaining;
        break;
      case "engine":
        L.engine.push(p); L.misfit = !!p.misfit; L.residuals = p.residuals || [];
        break;
      case "agent": case "log":
        L.feed.push({ kind: ev.kind, t: ev.t, ...p });
        break;
      case "probe_request":
        L.probe = { ...p, t: ev.t };
        break;
      case "probe_done":
        L.probe = null;
        L.feed.push({ kind: "log", t: ev.t, level: p.volts == null ? "WARNING" : "INFO", logger: "probe",
                      msg: p.volts == null ? "No reading entered before the timeout" : `Reading entered: ${p.volts} V` });
        break;
      case "session_end":
        L.end = p; L.probe = null; refreshStatus();
        break;
    }
  }
  scheduleRender();
}

function onBench(p, ev) {
  const L = S.live;
  const quiet = ["connected", "disconnected", "reconnected"];
  if (L.id && ev.session_id === L.id && !quiet.includes(p.event)) {
    L.feed.push({ kind: "log", t: ev.t, level: "WARNING", logger: "bench", msg: `Bench event: ${p.event}${p.detail ? ` (${p.detail})` : ""}` });
  }
  refreshStatus(); // the server's view of the link is the authority
}

let statusTimer = null;
function refreshStatus() {
  clearTimeout(statusTimer);
  statusTimer = setTimeout(async () => {
    try { S.status = await api("/api/status"); } catch (e) { S.status = null; }
    renderTop(); if (S.page === "live") renderLiveControls();
  }, 120);
}

let renderQueued = false;
function scheduleRender() {
  if (renderQueued) return;
  renderQueued = true;
  requestAnimationFrame(() => { renderQueued = false; if (S.page === "live") renderSession(); });
}

// ------------------------------------------------------------------ top bar
function renderTop() {
  const pill = $("#link-status"), banner = $("#banner");
  const b = S.status && S.status.bench;
  let text = "Simulation only", cls = "", alert = "";
  if (S.sse === "lost") { text = "Server unreachable"; cls = "bad"; alert = "Lost contact with the dashboard server. Retrying every 2 s; the bench firmware keeps its own limits."; }
  else if (b && b.mode && !b.connected) { text = "Bench link lost"; cls = "bad"; alert = `Bench connection lost (${b.mode}). Reconnecting; any running session will stop if the board reset.`; }
  else if (b && b.interrupted) { text = "Bench reset"; cls = "bad"; alert = "The bench board reset since it was connected. The session was marked interrupted. Reconnect the bench before the next session."; }
  else if (b && b.inhibited) { text = "Safe state latched"; cls = "warn"; }
  else if (b && b.connected) { text = b.mode === "hardware" ? "Bench connected" : "Mock bench connected"; cls = "ok"; }
  pill.className = `pill ${cls}`; pill.textContent = text;
  banner.hidden = !alert; banner.textContent = alert;
}

$("#safe-btn").addEventListener("click", async () => {
  try {
    const r = await api("/api/safe_state", { method: "POST" });
    toast(r.sent ? "Safe state sent: switched loads are off" : `Nothing sent: ${r.reason}`, r.sent ? "" : "error");
  } catch (e) { fail(e); }
  refreshStatus();
});

// ------------------------------------------------------------------ router
const PAGES = { live: pageLive, experiments: pageExperiments, models: pageModels, calibration: pageCalibration, tracker: pageTracker };

function route() {
  const [page, ...rest] = location.hash.replace(/^#\/?/, "").split("/");
  const name = PAGES[page] ? page : "live";
  S.page = name;
  for (const a of document.querySelectorAll(".top nav a")) {
    if (a.dataset.page === name) a.setAttribute("aria-current", "page"); else a.removeAttribute("aria-current");
  }
  const view = $("#view");
  view.replaceChildren();
  PAGES[name](view, rest.map(decodeURIComponent).join("/"));
}

// ------------------------------------------------------------------ live session
function pageLive(view) {
  const o = S.options || { methods: [], sim_scenarios: [], bench_scenarios: [], ports: [] };
  const portOptions = () => (o.ports.length ? o.ports.map((p) => h("option", { value: p.device }, `${p.device} (${p.description})`))
    : [h("option", { value: "" }, "No serial ports found")]);

  const bench = h("section", { class: "panel" },
    h("h2", {}, "Bench"),
    h("div", { id: "bench-state" }),
    h("form", { id: "bench-form", onsubmit: (e) => e.preventDefault() },
      h("div", { class: "form-row" },
        h("label", {}, "Connect to", h("select", { name: "mode" },
          h("option", { value: "mock" }, "Mock bench (simulated serial)"), h("option", { value: "hardware" }, "Real bench"))),
        h("label", { class: "hw-only" }, "Bench port", h("select", { name: "port" }, portOptions())),
        h("label", { class: "hw-only" }, "Injector port", h("select", { name: "injector_port" }, portOptions()))),
      h("div", { class: "actions" },
        h("button", { class: "primary", id: "connect-btn", type: "button", onclick: connectBench }, "Connect"),
        h("button", { id: "disconnect-btn", type: "button", onclick: () => api("/api/bench/disconnect", { method: "POST" }).then(refreshStatus, fail) }, "Disconnect"),
        h("button", { class: "link", type: "button", onclick: async () => { S.options = await api("/api/options"); route(); } }, "Rescan ports"))));

  const start = h("section", { class: "panel" },
    h("h2", {}, "Start a session"),
    h("form", { id: "start-form", onsubmit: (e) => e.preventDefault() },
      h("div", { class: "form-row" },
        h("label", {}, "Bench", h("select", { name: "mode" },
          h("option", { value: "sim" }, "Simulator"), h("option", { value: "mock" }, "Mock bench"), h("option", { value: "hardware" }, "Real bench"))),
        h("label", {}, "Method", h("select", { name: "method" }, o.methods.map((m) => h("option", { value: m }, METHOD_NAMES[m] || m)))),
        h("label", {}, "Scenario", h("select", { name: "scenario" })),
        h("label", {}, "Seed", h("input", { name: "seed", type: "number", min: 0, placeholder: "random" }))),
      h("div", { class: "form-row llm-only", style: "margin-top:.6rem" },
        h("label", {}, "Model", h("input", { name: "model", placeholder: "for example qwen3:8b", value: savedModel() })),
        h("label", {}, "Model server", h("select", { name: "server" }, h("option", { value: "ollama" }, "Ollama"), h("option", { value: "llamacpp" }, "llama.cpp server"))),
        h("label", { class: "check" }, h("input", { name: "think", type: "checkbox" }), "Thinking on")),
      h("label", { class: "check hw-run", style: "margin-top:.6rem" }, h("input", { name: "allow_stale_calibration", type: "checkbox" }), "Run even if the calibration is stale"),
      h("div", { class: "actions" },
        h("button", { class: "primary", id: "start-btn", type: "button", onclick: startSession }, "Start session"),
        h("button", { id: "stop-btn", type: "button", onclick: () => api("/api/session/stop", { method: "POST" }).then(() => toast("Stopping after the current step"), fail) }, "Stop session"))));

  view.append(
    h("div", { class: "page-head" }, h("div", {}, h("h1", {}, "Live session"),
      h("p", { class: "lede" }, "Watch one diagnosis at a time, step by step. Any number of tabs can follow the same session."))),
    h("div", { class: "grid cols-2" }, bench, start),
    h("div", { id: "session", class: "stack", style: "margin-top:1rem" }));

  const sf = $("#start-form"), bf = $("#bench-form");
  sf.mode.addEventListener("change", syncStartForm);
  sf.method.addEventListener("change", syncStartForm);
  bf.mode.addEventListener("change", syncStartForm);
  syncStartForm();
  renderLiveControls();
  renderSession();
}

const METHOD_NAMES = { fixed: "A: fixed sequence", tree: "B: decision tree", bayes: "C: Bayesian engine", llm: "D: LLM alone", hybrid: "E: LLM on the engine" };

function savedModel() {
  try { return localStorage.getItem("model") || ""; } catch (e) { return ""; }
}

function syncStartForm() {
  const sf = $("#start-form"), bf = $("#bench-form"), o = S.options;
  if (!sf || !o) return;
  const mode = sf.mode.value;
  const list = mode === "sim" ? o.sim_scenarios : mode === "mock" ? o.bench_scenarios : [];
  const keep = sf.scenario.value;
  sf.scenario.replaceChildren(...(mode === "hardware"
    ? [h("option", { value: "" }, "Chosen by the server, hidden until grading")]
    : [h("option", { value: "" }, "Random"), ...list.map((n) => h("option", { value: n }, n))]));
  if (list.includes(keep)) sf.scenario.value = keep;
  sf.scenario.disabled = mode === "hardware";
  for (const el of sf.querySelectorAll(".llm-only")) el.hidden = !["llm", "hybrid"].includes(sf.method.value);
  for (const el of sf.querySelectorAll(".hw-run")) el.hidden = mode !== "hardware";
  for (const el of bf.querySelectorAll(".hw-only")) el.hidden = bf.mode.value !== "hardware";
}

async function connectBench() {
  const f = $("#bench-form");
  const btn = $("#connect-btn");
  btn.disabled = true;
  try {
    const b = await api("/api/bench/connect", { method: "POST", body: { mode: f.mode.value, port: f.port.value || null, injector_port: f.injector_port.value || null } });
    toast(`Connected: firmware ${b.firmware || "unknown"}`);
  } catch (e) { fail(e); }
  btn.disabled = false;
  refreshStatus();
}

async function startSession() {
  const f = $("#start-form");
  const body = { mode: f.mode.value, method: f.method.value, scenario: f.scenario.value || null,
                 seed: f.seed.value === "" ? null : Number(f.seed.value), server: f.server.value,
                 think: f.think.checked, allow_stale_calibration: f.allow_stale_calibration.checked };
  if (["llm", "hybrid"].includes(body.method)) {
    body.model = f.model.value.trim();
    if (!body.model) return toast("Type a model name for the LLM methods, for example qwen3:8b", "error");
    try { localStorage.setItem("model", body.model); } catch (e) { /* private window: not remembered */ }
  }
  $("#start-btn").disabled = true;
  try { const r = await api("/api/session/start", { method: "POST", body }); toast(`Session ${r.id} started`); }
  catch (e) { fail(e); }
  refreshStatus();
}

function renderLiveControls() {
  const box = $("#bench-state");
  if (!box) return;
  const st = S.status || {}, b = st.bench || {}, running = !!st.session;
  const rows = [];
  if (!b.mode) {
    rows.push(h("p", { class: "muted" }, "No bench connected. Simulator sessions need none."));
  } else {
    rows.push(h("p", {}, h("span", { class: `pill ${b.connected ? "ok" : "bad"}` }, b.connected ? "Connected" : "Link lost, reconnecting"),
      " ", b.mode === "hardware" ? `Real bench on ${b.port}` : "Mock bench (results labelled sim)"));
    rows.push(h("dl", { class: "small", style: "display:grid;grid-template-columns:max-content 1fr;gap:.1rem .8rem;margin:.4rem 0" },
      h("dt", { class: "muted" }, "Bench firmware"), h("dd", { style: "margin:0" }, b.firmware || "unknown"),
      h("dt", { class: "muted" }, "Injector firmware"), h("dd", { style: "margin:0" }, b.injector_fw || "unknown"),
      h("dt", { class: "muted" }, "Calibration"), h("dd", { style: "margin:0" }, b.calibration || "none on the board")));
    if (b.calibration_problem) rows.push(h("p", { class: "warnbox" }, b.calibration_problem));
    if (b.inhibited) {
      rows.push(h("div", { class: "warnbox" }, h("p", { style: "margin:0 0 .5rem" }, "Safe state latched: loads are off and the firmware refuses load tests. Check the bench, then clear it to run the next session."),
        h("button", { type: "button", disabled: running, onclick: () => api("/api/resume", { method: "POST" }).then(() => { toast("Safe state cleared"); refreshStatus(); }, fail) }, "Clear safe state")));
    }
    if (b.interrupted) rows.push(h("p", { class: "warnbox" }, "The board reset since it was connected. Reconnect it."));
  }
  box.replaceChildren(...rows);
  const set = (id, dis) => { const el = $(id); if (el) el.disabled = dis; };
  set("#start-btn", running);
  set("#stop-btn", !running);
  set("#connect-btn", running);
  set("#disconnect-btn", running || !b.mode);
}

function renderSession() {
  const root = $("#session");
  if (!root) return;
  const L = S.live;
  if (!L.id) {
    if (!root.firstChild) root.append(empty("No session has run since the dashboard started. Pick a method above and start one."));
    return;
  }
  if (root.dataset.sid !== L.id) { // new session or a fresh page: build the skeleton once
    root.dataset.sid = L.id;
    root._r = { steps: 0, feed: 0, probe: undefined };
    root.replaceChildren(
      h("section", { class: "panel", id: "s-head" }),
      h("section", { class: "panel probe", id: "s-probe", hidden: true }),
      h("div", { class: "grid cols-main" },
        h("section", { class: "panel" }, h("h2", {}, "Top hypotheses after each step"),
          h("p", { class: "small muted" }, "Posterior probability from the Bayesian engine, following the readings of whichever method runs. Dashed line: the engine's diagnosis threshold."),
          h("div", { id: "s-chart" })),
        h("section", { class: "panel", id: "s-diag" })),
      h("div", { class: "grid cols-main" },
        h("section", { class: "panel" }, h("h2", {}, "Steps"),
          h("div", { class: "scroll", id: "s-steps-box" },
            table([th("#", "num"), th("Tool"), th("Arguments"), th("Reading"), th("Cost", "num"), th("Budget left", "num")],
              [], { class: "steps", id: "s-steps" }))),
        h("section", { class: "panel" }, h("h2", {}, "Model replies and warnings"),
          h("div", { class: "scroll", id: "s-feed-box" }, h("ul", { class: "feed", id: "s-feed" })))));
  }
  const r = root._r;
  renderHead(L);
  if (r.probe !== L.probe) { r.probe = L.probe; renderProbe(L.probe); }
  drawPosterior($("#s-chart"), L);
  renderDiag(L);
  appendRows($("#s-steps tbody"), $("#s-steps-box"), L.steps, r, "steps", stepRow);
  appendRows($("#s-feed"), $("#s-feed-box"), L.feed, r, "feed", feedItem);
}

function appendRows(parent, box, items, r, key, make) {
  if (r[key] >= items.length) return;
  const stick = box.scrollHeight - box.scrollTop - box.clientHeight < 40;
  const frag = document.createDocumentFragment();
  for (let i = r[key]; i < items.length; i++) frag.append(make(items[i]));
  parent.append(frag);
  r[key] = items.length;
  if (stick) box.scrollTop = box.scrollHeight;
}

function renderHead(L) {
  const st = L.start, end = L.end;
  const budget = st.budget || 0, left = L.remaining == null ? budget : L.remaining;
  const frac = budget ? Math.max(0, Math.min(1, left / budget)) : 0;
  const scenario = st.scenario || (end && end.scenario) || null;
  const status = end ? end.status : "running";
  const kv = (k, ...v) => h("div", { class: "kv" }, h("span", {}, k), h("span", {}, ...v));
  $("#s-head").replaceChildren(h("div", { class: "session-head" },
    kv("Session", L.id),
    kv("Source", srcBadge(st.source), st.mode === "mock" ? " mock serial" : ""),
    kv("Method", METHOD_NAMES[st.method] || st.method, st.model ? ` (${st.model})` : ""),
    kv("Scenario", scenario ? h("b", {}, scenario) : h("span", { class: "muted" }, "hidden until grading")),
    kv("Seed", st.seed),
    kv("Status", h("span", { class: `pill ${status === "running" ? "ok" : end && end.correct ? "ok" : status === "diagnosed" ? "bad" : "warn"}` }, status)),
    kv(`Budget: ${budget - left} of ${budget} used`,
      h("div", { class: `gauge ${frac === 0 ? "empty" : frac < 0.25 ? "low" : ""}`, role: "meter", "aria-valuemin": 0, "aria-valuemax": budget, "aria-valuenow": left, "aria-label": "Budget left" },
        h("i", { style: `transform:scaleX(${frac})` })))));
}

function renderProbe(p) {
  const box = $("#s-probe");
  box.hidden = !p;
  if (!p) return box.replaceChildren();
  const input = h("input", { type: "number", step: "0.001", inputmode: "decimal", "aria-label": "Measured volts", required: true });
  const send = async (v) => {
    if (v === "" || Number.isNaN(Number(v))) return toast("Type the meter reading in volts", "error");
    try { await api("/api/probe", { method: "POST", body: { volts: Number(v) } }); toast(`Reading sent: ${v} V`); } catch (e) { fail(e); }
  };
  box.replaceChildren(
    h("h2", {}, "Measurement needed"),
    h("p", {}, p.instruction),
    h("p", { class: "small muted" }, `Red lead on ${p.point_a}, black lead on ${p.point_b}. The session waits up to ${p.timeout_s} s from ${time(p.t)}.`),
    h("form", { class: "reading", onsubmit: (e) => { e.preventDefault(); send(input.value); } },
      h("label", {}, "Meter reading (V)", input),
      h("button", { class: "primary", type: "submit" }, "Send reading"),
      p.hint != null ? h("button", { type: "button", onclick: () => send(p.hint) }, `Use the simulated meter: ${(+p.hint).toFixed(3)} V`) : null));
  input.focus();
}

function drawPosterior(box, L) {
  const W = Math.max(280, box.clientWidth || 600), H = 250, m = { l: 40, r: 14, t: 12, b: 28 };
  const iw = W - m.l - m.r, ih = H - m.t - m.b;
  const n = L.engine.length;
  const series = new Map();
  L.engine.forEach((e, i) => (e.top || []).forEach((t) => {
    const k = t.faults.join(" + ");
    if (!series.has(k)) series.set(k, []);
    series.get(k).push([i, t.p]);
  }));
  const lastP = (pts) => (pts[pts.length - 1][0] === n - 1 ? pts[pts.length - 1][1] : 0);
  const shown = [...series.entries()].sort((a, b) => lastP(b[1]) - lastP(a[1]) || Math.max(...b[1].map((x) => x[1])) - Math.max(...a[1].map((x) => x[1]))).slice(0, 6);
  for (const [k] of shown) if (!L.colors.has(k)) L.colors.set(k, `var(--c${(L.colors.size % 6) + 1})`);
  const x = (i) => m.l + (n <= 1 ? iw / 2 : (i / (n - 1)) * iw);
  const y = (p) => m.t + (1 - p) * ih;
  const svg = s("svg", { class: "chart", width: W, height: H, viewBox: `0 0 ${W} ${H}`, role: "img", "aria-label": "Probability of the top hypotheses after each step" });
  for (const g of [0, 0.25, 0.5, 0.75, 1]) {
    svg.append(s("line", { class: "gridline", x1: m.l, x2: W - m.r, y1: y(g), y2: y(g) }),
      s("text", { x: m.l - 6, y: y(g) + 4, "text-anchor": "end" }, `${g * 100}%`));
  }
  svg.append(s("line", { x1: m.l, x2: W - m.r, y1: y(0.7), y2: y(0.7), stroke: "var(--ink-2)", "stroke-dasharray": "4 4" }));
  const ticks = Math.min(n, 8);
  for (let j = 0; j < ticks; j++) {
    const i = ticks <= 1 ? 0 : Math.round((j / (ticks - 1)) * (n - 1));
    svg.append(s("text", { x: x(i), y: H - 8, "text-anchor": "middle" }, L.engine[i].step));
  }
  if (!n) svg.append(s("text", { x: m.l + iw / 2, y: m.t + ih / 2, "text-anchor": "middle" }, "Waiting for the first reading"));
  for (const [k, pts] of shown) {
    let d = "";
    pts.forEach(([i, p], j) => {
      const prev = pts[j - 1];
      if (!prev || prev[0] !== i - 1) d += `M${x(i)},${y(p)}`;
      else d += `H${x(i)}V${y(p)}`;
    });
    if (n === 1) d = `M${x(0) - 6},${y(pts[0][1])}H${x(0) + 6}`;
    svg.append(s("path", { d, fill: "none", stroke: L.colors.get(k), "stroke-width": 2.5, "stroke-linejoin": "round" }, s("title", {}, k)));
  }
  box.replaceChildren(svg, h("ul", { class: "legend" }, shown.map(([k, pts]) =>
    h("li", {}, h("i", { style: `background:${L.colors.get(k)}` }), h("span", {}, k), h("b", {}, pct(lastP(pts)))))));
}

function renderDiag(L) {
  const end = L.end, kids = [h("h2", {}, end ? "Result" : "Diagnosis so far")];
  if (end) {
    kids.push(h("p", { class: "result" }, h("strong", {}, end.status), end.elements && end.elements.length ? `: ${end.elements.join(", ")}` : ""));
    if (end.truth) kids.push(h("p", {}, "Ground truth: ", h("b", {}, end.truth.join(", ") || "none"), "  ",
      h("span", { class: end.correct ? "yes" : "no" }, end.correct ? "correct" : "not correct")));
    if (end.confidence != null) kids.push(h("p", { class: "small muted" }, `Confidence ${pct(end.confidence)}, cost ${end.cost}`));
    if (end.reason) kids.push(h("p", { class: "warnbox" }, end.reason));
    if (end.error) kids.push(h("p", { class: "warnbox" }, end.error));
    if (end.agent_stats) {
      const a = end.agent_stats, lat = (a.latency_s || []).slice().sort((p, q) => p - q);
      kids.push(h("p", { class: "small" }, `Model calls ${a.llm_calls}, invalid ${a.invalid}, plain-text replies parsed ${a.content_parsed}, ` +
        `no call ${a.no_call}, timeouts ${a.timeouts}, fallbacks ${a.fallbacks}` +
        (lat.length ? `, median latency ${lat[Math.floor(lat.length / 2)]} s` : "") + (a.loop_detected ? ", loop detected" : "")));
    }
  } else {
    const top = L.engine.length ? L.engine[L.engine.length - 1].top[0] : null;
    kids.push(top ? h("p", { class: "result" }, "Leading: ", h("strong", {}, top.faults.join(" + ")), ` at ${pct(top.p)}`)
      : h("p", { class: "muted" }, "No readings yet."));
  }
  kids.push(L.misfit ? h("p", { class: "misfit" }, "Misfit: no modelled fault explains these readings. This is the out-of-model flag.")
    : h("p", { class: "small muted" }, "Readings fit the model (no misfit flag)."));
  if (L.residuals.length) {
    kids.push(h("h3", { style: "margin-top:.8rem" }, "Largest residuals"),
      table([th("Reading"), th("Measured", "num"), th("Expected", "num"), th("z", "num")],
        L.residuals.map((r) => h("tr", {}, h("td", {}, h("code", {}, `${r.tool} ${fmtArgs(r.args)}`), h("span", { class: "cell-sub" }, r.reading)),
          h("td", { class: "num" }, r.measured), h("td", { class: "num" }, r.expected), h("td", { class: "num" }, r.z)))));
  }
  $("#s-diag").replaceChildren(...kids);
}

function stepRow(st) {
  const rejected = st.step == null;
  return h("tr", { class: rejected ? "rejected" : st.status !== "ok" ? "failed" : "" },
    h("td", { class: "num" }, rejected ? "no" : st.step),
    h("td", {}, h("code", {}, st.tool)),
    h("td", {}, h("code", {}, fmtArgs(st.args))),
    h("td", {}, rejected ? `Rejected by the guard: ${st.reason}` : st.status === "ok" ? fmtData(st.data) : `${st.status}: ${st.error || ""}`),
    h("td", { class: "num" }, st.cost ?? ""),
    h("td", { class: "num" }, st.remaining ?? ""));
}

function feedItem(f) {
  if (f.kind === "agent") {
    return h("li", {}, h("span", { class: "t" }, time(f.t)),
      f.content ? h("span", { style: "white-space:pre-wrap" }, f.content) : h("span", { class: "muted" }, "(no text)"),
      (f.tool_calls || []).map((c) => h("div", { class: "calls" }, `${c.name}(${typeof c.args === "string" ? c.args : JSON.stringify(c.args)})`)));
  }
  return h("li", { class: f.level }, h("span", { class: "t" }, time(f.t)), `${f.level === "INFO" ? "" : f.level + ": "}${f.msg}`,
    f.logger ? h("span", { class: "cell-sub" }, f.logger) : null);
}

// ------------------------------------------------------------------ experiments
async function pageExperiments(view, runId) {
  if (runId) return experimentDetail(view, runId);
  view.append(h("div", { class: "page-head" }, h("div", {}, h("h1", {}, "Experiments"),
    h("p", { class: "lede" }, "Every run in results/. Each run has one source; simulation and hardware are never combined."))));
  const box = h("div", { class: "panel" }, "Loading runs...");
  view.append(box);
  try {
    const runs = await api("/api/runs");
    if (!runs.length) return box.replaceChildren(empty("No runs yet. Run python scripts/run_experiment.py --trials 10 to make one."));
    box.replaceChildren(table(
      [th("Run"), th("Kind"), th("Source"), th("Date (WAT)"), th("Commit"), th("Methods"), th("Model"), th("Sessions", "num")],
      runs.map((r) => h("tr", { class: "click", tabindex: 0, onclick: () => (location.hash = `#/experiments/${encodeURIComponent(r.id)}`),
                               onkeydown: (e) => e.key === "Enter" && (location.hash = `#/experiments/${encodeURIComponent(r.id)}`) },
        h("td", {}, h("a", { href: `#/experiments/${encodeURIComponent(r.id)}` }, r.id), r.final ? h("span", { class: "cell-sub" }, "final run") : null),
        h("td", {}, r.kind, r.graded ? null : h("span", { class: "cell-sub" }, "not graded yet")),
        h("td", {}, srcBadge(r.source), r.legacy ? h("span", { class: "cell-sub" }, "early run, no source field (sim)") : null),
        h("td", {}, when(r.date)), h("td", {}, h("code", {}, short(r.commit))),
        h("td", {}, r.methods.join(", ")), h("td", {}, r.models.join(", ") || "none"), h("td", { class: "num" }, r.sessions)))));
  } catch (e) { box.replaceChildren(empty(`Could not list runs: ${e.message}`)); }
}

// The CLI prints Markdown; showing that same text as tables guarantees the GUI's numbers are the CLI's.
function markdownBlocks(md) {
  const out = [];
  let rows = null;
  const flush = () => {
    if (!rows) return;
    const cells = (l) => l.trim().replace(/^\||\|$/g, "").split("|").map((c) => c.trim());
    const body = rows.filter((l) => !/^\|\s*-/.test(l.trim()));
    out.push(table(cells(body[0]).map((c) => th(c)), body.slice(1).map((l) => h("tr", {}, cells(l).map((c) => h("td", { class: /^-?[\d.]+%?$/.test(c) ? "num" : "" }, c))))));
    rows = null;
  };
  for (const line of md.split("\n")) {
    if (line.trim().startsWith("|")) { (rows = rows || []).push(line); continue; }
    flush();
    if (line.startsWith("#")) out.push(h("h3", { style: "margin-top:.8rem" }, line.replace(/^#+\s*/, "")));
    else if (line.trim()) out.push(h("p", {}, line));
  }
  flush();
  return out;
}

async function experimentDetail(view, runId) {
  view.append(h("p", {}, h("a", { href: "#/experiments" }, "All runs")));
  const head = h("div", { class: "page-head" }), body = h("div", { class: "stack" }, h("div", { class: "panel" }, "Loading..."));
  view.append(head, body);
  let sum, idx;
  try { [sum, idx] = await Promise.all([api(`/api/runs/summary?run=${encodeURIComponent(runId)}`), api(`/api/runs/sessions?run=${encodeURIComponent(runId)}`)]); }
  catch (e) { return body.replaceChildren(empty(`Could not load run ${runId}: ${e.message}`)); }
  const r = sum.run;
  head.append(h("div", {}, h("h1", {}, `Run ${r.id} `, srcBadge(r.source)),
    h("p", { class: "lede" }, `${r.kind}, ${when(r.date)}, commit ${short(r.commit)}, policy ${r.policy_hash || "n/a"}, model ${r.models.join(", ") || "none"}`)),
    h("a", { href: `/api/runs/csv?run=${encodeURIComponent(runId)}`, download: true }, h("button", { type: "button" }, "Download CSV")));
  const kids = [h("section", { class: "panel", id: "cli-summary" }, h("h2", {}, "Summary per test set and method"),
    h("p", { class: "small muted" }, "The same table the command line printed for this run."), ...markdownBlocks(sum.markdown))];
  if (sum.scenarios.length) {
    kids.push(h("section", { class: "panel" }, h("h2", {}, "Per scenario"),
      h("p", { class: "small muted" }, "Correct out of trials, with the mean cost under each."),
      table([th("Scenario"), th("Set"), ...sum.methods.map((m) => th(m, "num"))],
        sum.scenarios.map((sc) => h("tr", {}, h("td", {}, h("b", {}, sc.scenario)), h("td", {}, sc.set || ""),
          sum.methods.map((m) => {
            const c = sc.cells[m];
            if (!c) return h("td", { class: "num muted" }, "not run");
            const heat = c.correct === 0 ? 0 : c.correct === c.n ? 2 : 1;
            return h("td", { class: `num heat-${heat}` }, `${c.correct}/${c.n}`, h("span", { class: "cell-sub" }, `cost ${num(c.mean_cost)}`));
          }))))));
    kids.push(h("section", { class: "panel" }, h("h2", {}, "Cost against accuracy ", srcBadge(r.source)), costAccuracy(sum.cost_accuracy, r.source)));
  }
  const drill = h("section", { class: "panel", id: "drill", hidden: true });
  kids.push(sessionList(runId, idx, drill), drill);
  body.replaceChildren(...kids);
}

function costAccuracy(points, source) {
  // Sized to the page in pixels (not scaled by viewBox) so the labels stay readable on a phone.
  const W = Math.max(300, Math.min(640, ($("#view").clientWidth || 640) - 70)), H = 300, m = { l: 48, r: 110, t: 14, b: 40 };
  const iw = W - m.l - m.r, ih = H - m.t - m.b;
  const xmax = Math.max(1, ...points.map((p) => p.mean_cost)) * 1.1;
  const x = (c) => m.l + (c / xmax) * iw, y = (a) => m.t + (1 - a) * ih;
  const svg = s("svg", { class: "chart", width: W, height: H, viewBox: `0 0 ${W} ${H}`, role: "img", "aria-label": `Mean cost against accuracy per method, ${source}` });
  for (const g of [0, 0.25, 0.5, 0.75, 1]) svg.append(s("line", { class: "gridline", x1: m.l, x2: m.l + iw, y1: y(g), y2: y(g) }), s("text", { x: m.l - 6, y: y(g) + 4, "text-anchor": "end" }, `${g * 100}%`));
  for (let j = 0; j <= 4; j++) { const c = (xmax * j) / 4; svg.append(s("text", { x: x(c), y: H - 22, "text-anchor": "middle" }, c.toFixed(0))); }
  svg.append(s("text", { x: m.l + iw / 2, y: H - 4, "text-anchor": "middle" }, "mean cost per session (budget units)"));
  points.forEach((p, i) => svg.append(
    s("circle", { cx: x(p.mean_cost), cy: y(p.accuracy), r: 6, fill: `var(--c${(i % 6) + 1})` }, s("title", {}, `${p.method}: ${pct(p.accuracy)} correct, mean cost ${num(p.mean_cost)}, n ${p.n}`)),
    s("text", { x: x(p.mean_cost) + 10, y: y(p.accuracy) + 4, style: "fill:var(--ink)" }, `${p.method} ${pct(p.accuracy)}`)));
  return svg;
}

function sessionList(runId, idx, drill) {
  const methods = [...new Set(idx.map((r) => r.method))], scen = [...new Set(idx.map((r) => r.scenario))];
  const fm = h("select", {}, h("option", { value: "" }, "All methods"), methods.map((m) => h("option", { value: m }, m)));
  const fs = h("select", {}, h("option", { value: "" }, "All scenarios"), scen.map((m) => h("option", { value: m }, m)));
  const tbody = h("tbody");
  const fill = () => {
    const rows = idx.filter((r) => (!fm.value || r.method === fm.value) && (!fs.value || r.scenario === fs.value));
    tbody.replaceChildren(...rows.map((r) => h("tr", { class: "click", tabindex: 0, onclick: () => openSession(runId, r.index, drill), onkeydown: (e) => e.key === "Enter" && openSession(runId, r.index, drill) },
      h("td", { class: "num" }, r.index), h("td", {}, r.method), h("td", {}, r.scenario), h("td", {}, r.status),
      h("td", {}, (r.elements || []).join(", ")), h("td", {}, (r.truth || []).join(", ")),
      h("td", {}, r.correct == null ? "" : h("span", { class: r.correct ? "yes" : "no" }, r.correct ? "yes" : "no")), h("td", { class: "num" }, r.cost))));
  };
  fm.onchange = fill; fs.onchange = fill; fill();
  return h("section", { class: "panel" }, h("h2", {}, `Sessions (${idx.length})`),
    h("div", { class: "form-row", style: "max-width:28rem;margin-bottom:.6rem" }, h("label", {}, "Method", fm), h("label", {}, "Scenario", fs)),
    h("div", { class: "scroll" }, h("div", { class: "table-wrap" }, h("table", {},
      h("thead", {}, h("tr", {}, th("#", "num"), th("Method"), th("Scenario"), th("Status"), th("Diagnosis"), th("Truth"), th("Correct"), th("Cost", "num"))), tbody))));
}

async function openSession(runId, index, drill) {
  drill.hidden = false;
  drill.replaceChildren("Loading session...");
  try {
    const r = await api(`/api/runs/session?run=${encodeURIComponent(runId)}&index=${index}`);
    const meta = ["method", "model", "model_version", "scenario", "set", "seed", "status", "correct", "cost", "source", "firmware", "calibration", "commit", "timestamp"]
      .filter((k) => r[k] != null).map((k) => h("tr", {}, h("th", {}, k), h("td", {}, k === "timestamp" ? when(r[k]) : String(r[k]))));
    drill.replaceChildren(h("h2", {}, `Session ${index}: ${r.method} on ${r.scenario}`),
      h("div", { class: "grid cols-main" },
        h("div", {}, h("h3", {}, "Step log"), table([th("#", "num"), th("Tool"), th("Arguments"), th("Reading"), th("Cost", "num"), th("Spent", "num")],
          (r.history || []).map((st) => h("tr", { class: st.status !== "ok" ? "failed" : "" }, h("td", { class: "num" }, st.step), h("td", {}, h("code", {}, st.tool)),
            h("td", {}, h("code", {}, fmtArgs(st.args))), h("td", {}, st.status === "ok" ? fmtData(st.data) : `${st.status}: ${st.error || ""}`),
            h("td", { class: "num" }, st.cost), h("td", { class: "num" }, st.spent ?? ""))), { class: "steps" })),
        h("div", {}, h("h3", {}, "Record"), h("div", { class: "table-wrap" }, h("table", {}, h("tbody", {}, meta))),
          h("details", { style: "margin-top:.6rem" }, h("summary", {}, "Diagnosis and agent statistics"),
            h("pre", {}, JSON.stringify({ diagnosis: r.diagnosis, elements: r.elements, agent_stats: r.agent_stats, evidence: r.evidence_check }, null, 2))))));
    drill.scrollIntoView({ behavior: "smooth", block: "start" });
  } catch (e) { drill.replaceChildren(empty(`Could not load session ${index}: ${e.message}`)); }
}

// ------------------------------------------------------------------ models
async function pageModels(view) {
  view.append(h("div", { class: "page-head" }, h("div", {}, h("h1", {}, "Models ", srcBadge("sim")),
    h("p", { class: "lede" }, "Local models benchmarked on simulated sessions with scripts/bench_models.py. Latest run per model first, then the history."))));
  const box = h("div", { class: "stack" }, h("div", { class: "panel" }, "Loading..."));
  view.append(box);
  try {
    const d = await api("/api/models");
    if (!d.latest.length) return box.replaceChildren(empty("No benchmark yet. Run python scripts/bench_models.py --models qwen3:8b to make one."));
    box.replaceChildren(
      h("section", { class: "panel" }, h("h2", {}, "Latest benchmark per model"), table(
        [th("Model"), th("Version"), th("Sessions", "num"), th("Correct", "num"), th("Invalid calls", "num"), th("Plain-text parsed", "num"), th("No call", "num"),
         th("Timeouts", "num"), th("Fallbacks", "num"), th("Median latency", "num"), th("p95 latency", "num"), th("Peak memory", "num"), th("Run")],
        d.latest.map((m) => h("tr", {}, h("td", {}, h("b", {}, m.model), h("span", { class: "cell-sub" }, `${m.server}${m.think ? ", thinking on" : ""}`)),
          h("td", {}, h("code", {}, m.model_version || "n/a")), h("td", { class: "num" }, m.sessions), h("td", { class: "num" }, pct(m.accuracy)),
          h("td", { class: "num" }, pct(m.invalid_rate)), h("td", { class: "num" }, m.content_parsed), h("td", { class: "num" }, m.no_call),
          h("td", { class: "num" }, m.timeouts), h("td", { class: "num" }, m.fallbacks), h("td", { class: "num" }, `${num(m.median_latency_s, 2)} s`),
          h("td", { class: "num" }, `${num(m.p95_latency_s, 2)} s`), h("td", { class: "num" }, m.peak_memory_gb == null ? "n/a" : `${num(m.peak_memory_gb, 2)} GB`),
          h("td", {}, m.run, h("span", { class: "cell-sub" }, when(m.date))))))),
      h("section", { class: "panel" }, h("h2", {}, "History"),
        h("p", { class: "small muted" }, "Each row is one benchmark run of that model."),
        ...Object.entries(d.history).map(([model, rows]) => h("div", { style: "margin-top:.8rem" }, h("h3", {}, model), table(
          [th("Run"), th("Date (WAT)"), th("Sessions", "num"), th("Correct", "num"), th("Invalid calls", "num"), th("Median latency", "num"), th("p95 latency", "num")],
          rows.map((r) => h("tr", {}, h("td", {}, r.run), h("td", {}, when(r.date)), h("td", { class: "num" }, r.sessions), h("td", { class: "num" }, pct(r.accuracy)),
            h("td", { class: "num" }, pct(r.invalid_rate)), h("td", { class: "num" }, `${num(r.median_latency_s, 2)} s`), h("td", { class: "num" }, `${num(r.p95_latency_s, 2)} s`))))))));
  } catch (e) { box.replaceChildren(empty(`Could not load the benchmark: ${e.message}`)); }
}

// ------------------------------------------------------------------ calibration
function calTable(rec) {
  return table([th("Channel"), th("Gain", "num"), th("Offset", "num"), th("Residual RMS", "num"), th("Points (raw to meter)")],
    Object.entries(rec.channels).map(([ch, c]) => h("tr", {}, h("td", {}, h("b", {}, ch)), h("td", { class: "num" }, c.gain), h("td", { class: "num" }, c.offset),
      h("td", { class: "num" }, c.residual_rms), h("td", { class: "small" }, (c.points || []).map((p) => `${p.raw} to ${p.meter}`).join(", ")))));
}

async function pageCalibration(view) {
  view.append(h("div", { class: "page-head" }, h("div", {}, h("h1", {}, "Calibration"),
    h("p", { class: "lede" }, "Straight-line fits of each tap and current channel against the handheld multimeter (scripts/calibrate.py)."))));
  const box = h("div", { class: "stack" }, h("div", { class: "panel" }, "Loading..."));
  view.append(box);
  try {
    const d = await api("/api/calibration");
    const kids = [];
    if (d.warning) kids.push(h("p", { class: "warnbox" }, d.warning));
    if (d.latest) {
      const age = (Date.now() - new Date(d.latest.timestamp)) / 864e5;
      kids.push(h("section", { class: "panel" }, h("h2", {}, `Latest: ${d.latest.id} `, srcBadge("hardware")),
        h("p", { class: "small muted" }, `${when(d.latest.timestamp)}, ${age.toFixed(0)} days old (stale after ${d.stale_days}), firmware ${d.latest.firmware}, meter ${d.latest.meter}`),
        calTable(d.latest)));
      kids.push(h("section", { class: "panel" }, h("h2", {}, "All hardware records"), table([th("Record"), th("Date (WAT)"), th("Firmware"), th("Channels", "num"), th("Commit")],
        d.hardware.slice().reverse().map((r) => h("tr", {}, h("td", {}, r.id), h("td", {}, when(r.timestamp)), h("td", {}, r.firmware),
          h("td", { class: "num" }, Object.keys(r.channels).length), h("td", {}, h("code", {}, short(r.commit))))))));
    } else {
      kids.push(empty("No hardware calibration yet. It needs the wired bench, the lab supply and the multimeter: docs/OWNER_TODO.md step 17."));
    }
    if (d.mock.length) {
      kids.push(h("section", { class: "panel" }, h("h2", {}, "Mock calibrations ", srcBadge("sim")),
        h("p", { class: "small muted" }, "From the mock bench, to test the calibration pipeline. Never used for hardware runs."),
        d.mock.slice().reverse().map((r) => h("details", { style: "margin-top:.5rem" }, h("summary", {}, `${r.id}, ${when(r.timestamp)}`), calTable(r)))));
    }
    box.replaceChildren(...kids);
  } catch (e) { box.replaceChildren(empty(`Could not load calibration records: ${e.message}`)); }
}

// ------------------------------------------------------------------ tracker
const TRACK = {
  milestones: { title: "Plan", cols: ["day", "title", "detail", "status", "due"], statuses: ["todo", "doing", "done", "blocked"] },
  bom: { title: "Parts", cols: ["item", "qty", "status", "supplier", "price_ngn", "date", "notes"], statuses: ["needed", "ordered", "received", "tested"] },
  equipment: { title: "Lab equipment", cols: ["item", "purpose", "status", "date", "notes"], statuses: ["to request", "requested", "approved", "received", "returned"] },
  issues: { title: "Issues and risks", cols: ["kind", "title", "detail", "severity", "status", "date"], statuses: ["open", "closed"] },
};
const COL_NAMES = { day: "Day", title: "Title", detail: "Detail", status: "Status", due: "Due", item: "Item", qty: "Qty", supplier: "Supplier",
                    price_ngn: "Price (NGN)", date: "Date", notes: "Notes", purpose: "Purpose", kind: "Kind", severity: "Severity" };
const WIDE = new Set(["title", "detail", "item", "notes", "purpose"]);

function cellInput(table, col, value) {
  if (col === "status") return h("select", {}, TRACK[table].statuses.map((st) => h("option", { value: st, selected: st === value }, st)));
  if (col === "severity") return h("select", {}, ["low", "medium", "high"].map((st) => h("option", { value: st, selected: st === value }, st)));
  if (col === "kind") return h("select", {}, ["issue", "risk"].map((st) => h("option", { value: st, selected: st === value }, st)));
  return h("input", { type: col === "date" || col === "due" ? "date" : "text", value: value || "", inputmode: col === "qty" || col === "price_ngn" ? "numeric" : null });
}

async function pageTracker(view, tableName) {
  const t = TRACK[tableName] ? tableName : "milestones";
  view.append(h("div", { class: "page-head" }, h("div", {}, h("h1", {}, "Project tracker"),
    h("p", { class: "lede" }, "Plan, parts, lab equipment and risks. Changes save as you edit. The plan is a proposal built from docs/OWNER_TODO.md.")),
    h("a", { href: "/api/tracker/export.md", download: "tracker.md" }, h("button", { type: "button" }, "Export to Markdown"))),
    h("nav", { class: "tabs", "aria-label": "Tracker tables" }, Object.entries(TRACK).map(([k, v]) =>
      h("a", { href: `#/tracker/${k}`, "aria-current": k === t ? "page" : null }, v.title))));
  const box = h("div", { class: "panel" }, "Loading...");
  view.append(box);
  let rows;
  try { rows = await api(`/api/tracker/${t}`); } catch (e) { return box.replaceChildren(empty(`Could not load ${t}: ${e.message}`)); }
  const cols = TRACK[t].cols;
  const tally = h("p", { class: "tally" });
  const saved = h("span", { class: "muted" });
  const updateTally = () => tally.replaceChildren(...TRACK[t].statuses.map((st) => h("span", {}, h("b", {}, rows.filter((r) => r.status === st).length), ` ${st}`)), saved);
  const rowEl = (r) => h("tr", {}, cols.map((c) => {
    const inp = cellInput(t, c, r[c]);
    inp.setAttribute("aria-label", `${COL_NAMES[c]} of row ${r.id}`);
    inp.addEventListener("change", async () => {
      try {
        const upd = await api(`/api/tracker/${t}/${r.id}`, { method: "PUT", body: { [c]: inp.value } });
        Object.assign(r, upd); updateTally(); saved.textContent = `Saved ${time(new Date().toISOString())}`;
      } catch (e) { fail(e); inp.value = r[c] || ""; }
    });
    return h("td", { class: WIDE.has(c) ? "wide" : "mid" }, inp);
  }), h("td", {}, h("button", { class: "link", type: "button", onclick: async () => {
    if (!confirm(`Delete "${r.title || r.item}"?`)) return;
    try { await api(`/api/tracker/${t}/${r.id}`, { method: "DELETE" }); route(); } catch (e) { fail(e); }
  } }, "Delete")));
  const add = {};
  const addRow = h("tr", {}, cols.map((c) => {
    add[c] = cellInput(t, c, c === "status" ? TRACK[t].statuses[0] : "");
    add[c].setAttribute("aria-label", `New ${COL_NAMES[c]}`);
    return h("td", { class: WIDE.has(c) ? "wide" : "mid" }, add[c]);
  }), h("td", {}, h("button", { class: "primary", type: "button", onclick: async () => {
    const body = Object.fromEntries(cols.map((c) => [c, add[c].value]));
    if (!(body.title || body.item)) return toast("Give the new row a title or item name", "error");
    try { await api(`/api/tracker/${t}`, { method: "POST", body }); route(); } catch (e) { fail(e); }
  } }, "Add")));
  updateTally();
  box.replaceChildren(tally, table([...cols.map((c) => th(COL_NAMES[c])), th("")], [...rows.map(rowEl), addRow], { class: "tracker" }));
}

// ------------------------------------------------------------------ start
window.addEventListener("hashchange", route);
let resizeTimer;
window.addEventListener("resize", () => {
  clearTimeout(resizeTimer);
  resizeTimer = setTimeout(() => { if (S.page === "live" && $("#s-chart")) drawPosterior($("#s-chart"), S.live); }, 150);
});
(async () => {
  try { S.options = await api("/api/options"); } catch (e) { fail(e); }
  route();
  connectEvents();
  refreshStatus();
  setInterval(refreshStatus, 5000); // also catches a link lost while no event could be sent
})();
