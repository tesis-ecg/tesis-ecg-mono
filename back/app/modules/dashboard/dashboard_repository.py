"""Dashboard repository."""

import uuid
from datetime import date, datetime
from typing import Any

from sqlalchemy import case, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models.alert import Alert, AlertSeverity
from app.db.models.device import Device, DeviceStatus
from app.db.models.ecg_event import ECGEvent, ECGEventType
from app.db.models.patient import Patient, PatientStudyStatus
from app.db.models.patient_report import PatientReport
from app.db.models.study import Study, StudyStatus
from app.modules._alert_study import alert_study_id
from app.modules.dashboard.dashboard_time import DASHBOARD_TIMEZONE_NAME

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
        .outerjoin(ECGEvent, Alert.event_id == ECGEvent.id)
        .where(
            Alert.deleted_at.is_(None),
            Alert.acknowledged_at.is_(None),
            Patient.deleted_at.is_(None),
            or_(ECGEvent.id.is_(None), ECGEvent.deleted_at.is_(None)),
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
        .outerjoin(ECGEvent, Alert.event_id == ECGEvent.id)
        .where(
            Alert.deleted_at.is_(None),
            Alert.acknowledged_at.is_(None),
            Patient.deleted_at.is_(None),
            or_(ECGEvent.id.is_(None), ECGEvent.deleted_at.is_(None)),
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
) -> list[tuple[Alert, Patient, ECGEventType | None, dict[str, Any] | None, uuid.UUID | None]]:
    statement = (
        select(
            Alert,
            Patient,
            ECGEvent.event_type,
            ECGEvent.event_metadata,
            alert_study_id().label("study_id"),
        )
        .join(Patient, Alert.patient_id == Patient.id)
        .outerjoin(ECGEvent, Alert.event_id == ECGEvent.id)
        .where(
            Alert.deleted_at.is_(None),
            Alert.acknowledged_at.is_(None),
            Patient.deleted_at.is_(None),
            or_(ECGEvent.id.is_(None), ECGEvent.deleted_at.is_(None)),
        )
        .order_by(_SEVERITY_RANK.asc(), Alert.created_at.desc())
    )
    if doctor_id is not None:
        statement = statement.where(Patient.doctor_id == doctor_id)
    result = await db.execute(statement.limit(limit))
    return [
        (alert, patient, event_type, event_metadata, matched_study_id)
        for alert, patient, event_type, event_metadata, matched_study_id in result.all()
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


# --- Actividad (series y totales para los gráficos de la home) ---------------
#
# Nada de esto necesita histórico ni tablas nuevas: `alert`, `patient_report`,
# `study` y `patient` ya guardan cuándo apareció cada fila. Lo que faltaba era
# preguntárselo.


def _pending_alert_filters(patient_scope: list[Any]) -> list[Any]:
    """Los mismos filtros que `count_pending_alerts`.

    Extraído para que el donut por severidad no pueda desviarse del KPI
    agregado que se muestra en la misma pantalla.
    """
    return [
        Alert.deleted_at.is_(None),
        Alert.acknowledged_at.is_(None),
        Patient.deleted_at.is_(None),
        or_(ECGEvent.id.is_(None), ECGEvent.deleted_at.is_(None)),
        *patient_scope,
    ]


async def count_pending_alerts_by_severity(
    db: AsyncSession, doctor_id: uuid.UUID | None
) -> dict[str, int]:
    patient_scope = [] if doctor_id is None else [Patient.doctor_id == doctor_id]
    statement = (
        select(Alert.severity, func.count())
        .select_from(Alert)
        .join(Patient, Alert.patient_id == Patient.id)
        .outerjoin(ECGEvent, Alert.event_id == ECGEvent.id)
        .where(*_pending_alert_filters(patient_scope))
        .group_by(Alert.severity)
    )
    result = await db.execute(statement)
    return {severity.value.lower(): int(count or 0) for severity, count in result.all()}


async def count_fleet(
    db: AsyncSession, doctor_id: uuid.UUID | None, stale_before: datetime
) -> tuple[int, int]:
    """(asignados, transmitiendo). Mismo corte de frescura que el watchdog."""
    patient_scope = [] if doctor_id is None else [Patient.doctor_id == doctor_id]
    base = [
        Device.deleted_at.is_(None),
        Device.status == DeviceStatus.ASSIGNED,
        Device.patient_id.is_not(None),
        Patient.deleted_at.is_(None),
        *patient_scope,
    ]
    assigned = (
        select(func.count())
        .select_from(Device)
        .join(Patient, Device.patient_id == Patient.id)
        .where(*base)
        .scalar_subquery()
    )
    transmitting = (
        select(func.count())
        .select_from(Device)
        .join(Patient, Device.patient_id == Patient.id)
        .where(*base, Device.last_seen_at.is_not(None), Device.last_seen_at >= stale_before)
        .scalar_subquery()
    )
    row = (await db.execute(select(assigned, transmitting))).one()
    return int(row[0] or 0), int(row[1] or 0)


async def count_activity_by_day(
    db: AsyncSession, doctor_id: uuid.UUID | None, since: datetime
) -> tuple[dict[date, int], dict[date, int], dict[date, int]]:
    """Alertas, registros del paciente y estudios iniciados, por día desde `since`.

    Devuelve tres mapas dispersos: los días sin filas simplemente no están. El
    relleno con ceros lo hace el service, que es el que sabe cuántos días tiene
    que dibujar el gráfico.
    """
    patient_scope = [] if doctor_id is None else [Patient.doctor_id == doctor_id]

    alerts = (
        select(
            func.date(func.timezone(DASHBOARD_TIMEZONE_NAME, Alert.created_at)).label("day"),
            func.count(),
        )
        .select_from(Alert)
        .join(Patient, Alert.patient_id == Patient.id)
        .where(
            Alert.deleted_at.is_(None),
            Alert.created_at >= since,
            Patient.deleted_at.is_(None),
            *patient_scope,
        )
        .group_by("day")
    )
    reports = (
        select(
            func.date(func.timezone(DASHBOARD_TIMEZONE_NAME, PatientReport.occurred_at)).label(
                "day"
            ),
            func.count(),
        )
        .select_from(PatientReport)
        .join(Patient, PatientReport.patient_id == Patient.id)
        .where(
            PatientReport.deleted_at.is_(None),
            PatientReport.occurred_at >= since,
            Patient.deleted_at.is_(None),
            *patient_scope,
        )
        .group_by("day")
    )
    studies = (
        select(
            func.date(func.timezone(DASHBOARD_TIMEZONE_NAME, Study.started_at)).label("day"),
            func.count(),
        )
        .select_from(Study)
        .join(Patient, Study.patient_id == Patient.id)
        .where(
            Study.deleted_at.is_(None),
            Study.started_at >= since,
            Patient.deleted_at.is_(None),
            *patient_scope,
        )
        .group_by("day")
    )

    return (
        {day: int(count or 0) for day, count in (await db.execute(alerts)).all()},
        {day: int(count or 0) for day, count in (await db.execute(reports)).all()},
        {day: int(count or 0) for day, count in (await db.execute(studies)).all()},
    )


async def count_windows(
    db: AsyncSession, doctor_id: uuid.UUID | None, current_since: datetime, previous_since: datetime
) -> tuple[tuple[int, int], tuple[int, int], tuple[int, int]]:
    """Totales de dos ventanas consecutivas para alertas, estudios y pacientes.

    Una sola ida a la base con seis subconsultas: son contadores chicos y la home
    ya resuelve todo en un request.
    """
    patient_scope = [] if doctor_id is None else [Patient.doctor_id == doctor_id]

    def alerts_between(start: datetime, end: datetime | None) -> Any:
        statement = (
            select(func.count())
            .select_from(Alert)
            .join(Patient, Alert.patient_id == Patient.id)
            .where(
                Alert.deleted_at.is_(None),
                Alert.created_at >= start,
                Patient.deleted_at.is_(None),
                *patient_scope,
            )
        )
        if end is not None:
            statement = statement.where(Alert.created_at < end)
        return statement.scalar_subquery()

    def studies_between(start: datetime, end: datetime | None) -> Any:
        statement = (
            select(func.count())
            .select_from(Study)
            .join(Patient, Study.patient_id == Patient.id)
            .where(
                Study.deleted_at.is_(None),
                Study.started_at >= start,
                Patient.deleted_at.is_(None),
                *patient_scope,
            )
        )
        if end is not None:
            statement = statement.where(Study.started_at < end)
        return statement.scalar_subquery()

    def patients_between(start: datetime, end: datetime | None) -> Any:
        statement = (
            select(func.count())
            .select_from(Patient)
            .where(
                Patient.deleted_at.is_(None),
                Patient.created_at >= start,
                *patient_scope,
            )
        )
        if end is not None:
            statement = statement.where(Patient.created_at < end)
        return statement.scalar_subquery()

    row = (
        await db.execute(
            select(
                alerts_between(current_since, None),
                alerts_between(previous_since, current_since),
                studies_between(current_since, None),
                studies_between(previous_since, current_since),
                patients_between(current_since, None),
                patients_between(previous_since, current_since),
            )
        )
    ).one()
    values = [int(value or 0) for value in row]
    return (values[0], values[1]), (values[2], values[3]), (values[4], values[5])
