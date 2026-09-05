from typing import Optional

from sqlalchemy.orm import Session

from ..core.clock import clock
from ..core.errors import ForbiddenError
from ..models.data_asset import AssetState, DataAsset
from ..models.enums import DerivationType
from ..models.grant import Grant, GrantStatus
from ..schemas.copy import CopyCreate
from . import asset_service, grant_service
from .audit_service import write_audit_log
from .fingerprint import compute_fingerprint, compute_transformed_fingerprint


def _record_denied_attempt(
    db: Session,
    *,
    entity_type: str,
    entity_id: int,
    payload: CopyCreate,
    reason_code: str,
) -> None:
    """Mirrors retrieval's denial pattern: a distinctly-typed, audit-only
    event so a rejected copy/derivation attempt is still visible in the
    trail without ever implying a copy was created."""
    write_audit_log(
        db,
        event_type="COPY_CREATION_DENIED",
        entity_type=entity_type,
        entity_id=entity_id,
        details={
            "parent_asset_id": payload.parent_asset_id,
            "actor": payload.actor,
            "operation": payload.operation.value,
            "derivation_type": payload.derivation_type.value,
            "reason_code": reason_code,
        },
    )
    db.commit()


def create_copy(db: Session, payload: CopyCreate) -> DataAsset:
    """Create a copy or derived asset from an existing, already-retrieved
    asset. The child always inherits its root and origin grant from the
    parent — never from caller input (`CopyCreate` doesn't even accept
    those fields; see schemas/copy.py) — so the purpose provenance a
    grant established at retrieval time cannot be silently reassigned by
    creating a "copy" that claims a different origin.
    """
    parent = asset_service.get_asset(db, payload.parent_asset_id)

    if parent.origin_grant_id is None:
        _record_denied_attempt(
            db,
            entity_type="data_asset",
            entity_id=parent.id,
            payload=payload,
            reason_code="no_origin_grant",
        )
        raise ForbiddenError(
            "Cannot create a copy or derivative of an asset that was never "
            "retrieved under a purpose grant.",
            error_code="no_origin_grant",
        )

    grant: Grant = grant_service.get_grant(db, parent.origin_grant_id)

    if grant.subject != payload.actor:
        _record_denied_attempt(
            db, entity_type="grant", entity_id=grant.id, payload=payload, reason_code="actor_mismatch"
        )
        raise ForbiddenError(
            "This grant does not belong to the specified actor.",
            error_code="actor_mismatch",
        )

    evaluation = grant_service.evaluate_grant_status(grant)
    if evaluation.status != GrantStatus.ACTIVE:
        _record_denied_attempt(
            db, entity_type="grant", entity_id=grant.id, payload=payload, reason_code=evaluation.reason_code
        )
        raise ForbiddenError(evaluation.human_readable_reason, error_code="grant_not_active")

    if payload.operation.value not in grant.allowed_operations:
        _record_denied_attempt(
            db, entity_type="grant", entity_id=grant.id, payload=payload, reason_code="operation_not_permitted"
        )
        raise ForbiddenError(
            f"Operation '{payload.operation.value}' is not permitted by this grant.",
            error_code="operation_not_permitted",
        )

    root_id = parent.root_asset_id or parent.id  # the true root, always inherited

    if payload.derivation_type == DerivationType.COPY:
        root_asset = asset_service.get_asset(db, root_id)
        fingerprint: Optional[str] = compute_fingerprint(root_asset)
    else:
        fingerprint = compute_transformed_fingerprint(payload.name, payload.asset_type, parent)

    child = DataAsset(
        name=payload.name,
        asset_type=payload.asset_type,
        parent_asset_id=parent.id,
        root_asset_id=root_id,
        origin_grant_id=grant.id,
        state=AssetState.ACTIVE,
        fingerprint=fingerprint,
        created_at=clock.now(),
    )
    db.add(child)

    try:
        db.flush()  # assign child.id for the audit entry, without committing

        event_type = "COPY_CREATED" if payload.derivation_type == DerivationType.COPY else "DERIVED_ASSET_CREATED"
        write_audit_log(
            db,
            event_type=event_type,
            entity_type="data_asset",
            entity_id=child.id,
            details={
                "parent_asset_id": parent.id,
                "root_asset_id": root_id,
                "origin_grant_id": grant.id,
                "actor": payload.actor,
                "operation": payload.operation.value,
                "derivation_type": payload.derivation_type.value,
                "purpose": grant.purpose,
            },
        )

        db.commit()
    except Exception:
        db.rollback()
        raise

    db.refresh(child)
    return child
