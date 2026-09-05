from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from ..db.session import get_db
from ..schemas.data_asset import DataAssetOut
from ..schemas.retrieval import RetrievalCreate
from ..services import asset_service, retrieval_service

router = APIRouter(prefix="/retrievals", tags=["retrievals"])


@router.post("", response_model=DataAssetOut, status_code=201)
def create_retrieval(payload: RetrievalCreate, db: Session = Depends(get_db)):
    asset = retrieval_service.retrieve_data(db, payload)
    return asset_service.to_data_asset_out(db, asset)
