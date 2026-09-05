"""Deterministic demo scenarios for showing the full PurposeSeal journey
without manual setup.

Each `run_*_scenario` function drives one complete, self-contained
journey through the *same* services every other API endpoint uses
(asset_service, grant_service, retrieval_service, copy_service,
policy_service) — there is no separate "demo" business logic path, only
canned actors/purposes/durations and the abstracted clock in place of
whatever a human would otherwise type in by hand. Every scenario creates
its own fresh asset/grant/copy chain (autoincrement ids), so repeated
runs never collide with each other or with prior runs — nothing is
looked up or reused across calls.
"""

from dataclasses import dataclass
from typing import Optional

from sqlalchemy import and_, or_
from sqlalchemy.orm import Session

from ..core.clock import clock
from ..models.audit_log import AuditLog
from ..models.enums import AllowedOperation, DerivationType
from ..schemas.copy import CopyCreate
from ..schemas.data_asset import DataAssetCreate
from ..schemas.grant import GrantCreate
from ..schemas.retrieval import RetrievalCreate
from ..schemas.use import UseCreate
from . import asset_service, copy_service, grant_service, policy_service, retrieval_service

ACTOR = "researcher_01"
CLINICAL_TRIAL_PURPOSE = "clinical_trial_screening"
MARKETING_PURPOSE = "marketing_analytics"
_ALL_OPERATIONS = [AllowedOperation.VIEW, AllowedOperation.ANALYZE, AllowedOperation.COPY]


@dataclass(frozen=True)
class DemoScenarioResult:
    scenario: str
    decision: str
    reason_code: str
    reason: str
    asset_id: int
    grant_id: Optional[int]
    remediation: Optional[str]
    remediation_status: Optional[str]
    timeline: list[AuditLog]


def _collect_timeline(db: Session, entities: list[tuple[str, int]]) -> list[AuditLog]:
    """All audit events touching any of the given (entity_type, entity_id)
    pairs, in the order they were written. Ordered by primary key rather
    than `created_at` because the simulated clock can hold several writes
    at the exact same instant (e.g. everything before a clock.advance()
    call) — insertion order is the only reliable sequence."""
    conditions = [and_(AuditLog.entity_type == etype, AuditLog.entity_id == str(eid)) for etype, eid in entities]
    return db.query(AuditLog).filter(or_(*conditions)).order_by(AuditLog.id).all()


def run_legitimate_scenario(db: Session) -> DemoScenarioResult:
    """Create asset -> grant -> retrieve -> copy -> use it correctly
    before expiry. Expected outcome: ALLOW."""
    asset = asset_service.create_asset(
        db, DataAssetCreate(name="Patient Lab Result #104 (Legitimate Use Demo)", asset_type="lab_result")
    )
    grant = grant_service.create_grant(
        db,
        GrantCreate(
            subject=ACTOR,
            purpose=CLINICAL_TRIAL_PURPOSE,
            asset_id=asset.id,
            duration_minutes=30,
            allowed_operations=_ALL_OPERATIONS,
        ),
    )
    retrieved = retrieval_service.retrieve_data(
        db, RetrievalCreate(grant_id=grant.id, actor=ACTOR, asset_id=asset.id, operation=AllowedOperation.VIEW)
    )
    copy = copy_service.create_copy(
        db,
        CopyCreate(
            parent_asset_id=retrieved.id,
            name="analysis_dataset_1 (Legitimate Use Demo)",
            asset_type="dataset",
            derivation_type=DerivationType.COPY,
            actor=ACTOR,
            operation=AllowedOperation.COPY,
        ),
    )

    decision = policy_service.evaluate_use(
        db, UseCreate(asset_id=copy.id, actor=ACTOR, purpose=CLINICAL_TRIAL_PURPOSE, operation=AllowedOperation.ANALYZE)
    )

    timeline = _collect_timeline(
        db, [("data_asset", asset.id), ("grant", grant.id), ("data_asset", retrieved.id), ("data_asset", copy.id)]
    )

    return DemoScenarioResult(
        scenario="legitimate",
        decision=decision.decision,
        reason_code=decision.reason_code,
        reason=decision.reason,
        asset_id=decision.asset_id,
        grant_id=decision.grant_id,
        remediation=decision.remediation,
        remediation_status=decision.remediation_status,
        timeline=timeline,
    )


def run_expired_scenario(db: Session) -> DemoScenarioResult:
    """Create asset -> grant (10 minutes) -> retrieve -> derive a copy ->
    advance simulated time past expiry -> attempt reuse. Expected
    outcome: DENY / PURPOSE_EXPIRED, and the derived copy is
    quarantined."""
    asset = asset_service.create_asset(
        db, DataAssetCreate(name="Patient Lab Result #104 (Purpose Expiry Demo)", asset_type="lab_result")
    )
    grant = grant_service.create_grant(
        db,
        GrantCreate(
            subject=ACTOR,
            purpose=CLINICAL_TRIAL_PURPOSE,
            asset_id=asset.id,
            duration_minutes=10,
            allowed_operations=_ALL_OPERATIONS,
        ),
    )
    retrieved = retrieval_service.retrieve_data(
        db, RetrievalCreate(grant_id=grant.id, actor=ACTOR, asset_id=asset.id, operation=AllowedOperation.VIEW)
    )
    derived = copy_service.create_copy(
        db,
        CopyCreate(
            parent_asset_id=retrieved.id,
            name="analysis_dataset_1 (Purpose Expiry Demo)",
            asset_type="dataset",
            derivation_type=DerivationType.DERIVED,
            actor=ACTOR,
            operation=AllowedOperation.ANALYZE,
        ),
    )

    clock.advance(minutes=11)  # simulated jump past the 10-minute grant, no real waiting

    decision = policy_service.evaluate_use(
        db, UseCreate(asset_id=derived.id, actor=ACTOR, purpose=CLINICAL_TRIAL_PURPOSE, operation=AllowedOperation.ANALYZE)
    )

    timeline = _collect_timeline(
        db, [("data_asset", asset.id), ("grant", grant.id), ("data_asset", retrieved.id), ("data_asset", derived.id)]
    )

    return DemoScenarioResult(
        scenario="expired",
        decision=decision.decision,
        reason_code=decision.reason_code,
        reason=decision.reason,
        asset_id=decision.asset_id,
        grant_id=decision.grant_id,
        remediation=decision.remediation,
        remediation_status=decision.remediation_status,
        timeline=timeline,
    )


def run_purpose_mismatch_scenario(db: Session) -> DemoScenarioResult:
    """Retrieve legitimately for clinical_trial_screening, then attempt
    use for an unrelated purpose (marketing_analytics). Expected outcome:
    DENY / PURPOSE_MISMATCH, and the retrieved copy is quarantined."""
    asset = asset_service.create_asset(
        db, DataAssetCreate(name="Patient Lab Result #104 (Purpose Mismatch Demo)", asset_type="lab_result")
    )
    grant = grant_service.create_grant(
        db,
        GrantCreate(
            subject=ACTOR,
            purpose=CLINICAL_TRIAL_PURPOSE,
            asset_id=asset.id,
            duration_minutes=30,
            allowed_operations=_ALL_OPERATIONS,
        ),
    )
    retrieved = retrieval_service.retrieve_data(
        db, RetrievalCreate(grant_id=grant.id, actor=ACTOR, asset_id=asset.id, operation=AllowedOperation.VIEW)
    )

    decision = policy_service.evaluate_use(
        db, UseCreate(asset_id=retrieved.id, actor=ACTOR, purpose=MARKETING_PURPOSE, operation=AllowedOperation.ANALYZE)
    )

    timeline = _collect_timeline(db, [("data_asset", asset.id), ("grant", grant.id), ("data_asset", retrieved.id)])

    return DemoScenarioResult(
        scenario="purpose-mismatch",
        decision=decision.decision,
        reason_code=decision.reason_code,
        reason=decision.reason,
        asset_id=decision.asset_id,
        grant_id=decision.grant_id,
        remediation=decision.remediation,
        remediation_status=decision.remediation_status,
        timeline=timeline,
    )
