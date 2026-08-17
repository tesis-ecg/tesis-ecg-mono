import uuid

from sqlalchemy import Select, String, cast, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models.device import Device
from app.db.models.doctor import Doctor
from app.db.models.patient import Patient
from app.db.models.study import Study, StudyStatus
from app.db.models.user import User


def _apply_study_filters(
    statement: Select[tuple[Study, Patient, Device, str | None]] | Select[tuple[int]],
    doctor_id: uuid.UUID | None,
    q: str | None,
    statuses: list[StudyStatus] | None,
) -> Select[tuple[Study, Patient, Device, str | None]] | Select[tuple[int]]:
    statement = statement.where(
        Study.deleted_at.is_(None),
        Patient.deleted_at.is_(None),
        Device.deleted_at.is_(None),
    )
    if doctor_id is not None:
        statement = statement.where(Patient.doctor_id == doctor_id)
    if q:
        pattern = f"%{q.strip()}%"
        full_name = func.concat(Patient.first_name, " ", Patient.last_name)
        statement = statement.where(
            or_(
                full_name.ilike(pattern),
                Device.serial_number.ilike(pattern),
                cast(Study.id, String).ilike(pattern),
            )
        )
    if statuses:
        statement = statement.where(Study.status.in_(statuses))
    return statement


async def list_studies(
    db: AsyncSession,
    doctor_id: uuid.UUID | None,
    q: str | None,
    statuses: list[StudyStatus] | None,
    limit: int,
    offset: int,
) -> tuple[list[tuple[Study, Patient, Device, str | None]], int]:
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
    statement = _apply_study_filters(
        select(Study, Patient, Device, doctor_name.label("doctor_name"))
        .join(Patient, Study.patient_id == Patient.id)
        .join(Device, Study.device_id == Device.id),
        doctor_id,
        q,
        statuses,
        # `Study.id` desempata para que la paginación tenga un orden total.
    ).order_by(Study.started_at.desc(), Study.id.asc())
    count_statement = _apply_study_filters(
        select(func.count())
        .select_from(Study)
        .join(Patient, Study.patient_id == Patient.id)
        .join(Device, Study.device_id == Device.id),
        doctor_id,
        q,
        statuses,
    )

    result = await db.execute(statement.limit(limit).offset(offset))
    total = await db.scalar(count_statement)
    rows = [(study, patient, device, name) for study, patient, device, name in result.all()]
    return rows, total or 0


async def list_for_patient(
    db: AsyncSession, patient_id: uuid.UUID, doctor_id: uuid.UUID | None
) -> tuple[list[Study], int] | None:
    patient_statement = (
        select(func.count())
        .select_from(Patient)
        .where(Patient.id == patient_id, Patient.deleted_at.is_(None))
    )
    if doctor_id is not None:
        patient_statement = patient_statement.where(Patient.doctor_id == doctor_id)
    patient_exists = await db.scalar(patient_statement)
    if not patient_exists:
        return None

    statement = (
        select(Study)
        .where(Study.patient_id == patient_id, Study.deleted_at.is_(None))
        .order_by(Study.started_at.desc())
    )
    count_statement = (
        select(func.count())
        .select_from(Study)
        .where(Study.patient_id == patient_id, Study.deleted_at.is_(None))
    )
    result = await db.execute(statement)
    total = await db.scalar(count_statement)
    return list(result.scalars().all()), total or 0


async def get_detail(
    db: AsyncSession, study_id: uuid.UUID, doctor_id: uuid.UUID | None
) -> tuple[Study, Patient, Device, str | None] | None:
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
    statement = (
        select(Study, Patient, Device, doctor_name.label("doctor_name"))
        .join(Patient, Study.patient_id == Patient.id)
        .join(Device, Study.device_id == Device.id)
        .where(
            Study.id == study_id,
            Study.deleted_at.is_(None),
            Patient.deleted_at.is_(None),
            Device.deleted_at.is_(None),
        )
    )
    if doctor_id is not None:
        statement = statement.where(Patient.doctor_id == doctor_id)
    result = await db.execute(statement)
    row = result.one_or_none()
    if row is None:
        return None
    study, patient, device, name = row
    return study, patient, device, name
