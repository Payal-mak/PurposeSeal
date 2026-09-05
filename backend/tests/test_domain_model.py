from datetime import timedelta

import pytest
from sqlalchemy.exc import IntegrityError

from app.core.clock import clock
from app.models import AllowedOperation, AssetState, AuditLog, DataAsset, Grant, GrantStatus
from app.services.audit_service import write_audit_log


def _make_grant(db_session, **overrides):
    now = clock.now()
    defaults = dict(
        subject="analyst_1",
        purpose="fraud_investigation",
        resource_id="customer_records",
        allowed_operations=[AllowedOperation.VIEW.value, AllowedOperation.COPY.value],
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


def test_create_and_retrieve_original_asset(db_session):
    grant = _make_grant(db_session)

    asset = DataAsset(
        name="Customer 123 Record",
        asset_type="customer_record",
        origin_grant_id=grant.id,
        state=AssetState.ACTIVE,
        created_at=clock.now(),
    )
    db_session.add(asset)
    db_session.commit()

    fetched = db_session.get(DataAsset, asset.id)
    assert fetched is not None
    assert fetched.name == "Customer 123 Record"
    assert fetched.parent_asset_id is None
    assert fetched.root_asset_id is None
    assert fetched.state == AssetState.ACTIVE
    assert fetched.origin_grant_id == grant.id


def test_create_child_asset_relationship(db_session):
    grant = _make_grant(db_session)
    now = clock.now()

    root = DataAsset(
        name="Root Record",
        asset_type="customer_record",
        origin_grant_id=grant.id,
        state=AssetState.ACTIVE,
        created_at=now,
    )
    db_session.add(root)
    db_session.commit()
    db_session.refresh(root)

    child = DataAsset(
        name="Copy of Root Record",
        asset_type="customer_record",
        parent_asset_id=root.id,
        root_asset_id=root.id,
        origin_grant_id=grant.id,
        state=AssetState.ACTIVE,
        created_at=now,
    )
    db_session.add(child)
    db_session.commit()
    db_session.refresh(child)

    fetched = db_session.get(DataAsset, child.id)
    assert fetched.parent_asset_id == root.id


def test_root_relationship_is_valid(db_session):
    grant = _make_grant(db_session)
    now = clock.now()

    root = DataAsset(
        name="Root Record",
        asset_type="customer_record",
        origin_grant_id=grant.id,
        state=AssetState.ACTIVE,
        created_at=now,
    )
    db_session.add(root)
    db_session.commit()
    db_session.refresh(root)

    child = DataAsset(
        name="Copy",
        asset_type="customer_record",
        parent_asset_id=root.id,
        root_asset_id=root.id,
        origin_grant_id=grant.id,
        state=AssetState.ACTIVE,
        created_at=now,
    )
    db_session.add(child)
    db_session.commit()
    db_session.refresh(child)

    # NULL root_asset_id is the convention for "this row is the root".
    assert root.root_asset_id is None
    assert child.root_asset_id == root.id
    assert child.root_asset_id != child.id


def test_persist_purpose_grant(db_session):
    grant = _make_grant(db_session)

    fetched = db_session.get(Grant, grant.id)
    assert fetched is not None
    assert fetched.subject == "analyst_1"
    assert fetched.purpose == "fraud_investigation"
    assert fetched.status == GrantStatus.ACTIVE


def test_allowed_operations_survive_persistence(db_session):
    grant = _make_grant(
        db_session,
        allowed_operations=[AllowedOperation.VIEW.value, AllowedOperation.EXPORT.value],
    )

    db_session.expire_all()
    fetched = db_session.get(Grant, grant.id)
    assert fetched.allowed_operations == ["VIEW", "EXPORT"]


def test_audit_event_persists(db_session):
    grant = _make_grant(db_session)

    entry = write_audit_log(
        db_session,
        event_type="DATA_ASSET_CREATED",
        entity_type="data_asset",
        entity_id=1,
        details={"origin_grant_id": grant.id},
    )
    db_session.commit()

    fetched = db_session.get(AuditLog, entry.id)
    assert fetched is not None
    assert fetched.event_type == "DATA_ASSET_CREATED"
    assert fetched.entity_type == "data_asset"


def test_invalid_parent_asset_reference_fails_safely(db_session):
    grant = _make_grant(db_session)

    orphan = DataAsset(
        name="Orphan",
        asset_type="customer_record",
        parent_asset_id=999999,
        origin_grant_id=grant.id,
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
        asset_type="customer_record",
        origin_grant_id=999999,
        state=AssetState.ACTIVE,
        created_at=clock.now(),
    )
    db_session.add(orphan)

    with pytest.raises(IntegrityError):
        db_session.commit()

    db_session.rollback()
    assert db_session.query(DataAsset).count() == 0
