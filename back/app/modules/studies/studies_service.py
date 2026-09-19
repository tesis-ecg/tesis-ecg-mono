import asyncio
import hashlib
import json
import math
import struct
import uuid
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import Any, Literal, NamedTuple, cast

import structlog
from fastapi import BackgroundTasks, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.request_limits import MAX_CLINICAL_REPORT_PDF_BYTES
from app.core.s3 import (
    build_presigned_url as _build_presigned_ecg_url,
)
from app.core.s3 import (
    get_object_range as _get_ecg_object_range,
)
from app.db.models.alert import Alert, AlertSeverity
from app.db.models.audit_event import AuditEventType
from app.db.models.device import Device
from app.db.models.ecg_event import ECGEvent, ECGEventSeverity, ECGEventType
from app.db.models.patient import Patient, PatientStudyStatus
from app.db.models.patient_report import PatientReport
from app.db.models.study import Study, StudyStatus
from app.db.models.study_clinical_report import StudyClinicalReport, StudyClinicalReportDraft
from app.db.models.study_timeline_segment import StudyTimelineSegment
from app.db.models.user import User
from app.modules._alert_kind import resolve_alert_kind
from app.modules.auth import auth_repository as auth_repo
from app.modules.patient_app import patient_app_repository as patient_app_repo
from app.modules.patient_app import patient_app_service
from app.modules.patient_app.catalogs import activity_label, symptom_label
from app.modules.studies import studies_repository as repo
from app.modules.studies.studies_schemas import (
    PatientStudiesInput,
    PatientStudiesResponse,
    PatientStudyOut,
    SimulateAnomalyInput,
    SimulateAnomalyOut,
    SimulatedAnomalyType,
    StudyClinicalReportDraftInput,
    StudyClinicalReportDraftOut,
    StudyClinicalReportFinalizeInput,
    StudyClinicalReportIssueOut,
    StudyClinicalReportPreviewOut,
    StudyClinicalReportVersionOut,
    StudyClinicalReportVersionsOut,
    StudyClinicalReportWindowPlanOut,
    StudyDetailOut,
    StudyEcgAnnotationOut,
    StudyEcgLevelChunkOut,
    StudyEcgLevelOut,
    StudyEcgManifestOut,
    StudyEcgObjectOut,
    StudyEcgOut,
    StudyEcgReportWindowOut,
    StudyEcgReportWindowsRequest,
    StudyEcgReportWindowsResponse,
    StudyEcgSegmentOut,
    StudyEcgTimelineSegmentOut,
    StudyIdInput,
    StudyListInput,
    StudyListResponse,
    StudyPatientReportOut,
    StudyPatientReportsResponse,
)

logger = structlog.get_logger(__name__)

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
    study: Study,
    patient: Patient,
    device: Device,
    doctor_name: str | None,
    last_data_received_at: datetime | None,
    requesting_doctor_id: uuid.UUID | None,
) -> StudyDetailOut:
    return StudyDetailOut(
        id=study.id,
        patientId=patient.id,
        patientName=f"{patient.first_name} {patient.last_name}".strip(),
        deviceId=device.id,
        startedAt=study.started_at,
        endedAt=study.ended_at,
        durationMs=_duration_ms(study),
        deviceSerial=device.serial_number,
        canAccessDevice=(requesting_doctor_id is None or device.doctor_id == requesting_doctor_id),
        lastDataReceivedAt=last_data_received_at,
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


class _ReportPlacement(NamedTuple):
    """Coordenada del registro sobre la traza y el hallazgo del que cuelga."""

    #: `None` mientras no haya señal debajo: el registro existe pero no se pinta.
    offset_ms: int | None
    #: El `ecg_event` que este registro responde, si quedó dibujado.
    linked_event_id: uuid.UUID | None


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


def _report_symptoms_text(report: PatientReport) -> str | None:
    """Los síntomas del registro en una línea, ya traducidos del catálogo."""
    labels = [symptom_label(item) for item in report.symptoms or []]
    if report.symptoms_other:
        labels.append(report.symptoms_other)
    return " · ".join(labels) or None


def _report_alert_kind(report: PatientReport) -> str | None:
    """Qué hallazgo disparó el aviso que el registro contesta.

    Se resuelve con el mismo helper que la bandeja y el dashboard para que un
    mismo aviso no se llame distinto en cada pantalla.
    """
    alert = report.alert
    if alert is None:
        return None
    event = alert.event
    return resolve_alert_kind(
        alert.kind,
        event.event_type if event is not None else None,
        event.event_metadata if event is not None else None,
    )


def _answered_event_id(report: PatientReport) -> uuid.UUID | None:
    """El `ecg_event` que originó el aviso que este registro contesta."""
    alert = report.alert
    if alert is None:
        return None
    return alert.event_id


def _report_placements(
    study: Study,
    reports: list[PatientReport],
    event_offsets: dict[uuid.UUID, tuple[int, int]],
) -> dict[uuid.UUID, _ReportPlacement]:
    """Dónde va cada registro sobre la traza, y de qué hallazgo cuelga.

    Un registro espontáneo se ubica por su hora de pared (`_report_offset_ms`).
    Uno que **responde un aviso** se ancla en el medio de la banda del hallazgo
    que contesta, y no en su propia hora:

    - Es lo que el médico necesita ver. Suelta en la traza, una respuesta es
      una marca más entre las 24 h del estudio y no hay forma de saber a qué
      aviso pertenece; sobre la banda, la pertenencia se lee sola.
    - Es lo que hace que exista. El paciente contesta cuando ve la
      notificación, que puede ser media hora después del hallazgo — y esa media
      hora todavía no está grabada, así que por hora de pared el registro
      quedaría "sin señal" y no se pintaría nunca.

    La hora real del registro no se pierde: sigue viajando en `occurredAt` y es
    lo que muestra la solapa de registros.

    `event_offsets` son los hallazgos que **sí** quedaron dibujados. El vínculo
    se resuelve contra ese diccionario y no contra `alert.event_id` a secas:
    anclar contra un hallazgo que el visor no recibió dejaría la respuesta
    colgada de nada.
    """
    placements: dict[uuid.UUID, _ReportPlacement] = {}
    for report in reports:
        event_id = _answered_event_id(report)
        offsets = event_offsets.get(event_id) if event_id is not None else None
        if offsets is not None and event_id is not None:
            placements[report.id] = _ReportPlacement((offsets[0] + offsets[1]) // 2, event_id)
        else:
            placements[report.id] = _ReportPlacement(_report_offset_ms(report, study), None)
    return placements


def _report_annotations(
    reports: list[PatientReport],
    placements: dict[uuid.UUID, _ReportPlacement],
    to_epoch_ms: Callable[[int], int],
) -> list[StudyEcgAnnotationOut]:
    annotations: list[StudyEcgAnnotationOut] = []
    for report in reports:
        placement = placements[report.id]
        if placement.offset_ms is None:
            continue
        annotations.append(
            StudyEcgAnnotationOut(
                id=report.id,
                kind="patient_report",
                category="patient_marker",
                severity=_report_severity(report),
                # Puntual: el paciente marca un instante, no un intervalo. El
                # visor lo pinta como línea vertical, no como banda.
                startOffsetMs=placement.offset_ms,
                endOffsetMs=placement.offset_ms,
                startEpochMs=to_epoch_ms(placement.offset_ms),
                endEpochMs=to_epoch_ms(placement.offset_ms),
                confidenceScore=None,
                linkedAnnotationId=placement.linked_event_id,
                description=_report_symptoms_text(report),
            )
        )
    return annotations


def _event_offset_map(study: Study, events: list[ECGEvent]) -> dict[uuid.UUID, tuple[int, int]]:
    """Offsets de los hallazgos **dibujables**, indexados por id de evento.

    Vive aparte de `_event_annotations` porque hay dos consumidores con
    necesidades distintas: el manifest quiere las anotaciones armadas, y la
    solapa de registros del paciente solo quiere saber dónde cayó cada hallazgo
    para anclarle su respuesta. Que los dos salgan de acá es lo que garantiza que
    la solapa y el gráfico nunca discrepen sobre dónde está una marca.
    """
    offsets: dict[uuid.UUID, tuple[int, int]] = {}
    for event in events:
        resolved = _event_offsets_ms(event, study)
        if resolved is not None:
            offsets[event.id] = resolved
    return offsets


def _event_annotations(
    study: Study, events: list[ECGEvent], to_epoch_ms: Callable[[int], int]
) -> tuple[list[StudyEcgAnnotationOut], dict[uuid.UUID, tuple[int, int]]]:
    """Los hallazgos dibujables, con sus offsets indexados por id de evento."""
    annotations: list[StudyEcgAnnotationOut] = []
    offsets_by_event = _event_offset_map(study, events)
    for event in events:
        offsets = offsets_by_event.get(event.id)
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
                startEpochMs=to_epoch_ms(offsets[0]),
                endEpochMs=to_epoch_ms(offsets[1]),
                confidenceScore=event.confidence_score,
            )
        )
    return annotations, offsets_by_event


def _wall_clock_resolver(
    study: Study, segments: list[StudyTimelineSegment]
) -> Callable[[int], int]:
    """Devuelve `offsetMs (buffer empaquetado) -> epoch ms (hora real)`.

    Los offsets de las anotaciones están sobre el buffer de muestras, que no deja
    huecos. La hora de pared sí los tiene. La traducción es por tramo: se busca
    el que contiene esa muestra y se cuenta desde su hora de inicio.

    Sin tramos —estudio seedeado o legacy, o uno ingerido antes del backfill— se
    cae al comportamiento anterior, que para una grabación sin cortes da lo
    mismo.
    """
    started_ms = int(study.started_at.timestamp() * 1000)
    if not segments:
        return lambda offset_ms: started_ms + offset_ms

    rate = study.sample_rate or 500

    def resolve(offset_ms: int) -> int:
        sample = offset_ms * rate / 1000
        for segment in segments:
            end = segment.start_sample_index + segment.sample_count
            if sample < end or segment is segments[-1]:
                within = max(sample - segment.start_sample_index, 0)
                return int(segment.start_epoch_ms + within * 1000 / rate)
        return started_ms + offset_ms

    return resolve


def _study_annotations(
    study: Study,
    events: list[ECGEvent],
    reports: list[PatientReport],
    to_epoch_ms: Callable[[int], int],
) -> list[StudyEcgAnnotationOut]:
    # Los hallazgos primero: los registros necesitan saber cuáles llegaron a la
    # señal para poder anclarse en el que contestaron.
    annotations, event_offsets = _event_annotations(study, events, to_epoch_ms)
    annotations.extend(
        _report_annotations(reports, _report_placements(study, reports, event_offsets), to_epoch_ms)
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
            _study_detail_out(
                study,
                patient,
                device,
                doctor_name,
                last_data_received_at,
                input_data.doctor_id,
            )
            for study, patient, device, doctor_name, last_data_received_at in rows
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
    study, patient, device, doctor_name, last_data_received_at = result
    return _study_detail_out(
        study,
        patient,
        device,
        doctor_name,
        last_data_received_at,
        input_data.doctor_id,
    )


async def get_study_ecg(input_data: StudyIdInput, db: AsyncSession) -> StudyEcgOut:
    result = await repo.get_detail(db, input_data.study_id, input_data.doctor_id)
    if result is None:
        raise _not_found()
    study, _, _, _, _ = result
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
    study, _, _, _, _ = result
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
            samplesPerBucket=int(level["samplesPerBucket"]),
            pointCount=int(level["pointCount"]),
            chunks=[
                StudyEcgLevelChunkOut(
                    url=_build_presigned_ecg_url(str(chunk["key"])),
                    expiresAt=expires_at,
                    byteLength=int(chunk["byteLength"]),
                    sha256=str(chunk["sha256"]),
                    pointCount=int(chunk["pointCount"]),
                )
                for chunk in level.get("chunks", [])
            ],
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
    timeline_segments = await repo.list_timeline_segments(db, study.id)
    to_epoch_ms = _wall_clock_resolver(study, timeline_segments)
    timeline = [
        StudyEcgTimelineSegmentOut(
            ordinal=segment.ordinal,
            startSampleIndex=segment.start_sample_index,
            sampleCount=segment.sample_count,
            startEpochMs=segment.start_epoch_ms,
            endEpochMs=segment.end_epoch_ms,
            bootId=segment.boot_id,
            anchorSource=segment.anchor_source.value,
            anchorUncertaintyMs=segment.anchor_uncertainty_ms,
        )
        for segment in timeline_segments
    ]
    annotations = _study_annotations(
        study,
        await repo.list_ecg_events(db, study.id),
        await patient_app_repo.list_reports_for_study(db, study.id),
        to_epoch_ms,
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
        timeline=timeline,
        annotations=annotations,
    )


def _window_sample_bounds(
    segment: StudyTimelineSegment | None,
    start_ms: int,
    end_ms: int,
    study: Study,
) -> tuple[int, int] | None:
    """Intersección de una ventana de pared con un tramo de muestras."""
    if segment is None:
        recording_end_ms = int(study.started_at.timestamp() * 1000) + round(_recorded_ms(study))
        overlap_start = max(start_ms, int(study.started_at.timestamp() * 1000))
        overlap_end = min(end_ms, recording_end_ms)
        if overlap_end <= overlap_start:
            return None
        study_start_ms = int(study.started_at.timestamp() * 1000)
        start_sample = round((overlap_start - study_start_ms) * study.sample_rate / 1000)
        end_sample = round((overlap_end - study_start_ms) * study.sample_rate / 1000)
        return max(0, start_sample), min(study.samples_count, max(start_sample + 1, end_sample))

    overlap_start = max(start_ms, segment.start_epoch_ms)
    overlap_end = min(end_ms, segment.end_epoch_ms)
    if overlap_end <= overlap_start:
        return None
    wall_span = max(segment.end_epoch_ms - segment.start_epoch_ms, 1)
    start_offset = round(
        (overlap_start - segment.start_epoch_ms) * segment.sample_count / wall_span
    )
    end_offset = round((overlap_end - segment.start_epoch_ms) * segment.sample_count / wall_span)
    start_sample = segment.start_sample_index + max(0, min(start_offset, segment.sample_count))
    end_sample = segment.start_sample_index + max(0, min(end_offset, segment.sample_count))
    return start_sample, max(start_sample + 1, end_sample)


def _segment_timestamp_ms(
    segment: StudyTimelineSegment | None, sample_index: int, study: Study
) -> int:
    if segment is None:
        return int(study.started_at.timestamp() * 1000 + sample_index * 1000 / study.sample_rate)
    within = sample_index - segment.start_sample_index
    return round(
        segment.start_epoch_ms
        + within * (segment.end_epoch_ms - segment.start_epoch_ms) / max(segment.sample_count, 1)
    )


def _raw_objects_for_window(
    study: Study, start_sample: int, end_sample: int
) -> list[tuple[str, int, int]]:
    """Devuelve (key, inicio global, fin global) de cada objeto que toca el rango."""
    if study.ecg_segments:
        objects: list[tuple[str, int, int]] = []
        for item in study.ecg_segments:
            object_start = int(item["startSampleIndex"])
            object_end = object_start + int(item["sampleCount"])
            if object_end > start_sample and object_start < end_sample:
                objects.append((str(item["key"]), object_start, object_end))
        return objects
    if study.ecg_s3_key is not None:
        return [(study.ecg_s3_key, 0, study.samples_count)]
    return []


def _read_raw_window(
    study: Study,
    timeline_segments: list[StudyTimelineSegment],
    start_ms: int,
    end_ms: int,
) -> tuple[list[int], list[float], list[int]]:
    """Lee exactamente las muestras de una ventana, respetando huecos de pared."""
    timeline: list[StudyTimelineSegment | None] = (
        list(timeline_segments) if timeline_segments else [None]
    )
    timestamps: list[int] = []
    samples: list[float] = []
    gap_indices: list[int] = []
    previous_segment: StudyTimelineSegment | None = None

    for timeline_segment in timeline:
        bounds = _window_sample_bounds(timeline_segment, start_ms, end_ms, study)
        if bounds is None:
            continue
        sample_start, sample_end = bounds
        for key, object_start, object_end in _raw_objects_for_window(
            study, sample_start, sample_end
        ):
            read_start = max(sample_start, object_start)
            read_end = min(sample_end, object_end)
            if read_end <= read_start:
                continue
            start_byte = (read_start - object_start) << 2
            end_byte = ((read_end - object_start) << 2) - 1
            payload = _get_ecg_object_range(key, start_byte, end_byte)
            count = read_end - read_start
            if len(payload) != count * 4:
                raise HTTPException(
                    status_code=500,
                    detail={
                        "code": "ECG_CORRUPT",
                        "message": "No se pudo leer una ventana del ECG.",
                    },
                )
            if samples and timeline_segment is not previous_segment:
                gap_indices.append(len(samples))
            values = struct.unpack(f"<{count}f", payload)
            samples.extend(values)
            timestamps.extend(
                _segment_timestamp_ms(timeline_segment, sample, study)
                for sample in range(read_start, read_end)
            )
            previous_segment = timeline_segment
    return timestamps, samples, gap_indices


def _read_envelope_window(
    study: Study,
    timeline_segments: list[StudyTimelineSegment],
    start_ms: int,
    end_ms: int,
) -> tuple[list[int], list[float], list[int]]:
    """Fallback explícito a la envolvente más fina cuando ya no existe el crudo.

    La pirámide guarda pares min/max float32 por bucket. No se los presenta como
    señal diagnóstica: el contrato marca `source="envelope"`, pero permite que
    el informe conserve contexto en estudios antiguos recuperados parcialmente.
    """
    available = [level for level in study.ecg_pyramid_levels if level.get("chunks")]
    if not available:
        return [], [], []
    level = min(available, key=lambda item: int(item["samplesPerBucket"]))
    bucket = int(level["samplesPerBucket"])
    timeline: list[StudyTimelineSegment | None] = (
        list(timeline_segments) if timeline_segments else [None]
    )
    bounds = [
        bound
        for segment in timeline
        if (bound := _window_sample_bounds(segment, start_ms, end_ms, study)) is not None
    ]
    if not bounds:
        return [], [], []
    first_pair = min(bound[0] for bound in bounds) // bucket
    last_pair = math.ceil(max(bound[1] for bound in bounds) / bucket)
    timestamps: list[int] = []
    samples: list[float] = []
    gap_indices: list[int] = []
    pair_cursor = 0
    expected_gap_ms = bucket * 1000 / study.sample_rate * 1.5

    for chunk in level["chunks"]:
        pair_count = int(chunk["pointCount"]) // 2
        chunk_first = pair_cursor
        chunk_last = pair_cursor + pair_count
        pair_cursor = chunk_last
        read_first = max(first_pair, chunk_first)
        read_last = min(last_pair, chunk_last)
        if read_last <= read_first:
            continue
        byte_start = (read_first - chunk_first) * 8
        byte_end = (read_last - chunk_first) * 8 - 1
        payload = _get_ecg_object_range(str(chunk["key"]), byte_start, byte_end)
        value_count = (read_last - read_first) * 2
        if len(payload) != value_count * 4:
            raise HTTPException(
                status_code=500,
                detail={"code": "ECG_CORRUPT", "message": "No se pudo leer una ventana del ECG."},
            )
        values = struct.unpack(f"<{value_count}f", payload)
        for offset in range(read_last - read_first):
            sample_index = (read_first + offset) * bucket
            timestamp = _segment_timestamp_ms(
                next(
                    (
                        segment
                        for segment in timeline_segments
                        if segment.start_sample_index
                        <= sample_index
                        < segment.start_sample_index + segment.sample_count
                    ),
                    None,
                ),
                sample_index,
                study,
            )
            if timestamps and timestamp - timestamps[-1] > expected_gap_ms:
                gap_indices.append(len(samples))
            timestamps.extend([timestamp, timestamp])
            samples.extend(values[offset * 2 : offset * 2 + 2])
    return timestamps, samples, gap_indices


async def get_study_ecg_report_windows(
    input_data: StudyIdInput,
    data: StudyEcgReportWindowsRequest,
    db: AsyncSession,
) -> StudyEcgReportWindowsResponse:
    result = await repo.get_detail(db, input_data.study_id, input_data.doctor_id)
    if result is None:
        raise _not_found()
    study, _, _, _, _ = result
    has_raw = study.ecg_s3_key is not None or bool(study.ecg_segments)
    if study.samples_count <= 0 or (not has_raw and not study.ecg_pyramid_levels):
        raise HTTPException(
            status_code=404,
            detail={"code": "ECG_NOT_FOUND", "message": "ECG no disponible para este estudio."},
        )

    for window in data.windows:
        if (
            window.endEpochMs <= window.startEpochMs
            or window.endEpochMs - window.startEpochMs > 10_000
        ):
            raise HTTPException(
                status_code=422,
                detail={
                    "code": "INVALID_WINDOW",
                    "message": "Cada ventana debe durar entre 1 ms y 10 s.",
                },
            )

    timeline = await repo.list_timeline_segments(db, study.id)
    windows = []
    for window in data.windows:
        reader = _read_raw_window if has_raw else _read_envelope_window
        timestamps, samples, gap_indices = await asyncio.to_thread(
            reader, study, timeline, window.startEpochMs, window.endEpochMs
        )
        windows.append(
            StudyEcgReportWindowOut(
                id=window.id,
                startEpochMs=window.startEpochMs,
                endEpochMs=window.endEpochMs,
                timestampsMs=timestamps,
                samplesMv=samples,
                gapIndices=gap_indices,
                source="raw" if has_raw else "envelope",
            )
        )
    if input_data.actor_id is not None:
        await auth_repo.log_audit_event(
            db,
            AuditEventType.ECG_ACCESSED,
            user_id=input_data.actor_id,
            metadata={"target_study_id": str(study.id), "protocol": "report-windows"},
        )
        await db.commit()
    return StudyEcgReportWindowsResponse(windows=windows)


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


async def compact_study_pyramid(db: AsyncSession, study_id: uuid.UUID) -> None:
    """Funde los chunks de cada nivel de un estudio ya cerrado.

    Durante la ingesta cada lote anexa su propio chunk a cada nivel, que es lo
    que mantiene el trabajo por lote constante. El precio es la cantidad de
    objetos: 24 h subiendo cada 10 minutos dejan ~144 chunks por nivel, y el
    visor tendría que pedir 144 URLs para pintar la vista general. Un estudio
    cerrado ya no crece, así que es el momento exacto para pagar la fusión.

    Corre **después** del cierre y fuera de su transacción, no adentro. Fundir
    seis niveles de 144 chunks son ~900 GET a S3 con `boto3` sincrónico: adentro
    del request bloqueaban el event loop entero con la fila del estudio tomada,
    que es exactamente la forma de trabajo que este cambio vino a sacar de la
    ruta caliente. El `to_thread` saca el bloqueo del loop; correr después del
    commit saca el lock del camino del médico, que ya tiene su respuesta.

    Si falla, el cierre no se ve afectado: ya está commiteado, y los chunks
    siguen siendo una representación válida que el visor sabe leer. Solo quedan
    más objetos de los necesarios.
    """
    from app.modules.ingest import ingest_repository as ingest_repo
    from app.modules.ingest.processing import compact_pyramid

    try:
        study = await ingest_repo.get_study_for_update(db, study_id)
        if study is None or not study.ecg_pyramid_levels:
            await db.rollback()
            return
        study.ecg_pyramid_levels = await asyncio.to_thread(compact_pyramid, study, force=True)
        await db.commit()
    except Exception:  # noqa: BLE001 — ver docstring: no puede afectar al cierre
        await db.rollback()
        logger.exception("study_pyramid_compaction_failed", study_id=str(study_id))


async def compact_study_pyramid_task(study_id: uuid.UUID) -> None:
    """Entrypoint del `BackgroundTasks`: abre su propia sesión.

    Mismo patrón que `process_batch_task` — la sesión del request ya está
    cerrada cuando esto corre.
    """
    from app.db.session import async_session_factory

    async with async_session_factory() as session:
        await compact_study_pyramid(session, study_id)


async def _transition(
    input_data: StudyIdInput,
    db: AsyncSession,
    target: StudyStatus,
    background: BackgroundTasks | None = None,
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
    if background is not None:
        background.add_task(compact_study_pyramid_task, input_data.study_id)

    result = await repo.get_detail(db, input_data.study_id, input_data.doctor_id)
    if result is None:  # pragma: no cover - la fila se acaba de commitear
        raise _not_found()
    return _study_detail_out(*result, input_data.doctor_id)


async def complete_study(
    input_data: StudyIdInput, db: AsyncSession, background: BackgroundTasks | None = None
) -> StudyDetailOut:
    return await _transition(input_data, db, StudyStatus.COMPLETED, background)


async def cancel_study(
    input_data: StudyIdInput, db: AsyncSession, background: BackgroundTasks | None = None
) -> StudyDetailOut:
    return await _transition(input_data, db, StudyStatus.CANCELLED, background)


#: Cómo se llama cada hallazgo simulado en la alerta que ve el médico y el
#: paciente. El texto es el mismo que produciría el pipeline cuando exista.
_SIMULATED_MESSAGES = {
    SimulatedAnomalyType.TACHYCARDIA: "Se detectó un episodio de taquicardia.",
    SimulatedAnomalyType.BRADYCARDIA: "Se detectó un episodio de bradicardia.",
    SimulatedAnomalyType.AFIB: "Se detectó un ritmo compatible con fibrilación auricular.",
    SimulatedAnomalyType.PVC: "Se detectaron latidos ventriculares prematuros.",
    SimulatedAnomalyType.PAUSE: "Se detectó una pausa en el ritmo.",
}


def _no_signal() -> HTTPException:
    return HTTPException(
        status_code=409,
        detail={
            "code": "STUDY_HAS_NO_SIGNAL",
            "message": (
                "El estudio todavía no tiene señal ingerida. "
                "Enviá al menos un lote con el simulador de chalecos y volvé a intentar."
            ),
        },
    )


async def simulate_anomaly(
    input_data: SimulateAnomalyInput, db: AsyncSession, background: BackgroundTasks
) -> SimulateAnomalyOut:
    """Fabrica un hallazgo clínico sobre señal ya ingerida y avisa al paciente.

    Existe porque `app/ml/*` todavía son stubs: sin esto, la única alerta `HIGH`
    que el sistema sabe producir es la del botón de síntoma del chaleco, y no
    hay forma de ejercitar el aviso de "detectamos algo" ni la bitácora que lo
    responde. Cuando el pipeline exista, este endpoint deja de hacer falta y el
    camino de notificación no cambia: el push se dispara por la alerta, no por
    quién la creó.

    El hallazgo se ancla **hacia atrás desde el final de lo grabado** y no en el
    instante del pedido. Es lo que distingue esto de crear una alerta suelta:
    con el `occurredAt` dentro de la grabación, la respuesta del paciente cae
    dentro de la traza (`_report_offset_ms` la ubica en vez de omitirla) y el
    médico la ve como marca sobre el ECG, que es el flujo real que se quiere
    probar.

    Disponible en todos los entornos. Estaba bloqueado fuera de desarrollo, y
    eso dejaba el flujo completo —push, formulario del paciente, respuesta sobre
    el ECG— sin forma de demostrarse en el sistema desplegado, que es donde hay
    que mostrarlo. La barrera que queda es el rol: solo admin
    (`studies_routes.simulate_anomaly`). Lo escrito queda marcado como tal
    (`event_metadata["simulated"]`) y el log `anomaly_simulated` guarda quién lo
    pidió.
    """
    result = await repo.get_detail(db, input_data.study_id, input_data.doctor_id)
    if result is None:
        raise _not_found()
    study, patient, _, _, _ = result

    batch = await repo.get_latest_batch(db, study.id)
    # `ecg_event.batch_id` es NOT NULL: un hallazgo sin lote detrás no se puede
    # escribir, y sin muestras no habría dónde anclarlo aunque se pudiera.
    if batch is None or study.samples_count <= 0:
        raise _no_signal()

    data = input_data.data
    sample_rate = study.sample_rate or 500
    start_sample = max(0, study.samples_count - round(data.secondsBeforeEnd * sample_rate))
    length_samples = max(
        1, min(round(data.durationSeconds * sample_rate), study.samples_count - start_sample)
    )
    severity = ECGEventSeverity[data.severity.upper()]
    kind = data.eventType.value

    event = ECGEvent(
        batch_id=batch.id,
        event_type=ECGEventType[kind.upper()],
        severity=severity,
        timestamp_in_recording=start_sample / sample_rate,
        duration_seconds=length_samples / sample_rate,
        # Las mismas claves que escribe `ingest/processing._persist_events`: son
        # las que `_event_offsets_ms` sabe leer para pintar la banda.
        event_metadata={
            "kind": kind,
            "studyId": str(study.id),
            "startSampleIndex": start_sample,
            "sampleCount": length_samples,
            "simulated": True,
        },
    )
    db.add(event)
    await db.flush()

    alert = Alert(
        patient_id=patient.id,
        event_id=event.id,
        # Sin `kind`: lo deriva `resolve_alert_kind` del tipo de evento, que es
        # el mismo camino que recorre una alerta del pipeline real.
        kind=None,
        severity=AlertSeverity[data.severity.upper()],
        message=(data.message or "").strip() or _SIMULATED_MESSAGES[data.eventType],
    )
    db.add(alert)
    await db.flush()

    alert_id = alert.id
    event_id = event.id
    occurred_at = study.started_at + timedelta(seconds=start_sample / sample_rate)
    await db.commit()

    # Después del commit, como en la ingesta: un push con un `alertId` que la
    # transacción termina descartando deja al paciente tocando una notificación
    # que abre un formulario roto.
    patient_app_service.schedule_alert_push(background, patient.id, alert_id, occurred_at, kind)
    await logger.ainfo(
        "anomaly_simulated",
        study_id=str(study.id),
        alert_id=str(alert_id),
        kind=kind,
        severity=data.severity,
        start_sample=start_sample,
        actor_id=str(input_data.actor_id) if input_data.actor_id else None,
    )
    return SimulateAnomalyOut(
        alertId=alert_id,
        eventId=event_id,
        occurredAt=occurred_at,
        offsetMs=round(start_sample * 1000 / sample_rate),
    )


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
    study, _, _, _, _ = result
    reports = await patient_app_repo.list_reports_for_study(db, study.id)
    # Los mismos offsets que el manifest: si la solapa dijera "visible" y el
    # visor no pintara la marca, el botón "Ver en el ECG" no llevaría a ningún
    # lado. La ubicación de un registro se decide en un solo lugar.
    event_offsets = _event_offset_map(study, await repo.list_ecg_events(db, study.id))
    placements = _report_placements(study, reports, event_offsets)

    items: list[StudyPatientReportOut] = []
    pending = 0
    for report in reports:
        offset_ms = placements[report.id].offset_ms
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
                alertKind=_report_alert_kind(report),
                createdAt=report.created_at,
                offsetMs=offset_ms,
                visibleInChart=offset_ms is not None,
            )
        )
    return StudyPatientReportsResponse(items=items, total=len(items), pendingSignalTotal=pending)


# --------------------------------------------------------------------------- #
# Informe clínico versionado
# --------------------------------------------------------------------------- #


def _clean_report_text(value: str | None) -> str | None:
    cleaned = (value or "").strip()
    return cleaned or None


def _draft_out(
    study_id: uuid.UUID,
    draft: StudyClinicalReportDraft | None,
    updated_by: User | None = None,
) -> StudyClinicalReportDraftOut:
    if draft is None:
        return StudyClinicalReportDraftOut(
            studyId=study_id,
            revision=0,
            indication=None,
            medications=None,
            referringProfessional=None,
            technician=None,
            clinicalObservations=None,
            conclusion=None,
            updatedAt=None,
            updatedBy=None,
            updatedByName=None,
            updatedByRole=None,
        )
    return StudyClinicalReportDraftOut(
        studyId=study_id,
        revision=draft.revision,
        indication=draft.indication,
        medications=draft.medications,
        referringProfessional=draft.referring_professional,
        technician=draft.technician,
        clinicalObservations=draft.clinical_observations,
        conclusion=draft.conclusion,
        updatedAt=draft.updated_at,
        updatedBy=draft.updated_by,
        updatedByName=updated_by.full_name if updated_by else None,
        updatedByRole=updated_by.role.value if updated_by else None,
    )


def _annotation_duration_ms(annotation: StudyEcgAnnotationOut) -> int:
    return max(0, annotation.endEpochMs - annotation.startEpochMs)


def _selected_report_findings(
    annotations: list[StudyEcgAnnotationOut],
) -> list[StudyEcgAnnotationOut]:
    symptom_linked_ids = {
        annotation.linkedAnnotationId
        for annotation in annotations
        if annotation.linkedAnnotationId is not None
    }
    eligible = [
        annotation
        for annotation in annotations
        if annotation.category in {"clinical", "patient_marker"}
        and annotation.linkedAnnotationId is None
    ]
    grouped: dict[str, list[StudyEcgAnnotationOut]] = {}
    for annotation in eligible:
        grouped.setdefault(annotation.kind, []).append(annotation)

    severity_rank = {"critical": 4, "high": 3, "medium": 2, "low": 1}
    selected: list[StudyEcgAnnotationOut] = []
    for kind in sorted(grouped):
        ranked = sorted(
            grouped[kind],
            key=lambda item: (
                -(item.id in symptom_linked_ids),
                -severity_rank[item.severity],
                -_annotation_duration_ms(item),
                -(item.confidenceScore if item.confidenceScore is not None else -1),
                item.startEpochMs,
                str(item.id),
            ),
        )
        selected.extend(ranked[:3])
    return selected


def _report_window_ranges(annotation: StudyEcgAnnotationOut) -> list[tuple[int, int]]:
    start = annotation.startEpochMs
    end = max(annotation.endEpochMs, start)
    duration = end - start
    if duration <= 0:
        return [(max(0, start - 500), start + 500)]
    if duration <= 240_000:
        return [(cursor, min(cursor + 60_000, end)) for cursor in range(start, end, 60_000)]

    latest = end - 60_000
    starts = [start, start + duration // 3, start + (duration * 2) // 3, latest]
    normalized: list[int] = []
    for value in starts:
        value = min(max(value, start), latest)
        if value not in normalized:
            normalized.append(value)
    return [(value, value + 60_000) for value in normalized]


def _report_window_plans(
    annotations: list[StudyEcgAnnotationOut],
) -> list[StudyClinicalReportWindowPlanOut]:
    plans: list[StudyClinicalReportWindowPlanOut] = []
    responses: dict[uuid.UUID, list[str]] = {}
    for annotation in annotations:
        if annotation.linkedAnnotationId is not None and annotation.description:
            responses.setdefault(annotation.linkedAnnotationId, []).append(annotation.description)
    for annotation in _selected_report_findings(annotations):
        ranges = _report_window_ranges(annotation)
        for index, (start, end) in enumerate(ranges, start=1):
            plans.append(
                StudyClinicalReportWindowPlanOut(
                    id=f"finding:{annotation.id}:{index}",
                    findingId=annotation.id,
                    kind=annotation.kind,
                    category=cast(Literal["clinical", "patient_marker"], annotation.category),
                    severity=annotation.severity,
                    findingStartEpochMs=annotation.startEpochMs,
                    findingEndEpochMs=annotation.endEpochMs,
                    findingDurationMs=_annotation_duration_ms(annotation),
                    startEpochMs=start,
                    endEpochMs=end,
                    blockIndex=index,
                    blockCount=len(ranges),
                    confidenceScore=annotation.confidenceScore,
                    description=annotation.description,
                    relatedSymptoms=responses.get(annotation.id, []),
                )
            )
    return plans


def _summarize_annotations(
    annotations: list[StudyEcgAnnotationOut], categories: set[str]
) -> list[dict[str, Any]]:
    symptom_linked_ids = {
        item.linkedAnnotationId for item in annotations if item.linkedAnnotationId is not None
    }
    grouped: dict[str, list[StudyEcgAnnotationOut]] = {}
    for item in annotations:
        if item.category in categories and item.linkedAnnotationId is None:
            grouped.setdefault(item.kind, []).append(item)
    return [
        {
            "kind": kind,
            "count": len(items),
            "severities": sorted({item.severity for item in items}),
            "totalDurationMs": sum(_annotation_duration_ms(item) for item in items),
            "longestDurationMs": max((_annotation_duration_ms(item) for item in items), default=0),
            "symptomaticCount": sum(item.id in symptom_linked_ids for item in items),
        }
        for kind, items in sorted(grouped.items())
    ]


def _snapshot_hash(snapshot: dict[str, Any]) -> str:
    canonical = json.dumps(snapshot, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode()).hexdigest()


async def _clinical_report_snapshot(
    db: AsyncSession,
    study: Study,
    patient: Patient,
    device: Device,
    draft: StudyClinicalReportDraft | None,
    version: int,
    last_data_received_at: datetime | None,
) -> tuple[
    dict[str, Any],
    list[StudyClinicalReportWindowPlanOut],
    list[StudyClinicalReportIssueOut],
]:
    timeline = await repo.list_timeline_segments(db, study.id)
    reports = await patient_app_repo.list_reports_for_study(db, study.id)
    annotations = _study_annotations(
        study,
        await repo.list_ecg_events(db, study.id),
        reports,
        _wall_clock_resolver(study, timeline),
    )
    windows = _report_window_plans(annotations)
    doctor_info = await repo.get_responsible_doctor(db, patient.doctor_id)
    doctor, doctor_user = doctor_info if doctor_info is not None else (None, None)
    updated_by = await auth_repo.get_user_by_id(db, draft.updated_by) if draft else None
    draft_out = _draft_out(study.id, draft, updated_by)
    recorded_ms = round(_recorded_ms(study))
    wall_ms = _duration_ms(study)
    interruption_ms = max(0, wall_ms - recorded_ms)

    snapshot: dict[str, Any] = {
        "schemaVersion": 1,
        "version": version,
        "study": {
            "id": str(study.id),
            "status": study.status.value,
            "startedAt": study.started_at.isoformat(),
            "endedAt": study.ended_at.isoformat() if study.ended_at else None,
            "durationMs": wall_ms,
            "deviceSerial": device.serial_number,
            "sampleRate": study.sample_rate,
            "isSimulated": study.is_simulated,
        },
        "patient": {
            "id": str(patient.id),
            "fullName": f"{patient.first_name} {patient.last_name}".strip(),
            "dni": patient.dni,
            "birthDate": patient.date_of_birth.isoformat() if patient.date_of_birth else None,
            "sex": patient.sex.value,
            "medicalRecordNumber": patient.medical_record_num,
        },
        "responsibleDoctor": {
            "fullName": doctor_user.full_name if doctor_user else None,
            "specialty": doctor.specialty if doctor else None,
            "licenseNumber": doctor.license_number if doctor else None,
        },
        "clinicalContext": draft_out.model_dump(mode="json"),
        "quality": {
            "recordedMs": recorded_ms,
            "wallClockMs": wall_ms,
            "interruptionMs": interruption_ms,
            "coveragePercent": round(min(100, recorded_ms / wall_ms * 100), 1) if wall_ms else 0,
            "segments": len(timeline) or 1,
            "cuts": max(0, len(timeline) - 1),
            "lastDataReceivedAt": (
                last_data_received_at.isoformat() if last_data_received_at else None
            ),
            "synchronizationSources": sorted({item.anchor_source.value for item in timeline}),
            "maxSynchronizationUncertaintyMs": max(
                (item.anchor_uncertainty_ms or 0 for item in timeline), default=0
            ),
        },
        "findings": _summarize_annotations(annotations, {"clinical", "patient_marker"}),
        "technicalEvents": _summarize_annotations(annotations, {"signal_quality", "technical"}),
        "patientReports": [
            {
                "id": str(report.id),
                "occurredAt": report.occurred_at.isoformat(),
                "symptoms": [symptom_label(item) for item in report.symptoms or []],
                "symptomsOther": report.symptoms_other,
                "activity": activity_label(report.activity),
                "activityOther": report.activity_other,
                "notes": report.notes,
            }
            for report in reports
        ],
        "selectedWindows": [window.model_dump(mode="json") for window in windows],
    }
    issues: list[StudyClinicalReportIssueOut] = []
    if study.status is not StudyStatus.COMPLETED:
        issues.append(
            StudyClinicalReportIssueOut(
                code="STUDY_NOT_COMPLETED",
                message="El estudio debe estar completado para generar una versión final.",
                severity="blocking",
            )
        )
    if study.is_simulated:
        issues.append(
            StudyClinicalReportIssueOut(
                code="SIMULATED_STUDY",
                message=(
                    "La señal fue generada con un chaleco simulado; "
                    "verificá su uso antes de emitirla."
                ),
                severity="warning",
            )
        )
    if not _clean_report_text(draft_out.indication):
        issues.append(
            StudyClinicalReportIssueOut(
                code="MISSING_INDICATION",
                message="Completá la indicación del estudio para generar una versión final.",
                severity="blocking",
            )
        )
    if not _clean_report_text(draft_out.conclusion):
        issues.append(
            StudyClinicalReportIssueOut(
                code="MISSING_CONCLUSION",
                message="Completá la conclusión clínica para generar una versión final.",
                severity="blocking",
            )
        )
    has_raw = study.ecg_s3_key is not None or bool(study.ecg_segments)
    if study.samples_count <= 0 or not has_raw:
        issues.append(
            StudyClinicalReportIssueOut(
                code="MISSING_RAW_SIGNAL",
                message=(
                    "El estudio no tiene señal cruda disponible para "
                    "finalizar las tiras del informe."
                ),
                severity="blocking",
            )
        )
    return snapshot, windows, issues


async def get_clinical_report_draft(
    input_data: StudyIdInput, db: AsyncSession
) -> StudyClinicalReportDraftOut:
    if await repo.get_detail(db, input_data.study_id, input_data.doctor_id) is None:
        raise _not_found()
    draft = await repo.get_report_draft(db, input_data.study_id)
    updated_by = await auth_repo.get_user_by_id(db, draft.updated_by) if draft else None
    return _draft_out(input_data.study_id, draft, updated_by)


async def update_clinical_report_draft(
    input_data: StudyClinicalReportDraftInput, db: AsyncSession
) -> StudyClinicalReportDraftOut:
    if await repo.get_detail(db, input_data.study_id, input_data.doctor_id) is None:
        raise _not_found()
    draft = await repo.get_report_draft(db, input_data.study_id, for_update=True)
    if draft is None:
        if input_data.data.revision != 0:
            raise _report_revision_conflict()
        draft = StudyClinicalReportDraft(
            study_id=input_data.study_id,
            revision=1,
            updated_by=input_data.actor_id,
        )
        db.add(draft)
    else:
        if draft.revision != input_data.data.revision:
            raise _report_revision_conflict()
        draft.revision += 1

    data = input_data.data
    draft.indication = _clean_report_text(data.indication)
    draft.medications = _clean_report_text(data.medications)
    draft.referring_professional = _clean_report_text(data.referringProfessional)
    draft.technician = _clean_report_text(data.technician)
    draft.clinical_observations = _clean_report_text(data.clinicalObservations)
    draft.conclusion = _clean_report_text(data.conclusion)
    draft.updated_by = input_data.actor_id
    await db.flush()
    await auth_repo.log_audit_event(
        db,
        AuditEventType.STUDY_REPORT_DRAFT_UPDATED,
        user_id=input_data.actor_id,
        metadata={"target_study_id": str(input_data.study_id), "revision": draft.revision},
    )
    await db.commit()
    await db.refresh(draft)
    updated_by = await auth_repo.get_user_by_id(db, draft.updated_by)
    return _draft_out(input_data.study_id, draft, updated_by)


def _report_revision_conflict() -> HTTPException:
    return HTTPException(
        status_code=409,
        detail={
            "code": "REPORT_DRAFT_CONFLICT",
            "message": "El borrador cambió en otra sesión. Recargalo antes de guardar.",
        },
    )


async def get_clinical_report_preview(
    input_data: StudyIdInput, db: AsyncSession
) -> StudyClinicalReportPreviewOut:
    result = await repo.get_detail(db, input_data.study_id, input_data.doctor_id)
    if result is None:
        raise _not_found()
    study, patient, device, _, last_data_received_at = result
    draft = await repo.get_report_draft(db, study.id)
    version = await repo.next_clinical_report_version(db, study.id)
    snapshot, windows, issues = await _clinical_report_snapshot(
        db, study, patient, device, draft, version, last_data_received_at
    )
    blocking = [issue.message for issue in issues if issue.severity == "blocking"]
    return StudyClinicalReportPreviewOut(
        draft=_draft_out(
            study.id,
            draft,
            await auth_repo.get_user_by_id(db, draft.updated_by) if draft else None,
        ),
        snapshot=snapshot,
        snapshotHash=_snapshot_hash(snapshot),
        windows=windows,
        nextVersion=version,
        canGenerateDraft=True,
        canFinalize=not blocking,
        blockingReasons=blocking,
        issues=issues,
    )


def _validate_report_pdf(pdf: bytes) -> None:
    if len(pdf) > MAX_CLINICAL_REPORT_PDF_BYTES:
        raise HTTPException(
            status_code=413,
            detail={"code": "REPORT_TOO_LARGE", "message": "El PDF supera el límite de 20 MB."},
        )
    if len(pdf) < 32 or not pdf.startswith(b"%PDF-") or b"%%EOF" not in pdf[-2048:]:
        raise HTTPException(
            status_code=422,
            detail={"code": "INVALID_REPORT_PDF", "message": "El archivo no es un PDF válido."},
        )


async def finalize_clinical_report(
    input_data: StudyClinicalReportFinalizeInput, db: AsyncSession
) -> StudyClinicalReportVersionOut:
    _validate_report_pdf(input_data.pdf)
    locked = await repo.get_for_update(db, input_data.study_id, input_data.doctor_id)
    if locked is None:
        raise _not_found()
    study, patient = locked
    detail = await repo.get_detail(db, study.id, input_data.doctor_id)
    if detail is None:
        raise _not_found()
    _, _, device, _, last_data_received_at = detail
    draft = await repo.get_report_draft(db, study.id, for_update=True)
    if draft is None or draft.revision != input_data.draft_revision:
        raise _report_revision_conflict()
    version = await repo.next_clinical_report_version(db, study.id)
    snapshot, _, issues = await _clinical_report_snapshot(
        db, study, patient, device, draft, version, last_data_received_at
    )
    snapshot_hash = _snapshot_hash(snapshot)
    if snapshot_hash != input_data.snapshot_hash:
        raise HTTPException(
            status_code=409,
            detail={
                "code": "REPORT_SNAPSHOT_CONFLICT",
                "message": "El estudio cambió desde la vista previa. Generá el informe nuevamente.",
            },
        )
    blocking = [issue.message for issue in issues if issue.severity == "blocking"]
    if blocking:
        raise HTTPException(
            status_code=409,
            detail={"code": "REPORT_NOT_FINALIZABLE", "message": " ".join(blocking)},
        )

    actor = await auth_repo.get_user_by_id(db, input_data.actor_id)
    if actor is None:
        raise HTTPException(
            status_code=401,
            detail={"code": "UNAUTHORIZED", "message": "Sesión inválida."},
        )
    now = datetime.now(UTC)
    report = StudyClinicalReport(
        study_id=study.id,
        version=version,
        snapshot=snapshot,
        snapshot_sha256=snapshot_hash,
        pdf_data=input_data.pdf,
        pdf_sha256=hashlib.sha256(input_data.pdf).hexdigest(),
        pdf_byte_length=len(input_data.pdf),
        finalized_by=input_data.actor_id,
        finalized_at=now,
    )
    db.add(report)
    await db.flush()
    await auth_repo.log_audit_event(
        db,
        AuditEventType.STUDY_REPORT_FINALIZED,
        user_id=input_data.actor_id,
        metadata={
            "target_study_id": str(study.id),
            "report_id": str(report.id),
            "version": version,
            "pdf_sha256": report.pdf_sha256,
        },
    )
    await db.commit()
    return StudyClinicalReportVersionOut(
        id=report.id,
        studyId=study.id,
        version=version,
        finalizedAt=now,
        finalizedBy=actor.id,
        finalizedByName=actor.full_name,
        finalizedByRole=actor.role.value,
        pdfByteLength=report.pdf_byte_length,
        pdfSha256=report.pdf_sha256,
        snapshotSha256=report.snapshot_sha256,
    )


async def list_clinical_report_versions(
    input_data: StudyIdInput, db: AsyncSession
) -> StudyClinicalReportVersionsOut:
    if await repo.get_detail(db, input_data.study_id, input_data.doctor_id) is None:
        raise _not_found()
    rows = await repo.list_clinical_reports(db, input_data.study_id)
    return StudyClinicalReportVersionsOut(
        items=[
            StudyClinicalReportVersionOut(
                id=report.id,
                studyId=report.study_id,
                version=report.version,
                finalizedAt=report.finalized_at,
                finalizedBy=user.id,
                finalizedByName=user.full_name,
                finalizedByRole=user.role.value,
                pdfByteLength=report.pdf_byte_length,
                pdfSha256=report.pdf_sha256,
                snapshotSha256=report.snapshot_sha256,
            )
            for report, user in rows
        ]
    )


async def get_clinical_report_pdf(
    input_data: StudyIdInput, report_id: uuid.UUID, db: AsyncSession
) -> tuple[bytes, int]:
    if await repo.get_detail(db, input_data.study_id, input_data.doctor_id) is None:
        raise _not_found()
    report = await repo.get_clinical_report(db, report_id, input_data.study_id)
    if report is None:
        raise HTTPException(
            status_code=404,
            detail={"code": "REPORT_NOT_FOUND", "message": "Informe no encontrado."},
        )
    if input_data.actor_id is not None:
        await auth_repo.log_audit_event(
            db,
            AuditEventType.STUDY_REPORT_DOWNLOADED,
            user_id=input_data.actor_id,
            metadata={
                "target_study_id": str(input_data.study_id),
                "report_id": str(report.id),
                "version": report.version,
            },
        )
        await db.commit()
    return report.pdf_data, report.version
