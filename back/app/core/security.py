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
