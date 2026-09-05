import json

from sqlalchemy.orm import sessionmaker

from app.core.clock import clock
from app.models import AssetState, AuditLog, DataAsset, Remediation, RemediationStatus


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


def _use(client, asset_id, actor="researcher_01", purpose="clinical_trial_screening", operation="ANALYZE"):
    return client.post(
        "/uses",
        json={"asset_id": asset_id, "actor": actor, "purpose": purpose, "operation": operation},
    )


def _retrieved_copy(client, existing_asset, duration_minutes=30, allowed_operations=None):
    grant = _create_grant(client, existing_asset.id, allowed_operations=allowed_operations, duration_minutes=duration_minutes)
    retrieved = _retrieve(client, grant["id"], existing_asset.id)
    return grant, retrieved


def test_violation_creates_remediation(client, existing_asset, db_session):
    grant, retrieved = _retrieved_copy(client, existing_asset)

    resp = _use(client, retrieved["id"], purpose="marketing_analytics")
    assert resp.status_code == 200
    body = resp.json()
    assert body["decision"] == "DENY"
    assert body["remediation"]
    assert body["remediation_status"] == "COMPLIANCE_REVIEW_REQUIRED"

    remediations = db_session.query(Remediation).filter(Remediation.asset_id == retrieved["id"]).all()
    assert len(remediations) == 1
    remediation = remediations[0]
    assert remediation.reason_code == "PURPOSE_MISMATCH"
    assert remediation.corrective_action == body["remediation"]
    assert remediation.status == RemediationStatus.COMPLIANCE_REVIEW_REQUIRED
    assert remediation.grant_id == grant["id"]


def test_violating_asset_quarantined(client, existing_asset, db_session):
    grant, retrieved = _retrieved_copy(client, existing_asset, duration_minutes=5)

    clock.advance(minutes=6)
    resp = _use(client, retrieved["id"])
    assert resp.json()["decision"] == "DENY"

    asset = db_session.get(DataAsset, retrieved["id"])
    assert asset.state == AssetState.QUARANTINED


def test_repeated_same_request_behaves_safely(client, existing_asset, db_session):
    grant, retrieved = _retrieved_copy(client, existing_asset)

    first = _use(client, retrieved["id"], purpose="marketing_analytics")
    assert first.json()["reason_code"] == "PURPOSE_MISMATCH"

    second = _use(client, retrieved["id"], purpose="marketing_analytics")
    assert second.status_code == 200
    second_body = second.json()
    assert second_body["decision"] == "DENY"
    assert second_body["reason_code"] == "ASSET_QUARANTINED"
    # The repeat attempt still surfaces the existing remediation instead of
    # silently dropping it.
    assert second_body["remediation"] == first.json()["remediation"]
    assert second_body["remediation_status"] == "COMPLIANCE_REVIEW_REQUIRED"

    # Exactly one remediation and one COMPLIANCE_REVIEW_REQUIRED event ever
    # got created, not one per attempt.
    remediations = db_session.query(Remediation).filter(Remediation.asset_id == retrieved["id"]).all()
    assert len(remediations) == 1

    compliance_events = db_session.query(AuditLog).filter(
        AuditLog.entity_id == str(retrieved["id"]), AuditLog.event_type == "COMPLIANCE_REVIEW_REQUIRED"
    ).all()
    assert len(compliance_events) == 1


def test_remediation_survives_restart(client, existing_asset, db_engine):
    grant, retrieved = _retrieved_copy(client, existing_asset)
    resp = _use(client, retrieved["id"], purpose="marketing_analytics")
    assert resp.status_code == 200

    # Simulate an application restart: a brand new session bound to the
    # same underlying database, independent of any session used above.
    fresh_session = sessionmaker(bind=db_engine)()
    try:
        remediation = fresh_session.query(Remediation).filter(Remediation.asset_id == retrieved["id"]).one()
        assert remediation.reason_code == "PURPOSE_MISMATCH"
        assert remediation.status == RemediationStatus.COMPLIANCE_REVIEW_REQUIRED
        assert remediation.corrective_action
        assert remediation.created_at is not None

        asset = fresh_session.get(DataAsset, retrieved["id"])
        assert asset.state == AssetState.QUARANTINED
    finally:
        fresh_session.close()


def test_legitimate_asset_unaffected(client, existing_asset, db_session):
    grant, retrieved = _retrieved_copy(client, existing_asset)

    resp = _use(client, retrieved["id"])
    assert resp.status_code == 200
    body = resp.json()
    assert body["decision"] == "ALLOW"
    assert body["remediation"] is None
    assert body["remediation_status"] is None

    assert db_session.query(Remediation).count() == 0

    asset = db_session.get(DataAsset, retrieved["id"])
    assert asset.state == AssetState.ACTIVE


def test_ordinary_access_denial_does_not_create_remediation(client, existing_asset, db_session):
    """Basic policy semantics are unchanged: an access-control denial
    unrelated to purpose lifecycle (wrong actor here) still doesn't
    quarantine or remediate -- only purpose-lifecycle violations do."""
    grant, retrieved = _retrieved_copy(client, existing_asset)

    resp = _use(client, retrieved["id"], actor="someone_else")
    assert resp.status_code == 200
    body = resp.json()
    assert body["reason_code"] == "ACTOR_MISMATCH"
    assert body["remediation"] is None
    assert body["remediation_status"] is None

    assert db_session.query(Remediation).count() == 0
    asset = db_session.get(DataAsset, retrieved["id"])
    assert asset.state == AssetState.ACTIVE


def test_audit_records_remediation(client, existing_asset, db_session):
    grant, retrieved = _retrieved_copy(client, existing_asset)

    resp = _use(client, retrieved["id"], purpose="marketing_analytics")
    remediation_id = resp.json()["remediation"] and db_session.query(Remediation).filter(
        Remediation.asset_id == retrieved["id"]
    ).one().id

    events = db_session.query(AuditLog).filter(
        AuditLog.entity_id == str(retrieved["id"]), AuditLog.event_type == "COMPLIANCE_REVIEW_REQUIRED"
    ).all()
    assert len(events) == 1

    details = json.loads(events[0].details)
    assert details["remediation_id"] == remediation_id
    assert details["reason_code"] == "PURPOSE_MISMATCH"
    assert details["status"] == "COMPLIANCE_REVIEW_REQUIRED"


def test_evidence_is_never_deleted(client, existing_asset, db_session):
    """No endpoint in this project deletes a Remediation or AuditLog row
    -- this test documents that guarantee by checking both remain
    present and unchanged after the violating request completes and a
    subsequent blocked retry."""
    grant, retrieved = _retrieved_copy(client, existing_asset)
    _use(client, retrieved["id"], purpose="marketing_analytics")
    _use(client, retrieved["id"], purpose="marketing_analytics")

    assert db_session.query(Remediation).filter(Remediation.asset_id == retrieved["id"]).count() == 1
    violation_events = db_session.query(AuditLog).filter(
        AuditLog.entity_id == str(retrieved["id"]), AuditLog.event_type == "PURPOSE_VIOLATION"
    ).count()
    assert violation_events == 1
