"""Dashboard service."""

import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.db.models.alert import Alert
from app.db.models.device import Device
from app.db.models.ecg_event import ECGEventType
from app.db.models.patient import Patient
from app.db.models.study import Study, StudyStatus
from app.modules._alert_kind import resolve_alert_kind
from app.modules.dashboard import dashboard_repository as repo
from app.modules.dashboard.dashboard_schemas import (
    AttentionPatientOut,
    AttentionPatientsInput,
    DashboardAlertOut,
    DashboardAlertsInput,
    DashboardKpisInput,
    DashboardKpisOut,
    DashboardOverviewInput,
    DashboardOverviewOut,
    DeviceWatchdogInput,
    DeviceWatchdogOut,
    RunningStudiesInput,
    RunningStudyOut,
)

# Orden de prioridad del merge de alertas. Las 4 severities del ORM mapean 1:1
# en minúsculas contra el SEVERITY del FE.
_SEVERITY_RANK = {"critical": 0, "high": 1, "medium": 2, "low": 3}

_DEVICE_ALERT_SEVERITY = "medium"


def _patient_name(patient: Patient) -> str:
    return f"{patient.first_name} {patient.last_name}".strip()


def _stale_before() -> datetime:
    return datetime.now(UTC) - timedelta(hours=settings.dashboard_stale_hours)


def _duration_ms(study: Study) -> int:
    # Duplicado deliberado del helper privado de studies_service: importar un
    # símbolo privado cruzando la frontera de módulo sería peor.
    if study.duration_ms is not None:
        return study.duration_ms
    if study.ended_at is not None:
        return int((study.ended_at - study.started_at).total_seconds() * 1000)
    if study.status == StudyStatus.IN_PROGRESS:
        return max(int((datetime.now(UTC) - study.started_at).total_seconds() * 1000), 0)
    return 0


def _alert_out(
    alert: Alert,
    patient: Patient,
    event_type: ECGEventType | None,
    event_metadata: dict[str, object] | None,
    study_id: uuid.UUID | None,
) -> DashboardAlertOut:
    return DashboardAlertOut(
        id=str(alert.id),
        patientId=patient.id,
        patientName=_patient_name(patient),
        kind=resolve_alert_kind(alert.kind, event_type, event_metadata),
        severity=alert.severity.value.lower(),
        detectedAt=alert.created_at,
        studyId=study_id,
    )


def _device_alert_out(device: Device, patient: Patient) -> DashboardAlertOut:
    return DashboardAlertOut(
        id=f"device-{device.id}",
        patientId=patient.id,
        patientName=_patient_name(patient),
        kind="device_offline",
        severity=_DEVICE_ALERT_SEVERITY,
        # detectedAt es requerido por el FE: si el device nunca reportó, cae en created_at.
        detectedAt=device.last_seen_at or device.created_at,
        studyId=None,
    )


def _attention_patient_out(patient: Patient, device_serial: str | None) -> AttentionPatientOut:
    return AttentionPatientOut(
        id=patient.id,
        fullName=_patient_name(patient),
        studyStatus=patient.study_status,
        lastDataReceivedAt=patient.last_data_received_at,
        deviceSerial=device_serial,
    )


def _running_study_out(study: Study, patient: Patient, device: Device) -> RunningStudyOut:
    return RunningStudyOut(
        id=study.id,
        patientName=_patient_name(patient),
        startedAt=study.started_at,
        durationMs=_duration_ms(study),
        deviceSerial=device.serial_number,
    )


def _watchdog_out(device: Device, signal_quality: str | None, reason: str) -> DeviceWatchdogOut:
    return DeviceWatchdogOut(
        deviceId=device.id,
        serial=device.serial_number,
        status=device.status,
        batteryPercent=device.last_battery_pct,
        signalQuality=signal_quality,
        lastSeenAt=device.last_seen_at,
        reason=reason,
    )


async def get_kpis(input_data: DashboardKpisInput, db: AsyncSession) -> DashboardKpisOut:
    active_patients, running_studies, pending_alerts, stale_devices = await repo.get_kpi_counts(
        db, input_data.doctor_id, _stale_before()
    )
    return DashboardKpisOut(
        activePatients=active_patients,
        pendingAlerts=pending_alerts + stale_devices,
        runningStudies=running_studies,
        # TODO: deltas requieren histórico (fuera de alcance TES-33)
        activePatientsDelta=None,
        pendingAlertsDelta=None,
        runningStudiesDelta=None,
    )


async def list_alerts(
    input_data: DashboardAlertsInput, db: AsyncSession
) -> list[DashboardAlertOut]:
    rows = await repo.list_pending_alerts(db, input_data.doctor_id, input_data.limit)
    stale_devices = await repo.list_stale_devices(
        db, input_data.doctor_id, _stale_before(), input_data.limit
    )
    alerts = [
        _alert_out(alert, patient, event_type, event_metadata, study_id)
        for alert, patient, event_type, event_metadata, study_id in rows
    ]
    alerts.extend(_device_alert_out(device, patient) for device, patient in stale_devices)
    # Cada fuente ya viene ordenada y cortada por su propia clave final (el repo
    # ordena por severidad y después por fecha), así que el merge solo intercala.
    # Dos pasadas estables: severidad asc, y dentro de cada severidad detectedAt desc.
    alerts.sort(key=lambda item: item.detectedAt, reverse=True)
    alerts.sort(key=lambda item: _SEVERITY_RANK.get(item.severity, len(_SEVERITY_RANK)))
    return alerts[: input_data.limit]


async def list_attention_patients(
    input_data: AttentionPatientsInput, db: AsyncSession
) -> list[AttentionPatientOut]:
    rows = await repo.list_attention_patients(db, input_data.doctor_id, input_data.limit)
    return [_attention_patient_out(patient, serial) for patient, serial in rows]


async def list_running_studies(
    input_data: RunningStudiesInput, db: AsyncSession
) -> list[RunningStudyOut]:
    rows = await repo.list_running_studies(db, input_data.doctor_id, input_data.limit)
    return [_running_study_out(study, patient, device) for study, patient, device in rows]


async def list_device_watchdog(
    input_data: DeviceWatchdogInput, db: AsyncSession
) -> list[DeviceWatchdogOut]:
    rows = await repo.list_watchdog_devices(
        db,
        input_data.doctor_id,
        _stale_before(),
        settings.dashboard_low_battery_pct,
        input_data.limit,
    )
    items: list[DeviceWatchdogOut] = []
    for device, reason in rows:
        items.append(_watchdog_out(device, None, reason))
    return items


async def get_overview(
    input_data: DashboardOverviewInput, db: AsyncSession
) -> DashboardOverviewOut:
    kpis = await get_kpis(DashboardKpisInput(doctor_id=input_data.doctor_id), db)
    alerts = await list_alerts(
        DashboardAlertsInput(doctor_id=input_data.doctor_id, limit=input_data.alerts_limit), db
    )
    attention_patients = await list_attention_patients(
        AttentionPatientsInput(doctor_id=input_data.doctor_id, limit=input_data.widget_limit),
        db,
    )
    running_studies = await list_running_studies(
        RunningStudiesInput(doctor_id=input_data.doctor_id, limit=input_data.widget_limit), db
    )
    watchdog = await list_device_watchdog(
        DeviceWatchdogInput(doctor_id=input_data.doctor_id, limit=input_data.widget_limit), db
    )
    return DashboardOverviewOut(
        kpis=kpis,
        alerts=alerts,
        attentionPatients=attention_patients,
        runningStudies=running_studies,
        deviceWatchdog=watchdog,
    )
