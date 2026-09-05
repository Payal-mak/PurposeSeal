from datetime import timedelta

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from .. import models, schemas
from ..audit import write_audit_log
from ..clock import clock
from ..database import get_db

router = APIRouter(prefix="/grants", tags=["grants"])


@router.post("", response_model=schemas.GrantOut, status_code=201)
def create_grant(payload: schemas.GrantCreate, db: Session = Depends(get_db)) -> models.Grant:
    now = clock.now()
    expires_at = now + timedelta(minutes=payload.duration_minutes)

    grant = models.Grant(
        subject=payload.subject,
        purpose=payload.purpose,
        resource_id=payload.resource_id,
        status=models.GrantStatus.ACTIVE,
        created_at=now,
        expires_at=expires_at,
    )
    db.add(grant)
    db.commit()
    db.refresh(grant)

    write_audit_log(
        db,
        event_type="GRANT_CREATED",
        entity_type="grant",
        entity_id=grant.id,
        details={
            "subject": grant.subject,
            "purpose": grant.purpose,
            "resource_id": grant.resource_id,
            "created_at": grant.created_at.isoformat(),
            "expires_at": grant.expires_at.isoformat(),
        },
    )
    db.commit()

    return grant


@router.get("", response_model=list[schemas.GrantOut])
def list_grants(db: Session = Depends(get_db)) -> list[models.Grant]:
    return db.query(models.Grant).order_by(models.Grant.id).all()


@router.get("/{grant_id}", response_model=schemas.GrantOut)
def get_grant(grant_id: int, db: Session = Depends(get_db)) -> models.Grant:
    grant = db.query(models.Grant).filter(models.Grant.id == grant_id).first()
    if grant is None:
        raise HTTPException(status_code=404, detail="Grant not found")
    return grant
