from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from ..db.session import get_db
from ..schemas.data_asset import DataAssetOut
from ..schemas.lineage import LineageOut
from ..services import asset_service

router = APIRouter(prefix="/assets", tags=["assets"])


@router.get("/{asset_id}", response_model=DataAssetOut)
def get_asset(asset_id: int, db: Session = Depends(get_db)):
    asset = asset_service.get_asset(db, asset_id)
    return asset_service.to_data_asset_out(db, asset)


@router.get("/{asset_id}/lineage", response_model=LineageOut)
def get_asset_lineage(asset_id: int, db: Session = Depends(get_db)):
    return asset_service.get_lineage(db, asset_id)
