"""Patients schemas."""

import re
import uuid
from dataclasses import dataclass
from datetime import date, datetime

from pydantic import Field, field_validator

from app.db.models.patient import PatientSex, PatientStudyStatus
from app.db.models.user import User
from app.modules._base_schema import CamelModel


class PatientOut(CamelModel):
    id: uuid.UUID
    fullName: str
    dni: str
    birthDate: date
    sex: PatientSex
    assignedDeviceId: uuid.UUID | None
    assignedDeviceSerial: str | None
    studyStatus: PatientStudyStatus
    lastDataReceivedAt: datetime | None
    contactEmail: str | None
    contactPhone: str | None
    doctorId: uuid.UUID | None
    doctorName: str | None
    #: Si tiene cuenta en la app móvil. Es lo que decide si la ficha ofrece
    #: "Regenerar contraseña" o "Crear acceso".
    hasAppAccount: bool


class PatientListResponse(CamelModel):
    items: list[PatientOut]
    total: int
    limit: int
    offset: int


class MetricValue(CamelModel):
    trend: str


class HeartRateMetric(MetricValue):
    averageBpm: int
    deltaBpm: int


class EventsMetric(MetricValue):
    count: int
    delta: int


class AdherenceMetric(MetricValue):
    value: int
    deltaPp: int


class PatientSummaryOut(CamelModel):
    windowHours: int
    heartRate: HeartRateMetric | None
    eventsDetected: EventsMetric | None
    adherencePercent: AdherenceMetric | None


class PatientCreateRequest(CamelModel):
    fullName: str = Field(min_length=1, max_length=240)
    dni: str = Field(min_length=1, max_length=50)
    birthDate: date
    sex: PatientSex
    #: Obligatorio desde que todo paciente tiene cuenta en la app: es su usuario
    #: en Auth0 y el único canal por el que puede recuperar la contraseña solo.
    contactEmail: str = Field(min_length=3, max_length=320)
    contactPhone: str | None = Field(default=None, max_length=50)
    doctorId: uuid.UUID | None = None

    @field_validator("birthDate")
    @classmethod
    def validate_birth_date(cls, value: date) -> date:
        if value > date.today():
            raise ValueError("La fecha de nacimiento no puede ser futura")
        return value

    @field_validator("contactEmail")
    @classmethod
    def validate_email(cls, value: str) -> str:
        normalized = _normalize_optional_email(value)
        if normalized is None:  # pragma: no cover - `min_length` ya lo impide
            raise ValueError("Email requerido")
        return normalized


class PatientUpdateRequest(CamelModel):
    fullName: str | None = Field(default=None, min_length=1, max_length=240)
    dni: str | None = Field(default=None, min_length=1, max_length=50)
    birthDate: date | None = None
    sex: PatientSex | None = None
    contactEmail: str | None = Field(default=None, max_length=320)
    contactPhone: str | None = Field(default=None, max_length=50)

    @field_validator("birthDate")
    @classmethod
    def validate_birth_date(cls, value: date | None) -> date | None:
        if value is not None and value > date.today():
            raise ValueError("La fecha de nacimiento no puede ser futura")
        return value

    @field_validator("contactEmail")
    @classmethod
    def validate_email(cls, value: str | None) -> str | None:
        return _normalize_optional_email(value)


def _normalize_optional_email(value: str | None) -> str | None:
    if value is None:
        return None
    normalized = value.strip().lower()
    if not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", normalized):
        raise ValueError("Email inválido")
    return normalized


@dataclass(frozen=True)
class PatientRow:
    id: uuid.UUID
    #: Cuenta de la app móvil, si la tiene.
    user_id: uuid.UUID | None
    first_name: str
    last_name: str
    date_of_birth: date | None
    dni: str | None
    sex: PatientSex
    study_status: PatientStudyStatus
    last_data_received_at: datetime | None
    email: str | None
    phone: str | None
    assigned_device_id: uuid.UUID | None
    assigned_device_serial: str | None
    doctor_id: uuid.UUID
    doctor_name: str | None


@dataclass(frozen=True)
class PatientListInput:
    doctor_id: uuid.UUID | None
    q: str | None
    status: list[PatientStudyStatus] | None
    limit: int
    offset: int
    sort: str
    order: str
    has_device: bool | None


class PatientCreateOut(PatientOut):
    """El paciente recién creado + su contraseña inicial.

    `generatedPassword` viaja **una sola vez**, en el 201. Auth0 guarda solo el
    hash: no hay ningún endpoint que la pueda volver a leer, y el portal la
    muestra en un diálogo con botón de copiar antes de perderla para siempre.
    """

    generatedPassword: str


class PatientPasswordOut(CamelModel):
    """Contraseña nueva, también entregada una sola vez."""

    password: str


@dataclass(frozen=True)
class PatientCreateInput:
    doctor_id: uuid.UUID | None
    requesting_user: User
    data: PatientCreateRequest


@dataclass(frozen=True)
class PatientUpdateInput:
    doctor_id: uuid.UUID | None
    patient_id: uuid.UUID
    data: PatientUpdateRequest
    actor_id: uuid.UUID | None = None


@dataclass(frozen=True)
class PatientIdInput:
    doctor_id: uuid.UUID | None
    patient_id: uuid.UUID
    actor_id: uuid.UUID | None = None


@dataclass(frozen=True)
class PatientSummaryInput:
    doctor_id: uuid.UUID | None
    patient_id: uuid.UUID
    window_hours: int
