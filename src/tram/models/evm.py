"""EVM snapshots - computed by deterministic code, never by an LLM."""

from __future__ import annotations

from datetime import date

from pydantic import BaseModel, Field, computed_field


class EVMSnapshot(BaseModel):
    date: date
    pv: float = 0.0  # planned value (task points)
    ev: float = 0.0  # earned value
    ac: float = 0.0  # actual cost (spent points)
    task_refs: list[str] = Field(default_factory=list)

    @computed_field  # type: ignore[prop-decorator]
    @property
    def spi(self) -> float:
        return round(self.ev / self.pv, 3) if self.pv else 0.0

    @computed_field  # type: ignore[prop-decorator]
    @property
    def cpi(self) -> float:
        return round(self.ev / self.ac, 3) if self.ac else 0.0

    @computed_field  # type: ignore[prop-decorator]
    @property
    def sv(self) -> float:
        return round(self.ev - self.pv, 3)

    @computed_field  # type: ignore[prop-decorator]
    @property
    def cv(self) -> float:
        return round(self.ev - self.ac, 3)
