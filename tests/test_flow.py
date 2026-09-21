"""T1.10: the governance line as a state machine (LangGraph wrapper + interpreter)."""

from __future__ import annotations

import datetime as dt
import json

import pytest
import yaml
from typer.testing import CliRunner

from tram.cli import app
from tram.models.gates import GateResult, GateStatus
from tram.obs.eventlog import EventLog
from tram.orchestration.graph import build_langgraph, invoke_flow, walk_flow
from tram.orchestration.phases import apply_gate_result

runner_cli = CliRunner()

_ALL_GATES = ("g0_charter_gate", "g1_planning_gate", "g2_quality_gate", "g3_closing_gate")


class FakeGateRunner:
    """Same surface as GateRunner for graph tests; advances phase like the real one."""

    def __init__(self, ctx, script: dict[str, GateResult]):
        self.ctx = ctx
        self.script = script
        self.calls: list[str] = []

    def run(self, gate_id: str) -> GateResult:
        self.calls.append(gate_id)
        result = self.script[gate_id]
        state = self.ctx.load_state()
        apply_gate_result(state, result)
        self.ctx.state_store.save(state)
        return result


def _result(gate_id: str, status: GateStatus, round_num: int = 0) -> GateResult:
    return GateResult(
        gate_id=gate_id,
        ts=dt.datetime.now(dt.UTC),
        status=status,
        checks=[],
        decision_reasons=["planned failure"] if status != GateStatus.PASS else [],
        remediation_round=round_num,
    )


def test_walk_flow_green_line_to_terminus(ctx):
    script = {g: _result(g, GateStatus.PASS) for g in _ALL_GATES}
    bot = FakeGateRunner(ctx, script)
    final = walk_flow(bot)
    assert bot.calls == list(_ALL_GATES)
    assert final["phase"] == "done"
    assert final["stop_reason"] == "已达终点站，全线走完 🎉"
    assert len(final["journey"]) == 4


def test_walk_flow_stops_at_red_light(ctx):
    script = {g: _result(g, GateStatus.PASS) for g in _ALL_GATES}
    script["g1_planning_gate"] = _result("g1_planning_gate", GateStatus.FAIL, round_num=1)
    bot = FakeGateRunner(ctx, script)
    final = walk_flow(bot)
    assert bot.calls == ["g0_charter_gate", "g1_planning_gate"]
    assert "g1_planning_gate fail" in final["stop_reason"]
    assert "第 1 轮整改" in final["stop_reason"]


def test_walk_flow_stops_when_blocked_pending_human(ctx):
    script = {g: _result(g, GateStatus.PASS) for g in _ALL_GATES}
    script["g2_quality_gate"] = _result(
        "g2_quality_gate", GateStatus.BLOCKED_PENDING_HUMAN, round_num=4
    )
    bot = FakeGateRunner(ctx, script)
    final = walk_flow(bot)
    assert bot.calls == ["g0_charter_gate", "g1_planning_gate", "g2_quality_gate"]
    assert "blocked_pending_human" in final["stop_reason"]


def test_walk_flow_guard_against_out_of_order_pass_loop(ctx):
    """A pass that does not advance the phase must not loop forever."""
    bot = FakeGateRunner(ctx, {g: _result(g, GateStatus.PASS) for g in _ALL_GATES})
    state = {
        "phase": "planning",  # fake ctx starts at initiating; override so g1 is next
        "evaluated": [
            {"gate_id": "g1_planning_gate", "status": "pass", "reasons": [], "remediation_round": 0}
        ],
        "journey": [],
    }
    final = walk_flow(bot, state)
    assert bot.calls == []
    assert "越序记录" in final["stop_reason"]


def test_langgraph_and_interpreter_agree(ctx):
    """Both engines walk the same nodes: identical journey on green and red lines."""
    pytest.importorskip("langgraph")
    assert build_langgraph(FakeGateRunner(ctx, {})) is not None

    def reset():
        from tram.models.state import ProjectState

        ctx.state_store.save(ProjectState(project_name="demo"))
        return ctx

    green = {g: _result(g, GateStatus.PASS) for g in _ALL_GATES}
    red = {g: _result(g, GateStatus.PASS) for g in _ALL_GATES}
    red["g1_planning_gate"] = _result("g1_planning_gate", GateStatus.FAIL, round_num=2)

    lg_green = build_langgraph(FakeGateRunner(reset(), green)).invoke({"phase": "initiating"})
    it_green = walk_flow(FakeGateRunner(reset(), green))
    assert lg_green["journey"] == it_green["journey"]
    assert lg_green["stop_reason"] == it_green["stop_reason"]

    lg_red = build_langgraph(FakeGateRunner(reset(), red)).invoke({"phase": "initiating"})
    it_red = walk_flow(FakeGateRunner(reset(), red))
    assert lg_red["stop_reason"] == it_red["stop_reason"]
    assert "第 2 轮整改" in lg_red["stop_reason"]


def test_invoke_flow_reports_engine(ctx):
    final, engine = invoke_flow(
        FakeGateRunner(ctx, {g: _result(g, GateStatus.PASS) for g in _ALL_GATES})
    )
    assert engine in ("langgraph", "interpreter")
    assert final["stop_reason"]


# ---------- CLI: tram run ----------


def _write_gates(git_repo, cmd_by_gate: dict[str, str]) -> None:
    gates = {
        "gates": {
            gate: {"checks": [{"id": "c", "type": "command", "cmd": [cmd]}]}
            for gate, cmd in cmd_by_gate.items()
        }
    }
    (git_repo / ".tram" / "gates.yaml").write_text(
        yaml.safe_dump(gates, sort_keys=False), encoding="utf-8"
    )


def _init_and_approve(git_repo, monkeypatch) -> None:
    monkeypatch.chdir(git_repo)
    assert runner_cli.invoke(app, ["init", "--name", "demo"]).exit_code == 0
    assert runner_cli.invoke(app, ["baseline", "approve", "--by", "lay"]).exit_code == 0


def test_cli_run_full_line_with_release_hitl(git_repo, monkeypatch):
    """Auto-advance through g0-g2; g3 stops for the human release approval (HITL),
    and after `tram approve release` the line completes to the terminus."""
    _init_and_approve(git_repo, monkeypatch)
    assert runner_cli.invoke(app, ["artifact", "generate", "--all"]).exit_code == 0
    _write_gates(git_repo, {g: "true" for g in _ALL_GATES})

    result = runner_cli.invoke(app, ["run"], env={"COLUMNS": "220"})
    assert result.exit_code == 0, result.output
    assert result.output.count("PASS") == 3  # g0, g1, g2
    assert "g3_closing_gate" in result.output
    assert "release awaiting human approval" in result.output

    state = json.loads((git_repo / ".tram" / "state.json").read_text(encoding="utf-8"))
    assert state["phase"] == "closing"

    # the human grants the release, and the line completes
    result = runner_cli.invoke(app, ["approve", "release", "--by", "lay"])
    assert result.exit_code == 0, result.output
    result = runner_cli.invoke(app, ["run"], env={"COLUMNS": "220"})
    assert result.exit_code == 0, result.output
    assert "终点站" in result.output

    state = json.loads((git_repo / ".tram" / "state.json").read_text(encoding="utf-8"))
    assert state["phase"] == "done"


def test_cli_run_stops_at_red_light(git_repo, monkeypatch):
    _init_and_approve(git_repo, monkeypatch)
    _write_gates(git_repo, {"g0_charter_gate": "true", "g1_planning_gate": "false"})

    result = runner_cli.invoke(app, ["run"], env={"COLUMNS": "220"})
    assert result.exit_code == 0  # the journey succeeds; the stop is reported, not an error
    assert "g1_planning_gate" in result.output
    assert "整改" in result.output

    kinds = [e.kind.value for e in EventLog(git_repo / ".tram" / "events" / "events.jsonl").read()]
    assert kinds.count("gate_evaluated") == 2
