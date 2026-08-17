"""Patients repository stubs."""

import uuid
from datetime import UTC, date, datetime

from sqlalchemy import Select, func, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models.device import Device, DeviceStatus
from app.db.models.doctor import Doctor
from app.db.models.patient import Patient, PatientSex, PatientStudyStatus
from app.db.models.user import User
from app.modules.patients.patients_schemas import PatientRow


def _patient_row_statement() -> Select[tuple[Patient, uuid.UUID | None, str | None]]:
    assigned_device_id = (
        select(Device.id)
        .where(
            Device.patient_id == Patient.id,
            Device.deleted_at.is_(None),
            Device.status == DeviceStatus.ASSIGNED,
        )
        # order_by explícito: sin él, un paciente con dos devices asignados (estado
        # alcanzable por carrera) devolvería una fila arbitraria en cada request.
        .order_by(Device.created_at.desc(), Device.id.asc())
        .limit(1)
        .scalar_subquery()
    )
    doctor_name = (
        select(User.full_name)
        .join(Doctor, Doctor.user_id == User.id)
        .where(
            Doctor.id == Patient.doctor_id,
            Doctor.deleted_at.is_(None),
            User.deleted_at.is_(None),
            User.is_active.is_(True),
        )
        .limit(1)
        .scalar_subquery()
    )
    return select(
        Patient,
        assigned_device_id.label("assigned_device_id"),
        doctor_name.label("doctor_name"),
    )


def _to_row(
    patient: Patient, assigned_device_id: uuid.UUID | None, doctor_name: str | None
) -> PatientRow:
    return PatientRow(
        id=patient.id,
        first_name=patient.first_name,
        last_name=patient.last_name,
        date_of_birth=patient.date_of_birth,
        dni=patient.dni,
        sex=patient.sex,
        study_status=patient.study_status,
        last_data_received_at=patient.last_data_received_at,
        email=patient.email,
        phone=patient.phone,
        assigned_device_id=assigned_device_id,
        doctor_id=patient.doctor_id,
        doctor_name=doctor_name,
    )


def _apply_patient_filters(
    statement: Select[tuple[Patient, uuid.UUID | None, str | None]] | Select[tuple[int]],
    doctor_id: uuid.UUID | None,
    q: str | None,
    statuses: list[PatientStudyStatus] | None,
) -> Select[tuple[Patient, uuid.UUID | None, str | None]] | Select[tuple[int]]:
    statement = statement.where(Patient.deleted_at.is_(None))
    if doctor_id is not None:
        statement = statement.where(Patient.doctor_id == doctor_id)
    if q:
        pattern = f"%{q.strip()}%"
        full_name = func.concat(Patient.first_name, " ", Patient.last_name)
        statement = statement.where(or_(full_name.ilike(pattern), Patient.dni.ilike(pattern)))
    if statuses:
        statement = statement.where(Patient.study_status.in_(statuses))
    return statement


async def list_patients(
    db: AsyncSession,
    doctor_id: uuid.UUID | None,
    q: str | None,
    statuses: list[PatientStudyStatus] | None,
    limit: int,
    offset: int,
    sort: str,
    order: str,
) -> tuple[list[PatientRow], int]:
    statement = _apply_patient_filters(_patient_row_statement(), doctor_id, q, statuses)
    count_statement = _apply_patient_filters(
        select(func.count()).select_from(Patient), doctor_id, q, statuses
    )

    # `Patient.id` cierra cada orden: sin desempate, dos homónimos (o dos filas con el
    # mismo last_data_received_at) pueden repetirse entre páginas.
    if sort == "lastDataReceivedAt" and order == "desc":
        statement = statement.order_by(
            Patient.last_data_received_at.desc().nullslast(),
            Patient.last_name.desc(),
            Patient.id.asc(),
        )
    elif sort == "lastDataReceivedAt":
        statement = statement.order_by(
            Patient.last_data_received_at.asc().nullslast(),
            Patient.last_name.asc(),
            Patient.id.asc(),
        )
    elif order == "desc":
        statement = statement.order_by(
            Patient.first_name.desc(), Patient.last_name.desc(), Patient.id.asc()
        )
    else:
        statement = statement.order_by(
            Patient.first_name.asc(), Patient.last_name.asc(), Patient.id.asc()
        )

    result = await db.execute(statement.limit(limit).offset(offset))
    total = await db.scalar(count_statement)
    rows = [
        _to_row(patient, assigned_device_id, doctor_name)
        for patient, assigned_device_id, doctor_name in result.all()
    ]
    return rows, total or 0


async def get_patient_row(
    db: AsyncSession, patient_id: uuid.UUID, doctor_id: uuid.UUID | None
) -> PatientRow | None:
    statement = _patient_row_statement().where(
        Patient.id == patient_id,
        Patient.deleted_at.is_(None),
    )
    if doctor_id is not None:
        statement = statement.where(Patient.doctor_id == doctor_id)
    result = await db.execute(statement)
    row = result.one_or_none()
    if row is None:
        return None
    patient, assigned_device_id, doctor_name = row
    return _to_row(patient, assigned_device_id, doctor_name)


async def get_patient_model(
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


async def get_patient_by_dni(
    db: AsyncSession, dni: str, exclude_patient_id: uuid.UUID | None = None
) -> Patient | None:
    statement = select(Patient).where(Patient.dni == dni, Patient.deleted_at.is_(None))
    if exclude_patient_id is not None:
        statement = statement.where(Patient.id != exclude_patient_id)
    result = await db.execute(statement)
    return result.scalar_one_or_none()


async def create_patient(
    db: AsyncSession,
    doctor_id: uuid.UUID,
    first_name: str,
    last_name: str,
    dni: str,
    date_of_birth: date,
    sex: PatientSex,
    email: str | None,
    phone: str | None,
) -> Patient:
    patient = Patient(
        doctor_id=doctor_id,
        medical_record_num=dni,
        first_name=first_name,
        last_name=last_name,
        dni=dni,
        date_of_birth=date_of_birth,
        sex=sex,
        email=email,
        phone=phone,
        study_status=PatientStudyStatus.NONE,
    )
    db.add(patient)
    await db.flush()
    return patient


async def unassign_patient_devices(db: AsyncSession, patient_id: uuid.UUID) -> None:
    await db.execute(
        update(Device)
        .where(Device.patient_id == patient_id, Device.deleted_at.is_(None))
        .values(patient_id=None, status=DeviceStatus.AVAILABLE)
    )


async def soft_delete_patient(db: AsyncSession, patient: Patient) -> None:
    patient.deleted_at = datetime.now(UTC)
    await unassign_patient_devices(db, patient.id)
    await db.flush()


async def get_assigned_device_for_patient(db: AsyncSession, patient_id: uuid.UUID) -> Device | None:
    # first() y no scalar_one_or_none(): con dos devices ASSIGNED sobre el mismo
    # paciente (carrera entre dos assign) esto sería un 500 por MultipleResultsFound.
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
