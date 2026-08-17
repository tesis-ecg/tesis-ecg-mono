"""Users repository."""

import uuid

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models.user import User, UserRole


async def list_users(db: AsyncSession) -> list[User]:
    result = await db.execute(
        select(User).where(User.deleted_at.is_(None)).order_by(User.full_name.asc())
    )
    return list(result.scalars().all())


async def get_user_by_id(db: AsyncSession, user_id: uuid.UUID) -> User | None:
    result = await db.execute(select(User).where(User.id == user_id, User.deleted_at.is_(None)))
    return result.scalar_one_or_none()


async def get_user_by_email_any(db: AsyncSession, email: str) -> User | None:
    # Sin filtro de soft-delete a propósito: `user.email` es UNIQUE a secas, así que
    # una cuenta dada de baja sigue ocupando el email y un re-alta explotaría con
    # unique violation (500 sin `code`) en vez de un 409 prolijo.
    result = await db.execute(select(User).where(User.email == email))
    return result.scalar_one_or_none()


async def count_active_admins(db: AsyncSession) -> int:
    result = await db.execute(
        select(func.count())
        .select_from(User)
        .where(
            User.role == UserRole.ADMIN,
            User.is_active.is_(True),
            User.deleted_at.is_(None),
        )
    )
    return result.scalar_one()


async def create_user(
    db: AsyncSession,
    auth0_id: str,
    email: str,
    full_name: str,
    role: UserRole,
) -> User:
    user = User(
        auth0_id=auth0_id,
        email=email,
        full_name=full_name,
        role=role,
        is_active=True,
    )
    db.add(user)
    await db.flush()
    return user
