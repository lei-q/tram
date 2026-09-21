"""Deterministic check registry: command / coverage / evidence.

Everything here is plain subprocess + parsing. No LLM ever decides a check.
"""

from __future__ import annotations

import json
import re
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path

from tram.evidence.git_client import GitClient
from tram.evidence.verifier import VerifyContext, verify_all
from tram.models.artifacts import Artifact
from tram.models.events import EventKind
from tram.models.gates import CheckResult, CheckSpec
from tram.obs.eventlog import EventLog

_TOTAL_RE = re.compile(r"^TOTAL.*?(\d+)%", re.MULTILINE)


@dataclass
class CheckContext:
    repo: Path
    artifacts_index: Path
    git: GitClient
    events: EventLog

    def load_artifacts(self) -> list[Artifact]:
        if not self.artifacts_index.exists():
            return []
        raw = json.loads(self.artifacts_index.read_text(encoding="utf-8"))
        return [Artifact.model_validate(item) for item in raw]


def run_check(spec: CheckSpec, ctx: CheckContext) -> CheckResult:
    started = time.monotonic()
    try:
        if spec.type.value == "command":
            status, output = _run_command(spec, ctx)
        elif spec.type.value == "coverage":
            status, output = _run_coverage(spec, ctx)
        elif spec.type.value == "evidence":
            status, output = _run_evidence(spec, ctx)
        else:
            status, output = "error", f"unknown check type: {spec.type}"
    except subprocess.TimeoutExpired:
        status, output = "fail", f"timeout after {spec.timeout_s}s"
    except Exception as exc:  # noqa: BLE001 - a check failure must never crash the gate
        status, output = "error", f"{type(exc).__name__}: {exc}"
    return CheckResult(
        id=spec.id,
        status=status,
        output=output[-4000:],
        duration_ms=int((time.monotonic() - started) * 1000),
    )


def _run_command(spec: CheckSpec, ctx: CheckContext) -> tuple[str, str]:
    if not spec.cmd:
        return "error", "command check without cmd"
    proc = subprocess.run(
        spec.cmd,
        cwd=ctx.repo,
        capture_output=True,
        text=True,
        timeout=spec.timeout_s,
    )
    expected = spec.expect.get("exit_code", 0)
    output = (proc.stdout + "\n" + proc.stderr).strip()
    status = "pass" if proc.returncode == expected else "fail"
    return status, f"exit={proc.returncode} (expected {expected})\n{output}"


def _run_coverage(spec: CheckSpec, ctx: CheckContext) -> tuple[str, str]:
    if not spec.cmd or spec.min_percent is None:
        return "error", "coverage check needs cmd and min_percent"
    proc = subprocess.run(
        spec.cmd,
        cwd=ctx.repo,
        capture_output=True,
        text=True,
        timeout=spec.timeout_s,
    )
    if proc.returncode != 0:
        return "fail", f"coverage command failed (exit={proc.returncode})\n{proc.stderr[-2000:]}"
    match = _TOTAL_RE.search(proc.stdout)
    if not match:
        return "fail", "no TOTAL coverage line found in output"
    percent = float(match.group(1))
    status = "pass" if percent >= spec.min_percent else "fail"
    return status, f"coverage {percent}% (min {spec.min_percent}%)"


def _run_evidence(spec: CheckSpec, ctx: CheckContext) -> tuple[str, str]:
    if spec.require == "artifact_present":
        if not spec.artifact:
            return "error", "artifact_present check needs artifact kind"
        kinds = {a.kind for a in ctx.load_artifacts()}
        ok = spec.artifact in kinds
        return ("pass" if ok else "fail"), f"artifact kind '{spec.artifact}' present: {ok}"

    if spec.require == "every_artifact_has_verified_evidence":
        artifacts = ctx.load_artifacts()
        if not artifacts:
            return "pass", "no artifacts registered (nothing to verify)"
        vctx = VerifyContext(repo=ctx.repo, git=ctx.git, events=ctx.events)
        problems: list[str] = []
        for artifact in artifacts:
            if not artifact.has_evidence:
                problems.append(f"{artifact.id}: no evidence")
                continue
            bad = verify_all(artifact.evidence, vctx)
            if bad:
                kinds = ", ".join(f"{e.kind.value}:{e.ref}" for e in bad)
                problems.append(f"{artifact.id}: unverified evidence [{kinds}]")
        if problems:
            return "fail", "; ".join(problems)
        return "pass", f"{len(artifacts)} artifact(s) fully evidenced"

    return "error", f"unknown evidence requirement: {spec.require}"


__all__ = ["CheckContext", "EventKind", "run_check"]
