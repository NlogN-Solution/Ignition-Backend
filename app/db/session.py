from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from ..core.config import get_settings

settings = get_settings()

# asyncpg needs SSL forced on via connect_args, not the `sslmode` query
# param (see Settings._build_database_url) — only when async_database_url
# actually resolves to DATABASE_URL. ENVIRONMENT=test ignores DATABASE_URL
# (test hermeticity — see _build_database_url) and local dev's DB_HOST
# connection has no TLS listener either.
_connect_args = {"ssl": "require"} if settings.DATABASE_URL and settings.ENVIRONMENT != "test" else {}

engine = create_async_engine(
    settings.async_database_url,
    echo=settings.DB_ECHO,
    pool_size=settings.DB_POOL_SIZE,
    pool_pre_ping=True,
    connect_args=_connect_args,
)
session_factory = async_sessionmaker(engine, expire_on_commit=False)
