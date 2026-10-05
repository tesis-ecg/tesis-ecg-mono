"""Deja listo en la base al usuario de prueba de Playwright (`front/e2e`).

El login pasa por Auth0 (ROPG) y después busca la fila por `auth0_id`: con la cuenta
de Auth0 sola, `POST /auth/login` contesta 403 `USER_NOT_PROVISIONED`. Este script
crea (o repara) esa fila a partir del `sub` de una cuenta de Auth0 que ya existe. No
habla con Auth0, así que sirve igual en CI (base recién migrada) que en local.

Es idempotente: si la fila ya existe la reactiva y la vincula, sin tocar sus pacientes.
Solo corre en development/test: vincula una identidad de Auth0 a un usuario de la base,
y eso en preview/production lo hace únicamente la administración de usuarios.

Uso (desde `back/`):

    uv run python -m app.scripts.seed_e2e_user \\
        --email e2e@example.com --auth0-id 'auth0|64f0c0ffee'

El `sub` está en Auth0 → User Management → Users → <usuario> → `user_id`. Para que el
usuario tenga pacientes y estudios de demo, después:

    uv run python -m app.scripts.seed_demo --doctor-email e2e@example.com
"""

from __future__ import annotations

import argparse
import asyncio

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Environment, settings
from app.db.models.doctor import Doctor
from app.db.models.user import IdentityStatus, User, UserRole
from app.db.session import async_session_factory
from app.modules.auth import auth_repository as repo


async def seed_e2e_user(
    db: AsyncSession, *, email: str, auth0_id: str, role: UserRole, full_name: str
) -> User:
    """Crea o repara la fila activa de `email`, vinculada a `auth0_id`. No hace commit."""
    email = email.strip().lower()
    auth0_id = auth0_id.strip()
    # Un secreto sin definir llega acá como cadena vacía, y una fila con `auth0_id`
    # vacío no es de nadie: el login nunca la encontraría y el error aparecería
    # recién en el primer spec firmado.
    if "@" not in email or not auth0_id:
        raise ValueError("Hacen falta un email y el auth0_id (sub) de la cuenta de Auth0.")

    holder = await repo.get_user_by_auth0_id(db, auth0_id)
    if holder is not None and holder.email != email:
        raise ValueError(f"El auth0_id {auth0_id} ya está vinculado a {holder.email}.")

    # Sin filtrar `deleted_at`: el email es único también entre las filas borradas.
    user = await db.scalar(select(User).where(User.email == email))
    if user is None:
        user = await repo.create_user(
            db, auth0_id=auth0_id, email=email, full_name=full_name, role=role
        )
    else:
        user.auth0_id = auth0_id
        user.role = role
        user.is_active = True
        user.identity_status = IdentityStatus.ACTIVE
        user.pending_email = None
        user.deleted_at = None

    if role == UserRole.MEDICO:
        doctor = await db.scalar(select(Doctor).where(Doctor.user_id == user.id))
        if doctor is None:
            db.add(Doctor(user_id=user.id, specialty="Cardiología", license_number="MN E2E"))
        else:
            doctor.deleted_at = None
    await db.flush()
    return user


async def _run(email: str, auth0_id: str, role: UserRole, full_name: str) -> None:
    if settings.environment not in {Environment.DEVELOPMENT, Environment.TEST}:
        raise SystemExit("seed_e2e_user está permitido únicamente en development/test.")

    async with async_session_factory() as db:
        try:
            user = await seed_e2e_user(
                db, email=email, auth0_id=auth0_id, role=role, full_name=full_name
            )
        except ValueError as exc:
            raise SystemExit(str(exc)) from exc
        await db.commit()
    print(f"✓ Usuario e2e listo: {user.email} ({user.role.value}, id={user.id}).")


def main() -> None:
    parser = argparse.ArgumentParser(description="Prepara el usuario de prueba de Playwright.")
    parser.add_argument("--email", required=True, help="Email de la cuenta de Auth0.")
    parser.add_argument("--auth0-id", required=True, help="`user_id` (sub) de la cuenta de Auth0.")
    parser.add_argument(
        "--role",
        choices=[UserRole.MEDICO.value, UserRole.ADMIN.value],
        default=UserRole.MEDICO.value,
        help="Rol en el portal (default: medico, con perfil de médico).",
    )
    parser.add_argument("--name", default="E2E Test User", help="Nombre completo.")
    args = parser.parse_args()

    asyncio.run(_run(args.email, args.auth0_id, UserRole(args.role), args.name))


if __name__ == "__main__":
    main()
