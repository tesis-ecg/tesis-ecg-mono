"""Autenticación del chaleco.

Es el paralelo de `auth_dependencies` para dispositivos, y **no comparte nada**
con él a propósito: el chaleco no tiene sesión, no tiene cookie y no es un
usuario. Se identifica con `X-Device-Serial` + un bearer de alta entropía que se
compara contra `device.api_key_hash`.
"""

import hashlib
import hmac
from dataclasses import dataclass

import structlog
from fastapi import Depends, Header, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models.device import Device, DeviceStatus
from app.dependencies.common_dependencies import get_db

logger = structlog.get_logger(__name__)

#: Estados en los que un equipo puede subir señal. Uno en mantenimiento o dado
#: de baja no debería estar puesto sobre un paciente: si manda datos, es un
#: error de logística y hay que verlo, no absorberlo.
INGESTABLE_STATUSES = {DeviceStatus.ASSIGNED, DeviceStatus.AVAILABLE}


def _unauthorized() -> HTTPException:
    """401 genérico, a propósito.

    No distingue "serial inexistente" de "API key incorrecta": si lo hiciera,
    cualquiera podría enumerar qué seriales existen probando contra un endpoint
    expuesto a internet. La distinción sí queda en el log del servidor, con el
    `requestId`, que es donde la necesita un operador.
    """
    return HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail={"code": "DEVICE_UNAUTHORIZED", "message": "Dispositivo no autorizado."},
        headers={"WWW-Authenticate": "Bearer"},
    )


@dataclass(frozen=True)
class DeviceContext:
    device: Device
    #: `millis()` del equipo al momento de enviar. Con la hora de recepción del
    #: servidor forma el ancla que convierte los `t0Ms` de las tramas a UTC.
    uptime_ms: int
    firmware_version: str | None
    battery_pct: int | None


async def get_authenticated_device(
    authorization: str | None = Header(default=None),
    x_device_serial: str | None = Header(default=None),
    x_device_uptime_ms: int | None = Header(default=None),
    x_firmware_version: str | None = Header(default=None, max_length=120),
    x_battery_pct: int | None = Header(default=None),
    db: AsyncSession = Depends(get_db),
) -> DeviceContext:
    # Import diferido: `app.modules.ingest` importa este módulo (mismo patrón
    # que usa `auth_dependencies` con `auth_repository`).
    from app.modules.ingest import ingest_repository as repo

    if not x_device_serial:
        raise _unauthorized()
    scheme, _, token = (authorization or "").partition(" ")
    if scheme.lower() != "bearer" or not token:
        raise _unauthorized()

    device = await repo.get_device_by_serial(db, x_device_serial)
    if device is None:
        await logger.awarning("ingest_unknown_serial", serial=x_device_serial)
        raise _unauthorized()

    # `compare_digest` y no `==`: comparar hashes con el operador normal corta en
    # el primer byte distinto y filtra información por tiempo de respuesta.
    provided = hashlib.sha256(token.encode()).hexdigest()
    if not hmac.compare_digest(provided, device.api_key_hash):
        await logger.awarning("ingest_bad_api_key", serial=x_device_serial)
        raise _unauthorized()

    if device.status not in INGESTABLE_STATUSES:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "code": "DEVICE_NOT_INGESTABLE",
                "message": f"El dispositivo está en estado '{device.status.value}'.",
            },
        )

    if x_device_uptime_ms is None or x_device_uptime_ms < 0:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail={
                "code": "DEVICE_UPTIME_REQUIRED",
                "message": "Falta el header X-Device-Uptime-Ms.",
            },
        )
    if x_battery_pct is not None and not 0 <= x_battery_pct <= 100:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail={"code": "DEVICE_BATTERY_INVALID", "message": "X-Battery-Pct fuera de rango."},
        )

    return DeviceContext(
        device=device,
        uptime_ms=x_device_uptime_ms,
        firmware_version=x_firmware_version,
        battery_pct=x_battery_pct,
    )
