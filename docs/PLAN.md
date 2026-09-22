# Tram 实施计划（定稿 v1.1）

> 面向研发的 AI Coding Agent 治理轨道。Tram 不造引擎，只铺铁轨：AI 做研发像有轨列车——稳定、可观测、能自动发现问题并纠正。
>
> 状态：Phase 1 垂直切片**已跑通**（见任务状态表）。本文是定稿计划与决策记录。

## 1. 目标与非目标

**目标**
- **G1 治理运行时**：五大过程组裁剪为状态机；阶段门不通过即阻塞，Agent 无法绕过。
- **G2 工件即证据**：项目文件自动生成且链接真实证据（commit / 测试报告 / 事件）；证据不足显式标注 `evidence-gap`，禁止编造。
- **G3 确定性优先**：门禁检查、EVM、覆盖率、漏洞扫描全部确定性代码计算；LLM 只做结构化与叙述。
- **G4 人类在环**：范围基线、架构决策、发布、重大变更四类决策点强制人类审批。
- **G5 黑匣子**：append-only JSONL 事件日志 + OpenTelemetry，任何偏差可回放。

**非目标**：不自研代码生成引擎；不做全量 PMBOK 文档（采购/干系人裁剪为 lite）；MVP 不做 Web SaaS / 多租户 / Temporal。

## 2. 决策记录（已确认）

| # | 决策 | 结论 |
|---|------|------|
| D1 | 语言栈 | Python ≥3.11 + Pydantic v2 + Typer |
| D2 | 首个适配引擎 | 只做 Claude Code（headless CLI）；fake runner 用于离线测试/演示 |
| D3 | 策略引擎 | OPA/Rego 为主 + 内置 Python fallback（语义一致，CI 跑双引擎对比测试） |
| D4 | 沙箱 | **git worktree 为默认**；docker 为可选（共享环境强隔离） |
| D5 | HITL 形态 | MVP 用 CLI + approval 记录；Phase 2 接 PR review 流 ✅（`tram cr link/sync`，review 状态确定性映射裁决） |
| D6 | 节奏 | 垂直切片优先：先串通 guard→agent→CR→gate 最短闭环 |
| D7 | UI 节奏 | (a) Phase 1 收尾即上**只读线路图薄版**（React+Vite+自绘 SVG）；完整 UI 在 Phase 2 |
| D8 | 前端栈 | React + Vite + TypeScript，线路图自绘 SVG，设计 token 见 §7 |

## 3. 核心机制（已实现部分加 ✅）

- **过程即代码**：G0–G3 门禁 = `gates.yaml` 确定性检查 + `gates.rego` 策略决策（fallback 同语义）✅
- **工件即证据**：artifacts index + 证据校验器（commit 存在性 / 文件存在性 / 事件 seq 回查）✅
- **门禁即信号**：检查失败 → 门红（exit 1）；范围 CR 未闭环 → G2 阻塞；纠偏超 3 次 → `blocked_pending_human` ✅
- **变更即分支**：Intent Guard 拦截基线外改动 → 自动生成 CR → 人工 approve（并入基线 v+1）/ reject（关闭）✅
- **沙箱**：worktree 会话——任务在 `tram/<task>` 分支进行；提交由 Tram 亲自做（带任务 id + trailer）；有未提交改动时 worktree 保留不销毁 ✅
- **黑匣子**：16 种事件全量落 JSONL（seq 单调、断点续号、坏行不崩），`tram replay` 回放 ✅
- **人类在环**：`tram baseline approve` / `tram cr approve|reject`，审批写入 state + 事件流 ✅

## 4. 知识域裁剪（tram.yaml）

integration/scope/schedule/cost/quality/communication/risk = full；resource/procurement/stakeholder = lite。lite 域展开情况：采购=依赖引入 CR-lite ✅（越界改动命中依赖清单 `**/pyproject.toml`、`**/package.json` 等 20 种模式即按 procurement 建 CR，批准后并入基线）；干系人=审批人配置 ✅（`tram.yaml` 的 `approvers: {baseline|release|cr: [名单]}`，未配置不限制；UI 直连审批同受约束）。

## 5. 架构（模块即边界，未来即服务边界）

```
CLI (Typer) ─→ TramContext(repo/config/state/events/git)
                ├─ governance/  checks(确定性) + policy_engine(OPA|fallback) + intent_guard + gate_runner
                ├─ orchestration/  阶段推进纯函数（LangGraph 包装待接）
                ├─ adapters/   AgentRunner 协议：fake ✅ / claude-code ✅（/openhands 预留）
                ├─ sandbox/    WorktreeSession ✅ / DockerSandboxRunner ✅
                ├─ evidence/   git_client + verifier
                ├─ obs/        EventLog(JSONL) + otel(可选)
                └─ models/     Pydantic：state/gates/cr/risk/evm/task/artifact/event
```

红线：governance / evidence / metrics 三层禁止调用 LLM；agents 层不得直写 state。

## 6. 任务状态（Phase 1 垂直切片）

| ID | 任务 | 状态 |
|----|------|------|
| T1.1 | 仓库骨架 + git + CI（ruff/pytest，3×Python 矩阵 + OPA） | ✅ |
| T1.2 | 核心数据模型（Pydantic v2，11 个模块） | ✅ |
| T1.3 | JSONL 事件黑匣子 + OTel 挂钩（未装则 no-op） | ✅ |
| T1.4 | `tram init`（.tram/ + 裁剪配置 + 基线模板） | ✅ |
| T1.5 | git 证据客户端（sha/diff/pending） | ✅ |
| T1.6 | 确定性检查注册表（command/coverage/evidence） | ✅ |
| T1.7 | OPA + fallback 双引擎 + `tram gate run` | ✅（本机无 OPA 自动降级；CI 装 OPA 跑一致性测试） |
| T1.8 | 范围基线 + Intent Guard（含 dotfile 回归修复） | ✅ |
| T1.9 | worktree 沙箱（默认）+ Claude Code 适配器 + fake runner | ✅（docker 沙箱已落地，见第五批） |
| T1.10 | 状态机（阶段推进 + 纠偏轮次上限） | ✅ `tram run` 治理主线状态机（LangGraph 包装 + 零依赖解释器，行为一致） |
| T1.11 | HITL 门（baseline approve / cr approve / cr reject） | ✅ |
| T1.12 | 工件生成器 + 6 类模板（charter/scope_baseline/wbs/schedule/quality_plan/risk_register） | ✅ 证据 frontmatter + 确定性渲染 + 风险自动派生 |
| T1.13 | CR 流程（拦截自动建档 → 裁决 → 基线 v+1 → 门联动） | ✅ |
| T1.14 | demo 端到端冒烟（smoke.sh：init→G0→G1→拦截→CR→门红→驳回→门绿→回放） | ✅ |
| U1–U3 | 只读线路图薄版 | ✅ design tokens + FastAPI/SSE 只读 API + SVG 主视图（Trammy 滑行/信号灯/车票打孔/CR 支线/夜间模式/断线自愈） |

**U1–U3 实现说明（对 D8 的偏差）**：薄版采用无构建 vanilla JS + SVG，而非 React+Vite——零 Node 工具链、静态资源直接进 wheel、`tram ui` 完全离线。设计 token 独立于 tokens.css，Phase 2 React 版直接复用该文件与全部视觉规范。

验证：`ruff check` 全绿；pytest 75 passed + 1 skipped（OPA 对比测试，CI 有 OPA 时运行）；smoke.sh 全流程真人可见（含 EVM 越界入险 + KPI 仪表）；`tram ui` 实测 serve 页面 / state / artifacts / SSE 均正常。

## 7. UI 设计基调（D7/D8 已确认）

**产品纲领（2026-09 定版）**：CLI 命令行模式是基础能力，友好便捷直观的 UI 交互体验才是立命之根本。UI 全功能不等于绕过治理——每个按钮都长在铁轨上：UI 动作派发到与 CLI 相同的确定性服务层（`tram/operations.py`），写 state、写事件流、过 Intent Guard，入口 source（`tram.ui`）如实记账。治理红线不变，能力必须对齐 CLI。

「把治理过程画成一张有轨电车线路图」：过程组=5 站，电车位置=当前阶段，信号灯=门禁（绿过/红阻/橙待人审），工件=车票（证据=打孔，缺孔=虚线 evidence-gap），CR=绕行支线，SPI/CPI=司机仪表盘，事件回放=行车记录仪。视觉：米纸底 #FAF6EE + 五站马卡龙色（杏黄/薄荷/雾蓝/淡藤/藕粉），圆头粗轨道线，贴纸徽章，克制动效，`prefers-reduced-motion` 全程尊重。吉祥物 Trammy 只出现在空态/引导/庆祝。原则：隐喻唯一、红灯必须红得清楚、证据可见、UI 无任何绕过门禁的按钮（审批也写事件流）。

## 8. Phase 2 进展与预告

**已完成（第一批）**：EVM 引擎（`tram metrics/evm.py`，PV=Σest / EV=done 的 est / AC=Σspent，纯确定性）；`tram task points` / `tram agent run --points`（完成任务自动 spent=est）；`tram evm snapshot|show`（快照落 `.tram/artifacts/evm/`，阈值 spi∈[0.85,1.15]、cpi≥0.9 可配，越界即自动入风险册 `r-evm-<日期>-<指标>`，同日重跑幂等，trigger 指向 EVM 事件 seq）；`tram kpi`（门禁 MTTR=同门禁 fail/blocked→下次 pass 的时长，返工率=有返工的完成任务/完成任务，均从黑匣子与 state 确定性计算）；UI 仪表盘通电（/api/state 增 `evm` 字段，SPI/CPI 仪表盘实时显示，越界红色告警）；`tram status` 增 EVM 行。

**已完成（第二批：QA 复现-修复闭环）**：`tram qa fail <task>`（rework_count+1 并自动创建返工任务，`rework_of` 链、est 点数结转，QA_FAILED 事件带 task/rework_task refs）；`tram qa pass <task>`（返工闭环，QA_PASSED 事件）；`tram agent run --task T-XXX`（挂到既有返工任务而非新建）；缺陷 MTTR=qa_failed 到对应返工任务 qa_passed 的时长（按返工任务 id 配对，与门禁 MTTR 共用同一通用配对引擎）；`tram kpi` 增「MTTR 缺陷」段。**PM/QA/Dev 提示词分离**：`.tram/roles/{pm,qa,dev}.md`（init 装入、项目可自定义，包内模板兜底），`tram agent run --role <role>` 以 Jinja2 渲染（注入 task_id/task_prompt），角色记入 AGENT_RUN_STARTED。**逃逸率**：修复验证通过后又复发的缺陷 / 全部缺陷（qa pass 后同上游任务再 qa fail 即计一次逃逸，确定性口径）。

**已完成（第三批：UI 完整版第一增量）**：风险气象台（未关闭风险按 P×I 定天气：多云/阵雨/雷雨，trigger 事件 seq 可查）；行车 KPI 卡（门禁 MTTR / 缺陷 MTTR / 返工率 / 逃逸率，悬停看分主体明细）；站台审批页（只读列出所有待人工动作：基线待批、门禁升级待人审、CR 绕行待审、EVM 越界，各附对应 CLI 命令——UI 无任何绕过门禁的按钮，审批一律回 CLI 写事件流）。

**已完成（第四批：治理主线状态机，T1.10）**：`tram run`——沿主线连续过门禁：绿灯推进相位、红灯/升级即停（整改轮次由 GateRunner 计数，HITL 停车如实呈现，如 g3 的 release 人工放行）。`tram approve release`（HITL：放行后 `tram run` 即达终点站）。编排层 `tram orchestration/graph.py`：节点只依赖 GateRunner 与纯函数（确定性优先，LLM 不在决策路径），装了 langgraph（`pip install 'tram[orchestration]'`）用 StateGraph 编排，没装由内置解释器行走同一套节点/路由函数，行为逐分支一致（有等价性测试）。

**已完成（第五批：docker 沙箱）**：`tram agent run --sandbox docker`——agent 命令经 `docker run` 进容器执行（worktree bind-mount，`--pull never` 不偷偷拉镜像，`docker_image` 可配），Intent Guard / Tram 提交 / 事件流留在宿主机：容器管执行隔离、git 管变更隔离（R3 落地）。适配器拆出 `build_cmd`/`parse`，任何满足该形状的 runner 都能被沙箱包装。

**已完成（第六批：UI 审批直连事件流）**：HITL 审批收敛为一条共享代码路径 `governance/approvals.py`（baseline / release / CR 裁决，CLI 与 UI 共用，入口 source 如实记录 `tram.cli.*` / `tram.ui`）；UI 默认仍只读，`tram ui --approve` 开启站台审批——站台上每条待人工事项出现署名表单（by 必填），放行写的是与 CLI 完全相同的事件流记录，不是绕过门禁的按钮；写模式带双 CSRF 防护（每会话令牌 + Origin 校验），共享环境不开启即保持只读。

**D8 补充评估（React 版，2026-09）**：薄版 vanilla（无构建、零 Node 工具链、静态资源直接进 wheel、完全离线）在 UI 进入全功能化（调度台/审批直连）后依然稳定——写路径收敛在一个 `callAction` 派发器里，app.js 约 700 行；React+Vite 的收益（组件化、TS 类型）要到「多页面 + 多人协作 + 状态更复杂」才会兑现，而成本（构建链、CI、打包）立刻发生。**结论：暂缓迁移**，保持 vanilla + tokens.css 单一视觉源；文件/会话模块落地时按需拆 ES modules（仍是零构建）；触发重评的阈值：app.js 超 ~1200 行、或出现需要路由的多页需求、或 2+ 人同时改前端。

**已完成（第七批：PR review 流，D5 落地）**：`tram cr link <id> --pr <N>`（repo 从 origin remote 解析，关联落 CR 文件）+ `tram cr sync <id>`——拉取 GitHub PR reviews，确定性映射为裁决（每人取最新一条；有 changes_requested 即驳回，否则有 approved 即批准；只有 commented 不算决定），裁决仍走 approvals 同一条写账路径，`source=tram.github.pr` 记录"裁决来自 PR review"、by 记 reviewer。gh 只是数据源，不进决策逻辑；UI 站台的 CR 行显示关联 PR 并提示 `cr sync`。

**已完成（第八批：lite 域展开）**：procurement CR-lite——Intent Guard 拦截时确定性分类（`classify_violations`：越界路径命中依赖清单即 procurement，否则 scope），拦截面板显示 CR 类型，procurement CR 批准同样并入基线（闭环）；stakeholder 审批人配置——`tram.yaml` 增 `approvers`（baseline/release/cr 三 kind → 署名名单），服务层统一校验（`ApproverNotAllowedError`，CLI 门红 / UI 403），未配置不限制（向后兼容）。

**已完成（第九批：调度台，纲领落地第一步）**：共享服务层 `tram/operations.py`——CLI 动词与 UI 按钮派发同一组确定性服务（gate.run / flow.run / guard.check / task.points / qa.fail / qa.pass / artifact.generate / evm.snapshot / baseline.save），全部写 state + 事件流；UI 增调度台面板（`tram ui --approve` 解锁）：全线运行、G0–G3 单门重跑、Guard 检查、开票、EVM 快照、基线编辑器（schema 校验在服务层、批准仍走站台审批）、行车记录仪控制台（每个动作的结果逐行叙述）；任务板（点数直改 + QA ✓/✗ 闭环，返工链自动建档）；API `POST /api/action`（动词派发，与 /api/approve 共享写模式三道闸：开关/令牌/Origin）+ `GET /api/baseline`；`tram ui` 的 `--approve` 语义从"仅审批"升级为"写模式"（UI 能力对齐 CLI）。第一批寓意动画：到站颠簸（换相位 Trammy 弹跳）、信号翻灯（门禁状态变化灯球放大闪一下）、按钮按压反馈、行驶中按钮摇晃、flow.run 行车记录逐行打出，全部尊重 `prefers-reduced-motion`。

**后续批次（UI 全功能化路线）**：
- **第十批 · 文件管理**：`/api/files` 列目录/读文件/存文件（限 repo 根内，写前过 Intent Guard 预检），UI 文件树 + 编辑器；UI 的人工编辑对 Guard 可见（与 agent 修改同一套越界判定），不自动 commit。
- **第十一批 · 引擎会话**：claude 适配器会话支持（--resume / session_id 提取 / 流式输出），jobs + SSE；会话一律经 worktree/docker 沙箱 + Intent Guard；openhands 适配器在此批次调研落地。
- **第十二批 · 动画精修**：车票打孔、CR 支线岔道、终点站庆祝等隐喻动画全量打磨。
- 拆分触发点：文件/会话模块进主包时 app.js 拆 ES modules（零构建不变）。

## 9. 风险登记（项目自身）

R1 治理过重拖慢 Agent → 阈值可配、检查可缓存；R2 LLM 结构化不可靠 → schema 校验 + 确定性回查；R3 worktree 隔离弱于容器 → ✅ docker 沙箱已落地（`--sandbox docker`）；R4 OPA 安装摩擦 → fallback 引擎同语义；R5 工时噪声 → 用任务点数；R6 纠偏死循环 → 3 次上限 + 人类升级。
