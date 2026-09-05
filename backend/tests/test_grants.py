from sqlalchemy.orm import sessionmaker

from app import models


def test_create_grant_happy_path(client):
    resp = client.post(
        "/grants",
        json={
            "subject": "analyst_1",
            "purpose": "fraud_investigation",
            "resource_id": "customer_123_records",
            "duration_minutes": 30,
        },
    )
    assert resp.status_code == 201
    body = resp.json()
    assert body["id"] is not None
    assert body["subject"] == "analyst_1"
    assert body["purpose"] == "fraud_investigation"
    assert body["resource_id"] == "customer_123_records"
    assert body["status"] == "ACTIVE"
    assert body["created_at"] < body["expires_at"]


def test_create_grant_is_retrievable_by_id(client):
    create_resp = client.post(
        "/grants",
        json={
            "subject": "analyst_1",
            "purpose": "fraud_investigation",
            "resource_id": "customer_123_records",
            "duration_minutes": 15,
        },
    )
    grant_id = create_resp.json()["id"]

    get_resp = client.get(f"/grants/{grant_id}")
    assert get_resp.status_code == 200
    assert get_resp.json()["id"] == grant_id


def test_list_grants_returns_all_created(client):
    client.post(
        "/grants",
        json={"subject": "a", "purpose": "p1", "resource_id": "r1", "duration_minutes": 10},
    )
    client.post(
        "/grants",
        json={"subject": "b", "purpose": "p2", "resource_id": "r2", "duration_minutes": 20},
    )

    resp = client.get("/grants")
    assert resp.status_code == 200
    assert len(resp.json()) == 2


def test_get_nonexistent_grant_returns_404(client):
    resp = client.get("/grants/9999")
    assert resp.status_code == 404


def test_create_grant_missing_subject_returns_422(client):
    resp = client.post(
        "/grants",
        json={"purpose": "p", "resource_id": "r", "duration_minutes": 10},
    )
    assert resp.status_code == 422


def test_create_grant_empty_purpose_returns_422(client):
    resp = client.post(
        "/grants",
        json={"subject": "a", "purpose": "", "resource_id": "r", "duration_minutes": 10},
    )
    assert resp.status_code == 422


def test_create_grant_zero_duration_returns_422(client):
    resp = client.post(
        "/grants",
        json={"subject": "a", "purpose": "p", "resource_id": "r", "duration_minutes": 0},
    )
    assert resp.status_code == 422


def test_create_grant_negative_duration_returns_422(client):
    resp = client.post(
        "/grants",
        json={"subject": "a", "purpose": "p", "resource_id": "r", "duration_minutes": -5},
    )
    assert resp.status_code == 422


def test_create_grant_defaults_allowed_operations_to_all_four(client):
    resp = client.post(
        "/grants",
        json={
            "subject": "analyst_1",
            "purpose": "fraud_investigation",
            "resource_id": "r1",
            "duration_minutes": 30,
        },
    )
    assert resp.status_code == 201
    assert sorted(resp.json()["allowed_operations"]) == ["ANALYZE", "COPY", "EXPORT", "VIEW"]


def test_create_grant_with_explicit_allowed_operations(client):
    resp = client.post(
        "/grants",
        json={
            "subject": "analyst_1",
            "purpose": "fraud_investigation",
            "resource_id": "r1",
            "duration_minutes": 30,
            "allowed_operations": ["VIEW", "ANALYZE"],
        },
    )
    assert resp.status_code == 201
    assert resp.json()["allowed_operations"] == ["VIEW", "ANALYZE"]


def test_create_grant_with_invalid_allowed_operation_returns_422(client):
    resp = client.post(
        "/grants",
        json={
            "subject": "analyst_1",
            "purpose": "fraud_investigation",
            "resource_id": "r1",
            "duration_minutes": 30,
            "allowed_operations": ["DELETE"],
        },
    )
    assert resp.status_code == 422


def test_grant_creation_writes_single_audit_log_entry(client, db_engine):
    resp = client.post(
        "/grants",
        json={
            "subject": "analyst_1",
            "purpose": "fraud_investigation",
            "resource_id": "r1",
            "duration_minutes": 30,
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
