"""Root conftest.py — sets environment variables BEFORE any app module is imported.

pydantic-settings reads from os.environ first (higher priority than .env file),
so setting these here ensures tests don't need a real .env file and don't accidentally
talk to production services.
"""
import os

# ── Telegram ──────────────────────────────────────────────────────────────────
os.environ.setdefault("BOT_TOKEN", "1234567890:AAtest_token_for_pytest_only")
os.environ.setdefault("WEBHOOK_BASE_URL", "https://test.example.com")
os.environ.setdefault("WEBHOOK_SECRET", "test_webhook_secret_32chars_padded")

# ── Database ──────────────────────────────────────────────────────────────────
# Override with TEST_DATABASE_URL env var to point at a real test DB.
# Default assumes docker-compose db service is running and tg_bot_test DB exists.
os.environ.setdefault(
    "DATABASE_URL",
    os.environ.get(
        "TEST_DATABASE_URL",
        "postgresql+asyncpg://postgres:postgres@localhost:5432/tg_bot_test",
    ),
)

# ── Bot roles ─────────────────────────────────────────────────────────────────
os.environ.setdefault("OPERATOR_GROUP_ID", "-1001234567890")
os.environ.setdefault("ADMIN_TELEGRAM_ID", "111111111")

# ── Robokassa ─────────────────────────────────────────────────────────────────
os.environ.setdefault("ROBOKASSA_LOGIN", "TestShop")
os.environ.setdefault("ROBOKASSA_PASS1", "test_pass1_secret")
os.environ.setdefault("ROBOKASSA_PASS2", "test_pass2_secret")
os.environ.setdefault("ROBOKASSA_IS_TEST", "true")

# ── Redis ─────────────────────────────────────────────────────────────────────
os.environ.setdefault("REDIS_URL", "redis://localhost:6379/1")
