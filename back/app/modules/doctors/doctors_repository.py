"""Doctors repository."""

import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models.doctor import Doctor
from app.db.models.user import User


async def list_options(db: AsyncSession) -> list[tuple[uuid.UUID, str]]:
    result = await db.execute(
        select(Doctor.id, User.full_name)
        .join(User, Doctor.user_id == User.id)
        .where(
            Doctor.deleted_at.is_(None),
            User.deleted_at.is_(None),
            User.is_active.is_(True),
        )
        .order_by(User.full_name.asc())
    )
    return [(doctor_id, full_name) for doctor_id, full_name in result.all()]


async def get_by_user_id(db: AsyncSession, user_id: uuid.UUID) -> Doctor | None:
    result = await db.execute(
        select(Doctor).where(Doctor.user_id == user_id, Doctor.deleted_at.is_(None))
    )
    return result.scalar_one_or_none()


async def get_active_by_id(db: AsyncSession, doctor_id: uuid.UUID) -> Doctor | None:
    result = await db.execute(
        select(Doctor)
        .join(User, Doctor.user_id == User.id)
        .where(
            Doctor.id == doctor_id,
            Doctor.deleted_at.is_(None),
            User.deleted_at.is_(None),
            User.is_active.is_(True),
        )
    )
    return result.scalar_one_or_none()


async def create(db: AsyncSession, user_id: uuid.UUID) -> Doctor:
    doctor = Doctor(user_id=user_id)
    db.add(doctor)
    await db.flush()
    return doctor
