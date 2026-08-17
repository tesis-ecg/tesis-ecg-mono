"""Users service."""

from datetime import UTC, datetime

from fastapi import HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.auth0_client import (
    Auth0Error,
    block_auth0_user,
    create_auth0_user,
    delete_auth0_user,
    trigger_password_reset,
    update_auth0_user_email,
)
from app.db.models.audit_event import AuditEventType
from app.db.models.user import User, UserRole
from app.modules.auth import auth_repository as auth_repo
from app.modules.doctors import doctors_repository as doctors_repo
from app.modules.users import users_repository as repo
from app.modules.users.users_schemas import (
    UserAccountOut,
    UserCreateInput,
    UserIdInput,
    UserListInput,
    UserUpdateEmailInput,
)

CREATABLE_ROLES = (UserRole.MEDICO, UserRole.ADMIN)


def _user_account_out(user: User) -> UserAccountOut:
    return UserAccountOut(
        id=user.id,
        email=user.email,
        fullName=user.full_name,
        role=user.role,
        isActive=user.is_active,
        createdAt=user.created_at,
    )


def _not_found() -> HTTPException:
    return HTTPException(
        status_code=404,
        detail={"code": "USER_NOT_FOUND", "message": "Usuario no encontrado."},
    )


def _email_conflict() -> HTTPException:
    return HTTPException(
        status_code=409,
        detail={
            "code": "EMAIL_CONFLICT",
            "message": "Ese email ya está en uso, incluso por una cuenta dada de baja.",
        },
    )


async def list_users(input_data: UserListInput, db: AsyncSession) -> list[UserAccountOut]:
    users = await repo.list_users(db)
    return [_user_account_out(user) for user in users]


async def create_user(input_data: UserCreateInput, db: AsyncSession) -> UserAccountOut:
    # El rol se valida acá y no con un Literal en el schema: un 422 de pydantic
    # devuelve `detail` como array y el frontend lo renderiza como [object Object].
    if input_data.data.role not in CREATABLE_ROLES:
        raise HTTPException(
            status_code=422,
            detail={
                "code": "INVALID_ROLE",
                "message": "Solo se pueden crear usuarios médico o admin.",
            },
        )

    existing = await repo.get_user_by_email_any(db, input_data.data.email)
    if existing is not None:
        raise _email_conflict()

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

    try:
        user = await repo.create_user(
            db,
            auth0_id=auth0_id,
            email=input_data.data.email,
            full_name=input_data.data.fullName,
            role=input_data.data.role,
        )
        if user.role == UserRole.MEDICO:
            await doctors_repo.create(db, user.id)
        await auth_repo.log_audit_event(
            db,
            AuditEventType.REGISTER,
            user_id=input_data.requesting_user.id,
            ip_address=input_data.ip,
            metadata={"new_user_id": str(user.id), "role": user.role.value},
        )
    except Exception:
        # get_db() recién hace rollback cuando la route propaga la excepción, así
        # que la compensación tiene que ocurrir acá o queda un huérfano en Auth0.
        await delete_auth0_user(auth0_id)
        raise

    return _user_account_out(user)


async def update_user_email(input_data: UserUpdateEmailInput, db: AsyncSession) -> UserAccountOut:
    user = await repo.get_user_by_id(db, input_data.user_id)
    if user is None:
        raise _not_found()

    email = input_data.data.email
    if email == user.email:
        return _user_account_out(user)

    existing = await repo.get_user_by_email_any(db, email)
    if existing is not None:
        raise _email_conflict()

    if user.auth0_id is not None:
        try:
            await update_auth0_user_email(user.auth0_id, email)
        except Auth0Error as exc:
            raise HTTPException(
                status_code=exc.status,
                detail={"code": exc.code, "message": exc.message},
            ) from exc

    user.email = email
    await db.flush()
    return _user_account_out(user)


async def delete_user(input_data: UserIdInput, db: AsyncSession) -> None:
    if input_data.user_id == input_data.requesting_user.id:
        raise HTTPException(
            status_code=409,
            detail={"code": "CANNOT_DELETE_SELF", "message": "No podés eliminar tu propia cuenta."},
        )

    user = await repo.get_user_by_id(db, input_data.user_id)
    if user is None:
        raise _not_found()

    if user.role == UserRole.ADMIN and await repo.count_active_admins(db) <= 1:
        raise HTTPException(
            status_code=409,
            detail={
                "code": "LAST_ADMIN",
                "message": "No se puede eliminar al último administrador activo.",
            },
        )

    if user.auth0_id is not None:
        try:
            await block_auth0_user(user.auth0_id)
        except Auth0Error as exc:
            raise HTTPException(
                status_code=exc.status,
                detail={"code": exc.code, "message": exc.message},
            ) from exc

    # deleted_at lo saca de GET /users; is_active=False corta la sesión viva con
    # un 401 en el próximo request (get_current_user chequea is_active).
    user.deleted_at = datetime.now(UTC)
    user.is_active = False
    await db.flush()


async def send_password_reset(input_data: UserIdInput, db: AsyncSession) -> None:
    user = await repo.get_user_by_id(db, input_data.user_id)
    if user is None:
        raise _not_found()

    await trigger_password_reset(user.email)
    await auth_repo.log_audit_event(
        db,
        AuditEventType.PASSWORD_RESET_REQUESTED,
        user_id=input_data.requesting_user.id,
        ip_address=input_data.ip,
        metadata={"target_user_id": str(user.id), "email": user.email},
    )
