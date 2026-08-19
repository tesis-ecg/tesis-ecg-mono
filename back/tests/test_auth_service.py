from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException

from app.modules.auth import auth_service
from app.modules.auth.auth_service import LoginInput


@pytest.mark.asyncio
async def test_login_does_not_auto_provision_unknown_auth0_identity(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    db = AsyncMock()
    monkeypatch.setattr(auth_service, "authenticate_user", AsyncMock(return_value="auth0|unknown"))
    monkeypatch.setattr(auth_service.repo, "consume_rate_limit", AsyncMock(return_value=True))
    monkeypatch.setattr(auth_service.repo, "get_user_by_auth0_id", AsyncMock(return_value=None))
    monkeypatch.setattr(auth_service.repo, "log_audit_event", AsyncMock())

    with pytest.raises(HTTPException) as error:
        await auth_service.login(
            LoginInput(email="NEW@EXAMPLE.COM", password="secret", ip="127.0.0.1"), db
        )

    assert error.value.status_code == 403
    assert error.value.detail["code"] == "USER_NOT_PROVISIONED"
    assert db.commit.await_count == 2


@pytest.mark.asyncio
async def test_login_rate_limit_is_persisted_before_rejection(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    db = AsyncMock()
    monkeypatch.setattr(
        auth_service.repo, "consume_rate_limit", AsyncMock(side_effect=[False, True])
    )

    with pytest.raises(HTTPException) as error:
        await auth_service.login(
            LoginInput(email="doctor@example.com", password="secret", ip="127.0.0.1"), db
        )

    assert error.value.status_code == 429
    assert error.value.headers == {"Retry-After": "900"}
    db.commit.assert_awaited_once()
