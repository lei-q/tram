# Tram 🚋

面向研发的 AI Coding Agent **治理层**——铺铁轨的，不造引擎。Tram 包在 Claude Code / OpenHands / Cursor 等代码生成引擎外面，把项目管理过程组变成 Agent 运行时约束：

- **门禁即信号**：阶段质量门（测试/覆盖率/证据校验）不通过即阻塞，Agent 无法绕过。
- **工件即证据**：每个项目文件链接真实证据（commit / 测试报告 / 事件），无证据处显式标注，禁止编造。
- **变更即分支**：范围基线外的改动被 Intent Guard 拦截，走 CR + 人工审批。
- **度量即仪表**：任务点数驱动 EVM（SPI/CPI），阈值越界自动入风险册；MTTR / 返工率从事件黑匣子确定性计算。
- **黑匣子**：append-only JSONL 事件日志 + OpenTelemetry，任何偏差可回放。

详细实施计划与决策记录见 [docs/PLAN.md](docs/PLAN.md)；完整功能用法见 [docs/MANUAL.md](docs/MANUAL.md)（产品操作手册）。

## 安装

```bash
# Python >= 3.11
python -m pip install -e ".[dev]"      # 开发
python -m pip install .                 # 使用
```

策略引擎：优先使用 OPA（`brew install opa`），未安装时自动降级为内置 Python 策略引擎（语义一致）。

`tram.yaml` 常用配置：`sandbox`（worktree | docker）、`docker_image`、`evm_thresholds`（SPI/CPI 阈值）、`approvers`（干系人审批人名单，如 `release: [你]`——配置后署名不在名单内的审批会被拒绝，未配置不限制）。

## 5 分钟上手

在**目标项目**仓库根目录：

```bash
tram init                      # 生成 .tram/ 治理目录（状态、事件日志、门禁配置）
$EDITOR .tram/scope-baseline.yaml   # 声明本任务允许改动的路径（范围基线）
tram baseline approve --by 你的名字  # 人类批准范围基线（HITL）

tram agent run --runner fake \
  --fake-plan plan.json \
  --fake-violation-plan bad.json    # 假 runner 演示：越界改动被拦截并生成 CR
tram artifact generate --all   # 生成 6 类治理工件（自动挂证据，缺证据标 gap）
tram run                       # 沿治理主线连续过门禁：绿灯推进，红灯/待人审即停
tram gate run g2_quality_gate  # 跑单座门禁（确定性检查 + 策略决策）
tram task points T-001 --est 4 --spent 6  # 维护任务点数（EVM 数据源）
tram evm snapshot              # 计算 SPI/CPI；越界自动登记风险（阈值可配）
tram qa fail T-001 --note "复现步骤…"      # QA 复现失败 → 自动建返工任务（rework 链）
tram agent run --task T-002 --role dev --runner claude --prompt "修复"  # 挂到返工任务，注入 dev 角色提示词
tram qa pass T-002 --note "复现测试转绿"   # QA 验证通过 → 缺陷闭环
tram cr link cr-0001 --pr 42   # CR 关联 GitHub PR（D5 PR review 流）
tram cr sync cr-0001           # 按 PR review 裁决 CR（approved→并入基线）
tram kpi                       # 门禁 MTTR + 缺陷 MTTR + 返工率仪表
tram status                    # 项目状态一览
tram replay --limit 20         # 回放事件黑匣子
tram ui                        # 只读线路图 UI（需 pip install 'tram[ui]'）
tram ui --approve              # 写模式：调度台（门禁/Guard/工件/EVM/基线/任务 QA）+ 文件车厢 + 会话车厢（claude code / openhands，SSE 流式）+ 站台审批，全部与 CLI 同一条服务层与事件流
```

接真实引擎：`tram agent run --runner claude --prompt "实现 X"`（需 `claude` CLI；默认在 git worktree 沙箱内执行）。共享环境可加 `--sandbox docker`：agent 命令进容器执行（镜像可配 `tram.yaml` 的 `docker_image`，需镜像内装好引擎 CLI），Intent Guard / 提交 / 事件流仍留在宿主机——容器管执行隔离，git 管变更隔离。

### 角色提示词（PM / QA / Dev）

`tram init` 会把三份角色模板装进 `.tram/roles/{pm,qa,dev}.md`，可按项目自定义；`tram agent run --role <role>` 渲染模板（注入 `{{ task_id }}` / `{{ task_prompt }}`）后交给引擎。角色分工写在提示词里：PM 只拆解不写码，QA 负责最小复现与 `tram qa fail/pass`，dev 只改相关代码且先复现再修。

## 目录

```
src/tram/
├── models/        # ProjectState / 工件 / 门禁 / CR / 风险 / EVM / 事件（Pydantic）
├── obs/           # JSONL 事件日志（黑匣子）+ OTel 挂钩
├── evidence/      # git 证据客户端 + 证据真实性校验
├── governance/    # 确定性检查注册表 / OPA+fallback 策略引擎 / Intent Guard / 门禁执行器 / HITL 审批（CLI+UI 同路径）
├── metrics/       # EVM 引擎（SPI/CPI 越界入险）+ KPI（门禁/缺陷 MTTR、返工率）——纯确定性
├── orchestration/ # 阶段推进纯函数 + 治理主线状态机（LangGraph 包装/零依赖解释器）
├── artifacts/     # 工件生成器 + Jinja2 模板（证据 frontmatter，确定性渲染）
├── ui/            # 只读线路图 UI：FastAPI + SSE + 无构建 vanilla SVG 前端
├── sandbox/       # git worktree 沙箱（默认）／docker（可选）
├── adapters/      # AgentRunner 协议：claude CLI 适配器 + fake（离线测试）
└── cli.py         # tram init/status/run/baseline/gate/guard/agent/cr/artifact/task/evm/qa/kpi/replay/ui
examples/demo-project/   # 端到端冒烟演示（smoke.sh）
```

## 状态

Phase 1 垂直切片 + 只读线路图薄版已完成；Phase 2 进行中（EVM 越界入险、KPI 仪表、QA 复现-修复闭环 + 角色提示词分离、治理主线状态机 `tram run`、docker 沙箱、UI 站台审批直连事件流已上线）。任务清单与决策记录见 [docs/PLAN.md](docs/PLAN.md)。
