import uuid

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models.device import Device
from app.db.models.patient import Patient
from app.db.models.study import Study


async def list_for_patient(
    db: AsyncSession, patient_id: uuid.UUID, doctor_id: uuid.UUID
) -> tuple[list[Study], int] | None:
    patient_exists = await db.scalar(
        select(func.count())
        .select_from(Patient)
        .where(
            Patient.id == patient_id,
            Patient.doctor_id == doctor_id,
            Patient.deleted_at.is_(None),
        )
    )
    if patient_exists == 0:
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
    db: AsyncSession, study_id: uuid.UUID, doctor_id: uuid.UUID
) -> tuple[Study, Patient, Device] | None:
    result = await db.execute(
        select(Study, Patient, Device)
        .join(Patient, Study.patient_id == Patient.id)
        .join(Device, Study.device_id == Device.id)
        .where(
            Study.id == study_id,
            Study.deleted_at.is_(None),
            Patient.doctor_id == doctor_id,
            Patient.deleted_at.is_(None),
        )
    )
    row = result.one_or_none()
    if row is None:
        return None
    study, patient, device = row
    return study, patient, device
