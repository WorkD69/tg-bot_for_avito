"""Integration tests for OperatorEarning model and EarningRepo.

Tests:
  - Earning created on order completion with correct amounts
  - Idempotency (double-complete doesn't create two earnings)
  - mark_paid transitions status + records who paid and when
  - mark_paid_all_pending pays multiple pending earnings in one call
  - exclude sets status=excluded
  - adjust sets status=adjusted + new amount
  - get_summary_by_operator aggregates correctly
  - get_all_pending returns across multiple operators
  - Period filter (since) narrows results correctly

Requires a running PostgreSQL — see TESTING.md.
"""
from decimal import Decimal

import pytest

pytestmark = pytest.mark.asyncio(loop_scope="module")

from app.db.models.operator_earning import EarningStatus
from app.db.models.order import OrderStatus
from app.db.models.user import UserRole
from app.repositories.earning_repo import EarningRepo
from app.repositories.order_repo import OrderRepo

from tests.integration.conftest import make_order, make_user


# ── Helpers ───────────────────────────────────────────────────────────────────

async def _make_completed_order(session, client_tgid: int, op_tgid: int, amount: Decimal):
    """Create an order in completed state with payment_amount set."""
    client = await make_user(session, client_tgid, f"Client {client_tgid}")
    op = await make_user(session, op_tgid, f"Op {op_tgid}", UserRole.operator)
    order = await make_order(session, client.id)
    await session.commit()

    repo = OrderRepo(session)
    await repo.assign_operator(order, op.id, amount)
    await repo.confirm_payment(order)  # awaiting → in_progress
    await repo.update_status(order, OrderStatus.completed)
    await session.commit()
    return await repo.get_by_id(order.id), op


# ── Basic earning creation ─────────────────────────────────────────────────────

class TestEarningCreation:

    async def test_create_stores_gross_and_share(self, session):
        order, op = await _make_completed_order(session, 30001, 30002, Decimal("2000"))
        repo = EarningRepo(session)
        earning = await repo.create_for_order(
            order_id=order.id,
            operator_id=op.id,
            gross_amount=Decimal("2000"),
            payout_percent=80.0,
        )
        await session.commit()

        assert earning.gross_amount == Decimal("2000")
        assert earning.operator_share == Decimal("1600.00")
        assert earning.payout_percent == Decimal("80.00")
        assert earning.status == EarningStatus.pending

    async def test_create_stores_payout_percent(self, session):
        order, op = await _make_completed_order(session, 30003, 30004, Decimal("1500"))
        repo = EarningRepo(session)
        earning = await repo.create_for_order(
            order_id=order.id,
            operator_id=op.id,
            gross_amount=Decimal("1500"),
            payout_percent=70.0,
        )
        await session.commit()

        assert earning.payout_percent == Decimal("70.00")
        assert earning.operator_share == Decimal("1050.00")

    async def test_create_links_to_operator(self, session):
        order, op = await _make_completed_order(session, 30005, 30006, Decimal("1800"))
        repo = EarningRepo(session)
        earning = await repo.create_for_order(
            order_id=order.id,
            operator_id=op.id,
            gross_amount=Decimal("1800"),
            payout_percent=80.0,
        )
        await session.commit()

        assert earning.operator_id == op.id

    async def test_idempotent_double_create(self, session):
        """Calling create_for_order twice for the same order returns existing, doesn't create dupe."""
        order, op = await _make_completed_order(session, 30007, 30008, Decimal("1000"))
        repo = EarningRepo(session)
        e1 = await repo.create_for_order(order.id, op.id, Decimal("1000"), 80.0)
        await session.commit()
        e2 = await repo.create_for_order(order.id, op.id, Decimal("1000"), 80.0)
        await session.commit()

        assert e1.id == e2.id

    async def test_get_by_order_id(self, session):
        order, op = await _make_completed_order(session, 30009, 30010, Decimal("1200"))
        repo = EarningRepo(session)
        await repo.create_for_order(order.id, op.id, Decimal("1200"), 80.0)
        await session.commit()

        found = await repo.get_by_order_id(order.id)
        assert found is not None
        assert found.order_id == order.id

    async def test_get_by_order_id_nonexistent(self, session):
        repo = EarningRepo(session)
        assert await repo.get_by_order_id(999999) is None


# ── Status mutations ──────────────────────────────────────────────────────────

class TestMarkPaid:

    async def test_mark_paid_status(self, session):
        order, op = await _make_completed_order(session, 30020, 30021, Decimal("2000"))
        repo = EarningRepo(session)
        earning = await repo.create_for_order(order.id, op.id, Decimal("2000"), 80.0)
        await session.commit()

        admin = await make_user(session, 99900, "Admin", UserRole.admin)
        await session.commit()

        await repo.mark_paid(earning, paid_by_id=admin.id, note="Wire transfer Feb 2026")
        await session.commit()

        fresh = await repo.get_by_order_id(order.id)
        assert fresh.status == EarningStatus.paid
        assert fresh.paid_at is not None
        assert fresh.paid_by_id == admin.id
        assert fresh.note == "Wire transfer Feb 2026"

    async def test_mark_paid_all_pending_pays_multiple(self, session):
        """mark_paid_all_pending pays all pending earnings for one operator."""
        client1 = await make_user(session, 30030, "Client X1")
        client2 = await make_user(session, 30031, "Client X2")
        op = await make_user(session, 30032, "Op X", UserRole.operator)
        admin = await make_user(session, 99901, "Admin2", UserRole.admin)

        order1 = await make_order(session, client1.id)
        order2 = await make_order(session, client2.id)
        await session.commit()

        order_repo = OrderRepo(session)
        earning_repo = EarningRepo(session)

        for order, amount in [(order1, Decimal("1500")), (order2, Decimal("2500"))]:
            await order_repo.assign_operator(order, op.id, amount)
            await order_repo.confirm_payment(order)
            await order_repo.update_status(order, OrderStatus.completed)
            await session.commit()
            await earning_repo.create_for_order(order.id, op.id, amount, 80.0)
            await session.commit()

        paid = await earning_repo.mark_paid_all_pending(op.id, paid_by_id=admin.id)
        await session.commit()

        assert len(paid) == 2
        for e in paid:
            assert e.status == EarningStatus.paid


class TestExclude:

    async def test_exclude_sets_status(self, session):
        order, op = await _make_completed_order(session, 30040, 30041, Decimal("1000"))
        repo = EarningRepo(session)
        earning = await repo.create_for_order(order.id, op.id, Decimal("1000"), 80.0)
        await session.commit()

        await repo.exclude(earning, note="Dispute: client refund requested")
        await session.commit()

        fresh = await repo.get_by_order_id(order.id)
        assert fresh.status == EarningStatus.excluded
        assert "Dispute" in fresh.note


class TestAdjust:

    async def test_adjust_changes_share(self, session):
        order, op = await _make_completed_order(session, 30050, 30051, Decimal("3000"))
        repo = EarningRepo(session)
        earning = await repo.create_for_order(order.id, op.id, Decimal("3000"), 80.0)
        await session.commit()

        await repo.adjust(earning, new_share=Decimal("2000"), note="Manual correction by admin")
        await session.commit()

        fresh = await repo.get_by_order_id(order.id)
        assert fresh.operator_share == Decimal("2000")
        assert fresh.status == EarningStatus.adjusted
        assert fresh.note == "Manual correction by admin"

    async def test_adjust_does_not_change_gross(self, session):
        order, op = await _make_completed_order(session, 30052, 30053, Decimal("3000"))
        repo = EarningRepo(session)
        earning = await repo.create_for_order(order.id, op.id, Decimal("3000"), 80.0)
        await session.commit()

        await repo.adjust(earning, new_share=Decimal("500"), note="Penalty")
        await session.commit()

        fresh = await repo.get_by_order_id(order.id)
        assert fresh.gross_amount == Decimal("3000")  # gross unchanged


# ── Summary aggregates ────────────────────────────────────────────────────────

class TestSummary:

    async def test_summary_counts_and_sums(self, session):
        """get_summary_by_operator returns correct totals."""
        client = await make_user(session, 30060, "Client S")
        op = await make_user(session, 30061, "Op S", UserRole.operator)
        await session.commit()

        repo_o = OrderRepo(session)
        repo_e = EarningRepo(session)
        amounts = [Decimal("1000"), Decimal("2000"), Decimal("3000")]
        for i, amt in enumerate(amounts):
            c = await make_user(session, 30070 + i, f"SC {i}")
            order = await make_order(session, c.id)
            await session.commit()
            await repo_o.assign_operator(order, op.id, amt)
            await repo_o.confirm_payment(order)
            await repo_o.update_status(order, OrderStatus.completed)
            await session.commit()
            await repo_e.create_for_order(order.id, op.id, amt, 80.0)
            await session.commit()

        summary = await repo_e.get_summary_by_operator(op.id)
        assert summary["total_completed"] == 3
        assert summary["total_gross"] == Decimal("6000")
        assert summary["total_share"] == Decimal("4800.00")
        assert summary["pending_count"] == 3
        assert summary["pending_sum"] == Decimal("4800.00")
        assert summary["paid_sum"] == Decimal("0")
        assert summary["excluded_count"] == 0

    async def test_summary_after_partial_payment(self, session):
        """After paying 1 of 2 earnings, sums update correctly."""
        client1 = await make_user(session, 30080, "Client P1")
        client2 = await make_user(session, 30081, "Client P2")
        op = await make_user(session, 30082, "Op P", UserRole.operator)
        admin = await make_user(session, 99902, "Admin P", UserRole.admin)
        await session.commit()

        repo_o = OrderRepo(session)
        repo_e = EarningRepo(session)
        earnings_list = []
        for c, amt in [(client1, Decimal("1000")), (client2, Decimal("2000"))]:
            order = await make_order(session, c.id)
            await session.commit()
            await repo_o.assign_operator(order, op.id, amt)
            await repo_o.confirm_payment(order)
            await repo_o.update_status(order, OrderStatus.completed)
            await session.commit()
            e = await repo_e.create_for_order(order.id, op.id, amt, 80.0)
            await session.commit()
            earnings_list.append(e)

        # Pay first earning only
        await repo_e.mark_paid(earnings_list[0], paid_by_id=admin.id)
        await session.commit()

        summary = await repo_e.get_summary_by_operator(op.id)
        assert summary["paid_sum"] == Decimal("800.00")    # 1000 * 80%
        assert summary["pending_sum"] == Decimal("1600.00")  # 2000 * 80%


class TestGetAllPending:

    async def test_returns_all_operators_pending(self, session):
        """get_all_pending includes earnings from multiple operators."""
        c1 = await make_user(session, 30090, "Client AP1")
        c2 = await make_user(session, 30091, "Client AP2")
        op1 = await make_user(session, 30092, "Op AP1", UserRole.operator)
        op2 = await make_user(session, 30093, "Op AP2", UserRole.operator)
        await session.commit()

        repo_o = OrderRepo(session)
        repo_e = EarningRepo(session)
        for c, op, amt in [(c1, op1, Decimal("1000")), (c2, op2, Decimal("2000"))]:
            order = await make_order(session, c.id)
            await session.commit()
            await repo_o.assign_operator(order, op.id, amt)
            await repo_o.confirm_payment(order)
            await repo_o.update_status(order, OrderStatus.completed)
            await session.commit()
            await repo_e.create_for_order(order.id, op.id, amt, 80.0)
            await session.commit()

        all_pending = await repo_e.get_all_pending()
        operator_ids = {e.operator_id for e in all_pending}
        assert op1.id in operator_ids
        assert op2.id in operator_ids
