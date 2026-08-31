from datetime import UTC, datetime

from fastapi import APIRouter, BackgroundTasks, Depends, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.dependencies.common_dependencies import get_db
from app.dependencies.device_dependencies import DeviceContext, get_authenticated_device
from app.modules.ingest import ingest_service as service
from app.modules.ingest.ingest_schemas import (
    DeviceStatusAckOut,
    DeviceStatusInput,
    DeviceStatusRequest,
    IngestAckOut,
    IngestFramesInput,
)

router = APIRouter()


@router.post(
    "/ecg-frames",
    response_model=IngestAckOut,
    status_code=202,
    summary="Recibe un lote de tramas ECG comprimidas del chaleco",
    openapi_extra={
        "requestBody": {
            "required": True,
            "content": {
                "application/octet-stream": {
                    "schema": {"type": "string", "format": "binary"},
                    "description": "N tramas de 256 bytes concatenadas, little-endian.",
                }
            },
        }
    },
)
async def ingest_ecg_frames(
    request: Request,
    background: BackgroundTasks,
    ctx: DeviceContext = Depends(get_authenticated_device),
    db: AsyncSession = Depends(get_db),
) -> IngestAckOut:
    """Cuerpo binario crudo: N × 256 B, sin JSON, sin multipart, sin base64.

    Es lo que un ESP32-C3 puede transmitir con overhead cero. Los metadatos por
    trama (`seq`, `t0Ms`, `bootId`, CRC) ya viajan dentro de cada trama; en los
    headers va solo la identidad del equipo.

    Responde 202 y no 200: los bytes quedaron guardados de forma durable, pero
    todavía no procesados.
    """
    payload = await request.body()
    return await service.ingest_frames(
        ctx,
        IngestFramesInput(payload=payload, received_at=datetime.now(UTC)),
        db,
        background,
    )


@router.post(
    "/device-status",
    response_model=DeviceStatusAckOut,
    summary="Aviso del chaleco fuera del ciclo de envío",
)
async def report_device_status(
    data: DeviceStatusRequest,
    background: BackgroundTasks,
    ctx: DeviceContext = Depends(get_authenticated_device),
    db: AsyncSession = Depends(get_db),
) -> DeviceStatusAckOut:
    """Canal corto para lo que no puede esperar al próximo lote.

    El equipo sube tramas una vez por hora. Si el aviso de "mala calidad de
    señal durante dT" viajara con el lote, el paciente se enteraría hasta 60
    minutos después y esa hora de estudio ya está perdida.

    Misma autenticación que `/ecg-frames` (`X-Device-Serial` + API key) y mismo
    motivo para estar exento del chequeo de Origin: no hay cookie de por medio.
    """
    return await service.report_device_status(
        ctx, DeviceStatusInput(data=data, received_at=datetime.now(UTC)), db, background
    )
