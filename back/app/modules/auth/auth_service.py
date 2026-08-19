import hashlib
import hmac
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from fastapi import HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.auth0_client import (
    Auth0Error,
    authenticate_user,
    trigger_password_reset,
)
from app.core.config import settings
from app.core.security import create_access_token
from app.db.models.audit_event import AuditEventType
from app.db.models.user import User
from app.modules.auth import auth_repository as repo
from app.modules.auth.auth_schemas import LoginResponse, RegisterRequest, UserOut


def _user_out(user: User) -> UserOut:
    return UserOut(id=str(user.id), email=user.email, fullName=user.full_name, role=user.role)


@dataclass
class LoginInput:
    email: str
    password: str
    ip: str | None


@dataclass
class LogoutInput:
    user: User
    ip: str | None


@dataclass
class RegisterInput:
    data: RegisterRequest
    requesting_user: User
    ip: str | None


@dataclass
class ForgotPasswordInput:
    email: str
    ip: str | None


async def login(input_data: LoginInput, db: AsyncSession) -> tuple[str, LoginResponse]:
    email = input_data.email.strip().lower()
    account_key = _rate_limit_key("login-account", f"{email}|{input_data.ip or 'unknown'}")
    ip_key = _rate_limit_key("login-ip", input_data.ip or "unknown")
    await _enforce_rate_limits(
        db,
        ((account_key, 5, 15 * 60), (ip_key, 20, 15 * 60)),
    )
    try:
        auth0_id = await authenticate_user(email, input_data.password, input_data.ip)
    except Auth0Error as exc:
        await repo.log_audit_event(
            db,
            AuditEventType.LOGIN_FAILED,
            ip_address=input_data.ip,
            metadata={"identity_key": _rate_limit_key("audit-email", email), "error": exc.code},
        )
        await db.commit()
        raise HTTPException(
            status_code=exc.status,
            detail={"code": exc.code, "message": exc.message},
        ) from exc

    user = await repo.get_user_by_auth0_id(db, auth0_id)
    if user is None:
        await repo.log_audit_event(
            db,
            AuditEventType.LOGIN_FAILED,
            ip_address=input_data.ip,
            metadata={
                "identity_key": _rate_limit_key("audit-sub", auth0_id),
                "error": "USER_NOT_PROVISIONED",
            },
        )
        await db.commit()
        raise HTTPException(
            status_code=403,
            detail={
                "code": "USER_NOT_PROVISIONED",
                "message": "La cuenta no fue habilitada por un administrador.",
            },
        )

    if not user.is_active or user.identity_status.value != "active":
        await repo.log_audit_event(
            db,
            AuditEventType.LOGIN_FAILED,
            user_id=user.id,
            ip_address=input_data.ip,
            metadata={"error": "USER_INACTIVE"},
        )
        await db.commit()
        raise HTTPException(
            status_code=403, detail={"code": "USER_INACTIVE", "message": "Cuenta inactiva."}
        )

    token, expires_at = create_access_token(user)
    await repo.log_audit_event(
        db, AuditEventType.LOGIN_OK, user_id=user.id, ip_address=input_data.ip
    )
    await repo.clear_rate_limit(db, account_key)
    await db.commit()

    return token, LoginResponse(
        user=_user_out(user),
        expiresAt=expires_at.isoformat(),
    )


async def logout(input_data: LogoutInput, db: AsyncSession) -> None:
    await repo.set_last_logout(db, input_data.user.id)
    await repo.log_audit_event(
        db, AuditEventType.LOGOUT, user_id=input_data.user.id, ip_address=input_data.ip
    )
    await db.commit()


async def register(input_data: RegisterInput, db: AsyncSession) -> UserOut:
    # Compatibilidad temporal: un único flujo de provisión vive en users_service.
    from app.modules.users.users_schemas import UserCreateInput, UserCreateRequest
    from app.modules.users.users_service import create_user

    created = await create_user(
        UserCreateInput(
            requesting_user=input_data.requesting_user,
            data=UserCreateRequest(**input_data.data.model_dump()),
            ip=input_data.ip,
        ),
        db,
    )
    return UserOut(
        id=str(created.id),
        email=created.email,
        fullName=created.fullName,
        role=created.role,
    )


async def forgot_password(input_data: ForgotPasswordInput, db: AsyncSession) -> None:
    email = input_data.email.strip().lower()
    await _enforce_rate_limits(
        db,
        (
            (_rate_limit_key("forgot-email", email), 3, 60 * 60),
            (_rate_limit_key("forgot-ip", input_data.ip or "unknown"), 10, 60 * 60),
        ),
    )
    # Always log and trigger — never reveal whether the email exists
    user = await repo.get_user_by_email(db, email)
    await repo.log_audit_event(
        db,
        AuditEventType.PASSWORD_RESET_REQUESTED,
        user_id=user.id if user else None,
        ip_address=input_data.ip,
        metadata={"identity_key": _rate_limit_key("audit-email", email)},
    )
    await db.commit()
    await trigger_password_reset(email)


def _rate_limit_key(namespace: str, value: str) -> str:
    return hmac.new(
        settings.rate_limit_secret.encode(),
        f"{namespace}:{value}".encode(),
        hashlib.sha256,
    ).hexdigest()


def _bucket_start(now: datetime, window_seconds: int) -> datetime:
    epoch = int(now.timestamp())
    return datetime.fromtimestamp(epoch - (epoch % window_seconds), tz=UTC)


async def _enforce_rate_limits(
    db: AsyncSession,
    policies: tuple[tuple[str, int, int], ...],
) -> None:
    now = datetime.now(UTC)
    await repo.prune_rate_limits(db, now - timedelta(hours=2))
    retry_after = 0
    is_allowed = True
    for key, limit, window_seconds in policies:
        allowed = await repo.consume_rate_limit(
            db,
            key=key,
            bucket_start=_bucket_start(now, window_seconds),
            limit=limit,
        )
        is_allowed = is_allowed and allowed
        retry_after = max(retry_after, window_seconds)
    await db.commit()
    if not is_allowed:
        raise HTTPException(
            status_code=429,
            detail={"code": "RATE_LIMITED", "message": "Demasiados intentos. Probá más tarde."},
            headers={"Retry-After": str(retry_after)},
        )
