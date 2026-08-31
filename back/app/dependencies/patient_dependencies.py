"""Autenticación del paciente en la app móvil.

Es el paralelo de `auth_dependencies` para el celular, y **no comparte el
transporte** con él a propósito. El portal se autentica con la cookie
`holter_session_v2` (`Path=/api`, `SameSite=Lax`) y el middleware de `main.py`
exige un `Origin` válido en todo POST. Nada de eso aplica a un cliente React
Native: no hay navegador, no hay cookie y no se manda `Origin`.

La app usa entonces `Authorization: Bearer`, con una audience propia
(`jwt_mobile_audience`) que impide que un token del portal sirva acá o al revés.
Sin cookie no hay superficie CSRF, que es lo que justifica exceptuar `/mobile`
del chequeo de Origin — el mismo razonamiento que ya se aplicó a `/ingest`.
"""

import uuid
from dataclasses import dataclass

from fastapi import Depends, Header, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security import MOBILE_ACCESS, decode_mobile_token
from app.db.models.patient import Patient
from app.db.models.user import IdentityStatus, User, UserRole
from app.dependencies.common_dependencies import get_db


def _unauthorized(message: str, code: str = "UNAUTHORIZED") -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail={"code": code, "message": message},
        headers={"WWW-Authenticate": "Bearer"},
    )


def bearer_token(authorization: str | None) -> str:
    scheme, _, token = (authorization or "").partition(" ")
    if scheme.lower() != "bearer" or not token:
        raise _unauthorized("No autenticado.")
    return token


async def resolve_patient_user(db: AsyncSession, token: str, expected_typ: str) -> User:
    """Valida el token y devuelve el usuario paciente que hay detrás.

    Compartido por el access de cada request y por el refresh: los dos tienen
    que chequear exactamente lo mismo (usuario vivo, rol correcto,
    `session_version` al día), y duplicar la lógica es cómo se cuela un agujero.
    """
    from app.modules.auth import auth_repository as repo

    try:
        payload = decode_mobile_token(token, expected_typ)
    except ValueError:
        raise _unauthorized("Sesión vencida. Ingresá de nuevo.", "SESSION_EXPIRED") from None

    user_id_raw = payload.get("sub")
    if not isinstance(user_id_raw, str):
        raise _unauthorized("Token inválido.")
    try:
        user_id = uuid.UUID(user_id_raw)
    except ValueError:
        raise _unauthorized("Token inválido.") from None

    user = await repo.get_user_by_id(db, user_id)
    if user is None or not user.is_active or user.identity_status != IdentityStatus.ACTIVE:
        raise _unauthorized("Usuario no encontrado o inactivo.")
    if user.role != UserRole.PACIENTE:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={"code": "FORBIDDEN", "message": "Esta cuenta no es de un paciente."},
        )

    session_version = payload.get("session_version")
    if not isinstance(session_version, int) or session_version != user.session_version:
        raise _unauthorized("Sesión cerrada.", "SESSION_EXPIRED")
    return user


@dataclass(frozen=True)
class PatientContext:
    user: User
    patient: Patient


async def get_current_patient(
    authorization: str | None = Header(default=None),
    db: AsyncSession = Depends(get_db),
) -> PatientContext:
    from app.modules.patient_app import patient_app_repository as repo

    user = await resolve_patient_user(db, bearer_token(authorization), MOBILE_ACCESS)
    patient = await repo.get_patient_by_user_id(db, user.id)
    if patient is None:
        # La cuenta existe pero perdió su ficha: paciente dado de baja, o una
        # provisión que quedó a medias. No es un 401 —el token es válido— pero
        # tampoco hay nada que mostrar.
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={"code": "PATIENT_NOT_FOUND", "message": "La cuenta no tiene ficha activa."},
        )
    return PatientContext(user=user, patient=patient)
