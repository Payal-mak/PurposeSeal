from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from ..db.session import get_db
from ..models.user import User
from ..schemas.policy import PolicyDecisionOut
from ..schemas.use import UseCreate
from ..services import policy_service
from .deps import get_current_user_optional

router = APIRouter(prefix="/uses", tags=["uses"])


@router.post("", response_model=PolicyDecisionOut, status_code=200)
def create_use(
    payload: UseCreate,
    db: Session = Depends(get_db),
    current_user: User | None = Depends(get_current_user_optional),
):
    # See the identical note in api/retrievals.py: a verified caller
    # identity always wins over the request body's `actor`. This is
    # exactly the authentication/purpose-authorization boundary this
    # feature keeps separate -- overriding `actor` here changes who the
    # policy engine believes is asking, never whether their purpose is
    # valid; evaluate_use still independently checks purpose/expiry/
    # operation against the grant regardless of how well-authenticated
    # the caller is.
    if current_user is not None:
        payload = payload.model_copy(update={"actor": current_user.username})

    decision = policy_service.evaluate_use(db, payload)
    return PolicyDecisionOut(
        decision=decision.decision,
        reason_code=decision.reason_code,
        reason=decision.reason,
        asset_id=decision.asset_id,
        grant_id=decision.grant_id,
        evaluated_at=decision.evaluated_at,
        remediation=decision.remediation,
        remediation_status=decision.remediation_status,
    )
