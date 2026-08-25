"""Devices schemas."""

import uuid
from dataclasses import dataclass
from datetime import datetime

from pydantic import Field

from app.db.models.device import DeviceStatus
from app.modules._base_schema import CamelModel


class HolterOut(CamelModel):
    id: uuid.UUID
    serial: str
    model: str
    firmwareVersion: str | None
    status: DeviceStatus
    assignedPatientId: uuid.UUID | None
    assignedPatientName: str | None
    #: Estudio `in_progress` de este equipo, si lo hay. Es lo que permite saltar
    #: del inventario al registro que se está grabando ahora mismo.
    activeStudyId: uuid.UUID | None
    assignedDoctorId: uuid.UUID | None
    assignedDoctorName: str | None
    lastSeenAt: datetime | None
    createdAt: datetime


class HolterCreateOut(HolterOut):
    apiKey: str


class HolterApiKeyOut(CamelModel):
    """La API key en claro. Se devuelve UNA vez y no se puede volver a leer."""

    deviceId: uuid.UUID
    serial: str
    apiKey: str
    rotatedAt: datetime


class HolterListResponse(CamelModel):
    items: list[HolterOut]
    total: int
    limit: int
    offset: int


class HolterCreateRequest(CamelModel):
    serial: str = Field(min_length=1, max_length=120)
    model: str = Field(min_length=1, max_length=120)
    firmwareVersion: str | None = Field(default=None, max_length=120)


class HolterUpdateRequest(CamelModel):
    model: str | None = Field(default=None, min_length=1, max_length=120)
    firmwareVersion: str | None = Field(default=None, max_length=120)
    status: DeviceStatus | None = None


class AssignHolterRequest(CamelModel):
    patientId: uuid.UUID


class ReassignHolterRequest(CamelModel):
    patientId: uuid.UUID


class AssignDoctorRequest(CamelModel):
    doctorId: uuid.UUID


class HolterHealthOut(CamelModel):
    deviceId: uuid.UUID
    serial: str
    model: str
    firmwareVersion: str | None
    telemetryAvailable: bool
    batteryPercent: int | None
    signalDbm: int | None
    signalQuality: str | None
    lastPingAt: datetime
    nextScheduledUploadAt: datetime | None
    uploadsToday: int | None
    storageUsedMb: int | None
    storageTotalMb: int | None


@dataclass(frozen=True)
class HolterListInput:
    doctor_id: uuid.UUID | None
    q: str | None
    status: list[DeviceStatus] | None
    limit: int
    offset: int


@dataclass(frozen=True)
class HolterCreateInput:
    data: HolterCreateRequest
    actor_id: uuid.UUID | None = None


@dataclass(frozen=True)
class HolterUpdateInput:
    device_id: uuid.UUID
    data: HolterUpdateRequest
    actor_id: uuid.UUID | None = None


@dataclass(frozen=True)
class HolterIdInput:
    # None => admin (vista global, sin chequeo de ownership) o rol de solo lectura
    doctor_id: uuid.UUID | None
    device_id: uuid.UUID
    actor_id: uuid.UUID | None = None


@dataclass(frozen=True)
class AssignHolterInput:
    # None => admin (vista global, sin chequeo de ownership ni de médico del paciente)
    doctor_id: uuid.UUID | None
    device_id: uuid.UUID
    patient_id: uuid.UUID
    actor_id: uuid.UUID | None = None


@dataclass(frozen=True)
class AssignDoctorInput:
    device_id: uuid.UUID
    data: AssignDoctorRequest
    actor_id: uuid.UUID | None = None
