"""`alembic upgrade head` para el build de Vercel.

Previews y producción apuntan a la misma base, así que solo producción migra
(`VERCEL_ENV=production`). Si una preview migrara, la base quedaría en una
revisión que `main` no tiene en `alembic/versions` y el deploy de producción
cortaría con "Can't locate revision identified by ...". Fuera de Vercel (sin
`VERCEL_ENV`) se migra siempre.

Si aun así la base está en una revisión que este código no conoce (la migró
otra rama antes de esta regla), se saltean las migraciones con un aviso en vez
de romper el build.

    python -m app.scripts.migrate
"""

import asyncio
import os
import sys
from pathlib import Path

from alembic.config import Config
from alembic.script import ScriptDirectory
from sqlalchemy import text
from sqlalchemy.exc import ProgrammingError
from sqlalchemy.ext.asyncio import create_async_engine

from alembic import command
from app.core.config import settings

ALEMBIC_INI = Path(__file__).resolve().parents[2] / "alembic.ini"


async def _current_revisions() -> set[str]:
    engine = create_async_engine(settings.database_url)
    try:
        async with engine.connect() as conn:
            rows = await conn.execute(text("SELECT version_num FROM alembic_version"))
            return {row[0] for row in rows}
    except ProgrammingError:
        # Base vacía: todavía no existe `alembic_version`.
        return set()
    finally:
        await engine.dispose()


def main() -> int:
    vercel_env = os.environ.get("VERCEL_ENV")
    if vercel_env is not None and vercel_env != "production":
        print(f"VERCEL_ENV={vercel_env}: la base es la de producción, no se migra.")
        return 0

    cfg = Config(str(ALEMBIC_INI))
    script = ScriptDirectory.from_config(cfg)
    known = {rev.revision for rev in script.walk_revisions()}

    unknown = asyncio.run(_current_revisions()) - known
    if unknown:
        print(
            f"WARNING: la base está en {', '.join(sorted(unknown))}, que no existe en este "
            "código (la migró otra rama). Se saltean las migraciones.",
            file=sys.stderr,
        )
        return 0

    command.upgrade(cfg, "head")
    return 0


if __name__ == "__main__":
    sys.exit(main())
