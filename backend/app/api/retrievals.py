from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from ..db.session import get_db
from ..models.user import User
from ..schemas.data_asset import DataAssetOut
from ..schemas.retrieval import RetrievalCreate
from ..services import asset_service, retrieval_service
from .deps import get_current_user_optional

router = APIRouter(prefix="/retrievals", tags=["retrievals"])


@router.post("", response_model=DataAssetOut, status_code=201)
def create_retrieval(
    payload: RetrievalCreate,
    db: Session = Depends(get_db),
    current_user: User | None = Depends(get_current_user_optional),
):
    # A verified caller identity always wins over whatever `actor` the
    # request body claims -- see the "identity cannot be spoofed"
    # design decision in docs/PROJECT_PROGRESS.md. Unauthenticated
    # requests (no Authorization header) are completely unaffected.
    if current_user is not None:
        payload = payload.model_copy(update={"actor": current_user.username})

    asset = retrieval_service.retrieve_data(db, payload)
    return asset_service.to_data_asset_out(db, asset)
