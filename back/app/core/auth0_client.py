"""Auth0 HTTP client — ROPG login, Management API (user CRUD, password reset)."""

import asyncio
from datetime import UTC, datetime, timedelta
from urllib.parse import quote

import httpx

from app.core.config import settings
from app.core.jwt_codec import JwtValidationError, decode_rs256, unverified_header


class Auth0Error(Exception):
    def __init__(self, code: str, message: str, status: int = 400) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.status = status


# --- Management API token cache (in-memory, single process) ---
class _MgmtTokenCache:
    def __init__(self) -> None:
        self.token: str | None = None
        self.expires_at: datetime = datetime.min.replace(tzinfo=UTC)
        self.lock = asyncio.Lock()


_cache = _MgmtTokenCache()


async def _get_mgmt_token() -> str:
    async with _cache.lock:
        if _cache.token and datetime.now(UTC) < _cache.expires_at:
            return _cache.token

        async with httpx.AsyncClient() as client:
            resp = await client.post(
                f"https://{settings.auth0_domain}/oauth/token",
                json={
                    "grant_type": "client_credentials",
                    "client_id": settings.auth0_mgmt_client_id,
                    "client_secret": settings.auth0_mgmt_client_secret,
                    "audience": f"https://{settings.auth0_domain}/api/v2/",
                },
                timeout=10,
            )

        if resp.status_code != 200:
            raise Auth0Error("MGMT_TOKEN_ERROR", "Failed to obtain Auth0 management token", 500)

        data = resp.json()
        _cache.token = data["access_token"]
        expires_in: int = data.get("expires_in", 86400)
        _cache.expires_at = datetime.now(UTC) + timedelta(seconds=expires_in - 60)
        return _cache.token


def _mgmt_user_url(auth0_id: str) -> str:
    # Los auth0_id llevan `|` (auth0|abc123): sin escapar, la URL sale rota.
    return f"https://{settings.auth0_domain}/api/v2/users/{quote(auth0_id, safe='')}"


class _JwksCache:
    def __init__(self) -> None:
        self.keys: list[dict[str, object]] = []
        self.expires_at: datetime = datetime.min.replace(tzinfo=UTC)
        self.lock = asyncio.Lock()


_jwks_cache = _JwksCache()


async def _get_jwks() -> list[dict[str, object]]:
    async with _jwks_cache.lock:
        if _jwks_cache.keys and datetime.now(UTC) < _jwks_cache.expires_at:
            return _jwks_cache.keys
        async with httpx.AsyncClient() as client:
            response = await client.get(
                f"https://{settings.auth0_domain}/.well-known/jwks.json", timeout=10
            )
        if response.status_code != 200:
            raise Auth0Error("AUTH0_JWKS_ERROR", "No se pudo validar la identidad.", 502)
        keys = response.json().get("keys", [])
        if not isinstance(keys, list):
            raise Auth0Error("AUTH0_JWKS_ERROR", "JWKS inválido.", 502)
        _jwks_cache.keys = [key for key in keys if isinstance(key, dict)]
        _jwks_cache.expires_at = datetime.now(UTC) + timedelta(hours=1)
        return _jwks_cache.keys


async def authenticate_user(email: str, password: str, client_ip: str | None = None) -> str:
    """ROPG login → returns auth0_id (sub claim). Raises Auth0Error on failure."""
    async with httpx.AsyncClient() as client:
        headers = {"auth0-forwarded-for": client_ip} if client_ip else None
        resp = await client.post(
            f"https://{settings.auth0_domain}/oauth/token",
            json={
                "grant_type": "password",
                "username": email,
                "password": password,
                "audience": settings.auth0_audience,
                "scope": "openid",
                "client_id": settings.auth0_client_id,
                "client_secret": settings.auth0_client_secret,
                "connection": settings.auth0_connection,
            },
            headers=headers,
            timeout=15,
        )

    if resp.status_code == 200:
        access_token = str(resp.json()["access_token"])
        try:
            header = unverified_header(access_token)
            kid = header.get("kid")
            keys = await _get_jwks()
            signing_key = next((key for key in keys if key.get("kid") == kid), None)
            if signing_key is None:
                _jwks_cache.expires_at = datetime.min.replace(tzinfo=UTC)
                keys = await _get_jwks()
                signing_key = next((key for key in keys if key.get("kid") == kid), None)
            if signing_key is None:
                raise Auth0Error("AUTH0_TOKEN_INVALID", "Clave de firma desconocida.", 502)
            claims = decode_rs256(
                access_token,
                signing_key,
                audience=settings.auth0_audience,
                issuer=f"https://{settings.auth0_domain}/",
            )
            return str(claims["sub"])
        except (JwtValidationError, KeyError) as exc:
            raise Auth0Error(
                "AUTH0_TOKEN_INVALID", "Auth0 devolvió un token inválido.", 502
            ) from exc

    body = resp.json()
    error_code = body.get("error", "")
    description = body.get("error_description", "")

    if error_code == "invalid_grant" or "Wrong email or password" in description:
        raise Auth0Error("INVALID_CREDENTIALS", "Credenciales inválidas.", 401)
    if error_code == "unauthorized_client":
        raise Auth0Error("USER_BLOCKED", "Usuario bloqueado o inactivo.", 403)
    raise Auth0Error("AUTH0_ERROR", description or "Auth0 error.", 502)


async def create_auth0_user(email: str, password: str, full_name: str) -> str:
    """Management API: create user → returns auth0_id."""
    token = await _get_mgmt_token()

    async with httpx.AsyncClient() as client:
        resp = await client.post(
            f"https://{settings.auth0_domain}/api/v2/users",
            headers={"Authorization": f"Bearer {token}"},
            json={
                "email": email,
                "password": password,
                "name": full_name,
                "connection": settings.auth0_connection,
                "email_verified": False,
            },
            timeout=15,
        )

    if resp.status_code == 201:
        return str(resp.json()["user_id"])

    body = resp.json()
    if resp.status_code == 409:
        raise Auth0Error("EMAIL_CONFLICT", "Email already registered.", 409)
    raise Auth0Error("AUTH0_ERROR", body.get("message", "Failed to create user."), 502)


async def update_auth0_user_email(auth0_id: str, email: str) -> None:
    """Management API: update user email."""
    token = await _get_mgmt_token()

    async with httpx.AsyncClient() as client:
        resp = await client.patch(
            _mgmt_user_url(auth0_id),
            headers={"Authorization": f"Bearer {token}"},
            json={"email": email, "connection": settings.auth0_connection},
            timeout=15,
        )

    if resp.status_code == 200:
        return

    body = resp.json()
    if resp.status_code == 409:
        raise Auth0Error("EMAIL_CONFLICT", "Email already registered.", 409)
    raise Auth0Error("AUTH0_ERROR", body.get("message", "Failed to update user email."), 502)


async def block_auth0_user(auth0_id: str) -> None:
    """Management API: block user (baja lógica)."""
    token = await _get_mgmt_token()

    async with httpx.AsyncClient() as client:
        resp = await client.patch(
            _mgmt_user_url(auth0_id),
            headers={"Authorization": f"Bearer {token}"},
            json={"blocked": True},
            timeout=15,
        )

    if resp.status_code == 200:
        return

    body = resp.json()
    raise Auth0Error("AUTH0_ERROR", body.get("message", "Failed to block user."), 502)


async def delete_auth0_user(auth0_id: str) -> None:
    """Management API: delete user (compensación de un create fallido)."""
    token = await _get_mgmt_token()

    async with httpx.AsyncClient() as client:
        resp = await client.delete(
            _mgmt_user_url(auth0_id),
            headers={"Authorization": f"Bearer {token}"},
            timeout=15,
        )

    # El early return va ANTES de resp.json(): un 204 llega con el body vacío.
    if resp.status_code in (200, 204, 404):
        return

    raise Auth0Error("AUTH0_ERROR", "Failed to delete user.", 502)


async def trigger_password_reset(email: str) -> None:
    """Auth0 Change Password ticket — fire and forget (always 204 to caller)."""
    async with httpx.AsyncClient() as client:
        await client.post(
            f"https://{settings.auth0_domain}/dbconnections/change_password",
            json={
                "client_id": settings.auth0_client_id,
                "email": email,
                "connection": settings.auth0_connection,
            },
            timeout=10,
        )
