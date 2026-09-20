"""Autenticación del chaleco.

Es el paralelo de `auth_dependencies` para dispositivos, y **no comparte nada**
con él a propósito: el chaleco no tiene sesión, no tiene cookie y no es un
usuario. Se identifica con `X-Device-Serial` + un bearer de alta entropía que se
compara contra `device.api_key_hash`.
"""

import hashlib
import hmac
from dataclasses import dataclass
from datetime import UTC, datetime

import structlog
from fastapi import Depends, Header, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.db.models.device import Device, DeviceStatus
from app.db.models.study_timeline_segment import TimeSyncSource
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
    #: `millis()` del equipo al momento de enviar. Con el epoch del puente forma
    #: el ancla que convierte los `t0Ms` de las tramas a UTC.
    uptime_ms: int
    firmware_version: str | None
    battery_pct: int | None
    #: Epoch UTC en ms leído por el puente WiFi **en el mismo instante** que
    #: `uptime_ms`. `None` cuando el firmware todavía no manda la cabecera y el
    #: modo estricto está apagado.
    bridge_epoch_ms: int | None = None
    time_sync_source: TimeSyncSource = TimeSyncSource.SERVER_RECEIVE
    time_sync_uncertainty_ms: int | None = None
    #: Diagnóstico del paquete de STATUS reenviado por el puente (`INTEGRACION.md`
    #: §11.1). Son el ÚNICO canal por el que este equipo puede avisar que perdió
    #: señal del paciente: backlog pisado, flash que no graba, trama descartada
    #: por CRC, muestras perdidas aguas arriba. Sin leerlas, un equipo que está
    #: perdiendo registro se ve idéntico a uno sano.
    #:
    #: Los cuatro son `None` cuando el firmware es anterior a septiembre de 2026:
    #: son aditivas y opcionales a propósito, así que la ausencia no es un error.
    #: Vienen acumuladas con OR desde el último POST confirmado, no son una foto.
    lead_flags: int | None = None
    loss_flags: int | None = None
    status_flags: int | None = None
    #: Estimación del peor atraso del tramo, del propio firmware.
    #:
    #: **No sirve para alertar.** Biomédica la midió con +98 % de error con
    #: electrodo seco y +44 % con gel, y explicó que no la van a corregir: el
    #: firmware asume 280 muestras por trama (un número de PhysioNet) y la señal
    #: real de esta placa comprime a 141 con electrodo seco. Se persiste como
    #: diagnóstico. El atraso de verdad sale de restar el `t0Ms` de las tramas
    #: contra `uptime_ms` de este mismo POST, que están en el mismo dominio.
    backlog_seconds: int | None = None

    def boot_epoch_ms(self, received_at: datetime) -> tuple[int, TimeSyncSource, int]:
        """Instante UTC en que el `millis()` del equipo valía cero.

        Es el ancla de todo el tramo de arranque: `UTC(trama) = boot_epoch + t0Ms`.

        Con el epoch del puente, las dos cifras de la resta las mide el mismo
        lado del enlace, así que la latencia de red y nuestro propio reloj
        quedan afuera. Sin él se cae al camino viejo (hora de recepción menos
        uptime), que es correcto pero arrastra esa latencia: el informe de
        Biomédica la midió en 5,1 s de mediana con picos de 22,8 s.

        Devuelve también con qué precisión se obtuvo, para que el visor pueda
        decir cuánto vale la hora que está mostrando.
        """
        if self.bridge_epoch_ms is not None:
            uncertainty = self.time_sync_uncertainty_ms
            if uncertainty is None:
                uncertainty = _DEFAULT_UNCERTAINTY_MS[self.time_sync_source]
            return self.bridge_epoch_ms - self.uptime_ms, self.time_sync_source, uncertainty
        received_ms = int(received_at.timestamp() * 1000)
        return (
            received_ms - self.uptime_ms,
            TimeSyncSource.SERVER_RECEIVE,
            _DEFAULT_UNCERTAINTY_MS[TimeSyncSource.SERVER_RECEIVE],
        )


#: Incertidumbre que asumimos cuando el puente no declara la suya. Para el
#: camino viejo es el orden del p95 que midió Biomédica: la hora derivada de
#: nuestra recepción no puede ser mejor que la latencia del pedido.
_DEFAULT_UNCERTAINTY_MS: dict[TimeSyncSource, int] = {
    TimeSyncSource.NTP: 250,
    TimeSyncSource.NONE: 3_600_000,
    TimeSyncSource.SERVER_RECEIVE: 7_000,
}


def _in_range(value: int | None, low: int, high: int) -> int | None:
    """El valor si cae dentro del rango declarado; `None` si no.

    **Descarta en vez de rechazar, y es deliberado.** Estas cuatro cabeceras son
    diagnóstico: contestar `422` por un byte fuera de rango tiraría al piso un
    lote de señal del paciente por un dato accesorio. Lo que se pierde
    descartando es una lectura; lo que se perdería rechazando son minutos de
    registro que el equipo va a tener que retransmitir.
    """
    if value is None or not low <= value <= high:
        return None
    return value


def _time_sync_error(code: str, message: str) -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
        detail={"code": code, "message": message},
    )


def _resolve_time_sync(
    bridge_epoch_ms: int | None,
    source: str | None,
    uncertainty_ms: int | None,
) -> tuple[int | None, TimeSyncSource, int | None]:
    """Valida las tres cabeceras de hora del puente (`docs/integracion-ingesta-con-horario.md`).

    Las tres van juntas o no va ninguna: un epoch sin declarar de dónde salió no
    se puede pesar contra los demás al corregir la deriva, y quedaría archivado
    como si fuera bueno.

    Con `ingest_require_time_sync` apagado, la ausencia de las tres es válida y
    se cae al ancla vieja. Lo que **nunca** es válido es un epoch presente pero
    absurdo: un puente con SNTP roto que mande 0 fecharía el estudio en 1970, y
    eso no se puede distinguir después de una hora real.
    """
    if bridge_epoch_ms is None and source is None and uncertainty_ms is None:
        if settings.ingest_require_time_sync:
            raise _time_sync_error(
                "DEVICE_TIME_SYNC_REQUIRED",
                "Faltan los headers X-Bridge-Epoch-Ms, X-Time-Sync-Source y "
                "X-Time-Sync-Uncertainty-Ms.",
            )
        return None, TimeSyncSource.SERVER_RECEIVE, None

    if bridge_epoch_ms is None or source is None:
        raise _time_sync_error(
            "DEVICE_TIME_SYNC_REQUIRED",
            "X-Bridge-Epoch-Ms y X-Time-Sync-Source van juntos.",
        )
    try:
        parsed_source = TimeSyncSource(source.strip().lower())
    except ValueError:
        raise _time_sync_error(
            "DEVICE_TIME_SYNC_REQUIRED",
            "X-Time-Sync-Source tiene que ser 'ntp' o 'none'.",
        ) from None
    if parsed_source is TimeSyncSource.SERVER_RECEIVE:
        # Es un valor nuestro, no del puente: dejarlo entrar permitiría que un
        # equipo se declare anclado por el servidor y saltee la validación.
        raise _time_sync_error(
            "DEVICE_TIME_SYNC_REQUIRED",
            "X-Time-Sync-Source tiene que ser 'ntp' o 'none'.",
        )
    if uncertainty_ms is not None and uncertainty_ms < 0:
        raise _time_sync_error(
            "DEVICE_TIME_INVALID", "X-Time-Sync-Uncertainty-Ms no puede ser negativo."
        )

    skew_ms = abs(bridge_epoch_ms - int(datetime.now(UTC).timestamp() * 1000))
    if skew_ms > settings.ingest_time_sync_max_skew_seconds * 1000:
        raise _time_sync_error(
            "DEVICE_TIME_INVALID",
            f"X-Bridge-Epoch-Ms está a {skew_ms // 1000} s de la hora del servidor.",
        )
    return bridge_epoch_ms, parsed_source, uncertainty_ms


async def get_authenticated_device(
    authorization: str | None = Header(default=None),
    x_device_serial: str | None = Header(default=None),
    x_device_uptime_ms: int | None = Header(default=None),
    x_firmware_version: str | None = Header(default=None, max_length=120),
    x_battery_pct: int | None = Header(default=None),
    x_bridge_epoch_ms: int | None = Header(default=None),
    x_time_sync_source: str | None = Header(default=None, max_length=32),
    x_time_sync_uncertainty_ms: int | None = Header(default=None),
    x_device_lead_flags: int | None = Header(default=None),
    x_device_loss_flags: int | None = Header(default=None),
    x_device_status_flags: int | None = Header(default=None),
    x_device_backlog_seconds: int | None = Header(default=None),
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

    bridge_epoch_ms, sync_source, sync_uncertainty_ms = _resolve_time_sync(
        x_bridge_epoch_ms, x_time_sync_source, x_time_sync_uncertainty_ms
    )

    return DeviceContext(
        device=device,
        uptime_ms=x_device_uptime_ms,
        firmware_version=x_firmware_version,
        battery_pct=x_battery_pct,
        bridge_epoch_ms=bridge_epoch_ms,
        time_sync_source=sync_source,
        time_sync_uncertainty_ms=sync_uncertainty_ms,
        lead_flags=_in_range(x_device_lead_flags, 0, 255),
        loss_flags=_in_range(x_device_loss_flags, 0, 255),
        status_flags=_in_range(x_device_status_flags, 0, 255),
        backlog_seconds=_in_range(x_device_backlog_seconds, 0, 65_535),
    )
