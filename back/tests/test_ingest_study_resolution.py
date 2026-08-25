"""`serial → device.patient_id → estudio`.

El chaleco solo conoce su número de serie. Todo lo demás lo resuelve el
backend, y equivocarse acá significa archivar el registro de un paciente bajo
otro — el peor modo de falla del sistema.
"""

from datetime import UTC, datetime, timedelta

from sqlalchemy import func, select

from app.db.models.study import Study, StudyStatus
from tests.ingest_helpers import build_frames, post_frames


async def test_creates_a_study_when_the_device_has_none_open(
    client, s3, db, make_patient, make_device
) -> None:
    """El flujo real: el chaleco se enciende y empieza a grabar."""
    patient = await make_patient()
    device, api_key = await make_device(patient=patient)

    body = (await post_frames(client, device, api_key, build_frames(900))).json()

    study = await db.get(Study, body["studyId"])
    assert study is not None
    assert study.patient_id == patient.id
    assert study.device_id == device.id
    assert study.status == StudyStatus.IN_PROGRESS
    assert study.sample_rate == 500


async def test_started_at_comes_from_the_time_anchor(
    client, s3, db, make_patient, make_device
) -> None:
    patient = await make_patient()
    device, api_key = await make_device(patient=patient)
    uptime_ms = 7_200_000  # el equipo lleva 2 h encendido

    before = datetime.now(UTC)
    body = (
        await post_frames(client, device, api_key, build_frames(900), uptime_ms=uptime_ms)
    ).json()

    study = await db.get(Study, body["studyId"])
    assert study is not None
    # started_at ≈ ahora − uptime (la primera trama arranca en t0Ms = 0).
    expected = before - timedelta(milliseconds=uptime_ms)
    assert abs((study.started_at - expected).total_seconds()) < 5


async def test_reuses_the_open_study_across_batches(
    client, s3, db, make_patient, make_device
) -> None:
    patient = await make_patient()
    device, api_key = await make_device(patient=patient)
    frames = build_frames(2400)
    half = len(frames) // 2

    first = (await post_frames(client, device, api_key, frames[:half])).json()
    second = (await post_frames(client, device, api_key, frames[half:])).json()

    assert first["studyId"] == second["studyId"]
    assert await db.scalar(select(func.count()).select_from(Study)) == 1


async def test_a_completed_study_is_not_reopened(
    client, s3, db, make_patient, make_device, make_study
) -> None:
    patient = await make_patient()
    device, api_key = await make_device(patient=patient)
    finished = await make_study(patient, device, status=StudyStatus.COMPLETED)

    body = (await post_frames(client, device, api_key, build_frames(900))).json()

    assert body["studyId"] != str(finished.id)
    assert await db.scalar(select(func.count()).select_from(Study)) == 2


async def test_a_scheduled_study_is_not_used_either(
    client, s3, db, make_patient, make_device, make_study
) -> None:
    """Solo un estudio `in_progress` recibe señal; uno agendado todavía no."""
    patient = await make_patient()
    device, api_key = await make_device(patient=patient)
    scheduled = await make_study(patient, device, status=StudyStatus.SCHEDULED)

    body = (await post_frames(client, device, api_key, build_frames(900))).json()

    assert body["studyId"] != str(scheduled.id)


async def test_an_existing_in_progress_study_is_reused(
    client, s3, db, make_patient, make_device, make_study
) -> None:
    patient = await make_patient()
    device, api_key = await make_device(patient=patient)
    existing = await make_study(patient, device, status=StudyStatus.IN_PROGRESS)

    body = (await post_frames(client, device, api_key, build_frames(900))).json()

    assert body["studyId"] == str(existing.id)


async def test_signal_never_lands_in_the_previous_patients_study(
    client, s3, db, make_doctor, make_patient, make_device, make_study
) -> None:
    """El peor bug posible: señal de un paciente archivada bajo otro.

    El equipo se reasigna entre lotes. El estudio abierto del paciente anterior
    sigue abierto, pero **no** puede recibir esta señal: el lote nuevo va a un
    estudio nuevo del paciente nuevo.
    """
    doctor = await make_doctor()
    first_patient = await make_patient(doctor)
    second_patient = await make_patient(doctor)
    device, api_key = await make_device(patient=first_patient, doctor=doctor)

    first = (await post_frames(client, device, api_key, build_frames(900))).json()

    # Reasignación: el equipo pasa al segundo paciente.
    device.patient_id = second_patient.id
    await db.flush()

    second = (await post_frames(client, device, api_key, build_frames(900, first_seq=500))).json()

    assert second["studyId"] != first["studyId"]
    new_study = await db.get(Study, second["studyId"])
    old_study = await db.get(Study, first["studyId"])
    assert new_study is not None and old_study is not None
    assert new_study.patient_id == second_patient.id
    assert old_study.patient_id == first_patient.id


async def test_a_second_device_for_the_same_patient_gets_its_own_study(
    client, s3, db, make_doctor, make_patient, make_device
) -> None:
    """La invariante de un equipo activo por paciente la sostiene el índice
    parcial `uq_device_active_patient`; la ingesta no la puede violar de costado
    creando estudios cruzados."""
    doctor = await make_doctor()
    patient = await make_patient(doctor)
    device_a, key_a = await make_device(patient=patient, doctor=doctor)

    first = (await post_frames(client, device_a, key_a, build_frames(900))).json()

    # Se libera el equipo y se asigna otro al mismo paciente.
    device_a.patient_id = None
    device_a.status = device_a.status.__class__.AVAILABLE
    await db.flush()
    device_b, key_b = await make_device(patient=patient, doctor=doctor)

    second = (await post_frames(client, device_b, key_b, build_frames(900))).json()

    assert second["studyId"] != first["studyId"]
    studies = (await db.scalars(select(Study))).all()
    assert {s.device_id for s in studies} == {device_a.id, device_b.id}


async def test_two_devices_of_different_patients_do_not_interfere(
    client, s3, db, make_patient, make_device
) -> None:
    patient_a = await make_patient()
    patient_b = await make_patient()
    device_a, key_a = await make_device(patient=patient_a)
    device_b, key_b = await make_device(patient=patient_b)

    body_a = (await post_frames(client, device_a, key_a, build_frames(900))).json()
    body_b = (await post_frames(client, device_b, key_b, build_frames(900))).json()

    assert body_a["studyId"] != body_b["studyId"]
    assert body_a["framesAccepted"] > 0 and body_b["framesAccepted"] > 0


async def test_the_batch_row_records_the_raw_time_anchor(
    client, s3, db, make_patient, make_device
) -> None:
    """Se guarda el ancla usada, no solo la hora derivada.

    Si más adelante resulta que un ancla estaba mal, con el crudo se recalcula
    todo; con el UTC ya derivado, no (INTEGRACION.md §5).
    """
    from app.db.models.ecg_batch import ECGBatch

    patient = await make_patient()
    device, api_key = await make_device(patient=patient)

    body = (
        await post_frames(client, device, api_key, build_frames(900), uptime_ms=1_234_567)
    ).json()

    batch = await db.get(ECGBatch, body["batchId"])
    assert batch is not None
    assert batch.device_uptime_ms == 1_234_567
    assert batch.epoch_anchor_ms is not None
    assert batch.boot_id == 0
    assert batch.first_seq == 0
    assert batch.study_id is not None
    assert batch.frames_s3_key is not None
    assert batch.compression_type == "rice-frame-v1"


async def test_a_legacy_study_with_a_blob_does_not_receive_batches(
    client, s3, db, make_patient, make_device, make_study
) -> None:
    """Un estudio seedeado y uno ingestado no se pueden fusionar.

    El seedeado tiene toda su señal en un blob (`ecg_s3_key`); el ingestado la
    tiene en segmentos y su pirámide se reconstruye solo desde ahí. Sumarle
    lotes a un estudio con blob dejaría `samples_count` contando muestras que la
    pirámide no cubre — el gráfico mostraría menos señal de la que dice tener.
    """
    patient = await make_patient()
    device, api_key = await make_device(patient=patient)
    legacy = await make_study(patient, device, status=StudyStatus.IN_PROGRESS)
    legacy.ecg_s3_key = f"studies/{legacy.id}/ecg.f32"
    legacy.samples_count = 150_000
    await db.flush()

    body = (await post_frames(client, device, api_key, build_frames(900))).json()

    assert body["studyId"] != str(legacy.id)
    fresh = await db.get(Study, body["studyId"])
    assert fresh is not None
    assert fresh.ecg_s3_key is None
    assert fresh.samples_count == 0  # todavía sin procesar, pero limpio


# --------------------------------------------------------------------------- #
# Telemetría del paciente
# --------------------------------------------------------------------------- #
#
# El equipo no es lo único que tiene estado: el paciente también, y hasta acá
# nadie se lo escribía. `study_status` se quedaba en el valor del alta y
# `last_data_received_at` en NULL para siempre. El dashboard cuenta pacientes
# por esos campos, así que el KPI "Pacientes activos" daba 0 con estudios
# corriendo y el widget "Requieren atención" salía vacío.


async def test_an_accepted_batch_marks_the_patient_as_active(
    client, s3, db, make_patient, make_device
) -> None:
    from app.db.models.patient import PatientStudyStatus

    patient = await make_patient(study_status=PatientStudyStatus.NONE)
    device, api_key = await make_device(patient=patient)

    await post_frames(client, device, api_key, build_frames(900))

    await db.refresh(patient)
    assert patient.study_status is PatientStudyStatus.ACTIVE


async def test_an_accepted_batch_stamps_last_data_received_at(
    client, s3, db, make_patient, make_device
) -> None:
    patient = await make_patient(last_data_received_at=None)
    device, api_key = await make_device(patient=patient)

    await post_frames(client, device, api_key, build_frames(900))

    await db.refresh(patient)
    assert patient.last_data_received_at is not None
    # La misma hora de recepción que se le escribe al equipo: son el mismo lote.
    await db.refresh(device)
    assert patient.last_data_received_at == device.last_seen_at
