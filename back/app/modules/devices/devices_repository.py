"""Devices repository stubs."""

import uuid
from datetime import UTC, datetime

from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.sql.expression import ScalarSelect

from app.db.models.device import Device, DeviceStatus
from app.db.models.doctor import Doctor
from app.db.models.patient import Patient
from app.db.models.study import Study, StudyStatus
from app.db.models.user import User


def _patient_name_subquery() -> ScalarSelect[str]:
    """Nombre del paciente asignado. Reemplaza al UUID crudo en el listado."""
    return (
        select(func.concat(Patient.first_name, " ", Patient.last_name))
        .where(Patient.id == Device.patient_id, Patient.deleted_at.is_(None))
        .limit(1)
        .scalar_subquery()
    )


def _active_study_subquery() -> ScalarSelect[uuid.UUID]:
    """Estudio en curso del par paciente+equipo.

    Se filtra por los dos porque un paciente puede haber tenido otro Holter
    antes: el estudio que le corresponde a ESTE equipo es el que se graba con
    él. Mismo criterio que usa la ingesta para resolver a qué estudio va un lote.
    """
    return (
        select(Study.id)
        .where(
            Study.patient_id == Device.patient_id,
            Study.device_id == Device.id,
            Study.status == StudyStatus.IN_PROGRESS,
            Study.deleted_at.is_(None),
        )
        .order_by(Study.started_at.desc())
        .limit(1)
        .scalar_subquery()
    )


async def list_devices(
    db: AsyncSession,
    doctor_id: uuid.UUID | None,
    q: str | None,
    statuses: list[DeviceStatus] | None,
    limit: int,
    offset: int,
) -> tuple[list[tuple[Device, str | None, str | None, uuid.UUID | None]], int]:
    doctor_name = (
        select(User.full_name)
        .join(Doctor, Doctor.user_id == User.id)
        .where(
            Doctor.id == Device.doctor_id,
            Doctor.deleted_at.is_(None),
            User.deleted_at.is_(None),
            User.is_active.is_(True),
        )
        .limit(1)
        .scalar_subquery()
    )
    statement = select(
        Device,
        doctor_name.label("doctor_name"),
        _patient_name_subquery().label("patient_name"),
        _active_study_subquery().label("active_study_id"),
    ).where(Device.deleted_at.is_(None))
    count_statement = select(func.count()).select_from(Device).where(Device.deleted_at.is_(None))

    if doctor_id is not None:
        statement = statement.where(Device.doctor_id == doctor_id)
        count_statement = count_statement.where(Device.doctor_id == doctor_id)
    if q:
        pattern = f"%{q.strip()}%"
        filters = or_(
            Device.serial_number.ilike(pattern),
            Device.model.ilike(pattern),
            Device.firmware_version.ilike(pattern),
        )
        statement = statement.where(filters)
        count_statement = count_statement.where(filters)
    if statuses:
        statement = statement.where(Device.status.in_(statuses))
        count_statement = count_statement.where(Device.status.in_(statuses))

    # `Device.id` desempata: created_at usa now(), que es igual para todas las filas
    # insertadas en la misma transacción, y sin orden total la paginación repite filas.
    result = await db.execute(
        statement.order_by(Device.created_at.desc(), Device.id.asc()).limit(limit).offset(offset)
    )
    total = await db.scalar(count_statement)
    rows = [
        (device, doctor_name_value, patient_name, active_study_id)
        for device, doctor_name_value, patient_name, active_study_id in result.all()
    ]
    return rows, total or 0


async def get_device_by_id(db: AsyncSession, device_id: uuid.UUID) -> Device | None:
    result = await db.execute(
        select(Device).where(Device.id == device_id, Device.deleted_at.is_(None))
    )
    return result.scalar_one_or_none()


async def get_device_by_id_for_update(db: AsyncSession, device_id: uuid.UUID) -> Device | None:
    result = await db.execute(
        select(Device).where(Device.id == device_id, Device.deleted_at.is_(None)).with_for_update()
    )
    return result.scalar_one_or_none()


async def get_device_by_serial(db: AsyncSession, serial: str) -> Device | None:
    result = await db.execute(
        select(Device).where(Device.serial_number == serial, Device.deleted_at.is_(None))
    )
    return result.scalar_one_or_none()


async def get_patient_name(db: AsyncSession, patient_id: uuid.UUID | None) -> str | None:
    if patient_id is None:
        return None
    name: str | None = await db.scalar(
        select(func.concat(Patient.first_name, " ", Patient.last_name)).where(
            Patient.id == patient_id, Patient.deleted_at.is_(None)
        )
    )
    return name


async def get_active_study_id(
    db: AsyncSession, patient_id: uuid.UUID | None, device_id: uuid.UUID
) -> uuid.UUID | None:
    if patient_id is None:
        return None
    study_id: uuid.UUID | None = await db.scalar(
        select(Study.id)
        .where(
            Study.patient_id == patient_id,
            Study.device_id == device_id,
            Study.status == StudyStatus.IN_PROGRESS,
            Study.deleted_at.is_(None),
        )
        .order_by(Study.started_at.desc())
        .limit(1)
    )
    return study_id


async def get_doctor_name(db: AsyncSession, doctor_id: uuid.UUID | None) -> str | None:
    # Mismos filtros que doctors_repository.list_options: un médico que no aparece en
    # el Select de `GET /doctors` tampoco es asignable ni se muestra en la grilla.
    if doctor_id is None:
        return None
    result = await db.execute(
        select(User.full_name)
        .join(Doctor, Doctor.user_id == User.id)
        .where(
            Doctor.id == doctor_id,
            Doctor.deleted_at.is_(None),
            User.deleted_at.is_(None),
            User.is_active.is_(True),
        )
    )
    return result.scalar_one_or_none()


async def get_patient_for_doctor(
    db: AsyncSession, patient_id: uuid.UUID, doctor_id: uuid.UUID | None
) -> Patient | None:
    statement = select(Patient).where(
        Patient.id == patient_id,
        Patient.deleted_at.is_(None),
    )
    if doctor_id is not None:
        statement = statement.where(Patient.doctor_id == doctor_id)
    result = await db.execute(statement)
    return result.scalar_one_or_none()


async def get_patient_for_doctor_for_update(
    db: AsyncSession, patient_id: uuid.UUID, doctor_id: uuid.UUID | None
) -> Patient | None:
    statement = (
        select(Patient)
        .where(Patient.id == patient_id, Patient.deleted_at.is_(None))
        .with_for_update()
    )
    if doctor_id is not None:
        statement = statement.where(Patient.doctor_id == doctor_id)
    result = await db.execute(statement)
    return result.scalar_one_or_none()


async def get_assigned_device_for_patient(db: AsyncSession, patient_id: uuid.UUID) -> Device | None:
    # first() y no scalar_one_or_none(): nada impide dos devices ASSIGNED sobre el
    # mismo paciente (los chequeos son check-then-write sin lock) y esa carrera
    # convertiría cada lectura en un 500 por MultipleResultsFound.
    result = await db.execute(
        select(Device)
        .where(
            Device.patient_id == patient_id,
            Device.status == DeviceStatus.ASSIGNED,
            Device.deleted_at.is_(None),
        )
        .order_by(Device.created_at.desc(), Device.id.asc())
        .limit(1)
    )
    return result.scalars().first()


async def get_assigned_device_for_patient_for_update(
    db: AsyncSession, patient_id: uuid.UUID
) -> Device | None:
    result = await db.execute(
        select(Device)
        .where(
            Device.patient_id == patient_id,
            Device.status == DeviceStatus.ASSIGNED,
            Device.deleted_at.is_(None),
        )
        .order_by(Device.id)
        .with_for_update()
        .limit(1)
    )
    return result.scalars().first()


async def create_device(
    db: AsyncSession,
    serial: str,
    model: str,
    firmware_version: str | None,
    api_key_hash: str,
    api_key_encrypted: str,
    api_key_rotated_at: datetime,
) -> Device:
    device = Device(
        serial_number=serial,
        model=model,
        firmware_version=firmware_version,
        api_key_hash=api_key_hash,
        api_key_encrypted=api_key_encrypted,
        api_key_rotated_at=api_key_rotated_at,
        status=DeviceStatus.AVAILABLE,
    )
    db.add(device)
    await db.flush()
    return device


async def retire_device(db: AsyncSession, device: Device) -> None:
    device.status = DeviceStatus.RETIRED
    device.patient_id = None
    await db.flush()


async def assign_device(db: AsyncSession, device: Device, patient: Patient) -> None:
    device.patient_id = patient.id
    # Invariante: un device con paciente pertenece al médico de ese paciente. Para el
    # médico es un no-op (ya coincidían); para el admin evita dejar el device en manos
    # de otro médico, que es el estado que rompe /devices vs /dashboard.
    device.doctor_id = patient.doctor_id
    device.status = DeviceStatus.ASSIGNED
    await db.flush()


async def unassign_device(db: AsyncSession, device: Device) -> None:
    device.patient_id = None
    device.status = DeviceStatus.AVAILABLE
    await db.flush()


async def assign_doctor(db: AsyncSession, device: Device, doctor_id: uuid.UUID) -> None:
    device.doctor_id = doctor_id
    await db.flush()


async def unassign_doctor(db: AsyncSession, device: Device) -> None:
    device.doctor_id = None
    await db.flush()


async def touch_deleted(db: AsyncSession, device: Device) -> None:
    device.deleted_at = datetime.now(UTC)
    await db.flush()
