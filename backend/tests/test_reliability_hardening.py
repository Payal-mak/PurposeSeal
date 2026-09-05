"""Regression tests for the reliability/idempotency hardening pass.

Each test below pins down one issue found during that review. See the
"Reliability and Idempotency Hardening" entry in
docs/PROJECT_PROGRESS.md for the full writeup of what was reviewed,
what was already safe, and what needed a fix.
"""

from fastapi.testclient import TestClient
from sqlalchemy.orm import sessionmaker

from app.db.session import get_db
from app.main import app
from app.models import AssetState, DataAsset


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


# ---------------------------------------------------------------------------
# Attempting to copy quarantined data
# ---------------------------------------------------------------------------

def test_cannot_copy_a_quarantined_asset(client, existing_asset, db_session):
    """A grant that authorized the original retrieval can remain ACTIVE
    (a purpose-mismatch violation doesn't touch the grant's own status)
    even after the asset it produced has been quarantined. Before this
    fix, copy_service never checked the parent's state, so quarantined
    data could still be freely copied/derived as long as the grant
    itself still looked valid."""
    grant = _create_grant(client, existing_asset.id)
    retrieved = _retrieve(client, grant["id"], existing_asset.id)

    denied_use = _use(client, retrieved["id"], purpose="marketing_analytics")
    assert denied_use.json()["decision"] == "DENY"
    assert db_session.get(DataAsset, retrieved["id"]).state == AssetState.QUARANTINED

    resp = client.post(
        "/copies",
        json={
            "parent_asset_id": retrieved["id"],
            "name": "laundered_copy",
            "asset_type": "dataset",
            "derivation_type": "COPY",
            "actor": "researcher_01",
            "operation": "COPY",
        },
    )
    assert resp.status_code == 403, resp.text
    assert resp.json()["error_code"] == "parent_quarantined"

    # No copy was created.
    assert db_session.query(DataAsset).filter(DataAsset.parent_asset_id == retrieved["id"]).count() == 0


def test_copy_of_quarantined_asset_is_audited_as_denied(client, existing_asset, db_session):
    from app.models import AuditLog

    grant = _create_grant(client, existing_asset.id)
    retrieved = _retrieve(client, grant["id"], existing_asset.id)
    _use(client, retrieved["id"], purpose="marketing_analytics")

    client.post(
        "/copies",
        json={
            "parent_asset_id": retrieved["id"],
            "name": "laundered_copy",
            "asset_type": "dataset",
            "derivation_type": "COPY",
            "actor": "researcher_01",
            "operation": "COPY",
        },
    )

    events = db_session.query(AuditLog).filter(
        AuditLog.entity_id == str(retrieved["id"]), AuditLog.event_type == "COPY_CREATION_DENIED"
    ).all()
    assert len(events) == 1
    assert '"reason_code": "parent_quarantined"' in events[0].details


# ---------------------------------------------------------------------------
# Retrieving already-quarantined data under a fresh grant
# ---------------------------------------------------------------------------

def test_cannot_retrieve_an_already_quarantined_asset(client, existing_asset, db_session):
    """A grant can be issued directly against any existing DataAsset,
    including one that isn't a root/source asset. If that asset has
    since been quarantined for a purpose violation, a brand-new grant
    must not be usable to "retrieve" it again -- before this fix,
    retrieval_service never checked the target asset's own state."""
    grant = _create_grant(client, existing_asset.id)
    retrieved = _retrieve(client, grant["id"], existing_asset.id)
    _use(client, retrieved["id"], purpose="marketing_analytics")
    assert db_session.get(DataAsset, retrieved["id"]).state == AssetState.QUARANTINED

    new_grant = _create_grant(client, retrieved["id"], purpose="follow_up_review")

    resp = client.post(
        "/retrievals",
        json={"grant_id": new_grant["id"], "actor": "researcher_01", "asset_id": retrieved["id"], "operation": "VIEW"},
    )
    assert resp.status_code == 403, resp.text
    assert resp.json()["error_code"] == "asset_quarantined"

    assert db_session.query(DataAsset).filter(DataAsset.parent_asset_id == retrieved["id"]).count() == 0


# ---------------------------------------------------------------------------
# Consistent, frontend-friendly error contract for malformed payloads
# ---------------------------------------------------------------------------

def test_malformed_payload_returns_consistent_error_contract(client, existing_asset):
    """FastAPI's default validation-error shape is {"detail": [...]}, a
    different contract than every other error in this API
    ({"error_code", "message"}). This must be normalized."""
    resp = client.post(
        "/grants",
        json={
            "subject": "researcher_01",
            "purpose": "clinical_trial_screening",
            "asset_id": existing_asset.id,
            "duration_minutes": -5,  # invalid: must be > 0
            "allowed_operations": ["VIEW"],
        },
    )
    assert resp.status_code == 422
    body = resp.json()
    assert body["error_code"] == "validation_error"
    assert isinstance(body["message"], str) and body["message"]
    assert "detail" not in body


def test_invalid_path_parameter_type_returns_consistent_error_contract(client):
    resp = client.get("/grants/not-an-integer")
    assert resp.status_code == 422
    body = resp.json()
    assert body["error_code"] == "validation_error"
    assert "detail" not in body


def test_grant_create_rejects_unexpected_fields(client, existing_asset):
    resp = client.post(
        "/grants",
        json={
            "subject": "researcher_01",
            "purpose": "clinical_trial_screening",
            "asset_id": existing_asset.id,
            "duration_minutes": 30,
            "allowed_operations": ["VIEW"],
            "unexpected_field": "should not be silently accepted",
        },
    )
    assert resp.status_code == 422
    assert resp.json()["error_code"] == "validation_error"


def test_retrieval_create_rejects_unexpected_fields(client, existing_asset):
    grant = _create_grant(client, existing_asset.id)
    resp = client.post(
        "/retrievals",
        json={
            "grant_id": grant["id"],
            "actor": "researcher_01",
            "asset_id": existing_asset.id,
            "operation": "VIEW",
            "unexpected_field": "should not be silently accepted",
        },
    )
    assert resp.status_code == 422
    assert resp.json()["error_code"] == "validation_error"


def test_unexpected_error_returns_clean_500_without_leaking_details(existing_asset, db_engine, monkeypatch):
    """Proves the generic exception handler is actually reachable with a
    clean, non-leaking body under conditions matching a real deployment.
    Every other atomicity test relies on TestClient's default of
    re-raising unhandled exceptions (useful for asserting rollback
    behavior) -- raise_server_exceptions=False here instead mirrors how
    a real uvicorn server responds to the same failure."""
    from app.services import grant_service

    def _boom(*args, **kwargs):
        raise RuntimeError("simulated internal failure: super secret internal detail")

    monkeypatch.setattr(grant_service, "write_audit_log", _boom)

    session_local = sessionmaker(autocommit=False, autoflush=False, bind=db_engine)

    def override_get_db():
        db = session_local()
        try:
            yield db
        finally:
            db.close()

    app.dependency_overrides[get_db] = override_get_db
    try:
        with TestClient(app, raise_server_exceptions=False) as isolated_client:
            resp = isolated_client.post(
                "/grants",
                json={
                    "subject": "researcher_01",
                    "purpose": "clinical_trial_screening",
                    "asset_id": existing_asset.id,
                    "duration_minutes": 30,
                    "allowed_operations": ["VIEW"],
                },
            )
    finally:
        app.dependency_overrides.clear()

    assert resp.status_code == 500
    assert resp.json() == {"error_code": "internal_error", "message": "An unexpected error occurred."}
    assert "super secret internal detail" not in resp.text
    assert "Traceback" not in resp.text


# ---------------------------------------------------------------------------
# Duplicate registration under a simulated race
# ---------------------------------------------------------------------------

def test_duplicate_registration_race_returns_conflict_not_a_crash(client, monkeypatch):
    """The pre-check-then-insert in register_user isn't atomic: two
    near-simultaneous requests for the same username could both pass the
    "is this taken" check before either commits. Simulate that race
    window directly by forcing the pre-check to report "not taken" on a
    username that's already registered, and confirm the database's own
    unique constraint still results in a clean 409, not an unhandled
    IntegrityError."""
    from app.services import auth_service

    resp = client.post(
        "/auth/register", json={"username": "racer_01", "password": "password123", "role": "RESEARCHER"}
    )
    assert resp.status_code == 201

    monkeypatch.setattr(auth_service, "_username_taken", lambda db, username: False)

    resp = client.post(
        "/auth/register", json={"username": "racer_01", "password": "password123", "role": "RESEARCHER"}
    )
    assert resp.status_code == 409, resp.text
    assert resp.json()["error_code"] == "username_taken"


# ---------------------------------------------------------------------------
# Duplicate scenario execution
# ---------------------------------------------------------------------------

def test_duplicate_scenario_execution_produces_independent_journeys(client, db_session):
    """Each demo scenario run creates a brand-new asset/grant/copy chain
    with fresh autoincrement ids -- there is nothing to look up or
    collide with. Running the same scenario twice in a row must succeed
    both times and never error, confirming that design holds under
    direct repetition (e.g. an impatient double-click on the same demo
    button)."""
    first = client.post("/demo/scenarios/legitimate")
    second = client.post("/demo/scenarios/legitimate")

    assert first.status_code == 200, first.text
    assert second.status_code == 200, second.text
    assert first.json()["asset_id"] != second.json()["asset_id"]
    assert first.json()["grant_id"] != second.json()["grant_id"]
    assert first.json()["decision"] == second.json()["decision"] == "ALLOW"


# ---------------------------------------------------------------------------
# Already-established idempotent/safe behaviors, pinned here as a single
# reliability-focused checklist (each already has dedicated coverage
# elsewhere -- test_grant_revocation.py, test_policy.py -- this just
# confirms the specific scenarios called out in the hardening review).
# ---------------------------------------------------------------------------

def test_repeated_revoke_of_same_grant_is_idempotent(client, existing_asset):
    grant = _create_grant(client, existing_asset.id)
    client.post("/auth/register", json={"username": "compliance_race", "password": "password123", "role": "COMPLIANCE_OFFICER"})
    login = client.post("/auth/login", json={"username": "compliance_race", "password": "password123"})
    headers = {"Authorization": f"Bearer {login.json()['access_token']}"}

    first = client.post(f"/grants/{grant['id']}/revoke", headers=headers)
    second = client.post(f"/grants/{grant['id']}/revoke", headers=headers)
    assert first.status_code == second.status_code == 200
    assert first.json()["status"] == second.json()["status"] == "REVOKED"


def test_empty_database_endpoints_return_empty_lists_not_errors(client):
    assert client.get("/grants").json() == []
    assert client.get("/assets").json() == []
    assert client.get("/assets", params={"state": "QUARANTINED"}).json() == []
