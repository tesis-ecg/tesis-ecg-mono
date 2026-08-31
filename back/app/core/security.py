import uuid
from datetime import UTC, datetime, timedelta

from app.core.config import settings
from app.core.jwt_codec import JwtValidationError, decode_hs256, encode_hs256
from app.db.models.user import User


def create_access_token(user: User) -> tuple[str, datetime]:
    expires_at = datetime.now(UTC) + timedelta(minutes=settings.jwt_expire_minutes)
    payload = {
        "sub": str(user.id),
        "email": user.email,
        "role": user.role.value,
        "session_version": user.session_version,
        "iss": settings.jwt_issuer,
        "aud": settings.jwt_audience,
        "jti": str(uuid.uuid4()),
        "iat": int(datetime.now(UTC).timestamp()),
        "exp": int(expires_at.timestamp()),
    }
    token = encode_hs256(payload, settings.jwt_secret)
    return token, expires_at


def decode_access_token(token: str) -> dict[str, object]:
    try:
        return dict(
            decode_hs256(
                token,
                settings.jwt_secret,
                issuer=settings.jwt_issuer,
                audience=settings.jwt_audience,
                required={"sub", "exp", "iat", "iss", "aud", "jti", "session_version"},
            )
        )
    except JwtValidationError as exc:
        raise ValueError("Invalid or expired token") from exc


#: Tokens de la app móvil. El paciente no maneja cookie: `holter_session_v2`
#: tiene `Path=/api` y `SameSite=Lax`, que no significan nada en React Native, y
#: el chequeo de Origin del portal rechazaría a un cliente que no manda `Origin`.
#: Por eso la app usa Bearer, con audience propia para que un token del portal
#: no sirva en `/mobile` ni al revés.
MOBILE_ACCESS = "access"
MOBILE_REFRESH = "refresh"


def _create_mobile_token(user: User, typ: str, expires_at: datetime) -> str:
    payload = {
        "sub": str(user.id),
        "email": user.email,
        "role": user.role.value,
        "typ": typ,
        "session_version": user.session_version,
        "iss": settings.jwt_issuer,
        "aud": settings.jwt_mobile_audience,
        "jti": str(uuid.uuid4()),
        "iat": int(datetime.now(UTC).timestamp()),
        "exp": int(expires_at.timestamp()),
    }
    return encode_hs256(payload, settings.jwt_secret)


def create_mobile_tokens(user: User) -> tuple[str, str, datetime]:
    """`(access, refresh, expiración del access)`.

    No hay tabla de refresh tokens: los dos llevan el `session_version` del
    usuario, así que el logout —que lo incrementa— mata a los dos de una.
    """
    now = datetime.now(UTC)
    access_expires_at = now + timedelta(minutes=settings.mobile_access_expire_minutes)
    refresh_expires_at = now + timedelta(days=settings.mobile_refresh_expire_days)
    return (
        _create_mobile_token(user, MOBILE_ACCESS, access_expires_at),
        _create_mobile_token(user, MOBILE_REFRESH, refresh_expires_at),
        access_expires_at,
    )


def decode_mobile_token(token: str, expected_typ: str) -> dict[str, object]:
    """Valida firma, claims y **tipo**.

    El chequeo de `typ` no es decorativo: sin él un refresh de 60 días serviría
    como access, y la ventana de un token robado pasaría de una hora a dos meses.
    """
    try:
        payload = dict(
            decode_hs256(
                token,
                settings.jwt_secret,
                issuer=settings.jwt_issuer,
                audience=settings.jwt_mobile_audience,
                required={"sub", "exp", "iat", "iss", "aud", "jti", "session_version", "typ"},
            )
        )
    except JwtValidationError as exc:
        raise ValueError("Invalid or expired token") from exc
    if payload.get("typ") != expected_typ:
        raise ValueError("Unexpected token type")
    return payload
