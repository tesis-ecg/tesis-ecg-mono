import uuid
from types import SimpleNamespace

import pytest

from app.core.security import create_access_token, decode_access_token
from app.db.models.user import UserRole


def test_session_token_has_required_security_claims() -> None:
    user_id = uuid.uuid4()
    user = SimpleNamespace(
        id=user_id,
        email="doctor@example.com",
        role=UserRole.MEDICO,
        session_version=7,
    )

    token, _ = create_access_token(user)  # type: ignore[arg-type]
    payload = decode_access_token(token)

    assert payload["sub"] == str(user_id)
    assert payload["session_version"] == 7
    assert payload["iss"] == "holter-api"
    assert payload["aud"] == "holter-dashboard"
    assert isinstance(payload["jti"], str)


def test_session_token_rejects_tampering() -> None:
    user = SimpleNamespace(
        id=uuid.uuid4(), email="doctor@example.com", role=UserRole.MEDICO, session_version=0
    )
    token, _ = create_access_token(user)  # type: ignore[arg-type]

    with pytest.raises(ValueError):
        decode_access_token(f"{token[:-1]}x")
