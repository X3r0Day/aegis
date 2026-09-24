/* Aegis dashboard: overview panels and ECharts options. */
"use strict";

function hexA(hex, alpha) {
  const value = hex.replace("#", "");
  const r = parseInt(value.slice(0, 2), 16);
  const g = parseInt(value.slice(2, 4), 16);
  const b = parseInt(value.slice(4, 6), 16);
  return `rgba(${r}, ${g}, ${b}, ${alpha})`;
}

function grad(color, topAlpha, bottomAlpha) {
  return new echarts.graphic.LinearGradient(0, 0, 0, 1, [
    { offset: 0, color: hexA(color, topAlpha) },
    { offset: 1, color: hexA(color, bottomAlpha) },
  ]);
}

/* Tooltips and the dashed crosshair carry every detail, charts draw no hover dots. */
function tooltipStyle() {
  const base = {
    trigger: "axis",
    backgroundColor: "rgba(15, 16, 19, 0.97)",
    borderColor: "#343b43",
    borderWidth: 1,
    padding: [10, 13],
    textStyle: { color: "#d8d8e4", fontSize: 14.5, fontWeight: 600, fontFamily: FONT },
    axisPointer: {
      type: "line",
      lineStyle: { color: "rgba(216, 216, 228, 0.45)", width: 1.5, type: "dashed" },
      label: { show: false },
    },
    extraCssText: "box-shadow: 0 16px 40px rgba(0,0,0,0.62); border-radius: 6px;",
  };
  base.formatter = (params) => {
    if (!Array.isArray(params) || !params.length) return "";
    const rows = [
      `<div style="color:#70737f;font-family:${MONO};font-size:13px;font-weight:600;letter-spacing:0.02em;margin-bottom:7px">${params[0].axisValue}</div>`,
    ];
    for (const item of params) {
      rows.push(
        `<div style="display:flex;align-items:center;gap:8px">${item.marker}` +
        `<span style="color:#9a9dab;font-weight:600">${item.seriesName}</span>` +
        `<span style="margin-left:auto;padding-left:26px;font-family:${MONO};font-weight:700;color:#d8d8e4">${(Number(item.value) || 0).toLocaleString("en-US")}</span></div>`
      );
    }
    return rows.join("");
  };
  return base;
}

function stackedTooltip() {
  const base = tooltipStyle();
  const rows = base.formatter;
  base.formatter = (params) => {
    if (!Array.isArray(params) || !params.length) return "";
    let total = 0;
    for (const item of params) total += Number(item.value) || 0;
    return rows(params) +
      `<div style="display:flex;align-items:center;margin-top:7px;border-top:1px solid #343b43;padding-top:7px">` +
      `<span style="color:#9a9dab;font-weight:700">total</span>` +
      `<span style="margin-left:auto;padding-left:26px;font-family:${MONO};font-weight:700;color:#d8d8e4">${total.toLocaleString("en-US")}</span></div>`;
  };
  return base;
}

function axisStyle(labels, every) {
  return {
    type: "category",
    data: labels,
    boundaryGap: false,
    axisLine: { lineStyle: { color: "#343b43", width: 1.5 } },
    axisTick: { show: false },
    axisLabel: {
      color: "#9a9dab", fontSize: 15, fontWeight: 700, fontFamily: FONT,
      interval: Math.max(0, (every || 6) - 1), hideOverlap: true,
    },
  };
}

function valueAxis() {
  return {
    type: "value",
    minInterval: 1,
    splitLine: { lineStyle: { color: "#262b31", type: "dashed" } },
    axisLabel: { color: "#9a9dab", fontSize: 14.5, fontWeight: 700, fontFamily: MONO },
  };
}

function seriesChartOption(cfg) {
  const xAxis = Object.assign(axisStyle(cfg.labels, cfg.every), cfg.bar ? { boundaryGap: true } : {});
  return {
    animationDuration: cfg.animate ? 900 : 350,
    animationDurationUpdate: 500,
    animationEasing: "cubicOut",
    animationEasingUpdate: "cubicOut",
    textStyle: { fontFamily: FONT },
    grid: { left: 54, right: 22, top: 24, bottom: 50 },
    legend: {
      bottom: 2, left: "center", icon: "roundRect",
      itemWidth: 20, itemHeight: 6, itemGap: 26,
      textStyle: { color: "#9a9dab", fontSize: 15, fontWeight: 700, fontFamily: FONT },
      inactiveColor: "#4d525b",
    },
    tooltip: cfg.stack ? stackedTooltip() : tooltipStyle(),
    xAxis,
    yAxis: valueAxis(),
    series: cfg.series.map((s) => ({
      name: s.name,
      type: cfg.bar ? "bar" : "line",
      stack: cfg.stack ? "total" : undefined,
      data: s.values,
      showSymbol: false,
      symbol: "none",
      lineStyle: cfg.stack && !cfg.bar
        ? { width: 0, opacity: 0 }
        : { width: 3, color: s.color, shadowBlur: 12, shadowColor: hexA(s.color, 0.45) },
      itemStyle: cfg.bar
        ? { color: s.color, borderRadius: [4, 4, 0, 0], shadowBlur: 8, shadowColor: hexA(s.color, 0.3) }
        : { color: s.color },
      areaStyle: s.area ? { color: grad(s.color, 0.52, 0.03) } : undefined,
      emphasis: cfg.stack && !cfg.bar
        ? { disabled: true }
        : { focus: "series", scale: false },
      barWidth: cfg.bar ? "62%" : undefined,
      showBackground: cfg.bar && cfg.background ? true : undefined,
      backgroundStyle: cfg.bar && cfg.background ? { color: "#1f2328", borderRadius: [4, 4, 0, 0] } : undefined,
    })),
  };
}

function gaugeOption(cfg, animate) {
  const frac = cfg.max ? Math.max(0, Math.min(1, cfg.value / cfg.max)) : 0;
  const color = frac >= 0.8 ? COLORS.red : frac >= 0.5 ? COLORS.yellow : COLORS.green;
  return {
    animationDuration: animate ? 1100 : 400,
    animationDurationUpdate: 500,
    textStyle: { fontFamily: FONT },
    series: [{
      type: "gauge",
      startAngle: 225,
      endAngle: -45,
      min: 0,
      max: cfg.max,
      radius: "88%",
      center: ["50%", "58%"],
      axisLine: {
        lineStyle: {
          width: 14,
          color: [
            [0.5, hexA(COLORS.green, 0.45)],
            [0.8, hexA(COLORS.yellow, 0.45)],
            [1, hexA(COLORS.red, 0.45)],
          ],
          shadowBlur: 6,
          shadowColor: "rgba(0, 0, 0, 0.4)",
        },
      },
      progress: {
        show: true, width: 14, roundCap: true,
        itemStyle: { color, shadowBlur: 18, shadowColor: hexA(color, 0.55) },
      },
      pointer: { show: false },
      axisTick: { show: false },
      splitLine: { show: false },
      axisLabel: { show: false },
      anchor: { show: false },
      title: {
        show: cfg.unit !== "%",
        offsetCenter: [0, "62%"], color: "#9a9dab", fontSize: 12.5, fontWeight: 700, fontFamily: FONT,
      },
      detail: {
        valueAnimation: true,
        offsetCenter: [0, "-4%"],
        fontSize: 18, fontWeight: 700, color: "#d8d8e4", fontFamily: MONO,
        formatter: (value) => {
          const number = cfg.decimals ? Number(value).toFixed(cfg.decimals) : String(Math.round(value));
          return cfg.unit === "%" ? `{n|${number}}{u|%}` : `{n|${number}}`;
        },
        rich: {
          n: {
            fontSize: 18, fontWeight: 700, color: "#d8d8e4", fontFamily: MONO, lineHeight: 22,
            textShadowBlur: 16, textShadowColor: hexA(color, 0.55),
          },
          u: { fontSize: 11, fontWeight: 700, color: "#9a9dab", fontFamily: MONO, padding: [0, 0, 0, 3] },
        },
      },
      data: [{ value: cfg.value, name: cfg.unit }],
    }],
  };
}

function sparkOption(values, color, animate) {
  return {
    animationDuration: animate ? 900 : 350,
    animationDurationUpdate: 450,
    animationEasing: "cubicOut",
    grid: { left: 0, right: 0, top: 4, bottom: 0 },
    xAxis: { type: "category", show: false, boundaryGap: false, data: values.map((_, i) => i) },
    yAxis: { type: "value", show: false, min: 0 },
    series: [{
      type: "line", data: values, showSymbol: false, symbol: "none", silent: true,
      lineStyle: { width: 2.8, color, shadowBlur: 10, shadowColor: hexA(color, 0.5) },
      areaStyle: { color: grad(color, 0.5, 0) },
    }],
  };
}

function categoriesOption(categories, animate) {
  return {
    animationDuration: animate ? 800 : 350,
    animationDurationUpdate: 500,
    animationEasing: "cubicOut",
    textStyle: { fontFamily: FONT },
    grid: { left: 10, right: 10, top: 58, bottom: 38 },
    tooltip: { show: false },
    xAxis: {
      type: "category",
      data: categories.map((item) => item.category),
      axisTick: { show: false },
      axisLine: { lineStyle: { color: "#343b43", width: 1.5 } },
      axisLabel: {
        color: "#9a9dab", fontSize: 15, fontWeight: 700, fontFamily: FONT,
        interval: 0, width: 120, overflow: "truncate",
      },
    },
    yAxis: { type: "value", show: false },
    series: [{
      type: "bar",
      barWidth: "64%",
      showBackground: true,
      backgroundStyle: { color: "#1f2328", borderRadius: [5, 5, 0, 0] },
      label: { show: true, position: "top", distance: 8, fontSize: 27, fontWeight: 700, fontFamily: MONO },
      data: categories.map((item, i) => {
        const color = CATEGORY_PALETTE[i % CATEGORY_PALETTE.length];
        return {
          value: item.n,
          label: { color, textShadowBlur: 12, textShadowColor: hexA(color, 0.45) },
          itemStyle: {
            color: grad(color, 1, 0.45),
            borderRadius: [5, 5, 0, 0],
            shadowBlur: 10,
            shadowColor: hexA(color, 0.35),
            shadowOffsetY: 2,
          },
        };
      }),
    }],
  };
}

function initChart(container) {
  const chart = echarts.init(container, null, { renderer: "canvas" });
  if (!state.overview.observer) {
    state.overview.observer = new ResizeObserver((entries) => {
      for (const entry of entries) {
        const instance = entry.target.__chart;
        if (instance) {
          try { instance.resize(); } catch { /* disposed */ }
        }
      }
    });
  }
  container.__chart = chart;
  state.overview.observer.observe(container);
  container.addEventListener("mouseenter", () => { state.hovering = true; });
  container.addEventListener("mouseleave", () => { state.hovering = false; });
  return chart;
}

function teardownOverview() {
  for (const chart of Object.values(state.overview.charts)) {
    try { chart.dispose(); } catch { /* already gone */ }
  }
  if (state.overview.observer) {
    state.overview.observer.disconnect();
    state.overview.observer = null;
  }
  state.overview.charts = {};
  state.overview.stats = {};
  state.overview.built = false;
  state.overview.feedBody = null;
  state.overview.blockedBody = null;
}

function statPanel(title, key, opts) {
  const p = panel(title, "");
  p.body.classList.add("stat-body");
  const stat = el("div", "stat");
  const value = el("div", "v");
  value.style.fontFamily = MONO;
  value.style.fontSize = "36px";
  value.style.fontWeight = "700";
  value.style.lineHeight = "1.05";
  value.style.letterSpacing = "-1.4px";
  if (opts.color) {
    value.style.color = opts.color;
    value.style.textShadow = "0 0 30px " + hexA(opts.color, 0.4);
  }
  countUp(value, opts.value, opts.animate !== false);
  stat.append(value);
  const sub = el("div", "sub", opts.sub);
  stat.append(sub);
  p.body.append(stat);
  const sparkEl = el("div", "echart spark");
  p.body.append(sparkEl);
  state.overview.stats[key] = { node: value, sub, valueText: opts.value, sparkEl, spark: opts.spark };
  return p;
}

async function loadOverview(animate = true) {
  try {
    const [overview, clients] = await Promise.all([
      api("/console/api/overview"),
      api("/console/api/clients?top=50"),
    ]);
    const list = clients.clients || [];
    if (!animate && (state.hovering || JSON.stringify([overview, list]) === state.lastOverview)) return;
    state.lastOverview = JSON.stringify([overview, list]);
    if (!state.overview.built || animate) {
      teardownOverview();
      buildOverview(overview, list, animate);
    } else {
      updateOverview(overview, list);
    }
  } catch (err) { toast(err.message); }
}

function overviewParts(data) {
  return {
    series: data.series || [],
    minutes: data.minute_series || [],
    day: data.last_24h || {},
    totals: data.totals || {},
  };
}

function buildOverview(data, clients, animate) {
  const view = $("#view-overview");
  view.replaceChildren();
  const { series, minutes, day, totals } = overviewParts(data);

  const guardPanel = panel("guard · last hour", "span-4");
  guardPanel.body.classList.add("body-chart");
  const guardEl = el("div", "echart");
  guardPanel.body.append(guardEl);
  view.append(guardPanel.section);

  const abusePanel = panel("abuse · last hour", "span-4");
  abusePanel.body.classList.add("body-chart");
  const abuseEl = el("div", "echart");
  abusePanel.body.append(abuseEl);
  view.append(abusePanel.section);

  const cluster = el("div", "cluster");

  const blockSpark = series.map((b) => (b.guard_blocked || 0) + (b.abuse_blocked || 0));
  cluster.append(statPanel("clients blocked now", "sBlocked", {
    value: fmtCount(data.blocked_now),
    color: data.blocked_now > 0 ? COLORS.red : COLORS.green,
    sub: fmtCount(day.blocks) + " blocks in 24h",
    spark: { values: blockSpark, color: COLORS.red },
    animate,
  }).section);

  const ratePanel = panel("block rate · 24h", "");
  ratePanel.body.classList.add("body-chart");
  const rateEl = el("div", "echart");
  ratePanel.body.append(rateEl);
  cluster.append(ratePanel.section);

  cluster.append(statPanel("guarded · 24 hours", "sGuard", {
    value: fmtCount(day.guard_total),
    color: COLORS.blue,
    sub: "all time " + fmtCount(totals.guard_total),
    spark: { values: series.map((b) => b.guard_total), color: COLORS.blue },
    animate,
  }).section);

  cluster.append(statPanel("abuse verdicts · 24 hours", "sAbuse", {
    value: fmtCount(day.abuse_total),
    color: COLORS.purple,
    sub: "all time " + fmtCount(totals.abuse_total),
    spark: { values: series.map((b) => b.abuse_total), color: COLORS.purple },
    animate,
  }).section);

  view.append(cluster);

  const requestsPanel = panel("requests · last 24 hours", "span-8");
  requestsPanel.body.classList.add("body-chart");
  const requestsEl = el("div", "echart");
  requestsPanel.body.append(requestsEl);
  view.append(requestsPanel.section);

  const categoryPanel = panel("abuse categories", "span-4");
  categoryPanel.body.classList.add("body-chart");
  const categoryEl = el("div", "echart");
  categoryPanel.body.append(categoryEl);
  view.append(categoryPanel.section);

  const blocksPanel = panel("blocks · last 24 hours", "span-12");
  blocksPanel.body.classList.add("body-chart");
  const blocksEl = el("div", "echart");
  blocksPanel.body.append(blocksEl);
  view.append(blocksPanel.section);

  const recentPanel = panel("recent activity", "span-8");
  recentPanel.body.classList.add("flush", "scroll");
  state.overview.feedBody = recentPanel.body;
  view.append(recentPanel.section);

  const blockedListPanel = panel("blocked clients", "span-4");
  blockedListPanel.body.classList.add("flush", "scroll");
  state.overview.blockedBody = blockedListPanel.body;
  view.append(blockedListPanel.section);

  renderFeed(data.recent || [], animate);
  renderBlockedClients(data.blocked_clients || {});
  stagger(view, animate);
  state.overview.built = true;

  const charts = state.overview.charts;
  requestAnimationFrame(() => requestAnimationFrame(() => {
    charts.guard = initChart(guardEl);
    charts.guard.setOption(seriesChartOption({
      labels: minutes.map((m) => fmtMinute(m.ts)),
      every: 10,
      animate,
      series: [
        { name: "requests", color: COLORS.blue, area: true, values: minutes.map((m) => m.guard_total) },
        { name: "blocked", color: COLORS.red, values: minutes.map((m) => m.guard_blocked) },
      ],
    }));

    charts.abuse = initChart(abuseEl);
    charts.abuse.setOption(seriesChartOption({
      labels: minutes.map((m) => fmtMinute(m.ts)),
      every: 10,
      animate,
      series: [
        { name: "verdicts", color: COLORS.purple, area: true, values: minutes.map((m) => m.abuse_total) },
        { name: "blocked", color: COLORS.orange, values: minutes.map((m) => m.abuse_blocked) },
      ],
    }));

    const blocksCount = (day.guard_blocked || 0) + (day.abuse_blocked || 0);
    const decisions = (day.guard_total || 0) + (day.abuse_total || 0);
    const rate = decisions ? (100 * blocksCount) / decisions : 0;
    charts.gRate = initChart(rateEl);
    charts.gRate.setOption(gaugeOption({
      value: rate,
      max: 100,
      unit: "%",
      decimals: 1,
    }, animate));

    Object.assign(charts, {
      requests: initChart(requestsEl),
      categories: initChart(categoryEl),
      blocks: initChart(blocksEl),
    });
    charts.requests.setOption(requestsOption(series, animate));
    charts.categories.setOption(categoriesOption(data.categories || [], animate));
    charts.blocks.setOption(blocksOption(series, animate));

    for (const key of ["sBlocked", "sGuard", "sAbuse"]) {
      const stat = state.overview.stats[key];
      if (!stat) continue;
      stat.chart = initChart(stat.sparkEl);
      stat.chart.setOption(sparkOption(stat.spark.values, stat.spark.color, animate));
    }
  }));
}

function requestsOption(series, animate) {
  return seriesChartOption({
    labels: series.map((b) => fmtHour(b.ts)),
    every: 4,
    animate,
    stack: true,
    series: [
      { name: "guard", color: COLORS.blue, area: true, values: series.map((b) => b.guard_total) },
      { name: "abuse", color: COLORS.purple, area: true, values: series.map((b) => b.abuse_total) },
    ],
  });
}

function blocksOption(series, animate) {
  return seriesChartOption({
    labels: series.map((b) => fmtHour(b.ts)),
    every: 4,
    animate,
    bar: true,
    stack: true,
    series: [
      { name: "injection", color: COLORS.red, values: series.map((b) => b.guard_blocked) },
      { name: "abuse", color: COLORS.orange, values: series.map((b) => b.abuse_blocked) },
    ],
  });
}

function updateStat(key, value, sub) {
  const stat = state.overview.stats[key];
  if (!stat) return;
  if (stat.valueText !== value) {
    stat.node.classList.remove("flash");
    void stat.node.offsetWidth;
    stat.node.classList.add("flash");
    countUp(stat.node, value, false);
  }
  stat.valueText = value;
  stat.sub.textContent = sub;
}

function updateOverview(data, clients) {
  const charts = state.overview.charts;
  if (!charts.guard) return;
  const { series, minutes, day, totals } = overviewParts(data);

  charts.guard.setOption(seriesChartOption({
    labels: minutes.map((m) => fmtMinute(m.ts)),
    every: 10,
    animate: false,
    series: [
      { name: "requests", color: COLORS.blue, area: true, values: minutes.map((m) => m.guard_total) },
      { name: "blocked", color: COLORS.red, values: minutes.map((m) => m.guard_blocked) },
    ],
  }));
  charts.abuse.setOption(seriesChartOption({
    labels: minutes.map((m) => fmtMinute(m.ts)),
    every: 10,
    animate: false,
    series: [
      { name: "verdicts", color: COLORS.purple, area: true, values: minutes.map((m) => m.abuse_total) },
      { name: "blocked", color: COLORS.orange, values: minutes.map((m) => m.abuse_blocked) },
    ],
  }));
  charts.requests.setOption(requestsOption(series, false));
  charts.blocks.setOption(blocksOption(series, false));
  charts.categories.setOption(categoriesOption(data.categories || [], false));

  const blockSpark = series.map((b) => (b.guard_blocked || 0) + (b.abuse_blocked || 0));
  updateStat("sBlocked", fmtCount(data.blocked_now), fmtCount(day.blocks) + " blocks in 24h");
  const sBlocked = state.overview.stats.sBlocked;
  if (sBlocked) {
    sBlocked.node.style.color = data.blocked_now > 0 ? COLORS.red : COLORS.green;
    if (sBlocked.chart) sBlocked.chart.setOption(sparkOption(blockSpark, COLORS.red, false));
  }
  const blocksCount = (day.guard_blocked || 0) + (day.abuse_blocked || 0);
  const decisions = (day.guard_total || 0) + (day.abuse_total || 0);
  const rate = decisions ? (100 * blocksCount) / decisions : 0;
  charts.gRate.setOption(gaugeOption({
    value: rate,
    max: 100,
    unit: "%",
    decimals: 1,
  }, false));

  updateStat("sGuard", fmtCount(day.guard_total), "all time " + fmtCount(totals.guard_total));
  updateStat("sAbuse", fmtCount(day.abuse_total), "all time " + fmtCount(totals.abuse_total));
  const sGuard = state.overview.stats.sGuard;
  const sAbuse = state.overview.stats.sAbuse;
  if (sGuard && sGuard.chart) sGuard.chart.setOption(sparkOption(series.map((b) => b.guard_total), COLORS.blue, false));
  if (sAbuse && sAbuse.chart) sAbuse.chart.setOption(sparkOption(series.map((b) => b.abuse_total), COLORS.purple, false));

  renderFeed(data.recent || [], false);
  renderBlockedClients(data.blocked_clients || {});
}

function feedKey(entry) {
  return entry.type + "|" + entry.ts + "|" + (entry.label || "") + "|"
    + (entry.client || entry.note || "");
}

function feedItem(entry, animate, fresh) {
  const item = el("div", "feed-item lbl-" + (entry.label || "allow"));
  if (fresh) item.classList.add("fresh");
  if (animate) {
    item.style.animation = "feed-in .35s ease both";
  }
  item.append(el("span", "time", fmtClock(entry.ts)));
  item.append(labelChip(entry.label));
  item.append(el("span", "type-tag", entry.type));
  let text = "";
  if (entry.type === "guard") text = entry.note || "prompt check";
  else text = (entry.client || "") + " · " + (entry.category || "") + " · " + Number(entry.abuse_score || 0).toFixed(2);
  item.append(el("span", "what", text));
  return item;
}

function renderFeed(recent, animate) {
  const body = state.overview.feedBody;
  if (!body) return;
  body.replaceChildren();
  if (!recent.length) {
    body.append(el("div", "hint", "Nothing yet. Point an app at /v1 or run a bench scenario."));
    return;
  }
  for (const entry of recent) {
    const fresh = !animate && !state.seenFeed.has(feedKey(entry));
    body.append(feedItem(entry, animate, fresh));
  }
  for (const entry of recent) state.seenFeed.add(feedKey(entry));
  if (state.seenFeed.size > 400) state.seenFeed = new Set([...state.seenFeed].slice(-200));
}

function renderBlockedClients(blockedMap) {
  const body = state.overview.blockedBody;
  if (!body) return;
  body.replaceChildren();
  const entries = Object.entries(blockedMap || {});
  if (!entries.length) {
    body.append(el("div", "hint", "No client is blocked right now."));
    return;
  }
  entries.sort((a, b) => (b[1].remaining_s || 0) - (a[1].remaining_s || 0));
  const table = el("table", "table");
  const thead = el("thead");
  const head = el("tr");
  for (const name of ["client", "category", "remaining", ""]) head.append(el("th", null, name));
  thead.append(head);
  const tbody = el("tbody");
  for (const [client, info] of entries) {
    const row = el("tr");
    row.append(el("td", "mono", client));
    row.append(el("td", null, info.category || "blocked"));
    row.append(el("td", "num", Math.round(info.remaining_s || 0) + "s"));
    const action = el("td");
    const button = el("button", "btn ghost", "unblock");
    button.type = "button";
    button.onclick = () => unblock(client);
    action.append(button);
    row.append(action);
    tbody.append(row);
  }
  table.append(thead, tbody);
  body.append(table);
}
