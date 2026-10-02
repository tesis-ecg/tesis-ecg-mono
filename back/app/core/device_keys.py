"""Material criptográfico de la API key de un chaleco.

La API key vive en la base **dos veces**, y cada copia sirve para algo distinto:

- `device.api_key_hash` es el sha256 y es la credencial autoritativa. Es contra
  esto que `get_authenticated_device` valida cada lote de tramas, y es lo único
  que toca el camino caliente de ingesta.
- `device.api_key_encrypted` es la misma key cifrada con Fernet. Existe porque
  el admin tiene que poder volver a leerla para grabarla en el firmware del
  chaleco: con solo el hash, aprovisionar un equipo ya dado de alta obligaba a
  rotarle la key, y rotarla deja fuera de servicio al equipo que ya la tenía.

Un dump de la base sin el secreto de la aplicación no revela ninguna key.
"""

import base64
import hashlib
import secrets
from functools import lru_cache

from cryptography.fernet import Fernet, InvalidToken
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.kdf.hkdf import HKDF

from app.core.config import settings

#: Separación de dominio del HKDF. Es lo que impide que la clave derivada sirva
#: para nada más que descifrar API keys de dispositivos, aunque salga del mismo
#: secreto que firma las sesiones.
_HKDF_INFO = b"device-api-key-v1"


@lru_cache(maxsize=1)
def _fernet() -> Fernet:
    """Fernet de `DEVICE_API_KEY_SECRET`, o derivado de `JWT_SECRET` si no está.

    El fallback no es pereza: hacer obligatoria una variable nueva rompería todos
    los `.env` locales, el compose, el CI y el deploy en el mismo commit. Es el
    mismo criterio que `settings.rate_limit_secret`.
    """
    secret = settings.device_api_key_secret or settings.jwt_secret
    derived = HKDF(
        algorithm=hashes.SHA256(),
        length=32,
        salt=None,
        info=_HKDF_INFO,
    ).derive(secret.encode())
    return Fernet(base64.urlsafe_b64encode(derived))


def generate_api_key() -> str:
    return secrets.token_urlsafe(32)


def hash_api_key(api_key: str) -> str:
    """sha256 hex. Es lo que compara la ingesta, con `hmac.compare_digest`."""
    return hashlib.sha256(api_key.encode()).hexdigest()


def encrypt_api_key(api_key: str) -> str:
    return _fernet().encrypt(api_key.encode()).decode()


def decrypt_api_key(ciphertext: str) -> str | None:
    """La key en claro, o `None` si el token no se puede descifrar.

    Devuelve `None` en vez de propagar: si alguien rota el secreto de la
    aplicación, el detalle del equipo tiene que degradar a "rotala para generar
    una nueva" y no responder un 500.
    """
    try:
        return _fernet().decrypt(ciphertext.encode()).decode()
    except InvalidToken:
        return None
