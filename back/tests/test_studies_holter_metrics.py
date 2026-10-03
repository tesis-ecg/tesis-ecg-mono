"""`GET /studies/{id}/holter-metrics` y su paso al snapshot del informe."""

from datetime import UTC, datetime, timedelta

import numpy as np

from app.core.s3 import put_object
from app.db.models.study import Study, StudyStatus
from app.db.models.user import User
from app.modules.ingest.processing import append_beat_analysis, segment_key
from tests.synthetic_ecg import beat_times, synthetic_ecg

RATE = 500


async def _doctor_user(db, doctor) -> User:  # type: ignore[no-untyped-def]
    user = await db.get(User, doctor.user_id)
    assert user is not None
    return user


async def _study_with_signal(  # type: ignore[no-untyped-def]
    db, make_doctor, make_patient, make_device, make_study, *, analyze: bool = True
):
    """Un estudio cerrado de 6 min a 75 lpm con una pausa de 2,6 s, ya analizado."""
    doctor = await make_doctor()
    patient = await make_patient(doctor=doctor)
    device, _ = await make_device(patient=patient)
    rr = np.full(450, 0.8)
    rr[200] = 2.6
    times = beat_times(rr)
    signal = synthetic_ecg(times, times[-1] + 2.0, RATE, noise_mv=0.02).astype("<f4")
    started = datetime.now(UTC) - timedelta(hours=2)
    study: Study = await make_study(
        patient,
        device,
        status=StudyStatus.COMPLETED,
        started_at=started,
        ended_at=started + timedelta(minutes=10),
        duration_ms=signal.size * 1000 // RATE,
        samples_count=int(signal.size),
    )
    key = segment_key(study.id, 0)
    put_object(key, signal.tobytes())
    study.ecg_segments = [
        {"key": key, "startSampleIndex": 0, "sampleCount": int(signal.size), "byteLength": 0}
    ]
    if analyze:
        await append_beat_analysis(db, study, max_samples=None)
    await db.commit()
    return doctor, study, times


async def test_metricas_con_evidencia_horaria(
    db, s3, as_user, make_doctor, make_patient, make_device, make_study
) -> None:
    doctor, study, times = await _study_with_signal(
        db, make_doctor, make_patient, make_device, make_study
    )

    response = await as_user(await _doctor_user(db, doctor)).get(
        f"/studies/{study.id}/holter-metrics"
    )

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"
    heart = body["heartRate"]
    assert abs(heart["totalBeats"] - times.size) <= 1
    assert heart["min"]["value"] == heart["max"]["value"] == 75
    assert heart["abnormalBeats"] is None
    assert body["supraventricular"] is None and body["ventricular"] is None
    assert body["ectopyUnavailableReason"] == "BEAT_CLASSIFICATION_UNAVAILABLE"
    pauses = body["pauses"]
    assert pauses["count"] == 1
    assert abs(pauses["longest"]["durationMs"] - 2600) <= 10
    started_ms = int(study.started_at.timestamp() * 1000)
    assert abs(pauses["longest"]["epochMs"] - (started_ms + times[200] * 1000)) <= 10
    assert body["st"][0]["label"] == "Canal 1 (LL-RA)"
    assert body["hrvTime"]["sdnnMs"] is not None


async def test_estudio_sin_analizar_queda_pendiente_y_otro_medico_no_lo_ve(
    db, s3, as_user, make_doctor, make_patient, make_device, make_study
) -> None:
    doctor, study, _ = await _study_with_signal(
        db, make_doctor, make_patient, make_device, make_study, analyze=False
    )
    intruder = await make_doctor()
    await db.commit()

    pending = await as_user(await _doctor_user(db, doctor)).get(
        f"/studies/{study.id}/holter-metrics"
    )
    hidden = await as_user(await _doctor_user(db, intruder)).get(
        f"/studies/{study.id}/holter-metrics"
    )

    assert pending.status_code == 200
    assert pending.json()["status"] == "pending"
    assert pending.json()["unavailableReason"] == "ANALYSIS_PENDING"
    assert hidden.status_code == 404

    preview = await as_user(await _doctor_user(db, doctor)).get(
        f"/studies/{study.id}/clinical-report/preview"
    )
    assert preview.status_code == 200
    assert preview.json()["canFinalize"] is False
    assert any(issue["code"] == "BEAT_ANALYSIS_PENDING" for issue in preview.json()["issues"])


async def test_el_preview_bloquea_un_analisis_parcial(
    db, s3, as_user, make_doctor, make_patient, make_device, make_study
) -> None:
    doctor, study, _ = await _study_with_signal(
        db, make_doctor, make_patient, make_device, make_study
    )
    study.beats_analyzed_samples -= 1
    await db.commit()

    preview = await as_user(await _doctor_user(db, doctor)).get(
        f"/studies/{study.id}/clinical-report/preview"
    )

    assert preview.status_code == 200
    assert preview.json()["snapshot"]["metrics"]["status"] == "ok"
    assert preview.json()["canFinalize"] is False
    assert any(issue["code"] == "BEAT_ANALYSIS_PENDING" for issue in preview.json()["issues"])


async def test_el_preview_congela_las_metricas_y_suma_tiras_de_evidencia(
    db, s3, as_user, make_doctor, make_patient, make_device, make_study
) -> None:
    doctor, study, _ = await _study_with_signal(
        db, make_doctor, make_patient, make_device, make_study
    )
    client = as_user(await _doctor_user(db, doctor))

    first = (await client.get(f"/studies/{study.id}/clinical-report/preview")).json()
    second = (await client.get(f"/studies/{study.id}/clinical-report/preview")).json()

    assert first["snapshot"]["schemaVersion"] == 2
    assert first["snapshot"]["metrics"]["status"] == "ok"
    assert first["snapshotHash"] == second["snapshotHash"]
    metric_windows = [window for window in first["windows"] if window["category"] == "metric"]
    assert [window["kind"] for window in metric_windows] == ["hr_min", "hr_max", "pause_longest"]
    assert all(window["findingId"] is None for window in metric_windows)
    pause = metric_windows[-1]
    assert pause["startEpochMs"] < pause["findingStartEpochMs"] < pause["endEpochMs"]
