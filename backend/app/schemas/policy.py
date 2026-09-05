from datetime import datetime
from typing import Literal, Optional

from pydantic import BaseModel


class PolicyDecisionOut(BaseModel):
    """Matches the project's required policy-decision shape:
    decision, reason_code, reason, asset_id, grant_id, evaluated_at —
    plus `remediation`/`remediation_status`, populated only when this
    decision (or a prior one against the same still-quarantined asset)
    is a purpose-lifecycle violation. Both are `null` for ALLOW and for
    ordinary access-control denials, which have nothing to remediate.
    """

    decision: Literal["ALLOW", "DENY"]
    reason_code: str
    reason: str
    asset_id: int
    grant_id: Optional[int]
    evaluated_at: datetime
    remediation: Optional[str] = None
    remediation_status: Optional[str] = None
