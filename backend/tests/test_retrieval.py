from app.core.clock import clock
from app.models import AssetState, AuditLog, DataAsset


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


def _retrieve(client, grant_id, asset_id, actor="researcher_01", operation="VIEW"):
    return client.post(
        "/retrievals",
        json={"grant_id": grant_id, "actor": actor, "asset_id": asset_id, "operation": operation},
    )


def test_successful_retrieval(client, existing_asset):
    grant = _create_grant(client, existing_asset.id)

    resp = _retrieve(client, grant["id"], existing_asset.id)
    assert resp.status_code == 201
    body = resp.json()
    assert body["name"] == f"Retrieved copy of {existing_asset.name}"
    assert body["state"] == "ACTIVE"
    assert body["fingerprint"] is not None
    assert len(body["fingerprint"]) == 64  # SHA-256 hex digest length


def test_retrieval_root_linkage(client, existing_asset):
    grant = _create_grant(client, existing_asset.id)

    resp = _retrieve(client, grant["id"], existing_asset.id)
    body = resp.json()
    assert body["parent_asset_id"] == existing_asset.id
    assert body["root_asset_id"] == existing_asset.id
    assert body["effective_root_asset_id"] == existing_asset.id


def test_retrieval_grant_linkage(client, existing_asset):
    grant = _create_grant(client, existing_asset.id)

    resp = _retrieve(client, grant["id"], existing_asset.id)
    body = resp.json()
    assert body["origin_grant_id"] == grant["id"]
    assert body["origin_purpose"] == "clinical_trial_screening"
    assert body["origin_grant_expires_at"] == grant["expires_at"]


def test_retrieval_writes_data_retrieved_audit_event(client, existing_asset, db_session):
    grant = _create_grant(client, existing_asset.id)

    resp = _retrieve(client, grant["id"], existing_asset.id)
    retrieved_id = resp.json()["id"]

    logs = (
        db_session.query(AuditLog)
        .filter(AuditLog.entity_id == str(retrieved_id), AuditLog.event_type == "DATA_RETRIEVED")
        .all()
    )
    assert len(logs) == 1
    assert logs[0].entity_type == "data_asset"


def test_expired_grant_denied(client, existing_asset, db_session):
    grant = _create_grant(client, existing_asset.id, duration_minutes=5)
    clock.advance(minutes=6)

    resp = _retrieve(client, grant["id"], existing_asset.id)
    assert resp.status_code == 403
    assert resp.json()["error_code"] == "grant_not_active"

    # No retrieved DataAsset was created, and no DATA_RETRIEVED event exists.
    assert db_session.query(DataAsset).count() == 1  # only the original asset
    assert db_session.query(AuditLog).filter(AuditLog.event_type == "DATA_RETRIEVED").count() == 0
    denied = db_session.query(AuditLog).filter(AuditLog.event_type == "DATA_RETRIEVAL_DENIED").all()
    assert len(denied) == 1


def test_incorrect_actor_denied(client, existing_asset, db_session):
    grant = _create_grant(client, existing_asset.id)

    resp = _retrieve(client, grant["id"], existing_asset.id, actor="someone_else")
    assert resp.status_code == 403
    assert resp.json()["error_code"] == "actor_mismatch"
    assert db_session.query(DataAsset).count() == 1


def test_incorrect_asset_denied(client, existing_asset, db_session):
    other_asset = DataAsset(
        name="Unrelated Record",
        asset_type="other",
        state=AssetState.ACTIVE,
        created_at=clock.now(),
    )
    db_session.add(other_asset)
    db_session.commit()
    db_session.refresh(other_asset)

    grant = _create_grant(client, existing_asset.id)

    resp = _retrieve(client, grant["id"], other_asset.id)
    assert resp.status_code == 403
    assert resp.json()["error_code"] == "asset_mismatch"
    # Only the two seeded assets exist, no retrieved copy was created.
    assert db_session.query(DataAsset).count() == 2


def test_view_not_permitted(client, existing_asset, db_session):
    grant = _create_grant(client, existing_asset.id, allowed_operations=["ANALYZE"])

    resp = _retrieve(client, grant["id"], existing_asset.id, operation="VIEW")
    assert resp.status_code == 403
    assert resp.json()["error_code"] == "operation_not_permitted"
    assert db_session.query(DataAsset).count() == 1
    assert db_session.query(AuditLog).filter(AuditLog.event_type == "DATA_RETRIEVED").count() == 0


def test_retrieval_with_nonexistent_grant_returns_404(client, existing_asset):
    resp = _retrieve(client, 999999, existing_asset.id)
    assert resp.status_code == 404
