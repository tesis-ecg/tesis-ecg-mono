from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.dependencies.auth_dependencies import RoleScope, get_doctor_scope
from app.dependencies.common_dependencies import get_db
from app.modules.dashboard import dashboard_service as service
from app.modules.dashboard.dashboard_schemas import (
    AttentionPatientOut,
    AttentionPatientsInput,
    DashboardAlertOut,
    DashboardAlertsInput,
    DashboardKpisInput,
    DashboardKpisOut,
    DeviceWatchdogInput,
    DeviceWatchdogOut,
    RunningStudiesInput,
    RunningStudyOut,
)

router = APIRouter()


@router.get("/kpis", response_model=DashboardKpisOut)
async def get_kpis(
    scope: RoleScope = Depends(get_doctor_scope),
    db: AsyncSession = Depends(get_db),
) -> DashboardKpisOut:
    return await service.get_kpis(DashboardKpisInput(doctor_id=scope.doctor_id), db)


@router.get("/alerts", response_model=list[DashboardAlertOut])
async def list_alerts(
    limit: int = Query(default=settings.dashboard_alerts_limit, ge=1, le=50),
    scope: RoleScope = Depends(get_doctor_scope),
    db: AsyncSession = Depends(get_db),
) -> list[DashboardAlertOut]:
    return await service.list_alerts(
        DashboardAlertsInput(doctor_id=scope.doctor_id, limit=limit), db
    )


@router.get("/attention-patients", response_model=list[AttentionPatientOut])
async def list_attention_patients(
    limit: int = Query(default=settings.dashboard_widget_limit, ge=1, le=50),
    scope: RoleScope = Depends(get_doctor_scope),
    db: AsyncSession = Depends(get_db),
) -> list[AttentionPatientOut]:
    return await service.list_attention_patients(
        AttentionPatientsInput(doctor_id=scope.doctor_id, limit=limit), db
    )


@router.get("/running-studies", response_model=list[RunningStudyOut])
async def list_running_studies(
    limit: int = Query(default=settings.dashboard_widget_limit, ge=1, le=50),
    scope: RoleScope = Depends(get_doctor_scope),
    db: AsyncSession = Depends(get_db),
) -> list[RunningStudyOut]:
    return await service.list_running_studies(
        RunningStudiesInput(doctor_id=scope.doctor_id, limit=limit), db
    )


@router.get("/device-watchdog", response_model=list[DeviceWatchdogOut])
async def list_device_watchdog(
    limit: int = Query(default=settings.dashboard_widget_limit, ge=1, le=50),
    scope: RoleScope = Depends(get_doctor_scope),
    db: AsyncSession = Depends(get_db),
) -> list[DeviceWatchdogOut]:
    return await service.list_device_watchdog(
        DeviceWatchdogInput(doctor_id=scope.doctor_id, limit=limit), db
    )
