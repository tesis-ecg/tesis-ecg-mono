"""Dashboard repository."""

import uuid
from datetime import datetime

from sqlalchemy import case, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models.alert import Alert, AlertSeverity
from app.db.models.device import Device, DeviceStatus
from app.db.models.ecg_event import ECGEvent, ECGEventType
from app.db.models.patient import Patient, PatientStudyStatus
from app.db.models.study import Study, StudyStatus

# El LIMIT tiene que cortar por la misma clave con la que el service ordena, o las
# alertas críticas viejas quedan afuera del widget cuando hay muchas recientes leves.
_SEVERITY_RANK = case(
    {
        AlertSeverity.CRITICAL: 0,
        AlertSeverity.HIGH: 1,
        AlertSeverity.MEDIUM: 2,
        AlertSeverity.LOW: 3,
    },
    value=Alert.severity,
    else_=4,
)


async def count_active_patients(db: AsyncSession, doctor_id: uuid.UUID | None) -> int:
    statement = (
        select(func.count())
        .select_from(Patient)
        .where(
            Patient.deleted_at.is_(None),
            Patient.study_status == PatientStudyStatus.ACTIVE,
        )
    )
    if doctor_id is not None:
        statement = statement.where(Patient.doctor_id == doctor_id)
    return await db.scalar(statement) or 0


async def count_running_studies(db: AsyncSession, doctor_id: uuid.UUID | None) -> int:
    # Mismos joins y filtros que list_running_studies: si el count no joineara Device,
    # el KPI y el widget mostrarían números distintos ante un device soft-deleteado.
    statement = (
        select(func.count())
        .select_from(Study)
        .join(Patient, Study.patient_id == Patient.id)
        .join(Device, Study.device_id == Device.id)
        .where(
            Study.deleted_at.is_(None),
            Study.status == StudyStatus.IN_PROGRESS,
            Patient.deleted_at.is_(None),
            Device.deleted_at.is_(None),
        )
    )
    if doctor_id is not None:
        statement = statement.where(Patient.doctor_id == doctor_id)
    return await db.scalar(statement) or 0


async def count_pending_alerts(db: AsyncSession, doctor_id: uuid.UUID | None) -> int:
    # El join a ECGEvent replica el de list_pending_alerts (que lo necesita para el
    # `kind`): sin él, un evento soft-deleteado haría que el KPI cuente una alerta
    # que el widget no lista.
    statement = (
        select(func.count())
        .select_from(Alert)
        .join(Patient, Alert.patient_id == Patient.id)
        .join(ECGEvent, Alert.event_id == ECGEvent.id)
        .where(
            Alert.deleted_at.is_(None),
            Alert.acknowledged_at.is_(None),
            Patient.deleted_at.is_(None),
            ECGEvent.deleted_at.is_(None),
        )
    )
    if doctor_id is not None:
        statement = statement.where(Patient.doctor_id == doctor_id)
    return await db.scalar(statement) or 0


async def count_stale_devices(
    db: AsyncSession, doctor_id: uuid.UUID | None, stale_before: datetime
) -> int:
    statement = (
        select(func.count())
        .select_from(Device)
        .join(Patient, Device.patient_id == Patient.id)
        .where(
            Device.deleted_at.is_(None),
            Device.status == DeviceStatus.ASSIGNED,
            Device.patient_id.is_not(None),
            or_(Device.last_seen_at.is_(None), Device.last_seen_at < stale_before),
            Patient.deleted_at.is_(None),
        )
    )
    if doctor_id is not None:
        statement = statement.where(Patient.doctor_id == doctor_id)
    return await db.scalar(statement) or 0


async def get_kpi_counts(
    db: AsyncSession, doctor_id: uuid.UUID | None, stale_before: datetime
) -> tuple[int, int, int, int]:
    patient_scope = [] if doctor_id is None else [Patient.doctor_id == doctor_id]
    active_patients = (
        select(func.count())
        .select_from(Patient)
        .where(
            Patient.deleted_at.is_(None),
            Patient.study_status == PatientStudyStatus.ACTIVE,
            *patient_scope,
        )
        .scalar_subquery()
    )
    running_studies = (
        select(func.count())
        .select_from(Study)
        .join(Patient, Study.patient_id == Patient.id)
        .join(Device, Study.device_id == Device.id)
        .where(
            Study.deleted_at.is_(None),
            Study.status == StudyStatus.IN_PROGRESS,
            Patient.deleted_at.is_(None),
            Device.deleted_at.is_(None),
            *patient_scope,
        )
        .scalar_subquery()
    )
    pending_alerts = (
        select(func.count())
        .select_from(Alert)
        .join(Patient, Alert.patient_id == Patient.id)
        .join(ECGEvent, Alert.event_id == ECGEvent.id)
        .where(
            Alert.deleted_at.is_(None),
            Alert.acknowledged_at.is_(None),
            Patient.deleted_at.is_(None),
            ECGEvent.deleted_at.is_(None),
            *patient_scope,
        )
        .scalar_subquery()
    )
    stale_devices = (
        select(func.count())
        .select_from(Device)
        .join(Patient, Device.patient_id == Patient.id)
        .where(
            Device.deleted_at.is_(None),
            Device.status == DeviceStatus.ASSIGNED,
            or_(Device.last_seen_at.is_(None), Device.last_seen_at < stale_before),
            Patient.deleted_at.is_(None),
            *patient_scope,
        )
        .scalar_subquery()
    )
    row = (
        await db.execute(select(active_patients, running_studies, pending_alerts, stale_devices))
    ).one()
    return tuple(int(value or 0) for value in row)  # type: ignore[return-value]


async def list_pending_alerts(
    db: AsyncSession, doctor_id: uuid.UUID | None, limit: int
) -> list[tuple[Alert, Patient, ECGEventType, uuid.UUID | None]]:
    study_id = (
        select(Study.id)
        .where(
            Study.patient_id == Alert.patient_id,
            Study.deleted_at.is_(None),
            Study.started_at <= Alert.created_at,
            or_(Study.ended_at.is_(None), Study.ended_at >= Alert.created_at),
        )
        .order_by(Study.started_at.desc())
        .limit(1)
        .scalar_subquery()
    )
    statement = (
        select(Alert, Patient, ECGEvent.event_type, study_id.label("study_id"))
        .join(Patient, Alert.patient_id == Patient.id)
        .join(ECGEvent, Alert.event_id == ECGEvent.id)
        .where(
            Alert.deleted_at.is_(None),
            Alert.acknowledged_at.is_(None),
            Patient.deleted_at.is_(None),
            ECGEvent.deleted_at.is_(None),
        )
        .order_by(_SEVERITY_RANK.asc(), Alert.created_at.desc())
    )
    if doctor_id is not None:
        statement = statement.where(Patient.doctor_id == doctor_id)
    result = await db.execute(statement.limit(limit))
    return [
        (alert, patient, event_type, matched_study_id)
        for alert, patient, event_type, matched_study_id in result.all()
    ]


async def list_stale_devices(
    db: AsyncSession, doctor_id: uuid.UUID | None, stale_before: datetime, limit: int
) -> list[tuple[Device, Patient]]:
    statement = (
        select(Device, Patient)
        .join(Patient, Device.patient_id == Patient.id)
        .where(
            Device.deleted_at.is_(None),
            Device.status == DeviceStatus.ASSIGNED,
            Device.patient_id.is_not(None),
            or_(Device.last_seen_at.is_(None), Device.last_seen_at < stale_before),
            Patient.deleted_at.is_(None),
        )
        # Mismo criterio que el detectedAt del service (last_seen_at o created_at),
        # para que el limit se lleve las alertas sintéticas más recientes.
        .order_by(func.coalesce(Device.last_seen_at, Device.created_at).desc())
    )
    if doctor_id is not None:
        statement = statement.where(Patient.doctor_id == doctor_id)
    result = await db.execute(statement.limit(limit))
    return [(device, patient) for device, patient in result.all()]


async def list_attention_patients(
    db: AsyncSession, doctor_id: uuid.UUID | None, limit: int
) -> list[tuple[Patient, str | None]]:
    device_serial = (
        select(Device.serial_number)
        .where(
            Device.patient_id == Patient.id,
            Device.deleted_at.is_(None),
            Device.status == DeviceStatus.ASSIGNED,
        )
        # Mismo criterio que patients_repository: con dos devices asignados al mismo
        # paciente hay que elegir uno de forma estable, no arbitraria.
        .order_by(Device.created_at.desc(), Device.id.asc())
        .limit(1)
        .scalar_subquery()
    )
    statement = (
        select(Patient, device_serial.label("device_serial"))
        .where(
            Patient.deleted_at.is_(None),
            Patient.study_status.in_([PatientStudyStatus.ACTIVE, PatientStudyStatus.PAUSED]),
        )
        .order_by(Patient.last_data_received_at.desc().nullslast())
    )
    if doctor_id is not None:
        statement = statement.where(Patient.doctor_id == doctor_id)
    result = await db.execute(statement.limit(limit))
    return [(patient, serial) for patient, serial in result.all()]


async def list_running_studies(
    db: AsyncSession, doctor_id: uuid.UUID | None, limit: int
) -> list[tuple[Study, Patient, Device]]:
    statement = (
        select(Study, Patient, Device)
        .join(Patient, Study.patient_id == Patient.id)
        .join(Device, Study.device_id == Device.id)
        .where(
            Study.deleted_at.is_(None),
            Study.status == StudyStatus.IN_PROGRESS,
            Patient.deleted_at.is_(None),
            Device.deleted_at.is_(None),
        )
        .order_by(Study.started_at.desc())
    )
    if doctor_id is not None:
        statement = statement.where(Patient.doctor_id == doctor_id)
    result = await db.execute(statement.limit(limit))
    return [(study, patient, device) for study, patient, device in result.all()]


async def list_watchdog_devices(
    db: AsyncSession,
    doctor_id: uuid.UUID | None,
    stale_before: datetime,
    low_battery_pct: int,
    limit: int,
) -> list[tuple[Device, str]]:
    offline = or_(Device.last_seen_at.is_(None), Device.last_seen_at < stale_before)
    low_battery = Device.last_battery_pct < low_battery_pct
    reason = case((offline, "offline"), (low_battery, "low_battery"), else_=None)
    priority = case((offline, 0), (low_battery, 1), else_=2)
    statement = (
        select(Device, reason.label("reason"))
        .join(Patient, Device.patient_id == Patient.id)
        .where(
            Device.deleted_at.is_(None),
            Device.status == DeviceStatus.ASSIGNED,
            Device.patient_id.is_not(None),
            Patient.deleted_at.is_(None),
            or_(offline, low_battery),
        )
        .order_by(priority.asc(), Device.last_seen_at.asc().nullsfirst(), Device.id.asc())
    )
    if doctor_id is not None:
        statement = statement.where(Patient.doctor_id == doctor_id)
    result = await db.execute(statement.limit(limit))
    return [(device, str(reason_value)) for device, reason_value in result.all()]
