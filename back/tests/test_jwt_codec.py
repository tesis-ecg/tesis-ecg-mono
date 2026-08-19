import base64
import json
import time

import pytest
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import padding, rsa

from app.core.jwt_codec import JwtValidationError, decode_hs256, decode_rs256, encode_hs256


def _b64(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).rstrip(b"=").decode()


def _segment(value: dict[str, object]) -> str:
    return _b64(json.dumps(value, separators=(",", ":"), sort_keys=True).encode())


def test_hs256_rejects_expired_tokens() -> None:
    token = encode_hs256(
        {"sub": "subject", "iss": "issuer", "aud": "audience", "iat": 1, "exp": 2},
        "a-secret-that-is-long-enough-for-this-test",
    )

    with pytest.raises(JwtValidationError, match="vencido"):
        decode_hs256(
            token,
            "a-secret-that-is-long-enough-for-this-test",
            issuer="issuer",
            audience="audience",
            required={"sub", "exp", "iat", "iss", "aud"},
        )


def test_rs256_verifies_signature_issuer_and_audience() -> None:
    private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    numbers = private_key.public_key().public_numbers()
    header = _segment({"alg": "RS256", "typ": "JWT", "kid": "test-key"})
    payload = _segment(
        {
            "sub": "auth0|subject",
            "iss": "https://tenant.example/",
            "aud": ["https://api.example", "userinfo"],
            "iat": int(time.time()),
            "exp": int(time.time()) + 60,
        }
    )
    signing_input = f"{header}.{payload}".encode()
    signature = private_key.sign(signing_input, padding.PKCS1v15(), hashes.SHA256())
    token = f"{header}.{payload}.{_b64(signature)}"
    jwk = {
        "kty": "RSA",
        "kid": "test-key",
        "n": _b64(numbers.n.to_bytes((numbers.n.bit_length() + 7) // 8, "big")),
        "e": _b64(numbers.e.to_bytes((numbers.e.bit_length() + 7) // 8, "big")),
    }

    claims = decode_rs256(
        token,
        jwk,
        issuer="https://tenant.example/",
        audience="https://api.example",
    )
    assert claims["sub"] == "auth0|subject"

    tampered_signature = bytearray(signature)
    tampered_signature[0] ^= 1
    with pytest.raises(JwtValidationError):
        decode_rs256(
            f"{header}.{payload}.{_b64(bytes(tampered_signature))}",
            jwk,
            issuer="https://tenant.example/",
            audience="https://api.example",
        )
