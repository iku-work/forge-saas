from collections.abc import AsyncGenerator

from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from app.core.config import get_settings

_settings = get_settings()

engine: AsyncEngine = create_async_engine(
    str(_settings.DATABASE_URL),
    pool_size=5,
    max_overflow=15,
    pool_pre_ping=True,
    echo=_settings.ENVIRONMENT == "dev",
)

async_session_factory: async_sessionmaker[AsyncSession] = async_sessionmaker(
    engine, expire_on_commit=False, class_=AsyncSession
)


async def get_session() -> AsyncGenerator[AsyncSession, None]:
    session = async_session_factory()
    try:
        yield session
    except Exception:
        await session.rollback()
        raise
    finally:
        await session.close()


async def dispose_engine() -> None:
    await engine.dispose()
