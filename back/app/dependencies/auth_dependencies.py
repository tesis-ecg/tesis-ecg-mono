import uuid
from dataclasses import dataclass
from datetime import UTC, datetime

from fastapi import Cookie, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security import decode_access_token
from app.db.models.doctor import Doctor
from app.db.models.user import User, UserRole
from app.dependencies.common_dependencies import get_db


async def get_current_user(
    session: str | None = Cookie(default=None, alias="session"),
    db: AsyncSession = Depends(get_db),
) -> User:
    # Lazy import breaks the auth_dependencies ↔ auth_routes circular import.
    from app.modules.auth import auth_repository as repo

    if not session:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={"code": "UNAUTHORIZED", "message": "No autenticado."},
        )

    try:
        payload = decode_access_token(session)
    except ValueError:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={"code": "UNAUTHORIZED", "message": "Token inválido o vencido."},
        )

    user_id_raw = payload.get("sub")
    if not isinstance(user_id_raw, str):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={"code": "UNAUTHORIZED", "message": "Token inválido."},
        )

    try:
        user_id = uuid.UUID(user_id_raw)
    except ValueError:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={"code": "UNAUTHORIZED", "message": "Token inválido."},
        )

    user = await repo.get_user_by_id(db, user_id)
    if user is None or not user.is_active:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={"code": "UNAUTHORIZED", "message": "Usuario no encontrado o inactivo."},
        )

    # Invalidate tokens issued before last logout
    iat_raw = payload.get("iat")
    if user.last_logout_at is not None and isinstance(iat_raw, (int, float)):
        issued_at = datetime.fromtimestamp(float(iat_raw), tz=UTC)
        if issued_at <= user.last_logout_at:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail={
                    "code": "UNAUTHORIZED",
                    "message": "Sesión cerrada. Iniciá sesión nuevamente.",
                },
            )

    return user


async def require_admin(current_user: User = Depends(get_current_user)) -> User:
    if current_user.role != UserRole.ADMIN:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={"code": "FORBIDDEN", "message": "Se requiere rol de administrador."},
        )
    return current_user


async def get_current_doctor(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> Doctor:
    from app.modules.doctors import doctors_repository as doctors_repo

    doctor = await doctors_repo.get_by_user_id(db, current_user.id)
    if doctor is None:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={"code": "FORBIDDEN", "message": "Esta cuenta no tiene perfil de médico."},
        )
    return doctor


# Roles de solo lectura que ven la vista global sin tener perfil médico.
GLOBAL_READ_ROLES = (UserRole.INVESTIGADOR, UserRole.ASISTENTE)


@dataclass(frozen=True)
class RoleScope:
    user: User
    # None => vista global: admin o rol de solo lectura (GLOBAL_READ_ROLES)
    doctor_id: uuid.UUID | None
    is_admin: bool


async def get_role_scope(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> RoleScope:
    from app.modules.doctors import doctors_repository as doctors_repo

    # El chequeo de rol va ANTES de buscar la fila Doctor: un admin promovido
    # desde una cuenta médico conserva su perfil y debe ver la vista global igual.
    if current_user.role == UserRole.ADMIN:
        return RoleScope(user=current_user, doctor_id=None, is_admin=True)
    doctor = await doctors_repo.get_by_user_id(db, current_user.id)
    if doctor is None and current_user.role not in GLOBAL_READ_ROLES:
        # Fail-closed: `doctor_id=None` significa "vista global" para los repos, así
        # que una cuenta sin perfil médico (rol paciente, o un médico cuya fila
        # Doctor falta) no puede caer en esa rama.
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={"code": "FORBIDDEN", "message": "Esta cuenta no tiene perfil de médico."},
        )
    return RoleScope(
        user=current_user,
        doctor_id=doctor.id if doctor is not None else None,
        is_admin=False,
    )


async def get_doctor_scope(scope: RoleScope = Depends(get_role_scope)) -> RoleScope:
    if not scope.is_admin and scope.doctor_id is None:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={"code": "FORBIDDEN", "message": "Esta cuenta no tiene perfil de médico."},
        )
    return scope
