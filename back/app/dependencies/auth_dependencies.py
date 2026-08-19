import uuid
from dataclasses import dataclass
from enum import StrEnum

from fastapi import Cookie, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security import decode_access_token
from app.db.models.doctor import Doctor
from app.db.models.user import IdentityStatus, User, UserRole
from app.dependencies.common_dependencies import get_db


async def get_current_user(
    session_v2: str | None = Cookie(default=None, alias="holter_session_v2"),
    legacy_session: str | None = Cookie(default=None, alias="session"),
    db: AsyncSession = Depends(get_db),
) -> User:
    # Lazy import breaks the auth_dependencies ↔ auth_routes circular import.
    from app.modules.auth import auth_repository as repo

    session = session_v2 or legacy_session
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
    if user is None or not user.is_active or user.identity_status != IdentityStatus.ACTIVE:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={"code": "UNAUTHORIZED", "message": "Usuario no encontrado o inactivo."},
        )

    session_version = payload.get("session_version")
    if not isinstance(session_version, int) or session_version != user.session_version:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={"code": "UNAUTHORIZED", "message": "Sesión cerrada."},
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


class ScopeKind(StrEnum):
    ADMIN_GLOBAL = "admin_global"
    DOCTOR = "doctor"


@dataclass(frozen=True)
class RoleScope:
    user: User
    kind: ScopeKind
    doctor_id: uuid.UUID | None

    @property
    def is_admin(self) -> bool:
        return self.kind == ScopeKind.ADMIN_GLOBAL

    def require_doctor_id(self) -> uuid.UUID:
        if self.kind != ScopeKind.DOCTOR or self.doctor_id is None:
            raise RuntimeError("El scope no corresponde a un médico")
        return self.doctor_id


async def get_role_scope(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> RoleScope:
    from app.modules.doctors import doctors_repository as doctors_repo

    # El chequeo de rol va ANTES de buscar la fila Doctor: un admin promovido
    # desde una cuenta médico conserva su perfil y debe ver la vista global igual.
    if current_user.role == UserRole.ADMIN:
        return RoleScope(user=current_user, kind=ScopeKind.ADMIN_GLOBAL, doctor_id=None)
    if current_user.role != UserRole.MEDICO:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={"code": "FORBIDDEN", "message": "Rol no permitido."},
        )
    doctor = await doctors_repo.get_by_user_id(db, current_user.id)
    if doctor is None:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={"code": "FORBIDDEN", "message": "Esta cuenta no tiene perfil de médico."},
        )
    return RoleScope(user=current_user, kind=ScopeKind.DOCTOR, doctor_id=doctor.id)


async def get_doctor_scope(scope: RoleScope = Depends(get_role_scope)) -> RoleScope:
    return scope
