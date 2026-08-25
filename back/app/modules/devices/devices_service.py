"""Devices service."""

import hashlib
import secrets
import uuid
from datetime import UTC, datetime

from fastapi import HTTPException
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models.audit_event import AuditEventType
from app.db.models.device import Device, DeviceStatus
from app.modules.auth import auth_repository as auth_repo
from app.modules.devices import devices_repository as repo
from app.modules.devices.devices_schemas import (
    AssignDoctorInput,
    AssignHolterInput,
    HolterApiKeyOut,
    HolterCreateInput,
    HolterCreateOut,
    HolterHealthOut,
    HolterIdInput,
    HolterListInput,
    HolterListResponse,
    HolterOut,
    HolterUpdateInput,
)
from app.modules.studies import studies_service as studies


def _holter_out(
    device: Device,
    doctor_name: str | None,
    patient_name: str | None = None,
    active_study_id: uuid.UUID | None = None,
) -> HolterOut:
    return HolterOut(
        id=device.id,
        serial=device.serial_number,
        model=device.model,
        firmwareVersion=device.firmware_version,
        status=device.status,
        assignedPatientId=device.patient_id,
        assignedPatientName=patient_name,
        activeStudyId=active_study_id,
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
    return _not_found()


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
    return HolterHealthOut(
        deviceId=device.id,
        serial=device.serial_number,
        model=device.model,
        firmwareVersion=device.firmware_version,
        telemetryAvailable=any(
            value is not None for value in (device.last_battery_pct, device.last_sd_free_mb)
        ),
        batteryPercent=device.last_battery_pct,
        signalDbm=None,
        signalQuality=None,
        lastPingAt=device.last_seen_at,
        nextScheduledUploadAt=None,
        uploadsToday=None,
        storageUsedMb=None,
        storageTotalMb=None,
    )


async def _holter_out_resolved(
    db: AsyncSession, device: Device, doctor_name: str | None
) -> HolterOut:
    """`_holter_out` resolviendo paciente y estudio en curso desde la base.

    El listado los trae en subconsultas; los caminos de un solo equipo (detalle
    y mutaciones) no pueden, así que se consultan acá. Las dos funciones
    cortocircuitan si el equipo no tiene paciente, que es el caso más común
    después de una desasignación.
    """
    patient_name = await repo.get_patient_name(db, device.patient_id)
    active_study_id = await repo.get_active_study_id(db, device.patient_id, device.id)
    return _holter_out(device, doctor_name, patient_name, active_study_id)


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
        items=[
            _holter_out(device, doctor_name, patient_name, active_study_id)
            for device, doctor_name, patient_name, active_study_id in rows
        ],
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
    return await _holter_out_resolved(db, device, doctor_name)


async def create_holter(input_data: HolterCreateInput, db: AsyncSession) -> HolterCreateOut:
    existing = await repo.get_device_by_serial(db, input_data.data.serial)
    if existing is not None:
        raise HTTPException(
            status_code=409,
            detail={"code": "SERIAL_CONFLICT", "message": "Ya existe un Holter con ese serial."},
        )

    api_key = secrets.token_urlsafe(32)
    api_key_hash = hashlib.sha256(api_key.encode()).hexdigest()
    try:
        device = await repo.create_device(
            db,
            serial=input_data.data.serial.strip(),
            model=input_data.data.model.strip(),
            firmware_version=input_data.data.firmwareVersion,
            api_key_hash=api_key_hash,
        )
        await _audit_device(db, AuditEventType.DEVICE_CREATED, input_data.actor_id, device.id)
        await db.commit()
    except IntegrityError as exc:
        await db.rollback()
        raise HTTPException(
            status_code=409,
            detail={"code": "SERIAL_CONFLICT", "message": "Ya existe un Holter con ese serial."},
        ) from exc
    out = _holter_out(device, None)
    return HolterCreateOut(**out.model_dump(), apiKey=api_key)


async def rotate_api_key(input_data: HolterIdInput, db: AsyncSession) -> HolterApiKeyOut:
    """Genera una API key nueva para un equipo existente y devuelve la anterior inútil.

    Hace falta porque `create_holter` entrega la key en claro una sola vez y no
    hay forma de recuperarla después (en la base solo vive el sha256). Sin esto,
    un equipo ya dado de alta no se puede volver a aprovisionar — ni conectar al
    simulador — sin borrarlo y crearlo de nuevo.

    La rotación es inmediata: la key vieja deja de servir en el mismo commit.
    """
    device = await repo.get_device_by_id(db, input_data.device_id)
    if device is None:
        raise _not_found()

    api_key = secrets.token_urlsafe(32)
    device.api_key_hash = hashlib.sha256(api_key.encode()).hexdigest()
    await _audit_device(
        db,
        AuditEventType.DEVICE_API_KEY_ROTATED,
        input_data.actor_id,
        device.id,
        {"serial": device.serial_number},
    )
    await db.commit()
    return HolterApiKeyOut(
        deviceId=device.id,
        serial=device.serial_number,
        apiKey=api_key,
        rotatedAt=datetime.now(UTC),
    )


async def update_holter(input_data: HolterUpdateInput, db: AsyncSession) -> HolterOut:
    device = await repo.get_device_by_id_for_update(db, input_data.device_id)
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

    await _audit_device(db, AuditEventType.DEVICE_UPDATED, input_data.actor_id, device.id)
    await db.commit()
    doctor_name = await repo.get_doctor_name(db, device.doctor_id)
    return await _holter_out_resolved(db, device, doctor_name)


async def delete_holter(input_data: HolterIdInput, db: AsyncSession) -> HolterOut:
    device = await repo.get_device_by_id_for_update(db, input_data.device_id)
    if device is None:
        raise _not_found()
    # Antes de `retire_device`, que borra el `patient_id`: después ya no habría
    # forma de saber de qué paciente era el estudio abierto.
    await studies.close_open_studies_for_device(db, device, input_data.actor_id, "device_retired")
    await repo.retire_device(db, device)
    await _audit_device(db, AuditEventType.DEVICE_RETIRED, input_data.actor_id, device.id)
    await db.commit()
    doctor_name = await repo.get_doctor_name(db, device.doctor_id)
    return await _holter_out_resolved(db, device, doctor_name)


async def assign_holter_doctor(input_data: AssignDoctorInput, db: AsyncSession) -> HolterOut:
    device = await repo.get_device_by_id_for_update(db, input_data.device_id)
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
    await _audit_device(
        db,
        AuditEventType.DEVICE_ASSIGNED,
        input_data.actor_id,
        device.id,
        {"doctor_id": str(input_data.data.doctorId)},
    )
    await db.commit()
    return await _holter_out_resolved(db, device, doctor_name)


async def unassign_holter_doctor(input_data: HolterIdInput, db: AsyncSession) -> HolterOut:
    device = await repo.get_device_by_id_for_update(db, input_data.device_id)
    if device is None:
        raise _not_found()
    if device.patient_id is not None:
        raise _in_use()
    await repo.unassign_doctor(db, device)
    await _audit_device(
        db,
        AuditEventType.DEVICE_ASSIGNED,
        input_data.actor_id,
        device.id,
        {"doctor_id": None},
    )
    await db.commit()
    return await _holter_out_resolved(db, device, None)


async def assign_holter(input_data: AssignHolterInput, db: AsyncSession) -> HolterOut:
    device = await repo.get_device_by_id_for_update(db, input_data.device_id)
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

    patient = await repo.get_patient_for_doctor_for_update(
        db, input_data.patient_id, input_data.doctor_id
    )
    if patient is None:
        raise HTTPException(
            status_code=404,
            detail={"code": "PATIENT_NOT_FOUND", "message": "Paciente no encontrado."},
        )
    existing = await repo.get_assigned_device_for_patient_for_update(db, patient.id)
    if existing is not None:
        raise HTTPException(
            status_code=409,
            detail={
                "code": "PATIENT_ALREADY_ASSIGNED",
                "message": "El paciente ya tiene un Holter asignado.",
            },
        )

    try:
        await repo.assign_device(db, device, patient)
        await _audit_device(
            db,
            AuditEventType.DEVICE_ASSIGNED,
            input_data.actor_id,
            device.id,
            {"patient_id": str(patient.id)},
        )
        await db.commit()
    except IntegrityError as exc:
        await db.rollback()
        raise HTTPException(
            status_code=409,
            detail={
                "code": "PATIENT_ALREADY_ASSIGNED",
                "message": "El paciente ya tiene un Holter asignado.",
            },
        ) from exc
    doctor_name = await repo.get_doctor_name(db, device.doctor_id)
    return await _holter_out_resolved(db, device, doctor_name)


async def unassign_holter(input_data: HolterIdInput, db: AsyncSession) -> HolterOut:
    device = await repo.get_device_by_id_for_update(db, input_data.device_id)
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
    # Sacarle el chaleco al paciente es la forma normal en que termina un
    # Holter: el estudio abierto se cierra acá, antes de soltar el `patient_id`.
    await studies.close_open_studies_for_device(
        db, device, input_data.actor_id, "device_unassigned"
    )
    await repo.unassign_device(db, device)
    await _audit_device(
        db,
        AuditEventType.DEVICE_ASSIGNED,
        input_data.actor_id,
        device.id,
        {"patient_id": None},
    )
    await db.commit()
    doctor_name = await repo.get_doctor_name(db, device.doctor_id)
    return await _holter_out_resolved(db, device, doctor_name)


async def reassign_holter(input_data: AssignHolterInput, db: AsyncSession) -> HolterOut:
    device = await repo.get_device_by_id_for_update(db, input_data.device_id)
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

    patient = await repo.get_patient_for_doctor_for_update(
        db, input_data.patient_id, input_data.doctor_id
    )
    if patient is None:
        raise HTTPException(
            status_code=404,
            detail={"code": "PATIENT_NOT_FOUND", "message": "Paciente no encontrado."},
        )
    existing = await repo.get_assigned_device_for_patient_for_update(db, patient.id)
    if existing is not None and existing.id != device.id:
        raise HTTPException(
            status_code=409,
            detail={
                "code": "PATIENT_ALREADY_ASSIGNED",
                "message": "El paciente destino ya tiene un Holter asignado.",
            },
        )

    # Reasignar a OTRO paciente cierra el estudio del anterior. Si el destino es
    # el mismo paciente no se cierra nada: es un no-op, no una interrupción.
    if device.patient_id is not None and device.patient_id != patient.id:
        await studies.close_open_studies_for_device(
            db, device, input_data.actor_id, "device_reassigned"
        )

    try:
        await repo.assign_device(db, device, patient)
        await _audit_device(
            db,
            AuditEventType.DEVICE_ASSIGNED,
            input_data.actor_id,
            device.id,
            {"patient_id": str(patient.id)},
        )
        await db.commit()
    except IntegrityError as exc:
        await db.rollback()
        raise HTTPException(
            status_code=409,
            detail={
                "code": "PATIENT_ALREADY_ASSIGNED",
                "message": "El paciente destino ya tiene un Holter asignado.",
            },
        ) from exc
    doctor_name = await repo.get_doctor_name(db, device.doctor_id)
    return await _holter_out_resolved(db, device, doctor_name)


async def get_holter_health(input_data: HolterIdInput, db: AsyncSession) -> HolterHealthOut:
    device = await repo.get_device_by_id(db, input_data.device_id)
    if device is None:
        raise _not_found()
    if input_data.doctor_id is not None and device.doctor_id != input_data.doctor_id:
        raise _not_owned()
    return holter_health_out(device)


async def _audit_device(
    db: AsyncSession,
    event_type: AuditEventType,
    actor_id: uuid.UUID | None,
    device_id: uuid.UUID,
    metadata: dict[str, str | None] | None = None,
) -> None:
    if actor_id is None:
        return
    event_metadata: dict[str, object] = {"target_device_id": str(device_id)}
    if metadata:
        event_metadata.update(metadata)
    await auth_repo.log_audit_event(db, event_type, user_id=actor_id, metadata=event_metadata)
