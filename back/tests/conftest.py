import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession


@pytest_asyncio.fixture
async def db_session() -> AsyncSession:
    """Placeholder DB session fixture for future integration tests."""
    raise NotImplementedError("Implement db_session fixture when integration tests are added")
