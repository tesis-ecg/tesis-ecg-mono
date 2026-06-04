"""Devices service."""

import hashlib
import secrets
from datetime import timedelta

from fastapi import HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models.device import Device, DeviceStatus
from app.modules.devices import devices_repository as repo
from app.modules.devices.devices_schemas import (
    AssignHolterInput,
    HolterCreateInput,
    HolterCreateOut,
    HolterHealthOut,
    HolterIdInput,
    HolterListInput,
    HolterListResponse,
    HolterOut,
    HolterUpdateInput,
)

SD_TOTAL_MB = 128


def _holter_out(device: Device) -> HolterOut:
    return HolterOut(
        id=device.id,
        serial=device.serial_number,
        model=device.model,
        firmwareVersion=device.firmware_version,
        status=device.status,
        assignedPatientId=device.patient_id,
        lastSeenAt=device.last_seen_at,
        createdAt=device.created_at,
    )


def _not_found() -> HTTPException:
    return HTTPException(
        status_code=404,
        detail={"code": "DEVICE_NOT_FOUND", "message": "Holter no encontrado."},
    )


async def list_holters(input_data: HolterListInput, db: AsyncSession) -> HolterListResponse:
    devices, total = await repo.list_devices(
        db,
        q=input_data.q,
        statuses=input_data.status,
        limit=input_data.limit,
        offset=input_data.offset,
    )
    return HolterListResponse(
        items=[_holter_out(device) for device in devices],
        total=total,
        limit=input_data.limit,
        offset=input_data.offset,
    )


async def get_holter(input_data: HolterIdInput, db: AsyncSession) -> HolterOut:
    device = await repo.get_device_by_id(db, input_data.device_id)
    if device is None:
        raise _not_found()
    return _holter_out(device)


async def create_holter(input_data: HolterCreateInput, db: AsyncSession) -> HolterCreateOut:
    existing = await repo.get_device_by_serial(db, input_data.data.serial)
    if existing is not None:
        raise HTTPException(
            status_code=409,
            detail={"code": "SERIAL_CONFLICT", "message": "Ya existe un Holter con ese serial."},
        )

    api_key = secrets.token_urlsafe(32)
    api_key_hash = hashlib.sha256(api_key.encode()).hexdigest()
    device = await repo.create_device(
        db,
        serial=input_data.data.serial,
        model=input_data.data.model,
        firmware_version=input_data.data.firmwareVersion,
        api_key_hash=api_key_hash,
    )
    out = _holter_out(device)
    return HolterCreateOut(**out.model_dump(), apiKey=api_key)


async def update_holter(input_data: HolterUpdateInput, db: AsyncSession) -> HolterOut:
    device = await repo.get_device_by_id(db, input_data.device_id)
    if device is None:
        raise _not_found()

    update_data = input_data.data.model_dump(exclude_unset=True)
    if "status" in update_data and device.status == DeviceStatus.ASSIGNED:
        raise HTTPException(
            status_code=409,
            detail={
                "code": "DEVICE_ASSIGNED",
                "message": "Desasigná el Holter antes de cambiar el estado.",
            },
        )
    if update_data.get("status") == DeviceStatus.RETIRED:
        raise HTTPException(
            status_code=400,
            detail={
                "code": "INVALID_STATUS",
                "message": "El estado retirado se setea eliminando el Holter.",
            },
        )

    if "model" in update_data:
        device.model = update_data["model"]
    if "firmwareVersion" in update_data:
        device.firmware_version = update_data["firmwareVersion"]
    if "status" in update_data:
        device.status = update_data["status"]

    await db.flush()
    return _holter_out(device)


async def delete_holter(input_data: HolterIdInput, db: AsyncSession) -> HolterOut:
    device = await repo.get_device_by_id(db, input_data.device_id)
    if device is None:
        raise _not_found()
    await repo.retire_device(db, device)
    return _holter_out(device)


async def assign_holter(input_data: AssignHolterInput, db: AsyncSession) -> HolterOut:
    device = await repo.get_device_by_id(db, input_data.device_id)
    if device is None:
        raise _not_found()
    if device.status != DeviceStatus.AVAILABLE:
        raise HTTPException(
            status_code=409,
            detail={
                "code": "DEVICE_NOT_AVAILABLE",
                "message": "Este Holter no está disponible para asignación.",
            },
        )

    patient = await repo.get_patient_for_doctor(db, input_data.patient_id, input_data.doctor_id)
    if patient is None:
        raise HTTPException(
            status_code=404,
            detail={"code": "PATIENT_NOT_FOUND", "message": "Paciente no encontrado."},
        )
    existing = await repo.get_assigned_device_for_patient(db, patient.id)
    if existing is not None:
        raise HTTPException(
            status_code=409,
            detail={
                "code": "PATIENT_ALREADY_ASSIGNED",
                "message": "El paciente ya tiene un Holter asignado.",
            },
        )

    await repo.assign_device(db, device, patient.id)
    return _holter_out(device)


async def unassign_holter(input_data: HolterIdInput, db: AsyncSession) -> HolterOut:
    device = await repo.get_device_by_id(db, input_data.device_id)
    if device is None:
        raise _not_found()
    if device.status == DeviceStatus.RETIRED:
        raise HTTPException(
            status_code=409,
            detail={
                "code": "DEVICE_RETIRED",
                "message": "No se puede desasignar un Holter retirado.",
            },
        )
    await repo.unassign_device(db, device)
    return _holter_out(device)


async def reassign_holter(input_data: AssignHolterInput, db: AsyncSession) -> HolterOut:
    device = await repo.get_device_by_id(db, input_data.device_id)
    if device is None:
        raise _not_found()
    if device.status == DeviceStatus.RETIRED:
        raise HTTPException(
            status_code=409,
            detail={
                "code": "DEVICE_RETIRED",
                "message": "No se puede reasignar un Holter retirado.",
            },
        )

    patient = await repo.get_patient_for_doctor(db, input_data.patient_id, input_data.doctor_id)
    if patient is None:
        raise HTTPException(
            status_code=404,
            detail={"code": "PATIENT_NOT_FOUND", "message": "Paciente no encontrado."},
        )
    existing = await repo.get_assigned_device_for_patient(db, patient.id)
    if existing is not None and existing.id != device.id:
        raise HTTPException(
            status_code=409,
            detail={
                "code": "PATIENT_ALREADY_ASSIGNED",
                "message": "El paciente destino ya tiene un Holter asignado.",
            },
        )

    await repo.assign_device(db, device, patient.id)
    return _holter_out(device)


async def get_holter_health(input_data: HolterIdInput, db: AsyncSession) -> HolterHealthOut:
    device = await repo.get_device_by_id(db, input_data.device_id)
    if device is None:
        raise _not_found()
    if device.last_seen_at is None:
        raise HTTPException(
            status_code=404,
            detail={
                "code": "DEVICE_HEALTH_NOT_FOUND",
                "message": "Este Holter no se ha conectado todavía.",
            },
        )

    free_mb = device.last_sd_free_mb if device.last_sd_free_mb is not None else SD_TOTAL_MB
    storage_used_mb = max(SD_TOTAL_MB - free_mb, 0)
    battery = device.last_battery_pct if device.last_battery_pct is not None else 0
    signal_quality = "none" if device.last_seen_at is None else "good"
    return HolterHealthOut(
        deviceId=device.id,
        serial=device.serial_number,
        model=device.model,
        firmwareVersion=device.firmware_version or "unknown",
        batteryPercent=battery,
        signalDbm=0,
        signalQuality=signal_quality,
        lastPingAt=device.last_seen_at,
        nextScheduledUploadAt=device.last_seen_at + timedelta(hours=1),
        uploadsToday=0,
        storageUsedMb=storage_used_mb,
        storageTotalMb=SD_TOTAL_MB,
    )
