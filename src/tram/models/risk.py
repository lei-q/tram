"""Risk register items - every entry must reference a triggering event."""

from __future__ import annotations

import enum
from datetime import datetime

from pydantic import BaseModel


class RiskStrategy(enum.StrEnum):
    AVOID = "avoid"
    TRANSFER = "transfer"
    MITIGATE = "mitigate"
    ACCEPT = "accept"


class RiskStatus(enum.StrEnum):
    OPEN = "open"
    WATCHING = "watching"
    CLOSED = "closed"


class RiskItem(BaseModel):
    id: str
    description: str
    probability: int  # 1-5
    impact: int  # 1-5
    strategy: RiskStrategy = RiskStrategy.MITIGATE
    trigger_event_seq: int | None = None  # evidence: what raised this risk
    owner: str = ""
    status: RiskStatus = RiskStatus.OPEN
    created_at: datetime | None = None
