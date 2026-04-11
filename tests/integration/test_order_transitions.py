"""Integration tests for order state transitions and OrderRepo mutations.

Tests the state machine invariants:
  pending → awaiting_payment (assign_operator)
  awaiting_payment → in_progress (confirm_payment)
  any → cancelled (cancel)
  price negotiation: update_agreed_price increments revision + resets payment state

These are DB-level tests — no bot, no service layer, pure repository.
Requires a running PostgreSQL — see TESTING.md.
"""
from decimal import Decimal

import pytest

from app.db.models.order import OrderStatus
from app.db.models.user import UserRole
from app.repositories.order_repo import OrderRepo

from tests.integration.conftest import make_order, make_user


# ── pending → awaiting_payment ────────────────────────────────────────────────

class TestAssignOperator:

    async def test_status_becomes_awaiting_payment(self, session):
        client = await make_user(session, 20001, "Client A")
        op = await make_user(session, 20002, "Op A", UserRole.operator)
        order = await make_order(session, client.id)
        await session.commit()

        repo = OrderRepo(session)
        await repo.assign_operator(order, op.id, Decimal("1500"))
        await session.commit()

        fresh = await repo.get_by_id(order.id)
        assert fresh.status == OrderStatus.awaiting_payment

    async def test_operator_id_set(self, session):
        client = await make_user(session, 20003, "Client B")
        op = await make_user(session, 20004, "Op B", UserRole.operator)
        order = await make_order(session, client.id)
        await session.commit()

        repo = OrderRepo(session)
        await repo.assign_operator(order, op.id, Decimal("1800"))
        await session.commit()

        fresh = await repo.get_by_id(order.id)
        assert fresh.operator_id == op.id

    async def test_payment_amount_set(self, session):
        client = await make_user(session, 20005, "Client C")
        op = await make_user(session, 20006, "Op C", UserRole.operator)
        order = await make_order(session, client.id)
        await session.commit()

        repo = OrderRepo(session)
        await repo.assign_operator(order, op.id, Decimal("1750"))
        await session.commit()

        fresh = await repo.get_by_id(order.id)
        assert fresh.payment_amount == Decimal("1750")

    async def test_payment_revision_incremented(self, session):
        client = await make_user(session, 20007, "Client D")
        op = await make_user(session, 20008, "Op D", UserRole.operator)
        order = await make_order(session, client.id)
        initial_rev = order.payment_revision  # 0
        await session.commit()

        repo = OrderRepo(session)
        await repo.assign_operator(order, op.id, Decimal("1500"))
        await session.commit()

        fresh = await repo.get_by_id(order.id)
        assert fresh.payment_revision == initial_rev + 1

    async def test_invoice_id_format(self, session):
        """invoice_id must be '{order_id}_{revision}' after assignment."""
        client = await make_user(session, 20009, "Client E")
        op = await make_user(session, 20010, "Op E", UserRole.operator)
        order = await make_order(session, client.id)
        await session.commit()

        repo = OrderRepo(session)
        await repo.assign_operator(order, op.id, Decimal("1500"))
        await session.commit()

        fresh = await repo.get_by_id(order.id)
        assert fresh.payment_invoice_id == f"{order.id}_{fresh.payment_revision}"


# ── awaiting_payment → in_progress ───────────────────────────────────────────

class TestConfirmPayment:

    async def _setup_awaiting(self, session):
        """Create an order in awaiting_payment state."""
        client = await make_user(session, 20020, "Client F")
        op = await make_user(session, 20021, "Op F", UserRole.operator)
        order = await make_order(session, client.id)
        await session.commit()

        repo = OrderRepo(session)
        await repo.assign_operator(order, op.id, Decimal("1500"))
        await session.commit()
        return await repo.get_by_id(order.id)

    async def test_status_becomes_in_progress(self, session):
        order = await self._setup_awaiting(session)
        repo = OrderRepo(session)
        await repo.confirm_payment(order)
        await session.commit()

        fresh = await repo.get_by_id(order.id)
        assert fresh.status == OrderStatus.in_progress

    async def test_payment_confirmed_at_set(self, session):
        order = await self._setup_awaiting(session)
        repo = OrderRepo(session)
        await repo.confirm_payment(order)
        await session.commit()

        fresh = await repo.get_by_id(order.id)
        assert fresh.payment_confirmed_at is not None


# ── cancel ────────────────────────────────────────────────────────────────────

class TestCancel:

    async def test_status_becomes_cancelled(self, session):
        client = await make_user(session, 20030, "Client G")
        order = await make_order(session, client.id)
        await session.commit()

        repo = OrderRepo(session)
        await repo.cancel(order, "client")
        await session.commit()

        fresh = await repo.get_by_id(order.id)
        assert fresh.status == OrderStatus.cancelled

    async def test_cancelled_by_stored(self, session):
        client = await make_user(session, 20031, "Client H")
        order = await make_order(session, client.id)
        await session.commit()

        repo = OrderRepo(session)
        await repo.cancel(order, "operator")
        await session.commit()

        fresh = await repo.get_by_id(order.id)
        assert fresh.cancelled_by == "operator"

    async def test_cancel_by_admin(self, session):
        client = await make_user(session, 20032, "Client I")
        order = await make_order(session, client.id)
        await session.commit()

        repo = OrderRepo(session)
        await repo.cancel(order, "admin")
        await session.commit()

        fresh = await repo.get_by_id(order.id)
        assert fresh.cancelled_by == "admin"


# ── update_agreed_price (price negotiation) ───────────────────────────────────

class TestUpdateAgreedPrice:
    """update_agreed_price is called when operator accepts client counter-offer
    or sends their own counter-offer. It must:
    - Change payment_amount to the new value
    - Increment payment_revision (invalidates old Robokassa links)
    - Reset payment_received_at and payment_confirmed_at (old payment no longer valid)
    """

    async def _setup_awaiting(self, session, telegram_offset: int):
        """Helper: create order in awaiting_payment with payment_revision=1."""
        client = await make_user(session, 20040 + telegram_offset, f"Client {telegram_offset}")
        op = await make_user(session, 20050 + telegram_offset, f"Op {telegram_offset}", UserRole.operator)
        order = await make_order(session, client.id)
        await session.commit()
        repo = OrderRepo(session)
        await repo.assign_operator(order, op.id, Decimal("2000"))
        await session.commit()
        return await repo.get_by_id(order.id)

    async def test_payment_amount_updated(self, session):
        order = await self._setup_awaiting(session, 0)
        repo = OrderRepo(session)
        await repo.update_agreed_price(order, Decimal("1700"))
        await session.commit()

        fresh = await repo.get_by_id(order.id)
        assert fresh.payment_amount == Decimal("1700")

    async def test_payment_revision_incremented(self, session):
        order = await self._setup_awaiting(session, 1)
        revision_before = order.payment_revision
        repo = OrderRepo(session)
        await repo.update_agreed_price(order, Decimal("1700"))
        await session.commit()

        fresh = await repo.get_by_id(order.id)
        assert fresh.payment_revision == revision_before + 1

    async def test_invoice_id_reflects_new_revision(self, session):
        order = await self._setup_awaiting(session, 2)
        repo = OrderRepo(session)
        await repo.update_agreed_price(order, Decimal("1700"))
        await session.commit()

        fresh = await repo.get_by_id(order.id)
        assert fresh.payment_invoice_id == f"{order.id}_{fresh.payment_revision}"

    async def test_payment_received_at_reset(self, session):
        """Old payment receipt is invalidated when price changes."""
        from datetime import datetime, timezone
        order = await self._setup_awaiting(session, 3)

        # Simulate: client paid at old price
        order.payment_received_at = datetime.now(timezone.utc)
        await session.flush()
        await session.commit()

        # Now price changes
        repo = OrderRepo(session)
        order2 = await repo.get_by_id(order.id)
        await repo.update_agreed_price(order2, Decimal("1700"))
        await session.commit()

        fresh = await repo.get_by_id(order.id)
        assert fresh.payment_received_at is None

    async def test_payment_confirmed_at_reset(self, session):
        """Admin confirmation is invalidated when price changes."""
        from datetime import datetime, timezone
        order = await self._setup_awaiting(session, 4)

        # Simulate: admin had confirmed at old price
        order.payment_confirmed_at = datetime.now(timezone.utc)
        await session.flush()
        await session.commit()

        repo = OrderRepo(session)
        order2 = await repo.get_by_id(order.id)
        await repo.update_agreed_price(order2, Decimal("1700"))
        await session.commit()

        fresh = await repo.get_by_id(order.id)
        assert fresh.payment_confirmed_at is None

    async def test_two_price_changes_revision_increments_twice(self, session):
        """Revision must increment on each price change, not reset to a fixed value."""
        order = await self._setup_awaiting(session, 5)
        rev0 = order.payment_revision  # 1 after assign_operator
        repo = OrderRepo(session)

        await repo.update_agreed_price(order, Decimal("1700"))
        await session.commit()
        order2 = await repo.get_by_id(order.id)
        assert order2.payment_revision == rev0 + 1

        await repo.update_agreed_price(order2, Decimal("1600"))
        await session.commit()
        order3 = await repo.get_by_id(order.id)
        assert order3.payment_revision == rev0 + 2


# ── Robokassa stale InvId logic ───────────────────────────────────────────────

class TestStaleInvoiceId:
    """
    When Robokassa retries a callback for an outdated invoice revision,
    the payment endpoint must:
    - Recognise the stale revision (callback revision < order.payment_revision)
    - Return OK{InvId} to stop retries
    - NOT mark the order as payment_received

    This test validates the DB lookup logic used by the payment endpoint.
    """

    async def test_stale_inv_id_order_not_found_by_invoice(self, session):
        """After price change, the old invoice_id must no longer resolve to the order."""
        client = await make_user(session, 20060, "Client Stale")
        op = await make_user(session, 20061, "Op Stale", UserRole.operator)
        order = await make_order(session, client.id)
        await session.commit()

        repo = OrderRepo(session)
        await repo.assign_operator(order, op.id, Decimal("2000"))
        await session.commit()

        order_v1 = await repo.get_by_id(order.id)
        old_invoice_id = order_v1.payment_invoice_id  # e.g. "1_1"

        # Price negotiation changes revision
        await repo.update_agreed_price(order_v1, Decimal("1700"))
        await session.commit()

        order_v2 = await repo.get_by_id(order.id)
        new_invoice_id = order_v2.payment_invoice_id  # e.g. "1_2"

        # Old invoice_id must no longer match the order
        by_old = await repo.get_by_invoice_id(old_invoice_id)
        by_new = await repo.get_by_invoice_id(new_invoice_id)

        assert by_old is None          # stale — not found
        assert by_new is not None      # current — found
        assert by_new.id == order.id

    async def test_stale_revision_detected_by_comparison(self, session):
        """Simulate the stale-InvId detection logic from payment.py endpoint."""
        client = await make_user(session, 20062, "Client Stale2")
        op = await make_user(session, 20063, "Op Stale2", UserRole.operator)
        order = await make_order(session, client.id)
        await session.commit()

        repo = OrderRepo(session)
        await repo.assign_operator(order, op.id, Decimal("2000"))
        await session.commit()
        order_v1 = await repo.get_by_id(order.id)
        old_rev = order_v1.payment_revision  # 1

        # Price changes → revision becomes 2
        await repo.update_agreed_price(order_v1, Decimal("1700"))
        await session.commit()
        order_v2 = await repo.get_by_id(order.id)
        new_rev = order_v2.payment_revision  # 2

        # Stale callback arrives with the old InvId (format: "{order_id}_{old_rev}")
        stale_inv_id = f"{order.id}_{old_rev}"
        parts = stale_inv_id.split("_", 1)
        cb_revision = int(parts[1])

        # The endpoint logic: if cb_revision < current revision → stale, acknowledge
        assert cb_revision < new_rev, "Callback revision must be older than current"
        # The endpoint returns OK{InvId} to stop Robokassa retries
