"""Evidence links - every artifact claim must trace back to something real."""

from __future__ import annotations

import enum

from pydantic import BaseModel

# Locally verifiable kinds: commit / test_report / adr / scan / event.
# issue / pr require external integration (Phase 2) and are not yet verifiable.
VERIFIABLE_KINDS = {"commit", "test_report", "adr", "scan", "event"}
EXTERNAL_KINDS = {"issue", "pr"}


class EvidenceKind(enum.StrEnum):
    COMMIT = "commit"
    PR = "pr"
    TEST_REPORT = "test_report"
    ADR = "adr"
    ISSUE = "issue"
    EVENT = "event"
    SCAN = "scan"


class Evidence(BaseModel):
    kind: EvidenceKind
    ref: str  # sha / PR number / file path / event seq
    verified: bool = False
    note: str = ""
