from sqlalchemy.orm import sessionmaker

from app import models
from app.core.clock import clock


def test_create_grant_happy_path(client, existing_asset):
    resp = client.post(
        "/grants",
        json={
            "subject": "researcher_01",
            "purpose": "clinical_trial_screening",
            "asset_id": existing_asset.id,
            "duration_minutes": 30,
            "allowed_operations": ["VIEW", "ANALYZE", "COPY"],
        },
    )
    assert resp.status_code == 201
    body = resp.json()
    assert body["id"] is not None
    assert body["subject"] == "researcher_01"
    assert body["purpose"] == "clinical_trial_screening"
    assert body["asset_id"] == existing_asset.id
    assert body["allowed_operations"] == ["VIEW", "ANALYZE", "COPY"]
    assert body["status"] == "ACTIVE"
    assert body["created_at"] < body["expires_at"]


def test_create_grant_is_retrievable_by_id(client, existing_asset):
    create_resp = client.post(
        "/grants",
        json={
            "subject": "analyst_1",
            "purpose": "fraud_investigation",
            "asset_id": existing_asset.id,
            "duration_minutes": 15,
            "allowed_operations": ["VIEW"],
        },
    )
    grant_id = create_resp.json()["id"]

    get_resp = client.get(f"/grants/{grant_id}")
    assert get_resp.status_code == 200
    assert get_resp.json()["id"] == grant_id


def test_list_grants_returns_all_created(client, existing_asset):
    client.post(
        "/grants",
        json={
            "subject": "a",
            "purpose": "p1",
            "asset_id": existing_asset.id,
            "duration_minutes": 10,
            "allowed_operations": ["VIEW"],
        },
    )
    client.post(
        "/grants",
        json={
            "subject": "b",
            "purpose": "p2",
            "asset_id": existing_asset.id,
            "duration_minutes": 20,
            "allowed_operations": ["VIEW"],
        },
    )

    resp = client.get("/grants")
    assert resp.status_code == 200
    assert len(resp.json()) == 2


def test_get_nonexistent_grant_returns_404(client):
    resp = client.get("/grants/9999")
    assert resp.status_code == 404


def test_create_grant_with_nonexistent_asset_returns_404(client):
    resp = client.post(
        "/grants",
        json={
            "subject": "a",
            "purpose": "p",
            "asset_id": 999999,
            "duration_minutes": 10,
            "allowed_operations": ["VIEW"],
        },
    )
    assert resp.status_code == 404
    assert resp.json()["error_code"] == "asset_not_found"


def test_create_grant_missing_subject_returns_422(client, existing_asset):
    resp = client.post(
        "/grants",
        json={
            "purpose": "p",
            "asset_id": existing_asset.id,
            "duration_minutes": 10,
            "allowed_operations": ["VIEW"],
        },
    )
    assert resp.status_code == 422


def test_create_grant_empty_purpose_returns_422(client, existing_asset):
    resp = client.post(
        "/grants",
        json={
            "subject": "a",
            "purpose": "",
            "asset_id": existing_asset.id,
            "duration_minutes": 10,
            "allowed_operations": ["VIEW"],
        },
    )
    assert resp.status_code == 422


def test_create_grant_missing_allowed_operations_returns_422(client, existing_asset):
    resp = client.post(
        "/grants",
        json={
            "subject": "a",
            "purpose": "p",
            "asset_id": existing_asset.id,
            "duration_minutes": 10,
        },
    )
    assert resp.status_code == 422


def test_create_grant_with_empty_allowed_operations_returns_422(client, existing_asset):
    resp = client.post(
        "/grants",
        json={
            "subject": "a",
            "purpose": "p",
            "asset_id": existing_asset.id,
            "duration_minutes": 10,
            "allowed_operations": [],
        },
    )
    assert resp.status_code == 422


def test_create_grant_with_invalid_allowed_operation_returns_422(client, existing_asset):
    resp = client.post(
        "/grants",
        json={
            "subject": "a",
            "purpose": "p",
            "asset_id": existing_asset.id,
            "duration_minutes": 10,
            "allowed_operations": ["DELETE"],
        },
    )
    assert resp.status_code == 422


def test_create_grant_with_explicit_allowed_operations(client, existing_asset):
    resp = client.post(
        "/grants",
        json={
            "subject": "analyst_1",
            "purpose": "fraud_investigation",
            "asset_id": existing_asset.id,
            "duration_minutes": 30,
            "allowed_operations": ["VIEW", "ANALYZE"],
        },
    )
    assert resp.status_code == 201
    assert resp.json()["allowed_operations"] == ["VIEW", "ANALYZE"]


def test_create_grant_zero_duration_returns_422(client, existing_asset):
    """Expiry-must-be-after-issue-time is enforced structurally: the API
    only accepts a relative duration_minutes (never a raw expires_at), and
    a non-positive duration is rejected before it could ever produce an
    expiry at or before the issue time."""
    resp = client.post(
        "/grants",
        json={
            "subject": "a",
            "purpose": "p",
            "asset_id": existing_asset.id,
            "duration_minutes": 0,
            "allowed_operations": ["VIEW"],
        },
    )
    assert resp.status_code == 422


def test_create_grant_negative_duration_returns_422(client, existing_asset):
    """Negative duration is the concrete way "expiry before issue time"
    can be requested through this API's duration-based contract."""
    resp = client.post(
        "/grants",
        json={
            "subject": "a",
            "purpose": "p",
            "asset_id": existing_asset.id,
            "duration_minutes": -5,
            "allowed_operations": ["VIEW"],
        },
    )
    assert resp.status_code == 422


def test_grant_creation_writes_single_audit_log_entry(client, existing_asset, db_engine):
    resp = client.post(
        "/grants",
        json={
            "subject": "analyst_1",
            "purpose": "fraud_investigation",
            "asset_id": existing_asset.id,
            "duration_minutes": 30,
            "allowed_operations": ["VIEW"],
        },
    )
    grant_id = resp.json()["id"]

    session = sessionmaker(bind=db_engine)()
    try:
        logs = (
            session.query(models.AuditLog)
            .filter(models.AuditLog.entity_id == str(grant_id))
            .all()
        )
        assert len(logs) == 1
        assert logs[0].event_type == "GRANT_CREATED"
        assert logs[0].entity_type == "grant"
    finally:
        session.close()


def test_active_grant_status_evaluation(client, existing_asset):
    create_resp = client.post(
        "/grants",
        json={
            "subject": "researcher_01",
            "purpose": "clinical_trial_screening",
            "asset_id": existing_asset.id,
            "duration_minutes": 30,
            "allowed_operations": ["VIEW", "ANALYZE", "COPY"],
        },
    )
    grant_id = create_resp.json()["id"]

    status_resp = client.get(f"/grants/{grant_id}/status")
    assert status_resp.status_code == 200
    body = status_resp.json()
    assert body["grant_id"] == grant_id
    assert body["status"] == "ACTIVE"
    assert body["reason_code"] == "WITHIN_VALIDITY_WINDOW"
    assert "active" in body["human_readable_reason"].lower()


def test_expired_grant_status_evaluation_using_controlled_time(client, existing_asset, db_engine):
    create_resp = client.post(
        "/grants",
        json={
            "subject": "researcher_01",
            "purpose": "clinical_trial_screening",
            "asset_id": existing_asset.id,
            "duration_minutes": 30,
            "allowed_operations": ["VIEW"],
        },
    )
    grant_id = create_resp.json()["id"]

    clock.advance(minutes=31)

    status_resp = client.get(f"/grants/{grant_id}/status")
    assert status_resp.status_code == 200
    body = status_resp.json()
    assert body["status"] == "EXPIRED"
    assert body["reason_code"] == "EXPIRY_TIME_PASSED"

    # GET /grants/{id} reflects the same live-evaluated status.
    get_resp = client.get(f"/grants/{grant_id}")
    assert get_resp.json()["status"] == "EXPIRED"

    # The stored status column itself was never mutated by evaluating it.
    session = sessionmaker(bind=db_engine)()
    try:
        stored = session.get(models.Grant, grant_id)
        assert stored.status == models.GrantStatus.ACTIVE
    finally:
        session.close()


def test_grant_status_for_nonexistent_grant_returns_404(client):
    resp = client.get("/grants/9999/status")
    assert resp.status_code == 404
