"""End-to-end vertical slice: init -> HITL -> guard block/CR -> commit -> gate."""

import json

import yaml
from typer.testing import CliRunner

from tram.cli import app
from tram.cr_store import CRStore
from tram.models.events import EventKind
from tram.models.state import ProjectState

runner = CliRunner()


def _write_gates(git_repo, checks_body) -> None:
    gates = {"gates": {"g2_quality_gate": {"checks": checks_body}}}
    (git_repo / ".tram" / "gates.yaml").write_text(
        yaml.safe_dump(gates, sort_keys=False), encoding="utf-8"
    )


def test_full_governance_slice(git_repo, monkeypatch):
    monkeypatch.chdir(git_repo)

    # 0. init the governance directory
    result = runner.invoke(app, ["init", "--name", "demo"])
    assert result.exit_code == 0, result.output

    # 1. HITL: approve the scope baseline
    result = runner.invoke(app, ["baseline", "approve", "--by", "lay"])
    assert result.exit_code == 0, result.output
    assert "approved" in result.output

    state = ProjectState.model_validate(
        json.loads((git_repo / ".tram" / "state.json").read_text(encoding="utf-8"))
    )
    assert any(a.kind == "scope_baseline" for a in state.human_approvals)

    # 1b. artifacts: charter -> G0 pass (initiating -> planning)
    result = runner.invoke(app, ["artifact", "generate", "charter"])
    assert result.exit_code == 0, result.output
    result = runner.invoke(app, ["gate", "run", "g0_charter_gate"])
    assert result.exit_code == 0, result.output
    state = ProjectState.model_validate(
        json.loads((git_repo / ".tram" / "state.json").read_text(encoding="utf-8"))
    )
    assert state.phase == "planning"

    # 1c. all artifacts -> G1 pass (planning -> executing)
    result = runner.invoke(app, ["artifact", "generate", "--all"])
    assert result.exit_code == 0, result.output
    result = runner.invoke(app, ["gate", "run", "g1_planning_gate"])
    assert result.exit_code == 0, result.output
    state = ProjectState.model_validate(
        json.loads((git_repo / ".tram" / "state.json").read_text(encoding="utf-8"))
    )
    assert state.phase == "executing"

    # 2. quality gate passes with green deterministic checks
    _write_gates(
        git_repo,
        [
            {"id": "ok_cmd", "type": "command", "cmd": ["true"]},
            {"id": "ev", "type": "evidence", "require": "every_artifact_has_verified_evidence"},
        ],
    )
    result = runner.invoke(app, ["gate", "run", "g2_quality_gate"])
    assert result.exit_code == 0, result.output
    assert "pass" in result.output

    # 3. quality gate fails and exits non-zero when a check fails
    _write_gates(
        git_repo,
        [
            {"id": "bad_cmd", "type": "command", "cmd": ["false"]},
            {"id": "ev", "type": "evidence", "require": "every_artifact_has_verified_evidence"},
        ],
    )
    result = runner.invoke(app, ["gate", "run", "g2_quality_gate"])
    assert result.exit_code == 1
    assert "fail" in result.output

    # 4. guard check blocks an out-of-scope path and drafts a CR
    result = runner.invoke(app, ["guard", "check", "docs/out-of-scope.txt"])
    assert result.exit_code == 1
    assert "INTENT BLOCKED" in result.output

    crs = CRStore(git_repo / ".tram" / "crs").open_crs()
    assert len(crs) == 1
    assert crs[0].type.value == "scope"
    assert crs[0].impact.changed_paths == ["docs/out-of-scope.txt"]

    # 5. agent task with an out-of-scope write is blocked (fake runner)
    plan_ok = git_repo / "plan-ok.json"
    plan_bad = git_repo / "plan-bad.json"
    plan_ok.write_text(json.dumps({"src/feature.py": "x = 1\n"}), encoding="utf-8")
    plan_bad.write_text(json.dumps({"docs/evil.txt": "nope\n"}), encoding="utf-8")

    result = runner.invoke(
        app,
        [
            "agent",
            "run",
            "--runner",
            "fake",
            "--prompt",
            "add feature",
            "--fake-plan",
            str(plan_ok),
            "--fake-violation-plan",
            str(plan_bad),
        ],
    )
    assert result.exit_code == 0, result.output
    assert "BLOCKED" in result.output

    state = ProjectState.model_validate(
        json.loads((git_repo / ".tram" / "state.json").read_text(encoding="utf-8"))
    )
    assert "T-001" in [t.id for t in state.tasks]
    blocked_task = next(t for t in state.tasks if t.id == "T-001")
    assert blocked_task.status.value == "blocked"
    crs = CRStore(git_repo / ".tram" / "crs").open_crs()
    assert len(crs) == 2

    # black box recorded the whole story
    from tram.obs.eventlog import EventLog

    events = EventLog(git_repo / ".tram" / "events" / "events.jsonl")
    kinds = [e.kind for e in events.read()]
    assert EventKind.INTENT_BLOCKED in kinds
    assert EventKind.CR_CREATED in kinds
    assert EventKind.AGENT_RUN_STARTED in kinds
    assert EventKind.GATE_EVALUATED in kinds
    assert EventKind.ARTIFACT_GENERATED in kinds

    # 6. a fully in-scope agent task is committed to a tram branch
    result = runner.invoke(
        app,
        [
            "agent",
            "run",
            "--runner",
            "fake",
            "--prompt",
            "add feature 2",
            "--fake-plan",
            str(plan_ok),
        ],
    )
    assert result.exit_code == 0, result.output
    assert "committed" in result.output

    state = ProjectState.model_validate(
        json.loads((git_repo / ".tram" / "state.json").read_text(encoding="utf-8"))
    )
    done_task = next(t for t in state.tasks if t.id == "T-002")
    assert done_task.status.value == "done"
    assert done_task.commit_refs and done_task.commit_refs[0]

    # 7. quality gate stays red while a scope CR is open, green after rejection
    _write_gates(
        git_repo,
        [
            {"id": "ok_cmd", "type": "command", "cmd": ["true"]},
            {"id": "ev", "type": "evidence", "require": "every_artifact_has_verified_evidence"},
        ],
    )
    result = runner.invoke(app, ["gate", "run", "g2_quality_gate"])
    assert result.exit_code == 1
    assert "open scope change request" in result.output

    for cr_id in ("cr-0001", "cr-0002"):
        result = runner.invoke(app, ["cr", "reject", cr_id, "--by", "lay"])
        assert result.exit_code == 0, result.output

    result = runner.invoke(app, ["gate", "run", "g2_quality_gate"])
    assert result.exit_code == 0, result.output

    state = ProjectState.model_validate(
        json.loads((git_repo / ".tram" / "state.json").read_text(encoding="utf-8"))
    )
    assert state.open_crs == []

    # 8. replay renders the black box (wide console so rich does not truncate kinds)
    result = runner.invoke(app, ["replay", "--limit", "50"], env={"COLUMNS": "220"})
    assert result.exit_code == 0
    assert "agent_run_finished" in result.output
    assert "cr_status_changed" in result.output

    # 9. EVM: inflate T-001 (blocked, earns nothing) -> SPI/CPI breach -> auto risks
    result = runner.invoke(app, ["task", "points", "T-001", "--est", "8", "--spent", "8"])
    assert result.exit_code == 0, result.output
    result = runner.invoke(app, ["evm", "snapshot"])
    assert result.exit_code == 0, result.output
    assert "risk registered" in result.output

    state = ProjectState.model_validate(
        json.loads((git_repo / ".tram" / "state.json").read_text(encoding="utf-8"))
    )
    from datetime import date as _date

    today = _date.today().isoformat()
    # EVM 越界入险 + Guard 拦截入险（第二十三批起越界也进风险登记册）
    assert {r.id for r in state.risks} == {
        f"r-evm-{today}-spi",
        f"r-evm-{today}-cpi",
        "r-guard-cr-0001",
    }

    # 10. KPI dashboard: the step-3/step-7 gate failures closed by later passes
    result = runner.invoke(app, ["kpi"], env={"COLUMNS": "220"})
    assert result.exit_code == 0, result.output
    assert "MTTR" in result.output
    assert "breach" in result.output

    result = runner.invoke(app, ["status"], env={"COLUMNS": "220"})
    assert result.exit_code == 0
    assert "evm (latest)" in result.output

    # 11. QA loop: fail T-002 -> rework task T-003 -> dev fixes it -> qa pass
    result = runner.invoke(app, ["qa", "fail", "T-002", "--note", "edge case broken", "--by", "qa"])
    assert result.exit_code == 0, result.output
    assert "rework task T-003" in result.output

    state = ProjectState.model_validate(
        json.loads((git_repo / ".tram" / "state.json").read_text(encoding="utf-8"))
    )
    rework = next(t for t in state.tasks if t.id == "T-003")
    assert rework.rework_of == "T-002"
    assert rework.status.value == "todo"
    assert state.task("T-002").rework_count == 1

    result = runner.invoke(
        app,
        [
            "agent",
            "run",
            "--task",
            "T-003",
            "--role",
            "dev",
            "--runner",
            "fake",
            "--prompt",
            "fix the edge case",
            "--fake-plan",
            str(plan_ok),
        ],
    )
    assert result.exit_code == 0, result.output
    assert "committed" in result.output

    result = runner.invoke(
        app, ["qa", "pass", "T-003", "--note", "reproduction green", "--by", "qa"]
    )
    assert result.exit_code == 0, result.output

    state = ProjectState.model_validate(
        json.loads((git_repo / ".tram" / "state.json").read_text(encoding="utf-8"))
    )
    assert state.task("T-003").status.value == "done"
    assert {t.id for t in state.tasks} == {"T-001", "T-002", "T-003"}

    # 11b. the defect recurs after a verified fix -> escaped (QA missed it)
    result = runner.invoke(app, ["qa", "fail", "T-002", "--note", "regression", "--by", "qa"])
    assert result.exit_code == 0, result.output

    # 12. defect MTTR pairs qa_failed(T-003) with qa_passed(T-003); rework 1/2, escape 1/2
    result = runner.invoke(app, ["kpi"], env={"COLUMNS": "220"})
    assert result.exit_code == 0, result.output
    assert "MTTR 缺陷" in result.output
    assert "T-003" in result.output
    assert "rework rate" in result.output
    assert "50.0%" in result.output  # 1 of 2 done tasks has rework
    assert "escape rate" in result.output
    assert "50.0% (1/2" in result.output  # the recurrence counts as escaped

    kinds = [e.kind for e in events.read()]
    assert EventKind.QA_FAILED in kinds
    assert EventKind.QA_PASSED in kinds
