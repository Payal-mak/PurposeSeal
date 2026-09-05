import time

from app.models import AssetState, DataAsset


def test_legitimate_scenario_returns_allow(client):
    resp = client.post("/demo/scenarios/legitimate")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["scenario"] == "legitimate"
    assert body["decision"] == "ALLOW"
    assert body["reason_code"] == "WITHIN_PURPOSE_AND_VALIDITY"
    assert body["remediation"] is None


def test_expired_scenario_returns_deny(client):
    resp = client.post("/demo/scenarios/expired")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["scenario"] == "expired"
    assert body["decision"] == "DENY"
    assert body["reason_code"] == "PURPOSE_EXPIRED"
    assert body["remediation"]


def test_mismatch_scenario_returns_deny(client):
    resp = client.post("/demo/scenarios/purpose-mismatch")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["scenario"] == "purpose-mismatch"
    assert body["decision"] == "DENY"
    assert body["reason_code"] == "PURPOSE_MISMATCH"
    assert body["remediation"]


def test_legitimate_scenario_expected_audit_sequence(client, db_session):
    body = client.post("/demo/scenarios/legitimate").json()

    event_types = [e["event_type"] for e in body["timeline"]]
    assert event_types == [
        "SOURCE_ASSET_CREATED",
        "GRANT_CREATED",
        "DATA_RETRIEVED",
        "COPY_CREATED",
        "DATA_USE_ATTEMPTED",
        "USE_ALLOWED",
    ]

    asset = db_session.get(DataAsset, body["asset_id"])
    assert asset.state == AssetState.ACTIVE


def test_expired_scenario_expected_audit_sequence_and_state(client, db_session):
    body = client.post("/demo/scenarios/expired").json()

    event_types = [e["event_type"] for e in body["timeline"]]
    assert event_types == [
        "SOURCE_ASSET_CREATED",
        "GRANT_CREATED",
        "DATA_RETRIEVED",
        "DERIVED_ASSET_CREATED",
        "DATA_USE_ATTEMPTED",
        "PURPOSE_VIOLATION",
        "ASSET_QUARANTINED",
    ]

    quarantined = db_session.get(DataAsset, body["asset_id"])
    assert quarantined.state == AssetState.QUARANTINED


def test_mismatch_scenario_expected_audit_sequence_and_state(client, db_session):
    body = client.post("/demo/scenarios/purpose-mismatch").json()

    event_types = [e["event_type"] for e in body["timeline"]]
    assert event_types == [
        "SOURCE_ASSET_CREATED",
        "GRANT_CREATED",
        "DATA_RETRIEVED",
        "DATA_USE_ATTEMPTED",
        "PURPOSE_VIOLATION",
        "ASSET_QUARANTINED",
    ]

    quarantined = db_session.get(DataAsset, body["asset_id"])
    assert quarantined.state == AssetState.QUARANTINED


def test_scenarios_can_be_rerun_safely(client):
    first = client.post("/demo/scenarios/legitimate").json()
    second = client.post("/demo/scenarios/legitimate").json()

    assert first["decision"] == "ALLOW"
    assert second["decision"] == "ALLOW"
    # Fresh asset/grant/copy chain each run -- no id collision between runs.
    assert first["asset_id"] != second["asset_id"]


def test_all_scenarios_rerun_deterministically(client):
    for endpoint, expected_decision, expected_reason in [
        ("/demo/scenarios/legitimate", "ALLOW", "WITHIN_PURPOSE_AND_VALIDITY"),
        ("/demo/scenarios/expired", "DENY", "PURPOSE_EXPIRED"),
        ("/demo/scenarios/purpose-mismatch", "DENY", "PURPOSE_MISMATCH"),
    ]:
        first = client.post(endpoint).json()
        second = client.post(endpoint).json()
        assert first["decision"] == second["decision"] == expected_decision
        assert first["reason_code"] == second["reason_code"] == expected_reason


def test_no_real_time_sleeping_is_used(client, monkeypatch):
    def _boom(*args, **kwargs):
        raise AssertionError("scenario logic must not call time.sleep")

    monkeypatch.setattr(time, "sleep", _boom)

    for endpoint in ("/demo/scenarios/legitimate", "/demo/scenarios/expired", "/demo/scenarios/purpose-mismatch"):
        resp = client.post(endpoint)
        assert resp.status_code == 200


def test_timeline_events_include_details_and_timestamps(client):
    body = client.post("/demo/scenarios/expired").json()
    for event in body["timeline"]:
        assert "created_at" in event
        assert event["entity_type"] in {"data_asset", "grant"}
