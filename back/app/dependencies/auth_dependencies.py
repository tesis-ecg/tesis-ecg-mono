import uuid
from datetime import UTC, datetime

from fastapi import Cookie, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security import decode_access_token
from app.db.models.doctor import Doctor
from app.db.models.user import User
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
