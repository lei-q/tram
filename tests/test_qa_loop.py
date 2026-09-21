"""QA reproduce-fix loop: qa fail -> rework task -> agent fix -> qa pass -> MTTR."""

from __future__ import annotations

import json

from typer.testing import CliRunner

from tram.cli import (
    _render_role_prompt,  # noqa: PLC2701 - white-box test of the helper
    app,
)
from tram.models.events import EventKind
from tram.models.task import TaskRecord, TaskStatus

runner = CliRunner()


def _seed_task(ctx, **kwargs) -> TaskRecord:
    state = ctx.load_state()
    record = TaskRecord(id="T-001", title="add mul", status=TaskStatus.DONE, est_points=4, **kwargs)
    state.tasks.append(record)
    ctx.state_store.save(state)
    return record


def test_init_installs_customizable_roles(ctx):
    for role in ("pm", "qa", "dev"):
        role_file = ctx.roles_dir / f"{role}.md"
        assert role_file.exists()
        assert "{{ task_prompt }}" in role_file.read_text(encoding="utf-8")


def test_render_role_prompt_uses_custom_template(ctx):
    (ctx.roles_dir / "dev.md").write_text(
        "custom for {{ task_id }}: {{ task_prompt }}", encoding="utf-8"
    )
    rendered = _render_role_prompt(ctx, "dev", "T-001", "实现乘法")
    assert rendered == "custom for T-001: 实现乘法"
    # packaged fallback when no custom template exists
    (ctx.roles_dir / "qa.md").unlink()
    assert "复现" in _render_role_prompt(ctx, "qa", "T-001", "验证乘法")


def test_render_unknown_role_fails_clean(ctx):
    import pytest

    with pytest.raises(Exception, match="not found"):
        _render_role_prompt(ctx, "boss", "T-001", "x")


def test_qa_fail_creates_rework_task_chain(ctx, monkeypatch):
    monkeypatch.chdir(ctx.repo)
    _seed_task(ctx)

    result = runner.invoke(
        app, ["qa", "fail", "T-001", "--note", "mul(2,3) 返回 5", "--by", "tester"]
    )
    assert result.exit_code == 0, result.output
    assert "rework task T-002" in result.output

    state = ctx.load_state()
    original = state.task("T-001")
    rework = state.task("T-002")
    assert original.rework_count == 1
    assert rework.rework_of == "T-001"
    assert rework.status == TaskStatus.TODO
    assert rework.est_points == 4  # schedule impact carries over
    assert "修复 T-001" in rework.title

    kinds = [e.kind for e in ctx.events.read()]
    assert kinds.count(EventKind.QA_FAILED) == 1
    event = next(e for e in ctx.events.read() if e.kind == EventKind.QA_FAILED)
    assert event.data["rework_task"] == "T-002"
    assert event.data["note"] == "mul(2,3) 返回 5"

    # a second defect on the same task opens another rework link
    result = runner.invoke(app, ["qa", "fail", "T-001", "--note", "still wrong"])
    assert result.exit_code == 0
    state = ctx.load_state()
    assert state.task("T-001").rework_count == 2
    assert state.task("T-003").rework_of == "T-001"


def test_qa_fail_unknown_task(ctx, monkeypatch):
    monkeypatch.chdir(ctx.repo)
    result = runner.invoke(app, ["qa", "fail", "T-999"])
    assert result.exit_code == 1
    assert "unknown task" in result.output


def test_full_loop_fail_fix_pass_closes_defect_mttr(ctx, monkeypatch):
    monkeypatch.chdir(ctx.repo)
    _seed_task(ctx)
    plan_ok = ctx.repo / "plan-ok.json"
    plan_ok.write_text(json.dumps({"src/fix.py": "x = 1\n"}), encoding="utf-8")

    assert runner.invoke(app, ["qa", "fail", "T-001", "--note", "broken"]).exit_code == 0

    # dev fixes the rework task through the agent rails (role prompt attached)
    result = runner.invoke(
        app,
        [
            "agent",
            "run",
            "--task",
            "T-002",
            "--role",
            "dev",
            "--runner",
            "fake",
            "--prompt",
            "fix mul",
            "--fake-plan",
            str(plan_ok),
        ],
    )
    assert result.exit_code == 0, result.output
    assert "committed" in result.output
    state = ctx.load_state()
    assert state.task("T-002").status == TaskStatus.DONE
    assert state.task("T-002").spent_points == 4

    started = next(e for e in ctx.events.read() if e.kind == EventKind.AGENT_RUN_STARTED)
    assert started.data["role"] == "dev"
    assert started.data["task"] == "T-002"  # attached, not a new task id

    result = runner.invoke(app, ["qa", "pass", "T-002", "--note", "复现测试转绿", "--by", "tester"])
    assert result.exit_code == 0, result.output
    kinds = [e.kind for e in ctx.events.read()]
    assert EventKind.QA_PASSED in kinds

    result = runner.invoke(app, ["kpi"], env={"COLUMNS": "220"})
    assert result.exit_code == 0, result.output
    assert "MTTR 缺陷" in result.output
    assert "T-002" in result.output  # defect paired on the rework task id

    # no tasks were double-created by --task
    state = ctx.load_state()
    assert [t.id for t in state.tasks] == ["T-001", "T-002"]


def test_agent_run_rejects_done_task_and_unknown_role(ctx, monkeypatch):
    monkeypatch.chdir(ctx.repo)
    state = ctx.load_state()
    state.tasks.append(TaskRecord(id="T-001", title="done", status=TaskStatus.DONE))
    ctx.state_store.save(state)

    result = runner.invoke(app, ["agent", "run", "--task", "T-001", "--prompt", "x"])
    assert result.exit_code == 1
    assert "cannot attach" in result.output

    result = runner.invoke(app, ["agent", "run", "--prompt", "x", "--role", "boss"])
    assert result.exit_code == 1
    assert "unknown role" in result.output

    result = runner.invoke(app, ["agent", "run", "--task", "T-004", "--prompt", "x"])
    assert result.exit_code == 1
    assert "unknown task" in result.output
