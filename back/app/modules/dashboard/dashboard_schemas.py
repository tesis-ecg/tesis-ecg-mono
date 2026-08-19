"""Dashboard schemas."""

import uuid
from dataclasses import dataclass
from datetime import datetime

from app.db.models.device import DeviceStatus
from app.db.models.patient import PatientStudyStatus
from app.modules._base_schema import CamelModel


class KpiDeltaOut(CamelModel):
    value: int
    trend: str


class DashboardKpisOut(CamelModel):
    activePatients: int
    pendingAlerts: int
    runningStudies: int
    activePatientsDelta: KpiDeltaOut | None
    pendingAlertsDelta: KpiDeltaOut | None
    runningStudiesDelta: KpiDeltaOut | None


class DashboardAlertOut(CamelModel):
    # `id` es str y no uuid: las alertas sintéticas de dispositivo usan "device-<uuid>".
    id: str
    patientId: uuid.UUID
    patientName: str
    # `kind` y `severity` son str y no los enums del ORM: AlertSeverity y ECGEventType
    # se guardan en MAYÚSCULAS (no llevan values_callable) y el FE espera minúsculas.
    kind: str
    severity: str
    detectedAt: datetime
    studyId: uuid.UUID | None


class AttentionPatientOut(CamelModel):
    id: uuid.UUID
    fullName: str
    studyStatus: PatientStudyStatus
    lastDataReceivedAt: datetime | None
    deviceSerial: str | None


class RunningStudyOut(CamelModel):
    id: uuid.UUID
    patientName: str
    startedAt: datetime
    durationMs: int
    deviceSerial: str


class DeviceWatchdogOut(CamelModel):
    deviceId: uuid.UUID
    serial: str
    status: DeviceStatus
    batteryPercent: int | None
    signalQuality: str | None
    lastSeenAt: datetime | None
    reason: str


class DashboardOverviewOut(CamelModel):
    kpis: DashboardKpisOut
    alerts: list[DashboardAlertOut]
    attentionPatients: list[AttentionPatientOut]
    runningStudies: list[RunningStudyOut]
    deviceWatchdog: list[DeviceWatchdogOut]


@dataclass(frozen=True)
class DashboardKpisInput:
    doctor_id: uuid.UUID | None


@dataclass(frozen=True)
class DashboardAlertsInput:
    doctor_id: uuid.UUID | None
    limit: int


@dataclass(frozen=True)
class AttentionPatientsInput:
    doctor_id: uuid.UUID | None
    limit: int


@dataclass(frozen=True)
class RunningStudiesInput:
    doctor_id: uuid.UUID | None
    limit: int


@dataclass(frozen=True)
class DeviceWatchdogInput:
    doctor_id: uuid.UUID | None
    limit: int


@dataclass(frozen=True)
class DashboardOverviewInput:
    doctor_id: uuid.UUID | None
    widget_limit: int
    alerts_limit: int
