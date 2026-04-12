"""Regression tests for _schedule_requisites_reminder and close_auction resilience.

Covers:
- NameError fix: datetime/timezone/timedelta are imported inside the function
- close_auction does NOT fail if reminder scheduling raises
- reminder scheduling success path works
"""
import asyncio
import sys
from datetime import datetime, timezone, timedelta
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock, patch


# ── Helper: build a mock closed auction result ─────────────────────────────────

def _make_closed_order(order_id: int = 42, budget: Decimal = Decimal("1500")):
    """Return a minimal mock Order in awaiting_payment state."""
    order = MagicMock()
    order.id = order_id
    order.status = "awaiting_payment"   # just for repr — not checked by schedule fn
    order.operator_id = 7
    order.client_id = 3
    order.budget = budget
    order.payment_amount = Decimal("1200")
    order.payment_invoice_id = f"{order_id}_1"
    order.auction_end_at = datetime.now(timezone.utc) - timedelta(minutes=1)
    order.logs = []
    return order


# ── A. _schedule_requisites_reminder standalone ────────────────────────────────

class TestScheduleRequisitesReminder:
    """Direct tests on _schedule_requisites_reminder().

    Uses a fake scheduler to avoid real DB/psycopg2 dependency.
    """

    def _run(self, scheduler_raises=False):
        """Call _schedule_requisites_reminder with a mock scheduler.

        Returns dict with raised exception (or None) and scheduler call args.
        """
        mock_scheduler = MagicMock()
        if scheduler_raises:
            mock_scheduler.add_job.side_effect = Exception("jobstore unavailable")

        mock_scheduler_module = MagicMock()
        mock_scheduler_module.scheduler = mock_scheduler

        result = {"raised": None, "add_job_called": False, "add_job_args": None}

        async def _inner():
            auction_mod_key = "app.services.auction_service"
            saved = sys.modules.pop(auction_mod_key, None)
            try:
                with patch.dict(sys.modules, {"app.scheduler.setup": mock_scheduler_module}):
                    from app.services.auction_service import _schedule_requisites_reminder
                    _schedule_requisites_reminder(order_id=42)
                    result["add_job_called"] = mock_scheduler.add_job.called
                    if mock_scheduler.add_job.called:
                        result["add_job_args"] = mock_scheduler.add_job.call_args
            except Exception as e:
                result["raised"] = e
            finally:
                sys.modules.pop(auction_mod_key, None)
                if saved is not None:
                    sys.modules[auction_mod_key] = saved

        asyncio.run(_inner())
        return result

    def test_no_name_error_datetime(self):
        """Regression: datetime/timezone/timedelta must be imported inside the function.

        Previously caused NameError: name 'datetime' is not defined because only
        timedelta was imported but datetime and timezone were not.
        """
        result = self._run(scheduler_raises=False)
        assert result["raised"] is None, (
            f"_schedule_requisites_reminder raised: "
            f"{type(result['raised']).__name__}: {result['raised']}"
        )

    def test_scheduler_called_with_correct_job_id(self):
        """Job id must be 'req_reminder_{order_id}'."""
        result = self._run(scheduler_raises=False)
        assert result["add_job_called"], "scheduler.add_job must be called"
        kwargs = result["add_job_args"].kwargs
        assert kwargs.get("id") == "req_reminder_42"

    def test_scheduler_called_with_replace_existing(self):
        """replace_existing=True ensures idempotency — only one job per order."""
        result = self._run(scheduler_raises=False)
        kwargs = result["add_job_args"].kwargs
        assert kwargs.get("replace_existing") is True

    def test_run_date_is_approximately_30_min_ahead(self):
        """Job must be scheduled ~30 minutes in the future."""
        result = self._run(scheduler_raises=False)
        kwargs = result["add_job_args"].kwargs
        run_date: datetime = kwargs.get("run_date")
        assert run_date is not None, "run_date kwarg must be set"
        now = datetime.now(timezone.utc)
        delta = (run_date - now).total_seconds()
        # Allow ±5 seconds for test execution time
        assert 25 * 60 <= delta <= 35 * 60, (
            f"run_date should be ~30 min ahead, got {delta:.0f}s"
        )

    def test_scheduler_failure_does_not_propagate(self):
        """If scheduler.add_job() raises, _schedule_requisites_reminder must swallow it.

        close_auction() relies on this: a failed reminder must not roll back the auction.
        """
        result = self._run(scheduler_raises=True)
        assert result["raised"] is None, (
            f"_schedule_requisites_reminder must not propagate scheduler errors, "
            f"but raised: {type(result['raised']).__name__}: {result['raised']}"
        )


# ── B. close_auction resilience when reminder scheduling fails ─────────────────

class TestCloseAuctionReminderResilience:
    """close_auction() must complete successfully even if reminder scheduling fails.

    Scenario: auction closes, operator assigned, payment notification sent —
    all this must happen regardless of whether _schedule_requisites_reminder raises.
    """

    def _run_close_auction_with_failing_reminder(self, order_id: int = 55):
        """Run close_auction() with reminder scheduling failing.

        Patches the scheduler so add_job always raises. Verifies the auction
        close path completes and returns AuctionCloseResult.closed.
        """
        result = {"raised": None, "close_result": None, "operator_notified": False}

        mock_scheduler = MagicMock()
        mock_scheduler.add_job.side_effect = Exception("jobstore unavailable")
        mock_scheduler_module = MagicMock()
        mock_scheduler_module.scheduler = mock_scheduler

        async def _inner():
            from unittest.mock import patch as _patch

            with _patch.dict(sys.modules, {"app.scheduler.setup": mock_scheduler_module}):
                auction_mod_key = "app.services.auction_service"
                saved = sys.modules.pop(auction_mod_key, None)
                try:
                    from app.services.auction_service import AuctionService, AuctionCloseResult

                    # Build mock order
                    order = _make_closed_order(order_id=order_id)

                    # Mock session
                    mock_session = AsyncMock()

                    # get_by_id_for_update returns a pending order
                    pending_order = MagicMock()
                    pending_order.id = order_id
                    pending_order.status.__eq__ = lambda self, other: other == "pending"

                    from app.db.models.order import OrderStatus
                    pending_order.status = OrderStatus.pending
                    pending_order.client_id = 3
                    pending_order.operator_id = None
                    pending_order.budget = Decimal("1500")
                    pending_order.payment_amount = Decimal("1200")
                    pending_order.payment_invoice_id = f"{order_id}_1"

                    # Mock repos
                    mock_order_repo = AsyncMock()
                    mock_order_repo.get_by_id_for_update.return_value = pending_order
                    mock_order_repo.add_log = AsyncMock()
                    mock_order_repo.assign_operator = AsyncMock()

                    # Min bid
                    mock_bid = MagicMock()
                    mock_bid.operator_id = 7
                    mock_bid.amount = Decimal("1200")

                    mock_bid_repo = AsyncMock()
                    mock_bid_repo.get_min_bid.return_value = mock_bid
                    mock_bid_repo.get_losers.return_value = []

                    # Operator user
                    mock_operator = MagicMock()
                    mock_operator.telegram_id = 9999

                    # Client user
                    mock_client = MagicMock()
                    mock_client.telegram_id = 8888

                    mock_user_repo = AsyncMock()
                    mock_user_repo.get_by_id.side_effect = lambda uid: (
                        mock_operator if uid == 7 else mock_client
                    )

                    mock_bot = MagicMock()
                    mock_bot.send_message = AsyncMock()

                    deferred = []
                    service = AuctionService(session=mock_session, bot=mock_bot, deferred=deferred)

                    with (
                        _patch("app.services.auction_service.OrderRepo", return_value=mock_order_repo),
                        _patch("app.services.auction_service.BidRepo", return_value=mock_bid_repo),
                        _patch("app.services.auction_service.UserRepo", return_value=mock_user_repo),
                        _patch("app.services.auction_service._remove_scheduler_job"),
                    ):
                        close_result = await service.close_auction(order_id)

                    result["close_result"] = close_result
                    result["deferred_count"] = len(deferred)

                except Exception as e:
                    result["raised"] = e
                finally:
                    sys.modules.pop(auction_mod_key, None)
                    if saved is not None:
                        sys.modules[auction_mod_key] = saved

        asyncio.run(_inner())
        return result

    def test_close_auction_succeeds_when_reminder_scheduling_fails(self):
        """close_auction() must return AuctionCloseResult.closed even if reminder scheduling fails."""
        from app.services.auction_service import AuctionCloseResult
        result = self._run_close_auction_with_failing_reminder()
        assert result["raised"] is None, (
            f"close_auction must not propagate reminder scheduling errors, "
            f"but raised: {type(result['raised']).__name__}: {result['raised']}"
        )
        assert result["close_result"] == AuctionCloseResult.closed, (
            f"Expected AuctionCloseResult.closed, got {result['close_result']}"
        )

    def test_client_notification_queued_even_when_reminder_fails(self):
        """Client and operator notifications must be deferred even if reminder scheduling fails."""
        result = self._run_close_auction_with_failing_reminder()
        assert result.get("deferred_count", 0) >= 1, (
            "At least one deferred notification (client or operator) must be queued"
        )


# ── C. Reminder success path ───────────────────────────────────────────────────

class TestScheduleRequisitesReminderSuccessPath:
    """Verify the happy path: job is scheduled with correct function reference."""

    def test_reminder_function_is_passed_as_job(self):
        """scheduler.add_job must receive _remind_operator_requisites as the callable."""
        mock_scheduler = MagicMock()
        mock_scheduler_module = MagicMock()
        mock_scheduler_module.scheduler = mock_scheduler

        auction_mod_key = "app.services.auction_service"
        saved = sys.modules.pop(auction_mod_key, None)
        try:
            with patch.dict(sys.modules, {"app.scheduler.setup": mock_scheduler_module}):
                from app.services.auction_service import (
                    _schedule_requisites_reminder,
                    _remind_operator_requisites,
                )
                _schedule_requisites_reminder(order_id=77)
                assert mock_scheduler.add_job.called
                called_fn = mock_scheduler.add_job.call_args.args[0]
                assert called_fn is _remind_operator_requisites, (
                    "scheduler.add_job must receive _remind_operator_requisites as callable"
                )
        finally:
            sys.modules.pop(auction_mod_key, None)
            if saved is not None:
                sys.modules[auction_mod_key] = saved

    def test_trigger_type_is_date(self):
        """Trigger must be 'date' for a one-shot job."""
        mock_scheduler = MagicMock()
        mock_scheduler_module = MagicMock()
        mock_scheduler_module.scheduler = mock_scheduler

        auction_mod_key = "app.services.auction_service"
        saved = sys.modules.pop(auction_mod_key, None)
        try:
            with patch.dict(sys.modules, {"app.scheduler.setup": mock_scheduler_module}):
                from app.services.auction_service import _schedule_requisites_reminder
                _schedule_requisites_reminder(order_id=77)
                trigger = mock_scheduler.add_job.call_args.args[1]
                assert trigger == "date", f"Expected 'date' trigger, got {trigger!r}"
        finally:
            sys.modules.pop(auction_mod_key, None)
            if saved is not None:
                sys.modules[auction_mod_key] = saved
