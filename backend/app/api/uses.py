from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from ..db.session import get_db
from ..schemas.policy import PolicyDecisionOut
from ..schemas.use import UseCreate
from ..services import policy_service

router = APIRouter(prefix="/uses", tags=["uses"])


@router.post("", response_model=PolicyDecisionOut, status_code=200)
def create_use(payload: UseCreate, db: Session = Depends(get_db)):
    decision = policy_service.evaluate_use(db, payload)
    return PolicyDecisionOut(
        decision=decision.decision,
        reason_code=decision.reason_code,
        reason=decision.reason,
        asset_id=decision.asset_id,
        grant_id=decision.grant_id,
        evaluated_at=decision.evaluated_at,
    )
