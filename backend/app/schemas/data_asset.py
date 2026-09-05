from datetime import datetime
from typing import Optional

from pydantic import BaseModel, ConfigDict, Field

from ..models.data_asset import AssetState


class DataAssetCreate(BaseModel):
    """Creates an original/root DataAsset — source data that exists
    independently of any purpose grant. `extra="forbid"` is deliberate:
    it's the only thing stopping a caller from supplying
    parent_asset_id/root_asset_id/origin_grant_id/state and forging
    provenance that should only ever come from the retrieval/copy
    services. Those fields are server-controlled and not accepted here
    at all, not merely ignored."""

    model_config = ConfigDict(extra="forbid")

    name: str = Field(..., min_length=1, description="Display name for the source asset")
    asset_type: str = Field(..., min_length=1, description="Kind of data, e.g. 'lab_result'")


class DataAssetOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    asset_type: str
    parent_asset_id: Optional[int]
    root_asset_id: Optional[int]
    effective_root_asset_id: int
    origin_grant_id: Optional[int]
    origin_purpose: Optional[str] = None
    origin_grant_expires_at: Optional[datetime] = None
    state: AssetState
    fingerprint: Optional[str]
    created_at: datetime
