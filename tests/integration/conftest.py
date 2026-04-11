"""Integration test fixtures.

Requires a running PostgreSQL instance.
Set TEST_DATABASE_URL env var or start docker-compose db service:

    docker-compose up -d db

Default URL: postgresql+asyncpg://postgres:postgres@localhost:5432/tg_bot_test
Create the DB once:
    docker-compose exec db psql -U postgres -c "CREATE DATABASE tg_bot_test;"

Isolation strategy: module-scoped engine (tables created once), savepoint-based
per-test isolation — each test runs inside a rolled-back transaction so tests
are fully independent without truncating tables on every run.
"""
import os
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock

import pytest
import pytest_asyncio
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

TEST_DB_URL = os.environ.get(
    "TEST_DATABASE_URL",
    "postgresql+asyncpg://postgres:postgres@localhost:5432/tg_bot_test",
)


@pytest_asyncio.fixture(scope="module", loop_scope="module")
async def db_engine():
    """Create all tables once per test module, drop them at module teardown.

    loop_scope="module" must match the loop scope of dependent fixtures and tests
    (see pytestmark in each test file) to avoid asyncpg "Future attached to a
    different loop" RuntimeError in pytest-asyncio >= 0.23.
    """
    # Import here so env vars from conftest.py root are already set
    from app.db.base import Base
    import app.db.models  # noqa: F401 — registers all models with Base.metadata

    engine = create_async_engine(TEST_DB_URL, echo=False, pool_pre_ping=True)
    try:
        async with engine.begin() as conn:
            # Always start fresh — drop any leftovers from a crashed previous run
            await conn.run_sync(Base.metadata.drop_all)
            await conn.run_sync(Base.metadata.create_all)
    except Exception as exc:
        await engine.dispose()
        pytest.skip(
            f"Integration DB not available ({TEST_DB_URL}): {exc}\n"
            "Run: docker-compose up -d db && "
            "docker exec tg-bot_for_avito-db-1 psql -U botuser -d botdb "
            "-c 'CREATE DATABASE tg_bot_test;'"
        )
        return

    yield engine

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
    await engine.dispose()


@pytest_asyncio.fixture(loop_scope="module")
async def session(db_engine):
    """Per-test async session backed by a rolled-back transaction.

    Uses SQLAlchemy's join_transaction_mode="create_savepoint":
    - session.commit() inside a handler → releases savepoint (data visible in session)
    - fixture teardown → outer transaction ROLLBACK → all test data gone
    This gives full isolation without truncating tables between tests.
    """
    async with db_engine.connect() as conn:
        await conn.begin()
        async with AsyncSession(
            bind=conn,
            join_transaction_mode="create_savepoint",
            expire_on_commit=False,
        ) as sess:
            yield sess
        await conn.rollback()


@pytest.fixture
def mock_bot():
    """Mock aiogram Bot — captures send_message calls without any network I/O."""
    bot = MagicMock()
    bot.send_message = AsyncMock(return_value=MagicMock(message_id=999))
    bot.edit_message_text = AsyncMock()
    return bot


# ── DB helper factories ───────────────────────────────────────────────────────

async def make_user(session: AsyncSession, telegram_id: int, name: str, role=None):
    """Insert a User row and return it."""
    from app.db.models.user import User, UserRole
    user = User(
        telegram_id=telegram_id,
        full_name=name,
        role=role or UserRole.client,
    )
    session.add(user)
    await session.flush()
    return user


async def make_order(
    session: AsyncSession,
    client_id: int,
    budget: Decimal = Decimal("2000"),
    status=None,
):
    """Insert an Order row in pending status and return it."""
    from datetime import datetime, timedelta, timezone
    from app.db.models.order import Order, OrderStatus

    order = Order(
        client_id=client_id,
        status=status or OrderStatus.pending,
        budget=budget,
        auction_end_at=datetime.now(timezone.utc) + timedelta(hours=2),
        payment_revision=0,
    )
    session.add(order)
    await session.flush()
    return order
