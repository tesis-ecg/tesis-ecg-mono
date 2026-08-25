import uuid
from datetime import UTC, datetime, timedelta

from fastapi import HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.s3 import build_presigned_url as _build_presigned_ecg_url
from app.db.models.audit_event import AuditEventType
from app.db.models.device import Device
from app.db.models.patient import Patient, PatientStudyStatus
from app.db.models.study import Study, StudyStatus
from app.modules.auth import auth_repository as auth_repo
from app.modules.studies import studies_repository as repo
from app.modules.studies.studies_schemas import (
    PatientStudiesInput,
    PatientStudiesResponse,
    PatientStudyOut,
    StudyDetailOut,
    StudyEcgLevelOut,
    StudyEcgManifestOut,
    StudyEcgObjectOut,
    StudyEcgOut,
    StudyEcgSegmentOut,
    StudyIdInput,
    StudyListInput,
    StudyListResponse,
)

MAX_LEGACY_ECG_BYTES = 5 * 1024 * 1024


def _duration_ms(study: Study) -> int:
    if study.duration_ms is not None:
        return study.duration_ms
    if study.ended_at is not None:
        return int((study.ended_at - study.started_at).total_seconds() * 1000)
    if study.status == StudyStatus.IN_PROGRESS:
        return max(int((datetime.now(UTC) - study.started_at).total_seconds() * 1000), 0)
    return 0


def _duration_hours(study: Study) -> float | None:
    duration = _duration_ms(study)
    if duration == 0 and study.ended_at is None:
        return None
    return round(duration / 3_600_000, 2)


def _patient_study_out(study: Study) -> PatientStudyOut:
    return PatientStudyOut(
        id=study.id,
        patientId=study.patient_id,
        startedAt=study.started_at,
        endedAt=study.ended_at,
        durationHours=_duration_hours(study),
        status=study.status,
        deviceId=study.device_id,
        samplesCount=study.samples_count,
        eventsCount=study.events_count,
    )


def _study_detail_out(
    study: Study, patient: Patient, device: Device, doctor_name: str | None
) -> StudyDetailOut:
    return StudyDetailOut(
        id=study.id,
        patientId=patient.id,
        patientName=f"{patient.first_name} {patient.last_name}".strip(),
        startedAt=study.started_at,
        endedAt=study.ended_at,
        durationMs=_duration_ms(study),
        deviceSerial=device.serial_number,
        status=study.status,
        doctorId=patient.doctor_id,
        doctorName=doctor_name,
    )


def _not_found() -> HTTPException:
    return HTTPException(
        status_code=404,
        detail={"code": "STUDY_NOT_FOUND", "message": "Estudio no encontrado."},
    )


def _not_open() -> HTTPException:
    """409 y no un 200 silencioso.

    Cerrar un estudio es un acto clínico: si el médico lo pide dos veces, la
    segunda es o un doble click o una confusión sobre qué estudio está mirando.
    Responder "listo" a las dos esconde el problema.
    """
    return HTTPException(
        status_code=409,
        detail={
            "code": "STUDY_NOT_OPEN",
            "message": "El estudio ya está cerrado.",
        },
    )


def _not_started() -> HTTPException:
    return HTTPException(
        status_code=409,
        detail={
            "code": "STUDY_NOT_STARTED",
            "message": "Un estudio programado todavía no grabó nada: solo se puede cancelar.",
        },
    )


async def list_studies(input_data: StudyListInput, db: AsyncSession) -> StudyListResponse:
    rows, total = await repo.list_studies(
        db,
        doctor_id=input_data.doctor_id,
        q=input_data.q,
        statuses=input_data.status,
        limit=input_data.limit,
        offset=input_data.offset,
    )
    return StudyListResponse(
        items=[
            _study_detail_out(study, patient, device, doctor_name)
            for study, patient, device, doctor_name in rows
        ],
        total=total,
        limit=input_data.limit,
        offset=input_data.offset,
    )


async def list_patient_studies(
    input_data: PatientStudiesInput, db: AsyncSession
) -> PatientStudiesResponse:
    result = await repo.list_for_patient(db, input_data.patient_id, input_data.doctor_id)
    if result is None:
        raise HTTPException(
            status_code=404,
            detail={"code": "PATIENT_NOT_FOUND", "message": "Paciente no encontrado."},
        )
    studies, total = result
    return PatientStudiesResponse(
        items=[_patient_study_out(study) for study in studies], total=total
    )


async def get_study(input_data: StudyIdInput, db: AsyncSession) -> StudyDetailOut:
    result = await repo.get_detail(db, input_data.study_id, input_data.doctor_id)
    if result is None:
        raise _not_found()
    study, patient, device, doctor_name = result
    return _study_detail_out(study, patient, device, doctor_name)


async def get_study_ecg(input_data: StudyIdInput, db: AsyncSession) -> StudyEcgOut:
    result = await repo.get_detail(db, input_data.study_id, input_data.doctor_id)
    if result is None:
        raise _not_found()
    study, _, _, _ = result
    if study.ecg_s3_key is None:
        raise HTTPException(
            status_code=404,
            detail={"code": "ECG_NOT_FOUND", "message": "ECG no disponible para este estudio."},
        )
    byte_length = study.ecg_byte_length or study.samples_count * 4
    if byte_length > MAX_LEGACY_ECG_BYTES:
        raise HTTPException(
            status_code=413,
            detail={
                "code": "ECG_LEGACY_TOO_LARGE",
                "message": "Este ECG requiere el protocolo de manifest.",
            },
        )
    expires_at = datetime.now(UTC) + timedelta(seconds=settings.s3_presign_expire_seconds)
    if input_data.actor_id is not None:
        await auth_repo.log_audit_event(
            db,
            AuditEventType.ECG_ACCESSED,
            user_id=input_data.actor_id,
            metadata={"target_study_id": str(study.id), "protocol": "legacy"},
        )
        await db.commit()
    return StudyEcgOut(
        url=_build_presigned_ecg_url(study.ecg_s3_key),
        sampleRate=study.sample_rate,
        startTimestamp=int(study.started_at.timestamp() * 1000),
        durationMs=_duration_ms(study),
        sampleCount=study.samples_count,
        expiresAt=expires_at,
    )


async def get_study_ecg_manifest(input_data: StudyIdInput, db: AsyncSession) -> StudyEcgManifestOut:
    result = await repo.get_detail(db, input_data.study_id, input_data.doctor_id)
    if result is None:
        raise _not_found()
    study, _, _, _ = result
    # Un estudio ingestado no tiene `ecg_s3_key`: su señal vive en segmentos. Lo
    # que define "hay ECG" es que exista alguna de las dos formas.
    if study.ecg_s3_key is None and not study.ecg_segments:
        raise HTTPException(
            status_code=404,
            detail={"code": "ECG_NOT_FOUND", "message": "ECG no disponible para este estudio."},
        )

    expires_at = datetime.now(UTC) + timedelta(seconds=settings.s3_presign_expire_seconds)
    levels = [
        StudyEcgLevelOut(
            url=_build_presigned_ecg_url(str(level["key"])),
            expiresAt=expires_at,
            byteLength=int(level["byteLength"]),
            sha256=str(level["sha256"]),
            samplesPerBucket=int(level["samplesPerBucket"]),
            pointCount=int(level["pointCount"]),
        )
        for level in study.ecg_pyramid_levels
    ]
    segments = [
        StudyEcgSegmentOut(
            url=_build_presigned_ecg_url(str(segment["key"])),
            expiresAt=expires_at,
            byteLength=int(segment["byteLength"]),
            sha256=str(segment["sha256"]),
            startSampleIndex=int(segment["startSampleIndex"]),
            sampleCount=int(segment["sampleCount"]),
        )
        for segment in study.ecg_segments
    ]
    raw = (
        StudyEcgObjectOut(
            url=_build_presigned_ecg_url(study.ecg_s3_key),
            expiresAt=expires_at,
            byteLength=study.ecg_byte_length or study.samples_count * 4,
            sha256=study.ecg_sha256,
        )
        if study.ecg_s3_key is not None
        else None
    )
    if input_data.actor_id is not None:
        await auth_repo.log_audit_event(
            db,
            AuditEventType.ECG_ACCESSED,
            user_id=input_data.actor_id,
            metadata={"target_study_id": str(study.id), "protocol": "manifest-v1"},
        )
        await db.commit()
    return StudyEcgManifestOut(
        encoding=study.ecg_encoding,
        sampleRate=study.sample_rate,
        sampleCount=study.samples_count,
        startTimestamp=int(study.started_at.timestamp() * 1000),
        durationMs=_duration_ms(study),
        status=study.status,
        isSimulated=study.is_simulated,
        raw=raw,
        levels=levels,
        segments=segments,
    )


# --- Ciclo de vida ---------------------------------------------------------- #
#
# Hasta acá el estudio no tenía final: la ingesta lo abría en `IN_PROGRESS` y
# nadie lo cerraba nunca. Un Holter se saca del paciente y el estudio quedaba
# "en curso" para siempre, contaminando el listado, el dashboard y el visor.


def _close(study: Study, status: StudyStatus) -> None:
    """Aplica el cierre sobre la fila. No commitea: la transacción es del caller."""
    now = datetime.now(UTC)
    # El CHECK `ck_study_time_range` exige `ended_at >= started_at`. Un estudio
    # programado tiene `started_at` en el futuro, así que cerrarlo con la hora
    # actual violaría la constraint: se colapsa contra su propio inicio.
    study.ended_at = max(now, study.started_at)
    study.duration_ms = int((study.ended_at - study.started_at).total_seconds() * 1000)
    study.status = status


async def _sync_patient_status(db: AsyncSession, patient: Patient, closed_as: StudyStatus) -> None:
    """Deja `patient.study_status` de acuerdo con lo que quedó en `study`.

    La columna es un cache de estado que la UI usa para filtrar y que el
    dashboard cuenta; si no se mantiene acá, vuelve a quedar congelada.
    """
    if await repo.has_open_study(db, patient.id):
        # Le queda otro estudio abierto (otro equipo, o un huérfano anterior al
        # cierre automático): el paciente sigue en seguimiento.
        patient.study_status = PatientStudyStatus.ACTIVE
        return
    patient.study_status = (
        PatientStudyStatus.COMPLETED
        if closed_as is StudyStatus.COMPLETED
        else PatientStudyStatus.NONE
    )


async def _transition(
    input_data: StudyIdInput, db: AsyncSession, target: StudyStatus
) -> StudyDetailOut:
    row = await repo.get_for_update(db, input_data.study_id, input_data.doctor_id)
    if row is None:
        raise _not_found()
    study, patient = row

    if study.status not in repo.OPEN_STATUSES:
        raise _not_open()
    if study.status is StudyStatus.SCHEDULED and target is StudyStatus.COMPLETED:
        raise _not_started()

    _close(study, target)
    await _sync_patient_status(db, patient, target)
    await auth_repo.log_audit_event(
        db,
        AuditEventType.STUDY_COMPLETED
        if target is StudyStatus.COMPLETED
        else AuditEventType.STUDY_CANCELLED,
        user_id=input_data.actor_id,
        metadata={"target_study_id": str(study.id), "patient_id": str(patient.id)},
    )
    await db.commit()

    result = await repo.get_detail(db, input_data.study_id, input_data.doctor_id)
    if result is None:  # pragma: no cover - la fila se acaba de commitear
        raise _not_found()
    return _study_detail_out(*result)


async def complete_study(input_data: StudyIdInput, db: AsyncSession) -> StudyDetailOut:
    return await _transition(input_data, db, StudyStatus.COMPLETED)


async def cancel_study(input_data: StudyIdInput, db: AsyncSession) -> StudyDetailOut:
    return await _transition(input_data, db, StudyStatus.CANCELLED)


async def close_open_studies_for_device(
    db: AsyncSession, device: Device, actor_id: uuid.UUID | None, reason: str
) -> int:
    """Cierra los estudios abiertos del equipo. **No commitea.**

    La llama `devices_service` al desasignar, reasignar o retirar un Holter, y
    tiene que ser **antes** de soltar `device.patient_id`: una vez desasignado
    ya no hay forma de saber de qué paciente era el estudio.

    Se cierran como `COMPLETED` y no como `CANCELLED` porque sacarle el chaleco
    al paciente **es** la forma normal en que termina un Holter. Cancelar queda
    para la decisión explícita del médico (colocación fallida, datos de banco).

    Vive acá y no en `devices_service` para que la regla de cierre —qué estados
    son cerrables, cómo queda `duration_ms`, cómo se sincroniza el paciente—
    tenga un solo dueño.
    """
    if device.patient_id is None:
        return 0
    studies = await repo.list_open_for_device(db, device.patient_id, device.id)
    if not studies:
        return 0

    patient = await repo.get_patient_for_update(db, device.patient_id)
    for study in studies:
        _close(study, StudyStatus.COMPLETED)
        await auth_repo.log_audit_event(
            db,
            AuditEventType.STUDY_COMPLETED,
            user_id=actor_id,
            metadata={
                "target_study_id": str(study.id),
                "patient_id": str(device.patient_id),
                "device_id": str(device.id),
                "reason": reason,
            },
        )
    if patient is not None:
        await _sync_patient_status(db, patient, StudyStatus.COMPLETED)
    return len(studies)


async def close_open_studies_for_patient(
    db: AsyncSession, patient: Patient, actor_id: uuid.UUID | None, reason: str
) -> int:
    """Cierra todos los estudios abiertos del paciente. **No commitea.**

    La llama `patients_service` al dar de baja un paciente. Sin esto, borrar un
    paciente le desasignaba los equipos pero dejaba sus estudios "en curso":
    filas invisibles en la UI (el listado filtra por paciente activo) que igual
    seguían contando en el dashboard.
    """
    studies = await repo.list_open_for_patient(db, patient.id)
    for study in studies:
        _close(study, StudyStatus.CANCELLED)
        await auth_repo.log_audit_event(
            db,
            AuditEventType.STUDY_CANCELLED,
            user_id=actor_id,
            metadata={
                "target_study_id": str(study.id),
                "patient_id": str(patient.id),
                "reason": reason,
            },
        )
    patient.study_status = PatientStudyStatus.NONE
    return len(studies)
