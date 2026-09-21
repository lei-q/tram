"""The packaged demo project must survive the real (pytest-based) quality gate."""

import sys
from pathlib import Path

from tram.evidence.git_client import GitClient
from tram.governance.checks import CheckContext, run_check
from tram.models.gates import CheckSpec, CheckType
from tram.obs.eventlog import EventLog

DEMO = Path(__file__).resolve().parents[1] / "examples" / "demo-project"


def _ctx(tmp_path: Path) -> CheckContext:
    return CheckContext(
        repo=DEMO,
        artifacts_index=tmp_path / "index.json",
        git=GitClient(DEMO),
        events=EventLog(tmp_path / "events.jsonl"),
    )


def test_demo_tests_pass(tmp_path: Path):
    result = run_check(
        CheckSpec(id="tests", type=CheckType.COMMAND, cmd=[sys.executable, "-m", "pytest", "-q"]),
        _ctx(tmp_path),
    )
    assert result.status == "pass", result.output


def test_demo_coverage_meets_floor(tmp_path: Path):
    result = run_check(
        CheckSpec(
            id="coverage",
            type=CheckType.COVERAGE,
            cmd=[
                sys.executable,
                "-m",
                "pytest",
                "-q",
                "--cov=.",
                "--cov-report=term",
            ],
            min_percent=40,
        ),
        _ctx(tmp_path),
    )
    assert result.status == "pass", result.output
