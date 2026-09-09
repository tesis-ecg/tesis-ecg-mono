from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app.core.config import settings

#: Cuánto espera una sentencia por un lock de fila antes de rendirse. Es
#: deliberadamente mucho más corto que `statement_timeout`: la contención sobre
#: la fila del estudio es una condición esperada y recuperable (el equipo
#: reintenta), así que tiene que fallar rápido y con un error propio en vez de
#: colgar la request 15 s y caer al handler genérico como un 500.
LOCK_TIMEOUT_MS = 3_000

engine_kwargs: dict[str, object] = {
    "echo": False,
    "connect_args": {
        "command_timeout": 15,
        "server_settings": {
            "application_name": "holter-api",
            "statement_timeout": "15000",
            "lock_timeout": str(LOCK_TIMEOUT_MS),
        },
    },
}
if settings.is_secure_environment:
    # `NullPool` abre una conexión nueva por request. `pool_pre_ping` sobre eso
    # es un round trip regalado: hace `SELECT 1` para comprobar que una conexión
    # recién creada sigue viva, cosa que no puede no estar. Contra una base
    # remota son decenas de ms en la ruta del ACK del chaleco, que es donde más
    # duelen (informe de ingesta del 8/9/2026, hallazgo 2).
    engine_kwargs["poolclass"] = NullPool
else:
    engine_kwargs["pool_pre_ping"] = True

engine = create_async_engine(settings.database_url, **engine_kwargs)
async_session_factory = async_sessionmaker(engine, expire_on_commit=False)
