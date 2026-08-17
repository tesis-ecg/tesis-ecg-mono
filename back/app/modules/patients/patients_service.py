"""Patients service."""

from fastapi import HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.devices.devices_schemas import HolterHealthOut
from app.modules.devices.devices_service import holter_health_out
from app.modules.doctors import doctors_repository as doctors_repo
from app.modules.patients import patients_repository as repo
from app.modules.patients.patients_schemas import (
    PatientCreateInput,
    PatientIdInput,
    PatientListInput,
    PatientListResponse,
    PatientOut,
    PatientRow,
    PatientSummaryInput,
    PatientSummaryOut,
    PatientUpdateInput,
)


def _split_full_name(full_name: str) -> tuple[str, str]:
    parts = full_name.strip().split()
    if not parts:
        raise HTTPException(
            status_code=400,
            detail={"code": "INVALID_NAME", "message": "Nombre inválido."},
        )
    if len(parts) == 1:
        return parts[0], ""
    return parts[0], " ".join(parts[1:])


def _patient_out(row: PatientRow) -> PatientOut:
    if row.date_of_birth is None:
        raise HTTPException(
            status_code=500,
            detail={"code": "INVALID_PATIENT", "message": "Paciente sin fecha de nacimiento."},
        )
    return PatientOut(
        id=row.id,
        fullName=f"{row.first_name} {row.last_name}".strip(),
        dni=row.dni or "",
        birthDate=row.date_of_birth,
        sex=row.sex,
        assignedDeviceId=row.assigned_device_id,
        studyStatus=row.study_status,
        lastDataReceivedAt=row.last_data_received_at,
        contactEmail=row.email,
        contactPhone=row.phone,
        doctorId=row.doctor_id,
        doctorName=row.doctor_name,
    )


async def list_patients(input_data: PatientListInput, db: AsyncSession) -> PatientListResponse:
    rows, total = await repo.list_patients(
        db,
        doctor_id=input_data.doctor_id,
        q=input_data.q,
        statuses=input_data.status,
        limit=input_data.limit,
        offset=input_data.offset,
        sort=input_data.sort,
        order=input_data.order,
    )
    return PatientListResponse(
        items=[_patient_out(row) for row in rows],
        total=total,
        limit=input_data.limit,
        offset=input_data.offset,
    )


async def get_patient(input_data: PatientIdInput, db: AsyncSession) -> PatientOut:
    row = await repo.get_patient_row(db, input_data.patient_id, input_data.doctor_id)
    if row is None:
        raise HTTPException(
            status_code=404,
            detail={"code": "PATIENT_NOT_FOUND", "message": "Paciente no encontrado."},
        )
    return _patient_out(row)


async def create_patient(input_data: PatientCreateInput, db: AsyncSession) -> PatientOut:
    # Patient.doctor_id es NOT NULL y get_role_scope devuelve doctor_id=None al admin
    # para darle la vista global. Antes de rechazar se busca su propio perfil médico:
    # un admin promovido desde una cuenta médico conserva la fila Doctor.
    doctor_id = input_data.doctor_id
    if doctor_id is None:
        doctor = await doctors_repo.get_by_user_id(db, input_data.requesting_user.id)
        if doctor is None:
            raise HTTPException(
                status_code=422,
                detail={
                    "code": "DOCTOR_REQUIRED",
                    "message": "Un admin no puede crear pacientes sin un médico asignado.",
                },
            )
        doctor_id = doctor.id

    existing = await repo.get_patient_by_dni(db, input_data.data.dni)
    if existing is not None:
        raise HTTPException(
            status_code=409,
            detail={"code": "DNI_CONFLICT", "message": "Ya existe un paciente con ese DNI."},
        )

    first_name, last_name = _split_full_name(input_data.data.fullName)
    patient = await repo.create_patient(
        db,
        doctor_id=doctor_id,
        first_name=first_name,
        last_name=last_name,
        dni=input_data.data.dni,
        date_of_birth=input_data.data.birthDate,
        sex=input_data.data.sex,
        email=input_data.data.contactEmail,
        phone=input_data.data.contactPhone,
    )
    row = await repo.get_patient_row(db, patient.id, input_data.doctor_id)
    if row is None:
        raise HTTPException(status_code=500, detail="Created patient not found")
    return _patient_out(row)


async def update_patient(input_data: PatientUpdateInput, db: AsyncSession) -> PatientOut:
    patient = await repo.get_patient_model(db, input_data.patient_id, input_data.doctor_id)
    if patient is None:
        raise HTTPException(
            status_code=404,
            detail={"code": "PATIENT_NOT_FOUND", "message": "Paciente no encontrado."},
        )

    update_data = input_data.data.model_dump(exclude_unset=True)
    for field in ("dni", "fullName", "birthDate", "sex"):
        if field in update_data and update_data[field] is None:
            raise HTTPException(
                status_code=422,
                detail={"code": "INVALID_FIELD", "message": f"El campo {field} no puede ser nulo."},
            )

    if "dni" in update_data and update_data["dni"] != patient.dni:
        existing = await repo.get_patient_by_dni(
            db, update_data["dni"], exclude_patient_id=patient.id
        )
        if existing is not None:
            raise HTTPException(
                status_code=409,
                detail={"code": "DNI_CONFLICT", "message": "Ya existe un paciente con ese DNI."},
            )
        patient.dni = update_data["dni"]
        patient.medical_record_num = update_data["dni"]

    if "fullName" in update_data:
        patient.first_name, patient.last_name = _split_full_name(update_data["fullName"])
    if "birthDate" in update_data:
        patient.date_of_birth = update_data["birthDate"]
    if "sex" in update_data:
        patient.sex = update_data["sex"]
    if "contactEmail" in update_data:
        patient.email = update_data["contactEmail"]
    if "contactPhone" in update_data:
        patient.phone = update_data["contactPhone"]

    await db.flush()
    row = await repo.get_patient_row(db, patient.id, input_data.doctor_id)
    if row is None:
        raise HTTPException(status_code=500, detail="Updated patient not found")
    return _patient_out(row)


async def delete_patient(input_data: PatientIdInput, db: AsyncSession) -> None:
    patient = await repo.get_patient_model(db, input_data.patient_id, input_data.doctor_id)
    if patient is None:
        raise HTTPException(
            status_code=404,
            detail={"code": "PATIENT_NOT_FOUND", "message": "Paciente no encontrado."},
        )
    await repo.soft_delete_patient(db, patient)


async def get_patient_device(input_data: PatientIdInput, db: AsyncSession) -> HolterHealthOut:
    patient = await repo.get_patient_model(db, input_data.patient_id, input_data.doctor_id)
    if patient is None:
        raise HTTPException(
            status_code=404,
            detail={"code": "PATIENT_NOT_FOUND", "message": "Paciente no encontrado."},
        )

    device = await repo.get_assigned_device_for_patient(db, patient.id)
    if device is None:
        raise HTTPException(
            status_code=404,
            detail={"code": "DEVICE_NOT_FOUND", "message": "Paciente sin Holter asignado."},
        )
    # Mismo helper que GET /devices/{id}/health: el FE consume el mismo tipo
    # HolterHealth desde los dos endpoints y los valores tienen que coincidir.
    return holter_health_out(device)


async def get_patient_summary(
    input_data: PatientSummaryInput, db: AsyncSession
) -> PatientSummaryOut:
    patient = await repo.get_patient_model(db, input_data.patient_id, input_data.doctor_id)
    if patient is None:
        raise HTTPException(
            status_code=404,
            detail={"code": "PATIENT_NOT_FOUND", "message": "Paciente no encontrado."},
        )
    return PatientSummaryOut(
        windowHours=input_data.window_hours,
        heartRate=None,
        eventsDetected=None,
        adherencePercent=None,
    )
