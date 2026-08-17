"""Devices service."""

import hashlib
import secrets
from datetime import timedelta

from fastapi import HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models.device import Device, DeviceStatus
from app.modules.devices import devices_repository as repo
from app.modules.devices.devices_schemas import (
    AssignDoctorInput,
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


def signal_quality_for(device: Device) -> str | None:
    """Calidad de señal derivada. TODO: el device no reporta RSSI todavía."""
    return None if device.last_seen_at is None else "good"


def _holter_out(device: Device, doctor_name: str | None) -> HolterOut:
    return HolterOut(
        id=device.id,
        serial=device.serial_number,
        model=device.model,
        firmwareVersion=device.firmware_version,
        status=device.status,
        assignedPatientId=device.patient_id,
        assignedDoctorId=device.doctor_id,
        assignedDoctorName=doctor_name,
        lastSeenAt=device.last_seen_at,
        createdAt=device.created_at,
    )


def _not_found() -> HTTPException:
    return HTTPException(
        status_code=404,
        detail={"code": "DEVICE_NOT_FOUND", "message": "Holter no encontrado."},
    )


def _not_owned() -> HTTPException:
    return HTTPException(
        status_code=403,
        detail={"code": "DEVICE_NOT_OWNED", "message": "Este Holter no está asignado a tu cuenta."},
    )


def _in_use() -> HTTPException:
    return HTTPException(
        status_code=409,
        detail={
            "code": "DEVICE_ASSIGNED",
            "message": "Desasigná el Holter del paciente antes de cambiarle el médico.",
        },
    )


def holter_health_out(device: Device) -> HolterHealthOut:
    """Salud del Holter. Compartida con `GET /patients/{id}/device` (mismo tipo en el FE)."""
    if device.last_seen_at is None:
        raise HTTPException(
            status_code=404,
            detail={
                "code": "DEVICE_HEALTH_NOT_FOUND",
                "message": "Este Holter no se ha conectado todavía.",
            },
        )
    free_mb = device.last_sd_free_mb if device.last_sd_free_mb is not None else SD_TOTAL_MB
    return HolterHealthOut(
        deviceId=device.id,
        serial=device.serial_number,
        model=device.model,
        firmwareVersion=device.firmware_version or "unknown",
        batteryPercent=device.last_battery_pct if device.last_battery_pct is not None else 0,
        signalDbm=0,
        signalQuality=signal_quality_for(device) or "none",
        lastPingAt=device.last_seen_at,
        nextScheduledUploadAt=device.last_seen_at + timedelta(hours=1),
        uploadsToday=0,
        storageUsedMb=max(SD_TOTAL_MB - free_mb, 0),
        storageTotalMb=SD_TOTAL_MB,
    )


async def list_holters(input_data: HolterListInput, db: AsyncSession) -> HolterListResponse:
    rows, total = await repo.list_devices(
        db,
        doctor_id=input_data.doctor_id,
        q=input_data.q,
        statuses=input_data.status,
        limit=input_data.limit,
        offset=input_data.offset,
    )
    return HolterListResponse(
        items=[_holter_out(device, doctor_name) for device, doctor_name in rows],
        total=total,
        limit=input_data.limit,
        offset=input_data.offset,
    )


async def get_holter(input_data: HolterIdInput, db: AsyncSession) -> HolterOut:
    device = await repo.get_device_by_id(db, input_data.device_id)
    if device is None:
        raise _not_found()
    if input_data.doctor_id is not None and device.doctor_id != input_data.doctor_id:
        raise _not_owned()
    doctor_name = await repo.get_doctor_name(db, device.doctor_id)
    return _holter_out(device, doctor_name)


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
    out = _holter_out(device, None)
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
    doctor_name = await repo.get_doctor_name(db, device.doctor_id)
    return _holter_out(device, doctor_name)


async def delete_holter(input_data: HolterIdInput, db: AsyncSession) -> HolterOut:
    device = await repo.get_device_by_id(db, input_data.device_id)
    if device is None:
        raise _not_found()
    await repo.retire_device(db, device)
    doctor_name = await repo.get_doctor_name(db, device.doctor_id)
    return _holter_out(device, doctor_name)


async def assign_holter_doctor(input_data: AssignDoctorInput, db: AsyncSession) -> HolterOut:
    device = await repo.get_device_by_id(db, input_data.device_id)
    if device is None:
        raise _not_found()
    if device.patient_id is not None:
        # Rotar el médico de un Holter en uso desincroniza Device.doctor_id de
        # Patient.doctor_id: el dashboard (que scopea por paciente) lo seguiría
        # mostrando y /devices/{id} respondería 403 al médico que lo tiene puesto.
        raise _in_use()

    doctor_name = await repo.get_doctor_name(db, input_data.data.doctorId)
    if doctor_name is None:
        raise HTTPException(
            status_code=404,
            detail={"code": "DOCTOR_NOT_FOUND", "message": "Médico no encontrado."},
        )

    await repo.assign_doctor(db, device, input_data.data.doctorId)
    return _holter_out(device, doctor_name)


async def unassign_holter_doctor(input_data: HolterIdInput, db: AsyncSession) -> HolterOut:
    device = await repo.get_device_by_id(db, input_data.device_id)
    if device is None:
        raise _not_found()
    if device.patient_id is not None:
        raise _in_use()
    await repo.unassign_doctor(db, device)
    return _holter_out(device, None)


async def assign_holter(input_data: AssignHolterInput, db: AsyncSession) -> HolterOut:
    device = await repo.get_device_by_id(db, input_data.device_id)
    if device is None:
        raise _not_found()
    if input_data.doctor_id is not None and device.doctor_id != input_data.doctor_id:
        raise _not_owned()
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

    await repo.assign_device(db, device, patient)
    doctor_name = await repo.get_doctor_name(db, device.doctor_id)
    return _holter_out(device, doctor_name)


async def unassign_holter(input_data: HolterIdInput, db: AsyncSession) -> HolterOut:
    device = await repo.get_device_by_id(db, input_data.device_id)
    if device is None:
        raise _not_found()
    if input_data.doctor_id is not None and device.doctor_id != input_data.doctor_id:
        raise _not_owned()
    if device.status == DeviceStatus.RETIRED:
        raise HTTPException(
            status_code=409,
            detail={
                "code": "DEVICE_RETIRED",
                "message": "No se puede desasignar un Holter retirado.",
            },
        )
    await repo.unassign_device(db, device)
    doctor_name = await repo.get_doctor_name(db, device.doctor_id)
    return _holter_out(device, doctor_name)


async def reassign_holter(input_data: AssignHolterInput, db: AsyncSession) -> HolterOut:
    device = await repo.get_device_by_id(db, input_data.device_id)
    if device is None:
        raise _not_found()
    if input_data.doctor_id is not None and device.doctor_id != input_data.doctor_id:
        raise _not_owned()
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

    await repo.assign_device(db, device, patient)
    doctor_name = await repo.get_doctor_name(db, device.doctor_id)
    return _holter_out(device, doctor_name)


async def get_holter_health(input_data: HolterIdInput, db: AsyncSession) -> HolterHealthOut:
    device = await repo.get_device_by_id(db, input_data.device_id)
    if device is None:
        raise _not_found()
    if input_data.doctor_id is not None and device.doctor_id != input_data.doctor_id:
        raise _not_owned()
    return holter_health_out(device)
