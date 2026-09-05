from datetime import timedelta

from sqlalchemy.orm import Session

from ..core.clock import clock
from ..core.errors import NotFoundError
from ..models.data_asset import DataAsset
from ..models.enums import AllowedOperation
from ..models.grant import Grant, GrantStatus
from ..schemas.grant import GrantCreate
from .audit_service import write_audit_log


def create_grant(db: Session, payload: GrantCreate) -> Grant:
    asset = db.get(DataAsset, payload.asset_id)
    if asset is None:
        raise NotFoundError(f"DataAsset {payload.asset_id} not found", error_code="asset_not_found")

    now = clock.now()
    expires_at = now + timedelta(minutes=payload.duration_minutes)
    allowed_operations = payload.allowed_operations or list(AllowedOperation)

    grant = Grant(
        subject=payload.subject,
        purpose=payload.purpose,
        asset_id=payload.asset_id,
        allowed_operations=[op.value for op in allowed_operations],
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
            "asset_id": grant.asset_id,
            "allowed_operations": grant.allowed_operations,
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
