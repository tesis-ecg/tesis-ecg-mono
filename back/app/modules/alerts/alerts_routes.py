import uuid

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.dependencies.auth_dependencies import RoleScope, get_doctor_scope
from app.dependencies.common_dependencies import get_db
from app.modules.alerts import alerts_service as service
from app.modules.alerts.alerts_schemas import (
    AlertIdInput,
    AlertListInput,
    AlertListResponse,
    AlertOut,
)

router = APIRouter()


@router.get("", response_model=AlertListResponse)
async def list_alerts(
    acknowledged: bool | None = Query(default=None),
    severity: list[str] | None = Query(default=None, alias="severity"),
    severity_bracket: list[str] | None = Query(default=None, alias="severity[]"),
    limit: int = Query(default=20, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
    scope: RoleScope = Depends(get_doctor_scope),
    db: AsyncSession = Depends(get_db),
) -> AlertListResponse:
    """Alertas clínicas del médico (o de todos, para el admin).

    Solo las alertas reales: las sintéticas de "equipo sin transmitir" que el
    dashboard intercala no son filas y no se pueden atender — esas viven en el
    widget de watchdog.
    """
    return await service.list_alerts(
        AlertListInput(
            doctor_id=scope.doctor_id,
            acknowledged=acknowledged,
            severity=severity or severity_bracket,
            limit=limit,
            offset=offset,
        ),
        db,
    )


@router.post("/{alert_id}/acknowledge", response_model=AlertOut)
async def acknowledge_alert(
    alert_id: uuid.UUID,
    scope: RoleScope = Depends(get_doctor_scope),
    db: AsyncSession = Depends(get_db),
) -> AlertOut:
    """Marca la alerta como atendida y deja constancia de quién y cuándo."""
    return await service.acknowledge_alert(
        AlertIdInput(
            doctor_id=scope.doctor_id,
            alert_id=alert_id,
            actor_id=scope.user.id,
        ),
        db,
    )
