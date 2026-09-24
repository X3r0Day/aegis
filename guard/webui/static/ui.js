/* Aegis console shared helpers and state. */
"use strict";

const $ = (sel) => document.querySelector(sel);

const COLORS = {
  blue: "#5794f2",
  purple: "#b877d9",
  green: "#73bf69",
  yellow: "#f2cc0c",
  orange: "#ff9830",
  red: "#f2495c",
};
const CATEGORY_PALETTE = [COLORS.blue, COLORS.purple, COLORS.green, COLORS.orange, COLORS.red, COLORS.yellow];

const FONT = "'Space Grotesk', Inter, -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif";
const MONO = "'JetBrains Mono', ui-monospace, SFMono-Regular, Menlo, Consolas, monospace";

const state = {
  view: "overview",
  decisions: { kind: "guard", label: "", limit: 50, expanded: new Set() },
  settings: null,
  health: null,
  timer: null,
  lastOverview: "",
  lastData: {},
  hovering: false,
  seenFeed: new Set(),
  agentView: null,
  overview: { built: false, charts: {}, stats: {}, observer: null, feedBody: null, blockedBody: null },
};

function el(tag, className, text) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text !== undefined) node.textContent = text;
  return node;
}

function fmtClock(ts) {
  return new Date(ts * 1000).toTimeString().slice(0, 8);
}

function fmtHour(ts) {
  return new Date(ts * 1000).toTimeString().slice(0, 2) + ":00";
}

function fmtMinute(ts) {
  return new Date(ts * 1000).toTimeString().slice(0, 5);
}

function fmtAge(seconds) {
  if (seconds == null) return "never";
  if (seconds < 90) return Math.round(seconds) + "s";
  if (seconds < 5400) return Math.round(seconds / 60) + "m";
  if (seconds < 172800) return (seconds / 3600).toFixed(1) + "h";
  return (seconds / 86400).toFixed(1) + "d";
}

function fmtCount(n) {
  return (n == null ? 0 : n).toLocaleString("en-US");
}

let toastTimer = null;
function toast(message, ms = 3000) {
  const node = $("#toast");
  node.textContent = message;
  node.classList.remove("hidden");
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => node.classList.add("hidden"), ms);
}

function countUp(node, text, animate) {
  const match = String(text).match(/^([\d,]+(?:\.\d+)?)(.*)$/);
  if (!animate || !match) {
    node.textContent = text;
    return;
  }
  const target = parseFloat(match[1].replace(/,/g, ""));
  const suffix = match[2] || "";
  const decimals = (match[1].split(".")[1] || "").length;
  const start = performance.now();
  const duration = 750;
  const frame = (now) => {
    const p = Math.min(1, (now - start) / duration);
    const eased = 1 - Math.pow(1 - p, 3);
    const value = target * eased;
    node.textContent = value.toLocaleString("en-US", {
      minimumFractionDigits: decimals,
      maximumFractionDigits: decimals,
    }) + suffix;
    if (p < 1) requestAnimationFrame(frame);
  };
  requestAnimationFrame(frame);
}

function stagger(view, animate) {
  view.classList.remove("enter");
  if (!animate) return;
  [...view.children].forEach((child, i) => child.style.setProperty("--i", String(i)));
  void view.offsetWidth;
  view.classList.add("enter");
}

async function api(path, options = {}) {
  const res = await fetch(path, {
    headers: { "Content-Type": "application/json" },
    ...options,
  });
  if (res.status === 401) { location.href = "/login"; throw new Error("login required"); }
  if (res.status === 409) { location.href = "/setup"; throw new Error("setup required"); }
  if (!res.ok) {
    let detail = res.statusText;
    try {
      const data = await res.json();
      if (typeof data.detail === "string") detail = data.detail;
      else if (Array.isArray(data.detail)) detail = data.detail.map((d) => d.msg).join(", ");
    } catch { /* not json */ }
    throw new Error(detail);
  }
  return res.json();
}

function labelChip(label) {
  return el("span", "label-chip " + (label || "allow"), label || "allow");
}

function panel(title, span) {
  const section = el("section", "panel " + span);
  const head = el("div", "panel-head");
  head.append(el("span", null, title));
  const body = el("div", "panel-body");
  section.append(head, body);
  return { section, head, body };
}
