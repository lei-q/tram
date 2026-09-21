"""tram CLI - thin commands over the governance services."""

from __future__ import annotations

import datetime as dt
import json
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
app.add_typer(baseline_app, name="baseline")
app.add_typer(gate_app, name="gate")
app.add_typer(guard_app, name="guard")
app.add_typer(agent_app, name="agent")
app.add_typer(cr_app, name="cr")

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
    record = TaskRecord(id=task_id, title=prompt.splitlines()[0][:80], status=TaskStatus.DOING)
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


def main() -> None:
    app()


if __name__ == "__main__":
    main()
