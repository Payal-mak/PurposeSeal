from app.db.session import SessionLocal


def test_app_boots_and_exposes_openapi_schema(client):
    resp = client.get("/openapi.json")
    assert resp.status_code == 200


def test_health_returns_200_with_expected_shape(client):
    resp = client.get("/health")
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "ok"
    assert "app_name" in body


def test_database_session_can_be_created_and_closed():
    session = SessionLocal()
    try:
        assert session.is_active
    finally:
        session.close()


def test_unknown_route_returns_404(client):
    resp = client.get("/this-route-does-not-exist")
    assert resp.status_code == 404
