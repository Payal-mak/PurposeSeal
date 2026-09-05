import pytest

from app.models import AuditLog, DataAsset


def _create_asset(client, name="Patient Lab Result #104", asset_type="lab_result"):
    return client.post("/assets", json={"name": name, "asset_type": asset_type})


def test_create_valid_original_asset(client):
    resp = _create_asset(client)
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["name"] == "Patient Lab Result #104"
    assert body["asset_type"] == "lab_result"


def test_original_asset_has_no_parent(client):
    body = _create_asset(client).json()
    assert body["parent_asset_id"] is None


def test_original_asset_has_no_root_pointer(client):
    body = _create_asset(client).json()
    assert body["root_asset_id"] is None
    assert body["effective_root_asset_id"] == body["id"]  # root_asset_id or id


def test_original_asset_has_no_origin_grant(client):
    body = _create_asset(client).json()
    assert body["origin_grant_id"] is None
    assert body["origin_purpose"] is None


def test_original_asset_starts_active(client):
    body = _create_asset(client).json()
    assert body["state"] == "ACTIVE"


def test_source_asset_created_audit_event_exists(client, db_session):
    body = _create_asset(client).json()
    events = db_session.query(AuditLog).filter(AuditLog.entity_id == str(body["id"])).all()
    event_types = [e.event_type for e in events]
    assert "SOURCE_ASSET_CREATED" in event_types


def test_asset_creation_and_audit_are_atomic(client, db_session, monkeypatch):
    from app.services import asset_service

    def _boom(*args, **kwargs):
        raise RuntimeError("simulated audit failure")

    monkeypatch.setattr(asset_service, "write_audit_log", _boom)

    with pytest.raises(RuntimeError, match="simulated audit failure"):
        client.post("/assets", json={"name": "Should Not Persist", "asset_type": "lab_result"})

    assert db_session.query(DataAsset).filter(DataAsset.name == "Should Not Persist").first() is None


def test_missing_name_rejected(client):
    resp = client.post("/assets", json={"asset_type": "lab_result"})
    assert resp.status_code == 422


def test_empty_name_rejected(client):
    resp = _create_asset(client, name="")
    assert resp.status_code == 422


def test_missing_asset_type_rejected(client):
    resp = client.post("/assets", json={"name": "Patient Lab Result #104"})
    assert resp.status_code == 422


def test_caller_cannot_inject_parent_asset_id(client):
    resp = client.post(
        "/assets",
        json={"name": "Sneaky", "asset_type": "lab_result", "parent_asset_id": 1},
    )
    assert resp.status_code == 422


def test_caller_cannot_inject_root_asset_id(client):
    resp = client.post(
        "/assets",
        json={"name": "Sneaky", "asset_type": "lab_result", "root_asset_id": 1},
    )
    assert resp.status_code == 422


def test_caller_cannot_inject_origin_grant_id(client):
    resp = client.post(
        "/assets",
        json={"name": "Sneaky", "asset_type": "lab_result", "origin_grant_id": 1},
    )
    assert resp.status_code == 422


def test_caller_cannot_inject_state(client):
    resp = client.post(
        "/assets",
        json={"name": "Sneaky", "asset_type": "lab_result", "state": "QUARANTINED"},
    )
    assert resp.status_code == 422


def test_list_assets_works(client):
    _create_asset(client, name="Asset One")
    _create_asset(client, name="Asset Two", asset_type="survey")

    resp = client.get("/assets")
    assert resp.status_code == 200
    names = [a["name"] for a in resp.json()]
    assert "Asset One" in names
    assert "Asset Two" in names


def test_list_assets_filters_by_state(client):
    _create_asset(client, name="Active One")
    resp = client.get("/assets", params={"state": "ACTIVE"})
    assert resp.status_code == 200
    assert all(a["state"] == "ACTIVE" for a in resp.json())

    resp_quarantined = client.get("/assets", params={"state": "QUARANTINED"})
    assert resp_quarantined.status_code == 200
    assert all(a["name"] != "Active One" for a in resp_quarantined.json())


def test_list_assets_filters_by_asset_type(client):
    _create_asset(client, name="Lab", asset_type="lab_result")
    _create_asset(client, name="Survey", asset_type="survey")

    resp = client.get("/assets", params={"asset_type": "survey"})
    assert resp.status_code == 200
    names = [a["name"] for a in resp.json()]
    assert "Survey" in names
    assert "Lab" not in names


def test_list_assets_root_only_filter(client):
    root = _create_asset(client, name="Root Asset").json()
    grant = client.post(
        "/grants",
        json={
            "subject": "researcher_01",
            "purpose": "clinical_trial_screening",
            "asset_id": root["id"],
            "duration_minutes": 30,
            "allowed_operations": ["VIEW"],
        },
    ).json()
    client.post(
        "/retrievals",
        json={"grant_id": grant["id"], "actor": "researcher_01", "asset_id": root["id"], "operation": "VIEW"},
    )

    resp = client.get("/assets", params={"root_only": "true"})
    assert resp.status_code == 200
    ids = [a["id"] for a in resp.json()]
    assert root["id"] in ids


def test_retrieve_created_asset_through_get_by_id(client):
    created = _create_asset(client).json()
    resp = client.get(f"/assets/{created['id']}")
    assert resp.status_code == 200
    assert resp.json()["id"] == created["id"]


def test_nonexistent_asset_returns_clean_404(client):
    resp = client.get("/assets/999999")
    assert resp.status_code == 404
