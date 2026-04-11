from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.config import settings

engine = create_async_engine(
    settings.database_url,
    echo=False,
    # pool_pre_ping: test connection liveness before each checkout.
    # Prevents "server closed the connection unexpectedly" after DB restarts or
    # Docker network drops. Adds one lightweight SELECT 1 per checkout — negligible cost.
    pool_pre_ping=True,
    # pool_recycle: discard connections older than 30 minutes.
    # Guards against OS-level TCP keepalive timeouts and PostgreSQL idle-timeout policies.
    pool_recycle=1800,
)

AsyncSessionFactory: async_sessionmaker[AsyncSession] = async_sessionmaker(
    bind=engine,
    class_=AsyncSession,
    expire_on_commit=False,
)
