"""Manifest v2: estudios ingestados y estudios legacy conviven.

El visor consume `levels` en los dos casos; la diferencia está en dónde vive la
señal completa (`raw` para los seedeados, `segments` para los ingestados).
"""

import hashlib
import uuid
from datetime import UTC, datetime
from unittest.mock import patch

import numpy as np
from sqlalchemy import select

from app.core.s3 import put_object
from app.db.models.audit_event import AuditEvent, AuditEventType
from app.db.models.ecg_batch import ECGBatch, ProcessingStatus
from app.db.models.ecg_event import ECGEvent, ECGEventSeverity, ECGEventType
from app.db.models.study import Study, StudyStatus
from app.db.models.user import UserRole
from app.modules.ingest.processing import PYRAMID_BUCKETS, append_filtered_view, process_batch
from app.scripts.seed_demo import SAMPLE_RATE as DEMO_SAMPLE_RATE
from app.scripts.seed_demo import StudySpec, _seed_study
from tests.ingest_helpers import build_frames, post_frames


async def _manifest(client, study_id) -> dict:
    return (await client.get(f"/studies/{study_id}/ecg/manifest")).json()


async def _ingested_study(client, db, make_patient, make_device, samples: int = 4000):
    patient = await make_patient()
    device, api_key = await make_device(patient=patient)
    body = (await post_frames(client, device, api_key, build_frames(samples))).json()
    await process_batch(db, body["batchId"])
    study = await db.get(Study, body["studyId"])
    assert study is not None
    # These manifest contract tests use a short synthetic study. The production
    # path waits 120 seconds; force its safe prefix here to test the view shape.
    with patch("app.modules.ingest.processing.CONTEXT_SECONDS", 0):
        await append_filtered_view(db, study)
    await db.commit()
    return patient, body["studyId"]


async def test_an_ingested_study_exposes_segments_and_no_raw(
    client, s3, db, as_user, make_user, make_patient, make_device
) -> None:
    _, study_id = await _ingested_study(client, db, make_patient, make_device)
    as_user(await make_user(UserRole.ADMIN))

    response = await client.get(f"/studies/{study_id}/ecg/manifest")

    assert response.status_code == 200, response.text
    manifest = response.json()
    assert manifest["formatVersion"] == 3
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
    batch = ECGBatch(
        device_id=device.id,
        received_at=datetime.now(UTC),
        batch_timestamp=0,
        duration_seconds=4,
        sample_rate=250,
        num_channels=1,
        num_samples=1000,
        compression_type="raw-f32",
        s3_key=f"batches/{device.id}/legacy.f32",
        processing_status=ProcessingStatus.DONE,
    )
    db.add(batch)
    await db.flush()
    db.add(
        ECGEvent(
            batch_id=batch.id,
            event_type=ECGEventType.NOISE,
            severity=ECGEventSeverity.LOW,
            timestamp_in_recording=0.0,
            duration_seconds=0.5,
            confidence_score=None,
            event_metadata={
                "kind": "sqi_unanalyzable",
                "studyId": str(study.id),
                "offsetInStudySeconds": 1.25,
            },
        )
    )
    await db.flush()
    as_user(await make_user(UserRole.ADMIN))

    manifest = await _manifest(client, study.id)

    assert manifest["raw"] is not None
    assert manifest["raw"]["byteLength"] == len(payload)
    assert manifest["segments"] == []
    assert manifest["annotations"][0] == {
        "id": manifest["annotations"][0]["id"],
        "kind": "sqi_unanalyzable",
        "category": "signal_quality",
        "severity": "low",
        "startOffsetMs": 1250,
        "endOffsetMs": 1750,
        # Sin tramos de línea de tiempo —este estudio es seedeado, no ingerido—
        # la hora absoluta es el inicio del estudio más el offset. Es lo correcto
        # justamente porque un estudio seedeado no tiene huecos.
        "startEpochMs": manifest["annotations"][0]["startEpochMs"],
        "endEpochMs": manifest["annotations"][0]["endEpochMs"],
        "confidenceScore": None,
        # Solo los registros del paciente llenan estos dos: un hallazgo no
        # responde a nada ni trae texto propio.
        "linkedAnnotationId": None,
        "description": None,
    }
    started_ms = manifest["startTimestamp"]
    assert manifest["annotations"][0]["startEpochMs"] == started_ms + 1250
    assert manifest["annotations"][0]["endEpochMs"] == started_ms + 1750


async def test_a_demo_study_serves_its_levels_as_chunks(
    client, s3, db, as_user, make_user, make_patient, make_device
) -> None:
    """Los estudios de `seed_demo` traen la pirámide en la forma que lee el manifest.

    La seed escribía cada nivel como un objeto suelto, sin `chunks`. El manifest
    los servía con cero chunks, el visor no descargaba nada y los estudios de
    demo —los que abre el e2e del visor en CI— se dibujaban vacíos.
    """
    from app.core.s3 import get_object

    patient = await make_patient()
    device, _ = await make_device(patient=patient)
    spec = StudySpec(status=StudyStatus.COMPLETED, starts_hours_ago=2, minutes=3)
    study = await _seed_study(db, None, patient, device, spec, 0, batch_minutes=5)
    await db.flush()
    as_user(await make_user(UserRole.ADMIN))

    manifest = await _manifest(client, study.id)

    signal = np.frombuffer(get_object(_key_from_url(manifest["raw"]["url"])), dtype="<f4")
    assert signal.size == manifest["sampleCount"] == 3 * 60 * DEMO_SAMPLE_RATE
    assert [level["samplesPerBucket"] for level in manifest["levels"]] == list(PYRAMID_BUCKETS)
    for level in manifest["levels"]:
        bucket = level["samplesPerBucket"]
        assert level["chunks"], f"el nivel {bucket} llega sin chunks"
        assert sum(chunk["pointCount"] for chunk in level["chunks"]) == level["pointCount"]
        payload = b""
        for chunk in level["chunks"]:
            data = get_object(_key_from_url(chunk["url"]))
            assert len(data) == chunk["byteLength"] == chunk["pointCount"] * 4
            assert hashlib.sha256(data).hexdigest() == chunk["sha256"]
            payload += data
        # Lo mismo que escribe la ingesta: min/max de cada bucket completo.
        blocks = signal[: (signal.size // bucket) * bucket].reshape(-1, bucket)
        expected = np.empty(blocks.shape[0] * 2, dtype="<f4")
        expected[0::2] = blocks.min(axis=1)
        expected[1::2] = blocks.max(axis=1)
        assert np.array_equal(np.frombuffer(payload, dtype="<f4"), expected)


async def test_manifest_normalizes_orders_and_clips_ingested_events(
    client, s3, db, as_user, make_user, make_patient, make_device
) -> None:
    _, study_id = await _ingested_study(client, db, make_patient, make_device)
    batch = await db.scalar(select(ECGBatch).where(ECGBatch.study_id == study_id))
    assert batch is not None
    hidden = ECGEvent(
        batch_id=batch.id,
        event_type=ECGEventType.PVC,
        severity=ECGEventSeverity.HIGH,
        timestamp_in_recording=0.1,
        duration_seconds=0.1,
        confidence_score=0.7,
        event_metadata={"kind": "pvc", "startSampleIndex": 50, "sampleCount": 50},
        deleted_at=datetime.now(UTC),
    )
    db.add_all(
        [
            ECGEvent(
                batch_id=batch.id,
                event_type=ECGEventType.AFIB,
                severity=ECGEventSeverity.CRITICAL,
                timestamp_in_recording=7.0,
                duration_seconds=4.0,
                confidence_score=0.96,
                event_metadata={"kind": "afib", "startSampleIndex": 3500, "sampleCount": 2000},
            ),
            ECGEvent(
                batch_id=batch.id,
                event_type=ECGEventType.NOISE,
                severity=ECGEventSeverity.MEDIUM,
                timestamp_in_recording=1.0,
                duration_seconds=0.5,
                confidence_score=None,
                event_metadata={
                    "kind": "lead_off",
                    "startSampleIndex": 500,
                    "sampleCount": 250,
                },
            ),
            hidden,
        ]
    )
    await db.flush()
    as_user(await make_user(UserRole.ADMIN))

    manifest = await _manifest(client, study_id)

    assert [item["kind"] for item in manifest["annotations"]] == ["lead_off", "afib"]
    assert manifest["annotations"][0]["startOffsetMs"] == 1000
    assert manifest["annotations"][0]["endOffsetMs"] == 1500
    assert manifest["annotations"][1]["startOffsetMs"] == 7000
    assert manifest["annotations"][1]["endOffsetMs"] == 8000
    assert manifest["annotations"][1]["category"] == "clinical"
    assert manifest["annotations"][1]["confidenceScore"] == 0.96


def _quality_event(batch_id, kind: str, start: int, count: int) -> ECGEvent:
    return ECGEvent(
        batch_id=batch_id,
        event_type=ECGEventType.NOISE,
        severity=ECGEventSeverity.MEDIUM if kind == "lead_off" else ECGEventSeverity.LOW,
        timestamp_in_recording=start / 500,
        duration_seconds=count / 500,
        confidence_score=None,
        event_metadata={"kind": kind, "startSampleIndex": start, "sampleCount": count},
    )


async def test_contiguous_signal_quality_events_are_one_episode(
    client, s3, db, as_user, make_user, make_patient, make_device
) -> None:
    """La ingesta deriva un evento por lote; el médico tiene que ver un episodio."""
    _, study_id = await _ingested_study(client, db, make_patient, make_device)
    batch = await db.scalar(select(ECGBatch).where(ECGBatch.study_id == study_id))
    assert batch is not None
    db.add_all(
        [
            # Un electrodo suelto partido en tres lotes, más su SQI inanalizable.
            _quality_event(batch.id, "lead_off", 1000, 500),
            _quality_event(batch.id, "lead_off", 500, 500),
            _quality_event(batch.id, "lead_off", 1500, 500),
            _quality_event(batch.id, "sqi_unanalyzable", 500, 500),
            _quality_event(batch.id, "sqi_unanalyzable", 1000, 1000),
            # Dos segundos después: otro episodio.
            _quality_event(batch.id, "lead_off", 3000, 300),
            # Inanalizable sin electrodo suelto debajo: se queda.
            _quality_event(batch.id, "sqi_unanalyzable", 3500, 300),
        ]
    )
    await db.flush()
    as_user(await make_user(UserRole.ADMIN))

    manifest = await _manifest(client, study_id)

    assert [
        (item["kind"], item["startOffsetMs"], item["endOffsetMs"])
        for item in manifest["annotations"]
    ] == [
        ("lead_off", 1000, 4000),
        ("lead_off", 6000, 6600),
        ("sqi_unanalyzable", 7000, 7600),
    ]


async def test_findings_list_the_same_signal_quality_episodes_as_the_trace(
    client, s3, db, as_user, make_user, make_patient, make_device
) -> None:
    """El panel lleva al visor: si el visor dibuja un episodio, la lista no puede
    mostrar doce filas, una por lote."""
    _, study_id = await _ingested_study(client, db, make_patient, make_device)
    batch = await db.scalar(select(ECGBatch).where(ECGBatch.study_id == study_id))
    assert batch is not None
    db.add_all(
        [
            _quality_event(batch.id, "lead_off", 1000, 500),
            _quality_event(batch.id, "lead_off", 500, 500),
            _quality_event(batch.id, "sqi_unanalyzable", 500, 500),
            _quality_event(batch.id, "sqi_unanalyzable", 3500, 300),
        ]
    )
    await db.flush()
    as_user(await make_user(UserRole.ADMIN))

    body = (await client.get(f"/studies/{study_id}/findings")).json()
    items = [item for group in body["groups"] for item in group["items"]] + body["ungrouped"]

    assert sorted(
        (item["kind"], item["startOffsetMs"], item["endOffsetMs"])
        for item in items
        if item["kind"] in {"lead_off", "sqi_unanalyzable"}
    ) == [("lead_off", 1000, 3000), ("sqi_unanalyzable", 7000, 7600)]


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


async def test_report_windows_reads_only_the_requested_raw_range(
    client, s3, db, as_user, make_user, make_patient, make_device
) -> None:
    """El PDF pide tiras cortas, no descarga el Holter entero al navegador."""
    _, study_id = await _ingested_study(client, db, make_patient, make_device)
    as_user(await make_user(UserRole.ADMIN))
    manifest = await _manifest(client, study_id)
    start = manifest["startTimestamp"] + 1_000

    response = await client.post(
        f"/studies/{study_id}/ecg/report-windows",
        json={"windows": [{"id": "detail-1", "startEpochMs": start, "endEpochMs": start + 2_000}]},
    )

    assert response.status_code == 200, response.text
    window = response.json()["windows"][0]
    assert window["id"] == "detail-1"
    assert window["source"] == "filtered_visualization"
    assert len(window["samplesMv"]) == len(window["timestampsMs"])
    assert 900 <= len(window["samplesMv"]) <= 1_100


async def test_unfinished_filtered_window_reports_processing(
    client, s3, db, as_user, make_user, make_patient, make_device
) -> None:
    patient = await make_patient()
    device, key = await make_device(patient=patient)
    body = (await post_frames(client, device, key, build_frames(900))).json()
    await process_batch(db, body["batchId"])
    as_user(await make_user(UserRole.ADMIN))
    manifest = await _manifest(client, body["studyId"])
    start = manifest["timeline"][0]["startEpochMs"]
    response = await client.post(
        f"/studies/{body['studyId']}/ecg/report-windows",
        json={"windows": [{"id": "pending", "startEpochMs": start, "endEpochMs": start + 1000}]},
    )
    assert response.status_code == 503
    assert response.json()["code"] == "ECG_PROCESSING"


async def test_report_windows_rejects_a_window_longer_than_ten_seconds(
    client, s3, db, as_user, make_user, make_patient, make_device
) -> None:
    _, study_id = await _ingested_study(client, db, make_patient, make_device)
    as_user(await make_user(UserRole.ADMIN))
    manifest = await _manifest(client, study_id)

    response = await client.post(
        f"/studies/{study_id}/ecg/report-windows",
        json={
            "windows": [
                {
                    "id": "too-long",
                    "startEpochMs": manifest["startTimestamp"],
                    "endEpochMs": manifest["startTimestamp"] + 10_001,
                }
            ]
        },
    )

    assert response.status_code == 422
    assert response.json()["code"] == "INVALID_WINDOW"


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
    study = await db.get(Study, first["studyId"])
    assert study is not None
    with patch("app.modules.ingest.processing.CONTEXT_SECONDS", 0):
        await append_filtered_view(db, study)
    await db.commit()
    as_user(await make_user(UserRole.ADMIN))
    before = await _manifest(client, first["studyId"])

    second = (await post_frames(client, device, api_key, frames[half:])).json()
    await process_batch(db, second["batchId"])
    await db.refresh(study)
    with patch("app.modules.ingest.processing.CONTEXT_SECONDS", 0):
        await append_filtered_view(db, study)
    await db.commit()
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

    level_chunks = [chunk for level in manifest["levels"] for chunk in level["chunks"]]
    urls = [item["url"] for item in level_chunks + manifest["segments"]]
    assert urls
    for url in urls:
        params = parse_qs(urlparse(url).query)
        assert "X-Amz-Signature" in params
        assert int(params["X-Amz-Expires"][0]) <= 3600
    for item in level_chunks + manifest["segments"]:
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
