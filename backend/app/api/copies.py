from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from ..db.session import get_db
from ..schemas.copy import CopyCreate
from ..schemas.data_asset import DataAssetOut
from ..services import asset_service, copy_service

router = APIRouter(prefix="/copies", tags=["copies"])


@router.post("", response_model=DataAssetOut, status_code=201)
def create_copy(payload: CopyCreate, db: Session = Depends(get_db)):
    asset = copy_service.create_copy(db, payload)
    return asset_service.to_data_asset_out(db, asset)
