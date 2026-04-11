from apscheduler.jobstores.sqlalchemy import SQLAlchemyJobStore
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from sqlalchemy import create_engine

from app.config import settings

# Build an explicit sync engine for the APScheduler jobstore.
#
# Build an explicit sync engine and pass it via engine= keyword.
#
# The antipattern to avoid: passing url= directly to SQLAlchemyJobStore.
# That shortcut creates an internal engine with default pool settings (no pool_pre_ping),
# so stale psycopg2 connections are never detected. After a DB restart or Docker
# network hiccup the jobstore silently holds a dead connection, and the next call
# to scheduler.add_job() (from start_auction) raises:
#     psycopg2.OperationalError: server closed the connection unexpectedly
# This crashes the order-creation handler and the client sees a generic error.
#
# Fix: pass engine= with pool_pre_ping=True. SQLAlchemy then tests the connection
# before each checkout and reconnects automatically when it detects a stale one.
_jobstore_engine = create_engine(
    settings.sync_database_url,
    pool_pre_ping=True,   # test connection before checkout — auto-reconnects on stale
    pool_recycle=1800,    # discard connections older than 30 min (guards against TCP timeout)
)

scheduler = AsyncIOScheduler(
    jobstores={
        "default": SQLAlchemyJobStore(engine=_jobstore_engine)
    },
    # misfire_grace_time=3600: if the scheduler was down and a job is up to 1 hour
    # overdue, it still fires. Startup recovery (recover_overdue_auctions) handles
    # anything older than that.
    job_defaults={"misfire_grace_time": 3600},
    timezone="UTC",
)
