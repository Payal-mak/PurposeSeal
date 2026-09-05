from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from ..db.session import get_db
from ..models.user import User
from ..schemas.copy import CopyCreate
from ..schemas.data_asset import DataAssetOut
from ..services import asset_service, copy_service
from .deps import get_current_user_optional

router = APIRouter(prefix="/copies", tags=["copies"])


@router.post("", response_model=DataAssetOut, status_code=201)
def create_copy(
    payload: CopyCreate,
    db: Session = Depends(get_db),
    current_user: User | None = Depends(get_current_user_optional),
):
    # See the identical note in api/retrievals.py: a verified caller
    # identity always wins over the request body's `actor`.
    if current_user is not None:
        payload = payload.model_copy(update={"actor": current_user.username})

    asset = copy_service.create_copy(db, payload)
    return asset_service.to_data_asset_out(db, asset)
