"""Manifest v2: estudios ingestados y estudios legacy conviven.

El visor consume `levels` en los dos casos; la diferencia está en dónde vive la
señal completa (`raw` para los seedeados, `segments` para los ingestados).
"""

import hashlib
import uuid

from app.core.s3 import put_object
from app.db.models.audit_event import AuditEvent, AuditEventType
from app.db.models.user import UserRole
from app.modules.ingest.processing import process_batch
from tests.ingest_helpers import build_frames, post_frames


async def _manifest(client, study_id) -> dict:
    return (await client.get(f"/studies/{study_id}/ecg/manifest")).json()


async def _ingested_study(client, db, make_patient, make_device, samples: int = 4000):
    patient = await make_patient()
    device, api_key = await make_device(patient=patient)
    body = (await post_frames(client, device, api_key, build_frames(samples))).json()
    await process_batch(db, body["batchId"])
    return patient, body["studyId"]


async def test_an_ingested_study_exposes_segments_and_no_raw(
    client, s3, db, as_user, make_user, make_patient, make_device
) -> None:
    _, study_id = await _ingested_study(client, db, make_patient, make_device)
    as_user(await make_user(UserRole.ADMIN))

    response = await client.get(f"/studies/{study_id}/ecg/manifest")

    assert response.status_code == 200, response.text
    manifest = response.json()
    assert manifest["formatVersion"] == 2
    assert manifest["raw"] is None
    assert manifest["segments"], "un estudio ingestado tiene que traer segmentos"
    assert manifest["levels"], "y niveles de pirámide para el visor"
    assert manifest["sampleCount"] == 4000
    assert manifest["status"] == "in_progress"
    assert manifest["isSimulated"] is True


def _key_from_url(url: str) -> str:
    """Clave de S3 a partir de la URL prefirmada.

    Los objetos se leen con el cliente de S3 y no bajando la URL con httpx:
    `moto` intercepta boto3, no HTTP arbitrario, así que un GET a la URL
    prefirmada se iría a AWS de verdad. Un test que sale a la red no es un test.
    """
    from urllib.parse import unquote, urlparse

    from app.core.config import settings

    path = unquote(urlparse(url).path).lstrip("/")
    prefix = f"{settings.s3_bucket_name}/"
    return path[len(prefix) :] if path.startswith(prefix) else path


async def test_segment_metadata_matches_the_stored_objects(
    client, s3, db, as_user, make_user, make_patient, make_device
) -> None:
    from app.core.s3 import get_object

    _, study_id = await _ingested_study(client, db, make_patient, make_device)
    as_user(await make_user(UserRole.ADMIN))

    manifest = await _manifest(client, study_id)
    offset = 0
    for segment in manifest["segments"]:
        assert segment["startSampleIndex"] == offset
        offset += segment["sampleCount"]
        payload = get_object(_key_from_url(segment["url"]))
        assert len(payload) == segment["byteLength"]
        assert hashlib.sha256(payload).hexdigest() == segment["sha256"]
        assert segment["sampleCount"] * 4 == segment["byteLength"]
    assert offset == manifest["sampleCount"]


async def test_a_legacy_seeded_study_still_returns_raw(
    client, s3, db, as_user, make_user, make_patient, make_device, make_study
) -> None:
    """Compatibilidad: los estudios que escribió `seed_demo` siguen andando."""
    patient = await make_patient()
    device, _ = await make_device(patient=patient)
    study = await make_study(patient, device)
    payload = b"\x00\x00\x80\x3f" * 1000
    put_object(f"studies/{study.id}/ecg.f32", payload)
    study.ecg_s3_key = f"studies/{study.id}/ecg.f32"
    study.ecg_byte_length = len(payload)
    study.ecg_sha256 = hashlib.sha256(payload).hexdigest()
    study.samples_count = 1000
    await db.flush()
    as_user(await make_user(UserRole.ADMIN))

    manifest = await _manifest(client, study.id)

    assert manifest["raw"] is not None
    assert manifest["raw"]["byteLength"] == len(payload)
    assert manifest["segments"] == []


async def test_a_study_without_any_signal_is_404(
    client, s3, db, as_user, make_user, make_patient, make_device, make_study
) -> None:
    """Recién creado y sin ningún lote procesado: el visor muestra "esperando
    datos", no un error genérico."""
    patient = await make_patient()
    device, _ = await make_device(patient=patient)
    study = await make_study(patient, device)
    as_user(await make_user(UserRole.ADMIN))

    response = await client.get(f"/studies/{study.id}/ecg/manifest")

    assert response.status_code == 404
    assert response.json()["code"] == "ECG_NOT_FOUND"


async def test_manifest_grows_as_batches_arrive(
    client, s3, db, as_user, make_user, make_patient, make_device
) -> None:
    """El objetivo del feature: el gráfico crece lote a lote."""
    patient = await make_patient()
    device, api_key = await make_device(patient=patient)
    frames = build_frames(4000)
    half = len(frames) // 2

    first = (await post_frames(client, device, api_key, frames[:half])).json()
    await process_batch(db, first["batchId"])
    as_user(await make_user(UserRole.ADMIN))
    before = await _manifest(client, first["studyId"])

    second = (await post_frames(client, device, api_key, frames[half:])).json()
    await process_batch(db, second["batchId"])
    after = await _manifest(client, first["studyId"])

    assert after["sampleCount"] > before["sampleCount"]
    assert len(after["segments"]) == len(before["segments"]) + 1
    assert after["durationMs"] >= before["durationMs"]


async def test_every_url_is_presigned_with_an_expiry(
    client, s3, db, as_user, make_user, make_patient, make_device
) -> None:
    """Ningún objeto de señal se sirve público: todos van firmados y vencen."""
    from urllib.parse import parse_qs, urlparse

    _, study_id = await _ingested_study(client, db, make_patient, make_device)
    as_user(await make_user(UserRole.ADMIN))
    manifest = await _manifest(client, study_id)

    urls = [item["url"] for item in manifest["levels"] + manifest["segments"]]
    assert urls
    for url in urls:
        params = parse_qs(urlparse(url).query)
        assert "X-Amz-Signature" in params
        assert int(params["X-Amz-Expires"][0]) <= 3600
    for item in manifest["levels"] + manifest["segments"]:
        assert item["expiresAt"]


async def test_a_doctor_who_does_not_own_the_patient_gets_404(
    client, s3, db, as_user, make_user, make_doctor, make_patient, make_device
) -> None:
    """404 y no 403: no se filtra siquiera la existencia del estudio."""
    _, study_id = await _ingested_study(client, db, make_patient, make_device)
    other_doctor = await make_doctor()
    other_user = await db.get(type(await make_user(UserRole.MEDICO)), other_doctor.user_id)
    assert other_user is not None
    as_user(other_user)

    response = await client.get(f"/studies/{study_id}/ecg/manifest")

    assert response.status_code == 404


async def test_manifest_access_is_audited(
    client, s3, db, as_user, make_user, make_patient, make_device
) -> None:
    from sqlalchemy import select

    _, study_id = await _ingested_study(client, db, make_patient, make_device)
    admin = await make_user(UserRole.ADMIN)
    as_user(admin)

    await client.get(f"/studies/{study_id}/ecg/manifest")

    events = list(
        (
            await db.scalars(
                select(AuditEvent).where(AuditEvent.event_type == AuditEventType.ECG_ACCESSED)
            )
        ).all()
    )
    assert any(e.event_metadata["target_study_id"] == str(study_id) for e in events)


async def test_an_unknown_study_is_404(client, as_user, make_user) -> None:
    as_user(await make_user(UserRole.ADMIN))

    response = await client.get(f"/studies/{uuid.uuid4()}/ecg/manifest")

    assert response.status_code == 404
