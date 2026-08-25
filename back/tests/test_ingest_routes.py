"""Contrato HTTP de `/ingest/ecg-frames`.

El corazón de esto es la semántica del ACK go-back-N: `framesAccepted` es un
delta contiguo, y equivocarse ahí no rompe nada de forma visible — simplemente
hace que el equipo borre de su flash señal que el servidor nunca guardó.
"""

import uuid

import pytest

from app.db.models.device import DeviceStatus
from tests.frame_builder import corrupt_crc
from tests.ingest_helpers import INGEST_URL, build_frames, device_headers, post_frames


async def test_happy_path_accepts_every_frame(
    client, s3, make_patient, make_device, scheduled_batches
) -> None:
    patient = await make_patient()
    device, api_key = await make_device(patient=patient)
    frames = build_frames(1200)

    response = await post_frames(client, device, api_key, frames)

    assert response.status_code == 202
    body = response.json()
    assert body["framesReceived"] == len(frames)
    assert body["framesAccepted"] == len(frames)
    assert body["framesRejected"] == 0
    assert body["framesDuplicate"] == 0
    assert body["lastAcceptedSeq"] == len(frames) - 1
    assert body["batchId"] is not None
    assert body["studyId"] is not None
    assert [str(b) for b in scheduled_batches] == [body["batchId"]]


async def test_status_is_202_because_the_bytes_are_stored_not_processed(
    client, s3, make_patient, make_device
) -> None:
    patient = await make_patient()
    device, api_key = await make_device(patient=patient)

    response = await post_frames(client, device, api_key, build_frames(300))

    assert response.status_code == 202


# --------------------------------------------------------------------------- #
# Ventana de ACK: la regla que no se puede romper
# --------------------------------------------------------------------------- #


async def test_ack_stops_at_the_first_gap(client, s3, make_patient, make_device) -> None:
    """Llegan la 10, la 11 y la 13 → se confirman 2, no 3.

    La 12 falta y el cursor de lectura del equipo no la puede saltear.
    """
    patient = await make_patient()
    device, api_key = await make_device(patient=patient)
    frames = build_frames(1600, first_seq=10)
    assert len(frames) >= 4
    with_gap = [frames[0], frames[1], frames[3]]

    body = (await post_frames(client, device, api_key, with_gap)).json()

    assert body["framesAccepted"] == 2
    assert body["lastAcceptedSeq"] == 11


async def test_ack_is_zero_when_the_gap_is_at_the_start(
    client, s3, make_patient, make_device
) -> None:
    patient = await make_patient()
    device, api_key = await make_device(patient=patient)
    frames = build_frames(2000, first_seq=0)

    first = (await post_frames(client, device, api_key, frames[:2])).json()
    assert first["framesAccepted"] == 2

    # Se saltea la trama 2 y se manda de la 3 en adelante.
    second = (await post_frames(client, device, api_key, frames[3:5])).json()

    assert second["framesAccepted"] == 0
    assert second["batchId"] is None
    assert second["lastAcceptedSeq"] == 1


async def test_frames_out_of_order_within_a_body_are_not_a_gap(
    client, s3, make_patient, make_device
) -> None:
    """El equipo puede retransmitir desordenado después de un corte."""
    patient = await make_patient()
    device, api_key = await make_device(patient=patient)
    frames = build_frames(1600)
    shuffled = [frames[2], frames[0], frames[3], frames[1]]

    body = (await post_frames(client, device, api_key, shuffled)).json()

    assert body["framesAccepted"] == 4


async def test_exact_duplicates_inside_one_body_are_collapsed(
    client, s3, make_patient, make_device
) -> None:
    patient = await make_patient()
    device, api_key = await make_device(patient=patient)
    frames = build_frames(900)

    body = (await post_frames(client, device, api_key, frames + frames)).json()

    assert body["framesReceived"] == len(frames) * 2
    assert body["framesAccepted"] == len(frames)


async def test_retransmitting_a_whole_batch_is_idempotent(
    client, s3, db, make_patient, make_device
) -> None:
    """Si nuestra respuesta se perdió, el equipo reintenta el mismo lote.

    Hay que volver a confirmarlo. Contestar 0 dejaría al equipo reintentando
    para siempre — es la diferencia entre el ACK sobre HTTP y el del canal BLE,
    donde un duplicado no se re-confirma.
    """
    from sqlalchemy import func, select

    from app.db.models.ecg_batch import ECGBatch

    patient = await make_patient()
    device, api_key = await make_device(patient=patient)
    frames = build_frames(900)

    first = (await post_frames(client, device, api_key, frames)).json()
    second = (await post_frames(client, device, api_key, frames)).json()

    assert first["framesAccepted"] == len(frames)
    assert second["framesAccepted"] == len(frames)
    assert second["framesDuplicate"] == len(frames)
    assert second["batchId"] is None
    assert second["studyId"] == first["studyId"]

    batches = await db.scalar(select(func.count()).select_from(ECGBatch))
    assert batches == 1


async def test_a_later_batch_continues_from_the_cursor(
    client, s3, make_patient, make_device
) -> None:
    patient = await make_patient()
    device, api_key = await make_device(patient=patient)
    frames = build_frames(2400)
    half = len(frames) // 2

    first = (await post_frames(client, device, api_key, frames[:half])).json()
    second = (await post_frames(client, device, api_key, frames[half:])).json()

    assert second["studyId"] == first["studyId"]
    assert second["framesAccepted"] == len(frames) - half
    assert second["lastAcceptedSeq"] == len(frames) - 1


async def test_a_batch_already_covered_is_not_re_accepted_under_another_boot_id(
    client, s3, db, make_patient, make_device
) -> None:
    """Un `bootId` nuevo no habilita a reescribir señal ya archivada.

    Los objetos del estudio se nombran con el `first_seq` del lote
    (`frames_key`, `segment_key`), así que volver a aceptar un rango ya cubierto
    sobreescribía los bytes en S3 mientras `samples_count` seguía creciendo: el
    estudio perdía muestras en silencio.
    """
    from sqlalchemy import func, select

    from app.core.s3 import get_object
    from app.db.models.ecg_batch import ECGBatch
    from app.modules.ingest.ingest_service import frames_key

    patient = await make_patient()
    device, api_key = await make_device(patient=patient)
    frames = build_frames(1800, boot_id=2, first_seq=0)

    first = (await post_frames(client, device, api_key, frames)).json()
    key = frames_key(uuid.UUID(first["studyId"]), 0)
    stored = get_object(key)

    replay = build_frames(1800, boot_id=5, first_seq=0, t0_ms=0)
    second = (await post_frames(client, device, api_key, replay)).json()

    assert second["batchId"] is None
    assert second["framesDuplicate"] == len(replay)
    assert second["lastAcceptedSeq"] == first["lastAcceptedSeq"]
    assert get_object(key) == stored
    assert await db.scalar(select(func.count()).select_from(ECGBatch)) == 1


# --------------------------------------------------------------------------- #
# Tramas inválidas
# --------------------------------------------------------------------------- #


async def test_corrupt_frames_are_counted_and_valid_ones_still_land(
    client, s3, make_patient, make_device
) -> None:
    patient = await make_patient()
    device, api_key = await make_device(patient=patient)
    frames = build_frames(1600)
    mixed = [frames[0], corrupt_crc(frames[1]), frames[2]]

    body = (await post_frames(client, device, api_key, mixed)).json()

    assert body["framesRejected"] == 1
    # La 1 se descartó, así que la 2 queda del otro lado del hueco.
    assert body["framesAccepted"] == 1
    assert body["lastAcceptedSeq"] == 0


async def test_a_body_with_only_corrupt_frames_is_rejected(
    client, s3, make_patient, make_device
) -> None:
    patient = await make_patient()
    device, api_key = await make_device(patient=patient)
    frames = [corrupt_crc(f) for f in build_frames(900)]

    response = await post_frames(client, device, api_key, frames)

    assert response.status_code == 422
    assert response.json()["code"] == "INGEST_NO_VALID_FRAMES"


async def test_body_that_is_not_a_multiple_of_256_is_rejected(
    client, s3, make_patient, make_device
) -> None:
    patient = await make_patient()
    device, api_key = await make_device(patient=patient)

    response = await client.post(
        INGEST_URL,
        content=b"\x00" * 300,
        headers=device_headers(device, api_key),
    )

    assert response.status_code == 422
    assert response.json()["code"] == "INGEST_BAD_BODY"


async def test_empty_body_is_rejected(client, s3, make_patient, make_device) -> None:
    patient = await make_patient()
    device, api_key = await make_device(patient=patient)

    response = await client.post(INGEST_URL, content=b"", headers=device_headers(device, api_key))

    assert response.status_code == 422


async def test_body_over_the_limit_is_rejected(
    client, s3, make_patient, make_device, monkeypatch
) -> None:
    from app.core.config import settings

    monkeypatch.setattr(settings, "ingest_max_batch_bytes", 512)
    patient = await make_patient()
    device, api_key = await make_device(patient=patient)

    response = await post_frames(client, device, api_key, build_frames(2000))

    assert response.status_code == 413
    assert response.json()["code"] == "INGEST_BATCH_TOO_LARGE"


# --------------------------------------------------------------------------- #
# Estado del dispositivo
# --------------------------------------------------------------------------- #


async def test_device_without_a_patient_is_rejected(client, s3, make_device) -> None:
    device, api_key = await make_device(status=DeviceStatus.AVAILABLE)

    response = await post_frames(client, device, api_key, build_frames(900))

    assert response.status_code == 409
    assert response.json()["code"] == "DEVICE_UNASSIGNED"


async def test_device_whose_patient_was_soft_deleted_is_rejected(
    client, s3, db, make_patient, make_device
) -> None:
    from datetime import UTC, datetime

    patient = await make_patient()
    device, api_key = await make_device(patient=patient)
    patient.deleted_at = datetime.now(UTC)
    await db.flush()

    response = await post_frames(client, device, api_key, build_frames(900))

    assert response.status_code == 409
    assert response.json()["code"] == "PATIENT_NOT_FOUND"


async def test_telemetry_headers_update_the_device(
    client, s3, db, make_patient, make_device
) -> None:
    patient = await make_patient()
    device, api_key = await make_device(patient=patient)

    await post_frames(client, device, api_key, build_frames(900), battery=42, firmware="9.9.9")
    await db.refresh(device)

    assert device.last_battery_pct == 42
    assert device.firmware_version == "9.9.9"
    assert device.last_seen_at is not None


# --------------------------------------------------------------------------- #
# Origin: la excepción es deliberada y acotada
# --------------------------------------------------------------------------- #


async def test_ingest_accepts_a_post_without_an_origin_header(
    client, s3, make_patient, make_device
) -> None:
    """El co-procesador WiFi del chaleco es un cliente HTTP, no un navegador."""
    patient = await make_patient()
    device, api_key = await make_device(patient=patient)

    response = await post_frames(client, device, api_key, build_frames(900))

    assert response.status_code == 202


async def test_ingest_accepts_a_hostile_origin_because_no_cookie_is_involved(
    client, s3, make_patient, make_device
) -> None:
    """Intencional, no un descuido.

    El chequeo de Origin protege contra CSRF, que necesita una credencial
    ambiental. `/ingest` no lee ninguna cookie: se autentica con un bearer que
    un sitio hostil no tiene. Un Origin raro no le sirve de nada a nadie.
    """
    patient = await make_patient()
    device, api_key = await make_device(patient=patient)

    response = await client.post(
        INGEST_URL,
        content=b"".join(build_frames(900)),
        headers=device_headers(device, api_key) | {"Origin": "https://attacker.example"},
    )

    assert response.status_code == 202


async def test_the_origin_check_still_applies_everywhere_else(client) -> None:
    """La excepción es solo para `/ingest`; el resto de la API no se toca."""
    response = await client.post(
        "/auth/login",
        json={"email": "doctor@example.com", "password": "secret"},
        headers={"Origin": "https://attacker.example"},
    )

    assert response.status_code == 403
    assert response.json()["code"] == "ORIGIN_FORBIDDEN"


# --------------------------------------------------------------------------- #
# Envelope y headers comunes
# --------------------------------------------------------------------------- #


async def test_response_carries_the_standard_headers(client, s3, make_patient, make_device) -> None:
    patient = await make_patient()
    device, api_key = await make_device(patient=patient)

    response = await post_frames(client, device, api_key, build_frames(900))

    assert response.headers["X-Request-ID"]
    assert response.headers["Cache-Control"] == "private, no-store"


@pytest.mark.parametrize("field", ["code", "message", "requestId"])
async def test_errors_use_the_stable_envelope(client, s3, make_device, field: str) -> None:
    device, api_key = await make_device(status=DeviceStatus.AVAILABLE)

    response = await post_frames(client, device, api_key, build_frames(900))

    assert response.status_code == 409
    assert field in response.json()
