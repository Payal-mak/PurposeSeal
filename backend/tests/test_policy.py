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
    resp = client.post(
        "/retrievals",
        json={"grant_id": grant_id, "actor": actor, "asset_id": asset_id, "operation": operation},
    )
    assert resp.status_code == 201, resp.text
    return resp.json()


def _create_copy(client, parent_asset_id, name, asset_type="dataset", derivation_type="COPY",
                  actor="researcher_01", operation="COPY"):
    resp = client.post(
        "/copies",
        json={
            "parent_asset_id": parent_asset_id,
            "name": name,
            "asset_type": asset_type,
            "derivation_type": derivation_type,
            "actor": actor,
            "operation": operation,
        },
    )
    assert resp.status_code == 201, resp.text
    return resp.json()


def _use(client, asset_id, actor="researcher_01", purpose="clinical_trial_screening", operation="ANALYZE"):
    return client.post(
        "/uses",
        json={"asset_id": asset_id, "actor": actor, "purpose": purpose, "operation": operation},
    )


def _retrieved_copy(client, existing_asset, duration_minutes=30, allowed_operations=None):
    grant = _create_grant(client, existing_asset.id, allowed_operations=allowed_operations, duration_minutes=duration_minutes)
    retrieved = _retrieve(client, grant["id"], existing_asset.id)
    return grant, retrieved


def test_use_while_purpose_active_allows(client, existing_asset, db_session):
    grant, retrieved = _retrieved_copy(client, existing_asset)

    resp = _use(client, retrieved["id"])
    assert resp.status_code == 200
    body = resp.json()
    assert body["decision"] == "ALLOW"
    assert body["reason_code"] == "WITHIN_PURPOSE_AND_VALIDITY"
    assert body["asset_id"] == retrieved["id"]
    assert body["grant_id"] == grant["id"]

    asset = db_session.get(DataAsset, retrieved["id"])
    assert asset.state == AssetState.ACTIVE

    events = [e.event_type for e in db_session.query(AuditLog).filter(AuditLog.entity_id == str(retrieved["id"])).all()]
    assert "DATA_USE_ATTEMPTED" in events
    assert "USE_ALLOWED" in events


def test_use_exactly_before_expiry_allows(client, existing_asset):
    grant, retrieved = _retrieved_copy(client, existing_asset, duration_minutes=10)
    clock.advance(minutes=9)

    resp = _use(client, retrieved["id"])
    assert resp.status_code == 200
    assert resp.json()["decision"] == "ALLOW"


def test_use_after_expiry_denies_and_quarantines(client, existing_asset, db_session):
    grant, retrieved = _retrieved_copy(client, existing_asset, duration_minutes=10)
    clock.advance(minutes=11)

    resp = _use(client, retrieved["id"])
    assert resp.status_code == 200
    body = resp.json()
    assert body["decision"] == "DENY"
    assert body["reason_code"] == "PURPOSE_EXPIRED"
    assert "expired" in body["reason"].lower()

    asset = db_session.get(DataAsset, retrieved["id"])
    assert asset.state == AssetState.QUARANTINED


def test_purpose_mismatch_denies_and_quarantines(client, existing_asset, db_session):
    grant, retrieved = _retrieved_copy(client, existing_asset)

    resp = _use(client, retrieved["id"], purpose="marketing_analytics")
    assert resp.status_code == 200
    body = resp.json()
    assert body["decision"] == "DENY"
    assert body["reason_code"] == "PURPOSE_MISMATCH"

    asset = db_session.get(DataAsset, retrieved["id"])
    assert asset.state == AssetState.QUARANTINED


def test_unsupported_operation_denies_without_quarantine(client, existing_asset, db_session):
    grant, retrieved = _retrieved_copy(client, existing_asset, allowed_operations=["VIEW"])

    resp = _use(client, retrieved["id"], operation="EXPORT")
    assert resp.status_code == 200
    body = resp.json()
    assert body["decision"] == "DENY"
    assert body["reason_code"] == "OPERATION_NOT_PERMITTED"

    asset = db_session.get(DataAsset, retrieved["id"])
    assert asset.state == AssetState.ACTIVE  # not quarantined for this reason


def test_incorrect_actor_denies_without_quarantine(client, existing_asset, db_session):
    grant, retrieved = _retrieved_copy(client, existing_asset)

    resp = _use(client, retrieved["id"], actor="someone_else")
    assert resp.status_code == 200
    body = resp.json()
    assert body["decision"] == "DENY"
    assert body["reason_code"] == "ACTOR_MISMATCH"

    asset = db_session.get(DataAsset, retrieved["id"])
    assert asset.state == AssetState.ACTIVE


def test_expired_use_creates_violation_audit(client, existing_asset, db_session):
    grant, retrieved = _retrieved_copy(client, existing_asset, duration_minutes=5)
    clock.advance(minutes=6)

    _use(client, retrieved["id"])

    logs = db_session.query(AuditLog).filter(AuditLog.entity_id == str(retrieved["id"])).all()
    event_types = [log.event_type for log in logs]
    assert "PURPOSE_VIOLATION" in event_types
    violation = next(log for log in logs if log.event_type == "PURPOSE_VIOLATION")
    assert '"reason_code": "PURPOSE_EXPIRED"' in violation.details


def test_violating_copy_becomes_quarantined(client, existing_asset, db_session):
    grant, retrieved = _retrieved_copy(client, existing_asset, duration_minutes=5)
    clock.advance(minutes=6)

    _use(client, retrieved["id"])

    asset = db_session.get(DataAsset, retrieved["id"])
    assert asset.state == AssetState.QUARANTINED

    logs = db_session.query(AuditLog).filter(
        AuditLog.entity_id == str(retrieved["id"]), AuditLog.event_type == "ASSET_QUARANTINED"
    ).all()
    assert len(logs) == 1


def test_root_asset_remains_valid_when_derived_copy_quarantined(client, existing_asset, db_session):
    grant, retrieved = _retrieved_copy(client, existing_asset, duration_minutes=5)
    clock.advance(minutes=6)

    _use(client, retrieved["id"])

    root = db_session.get(DataAsset, existing_asset.id)
    assert root.state == AssetState.ACTIVE


def test_quarantined_asset_cannot_subsequently_be_used(client, existing_asset, db_session):
    grant, retrieved = _retrieved_copy(client, existing_asset, duration_minutes=5)
    clock.advance(minutes=6)

    first = _use(client, retrieved["id"])
    assert first.json()["decision"] == "DENY"

    second = _use(client, retrieved["id"])
    assert second.status_code == 200
    body = second.json()
    assert body["decision"] == "DENY"
    assert body["reason_code"] == "ASSET_QUARANTINED"

    # Quarantine only actually happened once, not re-triggered on the second attempt.
    quarantine_events = db_session.query(AuditLog).filter(
        AuditLog.entity_id == str(retrieved["id"]), AuditLog.event_type == "ASSET_QUARANTINED"
    ).all()
    assert len(quarantine_events) == 1


def test_legitimate_sibling_asset_behavior_remains_correct(client, existing_asset, db_session):
    grant, retrieved = _retrieved_copy(client, existing_asset)

    sibling_a = _create_copy(client, retrieved["id"], "analysis_dataset_1", derivation_type="DERIVED", operation="ANALYZE")
    sibling_b = _create_copy(client, retrieved["id"], "analysis_dataset_2", derivation_type="DERIVED", operation="ANALYZE")

    # Misuse sibling_a: wrong purpose -> quarantined.
    denied = _use(client, sibling_a["id"], purpose="marketing_analytics")
    assert denied.json()["decision"] == "DENY"
    assert db_session.get(DataAsset, sibling_a["id"]).state == AssetState.QUARANTINED

    # sibling_b is untouched and still legitimately usable.
    allowed = _use(client, sibling_b["id"])
    assert allowed.json()["decision"] == "ALLOW"
    assert db_session.get(DataAsset, sibling_b["id"]).state == AssetState.ACTIVE


def test_policy_response_includes_clear_reason(client, existing_asset):
    grant, retrieved = _retrieved_copy(client, existing_asset)

    allow_resp = _use(client, retrieved["id"])
    assert isinstance(allow_resp.json()["reason"], str) and len(allow_resp.json()["reason"]) > 0

    deny_resp = _use(client, retrieved["id"], purpose="marketing_analytics")
    assert isinstance(deny_resp.json()["reason"], str) and len(deny_resp.json()["reason"]) > 0


def test_use_of_never_retrieved_asset_denied(client, existing_asset):
    resp = _use(client, existing_asset.id)
    assert resp.status_code == 200
    body = resp.json()
    assert body["decision"] == "DENY"
    assert body["reason_code"] == "NO_ORIGIN_GRANT"


def test_use_of_nonexistent_asset_returns_404(client):
    resp = _use(client, 999999)
    assert resp.status_code == 404


def test_use_response_includes_evaluated_at(client, existing_asset):
    grant, retrieved = _retrieved_copy(client, existing_asset)
    resp = _use(client, retrieved["id"])
    assert "evaluated_at" in resp.json()
