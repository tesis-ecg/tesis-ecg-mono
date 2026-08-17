"""Doctors service."""

import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.doctors import doctors_repository as repo
from app.modules.doctors.doctors_schemas import DoctorListInput, DoctorOptionOut


def _doctor_option_out(doctor_id: uuid.UUID, full_name: str) -> DoctorOptionOut:
    return DoctorOptionOut(id=doctor_id, fullName=full_name)


async def list_doctor_options(
    input_data: DoctorListInput, db: AsyncSession
) -> list[DoctorOptionOut]:
    options = await repo.list_options(db)
    return [_doctor_option_out(doctor_id, full_name) for doctor_id, full_name in options]
