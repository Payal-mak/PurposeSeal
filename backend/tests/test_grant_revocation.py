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


def _compliance_officer_headers(client, username="compliance_01"):
    """Revocation is role-restricted (COMPLIANCE_OFFICER/ADMIN) as of
    the Actor Roles and Minimal Authentication feature -- see
    test_auth.py for dedicated auth/role tests. This helper just gets a
    valid, sufficiently-privileged token for tests that only care about
    revocation's own behavior."""
    client.post("/auth/register", json={"username": username, "password": "password123", "role": "COMPLIANCE_OFFICER"})
    login = client.post("/auth/login", json={"username": username, "password": "password123"})
    assert login.status_code == 200, login.text
    return {"Authorization": f"Bearer {login.json()['access_token']}"}


def test_valid_grant_can_be_revoked(client, existing_asset):
    grant = _create_grant(client, existing_asset.id)
    headers = _compliance_officer_headers(client)

    resp = client.post(f"/grants/{grant['id']}/revoke", headers=headers)
    assert resp.status_code == 200, resp.text
    assert resp.json()["status"] == "REVOKED"


def test_persisted_grant_status_becomes_revoked(client, existing_asset, db_session):
    grant = _create_grant(client, existing_asset.id)
    headers = _compliance_officer_headers(client)

    client.post(f"/grants/{grant['id']}/revoke", headers=headers)

    persisted = db_session.get(Grant, grant["id"])
    assert persisted.status == GrantStatus.REVOKED


def test_grant_revoked_audit_event_exists(client, existing_asset, db_session):
    grant = _create_grant(client, existing_asset.id)
    headers = _compliance_officer_headers(client)

    client.post(f"/grants/{grant['id']}/revoke", headers=headers)

    events = db_session.query(AuditLog).filter(
        AuditLog.entity_id == str(grant["id"]), AuditLog.event_type == "GRANT_REVOKED"
    ).all()
    assert len(events) == 1


def test_revocation_and_audit_are_atomic(client, existing_asset, db_session, monkeypatch):
    grant = _create_grant(client, existing_asset.id)
    headers = _compliance_officer_headers(client)

    from app.services import grant_service

    def _boom(*args, **kwargs):
        raise RuntimeError("simulated audit persistence failure")

    monkeypatch.setattr(grant_service, "write_audit_log", _boom)

    with pytest.raises(RuntimeError, match="simulated audit persistence failure"):
        client.post(f"/grants/{grant['id']}/revoke", headers=headers)

    persisted = db_session.get(Grant, grant["id"])
    assert persisted.status == GrantStatus.ACTIVE
    assert db_session.query(AuditLog).filter(AuditLog.event_type == "GRANT_REVOKED").count() == 0


def test_revoking_nonexistent_grant_returns_404(client):
    headers = _compliance_officer_headers(client)
    resp = client.post("/grants/999999/revoke", headers=headers)
    assert resp.status_code == 404


def test_repeated_revocation_is_deterministic_and_idempotent(client, existing_asset, db_session):
    grant = _create_grant(client, existing_asset.id)
    headers = _compliance_officer_headers(client)

    first = client.post(f"/grants/{grant['id']}/revoke", headers=headers)
    assert first.status_code == 200
    assert first.json()["status"] == "REVOKED"

    second = client.post(f"/grants/{grant['id']}/revoke", headers=headers)
    assert second.status_code == 200
    assert second.json()["status"] == "REVOKED"

    # No duplicate GRANT_REVOKED event was written on the second call.
    events = db_session.query(AuditLog).filter(
        AuditLog.entity_id == str(grant["id"]), AuditLog.event_type == "GRANT_REVOKED"
    ).all()
    assert len(events) == 1
