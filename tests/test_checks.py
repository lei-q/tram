import sys
from pathlib import Path

from tram.evidence.git_client import GitClient
from tram.governance.checks import CheckContext, run_check
from tram.models.artifacts import Artifact
from tram.models.events import EventKind
from tram.models.evidence import Evidence, EvidenceKind
from tram.models.gates import CheckSpec, CheckType
from tram.obs.eventlog import EventLog


def _ctx(git_repo: Path, tmp_path: Path) -> CheckContext:
    return CheckContext(
        repo=git_repo,
        artifacts_index=tmp_path / "index.json",
        git=GitClient(git_repo),
        events=EventLog(tmp_path / "events.jsonl"),
    )


def test_command_check_pass_and_fail(tmp_path: Path):
    from tram.evidence.git_client import GitClient  # noqa: F401  (import sanity)

    ctx = CheckContext(
        repo=tmp_path,
        artifacts_index=tmp_path / "index.json",
        git=GitClient(tmp_path),
        events=EventLog(tmp_path / "events.jsonl"),
    )
    ok = run_check(CheckSpec(id="ok", type=CheckType.COMMAND, cmd=["true"]), ctx)
    bad = run_check(CheckSpec(id="bad", type=CheckType.COMMAND, cmd=["false"]), ctx)
    expect_fail = run_check(
        CheckSpec(id="ef", type=CheckType.COMMAND, cmd=["false"], expect={"exit_code": 1}), ctx
    )
    assert ok.status == "pass"
    assert bad.status == "fail"
    assert expect_fail.status == "pass"


def test_coverage_check_parses_total(tmp_path: Path):
    ctx = CheckContext(
        repo=tmp_path,
        artifacts_index=tmp_path / "index.json",
        git=GitClient(tmp_path),
        events=EventLog(tmp_path / "events.jsonl"),
    )
    cmd = [sys.executable, "-c", "print('name stmts miss cover'); print('TOTAL   10    5    50%')"]
    low = run_check(CheckSpec(id="cov", type=CheckType.COVERAGE, cmd=cmd, min_percent=60), ctx)
    high = run_check(CheckSpec(id="cov", type=CheckType.COVERAGE, cmd=cmd, min_percent=40), ctx)
    assert low.status == "fail"
    assert high.status == "pass"
    assert "50.0%" in high.output


def test_evidence_check_empty_index_passes(git_repo: Path, tmp_path: Path):
    ctx = _ctx(git_repo, tmp_path)
    result = run_check(
        CheckSpec(id="ev", type=CheckType.EVIDENCE, require="every_artifact_has_verified_evidence"),
        ctx,
    )
    assert result.status == "pass"
    assert "no artifacts" in result.output


def test_evidence_check_verifies_commits(git_repo: Path, tmp_path: Path):
    ctx = _ctx(git_repo, tmp_path)
    sha = ctx.git.current_sha()
    good = Artifact(
        id="a1",
        kind="wbs",
        path=".tram/artifacts/wbs.md",
        evidence=[Evidence(kind=EvidenceKind.COMMIT, ref=sha)],
    )
    bad = Artifact(
        id="a2",
        kind="wbs",
        path=".tram/artifacts/wbs2.md",
        evidence=[Evidence(kind=EvidenceKind.COMMIT, ref="0" * 40)],
    )
    ctx.artifacts_index.write_text(
        f"[{good.model_dump_json()}, {bad.model_dump_json()}]", encoding="utf-8"
    )
    result = run_check(
        CheckSpec(id="ev", type=CheckType.EVIDENCE, require="every_artifact_has_verified_evidence"),
        ctx,
    )
    assert result.status == "fail"
    assert "a2" in result.output


def test_evidence_check_event_kind(git_repo: Path, tmp_path: Path):
    ctx = _ctx(git_repo, tmp_path)
    event = ctx.events.append(EventKind.PROJECT_INITIALIZED, "test")
    art = Artifact(
        id="a1",
        kind="charter",
        path="c.md",
        evidence=[Evidence(kind=EvidenceKind.EVENT, ref=str(event.seq))],
    )
    ctx.artifacts_index.write_text(f"[{art.model_dump_json()}]", encoding="utf-8")
    result = run_check(
        CheckSpec(id="ev", type=CheckType.EVIDENCE, require="every_artifact_has_verified_evidence"),
        ctx,
    )
    assert result.status == "pass"


def test_artifact_present_requirement(git_repo: Path, tmp_path: Path):
    ctx = _ctx(git_repo, tmp_path)
    art = Artifact(id="a1", kind="charter", path="c.md", evidence=[])
    ctx.artifacts_index.write_text(f"[{art.model_dump_json()}]", encoding="utf-8")
    present = run_check(
        CheckSpec(id="p", type=CheckType.EVIDENCE, require="artifact_present", artifact="charter"),
        ctx,
    )
    absent = run_check(
        CheckSpec(id="p", type=CheckType.EVIDENCE, require="artifact_present", artifact="wbs"), ctx
    )
    assert present.status == "pass"
    assert absent.status == "fail"
