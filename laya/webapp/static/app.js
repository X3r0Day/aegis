/* Laya Playground — frontend. Plain DOM, no framework. */
"use strict";

const $ = (sel) => document.querySelector(sel);

const ui = {
  status: $("#status"),
  modelSelect: $("#model-select"),
  scenarioSelect: $("#scenario-select"),
  scenarioReset: $("#scenario-reset"),
  scenarioDesc: $("#scenario-desc"),
  stateJson: $("#state-json"),
  stateValidity: $("#state-validity"),
  stateFormat: $("#state-format"),
  tabState: $("#tab-state"),
  tabQuestions: $("#tab-questions"),
  panelState: $("#panel-state"),
  panelQuestions: $("#panel-questions"),
  qCount: $("#q-count"),
  questionsBuilder: $("#questions-builder"),
  questionsJson: $("#questions-json"),
  questionsJsonToggle: $("#questions-json-toggle"),
  addQuestion: $("#add-question"),
  run: $("#run"),
  results: $("#results"),
  resultMeta: $("#result-meta"),
  copyJson: $("#copy-json"),
  rawToggle: $("#raw-toggle"),
  rawJson: $("#raw-json"),
  toast: $("#toast"),
};

const app = {
  presets: null,
  scenario: null,
  questions: {},
  jsonMode: false,
  expandedId: null,
  running: false,
  lastResult: null,
};

const QTYPES = ["choice", "score", "noul"];
const STORE_KEY = "laya-playground/v1";

/* ------------------------------------------------------------------ dom helpers */
function el(tag, className, text) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text !== undefined) node.textContent = text;
  return node;
}
const ord = (i) => String(i + 1).padStart(2, "0");
const pretty = (obj) => JSON.stringify(obj, null, 2);
const pct = (p) => (100 * p).toFixed(1) + "%";

let toastTimer = null;
function toast(msg, ms = 3000) {
  ui.toast.textContent = msg;
  ui.toast.classList.remove("hidden");
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => ui.toast.classList.add("hidden"), ms);
}

/* ------------------------------------------------------------------- tabs */
function setTab(name) {
  const isState = name === "state";
  ui.tabState.classList.toggle("active", isState);
  ui.tabQuestions.classList.toggle("active", !isState);
  ui.tabState.setAttribute("aria-selected", String(isState));
  ui.tabQuestions.setAttribute("aria-selected", String(!isState));
  ui.panelState.classList.toggle("active", isState);
  ui.panelQuestions.classList.toggle("active", !isState);
  ui.stateFormat.classList.toggle("hidden", !isState);
  ui.addQuestion.classList.toggle("hidden", isState || app.jsonMode);
  ui.questionsJsonToggle.classList.toggle("hidden", isState);
  save();
}

/* ------------------------------------------------------------------ status */
async function refreshStatus() {
  try {
    const res = await fetch("/api/status");
    if (!res.ok) throw new Error(res.statusText);
    renderStatus(await res.json());
  } catch {
    const item = el("span", "s-item off", "server unreachable");
    ui.status.replaceChildren(item);
  }
}

function renderStatus(s) {
  const frag = document.createDocumentFragment();
  for (const [name, info] of Object.entries(s.checkpoints)) {
    const state = !info.present ? "off" : info.loaded ? "on" : "busy";
    const note = !info.present ? "not downloaded" : info.loaded ? "loaded" : "loading";
    const item = el("span", "s-item " + state);
    item.title = `${name} — ${note}`;
    item.append(el("span", "sq"), document.createTextNode(name));
    frag.append(item);
  }
  const device = el("span", "s-item off");
  device.title = "inference device";
  device.append(document.createTextNode(s.device));
  frag.append(device);
  ui.status.replaceChildren(frag);

  for (const opt of ui.modelSelect.options) {
    const id = opt.value;
    const present = id === "auto"
      ? s.checkpoints.english.present
      : s.checkpoints[id] && s.checkpoints[id].present;
    opt.disabled = !present;
    const label = opt.dataset.label || opt.textContent;
    opt.dataset.label = label;
    opt.textContent = present ? label : `${label} — not downloaded`;
  }
  if (ui.modelSelect.selectedOptions[0]?.disabled) ui.modelSelect.value = "english";
}

/* ----------------------------------------------------------------- presets */
async function loadPresets() {
  const res = await fetch("/api/presets");
  if (!res.ok) throw new Error("Could not load presets: " + res.statusText);
  app.presets = await res.json();

  ui.modelSelect.replaceChildren(
    ...app.presets.models.map((m) => {
      const opt = el("option", null, m.name);
      opt.value = m.id;
      return opt;
    })
  );
  ui.scenarioSelect.replaceChildren(
    ...app.presets.scenarios.map((sc) => {
      const opt = el("option", null, sc.name);
      opt.value = sc.id;
      return opt;
    })
  );
}

function loadScenario(id, { keepEdits = false } = {}) {
  const sc = app.presets.scenarios.find((s) => s.id === id);
  if (!sc) return;
  app.scenario = sc.id;
  ui.scenarioSelect.value = sc.id;
  ui.scenarioDesc.textContent = sc.description || "";
  if (!keepEdits) {
    ui.stateJson.value = pretty(sc.state);
    checkStateValidity();
    app.expandedId = null;
    setQuestions(JSON.parse(JSON.stringify(sc.questions)));
    if (app.jsonMode) ui.questionsJson.value = pretty(app.questions);
  }
  save();
}

function applyCurrentScenario() {
  if (!app.scenario) return;
  loadScenario(app.scenario);
  toast("preset re-applied");
}

/* --------------------------------------------------------------- state check */
let validityTimer = null;
function checkStateValidity() {
  clearTimeout(validityTimer);
  validityTimer = setTimeout(() => {
    const raw = ui.stateJson.value.trim();
    if (!raw) { ui.stateValidity.classList.remove("show"); return; }
    let label = "text";
    let cls = "";
    try {
      JSON.parse(raw);
      label = "json";
      cls = "ok";
    } catch { /* will be sent as plain text */ }
    ui.stateValidity.textContent = label;
    ui.stateValidity.className = `validity show ${cls}`;
  }, 250);
}

/* --------------------------------------------------------------- question list */
function setQuestions(obj) {
  app.questions = obj;
  renderBuilder();
}

function renderBuilder() {
  const entries = Object.entries(app.questions || {});
  ui.qCount.textContent = String(entries.length);
  ui.questionsBuilder.replaceChildren();
  if (!entries.length) {
    ui.questionsBuilder.append(el("p", "crit-note", "No questions yet — use “+ add”."));
    return;
  }
  entries.forEach(([qid, q], i) => {
    ui.questionsBuilder.append(renderQuestion(qid, q, i));
  });
}

function renderQuestion(qid, q, i) {
  const open = app.expandedId === qid;
  const item = el("div", "q-item" + (open ? " open" : ""));
  item.dataset.qid = qid;

  const row = el("div", "q-row");
  row.append(
    el("span", "idx", ord(i)),
    el("span", "qid", qid),
    el("span", "tag " + q.type, q.type),
    el("span", "summary", q.instructions || "no instructions"),
    el("span", "sign", open ? "−" : "+"),
  );
  row.onclick = () => {
    app.expandedId = app.expandedId === qid ? null : qid;
    renderBuilder();
  };
  item.append(row);

  const body = el("div", "q-body");

  const idRow = el("div", "field-row");
  const idInput = el("input", "qid");
  idInput.value = qid;
  idInput.title = "Question id — becomes the key in the response";
  const typeSel = el("select");
  for (const t of QTYPES) {
    const opt = el("option", null, t);
    opt.value = t;
    typeSel.append(opt);
  }
  typeSel.value = q.type;
  const spacer = el("span", "spacer");
  const remove = el("button", "x", "remove");
  remove.title = "Delete this question";
  idRow.append(idInput, typeSel, spacer, remove);
  body.append(idRow);

  const insField = el("div", "field");
  insField.append(el("label", null, "instructions"));
  const insTa = el("textarea");
  insTa.value = q.instructions || "";
  insTa.placeholder = "What should the model decide?";
  insTa.rows = 2;
  insTa.oninput = () => {
    q.instructions = insTa.value;
    row.querySelector(".summary").textContent = insTa.value || "no instructions";
  };
  insField.append(insTa);
  body.append(insField);

  const crit = el("div", "field");
  const drawCriteria = () => {
    crit.replaceChildren();
    crit.append(el("label", null, "criteria"));
    const type = typeSel.value;

    if (type === "choice") {
      q.criteria = q.criteria && !Array.isArray(q.criteria) ? q.criteria : { option_1: "", option_2: "" };
      const rows = el("div", "crit-rows");
      for (const [key] of Object.entries(q.criteria)) rows.append(choiceRow(key));
      crit.append(rows);
      crit.append(el("div", "crit-note", "each option is scored and softmaxed — the keys become the answer labels"));
      crit.append(addButton("+ option", () => {
        let n = Object.keys(q.criteria).length + 1;
        while (q.criteria["option_" + n]) n++;
        q.criteria["option_" + n] = "";
        drawCriteria();
      }));
    } else if (type === "score") {
      q.criteria = Array.isArray(q.criteria) ? q.criteria : ["level 0", "level 1", "level 2"];
      const rows = el("div", "crit-rows");
      q.criteria.forEach((desc, n) => {
        const line = el("div", "crit-row");
        line.append(el("span", "num", String(n)));
        const v = el("input", "v");
        v.value = desc;
        v.placeholder = "describe this level";
        v.oninput = () => { q.criteria[n] = v.value; };
        const del = el("button", "x", "✕");
        del.title = "Remove level";
        del.onclick = () => {
          if (q.criteria.length <= 2) { toast("score questions need at least two levels"); return; }
          q.criteria.splice(n, 1);
          drawCriteria();
        };
        line.append(v, del);
        rows.append(line);
      });
      crit.append(rows);
      crit.append(el("div", "crit-note", "ordered levels 0…n — the answer is an expected value plus the full distribution"));
      crit.append(addButton("+ level", () => { q.criteria.push(""); drawCriteria(); }));
    } else {
      const note = el("div", "crit-note");
      note.append("boolean question — answers with ");
      note.append(el("b", null, "P(true)"));
      note.append(". custom yes/no wording lives in the json editor");
      crit.append(note);
    }
  };

  const choiceRow = (key) => {
    const line = el("div", "crit-row");
    const k = el("input", "k");
    k.value = key;
    k.placeholder = "key";
    let current = key;
    k.oninput = () => {
      const next = k.value;
      if (!next || next === current) return;
      if (Object.prototype.hasOwnProperty.call(q.criteria, next)) {
        toast("duplicate option key: " + next);
        return;
      }
      const rebuilt = {};
      for (const [kk, vv] of Object.entries(q.criteria)) rebuilt[kk === current ? next : kk] = vv;
      q.criteria = rebuilt;
      current = next;
    };
    const v = el("input", "v");
    v.value = q.criteria[key] || "";
    v.placeholder = "description (optional)";
    v.oninput = () => { q.criteria[current] = v.value; };
    const del = el("button", "x", "✕");
    del.title = "Remove option";
    del.onclick = () => {
      if (Object.keys(q.criteria).length <= 2) { toast("choice questions need at least two options"); return; }
      delete q.criteria[current];
      drawCriteria();
    };
    line.append(k, v, del);
    return line;
  };

  const addButton = (label, onclick) => {
    const b = el("button", "add-row", label);
    b.onclick = onclick;
    return b;
  };

  typeSel.onchange = () => {
    const t = typeSel.value;
    if (t === "choice") q.criteria = { option_1: "", option_2: "" };
    else if (t === "score") q.criteria = ["level 0", "level 1", "level 2"];
    else delete q.criteria;
    const tag = row.querySelector(".tag");
    tag.className = "tag " + t;
    tag.textContent = t;
    drawCriteria();
  };
  remove.onclick = () => {
    delete app.questions[item.dataset.qid];
    if (app.expandedId === item.dataset.qid) app.expandedId = null;
    renderBuilder();
  };
  idInput.onchange = () => {
    const oldId = item.dataset.qid;
    const newId = idInput.value.trim();
    if (!newId) { idInput.value = oldId; return; }
    if (newId === oldId) return;
    if (app.questions[newId]) { toast("question id already exists: " + newId); idInput.value = oldId; return; }
    const rebuilt = {};
    for (const [k, v] of Object.entries(app.questions)) rebuilt[k === oldId ? newId : k] = v;
    app.questions = rebuilt;
    item.dataset.qid = newId;
    if (app.expandedId === oldId) app.expandedId = newId;
  };

  body.append(crit);
  drawCriteria();
  item.append(body);
  return item;
}

function collectQuestions() {
  const out = {};
  for (const item of ui.questionsBuilder.querySelectorAll(".q-item")) {
    const qid = item.dataset.qid;
    const type = item.querySelector("select").value;
    const instructions = item.querySelector("textarea").value;
    out[qid] = { type, instructions };
    if (type !== "noul") out[qid].criteria = app.questions[qid].criteria;
  }
  return out;
}

function addQuestion() {
  let n = 1;
  while (app.questions["question_" + n]) n++;
  const qid = "question_" + n;
  app.questions[qid] = { type: "noul", instructions: "" };
  app.expandedId = qid;
  renderBuilder();
  const input = ui.questionsBuilder.querySelector(`.q-item[data-qid="${CSS.escape(qid)}"] input.qid`);
  if (input) input.focus();
}

/* --------------------------------------------------------------- json mode */
function toggleJsonMode() {
  if (!app.jsonMode) {
    ui.questionsJson.value = pretty(collectQuestions());
    ui.questionsJson.classList.remove("hidden");
    ui.questionsBuilder.classList.add("hidden");
    ui.questionsJsonToggle.textContent = "visual";
    ui.addQuestion.classList.add("hidden");
    app.jsonMode = true;
  } else {
    let parsed;
    try {
      parsed = JSON.parse(ui.questionsJson.value);
    } catch (e) {
      toast("Invalid questions JSON: " + e.message);
      return;
    }
    app.jsonMode = false;
    app.expandedId = null;
    setQuestions(parsed);
    ui.questionsJson.classList.add("hidden");
    ui.questionsBuilder.classList.remove("hidden");
    ui.questionsJsonToggle.textContent = "json";
    ui.addQuestion.classList.remove("hidden");
  }
  save();
}

/* ---------------------------------------------------------------------- run */
function parseState() {
  const raw = ui.stateJson.value.trim();
  if (!raw) throw new Error("the state is empty");
  try {
    return JSON.parse(raw);
  } catch {
    return raw;
  }
}

function showSkeleton() {
  ui.rawToggle.checked = false;
  ui.rawJson.classList.add("hidden");
  ui.results.classList.remove("hidden");
  const sk = el("div", "skel");
  sk.append(
    el("div", "loading", "running inference"),
    el("div", "sk w30"), el("div", "sk w85"), el("div", "sk w60"),
  );
  ui.results.replaceChildren(sk);
  ui.resultMeta.replaceChildren("working…");
}

async function run() {
  if (app.running) return;
  let stateVal, questions;
  try {
    stateVal = parseState();
    questions = app.jsonMode ? JSON.parse(ui.questionsJson.value) : collectQuestions();
    if (!questions || !Object.keys(questions).length) throw new Error("add at least one question");
    for (const [qid, q] of Object.entries(questions)) {
      if (!q.instructions || !q.instructions.trim()) throw new Error(`question "${qid}" has no instructions`);
      if (q.type === "choice" && (!q.criteria || Object.keys(q.criteria).length < 2)) throw new Error(`choice question "${qid}" needs at least 2 options`);
      if (q.type === "score" && (!Array.isArray(q.criteria) || q.criteria.length < 2)) throw new Error(`score question "${qid}" needs at least 2 levels`);
    }
  } catch (e) {
    toast(e.message);
    return;
  }

  app.running = true;
  ui.run.disabled = true;
  ui.run.querySelector(".run-label").textContent = "running";
  showSkeleton();
  save();

  try {
    const res = await fetch("/api/predict", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ state: stateVal, questions, model: ui.modelSelect.value }),
    });
    const data = await res.json();
    if (!res.ok) {
      const detail = typeof data.detail === "string" ? data.detail : pretty(data.detail);
      throw new Error(detail || res.statusText);
    }
    app.lastResult = data;
    renderResults(data);
    refreshStatus();
  } catch (e) {
    ui.results.replaceChildren(el("div", "error-note", "run failed — " + e.message));
    ui.resultMeta.replaceChildren();
  } finally {
    app.running = false;
    ui.run.disabled = false;
    ui.run.querySelector(".run-label").textContent = "run";
  }
}

/* -------------------------------------------------------------------- results */
function renderResults(data) {
  const routing = data.routing || {};
  const meta = ui.resultMeta;
  meta.replaceChildren();
  meta.append(el("b", null, routing.model || "?"), el("span", "sep", "·"));
  if (routing.reason) {
    const reason = el("span", null, routing.reason);
    reason.title = routing.reason;
    meta.append(reason, el("span", "sep", "·"));
  }
  meta.append(el("span", null, (data.latency_ms / 1000).toFixed(2) + " s"));
  if (data.usage) meta.append(el("span", "sep", "·"), el("span", null, data.usage.input_tokens + " tokens"));

  const frag = document.createDocumentFragment();
  Object.entries(data.answers).forEach(([qid, ans], i) => frag.append(renderFinding(qid, ans, i)));
  ui.results.replaceChildren(frag);
  requestAnimationFrame(() => {
    ui.results.querySelectorAll(".bar > i").forEach((f) => { f.style.width = f.dataset.w; });
  });

  ui.rawJson.textContent = pretty(data);
  if (!ui.rawToggle.checked) ui.rawJson.classList.add("hidden");
}

function renderFinding(qid, ans, i) {
  const card = el("div", "finding");

  const head = el("div", "finding-head");
  head.append(
    el("span", "idx", ord(i)),
    el("span", "qid", qid),
    el("span", "tag " + ans.type, ans.type),
  );
  const right = el("div", "head-right");
  const conf = el("span", null, "conf " + ans.confidence.toFixed(2));
  conf.title = "normalised entropy confidence of the answer distribution";
  right.append(conf);
  if (ans.action) {
    const esc = el("span", null, "escalate " + ans.action.act_probability.toFixed(2));
    esc.title = "act/escalate head probability";
    right.append(esc);
  }
  head.append(right);
  card.append(head);

  if (ans.type === "choice") {
    const probs = Object.entries(ans.probabilities).sort((a, b) => b[1] - a[1]);
    const verdict = el("div", "verdict");
    verdict.append(el("span", "answer small", probs[0][0]), el("span", "pct", pct(probs[0][1])));
    card.append(verdict);
    for (const [name, p] of probs) card.append(barRow(name, p, name === ans.choice));
  } else if (ans.type === "score") {
    const levels = Object.entries(ans.probabilities).map(([k, p]) => [Number(k), p]).sort((a, b) => b[0] - a[0]);
    const max = Object.keys(ans.probabilities).length - 1;
    const verdict = el("div", "verdict");
    verdict.append(el("span", "answer", ans.score.toFixed(2)), el("span", "qual", "of " + max + " expected"));
    card.append(verdict);
    for (const [level, p] of levels) {
      const legend = ans.legend[String(level)] || "level " + level;
      card.append(barRow(level + " · " + legend, p, false, legend));
    }
  } else {
    const verdict = el("div", "verdict");
    verdict.append(el("span", "answer", "P(true) = " + ans.noul.toFixed(3)));
    card.append(verdict);
    card.append(barRow("true", ans.noul, ans.noul >= 0.5));
    card.append(barRow("false", 1 - ans.noul, ans.noul < 0.5));
  }
  return card;
}

function barRow(label, p, isWinner, title) {
  const row = el("div", "bar-row" + (isWinner ? " win" : ""));
  const lbl = el("span", "lbl", label);
  lbl.title = title || label;
  const bar = el("div", "bar");
  const fill = el("i");
  fill.dataset.w = Math.max(0.5, Math.min(100, p * 100)) + "%";
  bar.append(fill);
  row.append(lbl, bar, el("span", "pct", pct(p)));
  return row;
}

/* ---------------------------------------------------------------- persistence */
let saveTimer = null;
function save() {
  clearTimeout(saveTimer);
  saveTimer = setTimeout(() => {
    try {
      localStorage.setItem(STORE_KEY, JSON.stringify({
        scenario: app.scenario,
        state: ui.stateJson.value,
        questions: app.jsonMode ? null : collectQuestions(),
        questionsJson: app.jsonMode ? ui.questionsJson.value : null,
        jsonMode: app.jsonMode,
        model: ui.modelSelect.value,
        tab: ui.tabQuestions.classList.contains("active") ? "questions" : "state",
      }));
    } catch { /* storage unavailable — ignore */ }
  }, 350);
}

function restore() {
  let saved;
  try {
    saved = JSON.parse(localStorage.getItem(STORE_KEY) || "null");
  } catch {
    return false;
  }
  if (!saved || typeof saved !== "object") return false;

  if (saved.model && [...ui.modelSelect.options].some((o) => o.value === saved.model)) {
    ui.modelSelect.value = saved.model;
  }
  if (saved.scenario) loadScenario(saved.scenario, { keepEdits: true });
  if (typeof saved.state === "string") {
    ui.stateJson.value = saved.state;
    checkStateValidity();
  }
  if (saved.jsonMode && typeof saved.questionsJson === "string") {
    try {
      setQuestions(JSON.parse(saved.questionsJson));
      app.jsonMode = true;
      ui.questionsJson.value = saved.questionsJson;
      ui.questionsJson.classList.remove("hidden");
      ui.questionsBuilder.classList.add("hidden");
      ui.questionsJsonToggle.textContent = "visual";
      ui.addQuestion.classList.add("hidden");
    } catch { /* corrupt saved JSON — keep the preset questions */ }
  } else if (saved.questions && Object.keys(saved.questions).length) {
    setQuestions(saved.questions);
  }
  setTab(saved.tab === "questions" ? "questions" : "state");
  return true;
}

/* -------------------------------------------------------------------- init */
ui.run.onclick = run;
ui.addQuestion.onclick = addQuestion;
ui.questionsJsonToggle.onclick = toggleJsonMode;
ui.scenarioSelect.onchange = () => loadScenario(ui.scenarioSelect.value);
ui.scenarioReset.onclick = applyCurrentScenario;
ui.tabState.onclick = () => setTab("state");
ui.tabQuestions.onclick = () => setTab("questions");
ui.stateJson.addEventListener("input", checkStateValidity);
ui.stateFormat.onclick = () => {
  try {
    ui.stateJson.value = pretty(JSON.parse(ui.stateJson.value));
    checkStateValidity();
  } catch {
    toast("state is not valid JSON — it will be sent as plain text");
  }
};
ui.rawToggle.onchange = () => {
  const raw = ui.rawToggle.checked;
  ui.rawJson.classList.toggle("hidden", !raw);
  ui.results.classList.toggle("hidden", raw);
};
ui.copyJson.onclick = async () => {
  if (!app.lastResult) { toast("nothing to copy yet"); return; }
  try {
    await navigator.clipboard.writeText(pretty(app.lastResult));
    toast("response copied as JSON");
  } catch {
    toast("clipboard not available in this browser context");
  }
};

document.addEventListener("input", () => save());
document.addEventListener("keydown", (e) => {
  if ((e.metaKey || e.ctrlKey) && e.key === "Enter") {
    e.preventDefault();
    run();
  } else if (e.key === "Escape" && app.expandedId !== null) {
    app.expandedId = null;
    renderBuilder();
  }
});

(async function init() {
  setTab("state");
  try {
    await loadPresets();
    if (!restore()) loadScenario(app.presets.scenarios[0].id);
  } catch (e) {
    ui.results.replaceChildren(el("div", "error-note", e.message));
  }
  refreshStatus();
  setInterval(refreshStatus, 3000);
})();
