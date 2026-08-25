"""Listado y acuse de alertas.

La ingesta ya generaba filas en `alert`, pero el único lugar donde se veían era
un widget de solo lectura del dashboard. `seen_at`, `acknowledged_at` y
`acknowledged_by` existían en el modelo desde el schema inicial y nadie los
escribía: no había forma de que un médico dijera "esta ya la vi".
"""

import uuid
from datetime import UTC, datetime, timedelta

from app.db.models.alert import Alert, AlertSeverity
from app.db.models.ecg_batch import ECGBatch
from app.db.models.ecg_event import ECGEvent, ECGEventSeverity, ECGEventType
from app.db.models.study import StudyStatus
from app.db.models.user import User, UserRole


async def _doctor_user(db, doctor) -> User:  # type: ignore[no-untyped-def]
    user = await db.get(User, doctor.user_id)
    assert user is not None
    return user


async def _make_alert(  # type: ignore[no-untyped-def]
    db,
    patient,
    device,
    severity: AlertSeverity = AlertSeverity.HIGH,
    event_type: ECGEventType = ECGEventType.TACHYCARDIA,
    event_metadata: dict[str, object] | None = None,
    message: str = "Taquicardia sostenida",
    study=None,
) -> Alert:
    """`batch → event → alert`, que es la cadena que arma la ingesta."""
    batch = ECGBatch(
        device_id=device.id,
        study_id=study.id if study is not None else None,
        received_at=datetime.now(UTC),
        batch_timestamp=int(datetime.now(UTC).timestamp() * 1000),
        duration_seconds=60,
        sample_rate=500,
        num_channels=1,
        num_samples=30_000,
        compression_type="rice",
        s3_key=f"test/{uuid.uuid4().hex}.bin",
    )
    db.add(batch)
    await db.flush()

    event = ECGEvent(
        batch_id=batch.id,
        event_type=event_type,
        severity=ECGEventSeverity.HIGH,
        timestamp_in_recording=12.5,
        event_metadata=event_metadata or {},
    )
    db.add(event)
    await db.flush()

    alert = Alert(
        patient_id=patient.id,
        event_id=event.id,
        severity=severity,
        message=message,
    )
    db.add(alert)
    await db.flush()
    return alert


# --------------------------------------------------------------------------- #
# Listado
# --------------------------------------------------------------------------- #


async def test_the_list_resolves_the_patient_name_and_the_kind(
    db, as_user, make_doctor, make_patient, make_device
) -> None:
    doctor = await make_doctor()
    patient = await make_patient(doctor=doctor, first_name="Ana", last_name="Pérez")
    device, _ = await make_device(patient=patient)
    await _make_alert(db, patient, device)
    await db.commit()

    response = await as_user(await _doctor_user(db, doctor)).get("/alerts")

    assert response.status_code == 200
    body = response.json()
    assert body["total"] == 1
    assert body["pendingTotal"] == 1
    item = body["items"][0]
    assert item["patientName"] == "Ana Pérez"
    # Minúsculas: el ORM guarda TACHYCARDIA/HIGH y el front espera el slug.
    assert item["kind"] == "tachycardia"
    assert item["severity"] == "high"
    assert item["acknowledgedAt"] is None


async def test_the_alert_links_to_the_study_of_its_batch(
    db, as_user, make_doctor, make_patient, make_device, make_study
) -> None:
    """El batch es el vínculo exacto y gana sobre cualquier heurística temporal."""
    doctor = await make_doctor()
    patient = await make_patient(doctor=doctor)
    device, _ = await make_device(patient=patient)
    ingested = await make_study(
        patient,
        device,
        started_at=datetime.now(UTC) - timedelta(days=5),
        ended_at=datetime.now(UTC) - timedelta(days=4),
        status=StudyStatus.COMPLETED,
    )
    # Un estudio abierto que sí cubre el instante de la alerta: si ganara la
    # ventana temporal, el link llevaría a este y no al del batch.
    await make_study(patient, device, started_at=datetime.now(UTC) - timedelta(hours=1))
    await _make_alert(db, patient, device, study=ingested)
    await db.commit()

    body = (await as_user(await _doctor_user(db, doctor)).get("/alerts")).json()

    assert body["items"][0]["studyId"] == str(ingested.id)


async def test_an_alert_outside_every_study_window_still_links_to_the_last_one(
    db, as_user, make_doctor, make_patient, make_device, make_study
) -> None:
    """Sin esto la fila queda muerta: no se puede abrir la señal desde la alerta.

    Pasa con los batches que llegan sin `study_id` y con las alertas cuyo
    `created_at` cae fuera de la ventana de todo estudio del paciente (las que
    siembra el seed, por ejemplo).
    """
    doctor = await make_doctor()
    patient = await make_patient(doctor=doctor)
    device, _ = await make_device(patient=patient)
    closed = await make_study(
        patient,
        device,
        started_at=datetime.now(UTC) - timedelta(days=6),
        ended_at=datetime.now(UTC) - timedelta(days=5),
        status=StudyStatus.COMPLETED,
    )
    await _make_alert(db, patient, device)
    await db.commit()
    client = as_user(await _doctor_user(db, doctor))

    inbox = (await client.get("/alerts")).json()["items"]
    dashboard = (await client.get("/dashboard/alerts")).json()

    assert inbox[0]["studyId"] == str(closed.id)
    # El widget del dashboard usa la misma correlación y tiene que coincidir.
    assert dashboard[0]["studyId"] == str(closed.id)


async def test_symptom_marker_keeps_its_clinical_kind_in_alerts_and_dashboard(
    db, as_user, make_doctor, make_patient, make_device
) -> None:
    doctor = await make_doctor()
    patient = await make_patient(doctor=doctor)
    device, _ = await make_device(patient=patient)
    alert = await _make_alert(
        db,
        patient,
        device,
        event_type=ECGEventType.OTHER,
        event_metadata={"kind": "symptom_marker"},
        message="El paciente marcó un síntoma.",
    )
    await db.commit()
    client = as_user(await _doctor_user(db, doctor))

    inbox = (await client.get("/alerts")).json()["items"]
    dashboard = (await client.get("/dashboard/alerts")).json()
    acknowledged = (await client.post(f"/alerts/{alert.id}/acknowledge")).json()

    assert inbox[0]["kind"] == "symptom_marker"
    assert any(item["kind"] == "symptom_marker" for item in dashboard)
    assert acknowledged["kind"] == "symptom_marker"


async def test_other_without_metadata_is_a_generic_finding(
    db, as_user, make_doctor, make_patient, make_device
) -> None:
    doctor = await make_doctor()
    patient = await make_patient(doctor=doctor)
    device, _ = await make_device(patient=patient)
    await _make_alert(db, patient, device, event_type=ECGEventType.OTHER)
    await db.commit()

    body = (await as_user(await _doctor_user(db, doctor)).get("/alerts")).json()

    assert body["items"][0]["kind"] == "other"


async def test_a_doctor_only_sees_their_own_patients_alerts(
    db, as_user, make_doctor, make_patient, make_device
) -> None:
    owner = await make_doctor()
    other = await make_doctor()
    patient = await make_patient(doctor=owner)
    device, _ = await make_device(patient=patient)
    await _make_alert(db, patient, device)
    await db.commit()

    response = await as_user(await _doctor_user(db, other)).get("/alerts")

    assert response.status_code == 200
    assert response.json()["total"] == 0


async def test_the_admin_sees_every_doctors_alerts(
    db, as_user, make_user, make_doctor, make_patient, make_device
) -> None:
    doctor = await make_doctor()
    patient = await make_patient(doctor=doctor)
    device, _ = await make_device(patient=patient)
    await _make_alert(db, patient, device)
    await db.commit()

    response = await as_user(await make_user(UserRole.ADMIN)).get("/alerts")

    assert response.status_code == 200
    assert response.json()["total"] == 1


async def test_critical_alerts_come_first(
    db, as_user, make_doctor, make_patient, make_device
) -> None:
    doctor = await make_doctor()
    patient = await make_patient(doctor=doctor)
    device, _ = await make_device(patient=patient)
    await _make_alert(db, patient, device, severity=AlertSeverity.LOW)
    await _make_alert(db, patient, device, severity=AlertSeverity.CRITICAL)
    await _make_alert(db, patient, device, severity=AlertSeverity.MEDIUM)
    await db.commit()

    response = await as_user(await _doctor_user(db, doctor)).get("/alerts")

    severities = [item["severity"] for item in response.json()["items"]]
    assert severities == ["critical", "medium", "low"]


async def test_the_acknowledged_filter_splits_the_list(
    db, as_user, make_doctor, make_patient, make_device
) -> None:
    doctor = await make_doctor()
    patient = await make_patient(doctor=doctor)
    device, _ = await make_device(patient=patient)
    pending = await _make_alert(db, patient, device)
    await _make_alert(db, patient, device)
    await db.commit()

    client = as_user(await _doctor_user(db, doctor))
    await client.post(f"/alerts/{pending.id}/acknowledge")

    assert (await client.get("/alerts", params={"acknowledged": False})).json()["total"] == 1
    assert (await client.get("/alerts", params={"acknowledged": True})).json()["total"] == 1


async def test_pending_total_ignores_the_filter(
    db, as_user, make_doctor, make_patient, make_device
) -> None:
    """El badge del menú cuenta lo que queda, no lo que se está mirando."""
    doctor = await make_doctor()
    patient = await make_patient(doctor=doctor)
    device, _ = await make_device(patient=patient)
    await _make_alert(db, patient, device)
    acknowledged = await _make_alert(db, patient, device)
    await db.commit()

    client = as_user(await _doctor_user(db, doctor))
    await client.post(f"/alerts/{acknowledged.id}/acknowledge")

    body = (await client.get("/alerts", params={"acknowledged": True})).json()
    assert body["total"] == 1
    assert body["pendingTotal"] == 1


# --------------------------------------------------------------------------- #
# Acuse
# --------------------------------------------------------------------------- #


async def test_acknowledging_stamps_who_and_when(
    db, as_user, make_doctor, make_patient, make_device
) -> None:
    doctor = await make_doctor()
    user = await _doctor_user(db, doctor)
    patient = await make_patient(doctor=doctor)
    device, _ = await make_device(patient=patient)
    alert = await _make_alert(db, patient, device)
    await db.commit()

    response = await as_user(user).post(f"/alerts/{alert.id}/acknowledge")

    assert response.status_code == 200
    body = response.json()
    assert body["acknowledgedAt"] is not None
    assert body["acknowledgedByName"] == user.full_name
    # `seen_at` se completa solo: no se puede atender algo sin haberlo visto.
    assert body["seenAt"] is not None


async def test_acknowledging_twice_is_a_conflict(
    db, as_user, make_doctor, make_patient, make_device
) -> None:
    """El segundo acuse no puede pisar quién fue el primero en verla."""
    doctor = await make_doctor()
    patient = await make_patient(doctor=doctor)
    device, _ = await make_device(patient=patient)
    alert = await _make_alert(db, patient, device)
    await db.commit()

    client = as_user(await _doctor_user(db, doctor))
    assert (await client.post(f"/alerts/{alert.id}/acknowledge")).status_code == 200

    second = await client.post(f"/alerts/{alert.id}/acknowledge")
    assert second.status_code == 409
    assert second.json()["code"] == "ALERT_ALREADY_ACKNOWLEDGED"


async def test_a_doctor_cannot_acknowledge_another_doctors_alert(
    db, as_user, make_doctor, make_patient, make_device
) -> None:
    owner = await make_doctor()
    intruder = await make_doctor()
    patient = await make_patient(doctor=owner)
    device, _ = await make_device(patient=patient)
    alert = await _make_alert(db, patient, device)
    await db.commit()

    response = await as_user(await _doctor_user(db, intruder)).post(
        f"/alerts/{alert.id}/acknowledge"
    )

    assert response.status_code == 404
