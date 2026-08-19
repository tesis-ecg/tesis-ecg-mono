import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException

from app.db.models.user import IdentityStatus, UserRole
from app.dependencies import auth_dependencies
from app.dependencies.auth_dependencies import ScopeKind
from app.modules.auth import auth_repository


@pytest.mark.asyncio
async def test_session_version_rejects_a_token_after_logout(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    user_id = uuid.uuid4()
    user = SimpleNamespace(
        id=user_id,
        is_active=True,
        identity_status=IdentityStatus.ACTIVE,
        session_version=2,
    )
    monkeypatch.setattr(
        auth_dependencies,
        "decode_access_token",
        lambda _: {"sub": str(user_id), "session_version": 1},
    )
    monkeypatch.setattr(auth_repository, "get_user_by_id", AsyncMock(return_value=user))

    with pytest.raises(HTTPException) as error:
        await auth_dependencies.get_current_user("token", None, AsyncMock())

    assert error.value.status_code == 401
    assert error.value.detail["message"] == "Sesión cerrada."


@pytest.mark.asyncio
async def test_unknown_role_fails_closed_before_database_access() -> None:
    current_user = SimpleNamespace(id=uuid.uuid4(), role="legacy-role")
    db = AsyncMock()

    with pytest.raises(HTTPException) as error:
        await auth_dependencies.get_role_scope(current_user, db)  # type: ignore[arg-type]

    assert error.value.status_code == 403
    db.execute.assert_not_awaited()


@pytest.mark.asyncio
async def test_admin_scope_is_explicitly_global() -> None:
    current_user = SimpleNamespace(id=uuid.uuid4(), role=UserRole.ADMIN)

    scope = await auth_dependencies.get_role_scope(current_user, AsyncMock())  # type: ignore[arg-type]

    assert scope.kind == ScopeKind.ADMIN_GLOBAL
    assert scope.doctor_id is None
    assert scope.is_admin is True
