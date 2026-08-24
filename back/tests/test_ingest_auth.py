"""Autenticación del chaleco.

El endpoint está expuesto a internet y no lo protege ninguna cookie: lo único
que separa a un equipo real de cualquiera es el par serial + API key.
"""

import hashlib
import secrets

from app.db.models.device import DeviceStatus
from tests.ingest_helpers import INGEST_URL, build_frames, device_headers, post_frames


async def test_valid_credentials_are_accepted(client, s3, make_patient, make_device) -> None:
    patient = await make_patient()
    device, api_key = await make_device(patient=patient)

    response = await post_frames(client, device, api_key, build_frames(300))

    assert response.status_code == 202, response.text


async def test_rejects_a_wrong_api_key(client, s3, make_patient, make_device) -> None:
    patient = await make_patient()
    device, _ = await make_device(patient=patient)

    response = await post_frames(client, device, secrets.token_urlsafe(32), build_frames(300))

    assert response.status_code == 401
    assert response.json()["code"] == "DEVICE_UNAUTHORIZED"


async def test_rejects_a_missing_authorization_header(
    client, s3, make_patient, make_device
) -> None:
    patient = await make_patient()
    device, api_key = await make_device(patient=patient)
    headers = device_headers(device, api_key)
    del headers["Authorization"]

    response = await client.post(INGEST_URL, content=b"".join(build_frames(300)), headers=headers)

    assert response.status_code == 401


async def test_rejects_a_non_bearer_scheme(client, s3, make_patient, make_device) -> None:
    patient = await make_patient()
    device, api_key = await make_device(patient=patient)
    headers = device_headers(device, api_key) | {"Authorization": f"Basic {api_key}"}

    response = await client.post(INGEST_URL, content=b"".join(build_frames(300)), headers=headers)

    assert response.status_code == 401


async def test_unknown_serial_looks_exactly_like_a_bad_key(
    client, s3, make_patient, make_device
) -> None:
    """No se puede enumerar qué seriales existen.

    Si un serial inexistente devolviera 404 y una key mala 401, cualquiera
    podría barrer el espacio de seriales contra un endpoint público. La
    distinción queda en el log del servidor, que es donde la necesita un
    operador.
    """
    patient = await make_patient()
    device, api_key = await make_device(patient=patient)

    unknown = await client.post(
        INGEST_URL,
        content=b"".join(build_frames(300)),
        headers=device_headers(device, api_key) | {"X-Device-Serial": "HOL-NO-EXISTE"},
    )
    bad_key = await post_frames(client, device, secrets.token_urlsafe(32), build_frames(300))

    assert unknown.status_code == bad_key.status_code == 401
    assert unknown.json()["code"] == bad_key.json()["code"]
    assert unknown.json()["message"] == bad_key.json()["message"]


async def test_rejects_a_key_that_belongs_to_another_device(
    client, s3, make_patient, make_device
) -> None:
    patient_a = await make_patient()
    patient_b = await make_patient()
    device_a, _ = await make_device(patient=patient_a)
    _, key_b = await make_device(patient=patient_b)

    response = await post_frames(client, device_a, key_b, build_frames(300))

    assert response.status_code == 401


async def test_rejects_a_retired_device(client, s3, make_patient, make_device) -> None:
    device, api_key = await make_device(status=DeviceStatus.RETIRED)

    response = await post_frames(client, device, api_key, build_frames(300))

    assert response.status_code == 409
    assert response.json()["code"] == "DEVICE_NOT_INGESTABLE"


async def test_rejects_a_device_in_maintenance(client, s3, make_device) -> None:
    device, api_key = await make_device(status=DeviceStatus.MAINTENANCE)

    response = await post_frames(client, device, api_key, build_frames(300))

    assert response.status_code == 409


async def test_rejects_a_soft_deleted_device(client, s3, db, make_patient, make_device) -> None:
    from datetime import UTC, datetime

    patient = await make_patient()
    device, api_key = await make_device(patient=patient)
    device.deleted_at = datetime.now(UTC)
    await db.flush()

    response = await post_frames(client, device, api_key, build_frames(300))

    assert response.status_code == 401


async def test_requires_the_uptime_header(client, s3, make_patient, make_device) -> None:
    """Sin uptime no hay ancla temporal, y sin ancla la señal no tiene hora."""
    patient = await make_patient()
    device, api_key = await make_device(patient=patient)
    headers = device_headers(device, api_key)
    del headers["X-Device-Uptime-Ms"]

    response = await client.post(INGEST_URL, content=b"".join(build_frames(300)), headers=headers)

    assert response.status_code == 422
    assert response.json()["code"] == "DEVICE_UPTIME_REQUIRED"


async def test_rejects_an_out_of_range_battery(client, s3, make_patient, make_device) -> None:
    patient = await make_patient()
    device, api_key = await make_device(patient=patient)

    response = await post_frames(client, device, api_key, build_frames(300), battery=140)

    assert response.status_code == 422
    assert response.json()["code"] == "DEVICE_BATTERY_INVALID"


async def test_api_key_is_never_stored_in_the_clear(make_patient, make_device) -> None:
    patient = await make_patient()
    device, api_key = await make_device(patient=patient)

    assert device.api_key_hash != api_key
    assert device.api_key_hash == hashlib.sha256(api_key.encode()).hexdigest()


async def test_session_cookie_is_not_a_valid_device_credential(
    client, s3, as_user, make_user, make_patient, make_device
) -> None:
    """Un médico logueado no puede ingestar señal: son credenciales distintas."""
    from app.db.models.user import UserRole

    patient = await make_patient()
    device, api_key = await make_device(patient=patient)
    as_user(await make_user(UserRole.ADMIN))

    headers = device_headers(device, api_key)
    del headers["Authorization"]
    response = await client.post(INGEST_URL, content=b"".join(build_frames(300)), headers=headers)

    assert response.status_code == 401
