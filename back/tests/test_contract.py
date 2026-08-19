from fastapi.testclient import TestClient

from app.main import app


def test_health_and_stub_routes_contract() -> None:
    with TestClient(app) as client:
        assert client.get("/health").json() == {"status": "ok"}
        assert client.get("/health/live").json() == {"status": "ok"}
        assert client.get("/admin/").status_code == 404
        assert client.get("/alerts/").status_code == 404
        assert client.get("/ecg-batches/").status_code == 404


def test_openapi_contains_compatible_and_manifest_ecg_endpoints() -> None:
    schema = app.openapi()
    paths = schema["paths"]

    assert "/auth/login" in paths
    assert "/auth/logout" in paths
    assert "/auth/me" in paths
    assert paths["/auth/register"]["post"]["deprecated"] is True
    assert "/studies/{study_id}/ecg" in paths
    assert "/studies/{study_id}/ecg/manifest" in paths


def test_validation_errors_use_stable_envelope() -> None:
    with TestClient(app) as client:
        response = client.post(
            "/auth/login",
            json={"email": "not-an-email", "password": "secret"},
            headers={"Origin": "http://localhost:5173"},
        )

    assert response.status_code == 422
    body = response.json()
    assert body["code"] == "VALIDATION"
    assert body["message"]
    assert body["fields"]
    assert body["requestId"]


def test_mutating_request_rejects_an_untrusted_origin() -> None:
    with TestClient(app) as client:
        response = client.post(
            "/auth/login",
            json={"email": "doctor@example.com", "password": "secret"},
            headers={"Origin": "https://attacker.example"},
        )

    assert response.status_code == 403
    assert response.json()["code"] == "ORIGIN_FORBIDDEN"
    assert response.headers["X-Request-ID"]
