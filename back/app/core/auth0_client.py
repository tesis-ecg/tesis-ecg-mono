"""Auth0 HTTP client — ROPG login, Management API (create user, password reset)."""

import asyncio
from datetime import UTC, datetime, timedelta

import httpx

from app.core.config import settings


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


async def authenticate_user(email: str, password: str) -> str:
    """ROPG login → returns auth0_id (sub claim). Raises Auth0Error on failure."""
    async with httpx.AsyncClient() as client:
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
            timeout=15,
        )

    if resp.status_code == 200:
        # Decode without verification — we trust our own Auth0 tenant
        import jose.jwt as _jwt

        claims = _jwt.get_unverified_claims(resp.json()["access_token"])
        return str(claims["sub"])

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
