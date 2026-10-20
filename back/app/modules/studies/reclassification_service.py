"""`POST /studies/{id}/findings/{finding_id}/reclassification` (req. 5, «evidencia»).

El médico corrige lo que el motor afirmó: lo confirma, lo descarta, dice que el
tramo es ruido o le cambia la clase a sus latidos. Tres reglas ordenan todo:

- **El original no se pisa.** `event_type` y `metadata.kind` quedan como los dejó
  el motor; la corrección vive en `ecg_event.correction`, con quién, cuándo y la
  nota (`validated_*`), y un `audit_event`. Un `reset` la borra y el hallazgo
  vuelve a ser el del motor.
- **El tramo se congela.** `correction.spanStart/spanEnd` se copian al corregir:
  el recálculo (`corrections.doctor_corrections`) lee solo eso y no depende de
  que el motor vuelva a mover el hallazgo.
- **Las métricas se calculan a pedido**, así que el borrador y la vista previa
  del informe ven la corrección enseguida. Los informes ya finalizados guardan su
  snapshot y no cambian; uno que se previsualizó antes de la corrección choca
  con `REPORT_SNAPSHOT_CONFLICT` al finalizar, porque el hash cambió.

El recálculo corre **después del commit y fuera del lock del estudio**: lee los
latidos de S3, y mantener bloqueada la fila del estudio (que la ingesta y el
motor necesitan) durante esa lectura frenaría la grabación. Si falla, la
corrección queda guardada y la respuesta lo dice.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime

from fastapi import HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models.audit_event import AuditEventType
from app.db.models.ecg_event import ECGEvent, ECGEventValidation
from app.db.models.study import Study
from app.modules.auth import auth_repository as auth_repo
from app.modules.studies import findings_service, studies_service
from app.modules.studies import studies_repository as repo
from app.modules.studies.annotations import (
    annotation_kind,
    event_view,
    wall_clock_resolver,
)
from app.modules.studies.corrections import (
    STATUS_BY_ACTION,
    InvalidReclassification,
    ReclassificationPlan,
    event_sample_span,
    is_reclassifiable,
    plan_reclassification,
)
from app.modules.studies.studies_schemas import (
    HolterMetricsOut,
    ReclassificationInput,
    ReclassificationOut,
    StudyFindingOut,
    StudyIdInput,
)

logger = logging.getLogger(__name__)


def _not_found() -> HTTPException:
    return HTTPException(
        status_code=404,
        detail={"code": "NOT_FOUND", "message": "Estudio no encontrado."},
    )


def _unprocessable(code: str, message: str) -> HTTPException:
    return HTTPException(status_code=422, detail={"code": code, "message": message})


def _plan(input_data: ReclassificationInput, event: ECGEvent) -> ReclassificationPlan | None:
    """`None` para un `reset`; levanta 422 si el hallazgo o la acción no valen."""
    span = event_sample_span(event)
    if not is_reclassifiable(event) or span is None:
        raise _unprocessable(
            "FINDING_NOT_RECLASSIFIABLE",
            "Este hallazgo no se puede reclasificar.",
        )
    data = input_data.data
    if data.action == "reset":
        if data.beatLabel is not None:
            raise _unprocessable(
                "INVALID_RECLASSIFICATION", "beatLabel no aplica a restablecer el hallazgo."
            )
        return None
    if (data.action == "relabel") != (data.beatLabel is not None):
        raise _unprocessable(
            "INVALID_RECLASSIFICATION",
            "beatLabel es obligatorio con «relabel» y no se admite con otras acciones.",
        )
    try:
        return plan_reclassification(annotation_kind(event), span, data.action, data.beatLabel)
    except InvalidReclassification as error:
        raise _unprocessable(
            "INVALID_RECLASSIFICATION",
            "Ese hallazgo no admite la clase indicada.",
        ) from error


async def reclassify_finding(
    input_data: ReclassificationInput, db: AsyncSession
) -> ReclassificationOut:
    # Mismo bloqueo que el cierre y la pasada del motor: serializa la corrección
    # contra el motor, que no toca los hallazgos corregidos pero sí escribe
    # vecinos y avanza el cursor.
    locked = await repo.get_for_update(db, input_data.study_id, input_data.doctor_id)
    if locked is None:
        raise _not_found()
    study, _ = locked
    event = await repo.get_study_event_for_update(db, study.id, input_data.finding_id)
    if event is None:
        raise _not_found()

    plan = _plan(input_data, event)
    previous_status = event.validation_status.value
    previous_label = (event.correction or {}).get("toLabel")
    data = input_data.data
    if plan is None:
        event.validation_status = ECGEventValidation.PENDING
        event.validated_by = None
        event.validated_at = None
        event.validation_note = None
        event.correction = None
    else:
        event.validation_status = STATUS_BY_ACTION[plan.action]
        event.validated_by = input_data.actor_id
        event.validated_at = datetime.now(UTC)
        event.validation_note = data.note
        event.correction = plan.as_json()
    await auth_repo.log_audit_event(
        db,
        AuditEventType.ECG_EVENT_RECLASSIFIED,
        user_id=input_data.actor_id,
        metadata={
            "target_study_id": str(study.id),
            "event_id": str(event.id),
            "action": data.action,
            "from_kind": annotation_kind(event),
            "effective_kind": plan.effective_kind if plan else None,
            "from_label": plan.from_label if plan else None,
            "to_label": plan.to_label if plan else None,
            "previous_status": previous_status,
            "previous_label": previous_label,
        },
    )
    await db.commit()

    finding = await _finding_out(db, study, event)
    return ReclassificationOut(
        finding=finding, holterMetrics=await _recalculated_metrics(input_data, db)
    )


async def _finding_out(db: AsyncSession, study: Study, event: ECGEvent) -> StudyFindingOut:
    """El hallazgo como lo devuelve `/findings`, con el mismo nombre y hora de pared."""
    runs = await repo.list_timeline_segments(db, study.id)
    view = event_view(event, study, wall_clock_resolver(study, runs))
    if view is None:  # pragma: no cover - el tramo ya se validó al corregir
        raise _unprocessable("FINDING_NOT_RECLASSIFIABLE", "Este hallazgo no se puede ubicar.")
    names = await repo.user_names(
        db, {event.validated_by} if event.validated_by is not None else set()
    )
    return findings_service.finding_out(view, names)


async def _recalculated_metrics(
    input_data: ReclassificationInput, db: AsyncSession
) -> HolterMetricsOut:
    try:
        return await studies_service.get_holter_metrics(
            StudyIdInput(doctor_id=input_data.doctor_id, study_id=input_data.study_id), db
        )
    except Exception:
        # La corrección ya está commiteada: un fallo de S3 acá no la deshace. El
        # cliente vuelve a pedir `/holter-metrics` cuando quiera ver las cifras.
        logger.exception("holter metrics recalculation failed after reclassification")
        return studies_service._metrics_unavailable("pending", "RECALCULATION_FAILED")
