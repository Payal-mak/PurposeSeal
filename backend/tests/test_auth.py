from app.models import AuditLog


def _register(client, username, password="password123", role="RESEARCHER"):
    resp = client.post("/auth/register", json={"username": username, "password": password, "role": role})
    assert resp.status_code == 201, resp.text
    return resp.json()


def _login(client, username, password="password123"):
    return client.post("/auth/login", json={"username": username, "password": password})


def _token_for(client, username, password="password123", role="RESEARCHER"):
    _register(client, username, password=password, role=role)
    resp = _login(client, username, password=password)
    assert resp.status_code == 200, resp.text
    return resp.json()["access_token"]


def _auth_header(token):
    return {"Authorization": f"Bearer {token}"}


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


# ---------------------------------------------------------------------------
# Registration and login
# ---------------------------------------------------------------------------

def test_register_and_login_returns_valid_token(client):
    _register(client, "researcher_01", role="RESEARCHER")

    resp = _login(client, "researcher_01")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["access_token"]
    assert body["token_type"] == "bearer"
    assert body["username"] == "researcher_01"
    assert body["role"] == "RESEARCHER"


def test_wrong_credentials_rejected(client):
    _register(client, "researcher_01", password="correct-password")

    resp = _login(client, "researcher_01", password="wrong-password")
    assert resp.status_code == 401
    assert resp.json()["error_code"] == "invalid_credentials"


def test_login_with_nonexistent_username_rejected(client):
    resp = _login(client, "nobody", password="whatever123")
    assert resp.status_code == 401
    assert resp.json()["error_code"] == "invalid_credentials"


def test_duplicate_username_rejected(client):
    _register(client, "researcher_01")

    resp = client.post(
        "/auth/register", json={"username": "researcher_01", "password": "password123", "role": "ANALYST"}
    )
    assert resp.status_code == 409
    assert resp.json()["error_code"] == "username_taken"


def test_get_me_returns_current_authenticated_user(client):
    token = _token_for(client, "researcher_01", role="RESEARCHER")

    resp = client.get("/auth/me", headers=_auth_header(token))
    assert resp.status_code == 200
    body = resp.json()
    assert body["username"] == "researcher_01"
    assert body["role"] == "RESEARCHER"


def test_get_me_without_token_returns_401(client):
    resp = client.get("/auth/me")
    assert resp.status_code == 401
    assert resp.json()["error_code"] == "not_authenticated"


def test_invalid_token_rejected(client):
    resp = client.get("/auth/me", headers=_auth_header("not-a-real-token"))
    assert resp.status_code == 401
    assert resp.json()["error_code"] == "invalid_token"


# ---------------------------------------------------------------------------
# Required test 1: unauthenticated protected request
# ---------------------------------------------------------------------------

def test_unauthenticated_protected_request_rejected(client, existing_asset):
    """POST /grants/{id}/revoke requires authentication outright."""
    grant = _create_grant(client, existing_asset.id)

    resp = client.post(f"/grants/{grant['id']}/revoke")
    assert resp.status_code == 401
    assert resp.json()["error_code"] == "not_authenticated"


# ---------------------------------------------------------------------------
# Required test 4: role-restricted operation
# ---------------------------------------------------------------------------

def test_role_restricted_operation_denies_wrong_role_allows_right_role(client, existing_asset):
    grant = _create_grant(client, existing_asset.id)

    researcher_token = _token_for(client, "researcher_01", role="RESEARCHER")
    denied = client.post(f"/grants/{grant['id']}/revoke", headers=_auth_header(researcher_token))
    assert denied.status_code == 403
    assert denied.json()["error_code"] == "role_not_permitted"

    compliance_token = _token_for(client, "compliance_01", role="COMPLIANCE_OFFICER")
    allowed = client.post(f"/grants/{grant['id']}/revoke", headers=_auth_header(compliance_token))
    assert allowed.status_code == 200
    assert allowed.json()["status"] == "REVOKED"


def test_admin_role_can_also_revoke(client, existing_asset):
    grant = _create_grant(client, existing_asset.id)
    admin_token = _token_for(client, "admin_01", role="ADMIN")

    resp = client.post(f"/grants/{grant['id']}/revoke", headers=_auth_header(admin_token))
    assert resp.status_code == 200
    assert resp.json()["status"] == "REVOKED"


def test_analyst_and_clinician_roles_also_denied_revocation(client, existing_asset):
    grant = _create_grant(client, existing_asset.id)

    for role in ("ANALYST", "CLINICIAN"):
        token = _token_for(client, f"user_{role.lower()}", role=role)
        resp = client.post(f"/grants/{grant['id']}/revoke", headers=_auth_header(token))
        assert resp.status_code == 403, f"role {role} should not be permitted to revoke"


# ---------------------------------------------------------------------------
# Required test 5: authenticated actor still denied by purpose policy
# ---------------------------------------------------------------------------

def test_authenticated_actor_still_denied_by_purpose_policy(client, existing_asset):
    """Successfully authenticating as researcher_01 (valid credentials,
    valid token) must not bypass the policy engine's independent
    purpose check -- authentication and purpose authorization are
    separate concerns."""
    token = _token_for(client, "researcher_01", role="RESEARCHER")

    grant = _create_grant(client, existing_asset.id, subject="researcher_01")
    retrieval = client.post(
        "/retrievals",
        json={"grant_id": grant["id"], "actor": "researcher_01", "asset_id": existing_asset.id, "operation": "VIEW"},
        headers=_auth_header(token),
    )
    assert retrieval.status_code == 201, retrieval.text
    retrieved = retrieval.json()

    resp = client.post(
        "/uses",
        json={
            "asset_id": retrieved["id"],
            "actor": "researcher_01",
            "purpose": "marketing_analytics",  # wrong purpose
            "operation": "ANALYZE",
        },
        headers=_auth_header(token),
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["decision"] == "DENY"
    assert body["reason_code"] == "PURPOSE_MISMATCH"


# ---------------------------------------------------------------------------
# Required test 6: identity cannot be spoofed through the request body
# ---------------------------------------------------------------------------

def test_identity_cannot_be_spoofed_through_request_body(client, existing_asset):
    """A verified Bearer identity must win over any `actor` claimed in
    the request body. Log in as researcher_01, then claim to be
    "mallory" in the body -- the retrieval must be evaluated as
    researcher_01 (matching the grant's subject), not mallory (who does
    not match and would otherwise be denied)."""
    token = _token_for(client, "researcher_01", role="RESEARCHER")
    grant = _create_grant(client, existing_asset.id, subject="researcher_01")

    resp = client.post(
        "/retrievals",
        json={"grant_id": grant["id"], "actor": "mallory", "asset_id": existing_asset.id, "operation": "VIEW"},
        headers=_auth_header(token),
    )

    # If the spoofed "mallory" identity had been used, this would be a
    # 403 actor_mismatch. Success proves the authenticated identity
    # (researcher_01) was used instead.
    assert resp.status_code == 201, resp.text


def test_spoofing_attempt_recorded_under_the_real_identity(client, existing_asset, db_session):
    """The audit trail itself must reflect the authenticated identity,
    not the spoofed body value, so the trail can't be forged either."""
    token = _token_for(client, "researcher_01", role="RESEARCHER")
    grant = _create_grant(client, existing_asset.id, subject="researcher_01")

    resp = client.post(
        "/retrievals",
        json={"grant_id": grant["id"], "actor": "mallory", "asset_id": existing_asset.id, "operation": "VIEW"},
        headers=_auth_header(token),
    )
    assert resp.status_code == 201
    retrieved_id = resp.json()["id"]

    event = db_session.query(AuditLog).filter(
        AuditLog.entity_id == str(retrieved_id), AuditLog.event_type == "DATA_RETRIEVED"
    ).one()
    assert '"actor": "researcher_01"' in event.details
    assert "mallory" not in event.details


def test_unauthenticated_request_unaffected_actor_still_from_body(client, existing_asset):
    """Backward compatibility: with no Authorization header at all,
    behavior is completely unchanged from before this feature --
    `actor` still comes from the request body."""
    grant = _create_grant(client, existing_asset.id, subject="researcher_01")

    resp = client.post(
        "/retrievals",
        json={"grant_id": grant["id"], "actor": "researcher_01", "asset_id": existing_asset.id, "operation": "VIEW"},
    )
    assert resp.status_code == 201, resp.text
