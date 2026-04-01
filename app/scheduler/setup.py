from apscheduler.jobstores.sqlalchemy import SQLAlchemyJobStore
from apscheduler.schedulers.asyncio import AsyncIOScheduler

from app.config import settings

scheduler = AsyncIOScheduler(
    jobstores={
        "default": SQLAlchemyJobStore(url=settings.sync_database_url)
    },
    # misfire_grace_time=3600: if the scheduler was down and a job is up to 1 hour
    # overdue, it still fires. Startup recovery (recover_overdue_auctions) handles
    # anything older than that.
    job_defaults={"misfire_grace_time": 3600},
    timezone="UTC",
)
