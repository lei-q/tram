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
                ├─ adapters/   AgentRunner 协议：fake ✅ / claude-code ✅ / openhands ✅
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

**已完成（第十批：文件车厢）**：`/api/files`（列目录，目录优先、`.git` 不展示，标注 pending changes 的 ● ）+ `GET /api/file`（读文件：文本/二进制判定、512KB 截断、并返回该路径的基线归属——打开文件就亮「轨内 ✓ / 越界 ⚠」信号灯）+ `POST /api/file`（保存，写模式三道闸 + 署名必填）。服务层 `operations.file_list/file_read/file_save`：路径钉死在 repo 根内（拒绝对路径/`..`/越界 symlink）；`.tram/`（事件证据）与 `.git/`（历史）结构保护谁都不能写（`ProtectedPath`）；越界保存走与 `tram guard check` 同一条 `guard_check`——拦截内容不落盘 + INTENT_BLOCKED/CR_CREATED 自动立案（依赖清单越界同款 procurement 分类），扩基线（调度台基线编辑器）后重存即过——铁轨闭环。UI 文件车厢：面包屑目录树 + 编辑器 + 保存；`pending_changes` 解析改 `-uall`（untracked 逐文件列出，不再折叠成目录，Guard CLI 同款受益）。不自动 commit——提交仍是显式的治理动作。

**已完成（第十一批：会话车厢）**：UI 直接与代码生成引擎多轮对话，全程治理铁轨。适配器层：`TaskSpec` 增 `session_id`/`resume`——claude `--session-id`（新建）与 `--resume`（续聊），result 事件的 `session_id` 回收入库即句柄；`StreamingRunner` 协议 + 共享 `fold_stream`（assistant.tool_use→轨迹、result→终态），`stream()` 逐事件 yield、`run()` 消费同一事件流，流式与阻塞语义同源。服务层 `tram/chat.py`：会话 = TaskRecord + 常驻 worktree（`tram/chat-XXXX` 分支）+ 引擎句柄，注册表落 `.tram/chat/sessions.json`；每条消息一个 job（线程 + MAX_JOB_LINES 限幅），SSE `/api/chat/stream` 逐行推送、`after` 游标增量拉取；首条消息立任务（AGENT_RUN_STARTED source=tram.chat），每轮跑完过与 `tram agent run` 同一条 Intent Guard——轨内改动由 Tram 提交（commit_refs 留痕）、越界拦截 + 自动立案 procurement CR；收车语义：干净 worktree 移除、脏 worktree 永不销毁（未审工作），任务 DONE、返工/QA 另走闭环。UI 会话车厢：引擎选择（claude/fake）、会话列表、流式对话窗（text/tool/result/error 四种行）、收车按钮；写模式三道闸同款（令牌/署名/Origin）。API：`GET|POST /api/chat/sessions`、`POST /api/chat/send`、`POST /api/chat/sessions/{sid}/close`、`GET /api/chat/jobs/{id}`。

**已完成（第十三批：openhands 适配器）**：`adapters/openhands.py` 落地 `OpenHandsRunner`，线格式拆 pypi 轮子核实（openhands 1.16 CLI + openhands-sdk 事件定义）：入口 `openhands`，headless 模式 `openhands --headless --json -t "<task>"`，stdout 逐行吐 SDK Event 的 `model_dump()`、每事件带 `kind`=类名（MessageEvent / ActionEvent / ObservationEvent / AgentErrorEvent / ConversationErrorEvent / ConversationStateUpdateEvent…）；会话句柄是 conversation id，从 `ConversationStateUpdateEvent(key="id")` 捡、续聊 `--resume <id>`（新建会话不能预置 id，与 claude 的 `--session-id` 不同）。适配器是纯翻译层：OpenHands 词汇 → 与 claude 同一套统一事件词汇（assistant/result/error），`fold_stream` 与会话车厢 `ui_line` 零改动；OpenHands 没有 result 事件——进程退出即终局，适配器合成统一 result（AgentError 工具级错误亮行不终局；ConversationError / 非零退出终局 error）。已接入会话车厢 ENGINES + UI 引擎下拉。契约测试用真实字段样例 + heredoc stub 二进制走真实 Popen 管道，不依赖引擎安装。顺带修正：chat 引擎 run 失败（status=error）不再走 Guard 收尾误报 ok，按 error 落账（AGENT_RUN_FINISHED error、不立案）。待真机验证：`--resume`+`-t` 的 seed 行为、ObservationEvent 的 UI 呈现——第十四批活体补记。

**已完成（第十二批：动画精修）**：寓意动画全量补齐，全部只在状态真变化时放一次、全部尊重 `prefers-reduced-motion`。车票打孔：任务 → done 票面留检票圆孔（历史 done 常驻孔印、票面文字淡化），刚验票的放印章落孔动画；CR 支线岔道：新 CR 立案瞬间虚线支线向前流一段（stroke-dashoffset 行进）+ 绕行气泡弹性弹出；终点站庆祝：收尾站 + 任务全清时 Trammy 欢快摇摆、14 张站点色车票彩带徐徐落下（animationend 自清扫，一次到站只庆一次，离开收尾站自动复位）；检票孔/岔道/庆祝动画收尾统一走 animationend 摘类，与既有的到站颠簸、信号翻灯、按钮按压、行车记录仪逐行打印同一套节拍。

**活体验证（第十四批，进行中）**：本机 venv 装 openhands 1.16.0，无 LLM 配置实测 `--headless --json`。三个真实契约点：(1) **stdout 混有人类可读行**——Rich banner、`Headless mode requires existing settings.` 拒绝提示、`Goodbye!`、`Conversation ID: …` hint 都走 stdout，与 JSONL 交替；适配器 `_parse_json` 只吃 JSON 行，其余当噪音，已钉测试（`test_stream_tolerates_non_json_preamble`）。(2) **headless 前置条件**：必须先交互式跑一次 `openhands` 配好 LLM 才能用 headless——live 全链路验证卡在这一步，需要司机提供 API key 配置；(3) spawn 时带上 `OPENHANDS_SUPPRESS_BANNER=1` + `OPENHANDS_DISABLE_ANALYTICS=1` 压噪音。待续：配置 LLM 后跑通完整一轮（ObservationEvent 呈现、`--resume`+`-t` seed 行为）；claude 引擎活体跑通 SSE 全链路。

**已完成（第十五批：驾驶舱改版）**：瀑布流排版推倒重来做驾驶舱——一屏到底不滚屏，操作分主次。骨架 `.shell` 五行 grid（`100dvh`）：顶栏（列车长身份 + 阶段/基线 chip + **司机署名输入**，driverName 全舱共用一份落款）→ 仪态条（仪表 + KPI 从方块卡变灯珠横排，一眼扫全）→ 主甲板（左列：风挡线路图 2fr + **主驾席会话车厢 3fr**——最大连续区域给主操作；右轨：调度台按钮排成 3 列仪表阵 + 站台审批（带待办角标，flex 内滚）+ 行车记录仪）→ 底舱 tab（文件车厢/任务板/车票·绕行/风险气象/调度日志 五面板切换，各自内滚）。基线编辑器改模态浮层不再挤布局；SVG viewBox 裁掉上下空白（0 40 920 176）让风挡在矮舱里更满；`[hidden]!important` 兜底；窄屏（<920px）优雅退化为可滚堆叠。全部 38 个 JS id 挂点原位保留，渲染层零改动——只有 tab 切换与角标两处新 JS。

**已完成（第十六批：知识域全覆盖）**：三处补课，把 PMBOK 骨架焊进产品。(1) 线路图：每站（过程组）下挂子过程轨道——`governance/domains.py` 单一词表（十大知识域徽记 + 各过程组裁剪版子过程，十大域全覆盖），`/api/domains` 供 UI 渲染，站台挂线 + 单字徽章胶囊 + 子过程名 + 底部十域图例；风挡/主驾席改对半分。(2) 文件车厢：`classify_path` 确定性归属（.tram 证据→整合·管理项目知识；依赖清单→采购·实施采购；tests→质量·管理质量；CI→质量·控制质量·监控组；docs/md→沟通；scripts/工具链→资源；默认产品工作→整合·指导与管理工作），逐文件带域徽章（悬停看全称），新增「🧭 按知识域」分组视图（`/api/files?flat=1` 全仓平铺，跳过 .git 与 .tram/worktrees），与「📁 按目录」一键切换。(3) 会话车厢治理前导：每条消息的 prompt 前面拼 `[Tram 治理上下文]`——项目/会话/任务/当前过程组、worktree 分支、五过程组×十大知识域工作流、G0–G3 由 Tram 掌管不可自改、基线轨内/禁止路径清单、越界拦截与自动立案语义、在途任务与未决 CR 数——纯确定性拼接，引擎从此知道自己在铁轨上（回测：录音 runner 断言 prompt 含全部要素）。

**已完成（第十七批：线路图融合调度台）**：五连修。(1) 署名前置拦截：callAction 统一先查司机署名，空署名不再放行到后端 422——顶栏司机框脉冲提醒 + 聚焦 + toast。(2)(3)(5) 布局重构：**调度能力长在线路图上**——点 G0–G3 信号灯即 gate.run、点 Trammy 即全线运行（flow.run），风挡头只剩一条紧凑按钮带（Guard/开票/EVM/基线）；右轨整体撤销，主甲板改单列全宽——风挡（33vh）+ 会话车厢（剩余全部），会话日志区显著变大；站台审批与行车记录仪移入底舱 tab（审批 tab 带待办角标），行车记录与事件流合并成「行车记录」面板；底舱收窄（20vh）让出主甲板；子过程轨道放宽（行距 17、字号上调、图例间距加大）。(4) 聊天历史持久化：`.tram/chat/<sid>/log.jsonl` 逐行落盘（me/引擎行/错误），刷新页面自动还原（选会话/启动时拉 `/api/chat/sessions/{sid}/log`），**默认永远保留**，唯一清除途径是「🧹 清空」按钮（DELETE 同端点，三道闸 + 署名 + confirm）。修掉一个隐藏 bug：ui_line 返回单 dict 时 `_tap` 直接迭代会把 key 当行。

**已完成（第十九批：会话并线）**：补上「沙箱产出 → 主线」的最后一公里。`ChatService.merge_session`：分支变更集（merge-base..branch）先过 Intent Guard——越界照章立案 CR（source=tram.chat.merge，站台审批放行/扩基线后重试即过）；主工作区**已跟踪**文件有未提交改动则拒绝（未跟踪不算：.tram/ 账本、init 写的 .gitignore 是治理层自己的落盘，碰撞由 git 兜底）；`git merge --no-ff` 入主线（TRAM_IDENTITY 落款），冲突即 `--abort` 并报「请手动解决」；成功记 `SESSION_MERGED` 事件 + 合并提交挂任务 commit_refs，进 REFRESH_KINDS 全舱刷新。API `POST /api/chat/sessions/{sid}/merge`（三道闸 + 署名；ValueError→404/400、MergeRefused→409）；UI 会话栏「🔀 并线」按钮（confirm → 结果三种呈现：并线成功/越界立案/无产出）。

**已完成（第二十批：WBS 锚定 + 跨会话重叠预警 + 自动驾驶）**：三项落地。(1) **WBS 锚定**：`.tram/wbs.yaml` 工作包（PM 规划工件，{id,title,paths}，schema 坏了报错不静默降级）；会话开/发消息可带 `wbs_package`（UI 会话栏下拉），任务落锚点字段；并线时锚定校验——项目定义了工作包而会话未锚定 → 拒（unanchored），产出越出工作包交付范围 → 拒（anchor_mismatch + outside 清单）；无工作包定义 = 自由模式放行（结果标 anchor: free），渐进收紧不砸存量。(2) **跨会话重叠预警**：并线结果带 `overlaps`——其他在途会话分支也改了相同路径（提示不拦截，UI 红字提醒冲突风险）。(3) **自动驾驶**（`tram/autopilot.py`，JEV 讨论后定型为决策表哑司机——无 LLM 判定）：`route_reasons` 确定性路由（awaiting human/open CR → 锚点硬停；missing artifacts/charter → 自动开票；failed checks → 孵整改会话修 G2 红灯→并线自产会话→重试）；四锚点永不自动（基线批准/CR 裁决/release 放行/升级待人审）；护栏 = 干跑（探门留痕零动作）+ 急停文件 `.tram/autopilot-stop` + 步数预算（默认 10）；每个动作 `AUTOPILOT_STEP` 事件（source=tram.autopilot）。CLI `tram autopilot [--dry-run --max-steps --engine]`；API `POST /api/autopilot`（三道闸+署名）；UI 调度条 🤖 按钮（confirm 提示四锚点与急停方法）。

**已完成（第二十一批：对话控制与知识沉淀）**：(1) **司机急停**：⏹ 按钮终止本轮引擎进程——ChatJob 带 `threading.Event`，三个适配器 stream() 收 `stop_event`（stdout 循环逐行检查，命中即 `proc.terminate()`）；本轮终态 `stopped`：不进 Guard、无提交、不沉淀知识，`AGENT_RUN_FINISHED(status=stopped)` 留痕；终态 job 再停 409；UI 发送中亮停止键、done 显示停止说明。（2) **多行输入**：input 换 textarea，Enter 发送 / Shift+Enter 换行，自动长高三行封顶内滚，发送后收起。(3) **知识沉淀**（整合·管理项目知识子过程落地）：每轮跑完确定性抄录三本账到 `.tram/knowledge/`——`requirements.md`（用户消息即需求陈述，带会话/任务/时间戳）、`changes.md`（有提交才记：commit + 逐路径子过程归属 + 知识域汇总）、`risks.md`（越界拦截才记：CR 与裁决指引）；纯事实抄录不解读——语义提炼留给人，铁轨只保证账不漏。

**已完成（第二十二批：环线与巡检）**：治「过程组被画成顺序阶段」的瀑布残余。(1) **环线模型**：`ProjectState.iteration`（第 N 圈）；`tram lap next`（UI ↺ 下一圈 / API `lap.next`，署名）在**收尾站折返**——iteration+1 回规划站，带着上一圈完整的账（需求/风险/变更/EVM）重新规划，`PHASE_CHANGED(data.lap=true)` 留痕；别处发车被拒（终局收车仍走 G3 release，二选一语义清晰）。阶段 chip 显示「第 N 圈」。(2) **监控乘务化**：`tram monitor`（UI 👁 巡检 / `monitor.sweep`）= EVM 快照（**当天幂等**，重复巡检不刷屏）+ Guard 核验（越界照章立案）+ 风险概览 + 需求对账——监控从「执行与收尾之间的车站」变成随车乘务，随时点随时巡。

**已完成（第二十三批：风险闭环与需求对账）**：(1) **越界拦截入风险登记册**：两条 Guard 路径（`tram guard check` 与会话消息）拦截时除立案 CR 外，同时登记 `r-guard-<cr>` 风险（P×I=9 风暴级，同 CR 幂等）——风险册与知识账（risks.md）双轨。(2) **风险状态流转**：`tram risk list/resolve`（UI 气象台行内 👀观察/✓关闭 按钮，`risk.resolve` 动词署名），`RISK_RESOLVED` 事件；snapshot 增 `storm_risks`（P×I≥9 未决数）。(3) **需求对账（渐进明细的机械落点）**：`requirement_gaps` ——项目定义了 WBS 工作包时点名未锚定的在途任务；进巡检输出与站台审批提醒（「N 个在途任务未锚定工作包——渐进明细没跟上」）。

**已完成（第二十四批：环线线路图）**：风挡从直线换轨成**赛道形环线**——隐喻与第二十二批的环线模型对齐（过程组每圈重复）。(1) 轨道 = 闭环 path + **行进方向层**：流动虚线顺时针 marching（`railflow` 2.6s/循环）+ 5 个沿切线转角的 ▸ 箭头（动画之外静态可读；respect reduced-motion）。(2) **里程标**：8 根外挑小柱 K0–K7 沿环均布。(3) **子过程节点上轨道**：每站的子过程在站→门之间的轨道上排成珠子（悬停看「过程组·知识域·子过程」全称），**当前阶段的珠子放大发光并显名**、其余收暗（renderState 每次刷新重亮）；旧的站下文字列移除，十域图例移到环下方。站点/门禁/Trammy 全部改为 `getPointAtLength` 参数化落位（外法线方向长标签/灯/车身），CR 支线改为环内捷径（执行→监控弦线）；confetti/终点庆祝坐标适配新 viewBox。

**已完成（第二十五批：风景双轨图）**：风挡升级为**双轨两层**（viewBox 0 30 920 460）。(1) **沿途风景背景**：淡雅卡通层永久垫底——暖阳、三朵软云（缓慢漂移动画）、两层远山（薄荷/淡藤 macaron 渐弱）、四棵棒棒糖树，全部 pointer-events:none 不挡调度。(2) **直线主线**（上半）：5 过程组站 + G0–G3 可点信号灯 + **K0–K7 里程标**（站间双柱、与站门错开）+ Trammy 行驶 + CR 支线浅弧。(3) **细节环线**（下半）：**当前阶段的放大镜**——该阶段的知识域子过程节点均布整圈（每颗独享 ~300px 弧长，徽章+名称沿外法线长出并按方位定锚，标签永不重叠）；环心大字显示阶段名与圈数；流动虚线方向动画与 ▸ 箭头保留在环上。词表缓存自 /api/domains，阶段一换 renderState 重画环线内容。

**纲领再定义（2026-09-23，第二十六批起生效）**：Tram 不是流程控制，是为项目最终成功交付**保驾护航**。类比：为了让列车稳稳抵达终点——提前或实时收集行驶数据与信息、为即将遭遇的风险提前预警、对可能导致脱轨的行为主动纠偏；**它本身不驾驶列车，只为驾驶员提供引导和决策**。已有能力据此归位：风险预警（气象台/巡检）、纠偏（Intent Guard/CR）是护航本分；「自动驾驶」应降级为领航建议（待下一批改造）。核心价值回路 = 会话对话 → 自动收集提炼各过程组项目文件 → 执行中按实际变动持续更新 → AI 知识库 → 下轮对话按需入上下文 → 循环往复。

**已完成（第二十六批：护航知识库）**：核心价值回路落地。(1) **项目文件白名单**（`knowledge.DOC_GROUPS`）：01-initiating（项目建议书/章程）、02-planning（项目管理/范围/进度/成本管理计划、范围/进度/成本基准、风险登记册）、04-monitoring（变更日志/进度报告）——提炼器只能写这些名字，越权文件名整段丢弃。(2) **确定性收集**：每轮跑完 `refresh_monitoring`（变更日志引变更账尾部、进度报告写阶段/任务/EVM/风险事实）。(3) **引擎提炼**（`tram knowledge` / UI 📚 提炼 / `knowledge.distill` 动词 / **收车自动触发**——inline 同步、服务端后台线程不阻塞收车）：把现有草稿+最近对话+变更/风险账喂给引擎，`=== FILE: 名 ===` 分段输出蒸馏内容；LLM 只产内容，白名单与落盘由 Tram 控制，`KNOWLEDGE_DISTILLED` 事件留痕。(4) **按需加载**：`digest()` 节选章程/风险册/进度报告/变更日志（总量 2200 字封顶）拼进下轮对话前导——循环回路闭合。测试教训：close 测试曾摸到真 claude（会话 engine 字段 vs 换装实例是两回事），autouse 换 FakeRunner 钉死。

**后续批次（UI 全功能化路线）**：
- **第二十七批候选 · 领航员改造**：自动驾驶降级为建议模式（只报下一动作不执行），全面对齐护航者定位。
- 拆分触发点：文件/会话模块进主包时 app.js 拆 ES modules（零构建不变）。

## 9. 风险登记（项目自身）

R1 治理过重拖慢 Agent → 阈值可配、检查可缓存；R2 LLM 结构化不可靠 → schema 校验 + 确定性回查；R3 worktree 隔离弱于容器 → ✅ docker 沙箱已落地（`--sandbox docker`）；R4 OPA 安装摩擦 → fallback 引擎同语义；R5 工时噪声 → 用任务点数；R6 纠偏死循环 → 3 次上限 + 人类升级。
