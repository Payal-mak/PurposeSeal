from datetime import datetime
from typing import Literal, Optional

from pydantic import BaseModel


class PolicyDecisionOut(BaseModel):
    """Matches the project's required policy-decision shape exactly:
    decision, reason_code, reason, asset_id, grant_id, evaluated_at."""

    decision: Literal["ALLOW", "DENY"]
    reason_code: str
    reason: str
    asset_id: int
    grant_id: Optional[int]
    evaluated_at: datetime
