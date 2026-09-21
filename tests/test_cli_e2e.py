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
