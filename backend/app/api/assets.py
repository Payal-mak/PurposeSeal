from typing import Optional

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from ..db.session import get_db
from ..models.data_asset import AssetState
from ..schemas.data_asset import DataAssetCreate, DataAssetOut
from ..schemas.lineage import LineageOut
from ..services import asset_service

router = APIRouter(prefix="/assets", tags=["assets"])


@router.post("", response_model=DataAssetOut, status_code=201)
def create_asset(payload: DataAssetCreate, db: Session = Depends(get_db)):
    asset = asset_service.create_asset(db, payload)
    return asset_service.to_data_asset_out(db, asset)


@router.get("", response_model=list[DataAssetOut])
def list_assets(
    state: Optional[AssetState] = None,
    asset_type: Optional[str] = None,
    root_only: bool = False,
    db: Session = Depends(get_db),
):
    assets = asset_service.list_assets(db, state=state, asset_type=asset_type, root_only=root_only)
    return [asset_service.to_data_asset_out(db, asset) for asset in assets]


@router.get("/{asset_id}", response_model=DataAssetOut)
def get_asset(asset_id: int, db: Session = Depends(get_db)):
    asset = asset_service.get_asset(db, asset_id)
    return asset_service.to_data_asset_out(db, asset)


@router.get("/{asset_id}/lineage", response_model=LineageOut)
def get_asset_lineage(asset_id: int, db: Session = Depends(get_db)):
    return asset_service.get_lineage(db, asset_id)
