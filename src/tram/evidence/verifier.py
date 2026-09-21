"""Evidence verification - deterministic, local-first, no LLM.

issue/pr evidence requires external API integration (Phase 2) and is
reported as unverified rather than trusted blindly.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from tram.evidence.git_client import GitClient
from tram.models.events import EventKind
from tram.models.evidence import Evidence, EvidenceKind
from tram.obs.eventlog import EventLog


@dataclass
class VerifyContext:
    repo: Path
    git: GitClient
    events: EventLog


def verify_evidence(evidence: Evidence, ctx: VerifyContext) -> bool:
    if evidence.kind == EvidenceKind.COMMIT:
        return ctx.git.commit_exists(evidence.ref)
    if evidence.kind in (EvidenceKind.TEST_REPORT, EvidenceKind.ADR, EvidenceKind.SCAN):
        return (ctx.repo / evidence.ref).is_file()
    if evidence.kind == EvidenceKind.EVENT:
        try:
            return ctx.events.contains_seq(int(evidence.ref))
        except ValueError:
            return False
    # issue / pr: external verification arrives with the GitHub integration.
    return False


def verify_all(items: list[Evidence], ctx: VerifyContext) -> list[Evidence]:
    """Return the subset that fails local verification."""
    return [e for e in items if not verify_evidence(e, ctx)]


__all__ = ["VerifyContext", "verify_all", "verify_evidence", "EventKind"]
