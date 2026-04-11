"""Integration tests for AuctionService.

Covers the auction close logic that is hardest to reason about statically:
  - No bids → order cancelled by system
  - Lowest bid wins operator assignment
  - Tie-break: earliest created_at wins (same amount, different time)
  - Idempotent close: calling twice on a non-pending order is a no-op
  - place_bid with amount == budget triggers immediate auto-assign

Requires a running PostgreSQL — see TESTING.md.
"""
from datetime import datetime, timezone
from decimal import Decimal
from unittest.mock import patch

import pytest

from app.db.models.order import OrderStatus
from app.db.models.user import UserRole
from app.repositories.bid_repo import BidRepo
from app.repositories.order_repo import OrderRepo
from app.services.auction_service import AuctionCloseResult, AuctionService

from tests.integration.conftest import make_order, make_user


def _service(session, mock_bot) -> AuctionService:
    """Build an AuctionService with deferred notifications collected into a list."""
    return AuctionService(session=session, bot=mock_bot, deferred=[])


# ── No bids ───────────────────────────────────────────────────────────────────

class TestCloseAuctionNoBids:

    async def test_result_is_cancelled_no_bids(self, session, mock_bot):
        client = await make_user(session, 10001, "Client A")
        order = await make_order(session, client.id)
        await session.commit()

        with patch("app.services.auction_service._remove_scheduler_job"):
            result = await _service(session, mock_bot).close_auction(order.id)
            await session.commit()

        assert result == AuctionCloseResult.cancelled_no_bids

    async def test_order_status_becomes_cancelled(self, session, mock_bot):
        client = await make_user(session, 10002, "Client B")
        order = await make_order(session, client.id)
        await session.commit()

        with patch("app.services.auction_service._remove_scheduler_job"):
            await _service(session, mock_bot).close_auction(order.id)
            await session.commit()

        fresh = await OrderRepo(session).get_by_id(order.id)
        assert fresh.status == OrderStatus.cancelled

    async def test_cancelled_by_is_system(self, session, mock_bot):
        client = await make_user(session, 10003, "Client C")
        order = await make_order(session, client.id)
        await session.commit()

        with patch("app.services.auction_service._remove_scheduler_job"):
            await _service(session, mock_bot).close_auction(order.id)
            await session.commit()

        fresh = await OrderRepo(session).get_by_id(order.id)
        assert fresh.cancelled_by == "system"


# ── With bids ─────────────────────────────────────────────────────────────────

class TestCloseAuctionWithBids:

    async def test_lowest_bid_wins(self, session, mock_bot):
        client = await make_user(session, 10010, "Client D")
        op1 = await make_user(session, 10011, "Op High", UserRole.operator)
        op2 = await make_user(session, 10012, "Op Low", UserRole.operator)
        order = await make_order(session, client.id, budget=Decimal("2000"))
        await session.commit()

        bid_repo = BidRepo(session)
        await bid_repo.upsert(order.id, op1.id, Decimal("1800"))
        await bid_repo.upsert(order.id, op2.id, Decimal("1500"))  # lower
        await session.commit()

        with patch("app.services.auction_service._remove_scheduler_job"):
            result = await _service(session, mock_bot).close_auction(order.id)
            await session.commit()

        assert result == AuctionCloseResult.closed
        fresh = await OrderRepo(session).get_by_id(order.id)
        assert fresh.status == OrderStatus.awaiting_payment
        assert fresh.operator_id == op2.id          # op2 had lowest bid
        assert fresh.payment_amount == Decimal("1500")

    async def test_payment_revision_incremented(self, session, mock_bot):
        """assign_operator must bump payment_revision so old Robokassa links expire."""
        client = await make_user(session, 10013, "Client E")
        op1 = await make_user(session, 10014, "Op E", UserRole.operator)
        order = await make_order(session, client.id)
        initial_revision = order.payment_revision  # 0
        await session.commit()

        await BidRepo(session).upsert(order.id, op1.id, Decimal("1500"))
        await session.commit()

        with patch("app.services.auction_service._remove_scheduler_job"):
            await _service(session, mock_bot).close_auction(order.id)
            await session.commit()

        fresh = await OrderRepo(session).get_by_id(order.id)
        assert fresh.payment_revision > initial_revision

    async def test_payment_invoice_id_versioned(self, session, mock_bot):
        """invoice_id format must be '{order_id}_{revision}'."""
        client = await make_user(session, 10015, "Client F")
        op1 = await make_user(session, 10016, "Op F", UserRole.operator)
        order = await make_order(session, client.id)
        await session.commit()

        await BidRepo(session).upsert(order.id, op1.id, Decimal("1500"))
        await session.commit()

        with patch("app.services.auction_service._remove_scheduler_job"):
            await _service(session, mock_bot).close_auction(order.id)
            await session.commit()

        fresh = await OrderRepo(session).get_by_id(order.id)
        expected_invoice_id = f"{order.id}_{fresh.payment_revision}"
        assert fresh.payment_invoice_id == expected_invoice_id

    async def test_tie_break_earliest_bid_wins(self, session, mock_bot):
        """When two operators bid the same amount, the earlier bid wins.

        We manually set created_at on Bid objects to control ordering precisely,
        since within one DB transaction func.now() returns the same timestamp.
        """
        from app.db.models.bid import Bid

        client = await make_user(session, 10020, "Client G")
        op1 = await make_user(session, 10021, "Op First", UserRole.operator)
        op2 = await make_user(session, 10022, "Op Second", UserRole.operator)
        order = await make_order(session, client.id, budget=Decimal("2000"))
        await session.commit()

        # Insert bids with explicit created_at — op1 is earlier
        bid1 = Bid(order_id=order.id, operator_id=op1.id, amount=Decimal("1700"))
        bid1.created_at = datetime(2024, 1, 1, 10, 0, 0, tzinfo=timezone.utc)
        bid2 = Bid(order_id=order.id, operator_id=op2.id, amount=Decimal("1700"))
        bid2.created_at = datetime(2024, 1, 1, 10, 0, 1, tzinfo=timezone.utc)
        session.add_all([bid1, bid2])
        await session.commit()

        with patch("app.services.auction_service._remove_scheduler_job"):
            result = await _service(session, mock_bot).close_auction(order.id)
            await session.commit()

        assert result == AuctionCloseResult.closed
        fresh = await OrderRepo(session).get_by_id(order.id)
        assert fresh.operator_id == op1.id  # earliest bid wins

    async def test_single_bid_wins(self, session, mock_bot):
        client = await make_user(session, 10030, "Client H")
        op1 = await make_user(session, 10031, "Op H", UserRole.operator)
        order = await make_order(session, client.id)
        await session.commit()

        await BidRepo(session).upsert(order.id, op1.id, Decimal("1800"))
        await session.commit()

        with patch("app.services.auction_service._remove_scheduler_job"):
            result = await _service(session, mock_bot).close_auction(order.id)
            await session.commit()

        assert result == AuctionCloseResult.closed
        fresh = await OrderRepo(session).get_by_id(order.id)
        assert fresh.operator_id == op1.id


# ── Idempotency ───────────────────────────────────────────────────────────────

class TestCloseAuctionIdempotency:

    async def test_already_closed_returns_already_closed(self, session, mock_bot):
        """Second call on a non-pending order must return already_closed — no side effects."""
        client = await make_user(session, 10040, "Client I")
        op1 = await make_user(session, 10041, "Op I", UserRole.operator)
        order = await make_order(session, client.id)
        await session.commit()

        await BidRepo(session).upsert(order.id, op1.id, Decimal("1500"))
        await session.commit()

        svc = _service(session, mock_bot)
        with patch("app.services.auction_service._remove_scheduler_job"):
            result1 = await svc.close_auction(order.id)
            await session.commit()
            # Second call — order is now awaiting_payment, not pending
            result2 = await svc.close_auction(order.id)

        assert result1 == AuctionCloseResult.closed
        assert result2 == AuctionCloseResult.already_closed

    async def test_already_closed_does_not_change_operator(self, session, mock_bot):
        """Second close must not overwrite the already-assigned operator."""
        client = await make_user(session, 10042, "Client J")
        op1 = await make_user(session, 10043, "Op J1", UserRole.operator)
        op2 = await make_user(session, 10044, "Op J2", UserRole.operator)
        order = await make_order(session, client.id)
        await session.commit()

        bid_repo = BidRepo(session)
        await bid_repo.upsert(order.id, op1.id, Decimal("1500"))  # op1 lowest
        await bid_repo.upsert(order.id, op2.id, Decimal("1800"))
        await session.commit()

        svc = _service(session, mock_bot)
        with patch("app.services.auction_service._remove_scheduler_job"):
            await svc.close_auction(order.id)
            await session.commit()
            await svc.close_auction(order.id)  # no-op

        fresh = await OrderRepo(session).get_by_id(order.id)
        assert fresh.operator_id == op1.id  # still op1, not changed by second call

    async def test_not_found_returns_not_found(self, session, mock_bot):
        with patch("app.services.auction_service._remove_scheduler_job"):
            result = await _service(session, mock_bot).close_auction(order_id=999999)
        assert result == AuctionCloseResult.not_found


# ── Auto-assign on budget match ───────────────────────────────────────────────

class TestAutoAssignOnBudgetMatch:

    async def test_bid_equal_to_budget_triggers_assign(self, session, mock_bot):
        """place_bid with amount == order.budget must immediately assign the operator."""
        client = await make_user(session, 10050, "Client K")
        op1 = await make_user(session, 10051, "Op K", UserRole.operator)
        budget = Decimal("2000")
        order = await make_order(session, client.id, budget=budget)
        await session.commit()

        with patch("app.services.auction_service._remove_scheduler_job"):
            svc = _service(session, mock_bot)
            await svc.place_bid(order.id, op1.id, budget)  # exact match
            await session.commit()

        fresh = await OrderRepo(session).get_by_id(order.id)
        assert fresh.status == OrderStatus.awaiting_payment
        assert fresh.operator_id == op1.id

    async def test_bid_below_budget_does_not_auto_assign(self, session, mock_bot):
        """place_bid with amount < budget must NOT auto-assign — auction continues."""
        client = await make_user(session, 10052, "Client L")
        op1 = await make_user(session, 10053, "Op L", UserRole.operator)
        order = await make_order(session, client.id, budget=Decimal("2000"))
        await session.commit()

        with patch("app.services.auction_service._remove_scheduler_job"):
            svc = _service(session, mock_bot)
            await svc.place_bid(order.id, op1.id, Decimal("1500"))  # below budget
            await session.commit()

        fresh = await OrderRepo(session).get_by_id(order.id)
        assert fresh.status == OrderStatus.pending  # still open

    async def test_operator_cannot_bid_on_own_order(self, session, mock_bot):
        """Guard: client_id == operator_id must silently reject the bid."""
        # Create a user who acts as both client and operator (edge case in tests)
        user = await make_user(session, 10054, "Dual User", UserRole.operator)
        order = await make_order(session, user.id)
        await session.commit()

        with patch("app.services.auction_service._remove_scheduler_job"):
            svc = _service(session, mock_bot)
            await svc.place_bid(order.id, user.id, Decimal("1500"))
            await session.commit()

        fresh = await OrderRepo(session).get_by_id(order.id)
        assert fresh.operator_id is None  # bid rejected, no assignment
        bids = await BidRepo(session).get_by_order(order.id)
        assert len(bids) == 0  # no bid recorded
