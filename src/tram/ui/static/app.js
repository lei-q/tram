/* Tram 薄版 UI - 无构建 vanilla JS。数据全部来自只读 API；
   仅当 `tram ui --approve` 时，站台审批按钮可用（写事件流，与 CLI 同路径）。 */
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

function esc(s) {
  return String(s).replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
}

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
  wrap.innerHTML = risks
    .map((r) => {
      const score = r.probability * r.impact;
      const wx = weather(score);
      return `
      <div class="wx-row ${wx.cls}">
        <span class="wx-icon">${wx.icon}</span>
        <span class="wx-id">${esc(r.id)}</span>
        <span class="wx-desc">${esc(r.description)}</span>
        <span class="wx-score">P${r.probability}×I${r.impact}=${score}</span>
        <span class="wx-meta">${esc(r.strategy)} · ${esc(r.owner || "—")}${r.trigger_event_seq != null ? ` · #${r.trigger_event_seq}` : ""}</span>
      </div>`;
    })
    .join("");
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
  try {
    Object.assign(uiConfig, await fetch("/api/ui-config").then((r) => r.json()));
  } catch (err) {
    console.error("ui-config failed", err);
  }
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
