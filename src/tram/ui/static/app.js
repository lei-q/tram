/* Tram 薄版 UI - 无构建 vanilla JS。数据来自只读 API；
   `tram ui --approve` 解锁写模式：调度台（/api/action）与站台审批（/api/approve）
   都派发到与 CLI 相同的服务层——每个按钮都长在铁轨上。 */
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

  // Trammy 小电车（外层管位移，内层管颠簸动画）
  tramEl = el("g", { class: "tram", id: "tram" });
  const tramInner = el("g", { class: "tram-inner" }, tramEl);
  const tram = (n, a) => el(n, a, tramInner);
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

  // 动画收尾：一次性动画结束后摘掉类，方便下次重放
  tramEl.addEventListener("animationend", () => tramEl.classList.remove("is-depart"));
  svg.addEventListener("animationend", (ev) => {
    const g = ev.target.closest(".signal");
    if (g) g.classList.remove("signal--blip");
  });
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

// 变更检测：只在状态真的变了时放一次动画（寓意：到站颠一下、信号翻灯闪一下）
let prevPhase = null;
const prevGateStatus = {};

function renderState(state) {
  document.getElementById("project-name").textContent = `Tram · ${state.project_name}`;
  const chip = document.getElementById("phase-chip");
  const phase = PHASES.find((p) => p.id === state.phase);
  chip.textContent = `阶段：${phaseLabel(state.phase)}`;
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

/* ---------- 调度台：按钮 → /api/action → operations 服务层（CLI 同款） ---------- */

function driverName() {
  return document.getElementById("driver-name").value.trim();
}

function applyWriteMode() {
  const on = Boolean(uiConfig.approvals_enabled);
  document.getElementById("console-help").hidden = on;
  for (const btn of document.querySelectorAll(".dispatch-btn")) btn.disabled = !on;
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
  } else {
    consoleLine(JSON.stringify(body));
  }
}

async function callAction(verb, args = {}, btn = null) {
  if (!uiConfig.approvals_enabled) {
    toast("只读模式 —— `tram ui --approve` 解锁调度台", true);
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

document.querySelector(".dispatch-bar").addEventListener("click", (ev) => {
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
    li.className = "task-row" + (t.rework_of ? " task-row--rework" : "");
    li.innerHTML = `
      <span class="task-id">${esc(t.id)}</span>
      <span class="task-title">${esc(t.title)}${
        t.rework_of ? ` <small>↩ 返工自 ${esc(t.rework_of)}</small>` : ""
      }</span>
      <span class="chip ${taskChipCls(t.status)}">${esc(t.status)}</span>
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
    li.innerHTML = `<span class="file-icon">${icon}</span><span class="file-name">${esc(
      e.name
    )}${dot}</span>${size}`;
    li.addEventListener("click", () => (e.type === "dir" ? openDir(e.path) : openFile(e.path)));
    list.appendChild(li);
  }
  if (entries.length === 0 && !fileDir) {
    list.innerHTML = '<li class="empty">仓库根是空的</li>';
  }
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
