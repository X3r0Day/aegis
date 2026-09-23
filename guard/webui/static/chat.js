/* Laya Guard chat — conversations + guard verdicts. No framework. */
"use strict";

const $ = (sel) => document.querySelector(sel);
const STORE = "laya-guard-chat/v1";

const ui = {
  thread: $("#thread"),
  input: $("#input"),
  send: $("#send"),
  newChat: $("#new-chat"),
  modeBlock: $("#mode-block"),
  modeMonitor: $("#mode-monitor"),
  convList: $("#conv-list"),
  guardLog: $("#guard-log"),
  logCounts: $("#log-counts"),
  status: $("#status"),
  banner: $("#banner"),
  toast: $("#toast"),
};

const state = {
  conversations: [],
  activeId: null,
  mode: "block",
  sending: false,
  keySet: true,
};

/* ------------------------------------------------------------------ utils */
function el(tag, className, text) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text !== undefined) node.textContent = text;
  return node;
}

function escapeHtml(s) {
  return s.replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
}

/* tiny markdown: fenced code, inline code, bold, italics, lists, line breaks */
function mdToHtml(src) {
  const blocks = [];
  let text = String(src).replace(/```([\w+-]*)\n?([\s\S]*?)```/g, (_, lang, code) => {
    blocks.push(`<pre class="code"><code>${escapeHtml(code.replace(/\n$/, ""))}</code></pre>`);
    return "\u0000B" + (blocks.length - 1) + "\u0000";
  });

  const inline = (s) =>
    s.replace(/`([^`\n]+)`/g, "<code>$1</code>")
     .replace(/\*\*([^*]+)\*\*/g, "<strong>$1</strong>")
     .replace(/(^|[\s(])\*([^*\n]+)\*/g, "$1<em>$2</em>");

  const out = [];
  for (const para of escapeHtml(text).split(/\n{2,}/)) {
    const trimmed = para.trim();
    const blockRef = trimmed.match(/^\u0000B(\d+)\u0000$/);
    if (blockRef) { out.push(blocks[Number(blockRef[1])]); continue; }
    const lines = para.split("\n");
    const pieces = [];
    let list = null;
    const closeList = () => { if (list) { pieces.push("<ul>" + list.join("") + "</ul>"); list = null; } };
    for (const line of lines) {
      const item = line.match(/^\s*[-*]\s+(.*)$/);
      if (item) { (list = list || []).push("<li>" + inline(item[1]) + "</li>"); continue; }
      closeList();
      if (line.trim()) pieces.push(inline(line));
    }
    closeList();
    if (pieces.length) out.push("<p>" + pieces.join("<br>") + "</p>");
  }
  return out.join("");
}

let toastTimer = null;
function toast(msg, ms = 3200) {
  ui.toast.textContent = msg;
  ui.toast.classList.remove("hidden");
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => ui.toast.classList.add("hidden"), ms);
}

/* ---------------------------------------------------------- persistence */
function save() {
  try {
    localStorage.setItem(STORE, JSON.stringify({
      conversations: state.conversations,
      activeId: state.activeId,
      mode: state.mode,
    }));
  } catch { /* ignore */ }
}

function load() {
  try {
    const saved = JSON.parse(localStorage.getItem(STORE) || "null");
    if (saved && Array.isArray(saved.conversations)) {
      state.conversations = saved.conversations;
      state.activeId = saved.activeId || null;
      state.mode = saved.mode === "monitor" ? "monitor" : "block";
    }
  } catch { /* start fresh */ }
  if (!state.conversations.length) newConversation({ silent: true });
  if (!activeConv()) state.activeId = state.conversations[0].id;
}

function activeConv() {
  return state.conversations.find((c) => c.id === state.activeId) || null;
}

function newConversation({ silent = false } = {}) {
  const conv = { id: "c" + Date.now().toString(36) + Math.random().toString(36).slice(2, 6), title: "New chat", messages: [], ts: Date.now() };
  state.conversations.unshift(conv);
  state.activeId = conv.id;
  if (!silent) { save(); renderAll(); ui.input.focus(); }
  return conv;
}

/* ------------------------------------------------------------ rendering */
function renderAll() {
  renderConversations();
  renderThread();
  renderMode();
}

function renderMode() {
  ui.modeBlock.classList.toggle("active", state.mode === "block");
  ui.modeMonitor.classList.toggle("active", state.mode === "monitor");
}

function renderConversations() {
  ui.convList.replaceChildren();
  for (const conv of state.conversations) {
    const row = el("div", "conv" + (conv.id === state.activeId ? " active" : ""));
    row.append(el("span", "title", conv.title || "New chat"));
    const del = el("button", "del", "✕");
    del.title = "Delete conversation";
    del.onclick = (e) => {
      e.stopPropagation();
      state.conversations = state.conversations.filter((c) => c.id !== conv.id);
      if (state.activeId === conv.id) state.activeId = state.conversations[0] ? state.conversations[0].id : null;
      if (!state.conversations.length) newConversation({ silent: true });
      save(); renderAll();
    };
    row.append(del);
    row.onclick = () => { state.activeId = conv.id; save(); renderAll(); };
    ui.convList.append(row);
  }
}

function renderThread() {
  const conv = activeConv();
  ui.thread.replaceChildren();
  if (!conv || !conv.messages.length) {
    const empty = el("div", "empty-state");
    empty.append(
      el("p", "t", "Chat with the model through the guard."),
      el("p", "n", "Every message is checked by Laya before it leaves this machine. Prompt injections are dropped, not forwarded."),
    );
    ui.thread.append(empty);
    return;
  }
  conv.messages.forEach((msg) => ui.thread.append(renderMessage(msg)));
  ui.thread.scrollTop = ui.thread.scrollHeight;
}

function verdictLabel(v) {
  if (!v) return "guarded";
  if (v.label) return v.label;
  return v.decision === "block" ? "block" : "allow";
}

function guardBadge(v) {
  const label = verdictLabel(v);
  const p = v.probabilities ? Math.max(...Object.values(v.probabilities)) : null;
  const text = label === "allow"
    ? "guard · allow"
    : label === "block" ? "guard · BLOCKED"
    : label === "would-block" ? "guard · would block"
    : "guard · error";
  const badge = el("span", "guard-badge " + label, p != null ? `${text} (${Number(p).toFixed(2)})` : text);
  badge.title = "Click for details";
  return badge;
}

function verdictDetail(v) {
  const detail = el("div", "verdict-detail");
  const rows = [];
  const thresholds = v.thresholds || null;
  for (const [q, p] of Object.entries(v.probabilities || {})) {
    const limit = thresholds && thresholds[q] != null ? ` (limit ${Number(thresholds[q]).toFixed(2)})` : "";
    rows.push([q + limit, Number(p).toFixed(3)]);
  }
  if (v.model) rows.push(["model", v.model]);
  if (v.threshold != null && !thresholds) rows.push(["threshold", Number(v.threshold).toFixed(2)]);
  if (v.latency_ms != null) rows.push(["guard latency", Number(v.latency_ms).toFixed(0) + " ms"]);
  if (v.mode) rows.push(["mode", v.mode]);
  if (v.scope) rows.push(["scope", v.scope]);
  if (v.error) rows.push(["error", v.error]);
  for (const [key, value] of rows) {
    const row = el("div", "row");
    row.append(el("span", null, key), el("b", null, String(value)));
    detail.append(row);
  }
  return detail;
}

function renderMessage(msg) {
  if (msg.role === "guard") {
    const wrap = el("div", "msg");
    const card = el("div", "block-card");
    const label = verdictLabel(msg.verdict);
    const head = el("div", "head", label === "would-block"
      ? "⚠ Laya Guard flagged this message (monitor mode — forwarded anyway)"
      : "⛔ Blocked by Laya Guard — prompt injection");
    card.append(head);
    const trig = (msg.verdict && msg.verdict.triggers || []).map((t) => `${t.question} = ${Number(t.probability).toFixed(2)}`).join(", ");
    if (trig) card.append(el("div", "detail", trig));
    if (msg.verdict) card.append(verdictDetail(msg.verdict));
    card.append(el("div", "note", label === "would-block"
      ? "The request was still sent upstream because monitor mode is on."
      : "The request was not forwarded to the upstream model."));
    wrap.append(card);
    return wrap;
  }

  const wrap = el("div", "msg " + (msg.role === "assistant" ? "assistant" : msg.role === "error" ? "assistant error" : "user"));
  const who = el("div", "who", msg.role === "assistant" ? "deepseek" : msg.role === "error" ? "error" : "you");
  if (msg.verdict) {
    const badge = guardBadge(msg.verdict);
    badge.onclick = () => wrap.classList.toggle("open");
    who.append(badge);
  }
  wrap.append(who);

  const bubble = el("div", "bubble");
  if (msg.role === "assistant") bubble.innerHTML = mdToHtml(msg.content || "");
  else bubble.textContent = msg.content || "";
  wrap.append(bubble);

  if (msg.verdict) {
    const detail = verdictDetail(msg.verdict);
    detail.classList.add("hidden");
    wrap.append(detail);
    const observer = () => detail.classList.toggle("hidden", !wrap.classList.contains("open"));
    const badge = who.querySelector(".guard-badge");
    if (badge) badge.addEventListener("click", observer);
  }
  return wrap;
}

/* --------------------------------------------------------------- sending */
function setBusy(busy) {
  ui.send.disabled = busy;
  ui.send.textContent = busy ? "waiting…" : "send";
}

function autoGrow() {
  ui.input.style.height = "auto";
  ui.input.style.height = Math.min(170, Math.max(44, ui.input.scrollHeight)) + "px";
}

async function send() {
  if (state.sending) return;
  const text = ui.input.value.trim();
  if (!text) return;
  const conv = activeConv() || newConversation({ silent: true });
  if (!conv.messages.length) conv.title = text.slice(0, 44) + (text.length > 44 ? "…" : "");

  const userMsg = { role: "user", content: text, ts: Date.now() };
  conv.messages.push(userMsg);
  ui.input.value = "";
  autoGrow();
  state.sending = true;
  setBusy(true);
  renderAll();

  const apiMessages = [];
  for (const m of conv.messages) {
    if (m.role === "user" && !m.blocked) apiMessages.push({ role: "user", content: m.content });
    else if (m.role === "assistant") apiMessages.push({ role: "assistant", content: m.content });
  }

  try {
    const res = await fetch("/v1/chat/completions", {
      method: "POST",
      headers: { "Content-Type": "application/json", "X-Guard-Mode": state.mode },
      body: JSON.stringify({ messages: apiMessages }),
    });
    let data = null;
    try { data = JSON.parse(await res.text()); } catch { /* non-JSON upstream */ }
    const headerVerdict = res.headers.get("X-Guard-Verdict");
    let compact = null;
    if (headerVerdict) { try { compact = JSON.parse(headerVerdict); } catch { /* ignore */ } }

    if (res.status === 403) {
      const verdict = (data && data.guard) || compact;
      userMsg.blocked = true;
      if (verdict) userMsg.verdict = verdict;
      conv.messages.push({ role: "guard", verdict, ts: Date.now() });
    } else if (res.ok && data && data.choices) {
      if (compact) userMsg.verdict = compact;
      conv.messages.push({
        role: "assistant",
        content: data.choices[0]?.message?.content || "(empty response)",
        ts: Date.now(),
      });
    } else {
      if (compact) userMsg.verdict = compact;
      const message = (data && data.error && data.error.message) || res.statusText || "request failed";
      conv.messages.push({ role: "error", content: `${res.status}: ${message}`, ts: Date.now() });
    }
  } catch (err) {
    conv.messages.push({ role: "error", content: "network error — " + err.message, ts: Date.now() });
  } finally {
    state.sending = false;
    setBusy(false);
    save();
    renderAll();
    refreshLog();
    refreshHealth();
  }
}

/* ------------------------------------------------------------ guard log */
async function refreshLog() {
  try {
    const res = await fetch("/api/decisions?limit=60");
    const data = await res.json();
    const decisions = data.decisions || [];
    renderLog(decisions);
  } catch { /* server restarting */ }
}

function renderLog(decisions) {
  ui.guardLog.replaceChildren();
  if (!decisions.length) {
    ui.guardLog.append(el("div", "log-empty", "No decisions logged yet."));
    ui.logCounts.textContent = "";
    return;
  }
  const labelOf = (d) => d.label || (d.decision === "block" ? (d.enforced ? "block" : "would-block") : "allow");
  const ordered = [...decisions].reverse();
  const blocked = ordered.filter((d) => labelOf(d) === "block").length;
  const flagged = ordered.filter((d) => labelOf(d) === "would-block").length;
  ui.logCounts.textContent = `${ordered.length} checked · ${blocked} blocked${flagged ? ` · ${flagged} flagged` : ""}`;

  for (const d of ordered) {
    const entry = el("div", "log-entry");
    const line1 = el("div", "line1");
    const time = (d.iso || "").split("T")[1] || "";
    line1.append(el("span", "time", time), el("span", "tag " + labelOf(d), labelOf(d)));
    const probs = d.probabilities || {};
    const p = Math.max(...Object.values(probs), 0);
    if (Object.keys(probs).length) line1.append(el("span", "meta", `p ${Number(p).toFixed(2)}`));
    if (d.latency_ms != null) line1.append(el("span", "meta", `${Number(d.latency_ms).toFixed(0)} ms`));
    entry.append(line1);
    if (d.excerpt) entry.append(el("div", "excerpt", d.excerpt));
    ui.guardLog.append(entry);
  }
}

/* --------------------------------------------------------------- health */
async function refreshHealth() {
  try {
    const res = await fetch("/health");
    const h = await res.json();
    state.keySet = !!h.key_set;
    ui.status.replaceChildren();
    ui.status.append(
      el("b", null, h.upstream_model),
      document.createTextNode(" via " + String(h.upstream).replace(/^https?:\/\//, "")),
      document.createElement("br"),
      document.createTextNode(`guard ${h.mode} · threshold ${h.threshold} · ${h.device}`),
    );
    if (!h.key_set) {
      ui.banner.replaceChildren();
      ui.banner.append(
        el("b", null, h.key_env + " is not set on the server."),
        document.createTextNode(" Clean requests cannot be answered — blocked requests are still stopped. Restart with: export " + h.key_env + "=…"),
      );
      ui.banner.classList.remove("hidden");
    } else {
      ui.banner.classList.add("hidden");
    }
  } catch {
    ui.status.textContent = "server unreachable";
  }
}

/* ------------------------------------------------------------------ init */
ui.send.onclick = send;
ui.newChat.onclick = () => newConversation();
ui.modeBlock.onclick = () => { state.mode = "block"; save(); renderMode(); };
ui.modeMonitor.onclick = () => { state.mode = "monitor"; save(); renderMode(); toast("Monitor mode: injections are logged but forwarded"); };
ui.input.addEventListener("input", autoGrow);
ui.input.addEventListener("keydown", (e) => {
  if (e.key === "Enter" && !e.shiftKey) {
    e.preventDefault();
    send();
  }
});

load();
renderAll();
autoGrow();
refreshHealth();
refreshLog();
setInterval(refreshLog, 5000);
setInterval(refreshHealth, 20000);
