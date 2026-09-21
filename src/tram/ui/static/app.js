/* Tram 薄版 UI - 无构建 vanilla JS。数据全部来自只读 API；任何修改都回 CLI。 */
"use strict";

const PHASES = [
  { id: "initiating", label: "启动", css: "var(--st-init)", x: 110 },
  { id: "planning", label: "规划", css: "var(--st-plan)", x: 300 },
  { id: "executing", label: "执行", css: "var(--st-exec)", x: 490 },
  { id: "monitoring", label: "监控", css: "var(--st-mon)", x: 690 },
  { id: "closing", label: "收尾", css: "var(--st-close)", x: 840 },
];
const GATES = [
  { id: "g0_charter_gate", label: "G0", x: 205 },
  { id: "g1_planning_gate", label: "G1", x: 395 },
  { id: "g2_quality_gate", label: "G2", x: 590 },
  { id: "g3_closing_gate", label: "G3", x: 765 },
];
const TRACK_Y = 150;
const PHASE_X = Object.fromEntries(PHASES.map((p) => [p.id, p.x]));
PHASE_X.done = 870;

const SVG_NS = "http://www.w3.org/2000/svg";
const svg = document.getElementById("route-map");
let tramEl;
const signalEls = {};

function el(name, attrs = {}, parent = svg) {
  const node = document.createElementNS(SVG_NS, name);
  for (const [k, v] of Object.entries(attrs)) node.setAttribute(k, v);
  parent.appendChild(node);
  return node;
}

function buildMap() {
  // 支线（CR 绕行道岔）
  el("path", {
    class: "branch",
    d: `M ${PHASE_X.executing} ${TRACK_Y} C 560 60, 640 60, ${PHASE_X.monitoring} ${TRACK_Y}`,
    id: "branch",
  });
  el("text", { class: "branch-label", x: 600, y: 58, id: "branch-label" }).textContent = "";
  el("circle", { cx: 600, cy: 74, r: 0, fill: "var(--pending)", id: "branch-bubble" });

  // 主线轨道
  el("path", { class: "track", d: `M 40 ${TRACK_Y} H 890` });

  // 车站
  for (const s of PHASES) {
    el("circle", { cx: s.x, cy: TRACK_Y, r: 18, fill: s.css, stroke: "var(--card)", "stroke-width": 4 });
    el("text", { class: "station-label", x: s.x, y: TRACK_Y + 44 }).textContent = s.label;
  }

  // 信号灯（阶段门）
  for (const g of GATES) {
    const group = el("g", { class: "signal", id: `signal-${g.id}` });
    el("line", { class: "signal-stem", x1: g.x, y1: TRACK_Y - 20, x2: g.x, y2: TRACK_Y - 40 }, group);
    el("circle", { cx: g.x, cy: TRACK_Y - 50, r: 9 }, group);
    el("text", { class: "gate-label", x: g.x, y: TRACK_Y - 66 }, group).textContent = g.label;
    signalEls[g.id] = group;
  }

  // Trammy 小电车
  tramEl = el("g", { class: "tram", id: "tram" });
  const tram = (n, a) => el(n, a, tramEl);
  tram("rect", { class: "tram-body", x: -48, y: -46, width: 96, height: 40, rx: 13 });
  tram("rect", { class: "tram-window", x: -36, y: -38, width: 20, height: 15, rx: 4 });
  tram("rect", { class: "tram-window", x: -10, y: -38, width: 20, height: 15, rx: 4 });
  tram("rect", { class: "tram-window", x: 16, y: -38, width: 18, height: 15, rx: 4 });
  tram("circle", { class: "tram-wheel", cx: -26, cy: -4, r: 8 });
  tram("circle", { class: "tram-wheel", cx: 26, cy: -4, r: 8 });
  const face = tram("g", { class: "tram-face" });
  el("circle", { cx: 32, cy: -26, r: 2.4 }, face);
  el("circle", { cx: 42, cy: -26, r: 2.4 }, face);
  el("path", { d: "M32 -19 q5 5 10 0", fill: "none", "stroke-width": 2 }, face);
  tram("text", { class: "zzz", x: 54, y: -48 }).textContent = "zzz";
  moveTram("initiating");
}

function moveTram(phase) {
  const x = PHASE_X[phase] ?? PHASE_X.initiating;
  tramEl.setAttribute("transform", `translate(${x} ${TRACK_Y})`);
}

function setSignal(gateId, status) {
  const node = signalEls[gateId];
  if (!node) return;
  node.classList.remove("signal--ok", "signal--fail", "signal--blocked_pending_human");
  if (status !== "idle") node.classList.add(`signal--${status}`);
}

/* ---------- 渲染 ---------- */

function phaseLabel(id) {
  const p = PHASES.find((x) => x.id === id);
  return p ? p.label : id;
}

function renderState(state) {
  document.getElementById("project-name").textContent = `Tram · ${state.project_name}`;
  const chip = document.getElementById("phase-chip");
  const phase = PHASES.find((p) => p.id === state.phase);
  chip.textContent = `阶段：${phaseLabel(state.phase)}`;
  if (phase) chip.style.background = phase.css;

  const base = document.getElementById("baseline-chip");
  base.textContent = state.scope_approved ? `基线 v${state.baseline_version} 已批` : "基线待批";
  base.className = "chip " + (state.scope_approved ? "chip--ok" : "chip--pending");

  moveTram(state.phase);
  tramEl.classList.toggle("is-waiting", !!state.open_crs.length);
  tramEl.classList.toggle("is-blocked", state.tasks.blocked > 0);

  for (const g of GATES) setSignal(g.id, state.gate_status[g.id] || "idle");

  const bubble = document.getElementById("branch-bubble");
  const branchLabel = document.getElementById("branch-label");
  branchLabel.textContent = state.open_crs.length
    ? `${state.open_crs.length} 条 CR 绕行中`
    : "";
  bubble.setAttribute("r", state.open_crs.length ? 14 : 0);

  const gauges = document.getElementById("gauges");
  const t = state.tasks;
  gauges.innerHTML = `
    <div class="gauge"><div class="num">${t.done}/${t.total}</div><div class="lbl">任务完成</div></div>
    <div class="gauge"><div class="num">${t.points_done}/${t.points_total}</div><div class="lbl">故事点</div></div>
    <div class="gauge ${state.open_crs.length ? "chip--fail" : ""}"><div class="num">${state.open_crs.length}</div><div class="lbl">进行中 CR</div></div>
    <div class="gauge"><div class="num">${state.events_count}</div><div class="lbl">事件（黑匣子）</div></div>
    <div class="gauge"><div class="num">${state.last_gate ? state.last_gate.id.replace("_", " ") : "—"}</div><div class="lbl">最近门禁</div></div>
    <div class="gauge gauge--dim"><div class="num">SPI·CPI</div><div class="lbl">EVM · Phase 2 供电</div></div>
  `;

  const list = document.getElementById("cr-list");
  list.innerHTML = "";
  if (!state.open_crs.length) {
    list.innerHTML = '<li class="empty">主线畅通，没有绕行支线 🛤</li>';
  } else {
    for (const cr of state.open_crs) {
      const li = document.createElement("li");
      li.className = "cr-item";
      const chipEl = document.createElement("span");
      chipEl.className = "chip chip--pending";
      chipEl.textContent = `${cr.id} · ${cr.type}`;
      const code = document.createElement("code");
      code.textContent = cr.paths.join(", ");
      li.append(chipEl, code);
      list.appendChild(li);
    }
  }
}

function renderTickets(artifacts) {
  const wrap = document.getElementById("tickets");
  wrap.innerHTML = "";
  if (!artifacts.length) {
    wrap.innerHTML = '<span style="color:var(--ink-soft)">车票夹是空的 —— `tram artifact generate --all` 开票</span>';
    return;
  }
  for (const a of artifacts) {
    const card = document.createElement("div");
    card.className = "ticket" + (a.verified ? " ticket--verified" : a.has_evidence ? "" : " ticket--gap");
    const holes = "●".repeat(a.evidence_verified) + "◐".repeat(a.evidence_total - a.evidence_verified) || "○";
    card.innerHTML = `
      <div class="kind">🎫 ${a.kind}</div>
      <div class="path">${a.path}</div>
      <div class="holes">${holes} <small>${a.evidence_verified}/${a.evidence_total}</small></div>
      ${a.verified ? '<span class="stamp">已检票</span>' : '<div class="gap-note">证据未闭环</div>'}
    `;
    wrap.appendChild(card);
  }
}

function prependEvent(ev) {
  const list = document.getElementById("events");
  const li = document.createElement("li");
  const refs = Object.entries(ev.refs || {})
    .map(([k, v]) => `${k}=${String(v).slice(0, 10)}`)
    .join(" ");
  li.innerHTML = `<span class="seq">#${ev.seq}</span><span class="kind">${ev.kind}</span><span class="refs">${refs}</span>`;
  list.prepend(li);
  while (list.children.length > 60) list.removeChild(list.lastChild);
}

async function refresh() {
  try {
    const [state, artifacts] = await Promise.all([
      fetch("/api/state").then((r) => r.json()),
      fetch("/api/artifacts").then((r) => r.json()),
    ]);
    renderState(state);
    renderTickets(artifacts);
  } catch (err) {
    console.error("refresh failed", err);
  }
}

async function boot() {
  buildMap();
  const events = await fetch("/api/events?limit=60").then((r) => r.json());
  for (const ev of events.reverse()) prependEvent(ev);
  await refresh();

  const es = new EventSource("/api/stream");
  es.onmessage = (msg) => {
    const ev = JSON.parse(msg.data);
    prependEvent(ev);
    if (ev.refresh) refresh();
  };
  es.onerror = () => setTimeout(refresh, 3000); // 断线自愈
}

boot();
