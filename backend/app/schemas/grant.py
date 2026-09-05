from datetime import datetime
from typing import Optional

from pydantic import BaseModel, ConfigDict, Field

from ..models.enums import AllowedOperation
from ..models.grant import GrantStatus


class GrantCreate(BaseModel):
    subject: str = Field(..., min_length=1, description="Who the grant is issued to")
    purpose: str = Field(..., min_length=1, description="The bounded purpose justifying access")
    resource_id: str = Field(..., min_length=1, description="Identifier of the sensitive resource")
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
    resource_id: str
    allowed_operations: list[AllowedOperation]
    status: GrantStatus
    created_at: datetime
    expires_at: datetime
