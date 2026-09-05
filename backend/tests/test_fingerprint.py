"""Fingerprinting and Provenance Evidence.

SHA-256 fingerprinting itself was already built as part of two earlier
features (Data Retrieval and Purpose Seal Propagation; Copy and
Derived-Data Lineage) -- see app/services/fingerprint.py. This file is
the dedicated regression coverage for the specific properties that
matter for it to serve as honest provenance *evidence*: determinism,
persistence, and the fact that it can prove exact-copy provenance but
NOT detect a transformed/aggregated derivative (a limitation this
suite makes concrete rather than just documenting in a comment).
"""

from app.models import DataAsset
from app.services.fingerprint import compute_fingerprint, compute_transformed_fingerprint


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


# ---------------------------------------------------------------------------
# Deterministic generation
# ---------------------------------------------------------------------------

def test_fingerprint_generation_is_deterministic(existing_asset):
    """The same asset hashed twice, independent of any HTTP call or
    database round-trip, must produce byte-identical hashes -- no
    randomness, no dependence on wall-clock time or insertion order."""
    first = compute_fingerprint(existing_asset)
    second = compute_fingerprint(existing_asset)
    assert first == second


def test_fingerprint_is_a_valid_sha256_hex_digest(existing_asset):
    digest = compute_fingerprint(existing_asset)
    assert len(digest) == 64
    assert all(c in "0123456789abcdef" for c in digest)


def test_different_source_assets_yield_different_fingerprints(client, existing_asset, db_session):
    from app.core.clock import clock
    from app.models.data_asset import AssetState

    other_asset = DataAsset(
        name="Unrelated Record", asset_type="other", state=AssetState.ACTIVE, created_at=clock.now()
    )
    db_session.add(other_asset)
    db_session.commit()
    db_session.refresh(other_asset)

    assert compute_fingerprint(existing_asset) != compute_fingerprint(other_asset)


# ---------------------------------------------------------------------------
# Copied identical content yields the expected fingerprint
# ---------------------------------------------------------------------------

def test_exact_copy_shares_the_root_assets_fingerprint(client, existing_asset):
    """A retrieval and a COPY derivation both represent byte-identical
    content -- they must carry the exact same fingerprint as evidence
    that the copy really did originate from the protected root asset."""
    grant = _create_grant(client, existing_asset.id)
    retrieved = _retrieve(client, grant["id"], existing_asset.id)
    copy = _create_copy(client, retrieved["id"], "exact_copy", derivation_type="COPY")

    expected = compute_fingerprint(existing_asset)
    assert retrieved["fingerprint"] == expected
    assert copy["fingerprint"] == expected


def test_repeated_retrieval_of_the_same_asset_yields_the_same_fingerprint(client, existing_asset):
    """Retrieving the same root asset twice (e.g. under two separate
    grants) must not produce two different fingerprints for what is
    still the same underlying content."""
    grant_a = _create_grant(client, existing_asset.id)
    grant_b = _create_grant(client, existing_asset.id, purpose="follow_up_review")

    first = _retrieve(client, grant_a["id"], existing_asset.id)
    second = _retrieve(client, grant_b["id"], existing_asset.id)

    assert first["fingerprint"] == second["fingerprint"]


# ---------------------------------------------------------------------------
# Changed content yields a changed fingerprint
# ---------------------------------------------------------------------------

def test_derived_transformed_asset_gets_a_different_fingerprint_than_its_parent(client, existing_asset):
    """A DERIVED asset represents transformed/summarized content, not a
    byte-identical copy -- it must NOT share its parent's fingerprint.
    This is the concrete demonstration of the honesty requirement: an
    exact hash cannot vouch for transformed content, so it deliberately
    doesn't pretend to by reusing the same hash."""
    grant = _create_grant(client, existing_asset.id)
    retrieved = _retrieve(client, grant["id"], existing_asset.id)
    derived = _create_copy(client, retrieved["id"], "summary_report", derivation_type="DERIVED", operation="ANALYZE")

    assert derived["fingerprint"] != retrieved["fingerprint"]


def test_two_derived_assets_with_different_content_get_different_fingerprints(client, existing_asset):
    grant = _create_grant(client, existing_asset.id)
    retrieved = _retrieve(client, grant["id"], existing_asset.id)

    derived_a = _create_copy(client, retrieved["id"], "summary_report_v1", derivation_type="DERIVED", operation="ANALYZE")
    derived_b = _create_copy(client, retrieved["id"], "summary_report_v2", derivation_type="DERIVED", operation="ANALYZE")

    assert derived_a["fingerprint"] != derived_b["fingerprint"]


def test_compute_transformed_fingerprint_changes_when_name_changes(existing_asset):
    """Unit-level confirmation, independent of the HTTP layer: changing
    what's being fingerprinted changes the output hash."""
    original = compute_transformed_fingerprint("report_v1", "dataset", existing_asset)
    changed = compute_transformed_fingerprint("report_v2", "dataset", existing_asset)
    assert original != changed


# ---------------------------------------------------------------------------
# Persisted fingerprint
# ---------------------------------------------------------------------------

def test_fingerprint_is_persisted_and_survives_reload(client, existing_asset, db_engine):
    from sqlalchemy.orm import sessionmaker

    grant = _create_grant(client, existing_asset.id)
    retrieved = _retrieve(client, grant["id"], existing_asset.id)

    # A brand new session bound to the same underlying database,
    # independent of any session used above -- simulates an application
    # restart, the same pattern used by test_remediation_survives_restart.
    fresh_session = sessionmaker(bind=db_engine)()
    try:
        persisted = fresh_session.get(DataAsset, retrieved["id"])
        assert persisted.fingerprint == retrieved["fingerprint"]
    finally:
        fresh_session.close()


def test_fingerprint_is_returned_by_get_asset(client, existing_asset):
    grant = _create_grant(client, existing_asset.id)
    retrieved = _retrieve(client, grant["id"], existing_asset.id)

    resp = client.get(f"/assets/{retrieved['id']}")
    assert resp.status_code == 200
    assert resp.json()["fingerprint"] == retrieved["fingerprint"]


# ---------------------------------------------------------------------------
# Missing content edge case
# ---------------------------------------------------------------------------

def test_original_source_asset_has_no_fingerprint(client):
    """An original/root asset isn't a copy of anything -- there's no
    "expected fingerprint" for it to match, so it's persisted with
    fingerprint=None rather than a hash of itself. This is the "missing
    content" edge case: no fingerprint is possible, and the API/DB must
    represent that as an explicit null, not an empty string, a crash, or
    a stray fabricated hash that would misleadingly claim provenance
    evidence for the one asset that doesn't need any."""
    resp = client.post("/assets", json={"name": "Never Retrieved", "asset_type": "lab_result"})
    assert resp.status_code == 201
    body = resp.json()

    assert body["fingerprint"] is None

    fetched = client.get(f"/assets/{body['id']}")
    assert fetched.json()["fingerprint"] is None
