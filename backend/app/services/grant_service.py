from datetime import timedelta

from sqlalchemy.orm import Session

from ..core.clock import clock
from ..core.errors import NotFoundError
from ..models.grant import Grant, GrantStatus
from ..schemas.grant import GrantCreate
from .audit_service import write_audit_log


def create_grant(db: Session, payload: GrantCreate) -> Grant:
    now = clock.now()
    expires_at = now + timedelta(minutes=payload.duration_minutes)

    grant = Grant(
        subject=payload.subject,
        purpose=payload.purpose,
        resource_id=payload.resource_id,
        status=GrantStatus.ACTIVE,
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


def list_grants(db: Session) -> list[Grant]:
    return db.query(Grant).order_by(Grant.id).all()


def get_grant(db: Session, grant_id: int) -> Grant:
    grant = db.query(Grant).filter(Grant.id == grant_id).first()
    if grant is None:
        raise NotFoundError(f"Grant {grant_id} not found", error_code="grant_not_found")
    return grant
