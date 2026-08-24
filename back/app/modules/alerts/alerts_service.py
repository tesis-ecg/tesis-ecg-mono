"""Alerts service.

Hasta acá la ingesta generaba filas en `alert` y el único lugar donde se veían
era un widget del dashboard, de solo lectura. `seen_at`, `acknowledged_at` y
`acknowledged_by` existían en el modelo desde el schema inicial y nadie los
escribía: no había forma de que un médico dijera "esta ya la vi".
"""

from datetime import UTC, datetime

from fastapi import HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models.alert import Alert, AlertSeverity
from app.db.models.audit_event import AuditEventType
from app.db.models.ecg_event import ECGEventType
from app.db.models.patient import Patient
from app.modules.alerts import alerts_repository as repo
from app.modules.alerts.alerts_schemas import (
    AlertIdInput,
    AlertListInput,
    AlertListResponse,
    AlertOut,
)
from app.modules.auth import auth_repository as auth_repo

#: Las severities viajan en minúsculas por la API (el ORM las guarda en
#: MAYÚSCULAS porque el Enum no lleva `values_callable`).
_SEVERITY_BY_NAME = {severity.value.lower(): severity for severity in AlertSeverity}


def _alert_kind(event_type: ECGEventType, event_metadata: dict[str, object]) -> str:
    if event_metadata.get("kind") == "symptom_marker":
        return "symptom_marker"
    if event_type == ECGEventType.OTHER:
        return "other"
    return event_type.value.lower()


def _alert_out(
    alert: Alert,
    patient: Patient,
    event_type: ECGEventType,
    event_metadata: dict[str, object],
    study_id: object,
    acknowledged_by_name: str | None,
) -> AlertOut:
    return AlertOut(
        id=alert.id,
        patientId=patient.id,
        patientName=f"{patient.first_name} {patient.last_name}".strip(),
        kind=_alert_kind(event_type, event_metadata),
        severity=alert.severity.value.lower(),
        message=alert.message,
        detectedAt=alert.created_at,
        studyId=study_id,  # type: ignore[arg-type]
        seenAt=alert.seen_at,
        acknowledgedAt=alert.acknowledged_at,
        acknowledgedByName=acknowledged_by_name,
    )


async def list_alerts(input_data: AlertListInput, db: AsyncSession) -> AlertListResponse:
    severities = (
        [_SEVERITY_BY_NAME[name] for name in input_data.severity if name in _SEVERITY_BY_NAME]
        if input_data.severity
        else None
    )
    rows, total, pending_total = await repo.list_alerts(
        db,
        doctor_id=input_data.doctor_id,
        acknowledged=input_data.acknowledged,
        severities=severities,
        limit=input_data.limit,
        offset=input_data.offset,
    )
    return AlertListResponse(
        items=[_alert_out(*row) for row in rows],
        total=total,
        limit=input_data.limit,
        offset=input_data.offset,
        pendingTotal=pending_total,
    )


async def acknowledge_alert(input_data: AlertIdInput, db: AsyncSession) -> AlertOut:
    """Deja constancia de que un médico revisó la alerta.

    Como el cierre de un estudio, es un acto clínico: acusar dos veces responde
    409 y no un 200 silencioso, para que un doble click no borre quién fue el
    primero en verla ni cuándo.
    """
    alert = await repo.get_for_update(db, input_data.alert_id, input_data.doctor_id)
    if alert is None:
        raise HTTPException(
            status_code=404,
            detail={"code": "ALERT_NOT_FOUND", "message": "Alerta no encontrada."},
        )
    if alert.acknowledged_at is not None:
        raise HTTPException(
            status_code=409,
            detail={"code": "ALERT_ALREADY_ACKNOWLEDGED", "message": "La alerta ya fue atendida."},
        )

    now = datetime.now(UTC)
    alert.acknowledged_at = now
    # Un admin puede acusar sin ser médico de nadie: `acknowledged_by` queda en
    # null y la trazabilidad la da el `audit_event`, que sí guarda el usuario.
    alert.acknowledged_by = input_data.doctor_id
    if alert.seen_at is None:
        alert.seen_at = now

    await auth_repo.log_audit_event(
        db,
        AuditEventType.ALERT_ACKNOWLEDGED,
        user_id=input_data.actor_id,
        metadata={"target_alert_id": str(alert.id), "patient_id": str(alert.patient_id)},
    )
    await db.commit()

    refreshed = await repo.get_detail(db, input_data.alert_id, input_data.doctor_id)
    if refreshed is None:  # pragma: no cover - se acaba de commitear
        raise HTTPException(
            status_code=404,
            detail={"code": "ALERT_NOT_FOUND", "message": "Alerta no encontrada."},
        )
    return _alert_out(*refreshed)
