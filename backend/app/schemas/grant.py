from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from ..models.enums import AllowedOperation
from ..models.grant import GrantStatus


class GrantCreate(BaseModel):
    subject: str = Field(..., min_length=1, description="Who the grant is issued to (the actor)")
    purpose: str = Field(..., min_length=1, description="The bounded purpose justifying access")
    asset_id: int = Field(..., gt=0, description="ID of the existing protected DataAsset this grant authorizes")
    duration_minutes: float = Field(..., gt=0, description="How long the grant stays active, in minutes")
    allowed_operations: list[AllowedOperation] = Field(
        ...,
        min_length=1,
        description="Operations this grant permits; at least one is required",
    )


class GrantOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    subject: str
    purpose: str
    asset_id: int
    allowed_operations: list[AllowedOperation]
    status: GrantStatus
    created_at: datetime
    expires_at: datetime


class GrantStatusOut(BaseModel):
    """The result of evaluating a grant's current status — always
    computed live from the stored status plus the current time, never a
    stale, previously-persisted value."""

    grant_id: int
    status: GrantStatus
    reason_code: str
    human_readable_reason: str
    evaluated_at: datetime
