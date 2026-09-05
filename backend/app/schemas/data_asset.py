from datetime import datetime
from typing import Optional

from pydantic import BaseModel, ConfigDict

from ..models.data_asset import AssetState


class DataAssetOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    asset_type: str
    parent_asset_id: Optional[int]
    root_asset_id: Optional[int]
    origin_grant_id: int
    state: AssetState
    fingerprint: Optional[str]
    created_at: datetime
