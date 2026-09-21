"""Change-request persistence + scope-baseline (path allowlist) model."""

from __future__ import annotations

import datetime as dt
import json
from pathlib import Path

import yaml
from pydantic import BaseModel, Field

from tram.models.cr import ChangeRequest, CRStatus, CRType, ImpactAnalysis
from tram.models.state import ProjectState

CR_OPEN_STATUSES = {CRStatus.DRAFT, CRStatus.ANALYZING, CRStatus.AWAITING_HUMAN, CRStatus.APPROVED}


class ScopeBaseline(BaseModel):
    version: int = 1
    allowed_paths: list[str] = Field(default_factory=list)
    forbidden_paths: list[str] = Field(default_factory=list)
    approved_by: str | None = None
    approved_at: dt.datetime | None = None

    def dump(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            yaml.safe_dump(self.model_dump(mode="json"), sort_keys=False, allow_unicode=True),
            encoding="utf-8",
        )

    @classmethod
    def load(cls, path: Path) -> ScopeBaseline:
        return cls.model_validate(yaml.safe_load(path.read_text(encoding="utf-8")))


class CRStore:
    def __init__(self, crs_dir: Path) -> None:
        self.crs_dir = crs_dir

    def create_draft(
        self,
        state: ProjectState,
        cr_type: CRType,
        changed_paths: list[str],
        reason: str,
        trigger_event_seq: int | None = None,
    ) -> ChangeRequest:
        cr = ChangeRequest(
            id=f"cr-{state.next_cr_seq:04d}",
            type=cr_type,
            status=CRStatus.DRAFT,
            title=f"baseline change: {cr_type.value} ({len(changed_paths)} path(s))",
            impact=ImpactAnalysis(changed_paths=sorted(changed_paths)),
            trigger_event_seq=trigger_event_seq,
            reason=reason,
            created_at=dt.datetime.now(dt.UTC),
        )
        state.next_cr_seq += 1
        state.open_crs.append(cr.id)
        self.save(cr)
        return cr

    def save(self, cr: ChangeRequest) -> None:
        self.crs_dir.mkdir(parents=True, exist_ok=True)
        (self.crs_dir / f"{cr.id}.json").write_text(cr.model_dump_json(indent=2), encoding="utf-8")

    def load_all(self) -> list[ChangeRequest]:
        if not self.crs_dir.exists():
            return []
        crs = []
        for path in sorted(self.crs_dir.glob("cr-*.json")):
            crs.append(ChangeRequest.model_validate(json.loads(path.read_text(encoding="utf-8"))))
        return crs

    def load(self, cr_id: str) -> ChangeRequest | None:
        path = self.crs_dir / f"{cr_id}.json"
        if not path.exists():
            return None
        return ChangeRequest.model_validate(json.loads(path.read_text(encoding="utf-8")))

    def open_crs(self) -> list[ChangeRequest]:
        return [cr for cr in self.load_all() if cr.status in CR_OPEN_STATUSES]
