"""Devices repository stubs."""

import uuid
from datetime import UTC, datetime

from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models.device import Device, DeviceStatus
from app.db.models.patient import Patient


async def list_devices(
    db: AsyncSession,
    q: str | None,
    statuses: list[DeviceStatus] | None,
    limit: int,
    offset: int,
) -> tuple[list[Device], int]:
    statement = select(Device).where(Device.deleted_at.is_(None))
    count_statement = select(func.count()).select_from(Device).where(Device.deleted_at.is_(None))

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

    result = await db.execute(
        statement.order_by(Device.created_at.desc()).limit(limit).offset(offset)
    )
    count_result = await db.execute(count_statement)
    return list(result.scalars().all()), count_result.scalar_one()


async def get_device_by_id(db: AsyncSession, device_id: uuid.UUID) -> Device | None:
    result = await db.execute(
        select(Device).where(Device.id == device_id, Device.deleted_at.is_(None))
    )
    return result.scalar_one_or_none()


async def get_device_by_serial(db: AsyncSession, serial: str) -> Device | None:
    result = await db.execute(
        select(Device).where(Device.serial_number == serial, Device.deleted_at.is_(None))
    )
    return result.scalar_one_or_none()


async def get_patient_for_doctor(
    db: AsyncSession, patient_id: uuid.UUID, doctor_id: uuid.UUID
) -> Patient | None:
    result = await db.execute(
        select(Patient).where(
            Patient.id == patient_id,
            Patient.doctor_id == doctor_id,
            Patient.deleted_at.is_(None),
        )
    )
    return result.scalar_one_or_none()


async def get_assigned_device_for_patient(db: AsyncSession, patient_id: uuid.UUID) -> Device | None:
    result = await db.execute(
        select(Device).where(
            Device.patient_id == patient_id,
            Device.status == DeviceStatus.ASSIGNED,
            Device.deleted_at.is_(None),
        )
    )
    return result.scalar_one_or_none()


async def create_device(
    db: AsyncSession,
    serial: str,
    model: str,
    firmware_version: str | None,
    api_key_hash: str,
) -> Device:
    device = Device(
        serial_number=serial,
        model=model,
        firmware_version=firmware_version,
        api_key_hash=api_key_hash,
        status=DeviceStatus.AVAILABLE,
    )
    db.add(device)
    await db.flush()
    return device


async def retire_device(db: AsyncSession, device: Device) -> None:
    device.status = DeviceStatus.RETIRED
    device.patient_id = None
    await db.flush()


async def assign_device(db: AsyncSession, device: Device, patient_id: uuid.UUID) -> None:
    device.patient_id = patient_id
    device.status = DeviceStatus.ASSIGNED
    await db.flush()


async def unassign_device(db: AsyncSession, device: Device) -> None:
    device.patient_id = None
    device.status = DeviceStatus.AVAILABLE
    await db.flush()


async def touch_deleted(db: AsyncSession, device: Device) -> None:
    device.deleted_at = datetime.now(UTC)
    await db.flush()
