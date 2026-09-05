from dataclasses import dataclass
from datetime import datetime
from typing import Optional

from sqlalchemy.orm import Session

from ..core.clock import clock
from ..models.data_asset import AssetState, DataAsset
from ..models.grant import Grant, GrantStatus
from ..models.remediation import Remediation
from ..schemas.use import UseCreate
from . import asset_service, grant_service, remediation_service
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

# Plain-language next step for each purpose-lifecycle violation, shown
# to the caller and persisted on the Remediation row. Generic (not
# per-copy wording) so the same message applies to any real /uses call,
# not just the canned demo scenarios that used to hardcode their own
# versions of this text.
CORRECTIVE_ACTIONS = {
    "PURPOSE_EXPIRED": (
        "Issue a new purpose grant against the original asset if continued "
        "access is legitimate; this asset remains quarantined under its "
        "expired grant."
    ),
    "PURPOSE_MISMATCH": (
        "If this new purpose is a legitimate use case, issue a new purpose "
        "grant for it against the original asset; this asset remains "
        "quarantined under its original grant's purpose."
    ),
    "GRANT_REVOKED": (
        "Issue a new purpose grant against the original asset if continued "
        "access is legitimate; this asset remains quarantined because its "
        "originating grant was revoked."
    ),
}


@dataclass(frozen=True)
class PolicyDecision:
    decision: str  # "ALLOW" or "DENY"
    reason_code: str
    reason: str
    asset_id: int
    grant_id: Optional[int]
    evaluated_at: datetime
    # Populated only for a purpose-lifecycle violation (this call or a
    # prior one against the same, still-quarantined asset) — None for
    # ALLOW and for ordinary access-control denials, which have nothing
    # to remediate.
    remediation: Optional[str] = None
    remediation_status: Optional[str] = None


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
            existing_remediation=remediation_service.get_latest_remediation_for_asset(db, asset.id),
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
    existing_remediation: Optional[Remediation] = None,
) -> PolicyDecision:
    remediation_text: Optional[str] = None
    remediation_status: Optional[str] = None

    if reason_code in PURPOSE_LIFECYCLE_REASONS:
        remediation_text = CORRECTIVE_ACTIONS[reason_code]
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

            remediation = remediation_service.create_remediation(
                db,
                asset_id=asset.id,
                grant_id=grant_id,
                reason_code=reason_code,
                reason=reason,
                corrective_action=remediation_text,
            )

            # Same entity_type/entity_id as PURPOSE_VIOLATION and
            # ASSET_QUARANTINED (not "remediation") so this event shows
            # up in the same per-asset audit trail/timeline queries as
            # everything else about this violation, with the
            # remediation row's own id kept in `details` for traceability.
            write_audit_log(
                db,
                event_type="COMPLIANCE_REVIEW_REQUIRED",
                entity_type="data_asset",
                entity_id=asset.id,
                details={"remediation_id": remediation.id, "reason_code": reason_code, "status": remediation.status.value},
            )

            db.commit()
            remediation_status = remediation.status.value
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

        # A repeat attempt against an already-quarantined asset doesn't
        # re-violate or re-remediate (see the ASSET_QUARANTINED branch
        # above in evaluate_use) -- it just still has an open
        # remediation, which the caller should keep seeing.
        if existing_remediation is not None:
            remediation_text = existing_remediation.corrective_action
            remediation_status = existing_remediation.status.value

    return PolicyDecision(
        decision="DENY",
        reason_code=reason_code,
        reason=reason,
        asset_id=asset.id,
        grant_id=grant_id,
        evaluated_at=now,
        remediation=remediation_text,
        remediation_status=remediation_status,
    )
