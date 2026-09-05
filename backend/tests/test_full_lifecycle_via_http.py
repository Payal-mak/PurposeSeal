"""Proves the entire PurposeSeal workflow — original asset, grant,
retrieval, derived copy, revocation, and use evaluation — can be driven
start-to-finish through HTTP alone, with no direct ORM seeding."""

from app.models import AssetState, DataAsset


def test_full_lifecycle_starts_and_ends_entirely_over_http(client, db_session):
    # 1. Create an original asset through HTTP (no ORM seeding).
    asset_resp = client.post(
        "/assets", json={"name": "Patient Lab Result #104", "asset_type": "lab_result"}
    )
    assert asset_resp.status_code == 201, asset_resp.text
    asset = asset_resp.json()

    # 2. Create a grant for that asset.
    grant_resp = client.post(
        "/grants",
        json={
            "subject": "researcher_01",
            "purpose": "clinical_trial_screening",
            "asset_id": asset["id"],
            "duration_minutes": 30,
            "allowed_operations": ["VIEW", "ANALYZE", "COPY"],
        },
    )
    assert grant_resp.status_code == 201, grant_resp.text
    grant = grant_resp.json()

    # 3. Retrieve it.
    retrieval_resp = client.post(
        "/retrievals",
        json={"grant_id": grant["id"], "actor": "researcher_01", "asset_id": asset["id"], "operation": "VIEW"},
    )
    assert retrieval_resp.status_code == 201, retrieval_resp.text
    retrieved = retrieval_resp.json()

    # 4. Create a derived/copy asset.
    copy_resp = client.post(
        "/copies",
        json={
            "parent_asset_id": retrieved["id"],
            "name": "analysis_dataset_1",
            "asset_type": "dataset",
            "derivation_type": "DERIVED",
            "actor": "researcher_01",
            "operation": "ANALYZE",
        },
    )
    assert copy_resp.status_code == 201, copy_resp.text
    derived = copy_resp.json()

    # 5. Revoke the original grant (revocation is COMPLIANCE_OFFICER/ADMIN-only).
    client.post(
        "/auth/register", json={"username": "compliance_01", "password": "password123", "role": "COMPLIANCE_OFFICER"}
    )
    login = client.post("/auth/login", json={"username": "compliance_01", "password": "password123"})
    assert login.status_code == 200, login.text
    headers = {"Authorization": f"Bearer {login.json()['access_token']}"}

    revoke_resp = client.post(f"/grants/{grant['id']}/revoke", headers=headers)
    assert revoke_resp.status_code == 200, revoke_resp.text
    assert revoke_resp.json()["status"] == "REVOKED"

    # 6. Attempt to use the existing (derived) copy.
    use_resp = client.post(
        "/uses",
        json={
            "asset_id": derived["id"],
            "actor": "researcher_01",
            "purpose": "clinical_trial_screening",
            "operation": "ANALYZE",
        },
    )
    assert use_resp.status_code == 200, use_resp.text
    decision = use_resp.json()

    # 7. Confirm policy returns DENY / GRANT_REVOKED.
    assert decision["decision"] == "DENY"
    assert decision["reason_code"] == "GRANT_REVOKED"

    # 8. Confirm the affected copy is quarantined.
    quarantined = db_session.get(DataAsset, derived["id"])
    assert quarantined.state == AssetState.QUARANTINED

    # The root asset itself is untouched by the derived copy's quarantine.
    root = db_session.get(DataAsset, asset["id"])
    assert root.state == AssetState.ACTIVE
