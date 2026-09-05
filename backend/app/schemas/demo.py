from datetime import datetime
from typing import Any, Optional

from pydantic import BaseModel


class TimelineEventOut(BaseModel):
    """One audit event in a demo scenario's reconstructed timeline —
    a thin, read-only projection of AuditLog, not a new event model."""

    event_type: str
    entity_type: str
    entity_id: str
    details: Optional[dict[str, Any]] = None
    created_at: datetime


class DemoScenarioOut(BaseModel):
    scenario: str
    decision: str
    reason_code: str
    reason: str
    asset_id: int
    grant_id: Optional[int]
    remediation: Optional[str] = None
    timeline: list[TimelineEventOut]
