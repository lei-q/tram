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
| D5 | HITL 形态 | MVP 用 CLI + approval 记录；Phase 2 接 PR review 流 |
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

integration/scope/schedule/cost/quality/communication/risk = full；resource/procurement/stakeholder = lite。lite 域 Phase 2 起按需展开（采购=依赖引入 CR-lite；干系人=审批人配置）。

## 5. 架构（模块即边界，未来即服务边界）

```
CLI (Typer) ─→ TramContext(repo/config/state/events/git)
                ├─ governance/  checks(确定性) + policy_engine(OPA|fallback) + intent_guard + gate_runner
                ├─ orchestration/  阶段推进纯函数（LangGraph 包装待接）
                ├─ adapters/   AgentRunner 协议：fake ✅ / claude-code ✅（/openhands 预留）
                ├─ sandbox/    WorktreeSession ✅（docker 预留）
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
| T1.9 | worktree 沙箱（默认）+ Claude Code 适配器 + fake runner | ✅（docker 模式预留配置，未实现） |
| T1.10 | 状态机（阶段推进 + 纠偏轮次上限） | 🔶 纯函数版已用；LangGraph 包装 Phase 2 接 |
| T1.11 | HITL 门（baseline approve / cr approve / cr reject） | ✅ |
| T1.12 | 工件生成器 + 6 类模板（charter/scope_baseline/wbs/schedule/quality_plan/risk_register） | ✅ 证据 frontmatter + 确定性渲染 + 风险自动派生 |
| T1.13 | CR 流程（拦截自动建档 → 裁决 → 基线 v+1 → 门联动） | ✅ |
| T1.14 | demo 端到端冒烟（smoke.sh：init→G0→G1→拦截→CR→门红→驳回→门绿→回放） | ✅ |
| U1–U3 | 只读线路图薄版 | ✅ design tokens + FastAPI/SSE 只读 API + SVG 主视图（Trammy 滑行/信号灯/车票打孔/CR 支线/夜间模式/断线自愈） |

**U1–U3 实现说明（对 D8 的偏差）**：薄版采用无构建 vanilla JS + SVG，而非 React+Vite——零 Node 工具链、静态资源直接进 wheel、`tram ui` 完全离线。设计 token 独立于 tokens.css，Phase 2 React 版直接复用该文件与全部视觉规范。

验证：`ruff check` 全绿；pytest 75 passed + 1 skipped（OPA 对比测试，CI 有 OPA 时运行）；smoke.sh 全流程真人可见（含 EVM 越界入险 + KPI 仪表）；`tram ui` 实测 serve 页面 / state / artifacts / SSE 均正常。

## 7. UI 设计基调（D7/D8 已确认）

「把治理过程画成一张有轨电车线路图」：过程组=5 站，电车位置=当前阶段，信号灯=门禁（绿过/红阻/橙待人审），工件=车票（证据=打孔，缺孔=虚线 evidence-gap），CR=绕行支线，SPI/CPI=司机仪表盘，事件回放=行车记录仪。视觉：米纸底 #FAF6EE + 五站马卡龙色（杏黄/薄荷/雾蓝/淡藤/藕粉），圆头粗轨道线，贴纸徽章，克制动效，`prefers-reduced-motion` 全程尊重。吉祥物 Trammy 只出现在空态/引导/庆祝。原则：隐喻唯一、红灯必须红得清楚、证据可见、UI 无任何绕过门禁的按钮（审批也写事件流）。

## 8. Phase 2 进展与预告

**已完成（第一批）**：EVM 引擎（`tram metrics/evm.py`，PV=Σest / EV=done 的 est / AC=Σspent，纯确定性）；`tram task points` / `tram agent run --points`（完成任务自动 spent=est）；`tram evm snapshot|show`（快照落 `.tram/artifacts/evm/`，阈值 spi∈[0.85,1.15]、cpi≥0.9 可配，越界即自动入风险册 `r-evm-<日期>-<指标>`，同日重跑幂等，trigger 指向 EVM 事件 seq）；`tram kpi`（门禁 MTTR=同门禁 fail/blocked→下次 pass 的时长，返工率=有返工的完成任务/完成任务，均从黑匣子与 state 确定性计算）；UI 仪表盘通电（/api/state 增 `evm` 字段，SPI/CPI 仪表盘实时显示，越界红色告警）；`tram status` 增 EVM 行。

**已完成（第二批：QA 复现-修复闭环）**：`tram qa fail <task>`（rework_count+1 并自动创建返工任务，`rework_of` 链、est 点数结转，QA_FAILED 事件带 task/rework_task refs）；`tram qa pass <task>`（返工闭环，QA_PASSED 事件）；`tram agent run --task T-XXX`（挂到既有返工任务而非新建）；缺陷 MTTR=qa_failed 到对应返工任务 qa_passed 的时长（按返工任务 id 配对，与门禁 MTTR 共用同一通用配对引擎）；`tram kpi` 增「MTTR 缺陷」段。**PM/QA/Dev 提示词分离**：`.tram/roles/{pm,qa,dev}.md`（init 装入、项目可自定义，包内模板兜底），`tram agent run --role <role>` 以 Jinja2 渲染（注入 task_id/task_prompt），角色记入 AGENT_RUN_STARTED。**逃逸率**：修复验证通过后又复发的缺陷 / 全部缺陷（qa pass 后同上游任务再 qa fail 即计一次逃逸，确定性口径）。

**已完成（第三批：UI 完整版第一增量）**：风险气象台（未关闭风险按 P×I 定天气：多云/阵雨/雷雨，trigger 事件 seq 可查）；行车 KPI 卡（门禁 MTTR / 缺陷 MTTR / 返工率 / 逃逸率，悬停看分主体明细）；站台审批页（只读列出所有待人工动作：基线待批、门禁升级待人审、CR 绕行待审、EVM 越界，各附对应 CLI 命令——UI 无任何绕过门禁的按钮，审批一律回 CLI 写事件流）。

**待做**：UI 完整版剩余（React 版评估、审批动作直连事件流的形态）；LangGraph 包装（T1.10）；docker 沙箱。

## 9. 风险登记（项目自身）

R1 治理过重拖慢 Agent → 阈值可配、检查可缓存；R2 LLM 结构化不可靠 → schema 校验 + 确定性回查；R3 worktree 隔离弱于容器 → 本机场景可接受，共享环境建议 docker；R4 OPA 安装摩擦 → fallback 引擎同语义；R5 工时噪声 → 用任务点数；R6 纠偏死循环 → 3 次上限 + 人类升级。
