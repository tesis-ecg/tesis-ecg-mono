"""Cierre y cancelación de estudios.

Antes de esto un estudio no tenía final: la ingesta lo abría en `IN_PROGRESS` y
ninguna capa lo cerraba nunca. Estos tests fijan las dos formas de terminarlo
—la explícita del médico y la implícita de sacarle el chaleco al paciente— y la
regla que las une: `patient.study_status` tiene que quedar de acuerdo con lo que
diga la tabla `study`.
"""

from datetime import UTC, datetime, timedelta

from sqlalchemy import select

from app.db.models.patient import PatientStudyStatus
from app.db.models.study import Study, StudyStatus
from app.db.models.user import User, UserRole


async def _doctor_user(db, doctor) -> User:  # type: ignore[no-untyped-def]
    """El `User` detrás de un `Doctor`.

    No se puede usar `doctor.user`: la relación no está cargada y en async
    tocarla dispara MissingGreenlet.
    """
    user = await db.get(User, doctor.user_id)
    assert user is not None
    return user


# --------------------------------------------------------------------------- #
# Cierre explícito
# --------------------------------------------------------------------------- #


async def test_complete_closes_the_study_and_stamps_the_end(
    db, as_user, make_doctor, make_patient, make_device, make_study
) -> None:
    doctor = await make_doctor()
    patient = await make_patient(doctor=doctor)
    device, _ = await make_device(patient=patient)
    study = await make_study(patient, device)
    await db.commit()

    response = await as_user(await _doctor_user(db, doctor)).post(f"/studies/{study.id}/complete")

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "completed"
    assert body["endedAt"] is not None
    assert body["durationMs"] > 0


async def test_complete_marks_the_patient_as_completed(
    db, as_user, make_doctor, make_patient, make_device, make_study
) -> None:
    doctor = await make_doctor()
    patient = await make_patient(doctor=doctor, study_status=PatientStudyStatus.ACTIVE)
    device, _ = await make_device(patient=patient)
    study = await make_study(patient, device)
    await db.commit()

    response = await as_user(await _doctor_user(db, doctor)).post(f"/studies/{study.id}/complete")
    assert response.status_code == 200

    await db.refresh(patient)
    assert patient.study_status is PatientStudyStatus.COMPLETED


async def test_cancel_leaves_the_patient_without_a_study(
    db, as_user, make_doctor, make_patient, make_device, make_study
) -> None:
    doctor = await make_doctor()
    patient = await make_patient(doctor=doctor, study_status=PatientStudyStatus.ACTIVE)
    device, _ = await make_device(patient=patient)
    study = await make_study(patient, device)
    await db.commit()

    response = await as_user(await _doctor_user(db, doctor)).post(f"/studies/{study.id}/cancel")

    assert response.status_code == 200
    assert response.json()["status"] == "cancelled"
    await db.refresh(patient)
    assert patient.study_status is PatientStudyStatus.NONE


async def test_a_second_close_is_a_conflict_not_a_silent_ok(
    db, as_user, make_doctor, make_patient, make_device, make_study
) -> None:
    """Cerrar dos veces tiene que doler.

    Un 200 idempotente escondería el caso real: el médico cree que cerró el
    estudio que está mirando y en realidad cerró otro hace rato.
    """
    doctor = await make_doctor()
    patient = await make_patient(doctor=doctor)
    device, _ = await make_device(patient=patient)
    study = await make_study(patient, device)
    await db.commit()

    client = as_user(await _doctor_user(db, doctor))
    assert (await client.post(f"/studies/{study.id}/complete")).status_code == 200

    second = await client.post(f"/studies/{study.id}/complete")
    assert second.status_code == 409
    assert second.json()["code"] == "STUDY_NOT_OPEN"


async def test_a_scheduled_study_can_be_cancelled_but_not_completed(
    db, as_user, make_doctor, make_patient, make_device, make_study
) -> None:
    doctor = await make_doctor()
    patient = await make_patient(doctor=doctor)
    device, _ = await make_device(patient=patient)
    # `started_at` en el futuro, como los estudios programados del seed.
    study = await make_study(
        patient,
        device,
        status=StudyStatus.SCHEDULED,
        started_at=datetime.now(UTC) + timedelta(days=3),
    )
    await db.commit()

    client = as_user(await _doctor_user(db, doctor))
    rejected = await client.post(f"/studies/{study.id}/complete")
    assert rejected.status_code == 409
    assert rejected.json()["code"] == "STUDY_NOT_STARTED"

    accepted = await client.post(f"/studies/{study.id}/cancel")
    assert accepted.status_code == 200
    # El CHECK `ck_study_time_range` exige ended_at >= started_at: un programado
    # se colapsa contra su propio inicio en vez de cerrarse "en el pasado".
    assert accepted.json()["durationMs"] == 0


async def test_a_doctor_cannot_close_another_doctors_study(
    db, as_user, make_doctor, make_patient, make_device, make_study
) -> None:
    owner = await make_doctor()
    intruder = await make_doctor()
    patient = await make_patient(doctor=owner)
    device, _ = await make_device(patient=patient)
    study = await make_study(patient, device)
    await db.commit()

    response = await as_user(await _doctor_user(db, intruder)).post(f"/studies/{study.id}/complete")

    # 404 y no 403: un 403 confirmaría que el estudio existe.
    assert response.status_code == 404


# --------------------------------------------------------------------------- #
# Cierre implícito: mover el Holter
# --------------------------------------------------------------------------- #


async def test_unassigning_the_holter_closes_the_open_study(
    db, as_user, make_doctor, make_patient, make_device, make_study
) -> None:
    doctor = await make_doctor()
    patient = await make_patient(doctor=doctor, study_status=PatientStudyStatus.ACTIVE)
    device, _ = await make_device(patient=patient)
    study = await make_study(patient, device)
    await db.commit()

    response = await as_user(await _doctor_user(db, doctor)).post(f"/devices/{device.id}/unassign")
    assert response.status_code == 200

    await db.refresh(study)
    await db.refresh(patient)
    # Sacarle el chaleco al paciente ES la forma normal en que termina un Holter.
    assert study.status is StudyStatus.COMPLETED
    assert study.ended_at is not None
    assert patient.study_status is PatientStudyStatus.COMPLETED


async def test_reassigning_to_another_patient_closes_the_previous_study(
    db, as_user, make_doctor, make_patient, make_device, make_study
) -> None:
    doctor = await make_doctor()
    first = await make_patient(doctor=doctor)
    second = await make_patient(doctor=doctor)
    device, _ = await make_device(patient=first)
    study = await make_study(first, device)
    await db.commit()

    response = await as_user(await _doctor_user(db, doctor)).post(
        f"/devices/{device.id}/reassign", json={"patientId": str(second.id)}
    )
    assert response.status_code == 200

    await db.refresh(study)
    assert study.status is StudyStatus.COMPLETED


async def test_reassigning_to_the_same_patient_does_not_interrupt_the_study(
    db, as_user, make_doctor, make_patient, make_device, make_study
) -> None:
    """Reasignar al mismo paciente es un no-op, no una interrupción."""
    doctor = await make_doctor()
    patient = await make_patient(doctor=doctor)
    device, _ = await make_device(patient=patient)
    study = await make_study(patient, device)
    await db.commit()

    response = await as_user(await _doctor_user(db, doctor)).post(
        f"/devices/{device.id}/reassign", json={"patientId": str(patient.id)}
    )
    assert response.status_code == 200

    await db.refresh(study)
    assert study.status is StudyStatus.IN_PROGRESS


async def test_retiring_the_holter_closes_the_open_study(
    db, as_user, make_user, make_doctor, make_patient, make_device, make_study
) -> None:
    admin = await make_user(UserRole.ADMIN)
    doctor = await make_doctor()
    patient = await make_patient(doctor=doctor)
    device, _ = await make_device(patient=patient)
    study = await make_study(patient, device)
    await db.commit()

    response = await as_user(admin).delete(f"/devices/{device.id}")
    assert response.status_code == 200

    await db.refresh(study)
    assert study.status is StudyStatus.COMPLETED


async def test_deleting_the_patient_cancels_their_open_studies(
    db, as_user, make_doctor, make_patient, make_device, make_study
) -> None:
    """La baja de un paciente no es el final normal de un Holter: se cancela.

    Sin esto, borrar un paciente le desasignaba los equipos pero dejaba los
    estudios "en curso": filas invisibles en la UI que igual seguían contando en
    el dashboard.
    """
    doctor = await make_doctor()
    patient = await make_patient(doctor=doctor)
    device, _ = await make_device(patient=patient)
    study = await make_study(patient, device)
    await db.commit()

    response = await as_user(await _doctor_user(db, doctor)).delete(f"/patients/{patient.id}")
    assert response.status_code in (200, 204)

    refreshed = await db.scalar(select(Study).where(Study.id == study.id))
    assert refreshed is not None
    assert refreshed.status is StudyStatus.CANCELLED


async def test_unassigning_a_holter_without_an_open_study_is_a_noop(
    db, as_user, make_doctor, make_patient, make_device
) -> None:
    doctor = await make_doctor()
    patient = await make_patient(doctor=doctor)
    device, _ = await make_device(patient=patient)
    await db.commit()

    response = await as_user(await _doctor_user(db, doctor)).post(f"/devices/{device.id}/unassign")

    assert response.status_code == 200
    assert response.json()["assignedPatientId"] is None
