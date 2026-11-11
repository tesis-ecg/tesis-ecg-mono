"""Hallazgos del médico anclados a muestras; nunca producen avisos al paciente."""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models.audit_event import AuditEventType
from app.db.models.ecg_event import ECGEvent, ECGEventSeverity, ECGEventType, ECGEventValidation
from app.db.models.study import Study, StudyStatus
from app.db.models.study_timeline_segment import StudyTimelineSegment
from app.modules.auth import auth_repository as auth_repo
from app.modules.studies import studies_repository as repo
from app.modules.studies.corrections import plan_reclassification
from app.modules.studies.reclassification_service import (
    _finding_out,
    _not_found,
    _recalculated_metrics,
    _unprocessable,
)
from app.modules.studies.studies_schemas import (
    ManualFindingInput,
    ReclassificationInput,
    ReclassificationOut,
    ReclassificationRequest,
)


def manual_sample_span(
    study: Study, runs: list[StudyTimelineSegment], start_ms: int, end_ms: int
) -> tuple[int, int]:
    """Invierte la misma proyección del visor, sin inventar muestras en huecos."""
    if end_ms <= start_ms:
        raise _unprocessable("INVALID_FINDING_RANGE", "Seleccioná un tramo de señal válido.")
    if runs:
        candidates = [r for r in runs if r.start_epoch_ms <= start_ms < end_ms <= r.end_epoch_ms]
        if len(candidates) != 1:
            raise _unprocessable(
                "INVALID_FINDING_RANGE", "El tramo cruza un hueco o queda fuera de la señal."
            )
        run = candidates[0]
        origin = run.start_sample_index
        epoch = run.start_epoch_ms
        scale = run.sample_count / max(run.end_epoch_ms - epoch, 1)
    else:
        origin = 0
        epoch = int(study.started_at.timestamp() * 1000)
        scale = study.sample_rate / 1000
        if not epoch <= start_ms < end_ms <= epoch + round(study.samples_count / scale):
            raise _unprocessable("INVALID_FINDING_RANGE", "El tramo queda fuera de la señal.")
    start = origin + round((start_ms - epoch) * scale)
    end = origin + round((end_ms - epoch) * scale)
    if not 0 <= start < end <= study.samples_count:
        raise _unprocessable(
            "INVALID_FINDING_RANGE", "El tramo debe contener al menos una muestra."
        )
    return start, end


async def create_manual_finding(
    input_data: ManualFindingInput, db: AsyncSession
) -> ReclassificationOut:
    locked = await repo.get_for_update(db, input_data.study_id, input_data.doctor_id)
    if locked is None:
        raise _not_found()
    study, _ = locked
    data = input_data.data
    key = f"manual:{input_data.actor_id}:{data.clientRequestId}"
    event = await repo.get_manual_finding_by_key(db, study.id, key)
    if event is not None:
        original = (event.event_metadata or {}).get("request")
        if original != data.model_dump(mode="json"):
            raise _unprocessable(
                "FINDING_REQUEST_CONFLICT", "El identificador ya se usó con otro hallazgo."
            )
    else:
        if study.status not in (StudyStatus.IN_PROGRESS, StudyStatus.COMPLETED):
            raise _unprocessable("INVALID_STUDY_STATUS", "El estudio no admite hallazgos manuales.")
        runs = await repo.list_timeline_segments(db, study.id)
        start, end = manual_sample_span(study, runs, data.startEpochMs, data.endEpochMs)
        batch = await repo.get_manual_finding_batch(db, study, start)
        if batch is None:
            raise _unprocessable(
                "INVALID_FINDING_RANGE", "No se encontró el lote de señal seleccionado."
            )
        names = await repo.user_names(db, {input_data.actor_id})
        event = ECGEvent(
            study_id=study.id,
            batch_id=batch.id,
            event_type=ECGEventType.OTHER,
            severity=ECGEventSeverity(data.severity.upper()),
            timestamp_in_recording=start / study.sample_rate,
            duration_seconds=(end - start) / study.sample_rate,
            model_version=None,
            confidence_score=None,
            dedupe_key=key,
            validation_status=ECGEventValidation.RECLASSIFIED,
            validated_by=input_data.actor_id,
            validated_at=datetime.now(UTC),
            event_metadata={
                "source": "manual",
                "kind": data.kind.value,
                "studyId": str(study.id),
                "startSampleIndex": start,
                "sampleCount": end - start,
                "createdBy": str(input_data.actor_id),
                "createdByName": names.get(input_data.actor_id),
                "request": data.model_dump(mode="json"),
            },
            correction=plan_reclassification(
                "other",
                (start, end),
                "relabel",
                None,
                target_kind=data.kind.value,
                severity=data.severity,
            ).as_json(),
        )
        db.add(event)
        await db.flush()
        study.events_count = await repo.count_study_events(db, study.id)
        await auth_repo.log_audit_event(
            db,
            AuditEventType.ECG_EVENT_CREATED,
            user_id=input_data.actor_id,
            metadata={
                "target_study_id": str(study.id),
                "event_id": str(event.id),
                "kind": data.kind.value,
                "severity": data.severity,
                "start_sample": start,
                "end_sample": end,
            },
        )
    # Release the recording lock before reading S3 to recalculate metrics.
    await db.commit()
    finding = await _finding_out(db, study, event)
    return ReclassificationOut(
        finding=finding,
        holterMetrics=await _recalculated_metrics(
            ReclassificationInput(
                doctor_id=input_data.doctor_id,
                study_id=study.id,
                finding_id=event.id,
                actor_id=input_data.actor_id,
                data=ReclassificationRequest(action="confirm"),
            ),
            db,
        ),
    )
