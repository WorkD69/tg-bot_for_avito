"""Smoke tests for database engine and scheduler configuration.

Root cause this prevents:
  APScheduler SQLAlchemyJobStore was initialized with SQLAlchemyJobStore(url=...)
  which creates an internal engine WITHOUT pool_pre_ping. After a DB restart,
  the stale psycopg2 connection in the jobstore causes:
      psycopg2.OperationalError: server closed the connection unexpectedly
  on scheduler.add_job() inside start_auction(), crashing order creation.

Tests:
  A. Async engine has pool_pre_ping=True and pool_recycle set
  B. scheduler/setup.py code structure uses engine= (not url=) with pool_pre_ping
  C. start_auction() gracefully handles scheduler.add_job() failures
"""
import sys
from unittest.mock import AsyncMock, MagicMock, patch


# ── A. Async engine config ────────────────────────────────────────────────────

class TestAsyncEngineConfig:
    """Async SQLAlchemy engine must have pool_pre_ping and pool_recycle."""

    def test_pool_pre_ping_enabled(self):
        """pool_pre_ping=True prevents stale connection errors after DB restarts."""
        from app.db.engine import engine
        assert engine.pool._pre_ping is True, (
            "Async engine must have pool_pre_ping=True. "
            "Without it, stale connections cause OperationalError on first query after DB restart."
        )

    def test_pool_recycle_set(self):
        """pool_recycle guards against OS-level TCP keepalive timeout."""
        from app.db.engine import engine
        recycle = engine.pool._recycle
        assert recycle > 0, (
            f"pool_recycle must be a positive value (got {recycle}). "
            "Set pool_recycle=1800 to discard connections older than 30 min."
        )
        assert 60 <= recycle <= 86400, f"pool_recycle={recycle} looks unreasonable"

    def test_session_factory_bound_to_configured_engine(self):
        """AsyncSessionFactory must be bound to the configured engine."""
        from app.db.engine import AsyncSessionFactory, engine
        # async_sessionmaker stores bind in kw
        assert AsyncSessionFactory.kw.get("bind") is not None


# ── B. Scheduler setup code structure ─────────────────────────────────────────

class TestSchedulerSetupStructure:
    """Verify scheduler/setup.py is structured correctly.

    Tests read the source file as text to avoid importing psycopg2
    (which is docker-only). The source structure test is the canonical
    regression guard: if someone reverts the fix, these tests fail immediately.
    """

    def _read_setup_source(self) -> str:
        import pathlib
        return pathlib.Path("app/scheduler/setup.py").read_text(encoding="utf-8")

    def test_uses_explicit_create_engine_not_url_string(self):
        """setup.py must use create_engine() + engine= to SQLAlchemyJobStore.

        SQLAlchemyJobStore(url=...) creates an internal engine without pool_pre_ping.
        SQLAlchemyJobStore(engine=explicit_engine) uses our configured engine.
        """
        source = self._read_setup_source()
        assert "create_engine(" in source, \
            "setup.py must call create_engine() to build an explicit engine"
        assert "SQLAlchemyJobStore(url=" not in source, \
            "SQLAlchemyJobStore must use engine=, NOT url=. " \
            "url= creates an internal engine without pool_pre_ping — the bug we fixed."

    def test_jobstore_engine_has_pool_pre_ping(self):
        """The jobstore engine must be created with pool_pre_ping=True."""
        source = self._read_setup_source()
        assert "pool_pre_ping=True" in source, \
            "setup.py must set pool_pre_ping=True on the jobstore engine. " \
            "Without it, stale psycopg2 connections crash start_auction()."

    def test_jobstore_engine_has_pool_recycle(self):
        """The jobstore engine must be created with pool_recycle."""
        source = self._read_setup_source()
        assert "pool_recycle" in source, \
            "setup.py must set pool_recycle on the jobstore engine."

    def test_jobstore_passes_engine_kwarg(self):
        """SQLAlchemyJobStore must receive engine= keyword argument."""
        source = self._read_setup_source()
        assert "SQLAlchemyJobStore(engine=" in source, \
            "SQLAlchemyJobStore must be called with engine= (not url=)."

    def test_setup_exposes_jobstore_engine_for_inspectability(self):
        """setup.py must define _jobstore_engine so tests and monitoring can inspect it."""
        source = self._read_setup_source()
        assert "_jobstore_engine" in source, \
            "setup.py must expose _jobstore_engine as a module-level name."


# ── C. start_auction scheduler resilience ─────────────────────────────────────

class TestStartAuctionSchedulerResilience:
    """start_auction() must survive scheduler.add_job() failures gracefully.

    Belt-and-suspenders: even with pool_pre_ping, external systems can fail.
    A scheduler write failure must NOT crash the order creation.
    """

    def _run_with_failing_scheduler(self, order_id=999):
        """Helper: run start_auction with scheduler.add_job() failing.

        Patches sys.modules to avoid psycopg2 dependency in app.scheduler.setup.
        Returns the mock bot for assertion.
        """
        import asyncio
        from datetime import datetime, timedelta, timezone

        mock_scheduler_module = MagicMock()
        mock_scheduler = MagicMock()
        mock_scheduler.add_job.side_effect = Exception(
            "psycopg2.OperationalError: server closed the connection unexpectedly"
        )
        mock_scheduler_module.scheduler = mock_scheduler

        results = {}

        async def _inner():
            with patch.dict(sys.modules, {"app.scheduler.setup": mock_scheduler_module}):
                # Force re-import to pick up the patched scheduler module
                auction_mod_key = "app.services.auction_service"
                saved = sys.modules.pop(auction_mod_key, None)
                try:
                    from app.services.auction_service import AuctionService

                    mock_bot = MagicMock()
                    send_call = AsyncMock(return_value=MagicMock())
                    mock_bot.send_message = send_call

                    mock_order = MagicMock()
                    mock_order.id = order_id
                    mock_order.auction_end_at = datetime.now(timezone.utc) + timedelta(hours=2)

                    service = AuctionService(
                        session=MagicMock(), bot=mock_bot, deferred=[]
                    )
                    await service.start_auction(mock_order)

                    results["send_call"] = send_call
                    results["raised"] = None
                except Exception as e:
                    results["raised"] = e
                finally:
                    # Restore original module if it existed
                    sys.modules.pop(auction_mod_key, None)
                    if saved is not None:
                        sys.modules[auction_mod_key] = saved

        asyncio.run(_inner())
        return results

    def test_scheduler_failure_does_not_propagate(self):
        """If scheduler.add_job() raises, start_auction must NOT propagate the exception."""
        results = self._run_with_failing_scheduler()
        assert results.get("raised") is None, (
            f"start_auction must not propagate scheduler errors to caller, "
            f"but raised: {type(results['raised']).__name__}: {results['raised']}"
        )

    def test_group_notification_sent_even_on_scheduler_failure(self):
        """Even if scheduler.add_job() fails, bot.send_message to group must have fired."""
        results = self._run_with_failing_scheduler()
        send_call = results.get("send_call")
        assert send_call is not None and send_call.called, \
            "Group notification (bot.send_message) must be called even if scheduler fails"
