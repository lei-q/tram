"""Project-level tram.yaml - knowledge-area tailoring and runtime defaults."""

from __future__ import annotations

import enum
from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, Field

AreaLevel = Literal["full", "lite", "off"]


class SandboxMode(enum.StrEnum):
    WORKTREE = "worktree"
    DOCKER = "docker"


class PolicyEngineKind(enum.StrEnum):
    AUTO = "auto"
    OPA = "opa"
    PYTHON = "python"


DEFAULT_KNOWLEDGE_AREAS: dict[str, AreaLevel] = {
    "integration": "full",
    "scope": "full",
    "schedule": "full",
    "cost": "full",
    "quality": "full",
    "resource": "lite",
    "communication": "full",
    "risk": "full",
    "procurement": "lite",
    "stakeholder": "lite",
}


class TramConfig(BaseModel):
    project_name: str
    sandbox: SandboxMode = SandboxMode.WORKTREE
    policy_engine: PolicyEngineKind = PolicyEngineKind.AUTO
    knowledge_areas: dict[str, AreaLevel] = Field(
        default_factory=lambda: dict(DEFAULT_KNOWLEDGE_AREAS)
    )

    def dump(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            yaml.safe_dump(self.model_dump(mode="json"), sort_keys=False, allow_unicode=True),
            encoding="utf-8",
        )

    @classmethod
    def load(cls, path: Path) -> TramConfig:
        return cls.model_validate(yaml.safe_load(path.read_text(encoding="utf-8")))
