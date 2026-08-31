"""Rutas de la app móvil, bajo `/mobile`.

Todas usan `get_current_patient` (Bearer, nunca cookie) salvo el login y el
refresh, que son los que emiten el token. El prefijo está exento del chequeo de
Origin en `main.py` por la misma razón que `/ingest`: sin cookie no hay CSRF.
"""

import uuid

from fastapi import APIRouter, Depends, Query, Request, Response, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.request_context import client_ip
from app.dependencies.common_dependencies import get_db
from app.dependencies.patient_dependencies import PatientContext, get_current_patient
from app.modules.patient_app import patient_app_service as service
from app.modules.patient_app.patient_app_schemas import (
    MobileAccessOut,
    MobileAlertListInput,
    MobileAlertListResponse,
    MobileAlertStatus,
    MobileCatalogsOut,
    MobileDeviceOut,
    MobileLoginInput,
    MobileLoginRequest,
    MobilePatientOut,
    MobileRefreshRequest,
    MobileReportCreateInput,
    MobileReportCreateRequest,
    MobileReportGetInput,
    MobileReportListInput,
    MobileReportListResponse,
    MobileReportOut,
    MobileSessionOut,
    PushTokenInput,
    PushTokenRequest,
)

router = APIRouter()


@router.post("/auth/login", response_model=MobileSessionOut)
async def login(
    data: MobileLoginRequest,
    request: Request,
    db: AsyncSession = Depends(get_db),
) -> MobileSessionOut:
    """Login del paciente por email **o** DNI.

    La app no le pregunta al paciente cuál está escribiendo: si el texto no
    tiene `@`, el backend lo resuelve como DNI y traduce a email antes de
    hablar con Auth0, que es lo único que Auth0 entiende.
    """
    return await service.login(MobileLoginInput(data=data, ip=client_ip(request)), db)


@router.post("/auth/refresh", response_model=MobileAccessOut)
async def refresh(
    data: MobileRefreshRequest, db: AsyncSession = Depends(get_db)
) -> MobileAccessOut:
    return await service.refresh(data.refreshToken, db)


@router.post("/auth/logout", status_code=status.HTTP_204_NO_CONTENT)
async def logout(
    request: Request,
    context: PatientContext = Depends(get_current_patient),
    db: AsyncSession = Depends(get_db),
) -> Response:
    await service.logout(context.user, client_ip(request), db)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get("/me", response_model=MobilePatientOut)
async def get_me(
    context: PatientContext = Depends(get_current_patient),
    db: AsyncSession = Depends(get_db),
) -> MobilePatientOut:
    return await service.get_me(context.patient, db)


@router.get("/device", response_model=MobileDeviceOut)
async def get_device(
    context: PatientContext = Depends(get_current_patient),
    db: AsyncSession = Depends(get_db),
) -> MobileDeviceOut:
    return await service.get_device(context.patient, db)


@router.get("/alerts", response_model=MobileAlertListResponse)
async def list_alerts(
    limit: int = Query(default=20, ge=1, le=50),
    offset: int = Query(default=0, ge=0),
    # `alert_status` y no `status`: el módulo ya importa el `status` de FastAPI
    # para los códigos de respuesta, y el parámetro lo taparía dentro de la ruta.
    alert_status: MobileAlertStatus = Query(default=MobileAlertStatus.ALL, alias="status"),
    context: PatientContext = Depends(get_current_patient),
    db: AsyncSession = Depends(get_db),
) -> MobileAlertListResponse:
    return await service.list_alerts(
        MobileAlertListInput(
            patient_id=context.patient.id,
            limit=limit,
            offset=offset,
            status=alert_status,
        ),
        db,
    )


@router.get("/catalogs", response_model=MobileCatalogsOut)
async def get_catalogs(
    _: PatientContext = Depends(get_current_patient),
) -> MobileCatalogsOut:
    """Síntomas y actividades. Vienen del backend para que agregar una opción no
    dependa de que el paciente actualice la app desde la store."""
    return service.get_catalogs()


@router.get("/reports", response_model=MobileReportListResponse)
async def list_reports(
    limit: int = Query(default=20, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
    context: PatientContext = Depends(get_current_patient),
    db: AsyncSession = Depends(get_db),
) -> MobileReportListResponse:
    return await service.list_reports(
        MobileReportListInput(patient_id=context.patient.id, limit=limit, offset=offset), db
    )


@router.get("/reports/{report_id}", response_model=MobileReportOut)
async def get_report(
    report_id: uuid.UUID,
    context: PatientContext = Depends(get_current_patient),
    db: AsyncSession = Depends(get_db),
) -> MobileReportOut:
    return await service.get_report(
        MobileReportGetInput(patient_id=context.patient.id, report_id=report_id), db
    )


@router.post("/reports", response_model=MobileReportOut, status_code=status.HTTP_201_CREATED)
async def create_report(
    data: MobileReportCreateRequest,
    context: PatientContext = Depends(get_current_patient),
    db: AsyncSession = Depends(get_db),
) -> MobileReportOut:
    """Bitácora: qué sintió y qué estaba haciendo.

    Con `alertId` es la respuesta a un push y la fila es idempotente por alerta;
    sin él, es un registro manual. En los dos casos se guarda hora de pared: el
    paciente puede marcarlo antes de que el chaleco suba la señal de ese rato.
    """
    return await service.create_report(
        MobileReportCreateInput(user=context.user, patient_id=context.patient.id, data=data), db
    )


@router.post("/push-tokens", status_code=status.HTTP_204_NO_CONTENT)
async def register_push_token(
    data: PushTokenRequest,
    context: PatientContext = Depends(get_current_patient),
    db: AsyncSession = Depends(get_db),
) -> Response:
    await service.register_push_token(PushTokenInput(user=context.user, data=data), db)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post("/push-tokens/remove", status_code=status.HTTP_204_NO_CONTENT)
async def unregister_push_token(
    data: PushTokenRequest,
    context: PatientContext = Depends(get_current_patient),
    db: AsyncSession = Depends(get_db),
) -> Response:
    """POST y no DELETE: el token va en el body y no en la URL.

    Un `ExponentPushToken[...]` en el path o el query string termina en los
    access logs de Vercel y en cualquier proxy del medio.
    """
    await service.unregister_push_token(PushTokenInput(user=context.user, data=data), db)
    return Response(status_code=status.HTTP_204_NO_CONTENT)
