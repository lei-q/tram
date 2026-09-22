"""tram CLI - thin commands over the governance services."""

from __future__ import annotations

import datetime as dt
import json
import re
import sys
from pathlib import Path
from typing import Annotated

import typer
from jinja2 import StrictUndefined, Template
from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from tram.adapters.base import RunnerUnavailableError
from tram.adapters.claude_code import ClaudeCodeRunner
from tram.adapters.fake import FakeRunner
from tram.context import ROLE_KINDS, TramContext, init_project, load_context
from tram.cr_store import CRStore
from tram.governance import approvals, pr_review
from tram.governance.gate_runner import GateRunner
from tram.governance.intent_guard import IntentGuard
from tram.metrics.evm import (
    compute_snapshot,
    escalate_breaches,
    evaluate_thresholds,
    latest_snapshot,
    save_snapshot,
)
from tram.metrics.kpis import defect_mttr, escape_report, mttr_report, rework_report
from tram.models.cr import CRType
from tram.models.events import EventKind
from tram.models.gates import GateStatus
from tram.models.task import TaskRecord, TaskSpec, TaskStatus
from tram.orchestration import invoke_flow
from tram.sandbox.docker import DockerSandboxRunner
from tram.sandbox.worktree import WorktreeSession

app = typer.Typer(help="Tram 🚋 - governance rails for AI coding agents.", no_args_is_help=True)
baseline_app = typer.Typer(help="scope baseline (HITL)", no_args_is_help=True)
approve_app = typer.Typer(help="human approvals (HITL)", no_args_is_help=True)
gate_app = typer.Typer(help="run phase gates", no_args_is_help=True)
guard_app = typer.Typer(help="intent guard: changes vs scope baseline", no_args_is_help=True)
agent_app = typer.Typer(help="run coding-agent tasks inside the sandbox", no_args_is_help=True)
cr_app = typer.Typer(help="change requests", no_args_is_help=True)
artifact_app = typer.Typer(help="governance artifacts (tickets)", no_args_is_help=True)
task_app = typer.Typer(help="task records (EVM data source)", no_args_is_help=True)
evm_app = typer.Typer(help="earned value management (SPI/CPI)", no_args_is_help=True)
qa_app = typer.Typer(help="QA loop: reproduce -> rework -> verify", no_args_is_help=True)
app.add_typer(baseline_app, name="baseline")
app.add_typer(approve_app, name="approve")
app.add_typer(gate_app, name="gate")
app.add_typer(guard_app, name="guard")
app.add_typer(agent_app, name="agent")
app.add_typer(cr_app, name="cr")
app.add_typer(artifact_app, name="artifact")
app.add_typer(task_app, name="task")
app.add_typer(evm_app, name="evm")
app.add_typer(qa_app, name="qa")

console = Console()
COMMIT_TRAILER = "Co-Authored-By: Claude Code <noreply@anthropic.com>"


def _utcnow() -> dt.datetime:
    return dt.datetime.now(dt.UTC)


def _fail(exc: Exception) -> None:
    console.print(Panel(str(exc), title="error", border_style="red"))
    raise typer.Exit(code=1) from exc


def _load_json_plan(path: Path | None) -> dict[str, str]:
    if path is None:
        return {}
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict) or not all(
        isinstance(k, str) and isinstance(v, str) for k, v in data.items()
    ):
        raise ValueError(f"plan file must be a JSON object of {{path: content}}: {path}")
    return data


def _next_task_id(state) -> str:
    """分配不冲突的任务 id：以现有 id 为准，防止计数器与手工编辑漂移。"""
    nums = [int(m.group(1)) for t in state.tasks if (m := re.fullmatch(r"T-(\d+)", t.id))]
    seq = max(state.next_task_seq, max(nums, default=0) + 1)
    state.next_task_seq = seq + 1
    return f"T-{seq:03d}"


def _render_role_prompt(ctx: TramContext, role: str, task_id: str, prompt: str) -> str:
    """PM/QA/Dev 提示词分离：项目可定制 .tram/roles/<role>.md，包内模板兜底。"""
    template_file = ctx.role_prompt_file(role)
    if not template_file.exists():
        raise ValueError(f"role template not found: {template_file} ({' | '.join(ROLE_KINDS)})")
    template = Template(template_file.read_text(encoding="utf-8"), undefined=StrictUndefined)
    return template.render(task_id=task_id, task_prompt=prompt)


@app.command()
def init(
    name: Annotated[str | None, typer.Option(help="project name")] = None,
    force: Annotated[bool, typer.Option("--force", help="reinitialize")] = False,
) -> None:
    """Create .tram/ governance state in this repo."""
    try:
        ctx = init_project(Path.cwd(), name=name, force=force)
    except Exception as exc:  # noqa: BLE001 - CLI boundary reports, never crashes
        _fail(exc)
        return
    console.print(
        Panel(
            f"project: {ctx.config.project_name}\n"
            f"sandbox: {ctx.config.sandbox.value}\n"
            f"policy engine: {ctx.config.policy_engine.value}\n\n"
            f"next: edit {ctx.baseline_file} then `tram baseline approve --by <you>`",
            title="tram init ✅",
            border_style="green",
        )
    )


@app.command()
def status() -> None:
    """Show project phase, gates, CRs, tasks."""
    try:
        ctx = load_context()
        state = ctx.load_state()
    except Exception as exc:  # noqa: BLE001
        _fail(exc)
        return
    crs = CRStore(ctx.crs_dir).open_crs()
    table = Table(title=f"🚋 {state.project_name}")
    table.add_column("item")
    table.add_column("value", overflow="fold")
    table.add_row("phase", state.phase.value)
    table.add_row("events", str(ctx.events.count()))
    table.add_row("open CRs", str(len(crs)))
    for cr in crs[:5]:
        table.add_row(f"  {cr.id}", f"{cr.type.value} / {cr.status.value}")
    done = sum(t.status == TaskStatus.DONE for t in state.tasks)
    table.add_row("tasks", f"{len(state.tasks)} ({done} done)")
    if state.gate_history:
        last = state.gate_history[-1]
        table.add_row("last gate", f"{last.gate_id}: {last.status.value}")
    approved = any(
        a.kind == "scope_baseline" and a.decision == "approved" for a in state.human_approvals
    )
    table.add_row("scope baseline", "approved ✅" if approved else "awaiting human approval ⏳")
    snap = latest_snapshot(ctx)
    if snap is not None:
        breaches = evaluate_thresholds(snap, ctx.config.evm_thresholds)
        mark = f" ⚠ {'; '.join(breaches)}" if breaches else " ✅"
        table.add_row("evm (latest)", f"{snap.date} SPI={snap.spi} CPI={snap.cpi}{mark}")
    console.print(table)


@baseline_app.command("show")
def baseline_show() -> None:
    """Print the current scope baseline."""
    try:
        ctx = load_context()
        console.print(ctx.baseline_file.read_text(encoding="utf-8"))
    except Exception as exc:  # noqa: BLE001
        _fail(exc)


@baseline_app.command("approve")
def baseline_approve(
    by: Annotated[str, typer.Option(help="approver identity")] = "human",
    note: Annotated[str, typer.Option(help="approval note")] = "",
) -> None:
    """HITL: approve the scope baseline (enables G0)."""
    try:
        ctx = load_context()
        approvals.approve_baseline(ctx, by, note)
    except Exception as exc:  # noqa: BLE001
        _fail(exc)
        return
    console.print("[green]scope baseline approved ✅[/green]")


@approve_app.command("release")
def approve_release(
    by: Annotated[str, typer.Option(help="approver identity")] = "human",
    note: Annotated[str, typer.Option(help="approval note")] = "",
) -> None:
    """HITL: approve the release (unblocks g3_closing_gate -> 终点站)."""
    try:
        ctx = load_context()
        approvals.approve_release(ctx, by, note)
    except Exception as exc:  # noqa: BLE001
        _fail(exc)
        return
    console.print("[green]release approved ✅ — `tram run` 可以开到终点站了[/green]")


@gate_app.command("run")
def gate_run(gate_id: Annotated[str, typer.Argument(help="e.g. g2_quality_gate")]) -> None:
    """Run a phase gate: deterministic checks + policy decision."""
    try:
        ctx = load_context()
        result = GateRunner(ctx).run(gate_id)
    except Exception as exc:  # noqa: BLE001
        _fail(exc)
        return
    style = {
        GateStatus.PASS: "green",
        GateStatus.FAIL: "red",
        GateStatus.BLOCKED_PENDING_HUMAN: "yellow",
    }[result.status]
    table = Table(title=f"gate {gate_id}: {result.status.value}", border_style=style)
    table.add_column("check")
    table.add_column("status")
    table.add_column("detail", overflow="fold")
    for check in result.checks:
        table.add_row(check.id, check.status, check.output[:300])
    console.print(table)
    for reason in result.decision_reasons:
        console.print(f"  reason: {reason}")
    if result.needs_human:
        console.print("[yellow]→ blocked pending human decision[/yellow]")
    if result.status != GateStatus.PASS:
        raise typer.Exit(code=1)


@app.command()
def run() -> None:
    """开到底站为止：沿主线连续过门禁，绿灯推进、红灯必停。"""
    try:
        ctx = load_context()
        final, engine = invoke_flow(GateRunner(ctx))
    except Exception as exc:  # noqa: BLE001
        _fail(exc)
        return
    for line in final.get("journey", []):
        console.print(line)
    stop = final.get("stop_reason")
    if stop:
        console.print(Panel(stop, title=f"停车（{engine}）", border_style="yellow"))
    else:
        console.print(f"[green]全线绿灯，抵达终点站 ✅（{engine}）[/green]")


@guard_app.command("check")
def guard_check(
    paths: Annotated[
        list[str] | None, typer.Argument(help="paths (default: git pending changes)")
    ] = None,
) -> None:
    """Intent Guard: verify changed paths against the scope baseline."""
    try:
        ctx: TramContext = load_context()
        baseline = ctx.load_baseline()
        paths = paths or ctx.git.pending_changes()
        decision = IntentGuard(baseline).check(list(paths))
        if decision.ok:
            console.print(f"[green]guard ok ✅ ({len(decision.allowed)} path(s) in scope)[/green]")
            return
        event = ctx.events.append(
            EventKind.INTENT_BLOCKED,
            source="tram.guard",
            data={"violations": decision.violations},
        )
        state = ctx.load_state()
        cr = CRStore(ctx.crs_dir).create_draft(
            state,
            CRType.SCOPE,
            changed_paths=decision.violations,
            reason="intent guard: changes outside scope baseline",
            trigger_event_seq=event.seq,
        )
        ctx.state_store.save(state)
        ctx.events.append(
            EventKind.CR_CREATED,
            source="tram.guard",
            data={"cr": cr.id, "type": cr.type.value, "paths": decision.violations},
            refs={"event": str(event.seq)},
        )
    except Exception as exc:  # noqa: BLE001
        _fail(exc)
        return
    console.print(
        Panel(
            "\n".join(decision.violations),
            title=f"INTENT BLOCKED 🛑 CR {cr.id} drafted",
            border_style="red",
        )
    )
    raise typer.Exit(code=1)


@agent_app.command("run")
def agent_run(
    prompt: Annotated[str, typer.Option(help="task prompt for the agent")],
    runner: Annotated[str, typer.Option(help="fake | claude")] = "fake",
    fake_plan: Annotated[
        Path | None, typer.Option(help="JSON {path: content} the fake runner writes")
    ] = None,
    fake_violation_plan: Annotated[
        Path | None, typer.Option(help="extra JSON writes outside scope, to demo the guard")
    ] = None,
    allowed_tools: Annotated[
        str | None, typer.Option(help="comma-separated tool allowlist")
    ] = None,
    max_turns: Annotated[int | None, typer.Option(help="agent turn limit")] = None,
    points: Annotated[
        float | None, typer.Option(help="story-point estimate for this task (EVM)")
    ] = None,
    role: Annotated[str, typer.Option(help="role prompt template: pm | qa | dev")] = "dev",
    sandbox: Annotated[
        str | None,
        typer.Option(
            help="worktree (default) | docker: agent in a container, governance host-side"
        ),
    ] = None,
    task_ref: Annotated[
        str | None,
        typer.Option("--task", help="attach to an existing task (e.g. a QA rework task)"),
    ] = None,
) -> None:
    """Run one agent task in a sandbox, guarded by Intent Guard."""
    try:
        ctx = load_context()
        state = ctx.load_state()
    except Exception as exc:  # noqa: BLE001
        _fail(exc)
        return

    if runner == "fake":
        writes = _load_json_plan(fake_plan)
        writes.update(_load_json_plan(fake_violation_plan))
        engine = FakeRunner(writes)
    elif runner == "claude":
        engine = ClaudeCodeRunner()
    else:
        _fail(ValueError(f"unknown runner '{runner}' (fake | claude)"))
        return
    sandbox_mode = sandbox or ctx.config.sandbox.value
    if sandbox_mode == "docker":
        if runner != "claude":
            _fail(ValueError("--sandbox docker needs --runner claude (fake runs host-side)"))
            return
        engine = DockerSandboxRunner(engine, image=ctx.config.docker_image)
    elif sandbox_mode != "worktree":
        _fail(ValueError(f"unknown sandbox '{sandbox_mode}' (worktree | docker)"))
        return
    if role not in ROLE_KINDS:
        _fail(ValueError(f"unknown role '{role}' ({' | '.join(ROLE_KINDS)})"))
        return

    if task_ref is not None:
        record = state.task(task_ref)
        if record is None:
            _fail(ValueError(f"unknown task: {task_ref} (see `tram task list`)"))
            return
        if record.status not in (TaskStatus.TODO, TaskStatus.BLOCKED, TaskStatus.DOING):
            _fail(ValueError(f"task {task_ref} is {record.status.value}; cannot attach a run"))
            return
        task_id = record.id
        record.status = TaskStatus.DOING
        if points is not None:
            record.est_points = points
    else:
        task_id = _next_task_id(state)
        record = TaskRecord(
            id=task_id,
            title=prompt.splitlines()[0][:80],
            status=TaskStatus.DOING,
            est_points=points if points is not None else 1.0,
        )
        state.tasks.append(record)

    try:
        spec_prompt = _render_role_prompt(ctx, role, task_id, prompt)
    except Exception as exc:  # noqa: BLE001
        _fail(exc)
        return

    spec = TaskSpec(
        id=task_id,
        prompt=spec_prompt,
        allowed_tools=allowed_tools.split(",") if allowed_tools else [],
        max_turns=max_turns,
    )
    ctx.state_store.save(state)
    ctx.events.append(
        EventKind.AGENT_RUN_STARTED,
        source="tram.agent",
        data={"task": task_id, "runner": runner, "role": role, "prompt": prompt[:500]},
    )

    try:
        session = WorktreeSession(ctx.repo, task_id)
        with session as worktree:
            result = engine.run(spec, worktree)
            console.print(f"runner {result.runner}: {result.status} - {result.summary[:160]}")
            changed = session.pending_changes()
            decision = IntentGuard(ctx.load_baseline()).check(changed)

            if not decision.ok:
                record.status = TaskStatus.BLOCKED
                event = ctx.events.append(
                    EventKind.INTENT_BLOCKED,
                    source="tram.agent",
                    data={"task": task_id, "violations": decision.violations},
                    refs={"task": task_id},
                )
                cr = CRStore(ctx.crs_dir).create_draft(
                    state,
                    CRType.SCOPE,
                    changed_paths=decision.violations,
                    reason=f"agent task {task_id} wrote outside scope baseline",
                    trigger_event_seq=event.seq,
                )
                ctx.state_store.save(state)
                ctx.events.append(
                    EventKind.CR_CREATED,
                    source="tram.agent",
                    data={"cr": cr.id, "task": task_id, "paths": decision.violations},
                    refs={"event": str(event.seq), "task": task_id},
                )
                ctx.events.append(
                    EventKind.AGENT_RUN_FINISHED,
                    source="tram.agent",
                    data={"task": task_id, "status": "blocked", "cr": cr.id},
                    refs={"task": task_id, "cr": cr.id},
                )
                console.print(
                    Panel(
                        "\n".join(decision.violations),
                        title=f"task {task_id} BLOCKED by Intent Guard 🛑 CR {cr.id}",
                        border_style="red",
                    )
                )
                console.print(f"worktree kept for review: {worktree}")
                return

            if changed:
                sha = session.commit_all(f"tram {task_id}: {record.title}\n\n{COMMIT_TRAILER}")
                record.status = TaskStatus.DONE
                record.spent_points = record.est_points
                record.commit_refs.append(sha or "")
                ctx.state_store.save(state)
                ctx.events.append(
                    EventKind.AGENT_RUN_FINISHED,
                    source="tram.agent",
                    data={"task": task_id, "status": "ok", "commit": sha},
                    refs={"task": task_id, "commit": sha or ""},
                )
                console.print(
                    f"[green]task {task_id} committed ✅ branch {session.branch} @ "
                    f"{(sha or '')[:8]} ({len(changed)} file(s))[/green]"
                )
            else:
                record.status = TaskStatus.DONE
                record.spent_points = record.est_points
                ctx.state_store.save(state)
                ctx.events.append(
                    EventKind.AGENT_RUN_FINISHED,
                    source="tram.agent",
                    data={"task": task_id, "status": "ok", "changes": 0},
                    refs={"task": task_id},
                )
                console.print(f"[yellow]task {task_id} finished with no changes[/yellow]")
    except RunnerUnavailableError as exc:
        record.status = TaskStatus.BLOCKED
        ctx.state_store.save(state)
        _fail(exc)


@cr_app.command("list")
def cr_list() -> None:
    """List all change requests."""
    try:
        ctx = load_context()
        crs = CRStore(ctx.crs_dir).load_all()
    except Exception as exc:  # noqa: BLE001
        _fail(exc)
        return
    table = Table(title="🎫 change requests")
    table.add_column("id")
    table.add_column("type")
    table.add_column("status")
    table.add_column("paths", overflow="fold")
    if not crs:
        console.print("[dim]no change requests[/dim]")
        return
    for cr in crs:
        table.add_row(cr.id, cr.type.value, cr.status.value, ", ".join(cr.impact.changed_paths))
    console.print(table)


@cr_app.command("reject")
def cr_reject(
    cr_id: Annotated[str, typer.Argument(help="e.g. cr-0001")],
    by: Annotated[str, typer.Option(help="decider identity")] = "human",
    note: Annotated[str, typer.Option(help="decision note")] = "",
) -> None:
    """HITL: reject a change request (closes it, unblocks gates)."""
    _decide_cr(cr_id, "rejected", by, note)


@cr_app.command("approve")
def cr_approve(
    cr_id: Annotated[str, typer.Argument(help="e.g. cr-0001")],
    by: Annotated[str, typer.Option(help="decider identity")] = "human",
    note: Annotated[str, typer.Option(help="decision note")] = "",
) -> None:
    """HITL: approve a change request (scope CRs update the baseline)."""
    _decide_cr(cr_id, "approved", by, note)


@cr_app.command("link")
def cr_link(
    cr_id: Annotated[str, typer.Argument(help="e.g. cr-0001")],
    pr: Annotated[int, typer.Option("--pr", help="PR number")],
) -> None:
    """Link a CR to a GitHub PR (repo resolved from origin remote)."""
    try:
        ctx = load_context()
        ref = pr_review.link_pr(ctx, cr_id, pr)
    except Exception as exc:  # noqa: BLE001
        _fail(exc)
        return
    console.print(f"[green]cr {cr_id} linked to {ref.repo}#{ref.number} ✅[/green]")


@cr_app.command("sync")
def cr_sync(
    cr_id: Annotated[str, typer.Argument(help="e.g. cr-0001")],
    note: Annotated[str, typer.Option(help="decision note")] = "",
) -> None:
    """HITL via PR review: apply the PR's review state to the CR (needs gh)."""
    try:
        ctx = load_context()
        status, detail = pr_review.sync_cr(ctx, cr_id, note)
    except Exception as exc:  # noqa: BLE001
        _fail(exc)
        return
    if status is None:
        console.print(f"[yellow]{detail}[/yellow]")
    else:
        console.print(f"[green]{detail} ✅[/green]")


def _decide_cr(cr_id: str, decision: str, by: str, note: str) -> None:
    try:
        ctx = load_context()
        cr_status = approvals.decide_cr(ctx, cr_id, decision, by, note)
    except Exception as exc:  # noqa: BLE001
        _fail(exc)
        return
    console.print(f"[green]cr {cr_id} -> {cr_status.value} ✅[/green]")


@artifact_app.command("list")
def artifact_list() -> None:
    """List registered artifacts and their evidence status."""
    try:
        ctx = load_context()
        if ctx.artifacts_index.exists():
            import json as _json

            from tram.models.artifacts import Artifact

            items = [
                Artifact.model_validate(obj)
                for obj in _json.loads(ctx.artifacts_index.read_text(encoding="utf-8"))
            ]
        else:
            items = []
    except Exception as exc:  # noqa: BLE001
        _fail(exc)
        return
    table = Table(title="🎫 artifacts")
    table.add_column("kind")
    table.add_column("path")
    table.add_column("evidence")
    for item in items:
        verified = sum(1 for e in item.evidence if e.verified)
        mark = "✅" if item.verified else ("⚠ gap" if not item.has_evidence else "⚠ unverified")
        table.add_row(item.kind, item.path, f"{verified}/{len(item.evidence)} {mark}")
    if not items:
        console.print("[dim]no artifacts - try `tram artifact generate --all`[/dim]")
        return
    console.print(table)


@artifact_app.command("generate")
def artifact_generate(
    kinds: Annotated[list[str] | None, typer.Argument(help="artifact kinds")] = None,
    all_: Annotated[bool, typer.Option("--all", help="generate all known kinds")] = False,
) -> None:
    """Generate evidence-linked project artifacts (deterministic render)."""
    from tram.artifacts.generator import ARTIFACT_KINDS, ArtifactGenerator

    try:
        ctx = load_context()
        if all_:
            kinds = list(ARTIFACT_KINDS)
        kinds = kinds or []
        unknown = [k for k in kinds if k not in ARTIFACT_KINDS]
        if unknown:
            raise ValueError(f"unknown kinds: {unknown}; known: {', '.join(ARTIFACT_KINDS)}")
        if not kinds:
            raise ValueError("nothing to generate: pass kinds or --all")
        generator = ArtifactGenerator(ctx)
        for kind in kinds:
            artifact = generator.generate(kind)
            console.print(f"[green]{kind} ✅ {artifact.path}[/green]")
    except Exception as exc:  # noqa: BLE001
        _fail(exc)


@task_app.command("list")
def task_list() -> None:
    """List task records with points and rework counters."""
    try:
        ctx = load_context()
        state = ctx.load_state()
    except Exception as exc:  # noqa: BLE001
        _fail(exc)
        return
    if not state.tasks:
        console.print("[dim]no tasks - tasks appear after `tram agent run`[/dim]")
        return
    table = Table(title="📋 tasks")
    table.add_column("id")
    table.add_column("status")
    table.add_column("est", justify="right")
    table.add_column("spent", justify="right")
    table.add_column("rework", justify="right")
    table.add_column("title", overflow="fold")
    for t in state.tasks:
        table.add_row(
            t.id,
            t.status.value,
            str(t.est_points),
            str(t.spent_points),
            str(t.rework_count),
            t.title,
        )
    console.print(table)


@task_app.command("points")
def task_points(
    task_id: Annotated[str, typer.Argument(help="e.g. T-001")],
    est: Annotated[float | None, typer.Option(help="estimate (story points)")] = None,
    spent: Annotated[float | None, typer.Option(help="actually spent points")] = None,
) -> None:
    """Set planned/actual points on a task (the EVM data source)."""
    try:
        ctx = load_context()
        state = ctx.load_state()
        record = state.task(task_id)
        if record is None:
            raise ValueError(f"unknown task: {task_id} (see `tram task list`)")
        if est is None and spent is None:
            raise ValueError("nothing to update: pass --est and/or --spent")
        if est is not None:
            record.est_points = est
        if spent is not None:
            record.spent_points = spent
        ctx.state_store.save(state)
        ctx.events.append(
            EventKind.TASK_UPDATED,
            source="tram.task",
            data={
                "task": task_id,
                "est_points": record.est_points,
                "spent_points": record.spent_points,
                "status": record.status.value,
            },
            refs={"task": task_id},
        )
    except Exception as exc:  # noqa: BLE001
        _fail(exc)
        return
    console.print(
        f"[green]task {task_id}: est={record.est_points} spent={record.spent_points} ✅[/green]"
    )


def _evm_table(snap) -> Table:
    table = Table(title=f"📊 EVM snapshot {snap.date.isoformat()}")
    table.add_column("metric")
    table.add_column("value", overflow="fold")
    table.add_row("PV / EV / AC", f"{snap.pv} / {snap.ev} / {snap.ac}")
    table.add_row("SPI (进度)", str(snap.spi))
    table.add_row("CPI (成本)", str(snap.cpi))
    table.add_row("SV / CV", f"{snap.sv} / {snap.cv}")
    return table


@evm_app.command("snapshot")
def evm_snapshot(
    day: Annotated[
        str | None, typer.Option(help="snapshot date YYYY-MM-DD (default: today)")
    ] = None,
) -> None:
    """Compute an EVM snapshot; threshold breaches auto-register risks (HITL-free)."""
    try:
        snap_day = dt.date.fromisoformat(day) if day else None
        ctx = load_context()
        state = ctx.load_state()
        snap = compute_snapshot(state, snap_day)
        path = save_snapshot(ctx, snap)
        event = ctx.events.append(
            EventKind.EVM_SNAPSHOT, source="tram.evm", data=snap.model_dump(mode="json")
        )
        reasons = evaluate_thresholds(snap, ctx.config.evm_thresholds)
        new_risks = []
        if reasons:
            existing = {r.id for r in state.risks}
            for risk in escalate_breaches(snap, reasons, trigger_seq=event.seq):
                if risk.id not in existing:
                    state.risks.append(risk)
                    new_risks.append(risk)
            if new_risks:
                ctx.state_store.save(state)
                for risk in new_risks:
                    ctx.events.append(
                        EventKind.RISK_REGISTERED,
                        source="tram.evm",
                        data={"risk": risk.id, "description": risk.description},
                        refs={"risk": risk.id, "event": str(event.seq)},
                    )
    except Exception as exc:  # noqa: BLE001
        _fail(exc)
        return
    console.print(_evm_table(snap))
    console.print(f"[dim]saved: {path}[/dim]")
    for reason in reasons:
        console.print(f"  [red]⚠ {reason}[/red]")
    for risk in new_risks:
        console.print(f"  [yellow]risk registered: {risk.id}[/yellow]")
    if not reasons:
        console.print("[green]within thresholds ✅[/green]")


@evm_app.command("show")
def evm_show() -> None:
    """Show the latest EVM snapshot."""
    try:
        ctx = load_context()
        snap = latest_snapshot(ctx)
    except Exception as exc:  # noqa: BLE001
        _fail(exc)
        return
    if snap is None:
        console.print("[dim]no snapshots yet - run `tram evm snapshot`[/dim]")
        return
    console.print(_evm_table(snap))
    breaches = evaluate_thresholds(snap, ctx.config.evm_thresholds)
    for reason in breaches:
        console.print(f"  [red]⚠ {reason}[/red]")
    if not breaches:
        console.print("[green]within thresholds ✅[/green]")


@qa_app.command("fail")
def qa_fail(
    task_id: Annotated[str, typer.Argument(help="task that failed QA, e.g. T-002")],
    note: Annotated[str, typer.Option(help="symptom / evidence")] = "",
    by: Annotated[str, typer.Option(help="reporter identity")] = "qa",
) -> None:
    """QA 复现失败：登记缺陷并自动创建返工任务（rework_of 链）。"""
    try:
        ctx = load_context()
        state = ctx.load_state()
        record = state.task(task_id)
        if record is None:
            raise ValueError(f"unknown task: {task_id} (see `tram task list`)")
        record.rework_count += 1
        rework = TaskRecord(
            id=_next_task_id(state),
            title=f"修复 {task_id}: {note.splitlines()[0][:60] if note else 'rework'}",
            status=TaskStatus.TODO,
            est_points=record.est_points,
            rework_of=task_id,
        )
        state.tasks.append(rework)
        ctx.state_store.save(state)
        ctx.events.append(
            EventKind.QA_FAILED,
            source="tram.qa",
            data={"task": task_id, "rework_task": rework.id, "note": note, "by": by},
            refs={"task": task_id, "rework_task": rework.id},
        )
    except Exception as exc:  # noqa: BLE001
        _fail(exc)
        return
    console.print(f"[red]qa fail: {task_id} (rework #{record.rework_count})[/red]")
    console.print(
        f"[yellow]rework task {rework.id}: `tram agent run --task {rework.id} --role dev`[/yellow]"
    )


@qa_app.command("pass")
def qa_pass(
    task_id: Annotated[str, typer.Argument(help="task verified fixed, e.g. the rework task")],
    note: Annotated[str, typer.Option(help="verification evidence")] = "",
    by: Annotated[str, typer.Option(help="verifier identity")] = "qa",
) -> None:
    """QA 验证通过：返工闭环（缺陷 MTTR 的终点）。"""
    try:
        ctx = load_context()
        state = ctx.load_state()
        record = state.task(task_id)
        if record is None:
            raise ValueError(f"unknown task: {task_id} (see `tram task list`)")
        if record.status != TaskStatus.DONE:
            record.status = TaskStatus.DONE
            record.spent_points = record.est_points
        ctx.state_store.save(state)
        ctx.events.append(
            EventKind.QA_PASSED,
            source="tram.qa",
            data={"task": task_id, "note": note, "by": by},
            refs={"task": task_id},
        )
    except Exception as exc:  # noqa: BLE001
        _fail(exc)
        return
    console.print(f"[green]qa pass: {task_id} verified ✅[/green]")


@app.command()
def kpi() -> None:
    """KPI dashboard: gate/defect MTTR + rework/escape rates (from the black box)."""
    try:
        ctx = load_context()
        state = ctx.load_state()
        events = list(ctx.events.read())
        gate_report = mttr_report(events)
        defect_report = defect_mttr(events)
        rework = rework_report(state)
        escape = escape_report(events, state)
    except Exception as exc:  # noqa: BLE001
        _fail(exc)
        return
    table = Table(title="📈 KPI dashboard")
    table.add_column("metric")
    table.add_column("value", overflow="fold")
    if gate_report.items or gate_report.open_subjects:
        overall = (
            f"{gate_report.overall_mttr_seconds}s" if gate_report.overall_mttr_seconds else "n/a"
        )
        table.add_row("MTTR 门禁 (all)", overall)
        for m in gate_report.items:
            table.add_row(f"  {m.subject}", f"MTTR {m.mttr_seconds}s ({m.breaches} breach(es))")
        for gate_id in gate_report.open_subjects:
            table.add_row(f"  {gate_id}", "still red, not yet recovered ⏳")
    else:
        table.add_row("MTTR 门禁", "[dim]no gate breaches recorded[/dim]")
    if defect_report.items or defect_report.open_subjects:
        overall = (
            f"{defect_report.overall_mttr_seconds}s"
            if defect_report.overall_mttr_seconds
            else "n/a"
        )
        table.add_row("MTTR 缺陷 (all)", overall)
        for m in defect_report.items:
            table.add_row(f"  {m.subject}", f"MTTR {m.mttr_seconds}s ({m.breaches} fix(es))")
        for task_id in defect_report.open_subjects:
            table.add_row(f"  {task_id}", "defect open, fix pending ⏳")
    else:
        table.add_row("MTTR 缺陷", "[dim]no defects recorded[/dim]")
    if rework.tasks_done:
        table.add_row(
            "rework rate",
            f"{rework.rate:.1%} ({rework.tasks_with_rework}/{rework.tasks_done} done,"
            f" {rework.rework_events} rework event(s))",
        )
    else:
        table.add_row("rework rate", "[dim]no done tasks yet[/dim]")
    if escape.defects_total:
        table.add_row(
            "escape rate",
            f"{escape.rate:.1%} ({escape.defects_escaped}/{escape.defects_total} defects"
            " recurred after a verified fix)",
        )
    else:
        table.add_row("escape rate", "[dim]no defects recorded[/dim]")
    console.print(table)


@app.command()
def replay(
    kind: Annotated[str | None, typer.Option(help="filter by event kind")] = None,
    limit: Annotated[int, typer.Option(help="show last N events")] = 20,
) -> None:
    """Replay the black box (event log)."""
    try:
        ctx = load_context()
        events = list(ctx.events.read(kind=kind))
    except Exception as exc:  # noqa: BLE001
        _fail(exc)
        return
    table = Table(title="⬛ event log (replay)")
    table.add_column("seq", justify="right")
    table.add_column("ts")
    table.add_column("kind")
    table.add_column("source")
    table.add_column("refs", overflow="fold")
    for event in events[-limit:]:
        refs = " ".join(f"{k}={v[:12]}" for k, v in event.refs.items())
        table.add_row(
            str(event.seq),
            event.ts.strftime("%m-%d %H:%M:%S"),
            event.kind.value,
            event.source,
            refs,
        )
    console.print(table)


@app.command()
def ui(
    host: Annotated[str, typer.Option(help="bind host")] = "127.0.0.1",
    port: Annotated[int, typer.Option(help="port")] = 8417,
    no_open: Annotated[bool, typer.Option("--no-open", help="do not open the browser")] = False,
    approve: Annotated[
        bool, typer.Option("--approve", help="开启站台审批（写事件流，与 CLI 同路径）")
    ] = False,
) -> None:
    """Launch the route-map UI (read-only by default; requires tram[ui])."""
    try:
        import uvicorn

        from tram.ui.api import create_app
    except ImportError as exc:
        _fail(RuntimeError(f"UI 依赖未安装：python -m pip install 'tram[ui]'（缺 {exc.name}）"))
        return
    try:
        repo = Path.cwd()
        load_context(repo)
    except Exception as exc:  # noqa: BLE001
        _fail(exc)
        return
    import threading
    import webbrowser

    url = f"http://{host}:{port}"
    if approve:
        console.print(
            f"🚋 tram ui → {url}（Ctrl-C 退出）\n"
            "[yellow]⚠️ 站台审批已开启：页面上的放行会写入事件流（与 CLI 同一代码路径）[/yellow]"
        )
    else:
        console.print(f"🚋 tram ui → {url}（Ctrl-C 退出；只读视图，操作请回 CLI）")
    if not no_open:
        threading.Timer(1.2, lambda: webbrowser.open(url)).start()
    uvicorn.run(
        create_app(repo, allow_approvals=approve), host=host, port=port, log_level="warning"
    )


def _force_utf8_stdio() -> None:
    """Windows 控制台默认 charmap 编码遇到 emoji 会 UnicodeEncodeError；
    统一 UTF-8 并把不可编码字符降级为替换符，保证永不因输出崩溃。"""
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):  # non-standard streams in tests/embedders
            pass


def main() -> None:
    _force_utf8_stdio()
    app()


if __name__ == "__main__":
    main()
