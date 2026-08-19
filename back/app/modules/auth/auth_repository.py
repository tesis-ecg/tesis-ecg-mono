import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import delete, select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models.audit_event import AuditEvent, AuditEventType
from app.db.models.auth_rate_limit import AuthRateLimit
from app.db.models.user import User, UserRole


async def get_user_by_auth0_id(db: AsyncSession, auth0_id: str) -> User | None:
    result = await db.execute(select(User).where(User.auth0_id == auth0_id))
    return result.scalar_one_or_none()


async def get_user_by_id(db: AsyncSession, user_id: uuid.UUID) -> User | None:
    result = await db.execute(select(User).where(User.id == user_id, User.deleted_at.is_(None)))
    return result.scalar_one_or_none()


async def get_user_by_email(db: AsyncSession, email: str) -> User | None:
    result = await db.execute(select(User).where(User.email == email, User.deleted_at.is_(None)))
    return result.scalar_one_or_none()


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


async def set_last_logout(db: AsyncSession, user_id: uuid.UUID) -> None:
    await db.execute(
        update(User)
        .where(User.id == user_id)
        .values(
            last_logout_at=datetime.now(UTC),
            session_version=User.session_version + 1,
        )
    )


async def consume_rate_limit(
    db: AsyncSession,
    *,
    key: str,
    bucket_start: datetime,
    limit: int,
) -> bool:
    statement = (
        insert(AuthRateLimit)
        .values(key=key, bucket_start=bucket_start, attempts=1)
        .on_conflict_do_update(
            index_elements=[AuthRateLimit.key, AuthRateLimit.bucket_start],
            set_={"attempts": AuthRateLimit.attempts + 1},
        )
        .returning(AuthRateLimit.attempts)
    )
    attempts = await db.scalar(statement)
    return bool(attempts is not None and attempts <= limit)


async def clear_rate_limit(db: AsyncSession, key: str) -> None:
    await db.execute(delete(AuthRateLimit).where(AuthRateLimit.key == key))


async def prune_rate_limits(db: AsyncSession, before: datetime) -> None:
    await db.execute(delete(AuthRateLimit).where(AuthRateLimit.bucket_start < before))


async def log_audit_event(
    db: AsyncSession,
    event_type: AuditEventType,
    user_id: uuid.UUID | None = None,
    ip_address: str | None = None,
    metadata: dict[str, Any] | None = None,
) -> None:
    event = AuditEvent(
        user_id=user_id,
        event_type=event_type,
        ip_address=ip_address,
        event_metadata=metadata,
    )
    db.add(event)
    await db.flush()
