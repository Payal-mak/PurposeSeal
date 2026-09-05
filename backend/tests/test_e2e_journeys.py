"""Full end-to-end regression suite for PurposeSeal's three central
purpose-enforcement journeys, driven entirely through the public HTTP
API (never by calling service functions directly, and never through
the `/demo/scenarios/*` shortcuts, which collapse an entire journey
into one call). Each journey here issues the same individual requests
a real client would: create asset, create grant, retrieve, copy, use.

This does not add new functionality -- it is a consolidated, thorough
regression pass over behavior already covered piecemeal across
test_retrieval.py, test_policy.py, test_remediation.py, and
test_demo_scenarios.py. What's new here is asserting all three things
together, for the same run, every time: the API response, the
persisted database state, and the audit trail.

Isolated temporary databases: every test uses the `client`/`db_session`
fixtures from conftest.py, each backed by its own `tmp_path`-scoped
SQLite file (see conftest.py's `db_engine` fixture) that's created
fresh and disposed at the end of the test -- nothing here is shared
across tests or across runs, so the whole file can be re-run any
number of times with identical results.

No real time is used anywhere: Journey B advances time via the
project's simulated `Clock.advance()`, never `time.sleep()`.
"""

import time

from app.models import AssetState, AuditLog, DataAsset, Grant, GrantStatus, Remediation, RemediationStatus


def _create_asset(client, name="Patient Lab Result #104", asset_type="lab_result"):
    resp = client.post("/assets", json={"name": name, "asset_type": asset_type})
    assert resp.status_code == 201, resp.text
    return resp.json()


def _create_grant(client, asset_id, purpose="clinical_trial_screening", duration_minutes=30,
                   allowed_operations=None, subject="researcher_01"):
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


def _copy(client, parent_asset_id, name, derivation_type="COPY", actor="researcher_01", operation="COPY"):
    resp = client.post(
        "/copies",
        json={
            "parent_asset_id": parent_asset_id,
            "name": name,
            "asset_type": "dataset",
            "derivation_type": derivation_type,
            "actor": actor,
            "operation": operation,
        },
    )
    assert resp.status_code == 201, resp.text
    return resp.json()


def _use(client, asset_id, purpose, actor="researcher_01", operation="ANALYZE"):
    resp = client.post(
        "/uses",
        json={"asset_id": asset_id, "actor": actor, "purpose": purpose, "operation": operation},
    )
    assert resp.status_code == 200, resp.text
    return resp.json()


def _audit_event_types(db_session, entities):
    """All audit events touching any of the given (entity_type, entity_id)
    rows, ordered by insertion -- the same approach demo_service.py uses
    for its own timelines, since the simulated clock can hold several
    writes at the same instant."""
    conditions = [
        (AuditLog.entity_type == etype) & (AuditLog.entity_id == str(eid)) for etype, eid in entities
    ]
    combined = conditions[0]
    for condition in conditions[1:]:
        combined = combined | condition
    events = db_session.query(AuditLog).filter(combined).order_by(AuditLog.id).all()
    return [event.event_type for event in events]


# ---------------------------------------------------------------------------
# Journey A -- Legitimate purpose: grant -> retrieve -> copy -> valid use -> ALLOW
# ---------------------------------------------------------------------------

def test_journey_a_legitimate_purpose_end_to_end(client, db_session):
    asset = _create_asset(client)
    grant = _create_grant(client, asset["id"], purpose="clinical_trial_screening")
    retrieved = _retrieve(client, grant["id"], asset["id"])
    copy = _copy(client, retrieved["id"], "analysis_dataset_1")
    decision = _use(client, copy["id"], purpose="clinical_trial_screening")

    # --- API response ---
    assert decision["decision"] == "ALLOW"
    assert decision["reason_code"] == "WITHIN_PURPOSE_AND_VALIDITY"
    assert decision["remediation"] is None
    assert decision["remediation_status"] is None
    assert decision["grant_id"] == grant["id"]

    # --- Persisted database state ---
    persisted_grant = db_session.get(Grant, grant["id"])
    assert persisted_grant.status == GrantStatus.ACTIVE

    root = db_session.get(DataAsset, asset["id"])
    persisted_retrieved = db_session.get(DataAsset, retrieved["id"])
    persisted_copy = db_session.get(DataAsset, copy["id"])
    assert root.state == AssetState.ACTIVE
    assert persisted_retrieved.state == AssetState.ACTIVE
    assert persisted_copy.state == AssetState.ACTIVE
    assert persisted_copy.origin_grant_id == grant["id"]
    assert persisted_copy.root_asset_id == asset["id"]

    assert db_session.query(Remediation).count() == 0

    # --- Audit trail ---
    event_types = _audit_event_types(
        db_session,
        [("data_asset", asset["id"]), ("grant", grant["id"]), ("data_asset", retrieved["id"]), ("data_asset", copy["id"])],
    )
    assert event_types == [
        "SOURCE_ASSET_CREATED",
        "GRANT_CREATED",
        "DATA_RETRIEVED",
        "COPY_CREATED",
        "DATA_USE_ATTEMPTED",
        "USE_ALLOWED",
    ]


def test_journey_a_can_be_run_repeatedly_without_interference(client, db_session):
    """Same journey, run twice in the same test (and implicitly, this
    whole file can be re-run any number of times -- see the isolated
    tmp_path-backed database in conftest.py). Each run must produce its
    own independent, non-colliding chain and an independent ALLOW."""
    first_asset = _create_asset(client, name="Run 1 Asset")
    first_grant = _create_grant(client, first_asset["id"])
    first_retrieved = _retrieve(client, first_grant["id"], first_asset["id"])
    first_copy = _copy(client, first_retrieved["id"], "run_1_copy")
    first_decision = _use(client, first_copy["id"], purpose="clinical_trial_screening")

    second_asset = _create_asset(client, name="Run 2 Asset")
    second_grant = _create_grant(client, second_asset["id"])
    second_retrieved = _retrieve(client, second_grant["id"], second_asset["id"])
    second_copy = _copy(client, second_retrieved["id"], "run_2_copy")
    second_decision = _use(client, second_copy["id"], purpose="clinical_trial_screening")

    assert first_decision["decision"] == second_decision["decision"] == "ALLOW"
    assert first_asset["id"] != second_asset["id"]
    assert first_grant["id"] != second_grant["id"]
    assert db_session.query(DataAsset).count() == 6  # 2 roots + 2 retrieved + 2 copies


# ---------------------------------------------------------------------------
# Journey B -- Expired purpose: grant -> retrieve -> copy -> advance time ->
# reuse -> DENY -> violation -> quarantine
# ---------------------------------------------------------------------------

def test_journey_b_expired_purpose_end_to_end(client, db_session):
    from app.core.clock import clock

    asset = _create_asset(client)
    grant = _create_grant(client, asset["id"], purpose="clinical_trial_screening", duration_minutes=10)
    retrieved = _retrieve(client, grant["id"], asset["id"])
    copy = _copy(client, retrieved["id"], "analysis_dataset_1")

    clock.advance(minutes=11)  # simulated jump past the 10-minute grant -- no real waiting

    decision = _use(client, copy["id"], purpose="clinical_trial_screening")

    # --- API response ---
    assert decision["decision"] == "DENY"
    assert decision["reason_code"] == "PURPOSE_EXPIRED"
    assert "expired" in decision["reason"].lower()
    assert decision["remediation"]
    assert decision["remediation_status"] == "COMPLIANCE_REVIEW_REQUIRED"

    # --- Persisted database state ---
    persisted_copy = db_session.get(DataAsset, copy["id"])
    assert persisted_copy.state == AssetState.QUARANTINED

    root = db_session.get(DataAsset, asset["id"])
    assert root.state == AssetState.ACTIVE  # only the copy is punished, not the source

    status_resp = client.get(f"/grants/{grant['id']}/status")
    assert status_resp.json()["status"] == "EXPIRED"
    assert status_resp.json()["reason_code"] == "EXPIRY_TIME_PASSED"

    remediation = db_session.query(Remediation).filter(Remediation.asset_id == copy["id"]).one()
    assert remediation.reason_code == "PURPOSE_EXPIRED"
    assert remediation.status == RemediationStatus.COMPLIANCE_REVIEW_REQUIRED
    assert remediation.grant_id == grant["id"]
    assert remediation.corrective_action == decision["remediation"]

    # --- Audit trail ---
    event_types = _audit_event_types(
        db_session,
        [("data_asset", asset["id"]), ("grant", grant["id"]), ("data_asset", retrieved["id"]), ("data_asset", copy["id"])],
    )
    assert event_types == [
        "SOURCE_ASSET_CREATED",
        "GRANT_CREATED",
        "DATA_RETRIEVED",
        "COPY_CREATED",
        "DATA_USE_ATTEMPTED",
        "PURPOSE_VIOLATION",
        "ASSET_QUARANTINED",
        "COMPLIANCE_REVIEW_REQUIRED",
    ]


def test_journey_b_can_be_run_repeatedly_without_interference(client, db_session):
    from app.core.clock import clock

    for i in range(2):
        asset = _create_asset(client, name=f"Expiry Run {i}")
        grant = _create_grant(client, asset["id"], duration_minutes=10)
        retrieved = _retrieve(client, grant["id"], asset["id"])
        copy = _copy(client, retrieved["id"], f"expiry_copy_{i}")

        clock.advance(minutes=11)
        decision = _use(client, copy["id"], purpose="clinical_trial_screening")

        assert decision["decision"] == "DENY"
        assert decision["reason_code"] == "PURPOSE_EXPIRED"
        assert db_session.get(DataAsset, copy["id"]).state == AssetState.QUARANTINED

    assert db_session.query(Remediation).count() == 2


# ---------------------------------------------------------------------------
# Journey C -- Wrong purpose: grant for clinical trial -> retrieve ->
# attempted marketing use -> DENY -> remediation
# ---------------------------------------------------------------------------

def test_journey_c_wrong_purpose_end_to_end(client, db_session):
    asset = _create_asset(client)
    grant = _create_grant(client, asset["id"], purpose="clinical_trial_screening")
    retrieved = _retrieve(client, grant["id"], asset["id"])

    decision = _use(client, retrieved["id"], purpose="marketing_analytics")

    # --- API response ---
    assert decision["decision"] == "DENY"
    assert decision["reason_code"] == "PURPOSE_MISMATCH"
    assert "clinical_trial_screening" in decision["reason"]
    assert "marketing_analytics" in decision["reason"]
    assert decision["remediation"]
    assert decision["remediation_status"] == "COMPLIANCE_REVIEW_REQUIRED"

    # --- Persisted database state ---
    persisted_retrieved = db_session.get(DataAsset, retrieved["id"])
    assert persisted_retrieved.state == AssetState.QUARANTINED

    # The grant itself is untouched -- a purpose mismatch is a fault of
    # the *use*, not the grant, which remains valid for its own,
    # original purpose until it separately expires or is revoked.
    persisted_grant = db_session.get(Grant, grant["id"])
    assert persisted_grant.status == GrantStatus.ACTIVE

    remediation = db_session.query(Remediation).filter(Remediation.asset_id == retrieved["id"]).one()
    assert remediation.reason_code == "PURPOSE_MISMATCH"
    assert remediation.status == RemediationStatus.COMPLIANCE_REVIEW_REQUIRED

    # --- Audit trail ---
    event_types = _audit_event_types(
        db_session, [("data_asset", asset["id"]), ("grant", grant["id"]), ("data_asset", retrieved["id"])]
    )
    assert event_types == [
        "SOURCE_ASSET_CREATED",
        "GRANT_CREATED",
        "DATA_RETRIEVED",
        "DATA_USE_ATTEMPTED",
        "PURPOSE_VIOLATION",
        "ASSET_QUARANTINED",
        "COMPLIANCE_REVIEW_REQUIRED",
    ]


def test_journey_c_can_be_run_repeatedly_without_interference(client, db_session):
    for i in range(2):
        asset = _create_asset(client, name=f"Mismatch Run {i}")
        grant = _create_grant(client, asset["id"])
        retrieved = _retrieve(client, grant["id"], asset["id"])

        decision = _use(client, retrieved["id"], purpose="marketing_analytics")

        assert decision["decision"] == "DENY"
        assert decision["reason_code"] == "PURPOSE_MISMATCH"

    assert db_session.query(Remediation).count() == 2


# ---------------------------------------------------------------------------
# Cross-cutting requirements for this suite itself
# ---------------------------------------------------------------------------

def test_no_journey_depends_on_real_time_sleeping(client, monkeypatch):
    """Hard guard, not just convention: if any code path this suite
    exercises ever started calling time.sleep, this test fails loudly
    instead of the suite silently becoming slow/flaky."""

    def _boom(*args, **kwargs):
        raise AssertionError("E2E journeys must not depend on real time.sleep")

    monkeypatch.setattr(time, "sleep", _boom)

    asset = _create_asset(client)
    grant = _create_grant(client, asset["id"], duration_minutes=10)
    retrieved = _retrieve(client, grant["id"], asset["id"])
    copy = _copy(client, retrieved["id"], "no_sleep_copy")

    from app.core.clock import clock

    clock.advance(minutes=11)
    decision = _use(client, copy["id"], purpose="clinical_trial_screening")
    assert decision["decision"] == "DENY"
