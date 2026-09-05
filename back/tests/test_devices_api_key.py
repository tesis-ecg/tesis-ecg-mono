"""Lectura y rotación de la API key de un equipo.

La key habilita a subir señal como ese dispositivo: entregarla es equivalente a
entregar el equipo, así que las dos operaciones son solo de admin y las dos
quedan auditadas.
"""

import uuid

from sqlalchemy import select

from app.core.device_keys import hash_api_key
from app.db.models.audit_event import AuditEvent, AuditEventType
from app.db.models.user import UserRole
from tests.ingest_helpers import build_frames, post_frames


async def test_admin_can_rotate_and_gets_a_new_key(
    client, db, as_user, make_user, make_device
) -> None:
    device, _ = await make_device()
    as_user(await make_user(UserRole.ADMIN))

    first = await client.post(f"/devices/{device.id}/api-key")
    second = await client.post(f"/devices/{device.id}/api-key")

    assert first.status_code == 200
    body = first.json()
    assert body["deviceId"] == str(device.id)
    assert body["serial"] == device.serial_number
    assert len(body["apiKey"]) >= 32
    assert second.json()["apiKey"] != body["apiKey"]


async def test_the_key_is_never_stored_in_the_clear(
    client, db, as_user, make_user, make_device
) -> None:
    device, _ = await make_device()
    as_user(await make_user(UserRole.ADMIN))

    api_key = (await client.post(f"/devices/{device.id}/api-key")).json()["apiKey"]

    await db.refresh(device)
    assert device.api_key_hash == hash_api_key(api_key)
    # La copia legible existe, pero cifrada: un dump de la base no la revela.
    assert device.api_key_encrypted is not None
    assert api_key not in device.api_key_encrypted


async def test_admin_can_read_back_the_key_of_an_existing_device(
    client, as_user, make_user, make_device
) -> None:
    device, api_key = await make_device()
    as_user(await make_user(UserRole.ADMIN))

    response = await client.get(f"/devices/{device.id}/api-key")

    assert response.status_code == 200
    body = response.json()
    assert body["apiKey"] == api_key
    assert body["deviceId"] == str(device.id)
    assert body["serial"] == device.serial_number
    # La key es una credencial reutilizable: ni el navegador ni un proxy deben
    # guardar la respuesta. El middleware lo impone para toda la API.
    assert response.headers["Cache-Control"] == "private, no-store"


async def test_reading_the_key_after_rotating_returns_the_new_one(
    client, as_user, make_user, make_device
) -> None:
    device, old_key = await make_device()
    as_user(await make_user(UserRole.ADMIN))

    rotated = (await client.post(f"/devices/{device.id}/api-key")).json()["apiKey"]
    read_back = (await client.get(f"/devices/{device.id}/api-key")).json()["apiKey"]

    assert read_back == rotated
    assert read_back != old_key


async def test_the_key_read_back_authenticates_an_ingest(
    client, s3, as_user, make_user, make_patient, make_device
) -> None:
    """Lo que ata las dos copias: la que se lee es la que valida la ingesta.

    En la base la key vive hasheada (contra eso valida el chaleco) y cifrada
    (eso es lo que devuelve el GET). Si las dos se desincronizaran, el admin
    grabaría en el firmware una credencial que el backend rechaza.
    """
    patient = await make_patient()
    device, _ = await make_device(patient=patient)
    as_user(await make_user(UserRole.ADMIN))
    api_key = (await client.get(f"/devices/{device.id}/api-key")).json()["apiKey"]

    response = await post_frames(client, device, api_key, build_frames(900))

    assert response.status_code == 202


async def test_a_key_that_cannot_be_decrypted_asks_for_a_rotation(
    client, db, as_user, make_user, make_device
) -> None:
    """Un equipo dado de alta antes de que la key se guardara cifrada."""
    device, _ = await make_device()
    device.api_key_encrypted = None
    await db.flush()
    as_user(await make_user(UserRole.ADMIN))

    response = await client.get(f"/devices/{device.id}/api-key")

    assert response.status_code == 409
    assert response.json()["code"] == "API_KEY_UNAVAILABLE"


async def test_a_doctor_cannot_read_the_key_even_owning_the_device(
    client, db, as_user, make_user, make_doctor, make_patient, make_device
) -> None:
    doctor = await make_doctor()
    patient = await make_patient(doctor)
    device, _ = await make_device(patient=patient, doctor=doctor)
    owner_user = await db.get(type(await make_user(UserRole.MEDICO)), doctor.user_id)
    assert owner_user is not None
    as_user(owner_user)

    response = await client.get(f"/devices/{device.id}/api-key")

    assert response.status_code == 403


async def test_reading_the_key_is_audited(client, db, as_user, make_user, make_device) -> None:
    device, _ = await make_device()
    admin = await make_user(UserRole.ADMIN)
    as_user(admin)

    await client.get(f"/devices/{device.id}/api-key")

    events = list(
        (
            await db.scalars(
                select(AuditEvent).where(
                    AuditEvent.event_type == AuditEventType.DEVICE_API_KEY_VIEWED
                )
            )
        ).all()
    )
    assert len(events) == 1
    assert events[0].user_id == admin.id
    assert events[0].event_metadata["target_device_id"] == str(device.id)
    # La key en claro NUNCA se guarda, tampoco en la auditoría.
    assert "apiKey" not in str(events[0].event_metadata)


async def test_reading_the_key_of_an_unknown_device_is_404(client, as_user, make_user) -> None:
    as_user(await make_user(UserRole.ADMIN))

    response = await client.get(f"/devices/{uuid.uuid4()}/api-key")

    assert response.status_code == 404


async def test_the_new_key_works_for_ingest(
    client, s3, db, as_user, make_user, make_patient, make_device
) -> None:
    patient = await make_patient()
    device, _ = await make_device(patient=patient)
    as_user(await make_user(UserRole.ADMIN))
    new_key = (await client.post(f"/devices/{device.id}/api-key")).json()["apiKey"]

    response = await post_frames(client, device, new_key, build_frames(900))

    assert response.status_code == 202


async def test_the_old_key_stops_working_immediately(
    client, s3, db, as_user, make_user, make_patient, make_device
) -> None:
    patient = await make_patient()
    device, old_key = await make_device(patient=patient)
    as_user(await make_user(UserRole.ADMIN))
    await client.post(f"/devices/{device.id}/api-key")

    response = await post_frames(client, device, old_key, build_frames(900))

    assert response.status_code == 401


async def test_a_doctor_cannot_rotate_even_owning_the_device(
    client, db, as_user, make_user, make_doctor, make_patient, make_device
) -> None:
    doctor = await make_doctor()
    patient = await make_patient(doctor)
    device, _ = await make_device(patient=patient, doctor=doctor)
    owner_user = await db.get(type(await make_user(UserRole.MEDICO)), doctor.user_id)
    assert owner_user is not None
    as_user(owner_user)

    response = await client.post(f"/devices/{device.id}/api-key")

    assert response.status_code == 403


async def test_an_unauthenticated_request_is_rejected(client, make_device) -> None:
    device, _ = await make_device()

    response = await client.post(f"/devices/{device.id}/api-key")

    assert response.status_code == 401


async def test_rotation_is_audited(client, db, as_user, make_user, make_device) -> None:
    device, _ = await make_device()
    admin = await make_user(UserRole.ADMIN)
    as_user(admin)

    await client.post(f"/devices/{device.id}/api-key")

    events = list(
        (
            await db.scalars(
                select(AuditEvent).where(
                    AuditEvent.event_type == AuditEventType.DEVICE_API_KEY_ROTATED
                )
            )
        ).all()
    )
    assert len(events) == 1
    assert events[0].user_id == admin.id
    assert events[0].event_metadata["target_device_id"] == str(device.id)
    # La key en claro NUNCA se guarda, tampoco en la auditoría.
    assert "apiKey" not in str(events[0].event_metadata)


async def test_rotating_an_unknown_device_is_404(client, as_user, make_user) -> None:
    as_user(await make_user(UserRole.ADMIN))

    response = await client.post(f"/devices/{uuid.uuid4()}/api-key")

    assert response.status_code == 404


async def test_keys_have_enough_entropy(client, as_user, make_user, make_device) -> None:
    device, _ = await make_device()
    as_user(await make_user(UserRole.ADMIN))

    keys = {(await client.post(f"/devices/{device.id}/api-key")).json()["apiKey"] for _ in range(5)}

    assert len(keys) == 5
    assert all(len(k) >= 32 for k in keys)
