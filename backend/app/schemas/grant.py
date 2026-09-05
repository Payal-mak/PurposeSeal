from datetime import datetime
from typing import Optional

from pydantic import BaseModel, ConfigDict, Field

from ..models.enums import AllowedOperation
from ..models.grant import GrantStatus


class GrantCreate(BaseModel):
    subject: str = Field(..., min_length=1, description="Who the grant is issued to")
    purpose: str = Field(..., min_length=1, description="The bounded purpose justifying access")
    asset_id: int = Field(..., gt=0, description="ID of the existing protected DataAsset this grant authorizes")
    duration_minutes: float = Field(..., gt=0, description="How long the grant stays active, in minutes")
    allowed_operations: Optional[list[AllowedOperation]] = Field(
        default=None,
        description="Operations this grant permits; defaults to all operations if omitted",
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
