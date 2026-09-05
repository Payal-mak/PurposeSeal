from datetime import timedelta

import pytest
from sqlalchemy.exc import IntegrityError

from app.core.clock import clock
from app.models import AllowedOperation, AssetState, AuditLog, DataAsset, Grant, GrantStatus
from app.services.audit_service import write_audit_log


def _make_root_asset(db_session, **overrides):
    defaults = dict(
        name="Patient Lab Result #104",
        asset_type="lab_result",
        state=AssetState.ACTIVE,
        created_at=clock.now(),
    )
    defaults.update(overrides)
    asset = DataAsset(**defaults)
    db_session.add(asset)
    db_session.commit()
    db_session.refresh(asset)
    return asset


def _make_grant(db_session, asset_id, **overrides):
    now = clock.now()
    defaults = dict(
        subject="researcher_01",
        purpose="clinical_trial_screening",
        asset_id=asset_id,
        allowed_operations=[AllowedOperation.VIEW.value, AllowedOperation.ANALYZE.value, AllowedOperation.COPY.value],
        status=GrantStatus.ACTIVE,
        created_at=now,
        expires_at=now + timedelta(minutes=30),
    )
    defaults.update(overrides)
    grant = Grant(**defaults)
    db_session.add(grant)
    db_session.commit()
    db_session.refresh(grant)
    return grant


def test_original_asset_can_exist_without_a_grant(db_session):
    """An original protected asset (e.g. a patient's lab result) exists
    before any grant does — it is not brought into being by one."""
    asset = _make_root_asset(db_session)

    fetched = db_session.get(DataAsset, asset.id)
    assert fetched is not None
    assert fetched.name == "Patient Lab Result #104"
    assert fetched.parent_asset_id is None
    assert fetched.root_asset_id is None
    assert fetched.origin_grant_id is None
    assert fetched.state == AssetState.ACTIVE


def test_grant_can_reference_an_existing_original_asset(db_session):
    asset = _make_root_asset(db_session)
    grant = _make_grant(db_session, asset_id=asset.id)

    fetched = db_session.get(Grant, grant.id)
    assert fetched is not None
    assert fetched.asset_id == asset.id
    assert fetched.subject == "researcher_01"
    assert fetched.purpose == "clinical_trial_screening"
    assert fetched.status == GrantStatus.ACTIVE


def test_grant_referencing_nonexistent_asset_fails_safely(db_session):
    with pytest.raises(IntegrityError):
        _make_grant(db_session, asset_id=999999)

    db_session.rollback()
    assert db_session.query(Grant).count() == 0


def test_retrieved_asset_can_reference_origin_grant_and_root(db_session):
    """Schema check only (no retrieval logic yet): once data is retrieved
    under a grant, the resulting DataAsset should be able to carry both
    its lineage (parent/root) and the grant that authorized it."""
    original = _make_root_asset(db_session)
    grant = _make_grant(db_session, asset_id=original.id)

    retrieved = DataAsset(
        name="Retrieved copy of Patient Lab Result #104",
        asset_type="lab_result",
        parent_asset_id=original.id,
        root_asset_id=original.id,
        origin_grant_id=grant.id,
        state=AssetState.ACTIVE,
        created_at=clock.now(),
    )
    db_session.add(retrieved)
    db_session.commit()
    db_session.refresh(retrieved)

    fetched = db_session.get(DataAsset, retrieved.id)
    assert fetched.parent_asset_id == original.id
    assert fetched.root_asset_id == original.id
    assert fetched.origin_grant_id == grant.id
    # The original itself remains ungoverned by any single grant.
    assert original.origin_grant_id is None


def test_allowed_operations_survive_persistence(db_session):
    asset = _make_root_asset(db_session)
    grant = _make_grant(
        db_session,
        asset_id=asset.id,
        allowed_operations=[AllowedOperation.VIEW.value, AllowedOperation.EXPORT.value],
    )

    db_session.expire_all()
    fetched = db_session.get(Grant, grant.id)
    assert fetched.allowed_operations == ["VIEW", "EXPORT"]


def test_audit_event_persists(db_session):
    asset = _make_root_asset(db_session)
    grant = _make_grant(db_session, asset_id=asset.id)

    entry = write_audit_log(
        db_session,
        event_type="GRANT_CREATED",
        entity_type="grant",
        entity_id=grant.id,
        details={"asset_id": asset.id},
    )
    db_session.commit()

    fetched = db_session.get(AuditLog, entry.id)
    assert fetched is not None
    assert fetched.event_type == "GRANT_CREATED"
    assert fetched.entity_type == "grant"


def test_invalid_parent_asset_reference_fails_safely(db_session):
    orphan = DataAsset(
        name="Orphan",
        asset_type="lab_result",
        parent_asset_id=999999,
        state=AssetState.ACTIVE,
        created_at=clock.now(),
    )
    db_session.add(orphan)

    with pytest.raises(IntegrityError):
        db_session.commit()

    db_session.rollback()
    assert db_session.query(DataAsset).count() == 0


def test_invalid_origin_grant_reference_fails_safely(db_session):
    orphan = DataAsset(
        name="Orphan",
        asset_type="lab_result",
        origin_grant_id=999999,
        state=AssetState.ACTIVE,
        created_at=clock.now(),
    )
    db_session.add(orphan)

    with pytest.raises(IntegrityError):
        db_session.commit()

    db_session.rollback()
    assert db_session.query(DataAsset).count() == 0
