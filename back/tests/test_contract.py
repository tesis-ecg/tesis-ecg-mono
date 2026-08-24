from fastapi.testclient import TestClient

from app.main import app


def test_health_and_stub_routes_contract() -> None:
    """`admin` y `ecg_batches` siguen siendo stubs sin registrar.

    `alerts` salió de esta lista: dejó de ser un stub y ahora expone el listado
    y el acuse que consume la página de alertas del portal.
    """
    with TestClient(app) as client:
        assert client.get("/health").json() == {"status": "ok"}
        assert client.get("/health/live").json() == {"status": "ok"}
        assert client.get("/admin/").status_code == 404
        assert client.get("/ecg-batches/").status_code == 404


def test_alerts_routes_require_authentication() -> None:
    """El listado existe, y sin cookie no dice nada de nadie."""
    with TestClient(app) as client:
        assert client.get("/alerts").status_code == 401


def test_openapi_contains_compatible_and_manifest_ecg_endpoints() -> None:
    schema = app.openapi()
    paths = schema["paths"]

    assert "/auth/login" in paths
    assert "/auth/logout" in paths
    assert "/auth/me" in paths
    assert paths["/auth/register"]["post"]["deprecated"] is True
    assert "/studies/{study_id}/ecg" in paths
    assert "/studies/{study_id}/ecg/manifest" in paths


def test_openapi_publishes_the_ingest_endpoint_as_a_binary_body() -> None:
    """El chaleco manda `application/octet-stream`, no JSON.

    Si el contrato se publicara como JSON, el cliente generado del front
    intentaría serializar las tramas y el firmware no tendría cómo hablarle.
    """
    schema = app.openapi()

    operation = schema["paths"]["/ingest/ecg-frames"]["post"]
    content = operation["requestBody"]["content"]
    assert "application/octet-stream" in content
    assert content["application/octet-stream"]["schema"]["format"] == "binary"
    assert "202" in operation["responses"]


def test_openapi_publishes_the_api_key_rotation_endpoint() -> None:
    assert "/devices/{device_id}/api-key" in app.openapi()["paths"]


def test_ecg_manifest_schema_is_version_2_shaped() -> None:
    """`raw` nullable + `segments`: es lo que separa un estudio ingestado de uno
    seedeado, y el front discrimina por ahí."""
    schema = app.openapi()["components"]["schemas"]["StudyEcgManifestOut"]

    properties = schema["properties"]
    assert properties["formatVersion"]["default"] == 2
    assert "segments" in properties
    assert "isSimulated" in properties
    assert "status" in properties
    assert "anyOf" in properties["raw"], "raw tiene que poder ser null"


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
