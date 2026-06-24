"""Crea o promueve un usuario admin en la base de datos.

El login pasa por Auth0 (ROPG), así que para poder iniciar sesión el usuario
debe existir en Auth0. De ahí los dos modos:

  • Promover (default): toma una cuenta que ya existe en la DB y le pone rol
    admin. Lo más simple para probar: logueate una vez con tu cuenta de Auth0
    (el backend la crea automáticamente como `medico`) y después corré esto.

  • Crear (--create): crea la cuenta en Auth0 (requiere credenciales de
    Management API en .env) y además la fila en la DB con rol admin y el
    `auth0_id` correcto, para que el login la encuentre.

Uso:
    # Promover un usuario existente a admin
    uv run python -m app.scripts.seed_admin --email medico@example.com

    # Crear un admin nuevo desde cero
    uv run python -m app.scripts.seed_admin \\
        --email admin@holter.ar --password 'Sup3rSecret!' --name 'Admin Root' --create
"""

from __future__ import annotations

import argparse
import asyncio

from app.core.auth0_client import Auth0Error, create_auth0_user
from app.db.models.user import UserRole
from app.db.session import async_session_factory
from app.modules.auth import auth_repository as repo


async def _run(email: str, name: str, password: str | None, create: bool) -> None:
    async with async_session_factory() as db:
        user = await repo.get_user_by_email(db, email)

        if user is not None:
            if user.role == UserRole.ADMIN and user.is_active:
                print(f"✓ {email} ya es admin (id={user.id}).")
                return
            user.role = UserRole.ADMIN
            user.is_active = True
            await db.commit()
            print(f"✓ {email} promovido a admin (id={user.id}).")
            return

        if not create:
            raise SystemExit(
                f"No existe un usuario con email {email}.\n"
                "Opciones:\n"
                "  • Logueate una vez con esa cuenta (se crea como medico) y reintentá; o\n"
                "  • Pasá --create --password '...' para crearla desde cero (también en Auth0)."
            )

        if not password:
            raise SystemExit("--create requiere --password.")

        try:
            auth0_id = await create_auth0_user(email, password, name)
        except Auth0Error as exc:
            raise SystemExit(f"Auth0 falló: {exc.code} — {exc.message}") from exc

        user = await repo.create_user(
            db, auth0_id=auth0_id, email=email, full_name=name, role=UserRole.ADMIN
        )
        await db.commit()
        print(f"✓ Admin creado: {email} (id={user.id}, auth0_id={auth0_id}).")


def main() -> None:
    parser = argparse.ArgumentParser(description="Crea o promueve un usuario admin.")
    parser.add_argument("--email", required=True, help="Email del usuario admin.")
    parser.add_argument(
        "--name", default=None, help="Nombre completo (default: parte local del email)."
    )
    parser.add_argument("--password", default=None, help="Contraseña — solo con --create.")
    parser.add_argument(
        "--create",
        action="store_true",
        help="Crear la cuenta (también en Auth0) si no existe en la DB.",
    )
    args = parser.parse_args()

    name: str = args.name or args.email.split("@")[0]
    asyncio.run(_run(args.email, name, args.password, args.create))


if __name__ == "__main__":
    main()
