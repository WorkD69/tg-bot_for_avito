"""Integration tests for OperatorEarning model and EarningRepo.

Tests:
  A. Basic earning creation (create, idempotency, lookup)
  B. mark_paid + mark_paid_all_payable (pays pending + adjusted, skips on_hold)
  C. exclude (sets excluded, raises on paid)
  D. adjust (sets adjusted, does not change gross, stays on_hold when frozen)
  E. Summary aggregates (payable_sum, paid_sum, on_hold_count, etc.)
  F. get_all_payable returns pending + adjusted, never on_hold
  G. PAYOUT SAFETY: freeze / unfreeze / on_hold cannot be auto-paid
  H. PAYOUT SAFETY: mark_paid raises ValueError on on_hold earning
  I. PAYOUT SAFETY: adjust on on_hold keeps on_hold status

Requires a running PostgreSQL — see TESTING.md.
"""
from decimal import Decimal

import pytest

pytestmark = pytest.mark.asyncio(loop_scope="module")

from app.db.models.operator_earning import EarningStatus, PAYABLE_STATUSES
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
    await repo.confirm_payment(order)
    await repo.update_status(order, OrderStatus.completed)
    await session.commit()
    return await repo.get_by_id(order.id), op


# ── A. Basic earning creation ──────────────────────────────────────────────────

class TestEarningCreation:

    async def test_create_stores_gross_and_share(self, session):
        order, op = await _make_completed_order(session, 30001, 30002, Decimal("2000"))
        repo = EarningRepo(session)
        earning = await repo.create_for_order(
            order_id=order.id, operator_id=op.id,
            gross_amount=Decimal("2000"), payout_percent=80.0,
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
            order_id=order.id, operator_id=op.id,
            gross_amount=Decimal("1500"), payout_percent=70.0,
        )
        await session.commit()

        assert earning.payout_percent == Decimal("70.00")
        assert earning.operator_share == Decimal("1050.00")

    async def test_create_links_to_operator(self, session):
        order, op = await _make_completed_order(session, 30005, 30006, Decimal("1800"))
        repo = EarningRepo(session)
        earning = await repo.create_for_order(
            order_id=order.id, operator_id=op.id,
            gross_amount=Decimal("1800"), payout_percent=80.0,
        )
        await session.commit()
        assert earning.operator_id == op.id

    async def test_idempotent_double_create(self, session):
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


# ── B. mark_paid + mark_paid_all_payable ─────────────────────────────────────

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

    async def test_mark_paid_all_payable_pays_multiple(self, session):
        """mark_paid_all_payable pays pending + adjusted, returns both."""
        c1 = await make_user(session, 30030, "Client X1")
        c2 = await make_user(session, 30031, "Client X2")
        op = await make_user(session, 30032, "Op X", UserRole.operator)
        admin = await make_user(session, 99901, "Admin2", UserRole.admin)

        order1 = await make_order(session, c1.id)
        order2 = await make_order(session, c2.id)
        await session.commit()

        o_repo = OrderRepo(session)
        e_repo = EarningRepo(session)

        for order, amount in [(order1, Decimal("1500")), (order2, Decimal("2500"))]:
            await o_repo.assign_operator(order, op.id, amount)
            await o_repo.confirm_payment(order)
            await o_repo.update_status(order, OrderStatus.completed)
            await session.commit()
            await e_repo.create_for_order(order.id, op.id, amount, 80.0)
            await session.commit()

        paid = await e_repo.mark_paid_all_payable(op.id, paid_by_id=admin.id)
        await session.commit()

        assert len(paid) == 2
        for e in paid:
            assert e.status == EarningStatus.paid

    async def test_mark_paid_all_payable_includes_adjusted(self, session):
        """Adjusted earnings (corrected amount) must be included in payable set."""
        order, op = await _make_completed_order(session, 30033, 30034, Decimal("2000"))
        admin = await make_user(session, 99902, "Admin3", UserRole.admin)
        await session.commit()

        repo = EarningRepo(session)
        earning = await repo.create_for_order(order.id, op.id, Decimal("2000"), 80.0)
        await session.commit()

        # Admin corrects the amount — status becomes adjusted
        await repo.adjust(earning, new_share=Decimal("1200"), note="Penalty applied")
        await session.commit()

        fresh = await repo.get_by_order_id(order.id)
        assert fresh.status == EarningStatus.adjusted

        # mark_paid_all_payable must include adjusted
        paid = await repo.mark_paid_all_payable(op.id, paid_by_id=admin.id)
        await session.commit()

        assert len(paid) == 1
        assert paid[0].status == EarningStatus.paid
        assert paid[0].operator_share == Decimal("1200")


# ── C. exclude ────────────────────────────────────────────────────────────────

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

    async def test_exclude_raises_on_paid(self, session):
        """Cannot exclude an already-paid earning."""
        order, op = await _make_completed_order(session, 30042, 30043, Decimal("1000"))
        admin = await make_user(session, 99903, "Admin4", UserRole.admin)
        await session.commit()

        repo = EarningRepo(session)
        earning = await repo.create_for_order(order.id, op.id, Decimal("1000"), 80.0)
        await session.commit()

        await repo.mark_paid(earning, paid_by_id=admin.id)
        await session.commit()

        with pytest.raises(ValueError, match="already paid"):
            await repo.exclude(earning)


# ── D. adjust ─────────────────────────────────────────────────────────────────

class TestAdjust:

    async def test_adjust_changes_share(self, session):
        order, op = await _make_completed_order(session, 30050, 30051, Decimal("3000"))
        repo = EarningRepo(session)
        earning = await repo.create_for_order(order.id, op.id, Decimal("3000"), 80.0)
        await session.commit()

        await repo.adjust(earning, new_share=Decimal("2000"), note="Manual correction")
        await session.commit()

        fresh = await repo.get_by_order_id(order.id)
        assert fresh.operator_share == Decimal("2000")
        assert fresh.status == EarningStatus.adjusted
        assert fresh.note == "Manual correction"

    async def test_adjust_does_not_change_gross(self, session):
        order, op = await _make_completed_order(session, 30052, 30053, Decimal("3000"))
        repo = EarningRepo(session)
        earning = await repo.create_for_order(order.id, op.id, Decimal("3000"), 80.0)
        await session.commit()

        await repo.adjust(earning, new_share=Decimal("500"), note="Penalty")
        await session.commit()

        fresh = await repo.get_by_order_id(order.id)
        assert fresh.gross_amount == Decimal("3000")  # unchanged

    async def test_adjust_raises_on_paid(self, session):
        """Cannot adjust an already-paid earning."""
        order, op = await _make_completed_order(session, 30054, 30055, Decimal("2000"))
        admin = await make_user(session, 99904, "Admin5", UserRole.admin)
        await session.commit()

        repo = EarningRepo(session)
        earning = await repo.create_for_order(order.id, op.id, Decimal("2000"), 80.0)
        await session.commit()
        await repo.mark_paid(earning, paid_by_id=admin.id)
        await session.commit()

        with pytest.raises(ValueError, match="already paid"):
            await repo.adjust(earning, new_share=Decimal("1000"))


# ── E. Summary aggregates ─────────────────────────────────────────────────────

class TestSummary:

    async def test_summary_counts_and_sums(self, session):
        c1 = await make_user(session, 30060, "Client S1")
        c2 = await make_user(session, 30061, "Client S2")
        c3 = await make_user(session, 30062, "Client S3")
        op = await make_user(session, 30063, "Op S", UserRole.operator)
        await session.commit()

        o_repo = OrderRepo(session)
        e_repo = EarningRepo(session)
        amounts = [Decimal("1000"), Decimal("2000"), Decimal("3000")]
        for c, amt in zip([c1, c2, c3], amounts):
            order = await make_order(session, c.id)
            await session.commit()
            await o_repo.assign_operator(order, op.id, amt)
            await o_repo.confirm_payment(order)
            await o_repo.update_status(order, OrderStatus.completed)
            await session.commit()
            await e_repo.create_for_order(order.id, op.id, amt, 80.0)
            await session.commit()

        summary = await e_repo.get_summary_by_operator(op.id)
        assert summary["total_completed"] == 3
        assert summary["total_gross"] == Decimal("6000")
        assert summary["payable_count"] == 3
        assert summary["payable_sum"] == Decimal("4800.00")
        assert summary["paid_sum"] == Decimal("0")
        assert summary["on_hold_count"] == 0
        assert summary["on_hold_sum"] == Decimal("0")
        assert summary["excluded_count"] == 0

    async def test_summary_after_partial_payment(self, session):
        c1 = await make_user(session, 30070, "Client P1")
        c2 = await make_user(session, 30071, "Client P2")
        op = await make_user(session, 30072, "Op P", UserRole.operator)
        admin = await make_user(session, 99905, "Admin P", UserRole.admin)
        await session.commit()

        o_repo = OrderRepo(session)
        e_repo = EarningRepo(session)
        earnings_list = []
        for c, amt in [(c1, Decimal("1000")), (c2, Decimal("2000"))]:
            order = await make_order(session, c.id)
            await session.commit()
            await o_repo.assign_operator(order, op.id, amt)
            await o_repo.confirm_payment(order)
            await o_repo.update_status(order, OrderStatus.completed)
            await session.commit()
            e = await e_repo.create_for_order(order.id, op.id, amt, 80.0)
            await session.commit()
            earnings_list.append(e)

        await e_repo.mark_paid(earnings_list[0], paid_by_id=admin.id)
        await session.commit()

        summary = await e_repo.get_summary_by_operator(op.id)
        assert summary["paid_sum"] == Decimal("800.00")
        assert summary["payable_sum"] == Decimal("1600.00")


# ── F. get_all_payable ────────────────────────────────────────────────────────

class TestGetAllPayable:

    async def test_returns_pending_and_adjusted_not_on_hold(self, session):
        """get_all_payable must never return on_hold earnings."""
        c1 = await make_user(session, 30080, "Client AP1")
        c2 = await make_user(session, 30081, "Client AP2")
        c3 = await make_user(session, 30082, "Client AP3")
        op = await make_user(session, 30083, "Op AP", UserRole.operator)
        admin = await make_user(session, 99906, "Admin AP", UserRole.admin)
        await session.commit()

        o_repo = OrderRepo(session)
        e_repo = EarningRepo(session)
        earnings = []
        for c, amt in [(c1, Decimal("1000")), (c2, Decimal("2000")), (c3, Decimal("3000"))]:
            order = await make_order(session, c.id)
            await session.commit()
            await o_repo.assign_operator(order, op.id, amt)
            await o_repo.confirm_payment(order)
            await o_repo.update_status(order, OrderStatus.completed)
            await session.commit()
            e = await e_repo.create_for_order(order.id, op.id, amt, 80.0)
            await session.commit()
            earnings.append(e)

        # Freeze the second earning
        await e_repo.freeze(earnings[1], frozen_by_id=admin.id, note="Client dispute")
        await session.commit()

        payable = await e_repo.get_all_payable()
        payable_order_ids = {e.order_id for e in payable}

        assert earnings[0].order_id in payable_order_ids   # pending — included
        assert earnings[1].order_id not in payable_order_ids  # on_hold — NOT included
        assert earnings[2].order_id in payable_order_ids   # pending — included


# ── G. PAYOUT SAFETY: freeze / unfreeze ──────────────────────────────────────

class TestFreeze:

    async def test_freeze_moves_to_on_hold(self, session):
        order, op = await _make_completed_order(session, 30090, 30091, Decimal("1500"))
        admin = await make_user(session, 99910, "Admin Freeze", UserRole.admin)
        await session.commit()

        repo = EarningRepo(session)
        earning = await repo.create_for_order(order.id, op.id, Decimal("1500"), 80.0)
        await session.commit()

        await repo.freeze(earning, frozen_by_id=admin.id, note="Client filed complaint")
        await session.commit()

        fresh = await repo.get_by_order_id(order.id)
        assert fresh.status == EarningStatus.on_hold
        assert fresh.frozen_at is not None
        assert fresh.frozen_by_id == admin.id
        assert fresh.note == "Client filed complaint"

    async def test_freeze_is_noop_if_already_on_hold(self, session):
        """Calling freeze twice does not error."""
        order, op = await _make_completed_order(session, 30092, 30093, Decimal("1500"))
        admin = await make_user(session, 99911, "Admin Freeze2", UserRole.admin)
        await session.commit()

        repo = EarningRepo(session)
        earning = await repo.create_for_order(order.id, op.id, Decimal("1500"), 80.0)
        await session.commit()

        await repo.freeze(earning, frozen_by_id=admin.id, note="First freeze")
        await session.commit()
        await repo.freeze(earning, frozen_by_id=admin.id, note="Second freeze — should be noop")
        await session.commit()

        fresh = await repo.get_by_order_id(order.id)
        assert fresh.status == EarningStatus.on_hold
        assert fresh.note == "First freeze"  # note unchanged by second call

    async def test_freeze_raises_on_paid(self, session):
        """Cannot freeze an already-paid earning."""
        order, op = await _make_completed_order(session, 30094, 30095, Decimal("1500"))
        admin = await make_user(session, 99912, "Admin Freeze3", UserRole.admin)
        await session.commit()

        repo = EarningRepo(session)
        earning = await repo.create_for_order(order.id, op.id, Decimal("1500"), 80.0)
        await session.commit()
        await repo.mark_paid(earning, paid_by_id=admin.id)
        await session.commit()

        with pytest.raises(ValueError, match="already"):
            await repo.freeze(earning, frozen_by_id=admin.id)

    async def test_unfreeze_moves_back_to_pending(self, session):
        order, op = await _make_completed_order(session, 30096, 30097, Decimal("2000"))
        admin = await make_user(session, 99913, "Admin Unfreeze", UserRole.admin)
        await session.commit()

        repo = EarningRepo(session)
        earning = await repo.create_for_order(order.id, op.id, Decimal("2000"), 80.0)
        await session.commit()

        await repo.freeze(earning, frozen_by_id=admin.id, note="Dispute opened")
        await session.commit()

        await repo.unfreeze(earning, note="Dispute resolved — op in the right")
        await session.commit()

        fresh = await repo.get_by_order_id(order.id)
        assert fresh.status == EarningStatus.pending
        assert fresh.frozen_at is None
        assert fresh.frozen_by_id is None
        assert fresh.note == "Dispute resolved — op in the right"

    async def test_unfreeze_raises_if_not_on_hold(self, session):
        order, op = await _make_completed_order(session, 30098, 30099, Decimal("1000"))
        await session.commit()

        repo = EarningRepo(session)
        earning = await repo.create_for_order(order.id, op.id, Decimal("1000"), 80.0)
        await session.commit()

        with pytest.raises(ValueError, match="not on_hold"):
            await repo.unfreeze(earning)


# ── H. PAYOUT SAFETY: mark_paid raises on on_hold ────────────────────────────

class TestMarkPaidSafety:

    async def test_mark_paid_raises_on_on_hold(self, session):
        """mark_paid must raise ValueError for on_hold earnings — never auto-pay frozen."""
        order, op = await _make_completed_order(session, 30100, 30101, Decimal("2000"))
        admin = await make_user(session, 99920, "Admin Safety", UserRole.admin)
        await session.commit()

        repo = EarningRepo(session)
        earning = await repo.create_for_order(order.id, op.id, Decimal("2000"), 80.0)
        await session.commit()

        await repo.freeze(earning, frozen_by_id=admin.id, note="Dispute")
        await session.commit()

        with pytest.raises(ValueError, match="on_hold"):
            await repo.mark_paid(earning, paid_by_id=admin.id)

    async def test_mark_paid_all_payable_skips_on_hold(self, session):
        """mark_paid_all_payable must never touch on_hold earnings."""
        c1 = await make_user(session, 30102, "Client Safe1")
        c2 = await make_user(session, 30103, "Client Safe2")
        op = await make_user(session, 30104, "Op Safe", UserRole.operator)
        admin = await make_user(session, 99921, "Admin Safe2", UserRole.admin)
        await session.commit()

        o_repo = OrderRepo(session)
        e_repo = EarningRepo(session)
        earnings = []
        for c, amt in [(c1, Decimal("1000")), (c2, Decimal("2000"))]:
            order = await make_order(session, c.id)
            await session.commit()
            await o_repo.assign_operator(order, op.id, amt)
            await o_repo.confirm_payment(order)
            await o_repo.update_status(order, OrderStatus.completed)
            await session.commit()
            e = await e_repo.create_for_order(order.id, op.id, amt, 80.0)
            await session.commit()
            earnings.append(e)

        # Freeze one of them
        await e_repo.freeze(earnings[0], frozen_by_id=admin.id, note="Under review")
        await session.commit()

        paid = await e_repo.mark_paid_all_payable(op.id, paid_by_id=admin.id)
        await session.commit()

        # Only the non-frozen earning was paid
        assert len(paid) == 1
        assert paid[0].order_id == earnings[1].order_id

        # Frozen one must remain on_hold
        frozen_fresh = await e_repo.get_by_order_id(earnings[0].order_id)
        assert frozen_fresh.status == EarningStatus.on_hold


# ── I. PAYOUT SAFETY: adjust on on_hold keeps on_hold ────────────────────────

class TestAdjustOnHold:

    async def test_adjust_on_on_hold_keeps_on_hold(self, session):
        """Adjusting an on_hold earning must NOT unfreeze it."""
        order, op = await _make_completed_order(session, 30110, 30111, Decimal("3000"))
        admin = await make_user(session, 99930, "Admin Adj", UserRole.admin)
        await session.commit()

        repo = EarningRepo(session)
        earning = await repo.create_for_order(order.id, op.id, Decimal("3000"), 80.0)
        await session.commit()

        await repo.freeze(earning, frozen_by_id=admin.id, note="Dispute")
        await session.commit()

        # Admin adjusts the amount while it's frozen
        await repo.adjust(earning, new_share=Decimal("1000"), note="Penalty — stays frozen")
        await session.commit()

        fresh = await repo.get_by_order_id(order.id)
        # Status must remain on_hold, not change to adjusted
        assert fresh.status == EarningStatus.on_hold
        assert fresh.operator_share == Decimal("1000")

    async def test_unfreeze_after_adjust_becomes_pending(self, session):
        """After adjusting then unfreezing, earning should be pending (payable)."""
        order, op = await _make_completed_order(session, 30112, 30113, Decimal("3000"))
        admin = await make_user(session, 99931, "Admin AdjUnfreeze", UserRole.admin)
        await session.commit()

        repo = EarningRepo(session)
        earning = await repo.create_for_order(order.id, op.id, Decimal("3000"), 80.0)
        await session.commit()

        await repo.freeze(earning, frozen_by_id=admin.id, note="Dispute")
        await session.commit()
        await repo.adjust(earning, new_share=Decimal("500"), note="Reduced penalty")
        await session.commit()
        await repo.unfreeze(earning, note="Resolved")
        await session.commit()

        fresh = await repo.get_by_order_id(order.id)
        assert fresh.status == EarningStatus.pending
        assert fresh.operator_share == Decimal("500")

        # Now must appear in payable
        payable = await repo.get_payable(op.id)
        assert any(e.order_id == order.id for e in payable)


# PAYABLE_STATUSES safety contract is tested in tests/unit/test_earning_repo.py
# (TestPayableStatusesSafetyContract) — sync tests live in unit, not integration.
