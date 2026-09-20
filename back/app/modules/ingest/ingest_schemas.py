import enum
import uuid
from dataclasses import dataclass
from datetime import datetime

from pydantic import Field

from app.modules._base_schema import CamelModel


class IngestAckOut(CamelModel):
    """El ACK del protocolo, no un simple "recibido".

    `framesAccepted` es un **delta contiguo**: cuántas tramas nuevas puede dar
    por entregadas el equipo, contadas desde la más vieja sin confirmar y
    cortando en el primer hueco de `seq` (`INTEGRACION.md` §4.6). Si llegan la
    10, la 11 y la 13, se confirman 2 — la 12 falta y el cursor de lectura del
    equipo no la puede saltear.
    """

    framesReceived: int
    framesAccepted: int
    framesRejected: int
    framesDuplicate: int
    lastAcceptedSeq: int | None
    batchId: uuid.UUID | None
    studyId: uuid.UUID
    serverTime: datetime


@dataclass(frozen=True)
class IngestFramesInput:
    payload: bytes
    received_at: datetime


class VestStatusEvent(enum.StrEnum):
    """Lo que el chaleco puede reportar fuera del ciclo de envío."""

    #: SQI malo sostenido durante un intervalo continuo dT.
    SIGNAL_QUALITY_BAD = "signal_quality_bad"
    #: Uno o más electrodos sin contacto durante dT.
    LEAD_OFF = "lead_off"
    #: El equipo volvió a medir bien. No genera alerta; cierra el episodio.
    SIGNAL_RECOVERED = "signal_recovered"
    #: **Latido neutro: "estoy encendido", y nada más** (`INTEGRACION.md` §11.5).
    #:
    #: Los otros tres afirman algo sobre la señal, y por eso ninguno servía de
    #: latido. El caso que este valor cubre es el que hoy es invisible: un equipo
    #: que está encendido y **no produce tramas** —la flash no monta, el
    #: front-end no inicializó— no existe para el backend, y no se distingue de
    #: uno apagado.
    #:
    #: Mandar `lead_off` ahí sería inventar un diagnóstico, y usar
    #: `signal_recovered` de latido sería peor: desde el commit `9933292` ese
    #: valor escribe `placement_ok = true`, o sea que le estaría afirmando al
    #: paciente que el chaleco está bien puesto cada diez minutos sin nada que lo
    #: sostenga. `alive` solo escribe telemetría: no toca la colocación, no crea
    #: alerta y no manda push.
    ALIVE = "alive"


class DeviceStatusRequest(CamelModel):
    """Aviso del chaleco **fuera de los baches de envío**.

    El equipo sube tramas una vez por hora. Si esperara al lote para contar que
    la señal viene mal, el paciente se enteraría hasta 60 minutos tarde y ese
    tiempo de estudio ya está perdido. Este endpoint es el canal corto: un JSON
    de pocos bytes, con la misma autenticación por API key que la ingesta.

    El co-procesador WiFi se enciende ~90 s por día para el envío; este aviso es
    un despertar extra y por eso el cuerpo es mínimo.
    """

    event: VestStatusEvent
    #: Cuánto lleva el equipo detectando el problema (el dT del requerimiento).
    durationSeconds: int = Field(ge=0, le=86_400)
    #: Índice de calidad de señal reportado por el firmware, si lo calculó.
    sqi: int | None = Field(default=None, ge=0, le=3)
    batteryPct: int | None = Field(default=None, ge=0, le=100)


class DeviceStatusAckOut(CamelModel):
    #: `False` cuando el aviso se absorbió por el debounce o el equipo no tiene
    #: paciente asignado. El chaleco no necesita hacer nada distinto, pero queda
    #: explícito para depurar desde el firmware.
    notified: bool
    alertId: uuid.UUID | None
    serverTime: datetime


@dataclass(frozen=True)
class DeviceStatusInput:
    data: DeviceStatusRequest
    received_at: datetime
