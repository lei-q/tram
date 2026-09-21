"""T1.12: artifact generator - evidence-linked, idempotent, gate-ready."""

import json
from pathlib import Path

import yaml

from tram.artifacts.generator import ARTIFACT_KINDS, ArtifactGenerator
from tram.evidence.git_client import GitClient
from tram.governance.checks import CheckContext, run_check
from tram.models.artifacts import Artifact
from tram.models.events import EventKind
from tram.models.gates import CheckSpec, CheckType
from tram.obs.eventlog import EventLog


def _index(ctx) -> list[Artifact]:
    return [
        Artifact.model_validate(obj)
        for obj in json.loads(ctx.artifacts_index.read_text(encoding="utf-8"))
    ]


def _frontmatter(path: Path) -> dict:
    text = path.read_text(encoding="utf-8")
    assert text.startswith("---\n")
    raw = text.split("---", 2)[1]
    return yaml.safe_load(raw)


def test_generate_charter_with_verified_evidence(ctx):
    artifact = ArtifactGenerator(ctx).generate("charter")
    target = ctx.repo / artifact.path
    assert target.exists()
    fm = _frontmatter(target)
    assert fm["artifact"] == "charter"
    kinds = {e["kind"] for e in fm["evidence"]}
    assert kinds == {"commit", "event"}
    assert all(e["verified"] for e in fm["evidence"])
    assert _index(ctx)[0].kind == "charter"


def test_regenerate_is_idempotent(ctx):
    gen = ArtifactGenerator(ctx)
    gen.generate("charter")
    gen.generate("charter")
    items = _index(ctx)
    assert len(items) == 1  # upsert by kind, never duplicates


def test_generate_all_kinds_satisfy_g1(ctx):
    gen = ArtifactGenerator(ctx)
    for kind in ARTIFACT_KINDS:
        gen.generate(kind)
    items = _index(ctx)
    assert {a.kind for a in items} == set(ARTIFACT_KINDS)
    assert all(a.verified for a in items)

    check_ctx = CheckContext(
        repo=ctx.repo,
        artifacts_index=ctx.artifacts_index,
        git=GitClient(ctx.repo),
        events=EventLog(ctx.repo / ".tram" / "events" / "events.jsonl"),
    )
    result = run_check(
        CheckSpec(id="ev", type=CheckType.EVIDENCE, require="every_artifact_has_verified_evidence"),
        check_ctx,
    )
    assert result.status == "pass", result.output
    assert "6 artifact(s)" in result.output


def test_risk_register_derives_from_intent_blocked(ctx):
    ctx.events.append(
        EventKind.INTENT_BLOCKED,
        source="test",
        data={"violations": ["docs/evil.txt"]},
    )
    artifact = ArtifactGenerator(ctx).generate("risk_register")
    body = (ctx.repo / artifact.path).read_text(encoding="utf-8")
    assert "docs/evil.txt" in body
    assert "r-auto-" in body
    fm = _frontmatter(ctx.repo / artifact.path)
    event_refs = [e for e in fm["evidence"] if e["kind"] == "event"]
    assert event_refs  # the triggering event is linked as evidence


def test_scope_baseline_gap_when_unapproved(ctx):
    artifact = ArtifactGenerator(ctx).generate("scope_baseline")
    fm = _frontmatter(ctx.repo / artifact.path)
    assert fm["evidence_gap"]
    assert "baseline approve" in fm["evidence_gap"][0]


def test_unknown_kind_rejected(ctx):
    import pytest

    with pytest.raises(KeyError):
        ArtifactGenerator(ctx).generate("nope")


def test_wbs_lists_agent_tasks(ctx, git_repo, monkeypatch):
    """After an in-scope agent task commits, WBS shows the task row."""
    from typer.testing import CliRunner

    from tram.cli import app

    monkeypatch.chdir(git_repo)
    plan = git_repo / "plan.json"
    plan.write_text(json.dumps({"src/x.py": "y = 2\n"}), encoding="utf-8")
    runner = CliRunner()
    result = runner.invoke(
        app, ["agent", "run", "--runner", "fake", "--prompt", "add x", "--fake-plan", str(plan)]
    )
    assert result.exit_code == 0, result.output

    artifact = ArtifactGenerator(ctx).generate("wbs")
    body = (ctx.repo / artifact.path).read_text(encoding="utf-8")
    assert "T-001" in body and "add x" in body
