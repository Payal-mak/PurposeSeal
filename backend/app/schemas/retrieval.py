from pydantic import BaseModel, ConfigDict, Field

from ..models.enums import AllowedOperation


class RetrievalCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    grant_id: int = Field(..., gt=0, description="The purpose grant authorizing this retrieval")
    actor: str = Field(..., min_length=1, description="Who is performing the retrieval; must match the grant's subject")
    asset_id: int = Field(..., gt=0, description="The asset being retrieved; must match the grant's asset")
    operation: AllowedOperation = Field(..., description="The operation being performed; must be permitted by the grant")
