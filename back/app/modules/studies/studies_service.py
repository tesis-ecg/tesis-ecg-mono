import math
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any, Literal

from fastapi import HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.s3 import build_presigned_url as _build_presigned_ecg_url
from app.db.models.audit_event import AuditEventType
from app.db.models.device import Device
from app.db.models.ecg_event import ECGEvent, ECGEventSeverity, ECGEventType
from app.db.models.patient import Patient, PatientStudyStatus
from app.db.models.patient_report import PatientReport
from app.db.models.study import Study, StudyStatus
from app.modules.auth import auth_repository as auth_repo
from app.modules.patient_app import patient_app_repository as patient_app_repo
from app.modules.patient_app.catalogs import activity_label, symptom_label
from app.modules.studies import studies_repository as repo
from app.modules.studies.studies_schemas import (
    PatientStudiesInput,
    PatientStudiesResponse,
    PatientStudyOut,
    StudyDetailOut,
    StudyEcgAnnotationOut,
    StudyEcgLevelOut,
    StudyEcgManifestOut,
    StudyEcgObjectOut,
    StudyEcgOut,
    StudyEcgSegmentOut,
    StudyIdInput,
    StudyListInput,
    StudyListResponse,
    StudyPatientReportOut,
    StudyPatientReportsResponse,
)

MAX_LEGACY_ECG_BYTES = 5 * 1024 * 1024

_SIGNAL_QUALITY_KINDS = {
    "noise",
    "lead_off",
    "sqi_unanalyzable",
    "adc_saturated",
}
_CLINICAL_EVENT_TYPES = {
    ECGEventType.TACHYCARDIA,
    ECGEventType.BRADYCARDIA,
    ECGEventType.AFIB,
    ECGEventType.PVC,
    ECGEventType.PAUSE,
}
_ANNOTATION_SEVERITY: dict[ECGEventSeverity, Literal["low", "medium", "high", "critical"]] = {
    ECGEventSeverity.LOW: "low",
    ECGEventSeverity.MEDIUM: "medium",
    ECGEventSeverity.HIGH: "high",
    ECGEventSeverity.CRITICAL: "critical",
}


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


def _finite_number(value: object) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    number = float(value)
    return number if math.isfinite(number) else None


def _event_offsets_ms(event: ECGEvent, study: Study) -> tuple[int, int] | None:
    """Normaliza coordenadas nuevas y legacy al eje comprimido de muestras."""
    metadata: dict[str, Any] = event.event_metadata or {}
    start_sample = _finite_number(metadata.get("startSampleIndex"))
    sample_count = _finite_number(metadata.get("sampleCount"))
    duration_seconds = _finite_number(event.duration_seconds)

    if start_sample is not None:
        start_ms = start_sample * 1000 / study.sample_rate
        if sample_count is not None:
            end_ms = (start_sample + max(sample_count, 0)) * 1000 / study.sample_rate
        else:
            end_ms = start_ms + max(duration_seconds or 0, 0) * 1000
    else:
        offset_seconds = _finite_number(metadata.get("offsetInStudySeconds"))
        if offset_seconds is None:
            offset_seconds = _finite_number(event.timestamp_in_recording)
        if offset_seconds is None:
            return None
        start_ms = offset_seconds * 1000
        end_ms = start_ms + max(duration_seconds or 0, 0) * 1000

    recording_duration_ms = study.samples_count * 1000 / study.sample_rate
    clipped_start = min(max(start_ms, 0), recording_duration_ms)
    clipped_end = min(max(end_ms, clipped_start), recording_duration_ms)
    return round(clipped_start), round(clipped_end)


def _annotation_kind(event: ECGEvent) -> str:
    kind = (event.event_metadata or {}).get("kind")
    if isinstance(kind, str) and kind.strip():
        return kind.strip().lower()
    return event.event_type.value.lower()


def _annotation_category(
    event: ECGEvent, kind: str
) -> Literal["signal_quality", "clinical", "patient_marker", "technical"]:
    if kind == "symptom_marker":
        return "patient_marker"
    if kind in _SIGNAL_QUALITY_KINDS or event.event_type is ECGEventType.NOISE:
        return "signal_quality"
    if event.event_type in _CLINICAL_EVENT_TYPES:
        return "clinical"
    return "technical"


def _recorded_ms(study: Study) -> float:
    return study.samples_count * 1000 / study.sample_rate


def _report_offset_ms(report: PatientReport, study: Study) -> int | None:
    """Dónde cae el registro dentro de la señal, o `None` si todavía no hay.

    **No se recorta contra el final de la grabación**, a diferencia de
    `_event_offsets_ms`. Ese clipping es correcto para un evento derivado de un
    lote ya decodificado: sus coordenadas vienen en muestras que existen. Acá
    no: el paciente pudo marcar el síntoma a las 14:30 y el chaleco subir esa
    hora recién a las 15:00. Recortarlo pegaría todos los registros recientes
    contra el borde derecho de la traza — una marca en un lugar donde no pasó
    nada, que es peor que no mostrar nada.

    Devolver `None` hace que el registro espere. Cuando llegue el lote,
    `samples_count` crece y la misma función lo empieza a ubicar sola: no hay
    job ni backfill, es una función del estado actual.
    """
    offset_ms = (report.occurred_at - study.started_at).total_seconds() * 1000
    if offset_ms < 0 or offset_ms > _recorded_ms(study):
        return None
    return round(offset_ms)


def _report_severity(report: PatientReport) -> Literal["low", "medium", "high", "critical"]:
    """ "No sentí nada" es contexto; un síntoma es un hallazgo."""
    symptoms = [item for item in (report.symptoms or []) if item != "sin_sintomas"]
    return "high" if symptoms else "low"


def _report_annotations(study: Study, reports: list[PatientReport]) -> list[StudyEcgAnnotationOut]:
    annotations: list[StudyEcgAnnotationOut] = []
    for report in reports:
        offset_ms = _report_offset_ms(report, study)
        if offset_ms is None:
            continue
        annotations.append(
            StudyEcgAnnotationOut(
                id=report.id,
                kind="patient_report",
                category="patient_marker",
                severity=_report_severity(report),
                # Puntual: el paciente marca un instante, no un intervalo. El
                # visor lo pinta como línea vertical, no como banda.
                startOffsetMs=offset_ms,
                endOffsetMs=offset_ms,
                confidenceScore=None,
            )
        )
    return annotations


def _study_annotations(
    study: Study, events: list[ECGEvent], reports: list[PatientReport]
) -> list[StudyEcgAnnotationOut]:
    annotations: list[StudyEcgAnnotationOut] = list(_report_annotations(study, reports))
    for event in events:
        offsets = _event_offsets_ms(event, study)
        if offsets is None:
            continue
        kind = _annotation_kind(event)
        annotations.append(
            StudyEcgAnnotationOut(
                id=event.id,
                kind=kind,
                category=_annotation_category(event, kind),
                severity=_ANNOTATION_SEVERITY[event.severity],
                startOffsetMs=offsets[0],
                endOffsetMs=offsets[1],
                confidenceScore=event.confidence_score,
            )
        )
    annotations.sort(key=lambda item: (item.startOffsetMs, item.endOffsetMs, str(item.id)))
    return annotations


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
    annotations = _study_annotations(
        study,
        await repo.list_ecg_events(db, study.id),
        await patient_app_repo.list_reports_for_study(db, study.id),
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
        annotations=annotations,
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


async def list_study_patient_reports(
    input_data: StudyIdInput, db: AsyncSession
) -> StudyPatientReportsResponse:
    """Los registros del paciente de un estudio, para la solapa del portal.

    Devuelve **todos**, incluidos los que todavía no se pueden pintar sobre el
    ECG. Ese es el punto de la solapa: si el médico solo viera las bandas del
    gráfico, un síntoma marcado hace veinte minutos sería invisible hasta el
    próximo envío del chaleco, y en la práctica eso es perderlo.
    """
    result = await repo.get_detail(db, input_data.study_id, input_data.doctor_id)
    if result is None:
        raise _not_found()
    study, _, _, _ = result
    reports = await patient_app_repo.list_reports_for_study(db, study.id)

    items: list[StudyPatientReportOut] = []
    pending = 0
    for report in reports:
        offset_ms = _report_offset_ms(report, study)
        if offset_ms is None:
            pending += 1
        items.append(
            StudyPatientReportOut(
                id=report.id,
                occurredAt=report.occurred_at,
                source=report.source.value,
                symptoms=list(report.symptoms or []),
                symptomLabels=[symptom_label(item) for item in report.symptoms or []],
                symptomsOther=report.symptoms_other,
                activity=report.activity,
                activityLabel=activity_label(report.activity),
                activityOther=report.activity_other,
                notes=report.notes,
                alertId=report.alert_id,
                createdAt=report.created_at,
                offsetMs=offset_ms,
                visibleInChart=offset_ms is not None,
            )
        )
    return StudyPatientReportsResponse(items=items, total=len(items), pendingSignalTotal=pending)
