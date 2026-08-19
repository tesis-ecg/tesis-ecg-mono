from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app.core.config import settings

engine_kwargs: dict[str, object] = {
    "echo": False,
    "pool_pre_ping": True,
    "connect_args": {
        "command_timeout": 15,
        "server_settings": {"application_name": "holter-api", "statement_timeout": "15000"},
    },
}
if settings.is_secure_environment:
    engine_kwargs["poolclass"] = NullPool

engine = create_async_engine(settings.database_url, **engine_kwargs)
async_session_factory = async_sessionmaker(engine, expire_on_commit=False)
