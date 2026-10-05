from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models.doctor import Doctor
from app.db.models.user import IdentityStatus, UserRole
from app.modules.auth import auth_repository as repo
from app.scripts.seed_e2e_user import seed_e2e_user


async def _doctor_count(db: AsyncSession, user_id: object) -> int:
    return (
        await db.scalar(select(func.count()).select_from(Doctor).where(Doctor.user_id == user_id))
        or 0
    )


async def test_creates_an_active_doctor_the_login_can_find(db: AsyncSession) -> None:
    user = await seed_e2e_user(
        db,
        email="E2E@Example.test ",
        auth0_id="auth0|e2e-1",
        role=UserRole.MEDICO,
        full_name="E2E Test User",
    )

    found = await repo.get_user_by_auth0_id(db, "auth0|e2e-1")
    assert found is not None and found.id == user.id
    assert found.email == "e2e@example.test"
    assert found.role == UserRole.MEDICO
    assert found.is_active and found.identity_status == IdentityStatus.ACTIVE
    assert await _doctor_count(db, user.id) == 1


async def test_is_idempotent_and_repairs_a_stale_row(
    db: AsyncSession, make_user: Callable[..., Any]
) -> None:
    stale = await make_user(
        UserRole.PACIENTE,
        email="e2e@example.test",
        auth0_id="auth0|old",
        full_name="Nombre que se conserva",
    )
    stale.is_active = False
    stale.identity_status = IdentityStatus.ERROR
    stale.pending_email = "otro@example.test"
    stale.deleted_at = datetime.now(UTC)
    await db.flush()

    args = {"email": "e2e@example.test", "auth0_id": "auth0|e2e-2", "role": UserRole.MEDICO}
    first = await seed_e2e_user(db, full_name="E2E Test User", **args)
    second = await seed_e2e_user(db, full_name="E2E Test User", **args)

    assert first.id == second.id == stale.id
    assert second.auth0_id == "auth0|e2e-2"
    assert second.role == UserRole.MEDICO
    assert second.is_active and second.identity_status == IdentityStatus.ACTIVE
    assert second.pending_email is None and second.deleted_at is None
    assert second.full_name == "Nombre que se conserva"
    assert await _doctor_count(db, second.id) == 1


async def test_admin_gets_no_doctor_profile(db: AsyncSession) -> None:
    user = await seed_e2e_user(
        db,
        email="admin-e2e@example.test",
        auth0_id="auth0|e2e-admin",
        role=UserRole.ADMIN,
        full_name="E2E Admin",
    )

    assert user.role == UserRole.ADMIN
    assert await _doctor_count(db, user.id) == 0


async def test_refuses_an_auth0_id_that_belongs_to_someone_else(
    db: AsyncSession, make_user: Callable[..., Any]
) -> None:
    other = await make_user(email="other@example.test", auth0_id="auth0|taken")

    with pytest.raises(ValueError, match=other.email):
        await seed_e2e_user(
            db,
            email="e2e@example.test",
            auth0_id="auth0|taken",
            role=UserRole.MEDICO,
            full_name="E2E Test User",
        )


@pytest.mark.parametrize(
    ("email", "auth0_id"),
    [
        ("e2e@example.test", ""),
        ("e2e@example.test", "  "),
        ("", "auth0|e2e-3"),
        ("sin-arroba", "x"),
    ],
)
async def test_refuses_an_empty_email_or_auth0_id(
    db: AsyncSession, email: str, auth0_id: str
) -> None:
    # Un secreto de CI sin definir llega como cadena vacía.
    with pytest.raises(ValueError, match="auth0_id"):
        await seed_e2e_user(
            db, email=email, auth0_id=auth0_id, role=UserRole.MEDICO, full_name="E2E Test User"
        )
