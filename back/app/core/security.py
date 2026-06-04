from datetime import UTC, datetime, timedelta

from jose import JWTError, jwt

from app.core.config import settings
from app.db.models.user import User


def create_access_token(user: User) -> tuple[str, datetime]:
    expires_at = datetime.now(UTC) + timedelta(minutes=settings.jwt_expire_minutes)
    payload = {
        "sub": str(user.id),
        "email": user.email,
        "role": user.role.value,
        "iat": datetime.now(UTC),
        "exp": expires_at,
    }
    token = jwt.encode(payload, settings.jwt_secret, algorithm=settings.jwt_algorithm)
    return token, expires_at


def decode_access_token(token: str) -> dict[str, object]:
    try:
        return dict(jwt.decode(token, settings.jwt_secret, algorithms=[settings.jwt_algorithm]))
    except JWTError as exc:
        raise ValueError("Invalid or expired token") from exc
