/* Tram 薄版 UI - 无构建 vanilla JS。数据来自只读 API；
   `tram ui --approve` 解锁写模式：调度台（/api/action）与站台审批（/api/approve）
   都派发到与 CLI 相同的服务层——每个按钮都长在铁轨上。 */
"use strict";

/* 双轨线路图：上半直线 = 项目主线（过程组 + 门禁 + 里程标 + Trammy）；
   下半环线 = 当前阶段的放大镜（该阶段的知识域子过程铺满一圈，环心是阶段名）。
   过程组每圈重复（渐进明细）——环线的方向动画一直在转。 */
const PHASES = [
  { id: "initiating", label: "启动", css: "var(--st-init)", x: 130 },
  { id: "planning", label: "规划", css: "var(--st-plan)", x: 300 },
  { id: "executing", label: "执行", css: "var(--st-exec)", x: 470 },
  { id: "monitoring", label: "监控", css: "var(--st-mon)", x: 640 },
  { id: "closing", label: "收尾", css: "var(--st-close)", x: 810 },
];
const GATES = [
  { id: "g0_charter_gate", label: "G0", x: 215 },
  { id: "g1_planning_gate", label: "G1", x: 385 },
  { id: "g2_quality_gate", label: "G2", x: 555 },
  { id: "g3_closing_gate", label: "G3", x: 725 },
];
const TRACK_Y = 110;
const PHASE_X = Object.fromEntries(PHASES.map((p) => [p.id, p.x]));
PHASE_X.done = 845;

const SVG_NS = "http://www.w3.org/2000/svg";
const svg = document.getElementById("route-map");
let tramEl;
let branchEl;
let loopPath;
let loopDetailEl;
const signalEls = {};
const stationEls = {};
let DOMAIN_DATA = null; // /api/domains 词表缓存（环线细节渲染用）

const reducedMotion = () => matchMedia("(prefers-reduced-motion: reduce)").matches;

/* 细节环线几何：绕环一周的分数位置 -> 坐标 / 外法线（标签往外长） */
const LOOP_D =
  "M 150 205 H 770 A 55 55 0 0 1 825 260 V 340 A 55 55 0 0 1 770 395 H 150 A 55 55 0 0 1 95 340 V 260 A 55 55 0 0 1 150 205 Z";
const LOOP_CENTER = { x: 460, y: 300 };

function pointAt(f) {
  const len = loopPath.getTotalLength();
  const t = (((f % 1) + 1) % 1) * len;
  return loopPath.getPointAtLength(t);
}

function tangentAt(f) {
  const a = pointAt(f - 0.004);
  const b = pointAt(f + 0.004);
  return Math.atan2(b.y - a.y, b.x - a.x);
}

function outwardAt(f) {
  const p = pointAt(f);
  const dx = p.x - LOOP_CENTER.x;
  const dy = p.y - LOOP_CENTER.y;
  const n = Math.hypot(dx, dy) || 1;
  return { x: dx / n, y: dy / n };
}

function el(name, attrs = {}, parent = svg) {
  const node = document.createElementNS(SVG_NS, name);
  for (const [k, v] of Object.entries(attrs)) node.setAttribute(k, v);
  parent.appendChild(node);
  return node;
}

function buildMap() {
  buildScenery();

  // ---------- 直线主线：过程组 + 门禁 + 里程标 ----------
  el("path", { class: "track", d: `M 60 ${TRACK_Y} H 860` });

  // 里程标 K0-K7：每段站间两根小立柱（与站、门都错开 40+px）
  let k = 0;
  for (let i = 0; i < PHASES.length - 1; i++) {
    for (const frac of [0.25, 0.75]) {
      const x = PHASES[i].x + frac * (PHASES[i + 1].x - PHASES[i].x);
      el("line", { class: "milestone", x1: x, y1: TRACK_Y + 14, x2: x, y2: TRACK_Y + 26 });
      el("text", { class: "milestone-text", x, y: TRACK_Y + 40, "text-anchor": "middle" }).textContent = `K${k}`;
      k += 1;
    }
  }

  // 车站（过程组）——分组 + 光环，当前阶段在直线上高亮
  for (const s of PHASES) {
    const g = el("g", { class: "station", "data-phase": s.id });
    el("circle", { class: "station-halo", cx: s.x, cy: TRACK_Y, r: 21, fill: "none" }, g);
    el("circle", { cx: s.x, cy: TRACK_Y, r: 15, fill: s.css, stroke: "var(--card)", "stroke-width": 4 }, g);
    el("text", { class: "station-label", x: s.x, y: TRACK_Y - 26, "text-anchor": "middle" }, g).textContent =
      s.label;
    stationEls[s.id] = g;
  }

  // 信号灯（阶段门）——线上方，点击重跑
  for (const g of GATES) {
    const group = el("g", { class: "signal", id: `signal-${g.id}`, "data-gate": g.id });
    group.setAttribute("data-tip", `${g.label} 门禁 —— 点击重跑（gate.run）`);
    el("line", { x1: g.x, y1: TRACK_Y + 8, x2: g.x, y2: TRACK_Y + 30 }, group);
    el("circle", { cx: g.x, cy: TRACK_Y + 40, r: 8.5 }, group);
    el("text", { class: "gate-label", x: g.x, y: TRACK_Y + 62, "text-anchor": "middle" }, group).textContent =
      g.label;
    signalEls[g.id] = group;
  }

  // 支线（CR 绕行道岔）：执行 → 监控 的线下浅弧
  branchEl = el("path", {
    class: "branch",
    d: `M ${PHASE_X.executing} ${TRACK_Y} Q 555 ${TRACK_Y + 78} ${PHASE_X.monitoring} ${TRACK_Y}`,
    id: "branch",
  });
  el("text", { class: "branch-label", x: 555, y: TRACK_Y + 52, "text-anchor": "middle", id: "branch-label" }).textContent = "";
  el("circle", { cx: 555, cy: TRACK_Y + 66, r: 0, fill: "var(--pending)", id: "branch-bubble" });

  // Trammy 小电车（外层管位移，内层管颠簸动画）——点电车 = 全线运行
  tramEl = el("g", { class: "tram", id: "tram" });
  tramEl.setAttribute("data-tip", "点 Trammy 全线运行（flow.run）——绿灯推进、红灯必停");
  const tramInner = el("g", { class: "tram-inner" }, tramEl);
  const tram = (n, a) => el(n, a, tramInner);
  tram("rect", { class: "tram-body", x: -34, y: -40, width: 68, height: 34, rx: 12 });
  tram("rect", { class: "tram-window", x: -25, y: -33, width: 14, height: 12, rx: 4 });
  tram("rect", { class: "tram-window", x: -7, y: -33, width: 14, height: 12, rx: 4 });
  tram("rect", { class: "tram-window", x: 11, y: -33, width: 13, height: 12, rx: 4 });
  tram("circle", { class: "tram-wheel", cx: -18, cy: -3, r: 7 });
  tram("circle", { class: "tram-wheel", cx: 18, cy: -3, r: 7 });
  const face = tram("g", { class: "tram-face" });
  el("circle", { cx: 22, cy: -23, r: 2.2 }, face);
  el("circle", { cx: 30, cy: -23, r: 2.2 }, face);
  el("path", { d: "M22 -17 q4 4 8 0", fill: "none", "stroke-width": 2 }, face);
  tram("text", { class: "zzz", x: 40, y: -42 }).textContent = "zzz";
  startTramOrbit();

  // ---------- 细节环线：当前阶段的放大镜（内容随 renderState 换装） ----------
  el("path", { class: "track loop-track", d: LOOP_D });
  el("path", { class: "track-flow", d: LOOP_D });
  loopPath = svg.querySelector(".loop-track");
  loopDetailEl = el("g", { id: "loop-detail" });

  // 动画收尾：一次性动画结束后摘掉类，方便下次重放
  tramEl.addEventListener("animationend", () => tramEl.classList.remove("is-depart", "is-cheer"));
  svg.addEventListener("animationend", (ev) => {
    const g = ev.target.closest(".signal");
    if (g) {
      g.classList.remove("signal--blip");
      return;
    }
    if (ev.target.classList && ev.target.classList.contains("confetti")) ev.target.remove();
    if (ev.target === branchEl) branchEl.classList.remove("branch--flow");
    const bubble = document.getElementById("branch-bubble");
    if (ev.target === bubble) bubble.classList.remove("bubble--pop");
  });

  // 线路图即调度台：点信号灯过门、点电车全线运行
  svg.addEventListener("click", (ev) => {
    const signal = ev.target.closest(".signal");
    if (signal) {
      callAction("gate.run", { gate: signal.dataset.gate });
      return;
    }
    if (ev.target.closest(".tram")) callAction("flow.run", {});
  });
}

/* 沿途风景：淡雅卡通背景（软云/远山/棒棒糖树/暖阳），永远垫底不挡操作 */
function buildScenery() {
  const g = el("g", { class: "scenery" });
  el("circle", { cx: 838, cy: 64, r: 24, class: "sun" }, g);
  cloud(140, 62, g);
  cloud(420, 46, g);
  cloud(664, 74, g);
  el(
    "path",
    {
      class: "hill hill--back",
      d: "M -20 468 Q 180 336 420 452 T 940 446 L 940 545 L -20 545 Z",
    },
    g
  );
  el(
    "path",
    {
      class: "hill hill--front",
      d: "M -20 502 Q 300 392 620 494 T 940 504 L 940 545 L -20 545 Z",
    },
    g
  );
  tree(76, 176, g);
  tree(874, 158, g);
  tree(44, 434, g);
  tree(890, 424, g);
}

function cloud(x, y, parent) {
  const g = el("g", { class: "cloud" }, parent);
  el("ellipse", { cx: 0, cy: 0, rx: 26, ry: 14 }, g);
  el("ellipse", { cx: 20, cy: 5, rx: 20, ry: 11 }, g);
  el("ellipse", { cx: -20, cy: 5, rx: 18, ry: 10 }, g);
  g.setAttribute("transform", `translate(${x} ${y})`);
}

function tree(x, y, parent) {
  const g = el("g", { class: "tree" }, parent);
  el("line", { x1: 0, y1: 0, x2: 0, y2: 22 }, g);
  el("circle", { cx: 0, cy: -8, r: 15 }, g);
  g.setAttribute("transform", `translate(${x} ${y})`);
}

/* 小火车行驶在环线上（当前阶段的放大镜里巡线），直线上改由光环高亮当前站 */
let tramF = 0;
function parkTramAt(f) {
  const p = pointAt(f);
  const n = outwardAt(f);
  tramEl.setAttribute("transform", `translate(${p.x + n.x * 22} ${p.y + n.y * 22 - 8})`);
}
function startTramOrbit() {
  if (reducedMotion()) {
    parkTramAt(0.02);
    return;
  }
  const speed = 1 / 26; // 一圈约 26 秒，悠闲巡线
  let last = performance.now();
  const step = (now) => {
    const dt = (now - last) / 1000;
    last = now;
    tramF = (tramF + dt * speed) % 1;
    parkTramAt(tramF);
    requestAnimationFrame(step);
  };
  requestAnimationFrame(step);
}
function moveTram(phase) {
  // 兼容旧调用点：阶段变化只改直线高亮，小火车一直在环线上
  highlightStation(phase);
}
function highlightStation(phaseId) {
  for (const [pid, node] of Object.entries(stationEls)) {
    node.classList.toggle("station--active", pid === phaseId);
  }
}

/* 细节环线换装：当前阶段的知识域子过程铺满一圈——每颗节点独享 ~300px 弧长，标签永不重叠 */
function renderLoopDetail(phaseId, iteration) {
  if (!loopDetailEl || !DOMAIN_DATA) return;
  loopDetailEl.innerHTML = "";
  const phase = PHASES.find((p) => p.id === phaseId) || PHASES[0];
  const procs = (DOMAIN_DATA.subprocesses || {})[phase.id] || [];

  // 环心：阶段名 + 第 N 圈
  el(
    "text",
    { class: "loop-phase", x: LOOP_CENTER.x, y: LOOP_CENTER.y - 6, "text-anchor": "middle", fill: phase.css },
    loopDetailEl
  ).textContent = `${phase.label}阶段`;
  el(
    "text",
    { class: "loop-lap", x: LOOP_CENTER.x, y: LOOP_CENTER.y + 22, "text-anchor": "middle" },
    loopDetailEl
  ).textContent = iteration > 1 ? `环线 · 第 ${iteration} 圈` : "知识域子过程";

  // 子过程节点均布整圈
  const n = Math.max(procs.length, 1);
  procs.forEach((p, i) => {
    const f = (i + 0.5) / n;
    const pt = pointAt(f);
    const o = outwardAt(f);
    const g = el("g", { class: "sub-node" }, loopDetailEl);
    g.setAttribute("data-tip", `${phase.label}组 · ${p.area}管理 · ${p.process}`);
    el("circle", { class: "sub-node-dot", cx: pt.x, cy: pt.y, r: 7 }, g);
    // 标签沿外法线长出，按方位定锚点（左右侧 start/end，上下 middle）
    const anchor = o.x > 0.35 ? "start" : o.x < -0.35 ? "end" : "middle";
    const lx = pt.x + o.x * 26;
    const ly = pt.y + o.y * 26 + 4;
    el("text", { class: "sub-node-badge", x: lx, y: ly, "text-anchor": anchor }, g).textContent = p.badge;
    const badgeShift = anchor === "end" ? -14 : anchor === "start" ? 14 : 0;
    el(
      "text",
      { class: "sub-node-label", x: lx + badgeShift, y: ly, "text-anchor": anchor },
      g
    ).textContent = p.process;
  });

  // 方向箭头（▸ 沿切线转角）
  for (const f of [0.12, 0.37, 0.62, 0.87]) {
    const p = pointAt(f);
    const o = outwardAt(f);
    el("text", {
      class: "track-arrow",
      x: p.x + o.x * 13,
      y: p.y + o.y * 13 + 4,
      transform: `rotate(${(tangentAt(f) * 180) / Math.PI} ${p.x + o.x * 13} ${p.y + o.y * 13})`,
    }).textContent = "▸";
  }
}

/* 十大知识域图例（环线下方一行扫全） */
function renderLegend() {
  if (!DOMAIN_DATA) return;
  const areas = DOMAIN_DATA.areas || [];
  areas.forEach((a, i) => {
    const g = el("g", { class: "ka-legend-item" });
    const x = 66 + i * ((920 - 110) / Math.max(areas.length - 1, 1));
    el("rect", { class: "ka-legend-badge", x: x - 8, y: 462, width: 17, height: 15, rx: 5 }, g);
    el("text", { class: "ka-legend-badge-text", x, y: 473, "text-anchor": "middle" }, g).textContent = a.badge;
    el("text", { class: "ka-legend-name", x: x + 14, y: 473 }, g).textContent = a.name;
  });
}

function setSignal(gateId, status) {
  const node = signalEls[gateId];
  if (!node) return;
  node.classList.remove("signal--ok", "signal--fail", "signal--blocked_pending_human");
  if (status !== "idle") node.classList.add(`signal--${status}`);
}

/* ---------- 渲染 ---------- */

function esc(s) {
  return String(s).replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
}

function phaseLabel(id) {
  const p = PHASES.find((x) => x.id === id);
  return p ? p.label : id;
}

// 变更检测：只在状态真的变了时放一次动画（寓意：到站颠一下、信号翻灯闪一下）
let prevPhase = null;
const prevGateStatus = {};
const prevTaskStatus = {};
let prevCrCount = null;
let celebrated = false;

function renderState(state) {
  document.getElementById("project-name").textContent = `Tram · ${state.project_name}`;
  const chip = document.getElementById("phase-chip");
  const phase = PHASES.find((p) => p.id === state.phase);
  const lap = state.iteration > 1 ? ` · 第${state.iteration}圈` : "";
  chip.textContent = `阶段：${phaseLabel(state.phase)}${lap}`;
  if (phase) chip.style.background = phase.css;

  const base = document.getElementById("baseline-chip");
  base.textContent = state.scope_approved ? `基线 v${state.baseline_version} 已批` : "基线待批";
  base.className = "chip " + (state.scope_approved ? "chip--ok" : "chip--pending");

  if (prevPhase !== null && prevPhase !== state.phase) {
    tramEl.classList.remove("is-depart");
    void tramEl.getBoundingClientRect(); // 强制 reflow，让动画可重放
    tramEl.classList.add("is-depart");
  }
  prevPhase = state.phase;

  moveTram(state.phase);
  renderLoopDetail(state.phase, state.iteration);
  tramEl.classList.toggle("is-waiting", !!state.open_crs.length);
  tramEl.classList.toggle("is-blocked", state.tasks.blocked > 0);

  for (const g of GATES) {
    const st = state.gate_status[g.id] || "idle";
    if (prevGateStatus[g.id] !== undefined && prevGateStatus[g.id] !== st && st !== "idle") {
      const node = signalEls[g.id];
      node.classList.remove("signal--blip");
      void node.getBoundingClientRect();
      node.classList.add("signal--blip");
    }
    prevGateStatus[g.id] = st;
    setSignal(g.id, st);
  }

  const bubble = document.getElementById("branch-bubble");
  const branchLabel = document.getElementById("branch-label");
  const crCount = state.open_crs.length;
  branchLabel.textContent = crCount ? `${crCount} 条 CR 绕行中` : "";
  bubble.setAttribute("r", crCount ? 14 : 0);
  // 岔道扳过去：新增 CR 的瞬间，虚线支线流一下、气泡弹一下（车走支线的意象）
  if (prevCrCount !== null && crCount > prevCrCount && !reducedMotion()) {
    branchEl.classList.remove("branch--flow");
    bubble.classList.remove("bubble--pop");
    void branchEl.getBoundingClientRect();
    branchEl.classList.add("branch--flow");
    bubble.classList.add("bubble--pop");
  }
  prevCrCount = crCount;

  // 终点站庆祝：收尾站 + 任务全清，Trammy 欢快一颠、车票彩带落一场（一次）
  const tk = state.tasks;
  const tasksDone = tk && tk.total > 0 && tk.done === tk.total;
  const celebrating = state.phase === "closing" && tasksDone;
  if (celebrating && !celebrated) celebrate();
  celebrated = celebrating;

  const gauges = document.getElementById("gauges");
  const t = state.tasks;
  const evm = state.evm;
  let evmGauge;
  if (evm) {
    const breaches = evm.breaches || [];
    const cls = breaches.length ? "chip--fail" : "chip--ok";
    const tip = breaches.length ? ` title="${esc(breaches.join("；"))}"` : "";
    const lbl = breaches.length ? `⚠ EVM 越界 · ${evm.date}` : `EVM 正常 · ${evm.date}`;
    evmGauge = `<div class="gauge ${cls}"${tip}><div class="num">SPI ${evm.spi} · CPI ${evm.cpi}</div><div class="lbl">${lbl}</div></div>`;
  } else {
    evmGauge = '<div class="gauge gauge--dim"><div class="num">SPI·CPI</div><div class="lbl">EVM · 等 `tram evm snapshot`</div></div>';
  }
  gauges.innerHTML = `
    <div class="gauge"><div class="num">${t.done}/${t.total}</div><div class="lbl">任务完成</div></div>
    <div class="gauge"><div class="num">${t.points_done}/${t.points_total}</div><div class="lbl">故事点</div></div>
    <div class="gauge ${state.open_crs.length ? "chip--fail" : ""}"><div class="num">${state.open_crs.length}</div><div class="lbl">进行中 CR</div></div>
    <div class="gauge"><div class="num">${state.events_count}</div><div class="lbl">事件（黑匣子）</div></div>
    <div class="gauge"><div class="num">${state.last_gate ? state.last_gate.id.replace("_", " ") : "—"}</div><div class="lbl">最近门禁</div></div>
    ${evmGauge}
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

  renderKpis(state.kpi);
  renderWeather(state.risks);
  renderPlatform(state);
  renderTasks(state.task_list || []);
}

/* ---------- 站台审批（只读列出待人工的事；--approve 时可就地署名放行） ---------- */

const uiConfig = { approvals_enabled: false, token: "" };

function renderPlatform(state) {
  const list = document.getElementById("platform");
  const items = [];
  if (!state.scope_approved) {
    items.push({ icon: "🚏", desc: "范围基线待批 —— G0 放行前提", cmd: "tram baseline approve --by <你>", act: { action: "baseline", label: "批准基线" } });
  }
  for (const [gateId, status] of Object.entries(state.gate_status)) {
    if (status === "blocked_pending_human") {
      if (gateId === "g3_closing_gate") {
        items.push({ icon: "🚦", desc: "门禁 g3 待 release 人工放行", cmd: "tram approve release --by <你>", act: { action: "release", label: "放行 release" } });
      } else {
        items.push({ icon: "🚦", desc: `门禁 ${gateId} 三次整改仍红，已升级待人审`, cmd: "tram gate run " + gateId + "  # 整改后重跑" });
      }
    }
  }
  for (const cr of state.open_crs) {
    const prTag = cr.pr ? ` · PR #${cr.pr}` : "";
    const prCmd = cr.pr ? `（或 tram cr sync ${cr.id} 按 PR review 裁决）` : "";
    items.push({ icon: "🔀", desc: `CR ${cr.id}（${cr.type}）绕行待审 · ${cr.paths.join(", ")}${prTag}`, cmd: `tram cr approve|reject ${cr.id} --by <你>${prCmd}`, act: { action: "cr", id: cr.id, label: "裁决 CR" } });
  }
  if (state.evm && state.evm.breaches && state.evm.breaches.length) {
    items.push({ icon: "🌧", desc: "EVM 越界已自动入险，需要纠偏决策", cmd: "tram evm show  # 看越界详情" });
  }
  if (state.storm_risks > 0) {
    items.push({
      icon: "⛈",
      desc: `${state.storm_risks} 项风暴级风险未决（P×I≥9）——去风险气象台裁决`,
      cmd: "tram risk resolve <R-id> --status watching|closed  # 或气象台按钮",
    });
  }
  const gaps = state.requirement_gaps;
  if (gaps && gaps.mode === "anchored" && gaps.unanchored > 0) {
    items.push({
      icon: "🧩",
      desc: `${gaps.unanchored} 个在途任务未锚定 WBS 工作包——渐进明细没跟上，规划站补对账`,
      cmd: "查看任务板 wbs 列 / 会话开锚定会话消化需求",
    });
  }
  // 角标：待人的事有几件，右轨标题上一眼可见
  const badge = document.getElementById("platform-badge");
  badge.hidden = !items.length;
  badge.textContent = items.length;

  if (!items.length) {
    list.innerHTML = '<li class="empty">站台空无一人 —— 没有在等你的事 ✅</li>';
    return;
  }
  list.innerHTML = items
    .map((it, i) => {
      const writable = uiConfig.approvals_enabled && it.act;
      const buttons = writable
        ? `<button class="platform-act" data-i="${i}">${esc(it.act.label)}</button>`
        : "";
      const action = it.act ? it.act.action : "";
      const crId = it.act && it.act.id ? it.act.id : "";
      return `
      <li class="platform-item" data-i="${i}" data-action="${action}" data-id="${crId}">
        <span class="platform-icon">${it.icon}</span>
        <span class="platform-desc">${esc(it.desc)}</span>
        <code class="platform-cmd">$ ${esc(it.cmd)}</code>
        ${buttons}
        <form class="platform-form" hidden data-i="${i}">
          <input name="by" placeholder="署名（谁批的）" required>
          <input name="note" placeholder="备注（可空）">
          ${action === "cr"
            ? `<button type="submit" class="act-approve" data-decision="approve">同意</button><button type="submit" class="act-reject" data-decision="reject">驳回</button>`
            : `<button type="submit">确认放行</button>`}
          <button type="button" class="act-cancel">取消</button>
        </form>
      </li>`;
    })
    .join("");
}

document.getElementById("platform").addEventListener("click", (ev) => {
  const act = ev.target.closest(".platform-act");
  if (act) {
    const item = act.closest(".platform-item");
    const form = item.querySelector(".platform-form");
    form.hidden = !form.hidden;
    if (!form.hidden) form.querySelector('[name="by"]').focus();
    return;
  }
  if (ev.target.closest(".act-cancel")) {
    const form = ev.target.closest(".platform-form");
    form.hidden = true;
    form.reset();
  }
});

document.getElementById("platform").addEventListener("submit", (ev) => {
  const form = ev.target.closest(".platform-form");
  if (!form) return;
  ev.preventDefault();
  submitApproval(form, ev.submitter ? ev.submitter.dataset.decision : "approve");
});

async function submitApproval(form, decision) {
  const item = form.closest(".platform-item");
  const idx = Number(form.dataset.i);
  const by = form.querySelector('[name="by"]').value.trim();
  const note = form.querySelector('[name="note"]').value.trim();
  if (!by) { toast("审批要署名 ✍️", true); return; }
  let action;
  if (item.dataset.action === "cr") {
    action = decision === "reject" ? "cr_reject" : "cr_approve";
  } else {
    action = item.dataset.action;
  }
  let payload;
  if (action === "cr_approve" || action === "cr_reject") {
    payload = { action, id: item.dataset.id, by, note };
  } else {
    payload = { action, by, note };
  }
  try {
    const res = await fetch("/api/approve", {
      method: "POST",
      headers: { "Content-Type": "application/json", "X-Tram-Token": uiConfig.token },
      body: JSON.stringify(payload),
    });
    const body = await res.json();
    toast(res.ok ? body.detail : (body.detail || "审批失败"), !res.ok);
    if (res.ok) { form.hidden = true; form.reset(); refresh(); }
  } catch (err) {
    toast("审批请求失败：" + err, true);
  }
}

/* 终点站庆祝：Trammy 欢快一颠 + 车票彩带落一场（一次性动画，结束自动清扫） */
function celebrate() {
  tramEl.classList.remove("is-depart", "is-cheer");
  void tramEl.getBoundingClientRect();
  tramEl.classList.add("is-depart", "is-cheer");
  if (reducedMotion()) return;
  const palette = PHASES.map((p) => p.css);
  for (let i = 0; i < 14; i++) {
    el("rect", {
      class: "confetti",
      x: Math.round(70 + Math.random() * 780),
      y: Math.round(48 + Math.random() * 30),
      width: 16,
      height: 9,
      rx: 2,
      fill: palette[i % palette.length],
      style: `animation-delay:${(Math.random() * 0.6).toFixed(2)}s`,
    });
  }
}

function toast(msg, isErr) {
  let el = document.getElementById("toast");
  if (!el) {
    el = document.createElement("div");
    el.id = "toast";
    document.body.appendChild(el);
  }
  el.textContent = msg;
  el.classList.toggle("toast--err", Boolean(isErr));
  el.classList.add("toast--show");
  clearTimeout(el._t);
  el._t = setTimeout(() => el.classList.remove("toast--show"), 4000);
}

/* ---------- 调度台：按钮 → /api/action → operations 服务层（CLI 同款） ---------- */

function driverName() {
  return document.getElementById("driver-name").value.trim();
}

function applyWriteMode() {
  const on = Boolean(uiConfig.approvals_enabled);
  document.getElementById("console-help").hidden = on;
  for (const btn of document.querySelectorAll(".dispatch-btn")) btn.disabled = !on;
}


/* 毫秒级浮动提示：原生 <title> 有浏览器级秒延迟，自绘 tip 即时显示 */
const mapTip = document.createElement("div");
mapTip.id = "map-tip";
mapTip.hidden = true;
document.body.appendChild(mapTip);
svg.addEventListener("mouseover", (ev) => {
  const host = ev.target.closest("[data-tip]");
  if (!host) return;
  mapTip.textContent = host.getAttribute("data-tip");
  mapTip.hidden = false;
});
svg.addEventListener("mousemove", (ev) => {
  if (mapTip.hidden) return;
  const pad = 14;
  mapTip.style.left = Math.min(ev.clientX + pad, window.innerWidth - mapTip.offsetWidth - 8) + "px";
  mapTip.style.top = Math.max(ev.clientY - mapTip.offsetHeight - 8, 8) + "px";
});
svg.addEventListener("mouseout", (ev) => {
  if (!ev.relatedTarget || !ev.relatedTarget.closest || !ev.relatedTarget.closest("[data-tip]")) {
    mapTip.hidden = true;
  }
});
svg.addEventListener("mouseleave", () => {
  mapTip.hidden = true;
});

/* 行车记录仪有新记录：高亮底舱 tab 并自动切换过来 */
function switchDockTab(paneId) {
  for (const t of document.querySelectorAll(".dock-tab")) t.classList.toggle("is-active", t.dataset.pane === paneId);
  for (const p of document.querySelectorAll(".dock-pane")) p.hidden = p.id !== paneId;
}

function consoleLine(text, cls = "") {
  const box = document.getElementById("console");
  const line = document.createElement("div");
  line.className = "line" + (cls ? ` ${cls}` : "");
  const ts = new Date().toLocaleTimeString([], { hour12: false });
  line.innerHTML = `<span class="line-ts">${ts}</span><span class="line-msg"></span>`;
  line.querySelector(".line-msg").textContent = text;
  box.appendChild(line);
  box.scrollTop = box.scrollHeight;
  // 有新记录：高亮行车记录 tab 并自动切换过来（司机刚点了按钮，结果就该立刻看见）
  const pane = document.getElementById("dock-events");
  if (pane && pane.hidden) {
    switchDockTab("dock-events");
    const tab = document.querySelector('.dock-tab[data-pane="dock-events"]');
    if (tab) {
      tab.classList.remove("is-attention");
      void tab.offsetWidth;
      tab.classList.add("is-attention");
    }
  }
  while (box.children.length > 200) box.removeChild(box.firstChild);
}

// 叙述员：把服务层返回翻译成行车记录仪口吻的一行行日志
function narrate(verb, body) {
  if (verb === "gate.run") {
    const icon = body.status === "pass" ? "🟢" : body.status === "fail" ? "🔴" : "🟡";
    consoleLine(`${icon} ${body.gate} → ${body.status}${body.needs_human ? "（待人审）" : ""}`);
    for (const c of body.checks || []) {
      consoleLine(`   · ${c.id} ${c.status}`, c.status === "pass" ? "" : "line--err");
    }
    for (const r of body.reasons || []) {
      consoleLine(`   ⚠ ${r}`, body.status === "pass" ? "" : "line--err");
    }
  } else if (verb === "flow.run") {
    const lines = body.journey || [];
    lines.forEach((line, i) => setTimeout(() => consoleLine("🚋 " + line), i * 160));
    setTimeout(
      () => consoleLine(`🏁 停车：${body.stop_reason || "—"}（引擎 ${body.engine}）`),
      lines.length * 160
    );
  } else if (verb === "guard.check") {
    if (body.ok) {
      consoleLine("🛡 轨内行驶——变更全部落在基线内");
    } else {
      consoleLine(`🛑 越界 ${body.violations.length} 处 → CR ${body.cr}（${body.cr_type}）已立案`, "line--err");
      for (const v of body.violations) consoleLine(`   · ${v}`, "line--err");
    }
  } else if (verb === "artifact.generate") {
    for (const a of body.artifacts || []) consoleLine(`🎫 ${a.kind} → ${a.path}`);
  } else if (verb === "evm.snapshot") {
    consoleLine(
      `📊 SPI ${body.spi} · CPI ${body.cpi}` +
        (body.reasons.length ? ` · 越界：${body.reasons.join("；")}` : " · 阈值内")
    );
    for (const r of body.new_risks || []) consoleLine(`🌧 自动入险：${r}`, "line--err");
  } else if (verb === "task.points") {
    consoleLine(`✍️ ${body.task} 点数更新 est=${body.est} spent=${body.spent}`);
  } else if (verb === "qa.fail") {
    consoleLine(`🐛 QA 失败立案 · 返工任务 ${body.rework_task} 已挂上主线`, "line--err");
  } else if (verb === "qa.pass") {
    consoleLine(`✅ ${body.task} QA 验证通过 → done`);
  } else if (verb === "baseline.save") {
    consoleLine(`📝 基线草稿已保存（v${body.version}）· 批准仍走站台审批`);
  } else if (verb === "lap.next") {
    consoleLine(`↺ 第 ${body.iteration} 圈发车——回到规划站，带着上一圈的账重新规划（渐进明细）`);
  } else if (verb === "risk.resolve") {
    consoleLine(`🌤 风险 ${body.risk} → ${body.status === "closed" ? "已关闭 ✅" : "观察中 👀"}`);
  } else if (verb === "knowledge.distill") {
    if ((body.files || []).length) {
      for (const f of body.files) consoleLine(`📚 ${f} 已更新`);
      consoleLine("知识库已更新——下轮对话自动携带摘要");
    } else {
      consoleLine("📚 没有新材料可提炼（先在会话车厢聊几轮）");
    }
  } else if (verb === "monitor.sweep") {
    const evm = body.evm || {};
    if (evm.skipped) {
      consoleLine(`👁 EVM：今天已快照（SPI ${evm.spi} · CPI ${evm.cpi}）`);
    } else {
      const mark = (evm.breaches || []).length ? ` · 越界：${evm.breaches.join("；")}` : " · 阈值内";
      consoleLine(`👁 EVM：SPI ${evm.spi} · CPI ${evm.cpi}${mark}`, !(evm.breaches || []).length ? "" : "line--err");
    }
    if (body.guard && body.guard.ok) consoleLine("👁 Guard：轨内行驶，无越界改动");
    else if (body.guard) consoleLine(`👁 Guard：越界 ${(body.guard.violations || []).length} 处 → CR ${body.guard.cr} 立案`, "line--err");
    consoleLine(`👁 风险：${(body.risks || {}).open ?? 0} 项未决`);
    const gaps = body.requirements || {};
    if (gaps.mode === "anchored" && gaps.unanchored > 0)
      consoleLine(`👁 需求对账：${gaps.unanchored} 个在途任务未锚定工作包（渐进明细没跟上）`, "line--err");
  } else {
    consoleLine(JSON.stringify(body));
  }
}

async function callAction(verb, args = {}, btn = null) {
  if (!uiConfig.approvals_enabled) {
    toast("只读模式 —— `tram ui --approve` 解锁调度台", true);
    return;
  }
  if (!driverName()) {
    // 开车前先亮司机证：调度动作都要署名落款，空署名后端只会 422
    const box = document.querySelector(".driver-box");
    box.classList.remove("is-missing");
    void box.offsetWidth;
    box.classList.add("is-missing");
    document.getElementById("driver-name").focus();
    toast("先在顶栏填司机署名 ✍️（写操作都要落款）", true);
    return;
  }
  if (btn) {
    btn.disabled = true;
    btn.classList.add("is-busy");
  }
  try {
    const res = await fetch("/api/action", {
      method: "POST",
      headers: { "Content-Type": "application/json", "X-Tram-Token": uiConfig.token },
      body: JSON.stringify({ verb, args, by: driverName() }),
    });
    const body = await res.json();
    if (!res.ok) {
      consoleLine(`✗ ${verb} 被拒绝：${body.detail || res.status}`, "line--err");
      toast(body.detail || "调度动作被拒绝", true);
      return;
    }
    narrate(verb, body);
    refresh();
  } catch (err) {
    consoleLine(`✗ ${verb} 请求异常：${err}`, "line--err");
  } finally {
    if (btn) {
      btn.disabled = false;
      btn.classList.remove("is-busy");
    }
  }
}

document.querySelector(".dispatch-strip").addEventListener("click", (ev) => {
  const btn = ev.target.closest(".dispatch-btn");
  if (!btn || btn.id === "baseline-open") return;
  const verb = btn.dataset.verb;
  if (!verb) return;
  const args = {};
  if (verb === "gate.run") args.gate = btn.dataset.gate;
  if (verb === "artifact.generate") args.all = true;
  callAction(verb, args, btn);
});

/* ---------- 基线编辑器（schema 校验在服务层，批准在站台） ---------- */

const baselineEditor = document.getElementById("baseline-editor");

document.getElementById("baseline-open").addEventListener("click", async () => {
  if (!uiConfig.approvals_enabled) {
    toast("只读模式 —— `tram ui --approve` 解锁基线编辑", true);
    return;
  }
  if (!baselineEditor.hidden) {
    baselineEditor.hidden = true;
    return;
  }
  try {
    const body = await fetch("/api/baseline").then((r) => r.json());
    document.getElementById("baseline-text").value = body.yaml;
    baselineEditor.hidden = false;
    document.getElementById("baseline-text").focus();
  } catch (err) {
    toast("读取基线失败：" + err, true);
  }
});

document.getElementById("baseline-cancel").addEventListener("click", () => {
  baselineEditor.hidden = true;
});

document.getElementById("baseline-save").addEventListener("click", async (ev) => {
  const by = driverName();
  if (!by) {
    toast("保存基线要署名 ✍️（司机署名栏）", true);
    return;
  }
  const btn = ev.currentTarget;
  btn.disabled = true;
  try {
    const res = await fetch("/api/action", {
      method: "POST",
      headers: { "Content-Type": "application/json", "X-Tram-Token": uiConfig.token },
      body: JSON.stringify({
        verb: "baseline.save",
        args: { yaml: document.getElementById("baseline-text").value },
        by,
      }),
    });
    const body = await res.json();
    if (!res.ok) {
      toast(body.detail || "基线保存失败", true);
      consoleLine(`✗ baseline.save 被拒绝：${body.detail || res.status}`, "line--err");
      return;
    }
    narrate("baseline.save", body);
    baselineEditor.hidden = true;
    refresh();
  } catch (err) {
    toast("基线保存请求异常：" + err, true);
  } finally {
    btn.disabled = false;
  }
});

/* ---------- 任务板：点数直改 + QA 闭环（返工链自动建档） ---------- */

function taskChipCls(status) {
  if (status === "done") return "chip--ok";
  if (status === "blocked") return "chip--fail";
  if (status === "doing") return "chip--pending";
  return "chip--idle";
}

function renderTasks(tasks) {
  const list = document.getElementById("task-list");
  list.innerHTML = "";
  if (!tasks.length) {
    list.innerHTML = '<li class="empty">任务册是空的 —— `tram task add` 落第一笔</li>';
    return;
  }
  const writable = Boolean(uiConfig.approvals_enabled);
  for (const t of tasks) {
    const li = document.createElement("li");
    // 车票打孔：任务这趟车验完票（→ done）就在票面上打个孔；刚打孔的放印章动画
    const prev = prevTaskStatus[t.id];
    const justPunched = prev !== undefined && prev !== t.status && t.status === "done";
    li.className =
      "task-row" +
      (t.rework_of ? " task-row--rework" : "") +
      (t.status === "done" ? " is-punched" : "") +
      (justPunched ? " is-just-punched" : "");
    li.innerHTML = `
      <span class="task-id">${esc(t.id)}</span>
      <span class="task-title">${esc(t.title)}${
        t.rework_of ? ` <small>↩ 返工自 ${esc(t.rework_of)}</small>` : ""
      }</span>
      <span class="chip ${taskChipCls(t.status)}">${esc(t.status)}</span>
      ${t.status === "done" ? '<span class="punch" title="QA 已验票"></span>' : ""}
      <span class="task-points">
        <label>est <input type="number" step="0.5" min="0" value="${t.est}" data-task="${esc(
          t.id
        )}" data-field="est"></label>
        <label>spent <input type="number" step="0.5" min="0" value="${t.spent}" data-task="${esc(
          t.id
        )}" data-field="spent"></label>
      </span>
      ${
        writable
          ? `<span class="task-qa">
        <button class="task-btn task-btn--pass" data-qa="pass" data-task="${esc(t.id)}" title="QA 验证通过 → done">✓</button>
        <button class="task-btn task-btn--fail" data-qa="fail" data-task="${esc(t.id)}" title="QA 复现失败 → 自动建返工任务">✗</button>
      </span>`
          : ""
      }
    `;
    list.appendChild(li);
  }
  for (const t of tasks) prevTaskStatus[t.id] = t.status;
}

document.getElementById("task-list").addEventListener("change", (ev) => {
  const input = ev.target.closest("input[data-field]");
  if (!input) return;
  const row = input.closest(".task-row");
  const est = row.querySelector('[data-field="est"]').value;
  const spent = row.querySelector('[data-field="spent"]').value;
  callAction("task.points", { task: input.dataset.task, est: Number(est), spent: Number(spent) });
});

document.getElementById("task-list").addEventListener("click", (ev) => {
  const btn = ev.target.closest(".task-btn");
  if (!btn) return;
  const taskId = btn.dataset.task;
  if (btn.dataset.qa === "fail") {
    const note = window.prompt(`QA 复现失败记录（${taskId}）——缺陷一句话：`, "");
    if (note === null) return; // 取消
    callAction("qa.fail", { task: taskId, note });
  } else {
    callAction("qa.pass", { task: taskId, note: "verified from UI" });
  }
});

/* ---------- 文件车厢：列/读/改，保存先过 Intent Guard（越界自动立案） ---------- */

let fileDir = "";
let openFilePath = "";

async function openDir(rel) {
  fileDir = rel;
  try {
    const body = await fetch("/api/files?path=" + encodeURIComponent(rel)).then((r) => {
      if (!r.ok) throw new Error(r.status);
      return r.json();
    });
    renderFiles(body.entries);
  } catch (err) {
    toast("目录读取失败：" + err.message, true);
  }
}

function renderCrumbs() {
  const wrap = document.getElementById("file-crumbs");
  wrap.innerHTML = "";
  const root = document.createElement("span");
  root.textContent = "📦 仓库根";
  root.className = "crumb";
  root.addEventListener("click", () => openDir(""));
  wrap.appendChild(root);
  let acc = "";
  for (const seg of fileDir.split("/").filter(Boolean)) {
    acc = acc ? `${acc}/${seg}` : seg;
    const part = acc;
    const sep = document.createElement("span");
    sep.textContent = " / ";
    sep.className = "crumb-sep";
    const crumb = document.createElement("span");
    crumb.textContent = seg;
    crumb.className = "crumb";
    crumb.addEventListener("click", () => openDir(part));
    wrap.append(sep, crumb);
  }
}

function renderFiles(entries) {
  renderCrumbs();
  const list = document.getElementById("file-list");
  list.innerHTML = "";
  if (fileDir) {
    const up = document.createElement("li");
    up.className = "file-item file-item--dir";
    up.innerHTML = '<span class="file-icon">↩</span><span class="file-name">..</span>';
    up.addEventListener("click", () => openDir(fileDir.split("/").slice(0, -1).join("/")));
    list.appendChild(up);
  }
  for (const e of entries) {
    const li = document.createElement("li");
    li.className = "file-item " + (e.type === "dir" ? "file-item--dir" : "file-item--file");
    const icon = e.type === "dir" ? "📁" : "📄";
    const dot = e.changed ? '<span class="file-dot" title="有未提交改动">●</span>' : "";
    const size = e.type === "file" ? `<span class="file-size">${fmtSize(e.size)}</span>` : "";
    const badge = domainBadge(e.domain);
    li.innerHTML = `<span class="file-icon">${icon}</span><span class="file-name">${esc(
      e.name
    )}${dot}</span>${badge}${size}`;
    li.addEventListener("click", () => (e.type === "dir" ? openDir(e.path) : openFile(e.path)));
    list.appendChild(li);
  }
  if (entries.length === 0 && !fileDir) {
    list.innerHTML = '<li class="empty">仓库根是空的</li>';
  }
}

/* 文件的知识域徽章：单字胶囊，悬停看全称（过程组 · 知识域 · 子过程） */
function domainBadge(domain) {
  if (!domain) return "";
  const tip = esc(`${domain.group}组 · ${domain.area}管理 · ${domain.process}`);
  return `<span class="ka-badge" title="${tip}">${esc(domain.badge)}</span>`;
}

/* ---------- 按知识域分组视图：文件答得出「属于哪个过程组·知识域·子过程」 ---------- */

async function loadDomainView() {
  const wrap = document.getElementById("files-domain-view");
  try {
    const body = await fetch("/api/files?flat=1").then((r) => {
      if (!r.ok) throw new Error(r.status);
      return r.json();
    });
    const groups = new Map(); // key: badge|process|group|area -> {meta, files}
    for (const e of body.entries) {
      const d = e.domain;
      const key = `${d.badge}|${d.process}|${d.group}|${d.area}`;
      if (!groups.has(key)) groups.set(key, { meta: d, files: [] });
      groups.get(key).files.push(e);
    }
    const sections = [...groups.values()].sort(
      (a, b) => a.meta.group.localeCompare(b.meta.group, "zh") || a.meta.area.localeCompare(b.meta.area, "zh")
    );
    wrap.innerHTML = "";
    for (const g of sections) {
      const sec = document.createElement("div");
      sec.className = "ka-group";
      sec.innerHTML = `
        <div class="ka-group-head">
          <span class="ka-badge ka-badge--lg">${esc(g.meta.badge)}</span>
          <strong>${esc(g.meta.area)}管理 · ${esc(g.meta.process)}</strong>
          <span class="chip">${esc(g.meta.group)}组</span>
          <span class="ka-count">${g.files.length} 个文件</span>
        </div>
        <ul class="ka-files"></ul>
      `;
      const ul = sec.querySelector(".ka-files");
      for (const f of g.files) {
        const li = document.createElement("li");
        li.className = "ka-file";
        const dot = f.changed ? '<span class="file-dot" title="有未提交改动">●</span>' : "";
        li.innerHTML = `<code>${esc(f.path)}</code>${dot}<span class="file-size">${fmtSize(f.size)}</span>`;
        li.addEventListener("click", () => openFile(f.path));
        ul.appendChild(li);
      }
      wrap.appendChild(sec);
    }
    if (!sections.length) wrap.innerHTML = '<div class="empty">仓库里还没有文件</div>';
  } catch (err) {
    wrap.innerHTML = '<div class="empty">知识域视图加载失败</div>';
    toast("知识域视图加载失败：" + err.message, true);
  }
}

document.getElementById("file-view-dir").addEventListener("click", () => switchFileView("dir"));
document.getElementById("file-view-domain").addEventListener("click", () => switchFileView("domain"));

function switchFileView(mode) {
  document.getElementById("file-view-dir").classList.toggle("is-active", mode === "dir");
  document.getElementById("file-view-domain").classList.toggle("is-active", mode === "domain");
  document.getElementById("files-dir-view").hidden = mode !== "dir";
  document.getElementById("files-domain-view").hidden = mode !== "domain";
  if (mode === "domain") loadDomainView();
  else openDir(fileDir);
}

function fmtSize(n) {
  if (n == null) return "";
  if (n < 1024) return `${n} B`;
  if (n < 1024 * 1024) return `${(n / 1024).toFixed(1)} KB`;
  return `${(n / 1024 / 1024).toFixed(1)} MB`;
}

async function openFile(rel) {
  try {
    const res = await fetch("/api/file?path=" + encodeURIComponent(rel));
    const body = await res.json();
    if (!res.ok) {
      toast(body.detail || "文件读取失败", true);
      return;
    }
    if (body.binary) {
      toast("二进制文件，车厢里看不了 👀", true);
      return;
    }
    openFilePath = body.path;
    document.getElementById("file-editor").hidden = false;
    document.getElementById("file-path").textContent = body.path;
    const guard = document.getElementById("file-guard");
    guard.textContent = body.in_baseline ? "轨内 ✓" : "越界 ⚠";
    guard.className = "chip " + (body.in_baseline ? "chip--ok" : "chip--fail");
    document.getElementById("file-text").value = body.content;
    if (body.truncated) toast("文件过大，只载入前 512KB", true);
  } catch (err) {
    toast("文件读取异常：" + err, true);
  }
}

document.getElementById("file-save").addEventListener("click", async (ev) => {
  const by = driverName();
  if (!by) {
    toast("保存文件要署名 ✍️（调度台司机署名栏）", true);
    return;
  }
  const btn = ev.currentTarget;
  btn.disabled = true;
  try {
    const res = await fetch("/api/file", {
      method: "POST",
      headers: { "Content-Type": "application/json", "X-Tram-Token": uiConfig.token },
      body: JSON.stringify({
        path: openFilePath,
        content: document.getElementById("file-text").value,
        by,
      }),
    });
    const body = await res.json();
    if (!res.ok) {
      toast(body.detail || "保存被拒绝", true);
      consoleLine(`✗ ${openFilePath} 保存被拒：${body.detail || res.status}`, "line--err");
      return;
    }
    consoleLine(`📄 ${body.path} 已保存（人工编辑 · 已过 Guard · ${body.bytes}B）`);
    toast("已保存 ✅");
    openFile(body.path);
    openDir(fileDir);
  } catch (err) {
    toast("保存请求异常：" + err, true);
  } finally {
    btn.disabled = false;
  }
});

/* ---------- 会话车厢：与引擎多轮对话（jobs + SSE 流式，Guard 铁轨收尾） ---------- */

let chatSessionId = null;
let chatES = null; // 当前 job 的 EventSource

async function refreshChatSessions() {
  try {
    const sessions = await fetch("/api/chat/sessions").then((r) => r.json());
    const sel = document.getElementById("chat-sessions");
    sel.innerHTML = '<option value="">— 选择会话 —</option>';
    for (const s of sessions) {
      const opt = document.createElement("option");
      opt.value = s.id;
      opt.textContent = `${s.id} · ${s.engine} · ${s.messages} 条` + (s.task_id ? ` · ${s.task_id}` : "");
      sel.appendChild(opt);
      if (s.id === chatSessionId) sel.value = s.id;
    }
    if (!sel.value && sessions.length) sel.value = sessions[0].id;
    chatSessionId = sel.value || null;
  } catch (err) {
    console.error("chat sessions failed", err);
  }
}

function chatLog() {
  return document.getElementById("chat-log");
}

/* 聊天历史：服务器端 .tram/chat/<sid>/log.jsonl 持久化，刷新页面据此还原 */
async function loadChatLog(sid) {
  const log = chatLog();
  log.innerHTML = "";
  if (!sid) {
    log.innerHTML = '<div class="chat-hint">开新会话后发第一条消息：立任务 + 建常驻 worktree，之后每条消息跑完即过 Guard</div>';
    return;
  }
  try {
    const body = await fetch(`/api/chat/sessions/${encodeURIComponent(sid)}/log`).then((r) => {
      if (!r.ok) throw new Error(r.status);
      return r.json();
    });
    if (!body.lines.length) {
      log.innerHTML = '<div class="chat-hint">这个会话还没有说过话</div>';
      return;
    }
    for (const line of body.lines) appendChatLine(line);
  } catch (err) {
    log.innerHTML = '<div class="chat-hint">历史加载失败，发条新消息继续</div>';
  }
}

document.getElementById("chat-sessions").addEventListener("change", (ev) => {
  chatSessionId = ev.target.value || null;
  loadChatLog(chatSessionId);
});

/* 并线：会话分支经 Guard 预检合回主线——沙箱产出进项目文件的正门 */
document.getElementById("chat-merge").addEventListener("click", async () => {
  const sid = document.getElementById("chat-sessions").value;
  if (!sid) {
    toast("先选一条会话", true);
    return;
  }
  if (!uiConfig.approvals_enabled) {
    toast("只读模式 —— `tram ui --approve` 解锁并线", true);
    return;
  }
  const by = driverName();
  if (!by) {
    toast("并线要署名 ✍️（顶栏司机署名）", true);
    return;
  }
  if (!window.confirm(`把 ${sid} 的分支并回主线？\n会先过 Intent Guard，轨内才合；冲突会自动中止。`)) return;
  try {
    const res = await fetch(`/api/chat/sessions/${encodeURIComponent(sid)}/merge`, {
      method: "POST",
      headers: { "Content-Type": "application/json", "X-Tram-Token": uiConfig.token },
      body: JSON.stringify({ by }),
    });
    const body = await res.json();
    if (!res.ok) {
      appendChatNote(`并线被拒：${body.detail || res.status}`, "error");
      toast(body.detail || "并线被拒", true);
      return;
    }
    if (body.merged) {
      const anchorNote = body.anchor && body.anchor !== "free" ? ` · 锚定 ${body.anchor}` : " · 自由模式（项目未定义 WBS 工作包）";
      appendChatNote(`🔀 已并线（${(body.commit || "").slice(0, 8)} · ${body.paths.length} 个路径进主线${anchorNote}）`, "result");
      if ((body.overlaps || []).length) {
        const lines = body.overlaps.map((o) => `${o.path} ↔ ${o.session}`).join("、");
        appendChatNote(`⚠️ 跨会话重叠预警：${lines} —— 另一条在途会话也改了这些路径，并线时留意冲突`, "error");
      }
      toast("会话分支已并回主线 ✅");
      refresh();
    } else if (body.reason === "unanchored" || body.reason === "anchor_mismatch") {
      appendChatNote(`锚定校验未过：${body.detail}${body.outside ? `（越出：${body.outside.join("、")}）` : ""}`, "error");
      toast(body.detail || "锚定校验未过", true);
    } else if (body.reason === "blocked") {
      appendChatNote(`越界路径被拦 → CR ${body.cr} 立案，站台审批放行或扩基线后重试`, "error");
      toast(`并线立案：CR ${body.cr}`, true);
      refresh();
    } else {
      appendChatNote(body.detail, "result");
    }
  } catch (err) {
    toast("并线异常：" + err, true);
  }
});

document.getElementById("chat-clear-log").addEventListener("click", async () => {
  const sid = document.getElementById("chat-sessions").value;
  if (!sid) {
    toast("先选一条会话", true);
    return;
  }
  if (!window.confirm(`清空 ${sid} 的聊天历史？服务器端一并删除，不可恢复。`)) return;
  const by = driverName();
  if (!by) {
    toast("清空历史要署名 ✍️（顶栏司机署名）", true);
    return;
  }
  try {
    const res = await fetch(`/api/chat/sessions/${encodeURIComponent(sid)}/log`, {
      method: "DELETE",
      headers: { "Content-Type": "application/json", "X-Tram-Token": uiConfig.token },
      body: JSON.stringify({ by }),
    });
    const body = await res.json();
    if (!res.ok) {
      toast(body.detail || "清空失败", true);
      return;
    }
    loadChatLog(sid);
    toast("聊天历史已清空 🧹");
  } catch (err) {
    toast("清空异常：" + err, true);
  }
});

function appendChatLine(line) {
  const log = chatLog();
  const hint = log.querySelector(".chat-hint");
  if (hint) hint.remove();
  const div = document.createElement("div");
  if (line.k === "me") {
    div.className = "chat-row chat-row--me";
    div.innerHTML = '<span class="chat-who">你</span><span class="chat-bubble"></span>';
  } else if (line.k === "text") {
    div.className = "chat-row chat-row--engine";
    div.innerHTML = '<span class="chat-who">🚋</span><span class="chat-bubble"></span>';
  } else if (line.k === "think") {
    div.className = "chat-row chat-row--think";
    div.innerHTML = '<span class="chat-who">💭</span><span class="chat-bubble"></span>';
  } else if (line.k === "tool") {
    div.className = "chat-row chat-row--tool";
    div.innerHTML = `<span class="chat-who">🔧</span><span class="chat-bubble">${esc(line.name)}</span>`;
  } else if (line.k === "result") {
    div.className = "chat-row chat-row--result";
    div.innerHTML = '<span class="chat-who">✅</span><span class="chat-bubble"></span>';
  } else if (line.k === "error") {
    div.className = "chat-row chat-row--err";
    div.innerHTML = '<span class="chat-who">⛔</span><span class="chat-bubble"></span>';
  } else {
    div.className = "chat-row chat-row--tool";
    div.innerHTML = '<span class="chat-who">·</span><span class="chat-bubble"></span>';
  }
  div.querySelector(".chat-bubble").textContent =
    line.text || line.input || "";
  log.appendChild(div);
  log.scrollTop = log.scrollHeight;
}

function appendChatNote(text, cls) {
  appendChatLine({ k: cls, text });
}

function chatBusy(on) {
  document.getElementById("chat-send").disabled = on;
  document.getElementById("chat-send").textContent = on ? "行驶中…" : "发送";
  document.getElementById("chat-stop").hidden = !on;
}

let currentJobId = null; // 正在行驶的 job（急停用）

function streamChatJob(jobId) {
  currentJobId = jobId;
  if (chatES) chatES.close();
  chatES = new EventSource("/api/chat/stream?job=" + encodeURIComponent(jobId));
  chatES.onmessage = (msg) => {
    const o = JSON.parse(msg.data);
    if (o.k === "line") {
      appendChatLine(o.line);
    } else if (o.k === "done") {
      chatES.close();
      const job = o.job;
      if (job.status === "ok" && job.commit) {
        appendChatNote(`已提交 ${(job.commit || "").slice(0, 8)} —— 改动在分支上，Guard 绿灯`, "result");
        toast("引擎改动已提交 ✅");
      } else if (job.status === "ok") {
        appendChatNote("本轮无文件改动", "result");
      } else if (job.status === "blocked") {
        appendChatNote(`越界改动已拦截 → CR ${job.cr} 立案，站台审批可裁`, "error");
        toast(`越界立案：CR ${job.cr}`, true);
      } else if (job.status === "stopped") {
        appendChatNote("⏹ 本轮已被司机手动停止（未过 Guard，无提交）", "result");
        toast("本轮已停止");
      } else {
        appendChatNote(`出故障了：${job.error || "未知错误"}`, "error");
        toast("会话出故障：" + (job.error || ""), true);
      }
      chatBusy(false);
      refresh();
      refreshChatSessions();
    }
  };
  chatES.onerror = () => {
    if (chatES && chatES.readyState === EventSource.CLOSED) chatBusy(false);
  };
}

async function sendChat() {
  if (!uiConfig.approvals_enabled) {
    toast("只读模式 —— `tram ui --approve` 解锁会话车厢", true);
    return;
  }
  const input = document.getElementById("chat-input");
  const message = input.value.trim();
  if (!message) return;
  const by = driverName();
  if (!by) {
    toast("发消息要署名 ✍️（调度台司机署名栏）", true);
    return;
  }
  chatSessionId = document.getElementById("chat-sessions").value || null;
  appendChatLine({ k: "me", text: message });
  input.value = "";
  input.style.height = "auto"; // 发送后收起多行
  chatBusy(true);
  try {
    const res = await fetch("/api/chat/send", {
      method: "POST",
      headers: { "Content-Type": "application/json", "X-Tram-Token": uiConfig.token },
      body: JSON.stringify({
        session: chatSessionId,
        engine: document.getElementById("chat-engine").value,
        wbs_package: document.getElementById("chat-package").value || null,
        message,
        by,
      }),
    });
    const body = await res.json();
    if (!res.ok) {
      appendChatNote(`被拒绝：${body.detail || res.status}`, "error");
      toast(body.detail || "消息被拒绝", true);
      chatBusy(false);
      return;
    }
    chatSessionId = body.session.id;
    document.getElementById("chat-sessions").value = chatSessionId;
    streamChatJob(body.job.id);
  } catch (err) {
    appendChatNote("请求异常：" + err, "error");
    chatBusy(false);
  }
}

document.getElementById("chat-send").addEventListener("click", sendChat);
const chatInputEl = document.getElementById("chat-input");
chatInputEl.addEventListener("keydown", (ev) => {
  if (ev.key === "Enter" && !ev.shiftKey) {
    ev.preventDefault(); // Enter 发送；Shift+Enter 留给换行
    sendChat();
  }
});
chatInputEl.addEventListener("input", () => {
  // 自动长高，封顶三行
  chatInputEl.style.height = "auto";
  chatInputEl.style.height = Math.min(chatInputEl.scrollHeight, 72) + "px";
});

/* 司机急停：终止本轮引擎进程（本轮作废，不进 Guard） */
document.getElementById("chat-stop").addEventListener("click", async () => {
  if (!currentJobId) return;
  if (!uiConfig.approvals_enabled) {
    toast("只读模式无正在行驶的任务", true);
    return;
  }
  try {
    const res = await fetch(`/api/chat/jobs/${encodeURIComponent(currentJobId)}/stop`, {
      method: "POST",
      headers: { "X-Tram-Token": uiConfig.token },
    });
    const body = await res.json();
    if (!res.ok) toast(body.detail || "停止失败", true);
    else toast("正在停止本轮…");
  } catch (err) {
    toast("停止异常：" + err, true);
  }
});

document.getElementById("chat-new").addEventListener("click", async () => {
  if (!uiConfig.approvals_enabled) {
    toast("只读模式 —— `tram ui --approve` 解锁会话车厢", true);
    return;
  }
  const by = driverName();
  if (!by) {
    toast("开新会话要署名 ✍️", true);
    return;
  }
  try {
    const res = await fetch("/api/chat/sessions", {
      method: "POST",
      headers: { "Content-Type": "application/json", "X-Tram-Token": uiConfig.token },
      body: JSON.stringify({
        engine: document.getElementById("chat-engine").value,
        wbs_package: document.getElementById("chat-package").value || null,
        by,
      }),
    });
    const body = await res.json();
    if (!res.ok) {
      toast(body.detail || "开会话失败", true);
      return;
    }
    chatSessionId = body.id;
    await refreshChatSessions();
    appendChatNote(`会话 ${body.id} 已开（engine: ${body.engine}）`, "result");
  } catch (err) {
    toast("开会话异常：" + err, true);
  }
});

document.getElementById("chat-close").addEventListener("click", async () => {
  const sid = document.getElementById("chat-sessions").value;
  if (!sid) {
    toast("先选一条会话", true);
    return;
  }
  try {
    const res = await fetch(`/api/chat/sessions/${sid}/close`, {
      method: "POST",
      headers: { "X-Tram-Token": uiConfig.token },
    });
    const body = await res.json();
    if (!res.ok) {
      toast(body.detail || "收车失败", true);
      return;
    }
    appendChatNote(
      body.kept_worktree
        ? `会话 ${sid} 已收 · worktree 有未审改动，保留在 ${body.worktree}`
        : `会话 ${sid} 已收 · worktree 干净移除`,
      "result"
    );
    if (chatSessionId === sid) chatSessionId = null;
    refreshChatSessions();
    refresh();
  } catch (err) {
    toast("收车异常：" + err, true);
  }
});

refreshChatSessions().then(() => loadChatLog(chatSessionId)); // 刷新页面还原会话历史

/* WBS 工作包下拉（.tram/wbs.yaml，PM 规划工件）；无工作包 = 自由模式 */
async function loadWbsPackages() {
  const sel = document.getElementById("chat-package");
  try {
    const body = await fetch("/api/wbs").then((r) => r.json());
    sel.innerHTML = '<option value="">🧩 自由（未锚定）</option>';
    for (const p of body.packages || []) {
      const opt = document.createElement("option");
      opt.value = p.id;
      opt.textContent = `${p.id} · ${p.title || ""}（${(p.paths || []).join(" ")}）`;
      sel.appendChild(opt);
    }
  } catch (err) {
    console.error("wbs failed", err);
  }
}

/* 领航员：只读护航建议——不执行任何动作，方向盘在驾驶员手里 */
document.getElementById("navigate-run").addEventListener("click", async () => {
  try {
    const body = await fetch("/api/navigate").then((r) => r.json());
    consoleLine(`🧭 领航员 · ${body.phase} · 环线第 ${body.iteration} 圈 · ${body.summary}`);
    for (const rec of body.recommendations || []) {
      consoleLine(`  → ${rec.label}（${rec.where}）`);
    }
    if (!(body.recommendations || []).length) consoleLine("  → 护航仪表全绿");
    toast(`领航员：${body.summary}`);
  } catch (err) {
    toast("领航异常：" + err, true);
  }
});

loadWbsPackages();

/* ---------- 行车 KPI ---------- */

function fmtDuration(sec) {
  if (!sec) return "—";
  if (sec < 90) return `${Math.round(sec)}s`;
  if (sec < 5400) return `${Math.round(sec / 60)}min`;
  return `${(sec / 3600).toFixed(1)}h`;
}

function renderKpis(kpi) {
  const wrap = document.getElementById("kpis");
  if (!kpi) { wrap.innerHTML = ""; return; }
  const gate = kpi.gate_mttr, defect = kpi.defect_mttr, rw = kpi.rework, es = kpi.escape;
  const gateTip = gate.items.map((m) => `${m.subject}: ${fmtDuration(m.mttr_seconds)} ×${m.breaches}`).join("\n")
    + (gate.open_subjects.length ? `\n未恢复: ${gate.open_subjects.join(", ")}` : "");
  const defectTip = defect.items.map((m) => `${m.subject}: ${fmtDuration(m.mttr_seconds)} ×${m.breaches}`).join("\n")
    + (defect.open_subjects.length ? `\n未修复: ${defect.open_subjects.join(", ")}` : "");
  const rwPct = `${Math.round(rw.rate * 100)}%`;
  const esPct = es ? `${Math.round(es.rate * 100)}% (${es.defects_escaped}/${es.defects_total})` : "—";
  wrap.innerHTML = `
    <div class="kpi" title="${esc(gateTip)}"><div class="num">${fmtDuration(gate.overall_seconds)}</div><div class="lbl">门禁 MTTR${gate.open_subjects.length ? " ⚠" : ""}</div></div>
    <div class="kpi" title="${esc(defectTip)}"><div class="num">${fmtDuration(defect.overall_seconds)}</div><div class="lbl">缺陷 MTTR${defect.open_subjects.length ? " ⚠" : ""}</div></div>
    <div class="kpi ${rw.rate >= 0.5 ? "chip--fail" : ""}"><div class="num">${rwPct}</div><div class="lbl">返工率 (${rw.tasks_with_rework}/${rw.tasks_done})</div></div>
    <div class="kpi" title="修复验证通过后又复发的缺陷占比"><div class="num">${esPct}</div><div class="lbl">逃逸率</div></div>
  `;
}

/* ---------- 风险气象台 ---------- */

// P×I 打分定天气：确定性映射，无主观措辞。
function weather(score) {
  if (score >= 15) return { icon: "⛈", label: "雷雨", cls: "wx--storm" };
  if (score >= 8) return { icon: "🌦", label: "阵雨", cls: "wx--rain" };
  return { icon: "🌤", label: "多云", cls: "wx--cloud" };
}

function renderWeather(risks) {
  const wrap = document.getElementById("weather");
  if (!risks.length) {
    wrap.innerHTML = '<span class="empty">全线晴朗，风险册是空的 🌈 —— EVM 越界 / 门禁红线会自动入险</span>';
    return;
  }
  const writable = Boolean(uiConfig.approvals_enabled);
  wrap.innerHTML = risks
    .map((r) => {
      const score = r.probability * r.impact;
      const wx = weather(score);
      const buttons = writable
        ? `<span class="wx-act">
            <button class="task-btn" data-risk="${esc(r.id)}" data-rstatus="watching" title="降级为观察">👀</button>
            <button class="task-btn task-btn--pass" data-risk="${esc(r.id)}" data-rstatus="closed" title="裁决关闭">✓</button>
          </span>`
        : "";
      return `
      <div class="wx-row ${wx.cls}">
        <span class="wx-icon">${wx.icon}</span>
        <span class="wx-id">${esc(r.id)}</span>
        <span class="wx-desc">${esc(r.description)}</span>
        <span class="wx-score">P${r.probability}×I${r.impact}=${score}</span>
        <span class="wx-meta">${esc(r.strategy)} · ${esc(r.owner || "—")}${r.trigger_event_seq != null ? ` · #${r.trigger_event_seq}` : ""}</span>
        ${buttons}
      </div>`;
    })
    .join("");
}

document.getElementById("weather").addEventListener("click", (ev) => {
  const btn = ev.target.closest("button[data-risk]");
  if (!btn) return;
  callAction("risk.resolve", { risk: btn.dataset.risk, status: btn.dataset.rstatus });
});

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

/* ---------- 底舱 tabs：次要面板一屏内切换，不滚屏 ---------- */

document.querySelector(".dock-tabs").addEventListener("click", (ev) => {
  const tab = ev.target.closest(".dock-tab");
  if (!tab) return;
  switchDockTab(tab.dataset.pane);
});

async function boot() {
  buildMap();
  try {
    // 十大知识域词表：服务层 /api/domains（与文件归属、会话前导同一份）
    DOMAIN_DATA = await fetch("/api/domains").then((r) => r.json());
    renderLegend();
    renderLoopDetail("initiating", 1); // 真值随首个 state 刷新换装
  } catch (err) {
    console.error("domains failed", err); // 词表拿不到也不挡地图主线
  }
  try {
    Object.assign(uiConfig, await fetch("/api/ui-config").then((r) => r.json()));
  } catch (err) {
    console.error("ui-config failed", err);
  }
  applyWriteMode();
  openDir("");
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
