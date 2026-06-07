from dataclasses import dataclass

from fastapi import HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.auth0_client import (
    Auth0Error,
    authenticate_user,
    create_auth0_user,
    trigger_password_reset,
)
from app.core.security import create_access_token
from app.db.models.audit_event import AuditEventType
from app.db.models.user import User, UserRole
from app.modules.auth import auth_repository as repo
from app.modules.auth.auth_schemas import LoginResponse, RegisterRequest, UserOut
from app.modules.doctors import doctors_repository as doctors_repo


def _user_out(user: User) -> UserOut:
    return UserOut(id=str(user.id), email=user.email, fullName=user.full_name, role=user.role.value)


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
    try:
        auth0_id = await authenticate_user(input_data.email, input_data.password)
    except Auth0Error as exc:
        await repo.log_audit_event(
            db,
            AuditEventType.LOGIN_FAILED,
            ip_address=input_data.ip,
            metadata={"email": input_data.email, "error": exc.code},
        )
        raise HTTPException(
            status_code=exc.status,
            detail={"code": exc.code, "message": exc.message},
        ) from exc

    user = await repo.get_user_by_auth0_id(db, auth0_id)
    if user is None:
        # First login — auto-provision with default role; admin can promote later
        user = await repo.create_user(
            db,
            auth0_id=auth0_id,
            email=input_data.email,
            full_name=input_data.email.split("@")[0],
            role=UserRole.MEDICO,
        )
        await doctors_repo.create(db, user.id)

    if not user.is_active:
        await repo.log_audit_event(
            db,
            AuditEventType.LOGIN_FAILED,
            user_id=user.id,
            ip_address=input_data.ip,
            metadata={"error": "USER_INACTIVE"},
        )
        raise HTTPException(
            status_code=403, detail={"code": "USER_INACTIVE", "message": "Cuenta inactiva."}
        )

    token, expires_at = create_access_token(user)
    await repo.log_audit_event(
        db, AuditEventType.LOGIN_OK, user_id=user.id, ip_address=input_data.ip
    )

    return token, LoginResponse(
        user=_user_out(user),
        expiresAt=expires_at.isoformat(),
    )


async def logout(input_data: LogoutInput, db: AsyncSession) -> None:
    await repo.set_last_logout(db, input_data.user.id)
    await repo.log_audit_event(
        db, AuditEventType.LOGOUT, user_id=input_data.user.id, ip_address=input_data.ip
    )


async def register(input_data: RegisterInput, db: AsyncSession) -> UserOut:
    if input_data.requesting_user.role != UserRole.ADMIN:
        raise HTTPException(
            status_code=403,
            detail={"code": "FORBIDDEN", "message": "Solo admins pueden registrar usuarios."},
        )

    existing = await repo.get_user_by_email(db, input_data.data.email)
    if existing:
        raise HTTPException(
            status_code=409, detail={"code": "EMAIL_CONFLICT", "message": "Email ya registrado."}
        )

    try:
        auth0_id = await create_auth0_user(
            input_data.data.email,
            input_data.data.password,
            input_data.data.fullName,
        )
    except Auth0Error as exc:
        raise HTTPException(
            status_code=exc.status,
            detail={"code": exc.code, "message": exc.message},
        ) from exc

    user = await repo.create_user(
        db,
        auth0_id=auth0_id,
        email=input_data.data.email,
        full_name=input_data.data.fullName,
        role=input_data.data.role,
    )
    if user.role == UserRole.MEDICO:
        await doctors_repo.create(db, user.id)
    await repo.log_audit_event(
        db,
        AuditEventType.REGISTER,
        user_id=input_data.requesting_user.id,
        ip_address=input_data.ip,
        metadata={"new_user_id": str(user.id), "role": user.role.value},
    )
    return _user_out(user)


async def forgot_password(input_data: ForgotPasswordInput, db: AsyncSession) -> None:
    # Always log and trigger — never reveal whether the email exists
    user = await repo.get_user_by_email(db, input_data.email)
    await repo.log_audit_event(
        db,
        AuditEventType.PASSWORD_RESET_REQUESTED,
        user_id=user.id if user else None,
        ip_address=input_data.ip,
        metadata={"email": input_data.email},
    )
    await trigger_password_reset(input_data.email)
