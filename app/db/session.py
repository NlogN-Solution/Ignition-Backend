from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from ..core.config import get_settings

settings = get_settings()

engine = create_async_engine(
    settings.async_database_url,
    echo=settings.DB_ECHO,
    pool_size=settings.DB_POOL_SIZE,
    pool_pre_ping=True,
)
session_factory = async_sessionmaker(engine, expire_on_commit=False)
