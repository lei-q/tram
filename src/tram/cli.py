"""tram CLI - thin commands over the governance services."""

from __future__ import annotations

import datetime as dt
import json
import sys
from pathlib import Path
from typing import Annotated

import typer
from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from tram.adapters.base import RunnerUnavailableError
from tram.adapters.claude_code import ClaudeCodeRunner
from tram.adapters.fake import FakeRunner
from tram.context import TramContext, init_project, load_context
from tram.cr_store import CRStore
from tram.governance.gate_runner import GateRunner
from tram.governance.intent_guard import IntentGuard
from tram.metrics.evm import (
    compute_snapshot,
    escalate_breaches,
    evaluate_thresholds,
    latest_snapshot,
    save_snapshot,
)
from tram.metrics.kpis import mttr_report, rework_report
from tram.models.cr import Approval, CRStatus, CRType
from tram.models.events import EventKind
from tram.models.gates import GateStatus
from tram.models.task import TaskRecord, TaskSpec, TaskStatus
from tram.sandbox.worktree import WorktreeSession

app = typer.Typer(help="Tram 🚋 - governance rails for AI coding agents.", no_args_is_help=True)
baseline_app = typer.Typer(help="scope baseline (HITL)", no_args_is_help=True)
gate_app = typer.Typer(help="run phase gates", no_args_is_help=True)
guard_app = typer.Typer(help="intent guard: changes vs scope baseline", no_args_is_help=True)
agent_app = typer.Typer(help="run coding-agent tasks inside the sandbox", no_args_is_help=True)
cr_app = typer.Typer(help="change requests", no_args_is_help=True)
artifact_app = typer.Typer(help="governance artifacts (tickets)", no_args_is_help=True)
task_app = typer.Typer(help="task records (EVM data source)", no_args_is_help=True)
evm_app = typer.Typer(help="earned value management (SPI/CPI)", no_args_is_help=True)
app.add_typer(baseline_app, name="baseline")
app.add_typer(gate_app, name="gate")
app.add_typer(guard_app, name="guard")
app.add_typer(agent_app, name="agent")
app.add_typer(cr_app, name="cr")
app.add_typer(artifact_app, name="artifact")
app.add_typer(task_app, name="task")
app.add_typer(evm_app, name="evm")

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
        state = ctx.load_state()
        baseline = ctx.load_baseline()
        baseline.approved_by = by
        baseline.approved_at = _utcnow()
        baseline.dump(ctx.baseline_file)
        state.human_approvals.append(
            Approval(
                decision="approved",
                by=by,
                at=_utcnow(),
                kind="scope_baseline",
                artifact_ref=f"scope-baseline@v{baseline.version}",
                note=note,
            )
        )
        state.baselines["scope"] = f"scope-baseline@v{baseline.version}"
        ctx.state_store.save(state)
        ctx.events.append(
            EventKind.HUMAN_DECISION,
            source="tram.baseline",
            data={"decision": "approved", "kind": "scope_baseline", "by": by, "note": note},
        )
    except Exception as exc:  # noqa: BLE001
        _fail(exc)
        return
    console.print("[green]scope baseline approved ✅[/green]")


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
) -> None:
    """Run one agent task in a worktree sandbox, guarded by Intent Guard."""
    try:
        ctx = load_context()
        state = ctx.load_state()
    except Exception as exc:  # noqa: BLE001
        _fail(exc)
        return
    task_id = f"T-{state.next_task_seq:03d}"
    state.next_task_seq += 1

    if runner == "fake":
        writes = _load_json_plan(fake_plan)
        writes.update(_load_json_plan(fake_violation_plan))
        engine = FakeRunner(writes)
    elif runner == "claude":
        engine = ClaudeCodeRunner()
    else:
        _fail(ValueError(f"unknown runner '{runner}' (fake | claude)"))
        return

    spec = TaskSpec(
        id=task_id,
        prompt=prompt,
        allowed_tools=allowed_tools.split(",") if allowed_tools else [],
        max_turns=max_turns,
    )
    record = TaskRecord(
        id=task_id,
        title=prompt.splitlines()[0][:80],
        status=TaskStatus.DOING,
        est_points=points if points is not None else 1.0,
    )
    state.tasks.append(record)
    ctx.state_store.save(state)
    ctx.events.append(
        EventKind.AGENT_RUN_STARTED,
        source="tram.agent",
        data={"task": task_id, "runner": runner, "prompt": prompt[:500]},
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


def _decide_cr(cr_id: str, decision: str, by: str, note: str) -> None:
    try:
        ctx = load_context()
        store = CRStore(ctx.crs_dir)
        cr = store.load(cr_id)
        if cr is None:
            raise ValueError(f"unknown CR: {cr_id}")
        state = ctx.load_state()

        cr.approvals.append(
            Approval(
                decision=decision,
                by=by,
                at=_utcnow(),
                kind="cr",
                artifact_ref=cr.id,
                note=note,
            )
        )
        cr.status = CRStatus.REJECTED if decision == "rejected" else CRStatus.APPROVED
        store.save(cr)

        if decision == "approved" and cr.type == CRType.SCOPE:
            # 变更即分支：批准的范围变更直接进入基线（版本 +1），并视为已实施
            baseline = ctx.load_baseline()
            baseline.allowed_paths.extend(p for p in cr.impact.changed_paths)
            baseline.version += 1
            baseline.dump(ctx.baseline_file)
            cr.status = CRStatus.IMPLEMENTED
            store.save(cr)
            state.baselines["scope"] = f"scope-baseline@v{baseline.version}"
            console.print(
                f"[green]baseline v{baseline.version}: added {cr.impact.changed_paths}[/green]"
            )

        if cr.id in state.open_crs and cr.status not in (
            CRStatus.DRAFT,
            CRStatus.ANALYZING,
            CRStatus.AWAITING_HUMAN,
            CRStatus.APPROVED,
        ):
            state.open_crs.remove(cr.id)
        ctx.state_store.save(state)
        ctx.events.append(
            EventKind.CR_STATUS_CHANGED,
            source="tram.cr",
            data={"cr": cr.id, "status": cr.status.value, "by": by, "note": note},
            refs={"cr": cr.id},
        )
        ctx.events.append(
            EventKind.HUMAN_DECISION,
            source="tram.cr",
            data={"decision": decision, "kind": "cr", "cr": cr.id, "by": by, "note": note},
            refs={"cr": cr.id},
        )
    except Exception as exc:  # noqa: BLE001
        _fail(exc)
        return
    console.print(f"[green]cr {cr_id} -> {cr.status.value} ✅[/green]")


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


@app.command()
def kpi() -> None:
    """KPI dashboard: gate MTTR + rework rate (computed from the black box)."""
    try:
        ctx = load_context()
        state = ctx.load_state()
        report = mttr_report(list(ctx.events.read()))
        rework = rework_report(state)
    except Exception as exc:  # noqa: BLE001
        _fail(exc)
        return
    table = Table(title="📈 KPI dashboard")
    table.add_column("metric")
    table.add_column("value", overflow="fold")
    if report.gates or report.open_breaches:
        overall = f"{report.overall_mttr_seconds}s" if report.overall_mttr_seconds else "n/a"
        table.add_row("MTTR (all gates)", overall)
        for m in report.gates:
            table.add_row(f"  {m.gate_id}", f"MTTR {m.mttr_seconds}s ({m.breaches} breach(es))")
        for gate_id in report.open_breaches:
            table.add_row(f"  {gate_id}", "still red, not yet recovered ⏳")
    else:
        table.add_row("MTTR", "[dim]no gate breaches recorded[/dim]")
    if rework.tasks_done:
        table.add_row(
            "rework rate",
            f"{rework.rate:.1%} ({rework.tasks_with_rework}/{rework.tasks_done} done,"
            f" {rework.rework_events} rework event(s))",
        )
    else:
        table.add_row("rework rate", "[dim]no done tasks yet[/dim]")
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
) -> None:
    """Launch the read-only route-map UI (requires tram[ui])."""
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
    console.print(f"🚋 tram ui → {url}（Ctrl-C 退出；只读视图，操作请回 CLI）")
    if not no_open:
        threading.Timer(1.2, lambda: webbrowser.open(url)).start()
    uvicorn.run(create_app(repo), host=host, port=port, log_level="warning")


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
