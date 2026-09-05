from typing import Optional

from sqlalchemy.orm import Session

from ..core.clock import clock
from ..models.remediation import Remediation, RemediationStatus


def create_remediation(
    db: Session,
    *,
    asset_id: int,
    grant_id: Optional[int],
    reason_code: str,
    reason: str,
    corrective_action: str,
) -> Remediation:
    """Persist a remediation record. Called only from
    policy_service._deny at the moment an asset is quarantined for a
    purpose-lifecycle violation. The caller owns the transaction (the
    same flush-then-audit-then-commit pattern used everywhere else in
    this project) -- this only adds and flushes, never commits.
    """
    remediation = Remediation(
        asset_id=asset_id,
        grant_id=grant_id,
        reason_code=reason_code,
        reason=reason,
        corrective_action=corrective_action,
        status=RemediationStatus.COMPLIANCE_REVIEW_REQUIRED,
        created_at=clock.now(),
    )
    db.add(remediation)
    db.flush()
    return remediation


def get_latest_remediation_for_asset(db: Session, asset_id: int) -> Optional[Remediation]:
    """The most recent remediation on file for an asset, if any. An
    asset is only ever quarantined once (quarantine is permanent in
    this project -- see Known Issues), so today this is always either
    None or exactly one row; `.order_by(...desc()).first()` is future-
    proofing, not evidence multiple rows are expected."""
    return db.query(Remediation).filter(Remediation.asset_id == asset_id).order_by(Remediation.id.desc()).first()
