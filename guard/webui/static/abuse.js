/* API-abuse scenario bench — real HTTP requests or synthetic payloads, scored by Laya. */
"use strict";

const $ = (sel) => document.querySelector(sel);

const ui = {
  status: $("#status"),
  scenarioList: $("#scenario-list"),
  run: $("#run"),
  result: $("#result"),
  payload: $("#payload"),
  payloadStats: $("#payload-stats"),
  payloadHint: $("#payload-hint"),
  payloadReset: $("#payload-reset"),
  sourceLive: $("#source-live"),
  sourceSynthetic: $("#source-synthetic"),
  modeBlock: $("#mode-block"),
  modeMonitor: $("#mode-monitor"),
  toast: $("#toast"),
};

const state = {
  profiles: [],
  selected: null,
  source: "live", // live | synthetic
  mode: "block",
  running: false,
  preview: null,
};

/* ------------------------------------------------------------------ utils */
function el(tag, className, text) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text !== undefined) node.textContent = text;
  return node;
}

function pct(p) {
  return (100 * p).toFixed(1) + "%";
}

let toastTimer = null;
function toast(msg, ms = 3400) {
  ui.toast.textContent = msg;
  ui.toast.classList.remove("hidden");
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => ui.toast.classList.add("hidden"), ms);
}

/* -------------------------------------------------------------- scenarios */
async function loadProfiles() {
  const res = await fetch("/api-abuse/profiles");
  if (!res.ok) throw new Error("could not load profiles: " + res.statusText);
  const data = await res.json();
  state.profiles = data.profiles || [];
  if (state.profiles.length) select(state.profiles[0].id);
}

function select(id) {
  state.selected = id;
  ui.run.disabled = !id || state.running;
  renderProfiles();
  loadPreview(id);
}

function renderProfiles() {
  ui.scenarioList.replaceChildren();
  for (const profile of state.profiles) {
    const card = el("div", "scenario-card" + (profile.id === state.selected ? " active" : ""));
    const row = el("div", "sc-row");
    row.append(el("span", "sc-name", profile.name), el("span", "expect-tag " + profile.expect, "expect " + profile.expect));
    card.append(row, el("p", "sc-desc", profile.description));
    card.onclick = () => select(profile.id);
    ui.scenarioList.append(card);
  }
}

/* ---------------------------------------------------------------- preview */
let previewSeq = 0;
async function loadPreview(profileId) {
  const seq = ++previewSeq;
  state.preview = null;
  ui.payload.value = "";
  ui.payloadStats.textContent = "loading scenario…";
  ui.payloadHint.textContent = "";
  try {
    const res = await fetch("/api-abuse/preview", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ profile: profileId }),
    });
    const data = await res.json();
    if (!res.ok) throw new Error((data.error && data.error.message) || res.statusText);
    if (seq !== previewSeq) return; // selection changed while loading
    state.preview = data;
    fillEditor();
  } catch (err) {
    if (seq !== previewSeq) return;
    ui.payloadStats.textContent = "could not load scenario: " + err.message;
  }
}

function targetHost(plan) {
  try {
    return new URL(plan.target).host;
  } catch {
    return plan.target || "?";
  }
}

function fillEditor() {
  const data = state.preview;
  if (!data) return;
  if (state.source === "live") {
    ui.payload.value = JSON.stringify(data.plan, null, 2);
    const plan = data.plan || {};
    ui.payloadStats.textContent = `${targetHost(plan)} · ${plan.count} requests at ${plan.rate_per_s}/s · client ${plan.client_ip}`;
    ui.payloadHint.textContent = "run scenario sends these HTTP requests for real; the telemetry is captured and scored by Laya. Edit the plan (target, paths, count, rate, user agent…) and run again.";
  } else {
    const events = data.events || [];
    ui.payload.value = "[\n" + events.map((event) => JSON.stringify(event)).join(",\n") + "\n]";
    const stats = data.stats || {};
    ui.payloadStats.textContent = `${stats.count} events · ${stats.span_s}s window · top ${stats.top_endpoint} (${Math.round((stats.top_endpoint_share || 0) * 100)}%)`;
    ui.payloadHint.textContent = "POST /api-abuse/simulate — these telemetry events are evaluated verbatim (same schema as /v1/abuse/events). Edit anything, then run.";
  }
}

function renderSource() {
  ui.sourceLive.classList.toggle("active", state.source === "live");
  ui.sourceSynthetic.classList.toggle("active", state.source === "synthetic");
}

function renderMode() {
  ui.modeBlock.classList.toggle("active", state.mode === "block");
  ui.modeMonitor.classList.toggle("active", state.mode === "monitor");
}

/* ------------------------------------------------------------------- run */
function showRunning(profile, message) {
  const wrap = el("div", null);
  wrap.append(
    el("p", "running", "running scenario — " + profile.name),
    el("p", "bench-meta", message),
  );
  ui.result.replaceChildren(wrap);
}

async function run() {
  if (state.running) return;
  const profile = state.profiles.find((p) => p.id === state.selected);
  if (!profile) return;

  const raw = ui.payload.value.trim();
  if (!raw) {
    toast("the payload is empty — press reset to regenerate the scenario");
    return;
  }

  let body;
  let runningMessage;
  try {
    if (state.source === "live") {
      const plan = JSON.parse(raw);
      if (!plan || typeof plan !== "object" || Array.isArray(plan)) throw new Error("the plan must be a JSON object");
      body = { profile: profile.id, plan, mode: state.mode };
      runningMessage = `sending ${plan.count || "?"} real HTTP requests at ${plan.rate_per_s || "?"}/s, then asking Laya…`;
    } else {
      const events = JSON.parse(raw);
      if (!Array.isArray(events) || !events.length) throw new Error("payload must be a non-empty JSON array of events");
      if (!events.every((event) => event && typeof event === "object" && !Array.isArray(event))) {
        throw new Error("every event must be a JSON object");
      }
      body = { profile: profile.id, events, mode: state.mode };
      runningMessage = `${events.length} events · asking Laya… (one CPU forward pass, ~10 s)`;
    }
  } catch (err) {
    toast("payload error — " + err.message);
    return;
  }

  state.running = true;
  ui.run.disabled = true;
  showRunning(profile, runningMessage);

  try {
    const endpoint = state.source === "live" ? "/api-abuse/live" : "/api-abuse/simulate";
    const res = await fetch(endpoint, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
    const data = await res.json();
    if (!res.ok) {
      const message = (data.error && data.error.message) || res.statusText;
      throw new Error(message);
    }
    renderVerdict(profile, data, state.source);
  } catch (err) {
    const wrap = el("div", null);
    wrap.append(el("p", "bench-title", "Run failed"), el("p", "bench-meta", err.message));
    ui.result.replaceChildren(wrap);
    toast(err.message);
  } finally {
    state.running = false;
    ui.run.disabled = false;
  }
}

/* --------------------------------------------------------------- verdict */
function metric(key, value, opts = {}) {
  const box = el("div", "metric");
  box.append(el("div", "k", key));
  box.append(el("div", "v" + (opts.small ? " small" : "") + (opts.win ? " win" : ""), value));
  return box;
}

function barRow(label, probability, isWinner) {
  const row = el("div", "bar-row" + (isWinner ? " win" : ""));
  row.append(el("span", "lbl", label));
  const bar = el("div", "bar");
  const fill = el("i");
  fill.dataset.w = Math.max(0.5, Math.min(100, probability * 100)) + "%";
  bar.append(fill);
  row.append(bar, el("span", "pct", pct(probability)));
  return row;
}

function execSummary(data) {
  const wrap = el("div", "exec");
  const chips = el("div", "triggers");
  chips.append(el("span", "trigger-chip", `${data.events} requests in ${data.duration_s}s`));
  for (const [status, count] of Object.entries(data.statuses || {})) {
    chips.append(el("span", "trigger-chip", `${status} ×${count}`));
  }
  if (data.errors) chips.append(el("span", "trigger-chip", `network errors ×${data.errors}`));
  wrap.append(chips);
  return wrap;
}

function renderVerdict(profile, data, source) {
  const verdict = data.verdict;
  if (!verdict) {
    ui.result.replaceChildren(el("p", "bench-title", "No verdict returned."));
    return;
  }

  const frag = document.createDocumentFragment();

  const head = el("div", "bench-head");
  head.append(el("h2", "bench-title", profile.name));
  frag.append(head);
  const meta = el("p", "bench-meta");
  meta.append(
    el("b", null, verdict.client),
    el("span", "sep", "·"),
    el("span", null, source === "live" ? "live HTTP requests" : "synthetic telemetry"),
    el("span", "sep", "·"),
    el("span", null, verdict.model),
    el("span", "sep", "·"),
    el("span", null, "Laya " + verdict.latency_ms.toFixed(0) + " ms"),
  );
  frag.append(meta);

  const banner = el("div", "verdict-banner " + verdict.label);
  const labelText = verdict.label === "would-block" ? "would block" : verdict.label;
  banner.append(el("span", "label", labelText));
  banner.append(el("span", "sub",
    verdict.label === "allow"
      ? "Laya judged this traffic legitimate — forward normally."
      : verdict.label === "block"
        ? "Laya judged this client abusive — reject or throttle its requests."
        : verdict.label === "would-block"
          ? "Laya judged this client abusive; monitor mode only flags it."
          : "The check failed; no enforcement was applied."));
  frag.append(banner);

  if (source === "live") frag.append(execSummary(data));

  const grid = el("div", "metric-grid");
  grid.append(
    metric("category", verdict.category, { small: true, win: verdict.category !== "normal_client" && verdict.category !== "integration_burst" }),
    metric("pattern", (verdict.pattern || "").split(" (")[0], { small: true }),
    metric("severity", verdict.severity.toFixed(2) + " / 3"),
    metric("abuse score", verdict.abuse_score.toFixed(3), { win: verdict.abuse_score >= verdict.threshold }),
    metric("recommended", verdict.recommended_action),
  );
  frag.append(grid);

  frag.append(el("div", "section-label", "Laya's probabilities"));
  const probs = verdict.probabilities || {};
  frag.append(barRow("is_abuse (question)", probs.is_abuse ?? 0, false));
  frag.append(barRow("true_positive (question)", probs.true_positive ?? 0, false));
  frag.append(barRow("abuse score (mean · decision)", probs.abuse_score ?? 0, verdict.abuse_score >= verdict.threshold));

  if (verdict.triggered_by && verdict.triggered_by.length) {
    frag.append(el("div", "section-label", "deterministic triggers"));
    const triggers = el("div", "triggers");
    for (const trigger of verdict.triggered_by) triggers.append(el("span", "trigger-chip", trigger));
    frag.append(triggers);
  }

  frag.append(el("div", "section-label", "what Laya saw"));
  frag.append(el("pre", "narrative", verdict.narrative || ""));

  if (source === "live" && data.captured && data.captured.length) {
    const details = el("details", "raw");
    details.append(el("summary", null, `captured telemetry — real requests/responses (${data.captured.length} of ${data.events} shown, start + tail)`));
    details.append(el("pre", null, data.captured.map((event) => JSON.stringify(event)).join("\n")));
    frag.append(details);
  }

  if (verdict.error) {
    frag.append(el("div", "section-label", "error"));
    frag.append(el("pre", "narrative", verdict.error));
  }

  const raw = el("details", "raw");
  raw.append(el("summary", null, "raw verdict JSON"));
  raw.append(el("pre", null, JSON.stringify(data.verdict, null, 2)));
  frag.append(raw);

  ui.result.replaceChildren(frag);
  requestAnimationFrame(() => {
    ui.result.querySelectorAll(".bar > i").forEach((fill) => { fill.style.width = fill.dataset.w; });
  });
}

/* ---------------------------------------------------------------- health */
async function refreshHealth() {
  try {
    const res = await fetch("/health");
    const h = await res.json();
    ui.status.replaceChildren(
      el("b", null, h.abuse.model),
      document.createTextNode(" · threshold " + h.abuse.threshold),
    );
  } catch {
    ui.status.textContent = "server unreachable";
  }
}

/* ------------------------------------------------------------------ init */
ui.run.onclick = run;
ui.payloadReset.onclick = () => { if (state.selected) loadPreview(state.selected); };
ui.sourceLive.onclick = () => { state.source = "live"; renderSource(); fillEditor(); };
ui.sourceSynthetic.onclick = () => { state.source = "synthetic"; renderSource(); fillEditor(); };
ui.modeBlock.onclick = () => { state.mode = "block"; renderMode(); };
ui.modeMonitor.onclick = () => { state.mode = "monitor"; renderMode(); };

(async function init() {
  renderMode();
  renderSource();
  try {
    await loadProfiles();
  } catch (err) {
    ui.result.replaceChildren(el("p", "bench-title", err.message));
  }
  refreshHealth();
})();
