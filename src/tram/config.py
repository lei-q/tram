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


class EvmThresholds(BaseModel):
    """度量即仪表：SPI/CPI 越界即升级（自动入风险册）。"""

    spi_min: float = 0.85
    spi_max: float = 1.15
    cpi_min: float = 0.9


APPROVER_KINDS = ("baseline", "release", "cr")


class TramConfig(BaseModel):
    project_name: str
    sandbox: SandboxMode = SandboxMode.WORKTREE
    policy_engine: PolicyEngineKind = PolicyEngineKind.AUTO
    docker_image: str = "node:22-bookworm-slim"  # 沙箱镜像：引擎 CLI 需已装在镜像内
    evm_thresholds: EvmThresholds = Field(default_factory=EvmThresholds)
    # 干系人（lite 域）：kind -> 审批人名单；未配置或空名单 = 不限制
    approvers: dict[str, list[str]] = Field(default_factory=dict)
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
