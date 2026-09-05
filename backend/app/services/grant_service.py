from dataclasses import dataclass
from datetime import datetime, timedelta

from sqlalchemy.orm import Session

from ..core.clock import clock
from ..core.errors import NotFoundError
from ..models.data_asset import DataAsset
from ..models.grant import Grant, GrantStatus
from ..schemas.grant import GrantCreate, GrantOut
from .audit_service import write_audit_log


def create_grant(db: Session, payload: GrantCreate) -> Grant:
    asset = db.get(DataAsset, payload.asset_id)
    if asset is None:
        raise NotFoundError(f"DataAsset {payload.asset_id} not found", error_code="asset_not_found")

    now = clock.now()
    expires_at = now + timedelta(minutes=payload.duration_minutes)

    grant = Grant(
        subject=payload.subject,
        purpose=payload.purpose,
        asset_id=payload.asset_id,
        allowed_operations=[op.value for op in payload.allowed_operations],
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


@dataclass(frozen=True)
class GrantStatusEvaluation:
    status: GrantStatus
    reason_code: str
    human_readable_reason: str
    evaluated_at: datetime


def evaluate_grant_status(grant: Grant) -> GrantStatusEvaluation:
    """Compute a grant's current effective status, purely as a read — this
    never writes back to `grant.status` in the database. The stored
    `status` column only ever changes via an explicit action (created as
    ACTIVE, or a future revoke action setting REVOKED); EXPIRED is always
    derived here from `expires_at` versus the current time, never
    persisted."""
    now = clock.now()

    if grant.status == GrantStatus.REVOKED:
        return GrantStatusEvaluation(
            status=GrantStatus.REVOKED,
            reason_code="EXPLICITLY_REVOKED",
            human_readable_reason="This grant was explicitly revoked.",
            evaluated_at=now,
        )

    if now >= grant.expires_at:
        return GrantStatusEvaluation(
            status=GrantStatus.EXPIRED,
            reason_code="EXPIRY_TIME_PASSED",
            human_readable_reason=(
                f"This grant expired at {grant.expires_at.isoformat()} "
                f"and was evaluated at {now.isoformat()}."
            ),
            evaluated_at=now,
        )

    return GrantStatusEvaluation(
        status=GrantStatus.ACTIVE,
        reason_code="WITHIN_VALIDITY_WINDOW",
        human_readable_reason=f"This grant is active until {grant.expires_at.isoformat()}.",
        evaluated_at=now,
    )


def is_grant_active(grant: Grant) -> bool:
    return evaluate_grant_status(grant).status == GrantStatus.ACTIVE


def to_grant_out(grant: Grant) -> GrantOut:
    """Serialize a grant with its live-evaluated status rather than the
    raw stored column, so API responses never show a stale ACTIVE past
    the grant's expiry."""
    evaluation = evaluate_grant_status(grant)
    return GrantOut(
        id=grant.id,
        subject=grant.subject,
        purpose=grant.purpose,
        asset_id=grant.asset_id,
        allowed_operations=grant.allowed_operations,
        status=evaluation.status,
        created_at=grant.created_at,
        expires_at=grant.expires_at,
    )
