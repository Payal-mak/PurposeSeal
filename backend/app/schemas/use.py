from pydantic import BaseModel, ConfigDict, Field

from ..models.enums import AllowedOperation


class UseCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    asset_id: int = Field(..., gt=0, description="The already-retrieved/derived asset being used")
    actor: str = Field(..., min_length=1, description="Who is using the data; must match the asset's origin grant")
    purpose: str = Field(..., min_length=1, description="The purpose this use is claimed for")
    operation: AllowedOperation = Field(..., description="The operation being performed")
