from collections.abc import AsyncGenerator

from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from app.core.config import get_settings

_settings = get_settings()

_echo = _settings.DB_ECHO if _settings.DB_ECHO is not None else _settings.ENVIRONMENT == "dev"

engine: AsyncEngine = create_async_engine(
    str(_settings.DATABASE_URL),
    pool_size=_settings.DB_POOL_SIZE,
    max_overflow=_settings.DB_MAX_OVERFLOW,
    pool_pre_ping=True,
    echo=_echo,
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
