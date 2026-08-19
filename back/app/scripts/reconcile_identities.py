"""Reintenta sincronizaciones Auth0 pendientes sin adivinar estados ambiguos."""

import asyncio

from sqlalchemy import select

from app.core.auth0_client import Auth0Error, block_auth0_user, update_auth0_user_email
from app.db.models.user import IdentityStatus, User
from app.db.session import async_session_factory


async def _run() -> None:
    async with async_session_factory() as db:
        users = list(
            (
                await db.scalars(
                    select(User)
                    .where(User.identity_status != IdentityStatus.ACTIVE)
                    .order_by(User.created_at, User.id)
                )
            ).all()
        )
        for user in users:
            if user.auth0_id is None:
                print(f"MANUAL {user.id}: falta auth0_id; reintentá el alta desde Usuarios.")
                continue
            try:
                if user.pending_email:
                    await update_auth0_user_email(user.auth0_id, user.pending_email)
                    user.email = user.pending_email
                    user.pending_email = None
                    user.identity_status = IdentityStatus.ACTIVE
                elif not user.is_active or user.deleted_at is not None:
                    await block_auth0_user(user.auth0_id)
                    user.identity_status = IdentityStatus.ACTIVE
                else:
                    print(f"MANUAL {user.id}: estado ambiguo; no se modificó Auth0.")
                    continue
                await db.commit()
                print(f"OK {user.id}")
            except Auth0Error as exc:
                await db.rollback()
                print(f"ERROR {user.id}: {exc.code} — {exc.message}")


def main() -> None:
    asyncio.run(_run())


if __name__ == "__main__":
    main()
