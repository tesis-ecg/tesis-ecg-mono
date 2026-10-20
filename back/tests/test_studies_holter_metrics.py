"""`GET /studies/{id}/holter-metrics` y su paso al snapshot del informe."""

import uuid
from datetime import UTC, datetime, timedelta

import numpy as np

from app.core.s3 import put_object
from app.db.models.ecg_event import ECGEvent
from app.db.models.study import Study, StudyStatus
from app.db.models.user import User
from app.ml.beats import DETECTOR
from app.modules.ingest.processing import append_beat_analysis, compact_beat_chunks, segment_key
from app.modules.studies.studies_service import _engine_pauses
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

    assert first["snapshot"]["schemaVersion"] == 5
    assert first["snapshot"]["metrics"]["status"] == "ok"
    assert first["snapshotHash"] == second["snapshotHash"]
    metric_windows = [window for window in first["windows"] if window["category"] == "metric"]
    assert [window["kind"] for window in metric_windows] == ["hr_min", "hr_max", "pause_longest"]
    assert all(window["findingId"] is None for window in metric_windows)
    pause = metric_windows[-1]
    assert pause["startEpochMs"] < pause["findingStartEpochMs"] < pause["endEpochMs"]


async def test_los_latidos_sin_la_marca_del_detector_declaran_el_pan_tompkins(
    db, s3, as_user, make_doctor, make_patient, make_device, make_study
) -> None:
    """Hasta el `backfill_beat_analysis --reset`, los latidos de un estudio
    viejo son del Pan-Tompkins: la versión tiene que decirlo, o la nota de
    método del PDF nombra un detector que no los produjo."""
    doctor, study, _ = await _study_with_signal(
        db, make_doctor, make_patient, make_device, make_study
    )
    client = as_user(await _doctor_user(db, doctor))
    assert all(chunk["detector"] == DETECTOR for chunk in study.ecg_beat_chunks)
    current = (await client.get(f"/studies/{study.id}/holter-metrics")).json()

    study.ecg_beat_chunks = [
        {key: value for key, value in chunk.items() if key != "detector"}
        for chunk in study.ecg_beat_chunks
    ]
    await db.commit()
    legacy = (await client.get(f"/studies/{study.id}/holter-metrics")).json()

    assert current["analysis"]["algorithmVersion"] == 4
    assert legacy["analysis"]["algorithmVersion"] == 1


def test_la_compactacion_conserva_la_marca_solo_si_todas_las_partes_la_tienen(s3) -> None:  # type: ignore[no-untyped-def]
    study = Study(id=uuid.uuid4())
    chunks = []
    for start in (0, 100):
        key = f"studies/{study.id}/beats/{start}.bin"
        put_object(key, b"")
        chunks.append({"key": key, "startSampleIndex": start, "sampleCount": 100, "beatCount": 0})
    study.ecg_beat_chunks = [{**chunk, "detector": DETECTOR} for chunk in chunks]
    assert compact_beat_chunks(study, force=True)[0]["detector"] == DETECTOR

    study.ecg_beat_chunks = [{**chunks[0], "detector": DETECTOR}, chunks[1]]
    assert "detector" not in compact_beat_chunks(study, force=True)[0]


def _pause_event(start: int, count: int, **metadata: object) -> ECGEvent:
    return ECGEvent(
        event_metadata={
            "kind": "pause",
            "source": "ml",
            "startSampleIndex": start,
            "sampleCount": count,
            **metadata,
        }
    )


def test_las_pausas_fundidas_del_motor_son_episodios_y_no_pausas() -> None:
    pauses, episodes = _engine_pauses(
        [
            _pause_event(0, 1500, beatCount=2),
            _pause_event(5000, 5500, beatCount=3),
            _pause_event(20000, 2000, beatCount=2, openStart=True),
            _pause_event(30000, 2000),
            _pause_event(40000, 2000, beatCount=2, source="firmware_flags"),
        ]
    )

    assert pauses == [(0, 1500)]
    assert episodes == [(5000, 10500), (20000, 22000), (30000, 32000)]


# --- S y V de punta a punta con la reclasificación (#14 + #15) -------------- #


async def _metrics(db, as_user, doctor, study) -> dict:  # type: ignore[no-untyped-def]
    response = await as_user(await _doctor_user(db, doctor)).get(
        f"/studies/{study.id}/holter-metrics"
    )
    assert response.status_code == 200
    body: dict = response.json()
    return body


async def _classified_study(  # type: ignore[no-untyped-def]
    db, make_doctor, make_patient, make_device, make_study
):
    """Estudio ya analizado con etiquetas del motor: un par de V (latidos 100 y 101)
    y una V aislada (latido 300), más su hallazgo `ventricular_ectopy` sobre el par."""
    from app.db.models.ecg_event import ECGEventType
    from app.db.models.signal_quality import SignalQualityInterval, SignalQualityLevel
    from app.ml import beat_labels as bl
    from app.modules.ingest import beat_label_store
    from tests.test_studies_reclassification import _finding, _study

    doctor, study, batch, times = await _study(
        db, make_doctor, make_patient, make_device, make_study
    )
    samples = np.round(times * RATE).astype(np.int64)
    codes = np.full(samples.size, bl.CODE_N, dtype=np.uint8)
    codes[[100, 101, 300]] = bl.CODE_V
    n = samples.size
    records = bl.build_records(
        samples,
        codes,
        np.zeros(n, dtype=np.uint8),
        np.full(n, -20, dtype=np.int64),
        np.full(n, 25, dtype=np.int64),
        np.zeros(n, dtype=np.int64),
        np.full(n, 1.0, dtype=np.float32),
        qrs_measured=np.ones(n, dtype=bool),
        p_detected=np.zeros(n, dtype=bool),
    )
    total = int(study.samples_count)
    study.ml_state = {
        beat_label_store.STATE_KEY: beat_label_store.append_block(
            study.id, None, records, block_start=0, block_end=total, cursor_before=0
        )
    }
    study.ml_analyzed_samples = total
    db.add(
        SignalQualityInterval(
            study_id=study.id,
            batch_id=batch.id,
            start_sample_index=0,
            sample_count=total,
            level=SignalQualityLevel.GOOD,
            reason="ok",
            window_count=1,
            metrics=None,
            model_version="test",
        )
    )
    event = _finding(
        batch,
        study,
        "ventricular_ectopy",
        ECGEventType.ECTOPY,
        int(samples[100]) - 50,
        int(samples[101] - samples[100]) + 100,
    )
    db.add(event)
    await db.commit()
    return doctor, study, event


async def test_las_metricas_s_y_v_vienen_de_las_etiquetas_del_motor(
    db, s3, as_user, make_doctor, make_patient, make_device, make_study
) -> None:
    doctor, study, _ = await _classified_study(
        db, make_doctor, make_patient, make_device, make_study
    )

    body = await _metrics(db, as_user, doctor, study)

    v = body["ventricular"]
    assert body["ectopyUnavailableReason"] is None
    assert body["analysis"]["algorithmVersion"] == 6
    assert v["total"] == 3 and v["single"] == 1 and v["pairs"] == {"episodes": 1, "beats": 2}
    # El par cae en un mismo minuto de reloj salvo que justo lo parta el borde
    # (el estudio arranca "ahora"); el conteo exacto lo fija el test unitario.
    assert v["maxPerMinute"]["value"] in (1, 2) and v["maxPerMinute"]["epochMs"] > 0
    assert body["supraventricular"]["total"] == 0
    assert body["heartRate"]["abnormalBeats"] == 3
    assert body["analysis"]["classifiedBeats"] > 400 and body["analysis"]["unclassifiedBeats"] == 0


async def test_reclasificar_una_v_como_ruido_baja_el_total_v_en_la_respuesta(
    db, s3, as_user, make_doctor, make_patient, make_device, make_study
) -> None:
    from tests.test_studies_reclassification import _url, _user_of

    doctor, study, event = await _classified_study(
        db, make_doctor, make_patient, make_device, make_study
    )
    client = as_user(await _user_of(db, doctor))

    noise = await client.post(_url(study, event), json={"action": "noise"})
    assert noise.status_code == 200, noise.text
    v = noise.json()["holterMetrics"]["ventricular"]
    assert v["total"] == 1 and v["pairs"]["episodes"] == 0
    assert noise.json()["holterMetrics"]["analysis"]["doctorCorrections"]["findings"] == 1

    # V -> S: el par cambia de recuadro; la V aislada fuera del hallazgo queda.
    await client.post(_url(study, event), json={"action": "reset"})
    relabel = await client.post(_url(study, event), json={"action": "relabel", "beatLabel": "S"})
    assert relabel.status_code == 200, relabel.text
    metrics = relabel.json()["holterMetrics"]
    assert metrics["ventricular"]["total"] == 1
    assert metrics["supraventricular"]["total"] == 2
    assert metrics["supraventricular"]["pairs"]["episodes"] == 1
    assert metrics["analysis"]["doctorCorrections"]["relabeledBeats"] == 2

    # Rechazar el hallazgo: sus latidos pasan a N.
    await client.post(_url(study, event), json={"action": "reset"})
    reject = await client.post(_url(study, event), json={"action": "reject"})
    assert reject.json()["holterMetrics"]["ventricular"]["total"] == 1
    assert reject.json()["holterMetrics"]["supraventricular"]["total"] == 0

    # Es lo mismo que devuelve la ruta de métricas después.
    again = await _metrics(db, as_user, doctor, study)
    assert again["ventricular"] == reject.json()["holterMetrics"]["ventricular"]


async def test_el_informe_congela_las_s_y_v_corregidas_en_el_snapshot(
    db, s3, as_user, make_doctor, make_patient, make_device, make_study
) -> None:
    from tests.test_studies_reclassification import _url, _user_of

    doctor, study, event = await _classified_study(
        db, make_doctor, make_patient, make_device, make_study
    )
    client = as_user(await _user_of(db, doctor))
    before = (await client.get(f"/studies/{study.id}/clinical-report/preview")).json()
    assert before["snapshot"]["metrics"]["ventricular"]["total"] == 3

    await client.post(_url(study, event), json={"action": "noise"})
    after = (await client.get(f"/studies/{study.id}/clinical-report/preview")).json()

    assert after["snapshot"]["schemaVersion"] == 5
    assert after["snapshot"]["metrics"]["ventricular"]["total"] == 1
    assert after["snapshot"]["metrics"]["analysis"]["doctorCorrections"]["findings"] == 1
    # El hallazgo marcado como ruido no se informa.
    assert all(item["kind"] != "ventricular_ectopy" for item in after["snapshot"]["findings"])
    assert before["snapshotHash"] != after["snapshotHash"]
