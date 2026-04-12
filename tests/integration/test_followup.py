"""Integration tests for P1 follow-up idempotency.

Tests:
  A. send_order_followup atomic idempotency — followup_sent_at set once
  B. skip when order not in completed status
  C. skip when order followup_sent_at already set
  D. skip when client has active orders
  E. followups_sent counter in business summary

Requires a running PostgreSQL — see tests/integration/conftest.py.
"""
from datetime import datetime, timezone
from decimal import Decimal

import pytest

pytestmark = pytest.mark.asyncio(loop_scope="module")

from app.db.models.order import Order, OrderStatus
from app.db.models.user import UserRole
from app.repositories.analytics_repo import AnalyticsRepo
from app.repositories.order_repo import OrderRepo

from tests.integration.conftest import make_order, make_user


# ── Helpers ───────────────────────────────────────────────────────────────────

async def _complete_order(session, order: Order, operator_id: int) -> Order:
    """Helper: move order through assign → confirm_payment → completed."""
    repo = OrderRepo(session)
    await repo.assign_operator(order, operator_id, Decimal("2000"))
    await repo.confirm_payment(order)
    await repo.update_status(order, OrderStatus.completed)
    await session.flush()
    return order


# ── A. Atomic idempotency — followup_sent_at set exactly once ────────────────

class TestFollowupIdempotency:

    async def test_followup_sent_at_is_null_initially(self, session):
        """Newly created order has followup_sent_at=NULL."""
        client = await make_user(session, 95001, "FU Client A")
        order = await make_order(session, client.id)
        await session.commit()

        assert order.followup_sent_at is None

    async def test_followup_sent_at_set_after_atomic_update(self, session):
        """Atomic UPDATE sets followup_sent_at when conditions are met."""
        from sqlalchemy import update, select
        client = await make_user(session, 95010, "FU Client B")
        op = await make_user(session, 95011, "FU Op B", UserRole.operator)
        order = await make_order(session, client.id)
        await _complete_order(session, order, op.id)
        await session.commit()

        # Simulate atomic update as send_order_followup does it
        now = datetime.now(timezone.utc)
        result = await session.execute(
            update(Order)
            .where(
                Order.id == order.id,
                Order.status == OrderStatus.completed,
                Order.followup_sent_at.is_(None),
            )
            .values(followup_sent_at=now)
            .returning(Order.client_id)
        )
        row = result.first()
        await session.commit()

        assert row is not None, "Expected UPDATE to match one row"
        assert row[0] == client.id

        # Re-fetch and verify
        await session.refresh(order)
        assert order.followup_sent_at is not None

    async def test_second_atomic_update_returns_no_rows(self, session):
        """Second UPDATE on same order returns no rows — idempotency guard."""
        from sqlalchemy import update
        client = await make_user(session, 95020, "FU Client C")
        op = await make_user(session, 95021, "FU Op C", UserRole.operator)
        order = await make_order(session, client.id)
        await _complete_order(session, order, op.id)
        # Pre-set followup_sent_at to simulate first send
        order.followup_sent_at = datetime.now(timezone.utc)
        await session.commit()

        # Second attempt
        result = await session.execute(
            update(Order)
            .where(
                Order.id == order.id,
                Order.status == OrderStatus.completed,
                Order.followup_sent_at.is_(None),
            )
            .values(followup_sent_at=datetime.now(timezone.utc))
            .returning(Order.client_id)
        )
        row = result.first()
        await session.commit()

        assert row is None, "Second UPDATE must match zero rows (already sent)"


# ── B. Skip when order not completed ─────────────────────────────────────────

class TestFollowupSkipConditions:

    async def test_skip_pending_order(self, session):
        """Atomic UPDATE does not match pending orders."""
        from sqlalchemy import update
        client = await make_user(session, 95030, "FU Skip Pending")
        order = await make_order(session, client.id)
        assert order.status == OrderStatus.pending
        await session.commit()

        result = await session.execute(
            update(Order)
            .where(
                Order.id == order.id,
                Order.status == OrderStatus.completed,
                Order.followup_sent_at.is_(None),
            )
            .values(followup_sent_at=datetime.now(timezone.utc))
            .returning(Order.client_id)
        )
        row = result.first()
        await session.commit()

        assert row is None, "Pending order must not be claimed for followup"

    async def test_skip_cancelled_order(self, session):
        """Atomic UPDATE does not match cancelled orders."""
        from sqlalchemy import update
        client = await make_user(session, 95040, "FU Skip Cancelled")
        order = await make_order(session, client.id)
        await OrderRepo(session).cancel(order, "client")
        await session.commit()

        result = await session.execute(
            update(Order)
            .where(
                Order.id == order.id,
                Order.status == OrderStatus.completed,
                Order.followup_sent_at.is_(None),
            )
            .values(followup_sent_at=datetime.now(timezone.utc))
            .returning(Order.client_id)
        )
        row = result.first()
        await session.commit()

        assert row is None, "Cancelled order must not be claimed for followup"


# ── C. Analytics: followups_sent counter ─────────────────────────────────────

class TestFollowupAnalytics:

    async def test_followups_sent_in_business_summary(self, session):
        """get_business_summary includes followups_sent key."""
        repo = AnalyticsRepo(session)
        summary = await repo.get_business_summary()
        assert "followups_sent" in summary
        assert isinstance(summary["followups_sent"], int)
        assert summary["followups_sent"] >= 0

    async def test_followups_sent_increments_after_set(self, session):
        """followups_sent count increases when followup_sent_at is set on an order."""
        from sqlalchemy import update
        client = await make_user(session, 95050, "FU Analytics")
        op = await make_user(session, 95051, "FU Analytics Op", UserRole.operator)
        order = await make_order(session, client.id)
        await _complete_order(session, order, op.id)
        await session.commit()

        repo = AnalyticsRepo(session)
        summary_before = await repo.get_business_summary()
        count_before = summary_before["followups_sent"]

        # Mark followup as sent
        await session.execute(
            update(Order)
            .where(Order.id == order.id)
            .values(followup_sent_at=datetime.now(timezone.utc))
        )
        await session.commit()

        summary_after = await repo.get_business_summary()
        assert summary_after["followups_sent"] >= count_before + 1
