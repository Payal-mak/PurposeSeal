from pydantic import BaseModel, ConfigDict, Field

from ..models.enums import AllowedOperation, DerivationType


class CopyCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    parent_asset_id: int = Field(..., gt=0, description="The existing asset this copy/derivative is made from")
    name: str = Field(..., min_length=1, description="Display name for the new asset")
    asset_type: str = Field(..., min_length=1, description="Kind of the new asset, e.g. 'analysis_dataset', 'report'")
    derivation_type: DerivationType = Field(
        default=DerivationType.COPY,
        description="COPY for an exact duplicate, DERIVED for a transformed/derivative asset",
    )
    actor: str = Field(..., min_length=1, description="Who is creating this copy/derivative; must match the origin grant's actor")
    operation: AllowedOperation = Field(..., description="Operation this action represents; must be permitted by the origin grant")
