"""Dashboard schemas."""

import uuid
from dataclasses import dataclass
from datetime import date, datetime

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


class DashboardActivityPointOut(CamelModel):
    """Un día de la serie semanal. Siempre hay siete, con ceros donde no pasó nada."""

    date: date
    alerts: int
    reports: int
    studies: int


class DashboardTrendOut(CamelModel):
    """Flujo de los últimos 7 días contra los 7 anteriores.

    Es *flujo* y no *stock*: `current` es cuántas alertas entraron esta semana,
    no cuántas hay pendientes ahora. Por eso no reusa `KpiDeltaOut` y expone los
    dos totales — el front rotula la comparación en vez de inventar un delta que
    parecería referirse al número grande de la tarjeta.
    """

    current: int
    previous: int


class DashboardSeverityBucketOut(CamelModel):
    # Minúsculas, igual que `DashboardAlertOut.severity`.
    severity: str
    count: int


class DashboardFleetOut(CamelModel):
    """Salud de la flota: cuántos chalecos asignados están transmitiendo.

    `transmitting` usa el mismo corte de `dashboard_stale_hours` que el watchdog,
    así que los dos widgets no pueden contradecirse.
    """

    assigned: int
    transmitting: int


class DashboardActivityOut(CamelModel):
    days: list[DashboardActivityPointOut]
    alertsTrend: DashboardTrendOut
    studiesTrend: DashboardTrendOut
    patientsTrend: DashboardTrendOut
    pendingBySeverity: list[DashboardSeverityBucketOut]
    fleet: DashboardFleetOut


class DashboardOverviewOut(CamelModel):
    kpis: DashboardKpisOut
    alerts: list[DashboardAlertOut]
    attentionPatients: list[AttentionPatientOut]
    runningStudies: list[RunningStudyOut]
    deviceWatchdog: list[DeviceWatchdogOut]
    activity: DashboardActivityOut


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
class DashboardActivityInput:
    doctor_id: uuid.UUID | None
    days: int


@dataclass(frozen=True)
class DashboardOverviewInput:
    doctor_id: uuid.UUID | None
    widget_limit: int
    alerts_limit: int
