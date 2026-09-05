"""DTOs de la app móvil del paciente.

Separados de los del portal a propósito: el paciente ve su propio caso, no una
grilla de casos. `MobileDeviceOut` no reusa `HolterHealthOut` porque ese DTO
responde 404 cuando el equipo nunca se conectó — para el médico es un dato
faltante, pero para el paciente "todavía no se conectó" es un estado legítimo
que la pantalla tiene que saber dibujar.
"""

import enum
import uuid
from dataclasses import dataclass
from datetime import date, datetime

from pydantic import Field, field_validator

from app.db.models.patient import PatientSex, PatientStudyStatus
from app.db.models.patient_report import PatientReportSource
from app.db.models.push_token import PushPlatform
from app.db.models.user import User
from app.modules._base_schema import CamelModel
from app.modules.patient_app import catalogs

# --------------------------------------------------------------------------- #
# Auth
# --------------------------------------------------------------------------- #


class MobileLoginRequest(CamelModel):
    #: Email o DNI. La app no pregunta cuál de los dos es: si no tiene `@`, el
    #: backend lo resuelve como DNI antes de hablar con Auth0.
    identifier: str = Field(min_length=1, max_length=320)
    password: str = Field(min_length=1, max_length=256)


class MobileRefreshRequest(CamelModel):
    refreshToken: str = Field(min_length=1, max_length=4096)


class MobileDoctorOut(CamelModel):
    fullName: str
    email: str | None


class MobilePatientOut(CamelModel):
    id: uuid.UUID
    fullName: str
    dni: str
    birthDate: date | None
    sex: PatientSex
    email: str | None
    phone: str | None
    studyStatus: PatientStudyStatus
    doctor: MobileDoctorOut | None


class MobileSessionOut(CamelModel):
    accessToken: str
    refreshToken: str
    expiresAt: datetime
    patient: MobilePatientOut


class MobileAccessOut(CamelModel):
    accessToken: str
    expiresAt: datetime


# --------------------------------------------------------------------------- #
# Dispositivo
# --------------------------------------------------------------------------- #


class MobileDeviceOut(CamelModel):
    """Estado del chaleco, contado para el paciente y no para el técnico.

    `state` resume en una palabra lo que la pantalla necesita decidir:
    - ``none``: no tiene chaleco asignado
    - ``never_connected``: asignado pero todavía no se encendió
    - ``recording``: hay un estudio en curso y llegaron datos hace poco
    - ``stale``: hace más de `dashboard_stale_hours` que no manda nada

    `vestPlacement` va aparte de `state` y no es un valor más suyo: el equipo
    puede estar grabando y transmitiendo perfecto y no registrar nada igual
    porque no hace contacto con la piel. Son dos preguntas distintas y la
    pantalla las dibuja en dos lugares distintos.
    """

    hasDevice: bool
    state: str
    #: ``ok`` | ``bad`` | ``unknown``. ``unknown`` es que el equipo nunca
    #: reportó su colocación, que no es lo mismo que estar bien puesto.
    vestPlacement: str
    vestPlacementAt: datetime | None
    deviceId: uuid.UUID | None
    serial: str | None
    model: str | None
    firmwareVersion: str | None
    batteryPercent: int | None
    lastSeenAt: datetime | None
    lastDataReceivedAt: datetime | None
    studyId: uuid.UUID | None
    studyStartedAt: datetime | None


# --------------------------------------------------------------------------- #
# Avisos y bitácora
# --------------------------------------------------------------------------- #


class MobileAlertOut(CamelModel):
    id: uuid.UUID
    kind: str
    severity: str
    message: str
    detectedAt: datetime
    requiresResponse: bool
    #: Compatibilidad con la app existente. Solo es verdadero cuando el aviso
    #: requiere respuesta y todavía no hay una bitácora asociada.
    needsReport: bool
    reportId: uuid.UUID | None
    answeredAt: datetime | None


class MobileAlertListResponse(CamelModel):
    items: list[MobileAlertOut]
    total: int
    pendingTotal: int
    limit: int
    offset: int


class MobileReportOut(CamelModel):
    id: uuid.UUID
    occurredAt: datetime
    source: PatientReportSource
    symptoms: list[str]
    symptomsOther: str | None
    activity: str
    activityOther: str | None
    notes: str | None
    alertId: uuid.UUID | None
    studyId: uuid.UUID | None
    createdAt: datetime


class MobileReportListResponse(CamelModel):
    items: list[MobileReportOut]
    total: int
    limit: int
    offset: int


class MobileReportCreateRequest(CamelModel):
    #: Cuándo le pasó, no cuándo lo cargó. Ausente = ahora.
    occurredAt: datetime | None = None
    #: Presente cuando el registro responde a un push.
    alertId: uuid.UUID | None = None
    symptoms: list[str] = Field(min_length=1, max_length=len(catalogs.SYMPTOMS))
    symptomsOther: str | None = Field(default=None, max_length=500)
    activity: str = Field(min_length=1, max_length=64)
    activityOther: str | None = Field(default=None, max_length=500)
    notes: str | None = Field(default=None, max_length=2000)

    @field_validator("symptoms")
    @classmethod
    def validate_symptoms(cls, value: list[str]) -> list[str]:
        unknown = [item for item in value if item not in catalogs.SYMPTOM_VALUES]
        if unknown:
            raise ValueError(f"Síntoma desconocido: {unknown[0]}")
        deduped = list(dict.fromkeys(value))
        if catalogs.EXCLUSIVE_SYMPTOM in deduped and len(deduped) > 1:
            raise ValueError("'No sentí nada' no se combina con otros síntomas")
        return deduped

    @field_validator("activity")
    @classmethod
    def validate_activity(cls, value: str) -> str:
        if value not in catalogs.ACTIVITY_VALUES:
            raise ValueError("Actividad desconocida")
        return value


class CatalogOptionOut(CamelModel):
    value: str
    label: str


class MobileCatalogsOut(CamelModel):
    symptoms: list[CatalogOptionOut]
    activities: list[CatalogOptionOut]


# --------------------------------------------------------------------------- #
# Push
# --------------------------------------------------------------------------- #


class PushTokenRequest(CamelModel):
    #: `ExponentPushToken[...]`. No se valida el formato acá: Expo puede cambiarlo
    #: y un token que no le sirve lo rechaza el propio servicio, que es quien sabe.
    token: str = Field(min_length=1, max_length=255)
    platform: PushPlatform


# --------------------------------------------------------------------------- #
# Inputs de servicio
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class MobileLoginInput:
    data: MobileLoginRequest
    ip: str | None


@dataclass(frozen=True)
class MobileReportCreateInput:
    user: User
    patient_id: uuid.UUID
    data: MobileReportCreateRequest


@dataclass(frozen=True)
class MobileReportListInput:
    patient_id: uuid.UUID
    limit: int
    offset: int


class MobileAlertStatus(enum.StrEnum):
    """Qué avisos pide la app.

    `ACTIONABLE` es la bandeja operativa del paciente: pedidos clínicos todavía
    sin responder más, como máximo, el episodio vigente de chaleco mal colocado.
    Las otras tres vistas se conservan para compatibilidad con clientes previos.

    `ANSWERED` no es "todo lo que no está pendiente": el aviso de chaleco mal
    colocado no pide respuesta, así que solo aparece en `ALL` o en `ACTIONABLE`
    mientras el equipo siga reportando mala colocación.
    """

    ALL = "all"
    PENDING = "pending"
    ANSWERED = "answered"
    ACTIONABLE = "actionable"


@dataclass(frozen=True)
class MobileAlertListInput:
    patient_id: uuid.UUID
    limit: int
    offset: int
    status: MobileAlertStatus


@dataclass(frozen=True)
class MobileReportGetInput:
    patient_id: uuid.UUID
    report_id: uuid.UUID


@dataclass(frozen=True)
class PushTokenInput:
    user: User
    data: PushTokenRequest
