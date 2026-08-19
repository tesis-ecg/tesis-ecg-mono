"""Minimal JWT codec restricted to the two algorithms used by this service."""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import time
from collections.abc import Collection
from typing import Any

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import padding, rsa


class JwtValidationError(ValueError):
    pass


def _base64url_encode(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).rstrip(b"=").decode("ascii")


def _base64url_decode(value: str) -> bytes:
    try:
        return base64.b64decode(value + "=" * (-len(value) % 4), altchars=b"-_", validate=True)
    except (ValueError, UnicodeEncodeError) as exc:
        raise JwtValidationError("JWT base64 inválido") from exc


def _json_segment(value: dict[str, Any]) -> str:
    return _base64url_encode(
        json.dumps(value, ensure_ascii=True, separators=(",", ":"), sort_keys=True).encode()
    )


def _segments(token: str) -> tuple[str, str, str]:
    parts = token.split(".")
    if len(parts) != 3 or any(not part for part in parts):
        raise JwtValidationError("JWT malformado")
    return parts[0], parts[1], parts[2]


def _json_object(segment: str) -> dict[str, Any]:
    try:
        value = json.loads(_base64url_decode(segment))
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise JwtValidationError("JWT JSON inválido") from exc
    if not isinstance(value, dict):
        raise JwtValidationError("JWT JSON inválido")
    return value


def unverified_header(token: str) -> dict[str, Any]:
    header, _, _ = _segments(token)
    return _json_object(header)


def encode_hs256(payload: dict[str, Any], secret: str) -> str:
    encoded_header = _json_segment({"alg": "HS256", "typ": "JWT"})
    encoded_payload = _json_segment(payload)
    signing_input = f"{encoded_header}.{encoded_payload}".encode("ascii")
    signature = hmac.new(secret.encode(), signing_input, hashlib.sha256).digest()
    return f"{encoded_header}.{encoded_payload}.{_base64url_encode(signature)}"


def _validate_registered_claims(
    payload: dict[str, Any],
    *,
    issuer: str,
    audience: str,
    required: Collection[str],
) -> None:
    if any(claim not in payload for claim in required):
        raise JwtValidationError("Faltan claims requeridos")
    now = int(time.time())
    exp = payload.get("exp")
    iat = payload.get("iat")
    nbf = payload.get("nbf")
    if not isinstance(exp, int) or exp <= now:
        raise JwtValidationError("JWT vencido")
    if iat is not None and (not isinstance(iat, int) or iat > now + 60):
        raise JwtValidationError("iat inválido")
    if nbf is not None and (not isinstance(nbf, int) or nbf > now + 60):
        raise JwtValidationError("JWT todavía no válido")
    if payload.get("iss") != issuer:
        raise JwtValidationError("issuer inválido")
    token_audience = payload.get("aud")
    if token_audience != audience and not (
        isinstance(token_audience, list) and audience in token_audience
    ):
        raise JwtValidationError("audience inválido")


def decode_hs256(
    token: str,
    secret: str,
    *,
    issuer: str,
    audience: str,
    required: Collection[str],
) -> dict[str, Any]:
    encoded_header, encoded_payload, encoded_signature = _segments(token)
    header = _json_object(encoded_header)
    if header.get("alg") != "HS256":
        raise JwtValidationError("Algoritmo JWT no permitido")
    signing_input = f"{encoded_header}.{encoded_payload}".encode("ascii")
    expected = hmac.new(secret.encode(), signing_input, hashlib.sha256).digest()
    if not hmac.compare_digest(expected, _base64url_decode(encoded_signature)):
        raise JwtValidationError("Firma JWT inválida")
    payload = _json_object(encoded_payload)
    _validate_registered_claims(payload, issuer=issuer, audience=audience, required=required)
    return payload


def decode_rs256(
    token: str,
    jwk: dict[str, object],
    *,
    issuer: str,
    audience: str,
) -> dict[str, Any]:
    encoded_header, encoded_payload, encoded_signature = _segments(token)
    header = _json_object(encoded_header)
    if header.get("alg") != "RS256":
        raise JwtValidationError("Algoritmo JWT no permitido")
    modulus = jwk.get("n")
    exponent = jwk.get("e")
    if not isinstance(modulus, str) or not isinstance(exponent, str):
        raise JwtValidationError("JWK RSA inválida")
    try:
        public_key = rsa.RSAPublicNumbers(
            int.from_bytes(_base64url_decode(exponent), "big"),
            int.from_bytes(_base64url_decode(modulus), "big"),
        ).public_key()
    except (TypeError, ValueError) as exc:
        raise JwtValidationError("JWK RSA inválida") from exc
    try:
        public_key.verify(
            _base64url_decode(encoded_signature),
            f"{encoded_header}.{encoded_payload}".encode("ascii"),
            padding.PKCS1v15(),
            hashes.SHA256(),
        )
    except InvalidSignature as exc:
        raise JwtValidationError("Firma JWT inválida") from exc
    payload = _json_object(encoded_payload)
    _validate_registered_claims(
        payload,
        issuer=issuer,
        audience=audience,
        required={"sub", "exp", "iat", "iss", "aud"},
    )
    return payload
