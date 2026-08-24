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
    header, payload, signature = token.split(".")

    # Se altera el PAYLOAD, no el último carácter de la firma. La firma HS256
    # son 32 bytes = 43 caracteres base64url, y 43*6 = 258 bits: los 2 últimos
    # bits del último carácter no codifican nada. Reemplazarlo dejaba la firma
    # decodificada intacta en ~1 de cada 4 corridas y el test fallaba solo a
    # veces — un flake real, no una hipótesis.
    tampered_payload = ("A" if payload[0] != "A" else "B") + payload[1:]

    with pytest.raises(ValueError):
        decode_access_token(f"{header}.{tampered_payload}.{signature}")

    with pytest.raises(ValueError):
        decode_access_token(f"{header}.{payload}.{'A' * len(signature)}")
