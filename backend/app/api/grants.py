from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from ..db.session import get_db
from ..schemas.grant import GrantCreate, GrantOut
from ..services import grant_service

router = APIRouter(prefix="/grants", tags=["grants"])


@router.post("", response_model=GrantOut, status_code=201)
def create_grant(payload: GrantCreate, db: Session = Depends(get_db)):
    return grant_service.create_grant(db, payload)


@router.get("", response_model=list[GrantOut])
def list_grants(db: Session = Depends(get_db)):
    return grant_service.list_grants(db)


@router.get("/{grant_id}", response_model=GrantOut)
def get_grant(grant_id: int, db: Session = Depends(get_db)):
    return grant_service.get_grant(db, grant_id)
