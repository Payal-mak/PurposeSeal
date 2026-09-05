from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from ..db.session import get_db
from ..schemas.grant import GrantCreate, GrantOut, GrantStatusOut
from ..services import grant_service

router = APIRouter(prefix="/grants", tags=["grants"])


@router.post("", response_model=GrantOut, status_code=201)
def create_grant(payload: GrantCreate, db: Session = Depends(get_db)):
    grant = grant_service.create_grant(db, payload)
    return grant_service.to_grant_out(grant)


@router.get("", response_model=list[GrantOut])
def list_grants(db: Session = Depends(get_db)):
    grants = grant_service.list_grants(db)
    return [grant_service.to_grant_out(grant) for grant in grants]


@router.get("/{grant_id}", response_model=GrantOut)
def get_grant(grant_id: int, db: Session = Depends(get_db)):
    grant = grant_service.get_grant(db, grant_id)
    return grant_service.to_grant_out(grant)


@router.get("/{grant_id}/status", response_model=GrantStatusOut)
def get_grant_status(grant_id: int, db: Session = Depends(get_db)):
    grant = grant_service.get_grant(db, grant_id)
    evaluation = grant_service.evaluate_grant_status(grant)
    return GrantStatusOut(
        grant_id=grant.id,
        status=evaluation.status,
        reason_code=evaluation.reason_code,
        human_readable_reason=evaluation.human_readable_reason,
        evaluated_at=evaluation.evaluated_at,
    )
