"""Patients schemas."""

import uuid
from dataclasses import dataclass
from datetime import date, datetime

from pydantic import Field

from app.db.models.patient import PatientSex, PatientStudyStatus
from app.modules._base_schema import CamelModel


class PatientOut(CamelModel):
    id: uuid.UUID
    fullName: str
    dni: str
    birthDate: date
    sex: PatientSex
    assignedDeviceId: uuid.UUID | None
    studyStatus: PatientStudyStatus
    lastDataReceivedAt: datetime | None
    contactEmail: str | None
    contactPhone: str | None


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
    contactEmail: str | None = Field(default=None, max_length=320)
    contactPhone: str | None = Field(default=None, max_length=50)


class PatientUpdateRequest(CamelModel):
    fullName: str | None = Field(default=None, min_length=1, max_length=240)
    dni: str | None = Field(default=None, min_length=1, max_length=50)
    birthDate: date | None = None
    sex: PatientSex | None = None
    contactEmail: str | None = Field(default=None, max_length=320)
    contactPhone: str | None = Field(default=None, max_length=50)


@dataclass(frozen=True)
class PatientRow:
    id: uuid.UUID
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


@dataclass(frozen=True)
class PatientListInput:
    doctor_id: uuid.UUID
    q: str | None
    status: list[PatientStudyStatus] | None
    limit: int
    offset: int
    sort: str
    order: str


@dataclass(frozen=True)
class PatientCreateInput:
    doctor_id: uuid.UUID
    data: PatientCreateRequest


@dataclass(frozen=True)
class PatientUpdateInput:
    doctor_id: uuid.UUID
    patient_id: uuid.UUID
    data: PatientUpdateRequest


@dataclass(frozen=True)
class PatientIdInput:
    doctor_id: uuid.UUID
    patient_id: uuid.UUID


@dataclass(frozen=True)
class PatientSummaryInput:
    doctor_id: uuid.UUID
    patient_id: uuid.UUID
    window_hours: int
