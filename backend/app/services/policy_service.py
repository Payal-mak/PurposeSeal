from dataclasses import dataclass
from datetime import datetime
from typing import Optional

from sqlalchemy.orm import Session

from ..core.clock import clock
from ..models.data_asset import AssetState, DataAsset
from ..models.grant import Grant, GrantStatus
from ..schemas.use import UseCreate
from . import asset_service, grant_service
from .audit_service import write_audit_log

# Reasons that mean "the purpose itself no longer covers this data" —
# these quarantine the asset (per the project's required violation
# response) and are audited as PURPOSE_VIOLATION. Reasons outside this
# set (wrong actor, disallowed operation, already-quarantined, never
# retrieved) are ordinary access-control denials: DENY, audited as
# USE_BLOCKED, but the asset itself isn't punished for a request that
# was never legitimate in the first place — see the design decision in
# docs/PROJECT_PROGRESS.md.
PURPOSE_LIFECYCLE_REASONS = {"PURPOSE_EXPIRED", "PURPOSE_MISMATCH", "GRANT_REVOKED"}


@dataclass(frozen=True)
class PolicyDecision:
    decision: str  # "ALLOW" or "DENY"
    reason_code: str
    reason: str
    asset_id: int
    grant_id: Optional[int]
    evaluated_at: datetime


def evaluate_use(db: Session, payload: UseCreate) -> PolicyDecision:
    """Evaluate an attempted use of an already-retrieved/derived asset
    against the purpose that originally justified retrieving it.

    Required policy inputs, all read here: actor, asset (incl. its
    current state), the inherited grant, the grant's original authorized
    purpose, the requested current purpose, the requested operation, the
    current effective time (via the clock abstraction), and the grant's
    live-evaluated status.
    """
    asset = asset_service.get_asset(db, payload.asset_id)
    now = clock.now()

    write_audit_log(
        db,
        event_type="DATA_USE_ATTEMPTED",
        entity_type="data_asset",
        entity_id=asset.id,
        details={"actor": payload.actor, "purpose": payload.purpose, "operation": payload.operation.value},
    )
    db.commit()

    if asset.state == AssetState.QUARANTINED:
        return _deny(
            db, asset, now,
            reason_code="ASSET_QUARANTINED",
            reason="This asset was already quarantined due to a prior purpose violation and cannot be used.",
            grant_id=asset.origin_grant_id,
        )

    if asset.origin_grant_id is None:
        return _deny(
            db, asset, now,
            reason_code="NO_ORIGIN_GRANT",
            reason="This asset was never retrieved under a purpose grant, so no use can be evaluated against it.",
            grant_id=None,
        )

    grant: Grant = grant_service.get_grant(db, asset.origin_grant_id)

    if grant.subject != payload.actor:
        return _deny(
            db, asset, now,
            reason_code="ACTOR_MISMATCH",
            reason="This asset's originating grant does not belong to the specified actor.",
            grant_id=grant.id,
        )

    if payload.purpose != grant.purpose:
        return _deny(
            db, asset, now,
            reason_code="PURPOSE_MISMATCH",
            reason=f"This data was retrieved for '{grant.purpose}', not '{payload.purpose}'.",
            grant_id=grant.id,
        )

    if payload.operation.value not in grant.allowed_operations:
        return _deny(
            db, asset, now,
            reason_code="OPERATION_NOT_PERMITTED",
            reason=f"Operation '{payload.operation.value}' is not permitted by the originating grant.",
            grant_id=grant.id,
        )

    evaluation = grant_service.evaluate_grant_status(grant)
    if evaluation.status == GrantStatus.EXPIRED:
        return _deny(
            db, asset, now,
            reason_code="PURPOSE_EXPIRED",
            reason=(
                f"The purpose grant authorizing this data expired at "
                f"{grant.expires_at.isoformat()} and was evaluated at {now.isoformat()}."
            ),
            grant_id=grant.id,
        )
    if evaluation.status == GrantStatus.REVOKED:
        return _deny(
            db, asset, now,
            reason_code="GRANT_REVOKED",
            reason=evaluation.human_readable_reason,
            grant_id=grant.id,
        )

    decision = PolicyDecision(
        decision="ALLOW",
        reason_code="WITHIN_PURPOSE_AND_VALIDITY",
        reason="Use is within the grant's active window, matches its original purpose, and the operation is permitted.",
        asset_id=asset.id,
        grant_id=grant.id,
        evaluated_at=now,
    )
    write_audit_log(
        db,
        event_type="USE_ALLOWED",
        entity_type="data_asset",
        entity_id=asset.id,
        details={
            "reason_code": decision.reason_code,
            "grant_id": grant.id,
            "purpose": payload.purpose,
            "operation": payload.operation.value,
        },
    )
    db.commit()
    return decision


def _deny(
    db: Session,
    asset: DataAsset,
    now: datetime,
    *,
    reason_code: str,
    reason: str,
    grant_id: Optional[int],
) -> PolicyDecision:
    decision = PolicyDecision(
        decision="DENY",
        reason_code=reason_code,
        reason=reason,
        asset_id=asset.id,
        grant_id=grant_id,
        evaluated_at=now,
    )

    if reason_code in PURPOSE_LIFECYCLE_REASONS:
        try:
            write_audit_log(
                db,
                event_type="PURPOSE_VIOLATION",
                entity_type="data_asset",
                entity_id=asset.id,
                details={"reason_code": reason_code, "reason": reason, "grant_id": grant_id},
            )

            asset.state = AssetState.QUARANTINED
            db.add(asset)
            db.flush()

            write_audit_log(
                db,
                event_type="ASSET_QUARANTINED",
                entity_type="data_asset",
                entity_id=asset.id,
                details={"reason_code": reason_code},
            )

            db.commit()
        except Exception:
            db.rollback()
            raise
    else:
        write_audit_log(
            db,
            event_type="USE_BLOCKED",
            entity_type="data_asset",
            entity_id=asset.id,
            details={"reason_code": reason_code, "reason": reason, "grant_id": grant_id},
        )
        db.commit()

    return decision
