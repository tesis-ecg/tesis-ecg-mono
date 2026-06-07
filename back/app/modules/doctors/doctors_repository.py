import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models.doctor import Doctor


async def get_by_user_id(db: AsyncSession, user_id: uuid.UUID) -> Doctor | None:
    result = await db.execute(select(Doctor).where(Doctor.user_id == user_id))
    return result.scalar_one_or_none()


async def create(db: AsyncSession, user_id: uuid.UUID) -> Doctor:
    doctor = Doctor(user_id=user_id)
    db.add(doctor)
    await db.flush()
    return doctor
