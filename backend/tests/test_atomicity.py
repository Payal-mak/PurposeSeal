import pytest

from app.models import AuditLog, DataAsset, Grant


def _create_grant(client, asset_id, allowed_operations=None, duration_minutes=30,
                   subject="researcher_01", purpose="clinical_trial_screening"):
    return client.post(
        "/grants",
        json={
            "subject": subject,
            "purpose": purpose,
            "asset_id": asset_id,
            "duration_minutes": duration_minutes,
            "allowed_operations": allowed_operations or ["VIEW", "ANALYZE", "COPY"],
        },
    )


def _retrieve(client, grant_id, asset_id, actor="researcher_01", operation="VIEW"):
    return client.post(
        "/retrievals",
        json={"grant_id": grant_id, "actor": actor, "asset_id": asset_id, "operation": operation},
    )


def test_successful_grant_creation_is_atomic(client, existing_asset, db_session):
    resp = _create_grant(client, existing_asset.id)
    assert resp.status_code == 201
    grant_id = resp.json()["id"]

    assert db_session.query(Grant).count() == 1
    audit = (
        db_session.query(AuditLog)
        .filter(AuditLog.event_type == "GRANT_CREATED", AuditLog.entity_id == str(grant_id))
        .all()
    )
    assert len(audit) == 1


def test_grant_creation_rolls_back_if_audit_write_fails(client, existing_asset, db_session, monkeypatch):
    """Simulates the exact partial-state risk this correction fixes: if
    writing the audit event fails, the grant must not remain committed."""
    from app.services import grant_service

    def failing_write_audit_log(*args, **kwargs):
        raise RuntimeError("simulated audit persistence failure")

    monkeypatch.setattr(grant_service, "write_audit_log", failing_write_audit_log)

    # TestClient re-raises unhandled server exceptions (Starlette's
    # ServerErrorMiddleware sends the 500 response, then re-raises for
    # the ASGI server/logs) — the exception surfacing here, rather than
    # being swallowed, is itself proof nothing was silently left in a
    # half-successful state.
    with pytest.raises(RuntimeError, match="simulated audit persistence failure"):
        _create_grant(client, existing_asset.id)

    assert db_session.query(Grant).count() == 0
    assert db_session.query(AuditLog).filter(AuditLog.event_type == "GRANT_CREATED").count() == 0


def test_successful_retrieval_is_atomic(client, existing_asset, db_session):
    grant_resp = _create_grant(client, existing_asset.id)
    grant_id = grant_resp.json()["id"]

    resp = _retrieve(client, grant_id, existing_asset.id)
    assert resp.status_code == 201
    retrieved_id = resp.json()["id"]

    # The original asset plus exactly one retrieved copy.
    assert db_session.query(DataAsset).count() == 2
    audit = (
        db_session.query(AuditLog)
        .filter(AuditLog.event_type == "DATA_RETRIEVED", AuditLog.entity_id == str(retrieved_id))
        .all()
    )
    assert len(audit) == 1


def test_retrieval_rolls_back_if_audit_write_fails(client, existing_asset, db_session, monkeypatch):
    """Simulates an audit persistence failure during retrieval: the
    retrieved DataAsset must not remain committed without its
    DATA_RETRIEVED event."""
    grant_resp = _create_grant(client, existing_asset.id)
    grant_id = grant_resp.json()["id"]

    from app.services import retrieval_service

    def failing_write_audit_log(*args, **kwargs):
        raise RuntimeError("simulated audit persistence failure")

    monkeypatch.setattr(retrieval_service, "write_audit_log", failing_write_audit_log)

    with pytest.raises(RuntimeError, match="simulated audit persistence failure"):
        _retrieve(client, grant_id, existing_asset.id)

    # Only the original asset remains; no retrieved copy was committed.
    assert db_session.query(DataAsset).count() == 1
    assert db_session.query(AuditLog).filter(AuditLog.event_type == "DATA_RETRIEVED").count() == 0
