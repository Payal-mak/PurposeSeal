from sqlalchemy.orm import Session

from ..core.clock import clock
from ..core.errors import ForbiddenError
from ..models.data_asset import AssetState, DataAsset
from ..models.grant import Grant, GrantStatus
from ..schemas.retrieval import RetrievalCreate
from . import grant_service
from .audit_service import write_audit_log
from .fingerprint import compute_fingerprint


def _record_denied_attempt(db: Session, grant: Grant, payload: RetrievalCreate, reason_code: str) -> None:
    """Failed retrieval must never produce a DATA_RETRIEVED event. This
    records a separate, distinctly-typed DATA_RETRIEVAL_DENIED event so
    the audit trail still shows the attempt and why it was rejected,
    without ever implying success."""
    write_audit_log(
        db,
        event_type="DATA_RETRIEVAL_DENIED",
        entity_type="grant",
        entity_id=grant.id,
        details={
            "actor": payload.actor,
            "asset_id": payload.asset_id,
            "operation": payload.operation.value,
            "reason_code": reason_code,
        },
    )
    db.commit()


def retrieve_data(db: Session, payload: RetrievalCreate) -> DataAsset:
    """Evaluate a grant and, if the retrieval is legitimate, persist a new
    DataAsset representing the retrieved copy, linked back to both its
    source asset and the grant that authorized it (the "purpose seal").
    Raises ForbiddenError (via grant_service.get_grant, NotFoundError) if
    the grant doesn't exist, or ForbiddenError for any other denial —
    never creates a DataAsset or a DATA_RETRIEVED event on failure.
    """
    grant = grant_service.get_grant(db, payload.grant_id)

    if grant.subject != payload.actor:
        _record_denied_attempt(db, grant, payload, reason_code="actor_mismatch")
        raise ForbiddenError(
            "This grant does not belong to the specified actor.",
            error_code="actor_mismatch",
        )

    if grant.asset_id != payload.asset_id:
        _record_denied_attempt(db, grant, payload, reason_code="asset_mismatch")
        raise ForbiddenError(
            "This grant does not authorize the specified asset.",
            error_code="asset_mismatch",
        )

    evaluation = grant_service.evaluate_grant_status(grant)
    if evaluation.status != GrantStatus.ACTIVE:
        _record_denied_attempt(db, grant, payload, reason_code=evaluation.reason_code)
        raise ForbiddenError(evaluation.human_readable_reason, error_code="grant_not_active")

    if payload.operation.value not in grant.allowed_operations:
        _record_denied_attempt(db, grant, payload, reason_code="operation_not_permitted")
        raise ForbiddenError(
            f"Operation '{payload.operation.value}' is not permitted by this grant.",
            error_code="operation_not_permitted",
        )

    root_asset = db.get(DataAsset, grant.asset_id)
    now = clock.now()
    root_id = root_asset.root_asset_id or root_asset.id

    retrieved = DataAsset(
        name=f"Retrieved copy of {root_asset.name}",
        asset_type=root_asset.asset_type,
        parent_asset_id=root_asset.id,
        root_asset_id=root_id,
        origin_grant_id=grant.id,
        state=AssetState.ACTIVE,
        fingerprint=compute_fingerprint(root_asset),
        created_at=now,
    )
    db.add(retrieved)
    db.commit()
    db.refresh(retrieved)

    write_audit_log(
        db,
        event_type="DATA_RETRIEVED",
        entity_type="data_asset",
        entity_id=retrieved.id,
        details={
            "grant_id": grant.id,
            "actor": payload.actor,
            "operation": payload.operation.value,
            "root_asset_id": root_id,
            "purpose": grant.purpose,
        },
    )
    db.commit()

    return retrieved
