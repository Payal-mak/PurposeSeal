from sqlalchemy.orm import sessionmaker

from app.models import AuditLog, DataAsset


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
    return client.post(
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


def _build_full_chain(client, existing_asset):
    """patient_lab_104 -> retrieved_copy_1 -> analysis_dataset_1 -> derived_report_1"""
    grant = _create_grant(client, existing_asset.id)
    retrieved_copy_1 = _retrieve(client, grant["id"], existing_asset.id)

    analysis_resp = _create_copy(
        client, retrieved_copy_1["id"], "analysis_dataset_1", asset_type="analysis_dataset",
        derivation_type="DERIVED", operation="ANALYZE",
    )
    assert analysis_resp.status_code == 201, analysis_resp.text
    analysis_dataset_1 = analysis_resp.json()

    report_resp = _create_copy(
        client, analysis_dataset_1["id"], "derived_report_1", asset_type="report",
        derivation_type="DERIVED", operation="ANALYZE",
    )
    assert report_resp.status_code == 201, report_resp.text
    derived_report_1 = report_resp.json()

    return grant, retrieved_copy_1, analysis_dataset_1, derived_report_1


def test_create_direct_copy(client, existing_asset):
    grant = _create_grant(client, existing_asset.id)
    retrieved_copy_1 = _retrieve(client, grant["id"], existing_asset.id)

    resp = _create_copy(client, retrieved_copy_1["id"], "analysis_dataset_1", asset_type="analysis_dataset")
    assert resp.status_code == 201
    body = resp.json()
    assert body["name"] == "analysis_dataset_1"
    assert body["asset_type"] == "analysis_dataset"
    assert body["state"] == "ACTIVE"


def test_child_has_correct_parent(client, existing_asset):
    grant, retrieved_copy_1, analysis_dataset_1, derived_report_1 = _build_full_chain(client, existing_asset)

    assert analysis_dataset_1["parent_asset_id"] == retrieved_copy_1["id"]
    assert derived_report_1["parent_asset_id"] == analysis_dataset_1["id"]


def test_child_has_correct_root(client, existing_asset):
    grant, retrieved_copy_1, analysis_dataset_1, derived_report_1 = _build_full_chain(client, existing_asset)

    assert retrieved_copy_1["root_asset_id"] == existing_asset.id
    assert analysis_dataset_1["root_asset_id"] == existing_asset.id
    assert derived_report_1["root_asset_id"] == existing_asset.id
    assert derived_report_1["effective_root_asset_id"] == existing_asset.id


def test_purpose_provenance_preserved_through_chain(client, existing_asset):
    grant, retrieved_copy_1, analysis_dataset_1, derived_report_1 = _build_full_chain(client, existing_asset)

    for node in (retrieved_copy_1, analysis_dataset_1, derived_report_1):
        assert node["origin_grant_id"] == grant["id"]
        assert node["origin_purpose"] == "clinical_trial_screening"
        assert node["origin_grant_expires_at"] == grant["expires_at"]


def test_multiple_descendants(client, existing_asset):
    grant, retrieved_copy_1, analysis_dataset_1, derived_report_1 = _build_full_chain(client, existing_asset)

    # A second, independent branch off the same retrieved copy.
    second_branch = _create_copy(
        client, retrieved_copy_1["id"], "analysis_dataset_2", asset_type="analysis_dataset",
        derivation_type="DERIVED", operation="ANALYZE",
    )
    assert second_branch.status_code == 201
    assert second_branch.json()["parent_asset_id"] == retrieved_copy_1["id"]
    assert second_branch.json()["root_asset_id"] == existing_asset.id


def test_lineage_query_from_root(client, existing_asset):
    grant, retrieved_copy_1, analysis_dataset_1, derived_report_1 = _build_full_chain(client, existing_asset)

    resp = client.get(f"/assets/{existing_asset.id}/lineage")
    assert resp.status_code == 200
    body = resp.json()

    assert body["root_asset_id"] == existing_asset.id
    node_ids = {n["id"] for n in body["nodes"]}
    assert node_ids == {existing_asset.id, retrieved_copy_1["id"], analysis_dataset_1["id"], derived_report_1["id"]}

    edges = {(e["parent_id"], e["child_id"]) for e in body["edges"]}
    assert (existing_asset.id, retrieved_copy_1["id"]) in edges
    assert (retrieved_copy_1["id"], analysis_dataset_1["id"]) in edges
    assert (analysis_dataset_1["id"], derived_report_1["id"]) in edges


def test_lineage_query_from_deep_descendant_returns_same_tree(client, existing_asset):
    grant, retrieved_copy_1, analysis_dataset_1, derived_report_1 = _build_full_chain(client, existing_asset)

    from_root = client.get(f"/assets/{existing_asset.id}/lineage").json()
    from_leaf = client.get(f"/assets/{derived_report_1['id']}/lineage").json()

    assert from_root["root_asset_id"] == from_leaf["root_asset_id"]
    assert {n["id"] for n in from_root["nodes"]} == {n["id"] for n in from_leaf["nodes"]}


def test_copy_with_invalid_parent_returns_404(client):
    resp = _create_copy(client, 999999, "orphan_copy")
    assert resp.status_code == 404
    assert resp.json()["error_code"] == "asset_not_found"


def test_extra_provenance_fields_are_rejected(client, existing_asset):
    """Cross-purpose misuse cannot silently change inherited provenance:
    CopyCreate doesn't accept root_asset_id/origin_grant_id at all, so an
    attempt to supply them is a hard validation error, not something that
    could quietly override the parent-derived values."""
    grant = _create_grant(client, existing_asset.id)
    retrieved_copy_1 = _retrieve(client, grant["id"], existing_asset.id)

    resp = client.post(
        "/copies",
        json={
            "parent_asset_id": retrieved_copy_1["id"],
            "name": "malicious_copy",
            "asset_type": "dataset",
            "actor": "researcher_01",
            "operation": "COPY",
            "origin_grant_id": 999999,
        },
    )
    assert resp.status_code == 422


def test_copy_of_never_retrieved_asset_denied(client, existing_asset):
    resp = _create_copy(client, existing_asset.id, "premature_copy")
    assert resp.status_code == 403
    assert resp.json()["error_code"] == "no_origin_grant"


def test_copy_with_wrong_actor_denied(client, existing_asset, db_session):
    grant = _create_grant(client, existing_asset.id)
    retrieved_copy_1 = _retrieve(client, grant["id"], existing_asset.id)

    resp = _create_copy(client, retrieved_copy_1["id"], "analysis_dataset_1", actor="someone_else")
    assert resp.status_code == 403
    assert resp.json()["error_code"] == "actor_mismatch"
    assert db_session.query(DataAsset).count() == 2  # root + retrieved copy only


def test_copy_with_disallowed_operation_denied(client, existing_asset):
    grant = _create_grant(client, existing_asset.id, allowed_operations=["VIEW"])
    retrieved_copy_1 = _retrieve(client, grant["id"], existing_asset.id, operation="VIEW")

    resp = _create_copy(client, retrieved_copy_1["id"], "analysis_dataset_1", operation="COPY")
    assert resp.status_code == 403
    assert resp.json()["error_code"] == "operation_not_permitted"


def test_copy_created_audit_event_for_exact_copy(client, existing_asset, db_session):
    grant = _create_grant(client, existing_asset.id)
    retrieved_copy_1 = _retrieve(client, grant["id"], existing_asset.id)

    resp = _create_copy(client, retrieved_copy_1["id"], "exact_copy_1", derivation_type="COPY", operation="COPY")
    assert resp.status_code == 201
    new_id = resp.json()["id"]

    logs = db_session.query(AuditLog).filter(AuditLog.entity_id == str(new_id)).all()
    assert len(logs) == 1
    assert logs[0].event_type == "COPY_CREATED"


def test_derived_asset_created_audit_event(client, existing_asset, db_session):
    grant = _create_grant(client, existing_asset.id)
    retrieved_copy_1 = _retrieve(client, grant["id"], existing_asset.id)

    resp = _create_copy(
        client, retrieved_copy_1["id"], "analysis_dataset_1", derivation_type="DERIVED", operation="ANALYZE"
    )
    assert resp.status_code == 201
    new_id = resp.json()["id"]

    logs = db_session.query(AuditLog).filter(AuditLog.entity_id == str(new_id)).all()
    assert len(logs) == 1
    assert logs[0].event_type == "DERIVED_ASSET_CREATED"


def test_lineage_survives_session_reload(client, existing_asset, db_engine):
    grant, retrieved_copy_1, analysis_dataset_1, derived_report_1 = _build_full_chain(client, existing_asset)

    # Simulate an application restart: a brand new session bound to the
    # same underlying database, independent of any session used above.
    fresh_session = sessionmaker(bind=db_engine)()
    try:
        root = fresh_session.get(DataAsset, existing_asset.id)
        copy1 = fresh_session.get(DataAsset, retrieved_copy_1["id"])
        analysis = fresh_session.get(DataAsset, analysis_dataset_1["id"])
        report = fresh_session.get(DataAsset, derived_report_1["id"])

        assert copy1.parent_asset_id == root.id
        assert analysis.parent_asset_id == copy1.id
        assert report.parent_asset_id == analysis.id

        assert copy1.root_asset_id == root.id
        assert analysis.root_asset_id == root.id
        assert report.root_asset_id == root.id

        assert copy1.origin_grant_id == grant["id"]
        assert analysis.origin_grant_id == grant["id"]
        assert report.origin_grant_id == grant["id"]
    finally:
        fresh_session.close()
