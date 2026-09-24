/* Aegis console: shell, navigation and the decisions/clients/settings views. */
"use strict";

const VIEW_TITLES = { overview: "Overview", decisions: "Decisions", clients: "Clients", devices: "Devices", agent: "Agent", settings: "Settings" };

function showView(name) {
  state.view = name;
  $("#crumb").textContent = VIEW_TITLES[name] || name;
  document.title = "Aegis · " + (VIEW_TITLES[name] || name);
  teardownOverview();
  for (const section of document.querySelectorAll(".view")) section.classList.add("hidden");
  $("#view-" + name).classList.remove("hidden");
  closePalette();
  closeMenu();
  clearInterval(state.timer);
  const load = { overview: loadOverview, decisions: loadDecisions, clients: loadClients, devices: loadDevices, agent: loadAgent, settings: loadSettings }[name];
  load(true);
  const every = { overview: 5000, decisions: 10000, clients: 5000, devices: 5000, agent: 5000, settings: 30000 }[name];
  state.timer = setInterval(() => { if (!document.hidden) load(false); }, every);
}

/* ---------------------------------------------------------------- decisions */
async function loadDecisions(animate = false) {
  const query = new URLSearchParams({
    kind: state.decisions.kind,
    limit: state.decisions.limit,
    label: state.decisions.label,
  });
  try {
    const data = await api("/console/api/decisions?" + query);
    const payload = JSON.stringify(data.decisions || []);
    if (!animate && payload === state.lastData.decisions) return;
    state.lastData.decisions = payload;
    renderDecisions(data.decisions || [], animate);
  } catch (err) { toast(err.message); }
}

function renderDecisions(decisions, animate = false) {
  const view = $("#view-decisions");
  view.replaceChildren();

  const title = state.decisions.kind === "guard" ? "decisions · LLM guard" : "decisions · API abuse";
  const decisionsPanel = panel(title, "span-12");

  const filters = el("div", "filters");
  const kind = el("select");
  kind.title = "decision source";
  for (const value of ["guard", "abuse"]) {
    const option = el("option", null, value === "guard" ? "LLM guard" : "API abuse");
    option.value = value;
    kind.append(option);
  }
  kind.value = state.decisions.kind;
  kind.onchange = () => { state.decisions.kind = kind.value; loadDecisions(); };

  const labelFilter = el("select");
  labelFilter.title = "decision label";
  for (const value of ["", "block", "would-block", "allow", "error"]) {
    const option = el("option", null, value || "all labels");
    option.value = value;
    labelFilter.append(option);
  }
  labelFilter.value = state.decisions.label;
  labelFilter.onchange = () => { state.decisions.label = labelFilter.value; loadDecisions(); };

  const limit = el("select");
  limit.title = "rows per fetch";
  for (const value of [25, 50, 100, 250]) {
    const option = el("option", null, value + " rows");
    option.value = String(value);
    limit.append(option);
  }
  limit.value = String(state.decisions.limit);
  limit.onchange = () => { state.decisions.limit = Number(limit.value); loadDecisions(); };

  filters.append(el("label", null, "source"), kind);
  filters.append(el("label", null, "label"), labelFilter);
  filters.append(el("label", null, "show"), limit);

  if (state.decisions.kind !== "guard" || state.decisions.label || state.decisions.limit !== 50) {
    const reset = el("button", "btn ghost", "reset");
    reset.type = "button";
    reset.title = "restore the default filters";
    reset.onclick = () => {
      state.decisions.kind = "guard";
      state.decisions.label = "";
      state.decisions.limit = 50;
      loadDecisions();
    };
    filters.append(reset);
  }

  filters.append(el("span", "spacer"));
  filters.append(el("span", "mono", fmtCount(decisions.length) + (decisions.length === 1 ? " row" : " rows")));
  const refresh = el("button", "btn ghost", "refresh");
  refresh.type = "button";
  refresh.title = "fetch now";
  refresh.onclick = () => loadDecisions(true);
  filters.append(refresh);
  decisionsPanel.body.append(filters);

  const table = el("table", "table");
  const thead = el("thead");
  const head = el("tr");
  for (const name of ["time", "label", state.decisions.kind === "guard" ? "detail" : "client",
                      "model", "latency", "details"]) {
    head.append(el("th", null, name));
  }
  thead.append(head);
  const tbody = el("tbody");

  if (!decisions.length) {
    const row = el("tr");
    const cell = el("td", "hint", "No decisions match this filter.");
    cell.colSpan = 6;
    row.append(cell);
    tbody.append(row);
  }

  for (const entry of decisions) {
    const expanded = state.decisions.expanded.has(entry.id);
    const row = el("tr", "row click");
    row.title = expanded ? "click to hide details" : "click to show details";
    row.append(el("td", "mono", fmtClock(entry.ts)));
    const labelCell = el("td");
    labelCell.append(labelChip(entry.label));
    row.append(labelCell);
    if (state.decisions.kind === "guard") {
      row.append(el("td", null, entry.note || "prompt check"));
    } else {
      row.append(el("td", "mono", entry.client || ""));
    }
    row.append(el("td", "mono", entry.model || ""));
    row.append(el("td", "num", entry.latency_ms == null ? "" : Math.round(entry.latency_ms) + " ms"));
    const action = el("td");
    const toggle = el("button", "btn ghost", expanded ? "hide" : "details");
    toggle.type = "button";
    toggle.title = expanded ? "collapse this decision" : "expand this decision";
    toggle.setAttribute("aria-expanded", expanded ? "true" : "false");
    action.append(toggle);
    row.append(action);
    row.onclick = () => {
      if (state.decisions.expanded.has(entry.id)) state.decisions.expanded.delete(entry.id);
      else state.decisions.expanded.add(entry.id);
      renderDecisions(decisions, false);
    };
    tbody.append(row);
    if (expanded) tbody.append(detailRow(entry));
  }
  table.append(thead, tbody);
  decisionsPanel.body.append(table);
  view.append(decisionsPanel.section);
  stagger(view, animate);
}

function detailRow(entry) {
  const row = el("tr", "detail-row");
  const cell = el("td");
  cell.colSpan = 6;

  const facts = el("div", "detail-kv");
  const add = (key, value, cls) => facts.append(el("span", "k", key), el("span", cls, String(value)));
  add("time", fmtClock(entry.ts), "mono");
  if (entry.mode) add("mode", entry.mode);
  if (entry.threshold != null) add("threshold", Number(entry.threshold).toFixed(2), "mono");
  if (entry.model) add("model", entry.model);
  if (entry.category) {
    add("category", entry.category);
    add("abuse score", Number(entry.abuse_score || 0).toFixed(3), "mono");
    add("severity", Number(entry.severity || 0).toFixed(2), "mono");
    if (entry.recommended_action) add("action", entry.recommended_action);
  }
  if (entry.triggers && entry.triggers.length) {
    add("triggers", entry.triggers.map((t) => (typeof t === "string" ? t : t.question)).join(", "));
  }
  if (entry.note) add("note", entry.note);
  if (entry.error) add("error", entry.error, "mono");
  if (entry.id != null) add("id", entry.id, "mono");
  cell.append(facts);

  const probabilities = entry.probabilities ? Object.entries(entry.probabilities) : [];
  if (probabilities.length) {
    const scores = el("div", "detail-kv");
    scores.append(
      el("span", "k", "probabilities"),
      el("span", "mono", probabilities.length + (probabilities.length === 1 ? " question" : " questions"))
    );
    for (const [question, value] of probabilities) {
      scores.append(el("span", "k", question), el("span", "mono", Number(value).toFixed(3)));
    }
    cell.append(scores);
  }

  if (entry.narrative) cell.append(el("pre", "narrative", entry.narrative));
  row.append(cell);
  return row;
}

/* ------------------------------------------------------------------ clients */
async function loadClients(animate = false) {
  try {
    const data = await api("/console/api/clients?top=100");
    const payload = JSON.stringify(data.clients || []);
    if (!animate && payload === state.lastData.clients) return;
    state.lastData.clients = payload;
    renderClients(data.clients || [], animate);
  } catch (err) { toast(err.message); }
}

function renderClients(clients, animate = false) {
  const view = $("#view-clients");
  view.replaceChildren();

  const clientsPanel = panel("clients", "span-12");
  const blockedNow = clients.filter((client) => client.blocked_for_s != null).length;
  clientsPanel.body.append(el("div", "hint",
    fmtCount(clients.length) + (clients.length === 1 ? " client" : " clients") + " tracked · " +
    fmtCount(blockedNow) + " blocked now · blocks are enforced by the guard when abuse enforcement is on."));

  const table = el("table", "table");
  const thead = el("thead");
  const head = el("tr");
  for (const name of ["client", "ip", "requests", "rate/min", "label", "category", "last seen", "blocked", "action"]) {
    head.append(el("th", null, name));
  }
  thead.append(head);
  const tbody = el("tbody");

  if (!clients.length) {
    const row = el("tr");
    const cell = el("td", "hint", "No clients yet. Ship telemetry to /v1/abuse/events or run a bench scenario.");
    cell.colSpan = 9;
    row.append(cell);
    tbody.append(row);
  }

  for (const client of clients) {
    const blocked = client.blocked_for_s != null;
    const row = el("tr", "row");
    row.title = client.client || "";
    row.append(el("td", "mono", client.client || ""));
    row.append(el("td", "mono", client.ip || ""));
    row.append(el("td", "num", fmtCount(client.total)));
    row.append(el("td", "num", client.rate_per_min == null ? "" : Number(client.rate_per_min).toFixed(1)));
    const labelCell = el("td");
    if (client.label) labelCell.append(labelChip(client.label));
    row.append(labelCell);
    row.append(el("td", null, client.category || ""));
    row.append(el("td", null, client.last_seen_age_s == null ? "" : fmtAge(client.last_seen_age_s) + " ago"));
    row.append(el("td", "num", blocked ? Math.round(client.blocked_for_s) + "s" : ""));
    const action = el("td");
    if (blocked) {
      const button = el("button", "btn ghost", "unblock");
      button.type = "button";
      button.title = "lift the block for " + (client.client || "this client");
      button.onclick = () => unblock(client.client);
      action.append(button);
    }
    row.append(action);
    tbody.append(row);
  }
  table.append(thead, tbody);
  clientsPanel.body.append(table);
  view.append(clientsPanel.section);
  stagger(view, animate);
}

async function unblock(client) {
  try {
    await api("/console/api/unblock", { method: "POST", body: JSON.stringify({ client }) });
    toast("unblocked " + client);
    loadOverview(true);
    if (state.view === "clients") loadClients();
  } catch (err) { toast(err.message); }
}

/* ------------------------------------------------------------------ devices */
async function loadDevices(animate = false) {
  try {
    const [devices, clients] = await Promise.all([
      api("/console/api/devices"),
      api("/console/api/clients?top=100"),
    ]);
    const payload = JSON.stringify([devices.rules || [], clients.clients || []]);
    if (!animate && payload === state.lastData.devices) return;
    state.lastData.devices = payload;
    renderDevices(devices.rules || [], clients.clients || [], animate);
  } catch (err) { toast(err.message); }
}

function ruleExpiry(rule) {
  if (!rule.expires_at) return "permanent";
  const remaining = rule.expires_at - Date.now() / 1000;
  if (remaining <= 0) return "expired";
  return fmtAge(remaining) + " left";
}

function renderDevices(rules, clients, animate = false) {
  const view = $("#view-devices");
  view.replaceChildren();
  const ruleSet = new Set(rules.map((rule) => rule.device));

  const rulesPanel = panel("device blacklist", "span-12");
  rulesPanel.body.classList.add("pad");
  rulesPanel.body.append(el("div", "hint",
    "Blacklisted devices stay blocked across restarts until the rule is removed here. "
    + "The abuse detector cannot lift these blocks."));

  const form = el("div", "filters");
  const deviceInput = el("input");
  deviceInput.placeholder = "ip address or api key id";
  deviceInput.setAttribute("aria-label", "device");
  const reasonInput = el("input");
  reasonInput.placeholder = "reason (optional)";
  reasonInput.setAttribute("aria-label", "reason");
  const ttl = el("select");
  for (const [value, text] of [["3600", "1 hour"], ["86400", "24 hours"], ["604800", "7 days"], ["0", "permanent"]]) {
    const option = el("option", null, text);
    option.value = value;
    ttl.append(option);
  }
  const blockButton = el("button", "btn primary", "block device");
  blockButton.type = "button";
  blockButton.onclick = async () => {
    const device = deviceInput.value.trim();
    if (!device) { toast("device is required"); return; }
    await blockDevice(device, reasonInput.value.trim(), Number(ttl.value));
    deviceInput.value = "";
    reasonInput.value = "";
  };
  deviceInput.addEventListener("keydown", (event) => { if (event.key === "Enter") blockButton.click(); });
  reasonInput.addEventListener("keydown", (event) => { if (event.key === "Enter") blockButton.click(); });
  form.append(el("label", null, "device"), deviceInput);
  form.append(el("label", null, "reason"), reasonInput);
  form.append(el("label", null, "duration"), ttl);
  form.append(blockButton);
  rulesPanel.body.append(form);

  if (!rules.length) {
    rulesPanel.body.append(el("div", "hint", "No blacklist rules yet."));
  } else {
    const table = el("table", "table");
    const thead = el("thead");
    const head = el("tr");
    for (const name of ["device", "reason", "added", "expires", "state", "action"]) head.append(el("th", null, name));
    thead.append(head);
    const tbody = el("tbody");
    for (const rule of rules) {
      const row = el("tr");
      row.append(el("td", "mono", rule.device));
      row.append(el("td", null, rule.reason || ""));
      row.append(el("td", null, fmtClock(rule.created_at)));
      row.append(el("td", null, ruleExpiry(rule)));
      row.append(el("td", null, rule.blocked_for_s != null ? "blocked" : "pending sync"));
      const action = el("td");
      const button = el("button", "btn ghost", "unblock");
      button.type = "button";
      button.onclick = () => removeDeviceRule(rule.device);
      action.append(button);
      row.append(action);
      tbody.append(row);
    }
    table.append(thead, tbody);
    rulesPanel.body.append(table);
  }
  view.append(rulesPanel.section);

  const knownPanel = panel("known devices", "span-12");
  knownPanel.body.append(el("div", "hint",
    fmtCount(clients.length) + " devices seen by the telemetry detector. Blocking one adds it to the blacklist."));
  const table = el("table", "table");
  const thead = el("thead");
  const head = el("tr");
  for (const name of ["device", "ip", "requests", "label", "last seen", "action"]) head.append(el("th", null, name));
  thead.append(head);
  const tbody = el("tbody");
  if (!clients.length) {
    const row = el("tr");
    const cell = el("td", "hint", "No devices yet. Ship telemetry to /v1/abuse/events or run a bench scenario.");
    cell.colSpan = 6;
    row.append(cell);
    tbody.append(row);
  }
  for (const client of clients) {
    const row = el("tr", "row");
    row.append(el("td", "mono", client.client || ""));
    row.append(el("td", "mono", client.ip || ""));
    row.append(el("td", "num", fmtCount(client.total)));
    const labelCell = el("td");
    if (client.label) labelCell.append(labelChip(client.label));
    row.append(labelCell);
    row.append(el("td", null, client.last_seen_age_s == null ? "" : fmtAge(client.last_seen_age_s) + " ago"));
    const action = el("td");
    const inRules = ruleSet.has(client.client);
    const blocked = client.blocked_for_s != null;
    if (inRules) action.append(el("span", "label-chip block", "blacklisted"));
    if (inRules || blocked) {
      const button = el("button", "btn ghost", "unblock");
      button.type = "button";
      button.title = inRules ? "remove the blacklist rule" : "lift the current block";
      button.onclick = () => removeDeviceRule(client.client);
      action.append(button);
    } else {
      const button = el("button", "btn ghost", "block");
      button.type = "button";
      button.onclick = () => blockDevice(client.client, reasonInput.value.trim(), Number(ttl.value));
      action.append(button);
    }
    row.append(action);
    tbody.append(row);
  }
  table.append(thead, tbody);
  knownPanel.body.append(table);
  view.append(knownPanel.section);
  stagger(view, animate);
}

async function blockDevice(device, reason, ttl) {
  try {
    const result = await api("/console/api/devices/block", {
      method: "POST",
      body: JSON.stringify({ device, reason, ttl }),
    });
    toast("blocked " + device + (result.permanent ? " permanently" : ""));
    loadDevices();
    refreshHealth();
  } catch (err) { toast(err.message); }
}

async function removeDeviceRule(device) {
  try {
    await api("/console/api/devices/unblock", { method: "POST", body: JSON.stringify({ device }) });
    toast("unblocked " + device);
    loadDevices();
    refreshHealth();
    if (state.view === "overview") loadOverview(true);
  } catch (err) { toast(err.message); }
}

/* -------------------------------------------------------------------- agent */
const agentChat = [];

function renderChatLog(log) {
  log.replaceChildren();
  if (!agentChat.length) {
    log.append(el("div", "chat-msg agent",
      "Hi. Ask me what is blocked, which rules exist, what looks wrong, or who is sending the most traffic."));
    return;
  }
  for (const message of agentChat) {
    const bubble = el("div", "chat-msg " + (message.role === "user" ? "user" : "agent"));
    if (message.role === "assistant") {
      const meta = message.source || "";
      bubble.append(el("div", "meta", meta + (message.error ? " · " + message.error : "")));
    }
    bubble.append(el("div", "body", message.content));
    log.append(bubble);
  }
  log.scrollTop = log.scrollHeight;
}

async function loadAgent(animate = false) {
  try {
    const data = await api("/console/api/agent");
    const payload = JSON.stringify([
      data.enabled, data.mode, data.interval, data.llm, data.runs, data.actions_taken,
      data.last_summary, data.last_error, data.events,
    ]);
    if (!animate && payload === state.lastData.agent) return;
    state.lastData.agent = payload;
    if (!state.agentView || animate) buildAgentView(data, animate);
    else updateAgentView(data);
  } catch (err) { toast(err.message); }
}

function agentStatusText(data) {
  const lastRun = data.last_run_age_s == null ? "waiting for first scan" : fmtAge(data.last_run_age_s) + " ago";
  return (data.enabled ? "running" : "disabled") + " · " + data.runs + " scans · "
    + data.actions_taken + " actions taken · last scan " + lastRun
    + (data.last_summary ? " · " + data.last_summary : "")
    + (data.last_error ? " · " + data.last_error : "");
}

function renderAgentEvents(box, data) {
  box.replaceChildren();
  const events = data.events || [];
  if (!events.length) {
    box.append(el("div", "hint", "No agent activity yet. It scans every " + data.interval + " seconds."));
    return;
  }
  const table = el("table", "table");
  const thead = el("thead");
  const head = el("tr");
  for (const name of ["time", "mode", "source", "action", "target", "reason"]) head.append(el("th", null, name));
  thead.append(head);
  const tbody = el("tbody");
  for (const event of events) {
    const row = el("tr");
    row.append(el("td", "mono", fmtClock(event.ts)));
    row.append(el("td", null, event.mode || ""));
    row.append(el("td", null, event.source || ""));
    const actionCell = el("td");
    actionCell.append(labelChip(event.action === "block_client" ? "block" : "would-block"));
    actionCell.append(el("span", "mono", " " + (event.action || "")));
    row.append(actionCell);
    row.append(el("td", "mono", event.target || ""));
    row.append(el("td", null, event.reason || ""));
    tbody.append(row);
  }
  table.append(thead, tbody);
  box.append(table);
}

function updateAgentView(data) {
  const refs = state.agentView;
  if (!refs) return;
  if (refs.statusLine) refs.statusLine.textContent = agentStatusText(data);
  if (refs.eventsBox) renderAgentEvents(refs.eventsBox, data);
}

function buildAgentView(data, animate = false) {
  const view = $("#view-agent");
  view.replaceChildren();
  state.agentView = {};

  const statusPanel = panel("agent monitor", "span-12");
  statusPanel.body.classList.add("pad");
  const statusLine = el("div", "hint", agentStatusText(data));
  state.agentView.statusLine = statusLine;
  statusPanel.body.append(statusLine);

  const form = el("div", "filters");
  const enabled = el("input");
  enabled.type = "checkbox";
  enabled.checked = Boolean(data.enabled);
  const enabledLine = el("label", "checkline");
  enabledLine.append(enabled, "enabled");
  const mode = el("select");
  for (const value of ["act", "monitor"]) {
    const option = el("option", null, value);
    option.value = value;
    mode.append(option);
  }
  mode.value = data.mode;
  const interval = el("input", "num");
  interval.type = "number";
  interval.min = "5";
  interval.max = "3600";
  interval.value = data.interval;
  const llm = el("input");
  llm.type = "checkbox";
  llm.checked = Boolean(data.llm);
  const llmLine = el("label", "checkline");
  llmLine.append(llm, "model review");
  const saveButton = el("button", "btn primary", "save agent");
  saveButton.type = "button";
  saveButton.onclick = async () => {
    try {
      await api("/console/api/settings", {
        method: "POST",
        body: JSON.stringify({
          agent_enabled: enabled.checked,
          agent_mode: mode.value,
          agent_interval: interval.value,
          agent_llm: llm.checked,
        }),
      });
      toast("agent settings saved");
      loadAgent();
      refreshHealth();
    } catch (err) { toast(err.message); }
  };
  const runButton = el("button", "btn ghost", "run now");
  runButton.type = "button";
  runButton.onclick = async () => {
    runButton.disabled = true;
    try {
      const result = await api("/console/api/agent/run", { method: "POST" });
      const r = result.result || {};
      toast("agent: " + (r.summary || "done"));
      loadAgent();
      refreshHealth();
      if (state.view === "overview") loadOverview(true);
    } catch (err) { toast(err.message); }
    runButton.disabled = false;
  };
  form.append(enabledLine);
  form.append(el("label", null, "mode"), mode);
  form.append(el("label", null, "interval (s)"), interval);
  form.append(llmLine);
  form.append(saveButton, runButton);
  statusPanel.body.append(form);
  view.append(statusPanel.section);

  const eventsPanel = panel("agent actions", "span-12");
  const eventsBox = el("div");
  renderAgentEvents(eventsBox, data);
  state.agentView.eventsBox = eventsBox;
  eventsPanel.body.append(eventsBox);
  view.append(eventsPanel.section);

  const chatPanel = panel("agent chat", "span-12");
  chatPanel.body.classList.add("pad");
  chatPanel.body.append(el("div", "hint",
    "Ask about the live state (blocked devices, rules, anomalies, top traffic) or give commands: "
    + "block 203.0.113.9, unblock 203.0.113.9, set abuse mode block, enforcement off, threshold 0.6, scan now. "
    + "Commands apply in act mode, proposals are logged in monitor mode."));
  const chatLog = el("div", "chat-log");
  renderChatLog(chatLog);
  const chatRow = el("div", "chat-row");
  const chatInput = el("input");
  chatInput.placeholder = "ask the agent...";
  chatInput.setAttribute("aria-label", "message");
  const sendButton = el("button", "btn primary", "send");
  sendButton.type = "button";
  const clearButton = el("button", "btn ghost", "clear");
  clearButton.type = "button";
  const sendChat = async () => {
    const text = chatInput.value.trim();
    if (!text) return;
    chatInput.value = "";
    agentChat.push({ role: "user", content: text });
    renderChatLog(chatLog);
    const pending = el("div", "chat-msg agent pending", "thinking...");
    chatLog.append(pending);
    chatLog.scrollTop = chatLog.scrollHeight;
    sendButton.disabled = true;
    try {
      const history = agentChat.slice(0, -1).slice(-10);
      const result = await api("/console/api/agent/chat", {
        method: "POST",
        body: JSON.stringify({ message: text, history }),
      });
      agentChat.push({
        role: "assistant",
        content: result.reply || "(no reply)",
        source: result.source || "",
        error: result.error || "",
      });
    } catch (err) {
      agentChat.push({ role: "assistant", content: err.message, source: "error" });
    }
    sendButton.disabled = false;
    renderChatLog(chatLog);
  };
  sendButton.onclick = sendChat;
  clearButton.onclick = () => { agentChat.length = 0; renderChatLog(chatLog); };
  chatInput.addEventListener("keydown", (event) => { if (event.key === "Enter") sendChat(); });
  chatRow.append(chatInput, sendButton, clearButton);
  chatPanel.body.append(chatLog, chatRow);
  view.append(chatPanel.section);
  stagger(view, animate);
}

/* ----------------------------------------------------------------- settings */
async function loadSettings(animate = false) {
  try {
    const [settings, system] = await Promise.all([
      api("/console/api/settings"),
      api("/console/api/system"),
    ]);
    const payload = JSON.stringify([
      settings, system.retention_days,
      Object.keys(system.blocked_clients || {}).length,
      system.abuse_mode, system.abuse_enforce,
      system.agent_mode, system.agent_interval, system.agent_enabled, system.agent_llm,
    ]);
    if (!animate && payload === state.lastData.settings) return;
    state.lastData.settings = payload;
    state.settings = settings;
    renderSettings(settings, system, animate);
  } catch (err) { toast(err.message); }
}

function field(label, control, hint) {
  const wrap = el("div", "field");
  wrap.append(el("label", null, label), control);
  if (hint) wrap.append(el("div", "hint", hint));
  return wrap;
}

function selectOptions(values, current) {
  const select = el("select");
  for (const value of values) {
    const option = el("option", null, value);
    option.value = value;
    select.append(option);
  }
  select.value = current;
  return select;
}

function numberInput(value, opts = {}) {
  const input = el("input", "num");
  input.type = "number";
  if (opts.step) input.step = opts.step;
  if (opts.min) input.min = opts.min;
  if (opts.max) input.max = opts.max;
  input.value = value;
  return input;
}

function renderSettings(settings, system, animate = false) {
  const view = $("#view-settings");
  view.replaceChildren();

  const guardCard = panel("LLM guard", "span-6");
  guardCard.body.classList.add("pad");
  const guardGrid = el("div", "form-grid");
  const guardMode = selectOptions(["block", "monitor"], settings.guard_mode);
  guardGrid.append(field("mode", guardMode, "block drops requests, monitor only logs them"));
  const thresholds = {};
  for (const [key, label] of [
    ["guard_threshold_prompt_injection", "prompt injection"],
    ["guard_threshold_hidden_instructions", "hidden instructions"],
    ["guard_threshold_secret_request", "secret requests"],
  ]) {
    const input = numberInput(settings[key], { step: "0.01", min: "0.01", max: "1" });
    thresholds[key] = input;
    guardGrid.append(field(label + " threshold", input));
  }
  guardCard.body.append(guardGrid);
  const guardActions = el("div", "form-actions");
  const guardSave = el("button", "btn primary", "save guard");
  guardSave.type = "button";
  guardSave.onclick = async () => {
    const body = { guard_mode: guardMode.value };
    for (const [key, input] of Object.entries(thresholds)) body[key] = input.value;
    await save(body, "guard settings saved");
  };
  guardActions.append(guardSave);
  guardCard.body.append(guardActions);
  view.append(guardCard.section);

  const abuseCard = panel("API abuse", "span-6");
  abuseCard.body.classList.add("pad");
  const abuseGrid = el("div", "form-grid");
  const abuseMode = selectOptions(["monitor", "block"], settings.abuse_mode);
  abuseGrid.append(field("mode", abuseMode, "monitor flags clients, block enforces 429s"));
  const abuseThreshold = numberInput(settings.abuse_threshold, { step: "0.01", min: "0.01", max: "1" });
  abuseGrid.append(field("decision threshold", abuseThreshold, "abuse score at which a client is flagged"));
  const abuseTtl = numberInput(settings.abuse_block_ttl, { min: "5" });
  abuseGrid.append(field("block ttl (seconds)", abuseTtl, "how long an enforced block lasts"));
  const enforce = el("input");
  enforce.type = "checkbox";
  enforce.checked = settings.abuse_enforce === "1";
  const enforceLine = el("label", "checkline");
  enforceLine.append(enforce, "enforce blocks on the protected API");
  const enforceField = el("div", "field");
  enforceField.append(el("label", null, "enforcement"), enforceLine);
  abuseGrid.append(enforceField);
  abuseCard.body.append(abuseGrid);
  const abuseActions = el("div", "form-actions");
  const abuseSave = el("button", "btn primary", "save abuse");
  abuseSave.type = "button";
  abuseSave.onclick = async () => {
    await save({
      abuse_mode: abuseMode.value,
      abuse_threshold: abuseThreshold.value,
      abuse_block_ttl: abuseTtl.value,
      abuse_enforce: enforce.checked,
    }, "abuse settings saved");
  };
  abuseActions.append(abuseSave);
  abuseCard.body.append(abuseActions);
  view.append(abuseCard.section);

  const upstreamCard = panel("upstream model", "span-6");
  upstreamCard.body.classList.add("pad");
  const upstreamGrid = el("div", "form-grid");
  const base = el("input");
  base.type = "text";
  base.value = settings.upstream_base || "";
  base.placeholder = "https://api.deepseek.com";
  upstreamGrid.append(field("base url", base, "OpenAI compatible endpoint the guard forwards to"));
  const model = el("input");
  model.type = "text";
  model.value = settings.upstream_model || "";
  model.placeholder = "deepseek-chat";
  upstreamGrid.append(field("model", model, "model id sent on every forwarded request"));
  const key = el("input");
  key.type = "password";
  key.placeholder = settings.upstream_key_state.set ? "replace stored key" : "paste the api key";
  const keyHint = settings.upstream_key_state.set
    ? "currently " + settings.upstream_key_state.hint
    : "not set, requests that pass the guard will fail upstream";
  upstreamGrid.append(field("api key", key, keyHint));
  upstreamCard.body.append(upstreamGrid);
  const upstreamActions = el("div", "form-actions");
  const upstreamSave = el("button", "btn primary", "save upstream");
  upstreamSave.type = "button";
  upstreamSave.onclick = async () => {
    const body = { upstream_base: base.value, upstream_model: model.value };
    if (key.value) body.upstream_key = key.value;
    await save(body, "upstream saved");
  };
  const clearKey = el("button", "btn ghost", "clear stored key");
  clearKey.type = "button";
  clearKey.onclick = async () => {
    await save({ upstream_key_clear: true }, "stored key cleared");
  };
  upstreamActions.append(upstreamSave, clearKey);
  upstreamCard.body.append(upstreamActions);
  view.append(upstreamCard.section);

  const adminCard = panel("console password", "span-6");
  adminCard.body.classList.add("pad");
  const adminGrid = el("div", "form-grid");
  const current = el("input");
  current.type = "password";
  adminGrid.append(field("current password", current));
  const next = el("input");
  next.type = "password";
  adminGrid.append(field("new password", next, "at least 8 characters"));
  adminCard.body.append(adminGrid);
  const adminActions = el("div", "form-actions");
  const adminSave = el("button", "btn primary", "change password");
  adminSave.type = "button";
  adminSave.onclick = async () => {
    try {
      await api("/console/api/password", {
        method: "POST",
        body: JSON.stringify({ current: current.value, new: next.value }),
      });
      current.value = "";
      next.value = "";
      toast("password changed");
    } catch (err) { toast(err.message); }
  };
  adminActions.append(adminSave);
  adminCard.body.append(adminActions);
  view.append(adminCard.section);

  const retentionCard = panel("data retention", "span-6");
  retentionCard.body.classList.add("pad");
  const retentionGrid = el("div", "form-grid");
  const days = numberInput(settings.retention_days, { min: "1", max: "365" });
  retentionGrid.append(field("keep decisions for (days)", days, "older rows are purged hourly"));
  retentionCard.body.append(retentionGrid);
  const retentionActions = el("div", "form-actions");
  const retentionSave = el("button", "btn primary", "save retention");
  retentionSave.type = "button";
  retentionSave.onclick = async () => { await save({ retention_days: days.value }, "retention saved"); };
  const purge = el("button", "btn ghost", "purge older rows now");
  purge.type = "button";
  purge.onclick = async () => { await purgeNow(); };
  retentionActions.append(retentionSave, purge);
  retentionCard.body.append(retentionActions);
  view.append(retentionCard.section);

  const systemCard = panel("system", "span-6");
  systemCard.section.id = "panel-system";
  systemCard.body.classList.add("pad");
  const kv = el("div", "detail-kv");
  const add = (k, v) => kv.append(el("span", "k", k), el("span", "mono", String(v)));
  add("uptime", fmtAge(system.uptime_s));
  add("database", system.db_path + " (" + Math.max(1, Math.round(system.db_bytes / 1024)) + " KB)");
  add("retention", system.retention_days + " days");
  add("upstream", system.upstream_base + " · " + system.upstream_model);
  add("upstream key", system.upstream_key.hint || "not set");
  add("abuse enforcement", system.abuse_enforce ? "on" : "off");
  add("blocked clients", Object.keys(system.blocked_clients || {}).length);
  add("guard fastpath", settings.guard_fastpath ? "enabled" : "disabled");
  systemCard.body.append(kv);
  view.append(systemCard.section);

  stagger(view, animate);
}

async function save(body, okMessage) {
  try {
    await api("/console/api/settings", { method: "POST", body: JSON.stringify(body) });
    toast(okMessage);
    loadSettings();
    refreshHealth();
  } catch (err) { toast(err.message); }
}

async function purgeNow() {
  try {
    const result = await api("/console/api/purge", { method: "POST" });
    const total = Object.values(result.removed || {}).reduce((a, b) => a + b, 0);
    toast("purged " + total + " rows");
    if (state.view === "settings") loadSettings();
  } catch (err) { toast(err.message); }
}

async function toggleMode(key, label) {
  try {
    const settings = state.settings || (await api("/console/api/settings"));
    const next = settings[key] === "block" ? "monitor" : "block";
    await api("/console/api/settings", { method: "POST", body: JSON.stringify({ [key]: next }) });
    state.settings = null;
    toast(label + " set to " + next);
    refreshHealth();
    if (state.view === "settings") loadSettings();
  } catch (err) { toast(err.message); }
}

/* --------------------------------------------------------------- commands */
const COMMANDS = [
  { id: "overview", title: "Go to overview", run: () => showView("overview") },
  { id: "decisions", title: "Go to decisions", run: () => showView("decisions") },
  { id: "clients", title: "Go to clients", run: () => showView("clients") },
  { id: "devices", title: "Go to devices", run: () => showView("devices") },
  { id: "agent", title: "Go to agent monitor", run: () => showView("agent") },
  { id: "settings", title: "Go to settings", run: () => showView("settings") },
  { id: "guard-mode", title: "Toggle LLM guard mode", meta: () => state.health ? state.health.mode : "",
    run: () => toggleMode("guard_mode", "guard mode") },
  { id: "abuse-mode", title: "Toggle API abuse mode", meta: () => state.health ? state.health.abuse.mode : "",
    run: () => toggleMode("abuse_mode", "abuse mode") },
  { id: "purge", title: "Purge rows older than retention", run: () => purgeNow() },
  { id: "system", title: "Open system info", run: () => {
    showView("settings");
    setTimeout(() => { const node = $("#panel-system"); if (node) node.scrollIntoView({ behavior: "smooth", block: "center" }); }, 60);
  } },
  { id: "chat", title: "Open chat tool (demo)", run: () => window.open("/chat", "_blank") },
  { id: "bench", title: "Open abuse bench (demo)", run: () => window.open("/api-abuse", "_blank") },
  { id: "logout", title: "Log out", run: async () => {
    await fetch("/console/api/logout", { method: "POST" });
    location.href = "/login";
  } },
];

function runCommand(command) {
  closePalette();
  closeMenu();
  command.run();
}

/* -------------------------------------------------------------------- menu */
function menuItem(command) {
  const button = el("button", "menu-item");
  button.type = "button";
  button.append(el("span", null, command.title));
  const meta = typeof command.meta === "function" ? command.meta() : command.meta;
  if (meta) button.append(el("span", "meta", meta));
  button.onclick = () => runCommand(command);
  return button;
}

function openMenu() {
  closePalette();
  const menu = $("#menu");
  menu.replaceChildren();
  for (const id of ["overview", "decisions", "clients", "devices", "agent", "settings"]) {
    menu.append(menuItem(COMMANDS.find((c) => c.id === id)));
  }
  menu.append(el("div", "menu-sep"));
  for (const id of ["guard-mode", "abuse-mode"]) menu.append(menuItem(COMMANDS.find((c) => c.id === id)));
  menu.append(el("div", "menu-sep"));
  for (const id of ["system", "chat", "bench"]) menu.append(menuItem(COMMANDS.find((c) => c.id === id)));
  menu.append(el("div", "menu-sep"));
  menu.append(menuItem(COMMANDS.find((c) => c.id === "logout")));
  menu.classList.remove("hidden");
}

function closeMenu() {
  $("#menu").classList.add("hidden");
}

/* ----------------------------------------------------------------- palette */
let paletteIndex = 0;
let paletteMatches = [];

function openPalette() {
  closeMenu();
  $("#palette").classList.remove("hidden");
  const input = $("#palette-input");
  input.value = "";
  paletteIndex = 0;
  renderPalette("");
  input.focus();
}

function closePalette() {
  $("#palette").classList.add("hidden");
}

function renderPalette(query) {
  paletteMatches = COMMANDS.filter((c) => c.title.toLowerCase().includes(query.toLowerCase()));
  if (paletteIndex >= paletteMatches.length) paletteIndex = Math.max(0, paletteMatches.length - 1);
  const list = $("#palette-list");
  list.replaceChildren();
  if (!paletteMatches.length) {
    list.append(el("div", "palette-item", "no matches"));
    return;
  }
  paletteMatches.forEach((command, i) => {
    const item = el("div", "palette-item" + (i === paletteIndex ? " sel" : ""));
    item.append(el("span", null, command.title));
    const meta = typeof command.meta === "function" ? command.meta() : command.meta;
    if (meta) item.append(el("span", "meta", meta));
    item.onmousedown = (event) => { event.preventDefault(); runCommand(command); };
    list.append(item);
  });
  const selected = list.children[paletteIndex];
  if (selected) selected.scrollIntoView({ block: "nearest" });
}

/* ------------------------------------------------------------------- status */
async function refreshHealth() {
  try {
    const res = await fetch("/health");
    const h = await res.json();
    state.health = h;
    const top = $("#top-status");
    top.replaceChildren();
    const chip = (label, value, on, warn) => {
      const node = el("span", "chip");
      node.append(el("span", "dot" + (on ? " on" : warn ? " warn" : "")), label, el("span", "mono", value));
      return node;
    };
    top.append(
      chip("guard", h.mode, h.mode === "block", h.mode !== "block"),
      chip("abuse", h.abuse.mode, h.abuse.mode === "block", h.abuse.mode !== "block"),
      chip("key", h.key_set ? "set" : "missing", h.key_set, !h.key_set),
      chip("device", h.device, true, false),
    );
    if (h.agent) {
      top.append(chip(
        "agent",
        h.agent.enabled ? h.agent.mode : "off",
        Boolean(h.agent.enabled) && h.agent.mode === "act",
        Boolean(h.agent.enabled) && h.agent.mode !== "act",
      ));
    }
  } catch {
    const top = $("#top-status");
    top.replaceChildren();
    const node = el("span", "chip");
    node.title = "could not reach /health";
    node.append(el("span", "dot warn"), "server unreachable");
    top.append(node);
  }
}

/* --------------------------------------------------------------------- init */
$("#menu-btn").onclick = (event) => {
  event.stopPropagation();
  if ($("#menu").classList.contains("hidden")) openMenu();
  else closeMenu();
};
document.addEventListener("click", (event) => {
  if (!$("#menu").classList.contains("hidden")
      && !$("#menu").contains(event.target)
      && event.target !== $("#menu-btn")) {
    closeMenu();
  }
});
$("#refresh").onclick = () => {
  const load = { overview: loadOverview, decisions: loadDecisions, clients: loadClients, devices: loadDevices, agent: loadAgent, settings: loadSettings }[state.view];
  const icon = $("#refresh svg");
  if (icon) {
    icon.classList.add("spinning");
    setTimeout(() => icon.classList.remove("spinning"), 700);
  }
  load(true);
  refreshHealth();
};
$("#palette-btn").onclick = openPalette;
$("#palette").addEventListener("click", (event) => {
  if (event.target === $("#palette")) closePalette();
});
$("#palette-input").addEventListener("input", (event) => {
  paletteIndex = 0;
  renderPalette(event.target.value);
});
$("#palette-input").addEventListener("keydown", (event) => {
  if (event.key === "ArrowDown") {
    event.preventDefault();
    paletteIndex = Math.min(paletteIndex + 1, paletteMatches.length - 1);
    renderPalette($("#palette-input").value);
  } else if (event.key === "ArrowUp") {
    event.preventDefault();
    paletteIndex = Math.max(paletteIndex - 1, 0);
    renderPalette($("#palette-input").value);
  } else if (event.key === "Enter") {
    const command = paletteMatches[paletteIndex];
    if (command) runCommand(command);
  } else if (event.key === "Escape") {
    closePalette();
  }
});
document.addEventListener("keydown", (event) => {
  if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === "k") {
    event.preventDefault();
    openPalette();
  } else if (event.key === "Escape") {
    closePalette();
    closeMenu();
  }
});

showView("overview");
refreshHealth();
setInterval(() => { if (!document.hidden) refreshHealth(); }, 10000);
