import pytest

from app.models import AuditLog, Grant, GrantStatus


def _create_grant(client, asset_id, allowed_operations=None, duration_minutes=30,
                   subject="researcher_01", purpose="clinical_trial_screening"):
    resp = client.post(
        "/grants",
        json={
            "subject": subject,
            "purpose": purpose,
            "asset_id": asset_id,
            "duration_minutes": duration_minutes,
            "allowed_operations": allowed_operations or ["VIEW", "ANALYZE", "COPY"],
        },
    )
    assert resp.status_code == 201, resp.text
    return resp.json()


def test_valid_grant_can_be_revoked(client, existing_asset):
    grant = _create_grant(client, existing_asset.id)

    resp = client.post(f"/grants/{grant['id']}/revoke")
    assert resp.status_code == 200, resp.text
    assert resp.json()["status"] == "REVOKED"


def test_persisted_grant_status_becomes_revoked(client, existing_asset, db_session):
    grant = _create_grant(client, existing_asset.id)

    client.post(f"/grants/{grant['id']}/revoke")

    persisted = db_session.get(Grant, grant["id"])
    assert persisted.status == GrantStatus.REVOKED


def test_grant_revoked_audit_event_exists(client, existing_asset, db_session):
    grant = _create_grant(client, existing_asset.id)

    client.post(f"/grants/{grant['id']}/revoke")

    events = db_session.query(AuditLog).filter(
        AuditLog.entity_id == str(grant["id"]), AuditLog.event_type == "GRANT_REVOKED"
    ).all()
    assert len(events) == 1


def test_revocation_and_audit_are_atomic(client, existing_asset, db_session, monkeypatch):
    grant = _create_grant(client, existing_asset.id)

    from app.services import grant_service

    def _boom(*args, **kwargs):
        raise RuntimeError("simulated audit persistence failure")

    monkeypatch.setattr(grant_service, "write_audit_log", _boom)

    with pytest.raises(RuntimeError, match="simulated audit persistence failure"):
        client.post(f"/grants/{grant['id']}/revoke")

    persisted = db_session.get(Grant, grant["id"])
    assert persisted.status == GrantStatus.ACTIVE
    assert db_session.query(AuditLog).filter(AuditLog.event_type == "GRANT_REVOKED").count() == 0


def test_revoking_nonexistent_grant_returns_404(client):
    resp = client.post("/grants/999999/revoke")
    assert resp.status_code == 404


def test_repeated_revocation_is_deterministic_and_idempotent(client, existing_asset, db_session):
    grant = _create_grant(client, existing_asset.id)

    first = client.post(f"/grants/{grant['id']}/revoke")
    assert first.status_code == 200
    assert first.json()["status"] == "REVOKED"

    second = client.post(f"/grants/{grant['id']}/revoke")
    assert second.status_code == 200
    assert second.json()["status"] == "REVOKED"

    # No duplicate GRANT_REVOKED event was written on the second call.
    events = db_session.query(AuditLog).filter(
        AuditLog.entity_id == str(grant["id"]), AuditLog.event_type == "GRANT_REVOKED"
    ).all()
    assert len(events) == 1
