# Tram 产品操作手册

> Tram 🚋 —— 面向研发的 AI Coding Agent 治理层。
> CLI 是基础能力，友好直观的驾驶舱 UI 才是日常工作的主入口；两条入口派发**同一组确定性服务、写同一条事件流**——每个按钮都长在铁轨上。

**适用读者**：在 Tram 管辖下使用 AI coding agent（Claude Code / OpenHands 等）做研发的工程师、QA、PM，以及负责流程治理的司务长。

---

## 目录

1. [Tram 是什么](#1-tram-是什么)
2. [安装与初始化](#2-安装与初始化)
3. [核心概念](#3-核心概念)
4. [第一次开车（最短路径）](#4-第一次开车最短路径)
5. [CLI 命令手册](#5-cli-命令手册)
6. [驾驶舱 UI 手册](#6-驾驶舱-ui-手册)
7. [引擎与沙箱](#7-引擎与沙箱)
8. [治理闭环场景演练](#8-治理闭环场景演练)
9. [配置参考](#9-配置参考)
10. [故障排查](#10-故障排查)

---

## 1. Tram 是什么

AI coding agent 生产力极高，但也极其容易越界：改了不该改的依赖、跳过了评审、把半成品直接怼进主线。Tram 不是又一个代码生成引擎，而是**包在引擎外面的治理层**——引擎只管生成，Tram 管路权。

| 原则 | 含义 |
|---|---|
| 过程即代码 | 治理流程（阶段、门禁、检查）都是可执行的确定性代码，不是 wiki 上的文档 |
| 工件即证据 | 每个治理动作产出落盘工件（车票），自动挂接事件证据链 |
| 门禁即信号 | G0–G3 门禁是线路上的信号灯：绿灯放行、红灯必停，状态一眼可见 |
| 确定性优先 | 判定路径（Guard、门禁、分类）永不使用 LLM——规则可预测、可审计 |
| 人类在环 | 批准基线、放行 release、裁决 CR 等关键决策必须人类署名，进事件流 |

治理的坐标系是 PMBOK 裁剪版：**五过程组**（启动 → 规划 → 执行 → 监控 → 收尾）× **十大知识域**（整合/范围/进度/成本/质量/资源/沟通/风险/采购/相关方）。线路图上每站下挂该过程组的子过程，仓库里每个文件都答得出「属于哪个过程组 · 哪个知识域 · 哪个子过程」。

---

## 2. 安装与初始化

```bash
pip install tram            # CLI
pip install 'tram[ui]'      # CLI + 驾驶舱 UI（FastAPI/uvicorn）
```

在**项目仓库根目录**初始化：

```bash
cd your-repo
tram init                   # 生成 .tram/ 治理目录
tram init --name "我的项目"  # 指定项目名；--force 可重置
```

初始化后的 `.tram/` 结构：

| 路径 | 内容 |
|---|---|
| `tram.yaml` | 项目配置（沙箱模式、镜像、EVM 阈值，见[§9](#9-配置参考)） |
| `state.json` | 项目状态（阶段、任务、CR 索引、审批记录） |
| `events/events.jsonl` | 事件黑匣子（追加式，`tram replay` 回放） |
| `scope-baseline.yaml` | 范围基线（轨内/禁止路径清单） |
| `gates.yaml` | 门禁配置 |
| `crs/` | 变更请求（CR）档案 |
| `artifacts/` | 治理工件（车票）与索引 |
| `roles/{pm,qa,dev}.md` | 角色提示词模板（可自定义，`tram agent run --role` 渲染） |
| `worktrees/` | 会话/任务沙箱的 git worktree |
| `chat/sessions.json` | 会话车厢的会话注册表 |

> 注意：要求仓库是 git 仓库。全新零提交仓库也能用——首次挂沙箱时 Tram 会自动补一个空的 bootstrap 提交落底。

---

## 3. 核心概念

### 3.1 五过程组与阶段

项目状态机沿主线推进：`initiating → planning → executing → monitoring → closing → done`。UI 风挡上的五座车站就是它们；Trammy 小电车停在当前阶段。

### 3.2 门禁（G0–G3）

| 门禁 | 位置 | 放行条件 |
|---|---|---|
| G0 章程门 | 启动→规划 | 范围基线已获人类批准（HITL） |
| G1 规划门 | 规划→执行 | 规划工件齐备（WBS/进度/质量计划/风险登记） |
| G2 质量门 | 执行→监控 | 质量检查通过（确定性检查 + 策略决策） |
| G3 收尾门 | 监控→收尾 | 需人类放行 release（HITL） |

门禁判定 = **确定性检查 + 策略引擎**。策略引擎优先 OPA（若安装），否则 fallback 引擎同语义执行——结论一致，不依赖环境。红灯连续 3 次整改不过会**升级待人审**（防纠偏死循环）。

### 3.3 范围基线与 Intent Guard

基线（`scope-baseline.yaml`）定义两条清单：

- `allowed_paths`：**轨内**——允许 agent/人改的路径（glob）
- `forbidden_paths`：**明令禁止**

Intent Guard 是越界判定器：任何改动（agent 会话、文件车厢保存、手动检查）都过同一条 `guard_check`。越界 = `INTENT_BLOCKED` 事件 + **自动立案 CR**：

- 命中依赖清单（pyproject/package.json/Cargo.toml…）→ **procurement CR**（采购域）
- 其余 → **scope CR**（范围域）

批准 scope CR 会自动把路径并入基线；扩基线后重存即过——铁轨闭环。

### 3.4 变更请求（CR）

CR 是「绕行支线」：主线不动，越界改动走审批。裁决方式三种：CLI `tram cr approve/reject`、UI 站台审批表单、**PR review 裁决**（`tram cr link` 关联 GitHub PR 后 `tram cr sync` 按 review 状态裁决）。所有裁决都要署名，进事件流。

### 3.5 事件黑匣子

每个治理动作写一条 JSONL 事件（带 seq/时间/来源/引用链）：门禁翻灯、Guard 拦截、CR 立案与裁决、审批、agent 跑批、文件保存、EVM 越界……来源标记 `tram.cli.*` / `tram.ui` / `tram.chat` / `tram.github.pr`，UI 和 CLI 谁做的账一清二楚。`tram replay` 回放，KPI 仪表也从这里算。

### 3.6 EVM 挣值与风险

任务点数（est/spent）是数据源。`tram evm snapshot` 算 SPI（进度绩效）/CPI（成本绩效）；越过阈值（可配）**自动登记风险**，进风险气象台按 P×I 定天气：☁️ 多云 / 🌧 雨 / ⛈ 风暴。

### 3.7 QA 返工闭环

`tram qa fail T-001` 登记缺陷并**自动创建返工任务**（`rework_of` 链）；返工任务挂角色提示词交给引擎修；`tram qa pass T-002` 验证闭环。缺陷 MTTR 从黑匣子算。

### 3.8 十大知识域归属

`governance/domains.py` 维护单一词表（十大域徽记「整范进成质资通险采相」+ 各过程组裁剪版子过程），三处共用：线路图子过程轨道（`/api/domains`）、文件车厢归属徽章、会话车厢治理前导。文件归属规则（先命中先得）：

| 文件 | 过程组 | 知识域 | 子过程 |
|---|---|---|---|
| `.tram/**` | 执行 | 整合 | 管理项目知识（证据） |
| 依赖清单（pyproject/lock…） | 执行 | 采购 | 实施采购 |
| `tests/**`、`**/test_*.py` | 执行 | 质量 | 管理质量 |
| `.github/workflows/**`、CI 配置 | 监控 | 质量 | 控制质量 |
| `docs/**`、`*.md` | 执行 | 沟通 | 管理沟通 |
| `scripts/**`、Makefile 等工具链 | 执行 | 资源 | 获取资源 |
| 其余（产品代码） | 执行 | 整合 | 指导与管理工作 |

---

## 4. 第一次开车（最短路径）

```bash
cd your-repo
tram init
vim .tram/scope-baseline.yaml        # 把允许 agent 改的路径写进 allowed_paths
tram baseline approve --by 你的名字   # 人类批基线（G0 前提）
tram ui --approve                    # 打开驾驶舱（写模式）
```

在驾驶舱里：顶栏填**司机署名** → 主驾席选引擎（claude code）→ ➕ 新会话 → 发第一条消息。Tram 自动：立任务 → 建常驻 worktree（`tram/chat-XXXX` 分支）→ 拼治理前导给引擎 → 流式显示 → 跑完过 Intent Guard → 轨内改动自动提交留痕。用完点 🏁 收车。

---

## 5. CLI 命令手册

> 所有写操作命令的输出都会同步写入事件黑匣子。`--by` 参数是署名，关键决策必填。

### tram init
**目的**：在当前仓库生成 `.tram/` 治理目录。
```bash
tram init [--name 项目名] [--force]
```

### tram status
**目的**：项目状态一览（阶段、门禁、CR、任务、基线审批、最近 EVM）。
```bash
tram status
```

### tram run
**目的**：开到底站——沿主线连续过门禁，绿灯推进、红灯/待人审即停。
```bash
tram run
```

### tram baseline show / approve
**目的**：查看 / 人类批准范围基线。批准是 G0 放行前提。
```bash
tram baseline show
tram baseline approve --by 你的名字 [--note "备注"]
```

### tram gate run
**目的**：跑单座门禁（确定性检查 + 策略决策）。
```bash
tram gate run g0_charter_gate   # 可选: g0_charter_gate / g1_planning_gate / g2_quality_gate / g3_closing_gate
```

### tram approve release
**目的**：G3 收尾门的人类放行（HITL）。
```bash
tram approve release --by 你的名字
```

### tram guard check
**目的**：Intent Guard——把改动路径对基线核验。不带参数默认查 git 未提交改动；越界自动立案 CR 并以退出码 1 报错（可接 CI）。
```bash
tram guard check                # 查全部 pending changes
tram guard check src/a.py vendor/x.toml
```

### tram agent run
**目的**：在沙箱里跑一次引擎任务，全程 Guard。**这是 CLI 侧调用引擎的主命令**。
```bash
tram agent run --prompt "实现登录页" --runner claude
```
常用选项：

| 选项 | 说明 |
|---|---|
| `--prompt` | 任务提示词（必填） |
| `--runner` | `fake`（离线）/ `claude` / `openhands` |
| `--sandbox` | `worktree`（默认）/ `docker`（容器执行，治理留在宿主机；目前配合 claude） |
| `--role` | `pm` / `qa` / `dev` 角色提示词模板（`.tram/roles/`） |
| `--task` | 挂到已有任务（如返工任务 T-002） |
| `--points` | 任务点数估计（EVM 数据源） |
| `--allowed-tools` / `--max-turns` | 引擎工具白名单 / 回合上限（claude） |
| `--fake-plan` / `--fake-violation-plan` | fake 引擎的写盘计划 JSON（演示/测试用） |

### tram cr list / approve / reject / link / sync
**目的**：变更请求的查看与裁决。
```bash
tram cr list
tram cr approve cr-0001 --by 你的名字 [--note "…"]   # scope CR 批准→路径并入基线
tram cr reject cr-0001 --by 你的名字
tram cr link cr-0001 --pr 42    # 关联 GitHub PR（origin 远端解析仓库）
tram cr sync cr-0001            # 按 PR review 状态裁决（需 gh CLI）
```

### tram artifact list / generate
**目的**：治理工件（车票）——确定性渲染、自动挂证据链，缺证据标 gap。
```bash
tram artifact list
tram artifact generate --all                # charter / scope_baseline / wbs / schedule / quality_plan / risk_register
tram artifact generate wbs risk_register    # 指定种类
```

### tram task list / points
**目的**：任务册与点数维护（EVM 数据源）。任务由 agent run / 会话车厢 / QA 返工自动产生。
```bash
tram task list
tram task points T-001 --est 4 --spent 6
```

### tram evm snapshot / show
**目的**：挣值快照（SPI/CPI），越界自动登记风险。
```bash
tram evm snapshot            # 可选 --day YYYY-MM-DD
tram evm show
```

### tram qa fail / pass
**目的**：QA 返工闭环。
```bash
tram qa fail T-001 --note "复现步骤…" --by qa      # 登记缺陷 → 自动建返工任务
tram agent run --task T-002 --role dev --runner claude --prompt "修复"   # 返工
tram qa pass T-002 --note "复现测试转绿" --by qa    # 闭环
```

### tram kpi
**目的**：KPI 仪表——门禁 MTTR、缺陷 MTTR、返工率、逃逸率（全部从黑匣子计算）。
```bash
tram kpi
```

### tram replay
**目的**：回放事件黑匣子。
```bash
tram replay --limit 50
tram replay --kind intent_blocked
```

### tram autopilot
**目的**：自动驾驶——决策表哑司机（无 LLM 判定），锚点之间自动推进、锚点处硬停。
```bash
tram autopilot --dry-run        # 干跑：探门看信号（事件留痕），零动作，只报下一步
tram autopilot --engine claude  # G2 红灯时孵 claude 整改会话（默认 fake）
tram autopilot --max-steps 20   # 步数预算（默认 10，防打转）
```
行为：缺工件自动开票重试；G2 测试红自动派整改会话（产出走并线正门）；**四个锚点永不自动**——基线批准、CR 裁决、release 放行、整改升级待人审，到了就停等你。急停：`touch .tram/autopilot-stop`（删除恢复）。UI 调度条 🤖 按钮同款。

### tram ui
**目的**：启动驾驶舱 UI。**默认只读**；`--approve` 开写模式。
```bash
tram ui                    # 只读线路图（默认 127.0.0.1:8417，自动开浏览器）
tram ui --approve          # 写模式：调度台/文件车厢/会话车厢/站台审批全解锁
tram ui --port 9000 --no-open --host 0.0.0.0
```

---

## 6. 驾驶舱 UI 手册

启动 `tram ui`（只读）或 `tram ui --approve`（写模式）后看到的**驾驶舱**：一屏到底不滚屏，操作分主次——主驾席给会话，次要面板收底舱。

### 6.1 写模式三道闸

`tram ui --approve` 才开放所有写操作，且每个写请求要过三道闸：

1. **开关**：服务以 `--approve` 启动（默认 403 只读）；
2. **令牌**：`/api/ui-config` 按会话发令牌，浏览器自动带上（`X-Tram-Token`）；
3. **Origin**：请求必须来自本 UI 源（防跨站）。

外加**署名闸**：QA、基线、审批、文件保存、会话消息都要司机署名（顶栏「👤 司机」输入框，全舱共用一份落款）。只读模式下调度台按钮禁用，页面顶部有解锁提示。

### 6.2 顶栏与仪态条

- **顶栏**：项目名、阶段 chip、基线 chip（`基线 vN 已批`/`待批`）、司机署名输入。
- **仪态条**（灯珠横排，一眼扫全）：任务完成数、故事点、进行中 CR、事件数、最近门禁、EVM（SPI·CPI，越界标 ⚠）；右侧 KPI 灯珠（门禁/缺陷 MTTR、返工/逃逸率，悬停看详情）。

### 6.3 风挡 · 线路图

- 五座车站（过程组）+ G0–G3 信号灯（绿=过/红=堵/橘=待人审，翻灯时闪一下）；
- Trammy 小电车停在当前阶段，换阶段颠一下；有未决 CR 时打瞌睡（zzz），任务阻塞时车窗变红；
- 上方虚线支线 = CR 绕行道岔：新 CR 立案瞬间支线流一段、气泡弹一下；
- **每站下挂该过程组的子过程轨道**（单字徽章 + 子过程名），底部一行十大知识域图例；
- 终点站（收尾 + 任务全清）庆祝：Trammy 欢快摇摆 + 车票彩带落下。
- 所有动效尊重系统「减少动态效果」设置。

### 6.4 主驾席 · 会话车厢

**与引擎直接多轮对话，是日常主入口。**

| 操作 | 用法 |
|---|---|
| 选引擎 | `claude code` / `openhands` / `fake（离线测试）` |
| ➕ 新会话 | 开一条 `chat-XXXX` 会话 |
| 发消息 | 输入框回车或「发送」按钮；SSE 流式显示引擎输出（文本/工具调用/结果/错误四种行） |
| 🏁 收车 | 关闭会话：干净 worktree 自动移除、任务转 DONE；**脏 worktree 永不销毁**（未审工作保留，`kept_worktree` 标记） |
| 🔀 并线 | 会话分支合回主线——**沙箱产出进项目文件的唯一正门**：分支变更集先过 Intent Guard（越界照章立案 CR，批准扩基线后重试即过）；**WBS 锚定校验**（项目定义了 `.tram/wbs.yaml` 工作包时，会话未锚定或产出越出工作包范围都拒绝；无工作包 = 自由模式）；主工作区有已跟踪未提交改动会拒绝（不和人手头的工作混）；冲突自动中止（确定性工具不裁语义冲突，手动 `git merge` 解决后重试）；成功后 `SESSION_MERGED` 事件 + 合并提交挂任务账 + **跨会话重叠预警**（其他在途会话也改了相同路径时红字提醒） |

**WBS 工作包锚定**（`.tram/wbs.yaml`，PM 规划工件）：

```yaml
packages:
  - id: WP-001
    title: 登录模块
    paths: ["src/auth/**"]   # 该工作包的交付范围（glob）
```

开会话时在会话栏下拉选择工作包（自由 = 不锚定）；项目一旦定义了工作包，未锚定/越界的会话产出无法并线——这是「子过程可交付成果符合范围基准」的机械保证。

每条消息的完整铁轨：

1. 首条消息自动立任务（标题取首行）+ 建常驻 worktree（`tram/chat-XXXX` 分支，主线不动）；
2. prompt 前自动拼 **[Tram 治理上下文]**（项目/阶段/基线轨内轨外路径/工作流/门禁权限），引擎知道自己在铁轨上——问「回到启动阶段」会得到懂行的回答；
3. 引擎跑完即过 Intent Guard：**轨内改动由 Tram 自动提交**（commit_refs 留痕，事件 `AGENT_RUN_FINISHED ok`）；越界 → 拦截 + 自动立案 CR（`blocked`，worktree 保留）；引擎自身失败 → `error` 落账，不进 Guard；
4. 引擎会话句柄（claude session / openhands conversation id）入库，下条消息自动 `--resume` 续聊；
5. 全程事件 `source=tram.chat`，与 CLI 同一条账。

### 6.5 右轨 · 调度台 / 站台审批 / 行车记录仪

**调度台**（按钮阵，写模式可用）：

| 按钮 | 动作 | 说明 |
|---|---|---|
| ▶ 全线运行 | `flow.run` | 等于 `tram run`：连续过门禁，红灯/待人审即停 |
| G0–G3 | `gate.run` | 单门重跑 |
| 🛡 Guard | `guard.check` | 核验当前改动（越界会自动立案 CR） |
| 🎫 开票 | `artifact.generate` | 生成全部治理工件 |
| 📊 EVM | `evm.snapshot` | 挣值快照，越界自动入险 |
| 📝 基线 | 基线编辑器 | 模态浮层编辑 YAML；**保存只做 schema 校验落盘，批准仍走站台审批**（HITL 不绕过） |

**站台审批**：所有等你拍板的事集中在这里，标题带待办角标（N）：

- 范围基线待批 → 表单署名批准（= `tram baseline approve`）
- 门禁升级待人审 / G3 待放行 → 署名放行
- CR 绕行待审 → 表单署名裁决（= `tram cr approve/reject`）
- EVM 越界提示 → 去 CLI 看 `tram evm show`

**行车记录仪**：每个调度动作的逐行叙述（时间戳 + 结果），是控制台回音不是日志转储。

### 6.6 底舱 · 五个标签面板

| 面板 | 内容与用法 |
|---|---|
| 📁 文件车厢 | 项目文件管理。**按目录**视图：面包屑 + 文件树，文件带知识域徽章（悬停看「过程组·知识域·子过程」全称）和未提交 ● 标记。**🧭 按知识域**视图：全仓文件按域分组，一眼看清各归哪个过程组哪个子过程。点开文件进编辑器：轨内 ✓ / 越界 ⚠ 信号灯、512KB 截断、二进制识别；**保存过 Intent Guard**——越界内容不落盘并自动立案 CR；`.tram/` 与 `.git/` 结构保护谁都不能写。不自动 commit（提交是显式治理动作，会话车厢除外） |
| 📋 任务板 | 任务册：est/spent 点数直改（EVM 数据源）、QA ✓/✗ 按钮（= `qa.pass`/`qa.fail`，fail 会弹缺陷备注并自动建返工任务）、返工链标记（↩ 返工自 T-00X）、完成票面打孔 |
| 🎫 车票 · 绕行 | 工件车票（打孔 = 证据已验）+ CR 绕行支线清单 |
| 🌤 风险气象 | 风险登记按 P×I 定天气：☁️/🌧/⛈ |
| 📜 调度日志 | 事件黑匣子实时流（SSE 推送，治理动作发生即刷新全舱） |

---

## 7. 引擎与沙箱

### 7.1 三种引擎

| 引擎 | 说明 | 前置 |
|---|---|---|
| `fake` | 离线确定性引擎，按 `--fake-plan` JSON 写文件 | 无（测试/演示） |
| `claude` | Claude Code CLI headless（stream-json 流式、`--resume` 续聊） | 安装 Claude Code |
| `openhands` | OpenHands CLI headless（`--json` JSONL 流、conversation id 续聊） | `pip install openhands`（需 Python 3.12），且先交互式跑一次 `openhands` 配置 LLM |

**二进制定位**：Tram 先查 PATH，找不到自动扫常见安装位（`~/.local/bin`、`~/.claude/local`、`/opt/homebrew/bin`、`/usr/local/bin`）——从 IDE 启动的进程 PATH 不全也能找到引擎。

**工具权限模型：沙箱即边界**。引擎在 worktree/docker 沙箱内获得完整工具权限（claude 适配器默认 `--dangerously-skip-permissions`；openhands headless 本就自动放行）——因为隔离已经由沙箱保证，写轨内轨外的判定归 Tram 的 Intent Guard 在跑完后收口，双保险不重叠。若通过 `--allowed-tools` 显式给了白名单，则尊重限制、不再全放。

### 7.2 沙箱

- **worktree（默认）**：任务/会话跑在 `.tram/worktrees/` 下的 git worktree（`tram/<id>` 分支），主线不动；变更隔离由 git 保证。零提交仓库首次使用会自动补空 bootstrap 提交。
- **docker**：`--sandbox docker`（或配置 `sandbox: docker`）引擎命令进容器执行（镜像 `docker_image` 可配，需镜像内预装引擎 CLI），Intent Guard/提交/事件流留在宿主机——容器管执行隔离，git 管变更隔离。

---

## 8. 治理闭环场景演练

### 场景 A：agent 想偷加依赖（采购域拦截）
会话里让引擎「加个新库」。它改了 `pyproject.toml`——基线轨外 → Intent Guard 拦截 → `INTENT_BLOCKED` + **procurement CR** 自动立案 → 站台审批出现待办（角标 +1）。你裁决：批准 → 路径并入基线，重跑即过；拒绝 → CR 关闭。脏 worktree 保留在 `.tram/worktrees/`，未审工作永不销毁。

### 场景 B：QA 缺陷返工链
`tram qa fail T-001 --note "登录 500"` → 自动建返工任务 T-002（`rework_of: T-001`）→ `tram agent run --task T-002 --role dev --runner claude --prompt "修复"`（dev 角色提示词注入）→ `tram qa pass T-002` 闭环。全程事件可 replay，缺陷 MTTR 进 KPI。

### 场景 C：CR 走 GitHub PR 裁决
`tram cr link cr-0001 --pr 42` → 团队在 PR 里 review → `tram cr sync cr-0001` 按 review 状态裁决（approved → 并入基线），裁决来源记 `tram.github.pr`。

### 场景 D：EVM 越界预警
任务点数持续超支 → `tram evm snapshot` CPI 越界 → 自动登记风险 → UI 风险气象变 🌧/⛈ → 站台审批出现「需要纠偏决策」提示。

---

## 9. 配置参考

`.tram/tram.yaml`（`tram init` 生成，可手改）：

```yaml
sandbox: worktree        # worktree | docker
docker_image: node:22-bookworm-slim   # docker 沙箱镜像（引擎 CLI 需装在镜像内）
policy_engine: auto      # auto（有 OPA 用 OPA，否则 fallback 同语义）
evm_thresholds:          # EVM 越界阈值（可调；默认如下）
  spi_min: 0.85
  cpi_min: 0.9
```

门禁配置在 `.tram/gates.yaml`；角色提示词在 `.tram/roles/{pm,qa,dev}.md`（支持 `{{ task_id }}` / `{{ task_prompt }}` 占位符）。

---

## 10. 故障排查

| 症状 | 原因与解法 |
|---|---|
| `has no .tram/` | 没初始化——仓库根目录跑 `tram init` |
| UI 点什么都提示只读 403 | 服务没带 `--approve` 启动；重启 `tram ui --approve` |
| 提示要署名 | 顶栏「👤 司机」填上名字——写操作都要落款 |
| `'claude' CLI not found` | 引擎没装或不在 PATH/常见安装位；装好后重试（不用重启 UI 也能找到，每次 spawn 都重新定位） |
| `'openhands' CLI not found` | `pip install openhands`（Python 3.12），并先交互式跑一次 `openhands` 配置 LLM |
| openhands `Headless mode requires existing settings` | 同上——headless 需要先完成一次交互式 LLM 配置 |
| `git worktree add failed: Not a valid object name: 'HEAD'` | 旧版本问题，已修复（零提交仓库自动补 bootstrap 提交）；升级 tram |
| 引擎说「写入被权限系统拦下」 | 旧版本问题，已修复（沙箱即边界，默认放开工具权限）；升级 tram；显式 `--allowed-tools` 时是白名单在起作用 |
| 会话 `blocked` + 出现 CR | 引擎写了轨外路径——预期的治理行为；去站台审批裁决或改基线 |
| 会话 `error` | 引擎自身失败（非治理结论）；看错误详情，通常是引擎未配置/网络问题 |
| 收车后 worktree 还在 | 该会话有未审改动，脏 worktree 永不销毁（`kept_worktree: true`）；处理完 CR 后可手动清理 `.tram/worktrees/` |
| docker 沙箱起不来 | 检查镜像存在且内含引擎 CLI；`docker_image` 可配 |

---

*手册与代码同仓库演进；架构与批次决策记录见 [docs/PLAN.md](PLAN.md)，项目概览见 [README](../README.md)。*
