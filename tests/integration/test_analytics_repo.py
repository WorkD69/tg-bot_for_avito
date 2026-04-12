"""Integration tests for AnalyticsRepo.

Tests:
  A. Funnel counts — each stage returns correct counts from order_logs
  B. Funnel period filter — since param correctly limits results
  C. Source breakdown — users grouped by source with correct pcts
  D. Business summary — orders by status + payout snapshot
  E. Funnel + payout coherence — completed orders appear in both layers

Requires a running PostgreSQL — see tests/integration/conftest.py.
"""
from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest

pytestmark = pytest.mark.asyncio(loop_scope="module")

from app.db.models.order import OrderStatus
from app.db.models.order_log import OrderLogAction
from app.db.models.user import UserRole
from app.repositories.analytics_repo import AnalyticsRepo
from app.repositories.order_repo import OrderRepo

from tests.integration.conftest import make_order, make_user


# ── Helpers ───────────────────────────────────────────────────────────────────

async def _add_log(session, order_id: int, action: OrderLogAction, actor_id: int | None = None):
    from app.db.models.order_log import OrderLog
    log = OrderLog(order_id=order_id, actor_id=actor_id, action=action)
    session.add(log)
    await session.flush()


async def _make_user_with_source(session, tg_id: int, name: str, source: str = "unknown", role=None):
    from app.db.models.user import User, UserRole as UR
    user = User(
        telegram_id=tg_id,
        full_name=name,
        role=role or UR.client,
        source=source,
    )
    session.add(user)
    await session.flush()
    return user


# ── A. Funnel counts ──────────────────────────────────────────────────────────

class TestFunnelCounts:

    async def test_users_registered_count(self, session):
        """users_registered counts rows in users table."""
        # Make 2 users
        await _make_user_with_source(session, 80001, "Ana")
        await _make_user_with_source(session, 80002, "Bob")
        await session.commit()

        repo = AnalyticsRepo(session)
        funnel = await repo.get_funnel()
        # At least 2 users registered (other tests may have added more)
        assert funnel["users_registered"] >= 2

    async def test_orders_created_via_log(self, session):
        """orders_created counts order_log entries with action=created."""
        client = await make_user(session, 80010, "Client A")
        order = await make_order(session, client.id)
        await _add_log(session, order.id, OrderLogAction.created, client.id)
        await session.commit()

        repo = AnalyticsRepo(session)
        funnel = await repo.get_funnel()
        assert funnel["orders_created"] >= 1

    async def test_orders_got_bid(self, session):
        """orders_got_bid counts distinct orders with bid_placed log."""
        client = await make_user(session, 80020, "Client B")
        op = await make_user(session, 80021, "Op B", UserRole.operator)
        order = await make_order(session, client.id)
        await _add_log(session, order.id, OrderLogAction.created, client.id)
        await _add_log(session, order.id, OrderLogAction.bid_placed, op.id)
        # Two bid logs on same order → still counts as 1 distinct order
        await _add_log(session, order.id, OrderLogAction.bid_placed, op.id)
        await session.commit()

        repo = AnalyticsRepo(session)
        funnel = await repo.get_funnel()
        assert funnel["orders_got_bid"] >= 1

    async def test_orders_completed(self, session):
        """orders_completed counts distinct orders with completed log."""
        client = await make_user(session, 80030, "Client C")
        order = await make_order(session, client.id)
        await _add_log(session, order.id, OrderLogAction.created, client.id)
        await _add_log(session, order.id, OrderLogAction.completed)
        await session.commit()

        repo = AnalyticsRepo(session)
        funnel = await repo.get_funnel()
        assert funnel["orders_completed"] >= 1

    async def test_orders_cancelled(self, session):
        """orders_cancelled counts distinct orders with cancelled log."""
        client = await make_user(session, 80040, "Client D")
        order = await make_order(session, client.id)
        await _add_log(session, order.id, OrderLogAction.created, client.id)
        await _add_log(session, order.id, OrderLogAction.cancelled)
        await session.commit()

        repo = AnalyticsRepo(session)
        funnel = await repo.get_funnel()
        assert funnel["orders_cancelled"] >= 1

    async def test_orders_disputed(self, session):
        """orders_disputed counts dispute_opened log entries."""
        client = await make_user(session, 80050, "Client E")
        order = await make_order(session, client.id)
        await _add_log(session, order.id, OrderLogAction.dispute_opened)
        await session.commit()

        repo = AnalyticsRepo(session)
        funnel = await repo.get_funnel()
        assert funnel["orders_disputed"] >= 1

    async def test_funnel_keys_present(self, session):
        """All expected funnel keys are always returned."""
        repo = AnalyticsRepo(session)
        funnel = await repo.get_funnel()
        expected_keys = {
            "users_registered", "orders_created", "orders_got_bid",
            "orders_assigned", "orders_pay_confirmed", "orders_completed",
            "orders_cancelled", "orders_disputed", "reviews_left",
        }
        assert expected_keys.issubset(funnel.keys())

    async def test_funnel_all_values_non_negative(self, session):
        """No funnel stage can return negative numbers."""
        repo = AnalyticsRepo(session)
        funnel = await repo.get_funnel()
        for key, val in funnel.items():
            assert val >= 0, f"Funnel stage '{key}' returned negative value: {val}"


# ── B. Funnel period filter ────────────────────────────────────────────────────

class TestFunnelPeriodFilter:

    async def test_since_future_returns_zeros_for_logs(self, session):
        """A since value in the future should return 0 for log-based stages."""
        future = datetime.now(timezone.utc) + timedelta(days=365)
        repo = AnalyticsRepo(session)
        funnel = await repo.get_funnel(since=future)

        assert funnel["orders_created"] == 0
        assert funnel["orders_completed"] == 0
        assert funnel["orders_cancelled"] == 0

    async def test_since_epoch_returns_all(self, session):
        """A since value far in the past should include all records."""
        from datetime import datetime
        ancient = datetime(2000, 1, 1, tzinfo=timezone.utc)
        repo = AnalyticsRepo(session)
        funnel_all = await repo.get_funnel()
        funnel_ancient = await repo.get_funnel(since=ancient)

        # Both should return same counts (all data is after year 2000)
        assert funnel_ancient["orders_completed"] == funnel_all["orders_completed"]
        assert funnel_ancient["users_registered"] == funnel_all["users_registered"]


# ── C. Source breakdown ────────────────────────────────────────────────────────

class TestSourceBreakdown:

    async def test_source_counts_by_value(self, session):
        """Users grouped by source field correctly."""
        await _make_user_with_source(session, 80100, "Avito User 1", "avito")
        await _make_user_with_source(session, 80101, "Avito User 2", "avito")
        await _make_user_with_source(session, 80102, "TG User", "tg_channel")
        await session.commit()

        repo = AnalyticsRepo(session)
        rows = await repo.get_source_breakdown()

        source_map = {r["source"]: r["count"] for r in rows}
        assert source_map.get("avito", 0) >= 2
        assert source_map.get("tg_channel", 0) >= 1

    async def test_source_percentages_sum_to_100(self, session):
        """Percentages across all sources sum to ~100%."""
        await _make_user_with_source(session, 80110, "Pct User 1", "avito")
        await _make_user_with_source(session, 80111, "Pct User 2", "tg_channel")
        await session.commit()

        repo = AnalyticsRepo(session)
        rows = await repo.get_source_breakdown()
        assert rows, "Expected at least 2 sources"

        total_pct = sum(r["pct"] for r in rows)
        assert abs(total_pct - 100.0) < 1.0, f"Percentages sum to {total_pct}, expected ~100"

    async def test_source_sorted_by_count_desc(self, session):
        """Source breakdown is sorted by count descending."""
        repo = AnalyticsRepo(session)
        rows = await repo.get_source_breakdown()

        counts = [r["count"] for r in rows]
        assert counts == sorted(counts, reverse=True)

    async def test_source_result_keys(self, session):
        """Each row has source, count, pct keys."""
        await _make_user_with_source(session, 80120, "Keys User", "avito")
        await session.commit()

        repo = AnalyticsRepo(session)
        rows = await repo.get_source_breakdown()
        assert rows, "Expected at least 1 source row"

        for r in rows:
            assert "source" in r
            assert "count" in r
            assert "pct" in r

    async def test_source_future_since_returns_empty(self, session):
        """Period filter in future returns empty list."""
        future = datetime.now(timezone.utc) + timedelta(days=365)
        repo = AnalyticsRepo(session)
        rows = await repo.get_source_breakdown(since=future)
        assert rows == []


# ── D. Business summary ────────────────────────────────────────────────────────

class TestBusinessSummary:

    async def test_summary_keys_present(self, session):
        """get_business_summary returns all expected keys."""
        repo = AnalyticsRepo(session)
        summary = await repo.get_business_summary()

        expected = {
            "orders_by_status", "total_gross", "payable_sum",
            "paid_sum", "on_hold_count", "on_hold_sum", "cancellations",
        }
        assert expected.issubset(summary.keys())

    async def test_summary_monetary_values_non_negative(self, session):
        """All monetary aggregate values are >= 0."""
        repo = AnalyticsRepo(session)
        summary = await repo.get_business_summary()

        for key in ("total_gross", "payable_sum", "paid_sum", "on_hold_sum"):
            assert summary[key] >= 0, f"'{key}' returned negative value: {summary[key]}"

    async def test_summary_on_hold_count_non_negative(self, session):
        repo = AnalyticsRepo(session)
        summary = await repo.get_business_summary()
        assert summary["on_hold_count"] >= 0

    async def test_orders_by_status_is_dict(self, session):
        """orders_by_status maps status value strings to counts."""
        repo = AnalyticsRepo(session)
        summary = await repo.get_business_summary()
        assert isinstance(summary["orders_by_status"], dict)

    async def test_summary_with_earning_reflects_gross(self, session):
        """After creating a completed order + earning, total_gross increases."""
        from app.repositories.earning_repo import EarningRepo

        repo = AnalyticsRepo(session)
        summary_before = await repo.get_business_summary()
        gross_before = summary_before["total_gross"]

        client = await make_user(session, 80200, "SummaryClient")
        op = await make_user(session, 80201, "SummaryOp", UserRole.operator)
        order = await make_order(session, client.id)
        order_repo = OrderRepo(session)
        await order_repo.assign_operator(order, op.id, Decimal("3000"))
        await order_repo.confirm_payment(order)
        await order_repo.update_status(order, OrderStatus.completed)
        await EarningRepo(session).create_for_order(
            order_id=order.id, operator_id=op.id,
            gross_amount=Decimal("3000"), payout_percent=80.0,
        )
        await session.commit()

        summary_after = await repo.get_business_summary()
        assert summary_after["total_gross"] >= gross_before + Decimal("3000")


# ── E. Source Funnel ──────────────────────────────────────────────────────────

async def _make_user_attributed(
    session,
    tg_id: int,
    name: str,
    source: str = "unknown",
    campaign: str | None = None,
):
    from app.db.models.user import User, UserRole
    user = User(
        telegram_id=tg_id,
        full_name=name,
        role=UserRole.client,
        source=source,
        campaign=campaign,
    )
    session.add(user)
    await session.flush()
    return user


class TestSourceFunnel:

    async def test_source_funnel_returns_list(self, session):
        """get_source_funnel returns a list (possibly empty if no users)."""
        repo = AnalyticsRepo(session)
        rows = await repo.get_source_funnel()
        assert isinstance(rows, list)

    async def test_source_funnel_has_required_keys(self, session):
        """Each row contains all required keys."""
        await _make_user_attributed(session, 90001, "SF User", "avito")
        await session.commit()

        repo = AnalyticsRepo(session)
        rows = await repo.get_source_funnel()
        assert rows, "Expected at least one source row"
        for row in rows:
            for key in ("source", "registered", "orders_created", "orders_paid",
                        "orders_completed", "gross_revenue", "completion_rate"):
                assert key in row, f"Missing key '{key}' in row: {row}"

    async def test_source_funnel_counts_registrations(self, session):
        """Registrations are counted per source."""
        await _make_user_attributed(session, 90010, "Avito U1", "avito")
        await _make_user_attributed(session, 90011, "Avito U2", "avito")
        await _make_user_attributed(session, 90012, "TG U1", "tg_channel")
        await session.commit()

        repo = AnalyticsRepo(session)
        rows = await repo.get_source_funnel()
        by_source = {r["source"]: r for r in rows}

        assert by_source.get("avito", {}).get("registered", 0) >= 2
        assert by_source.get("tg_channel", {}).get("registered", 0) >= 1

    async def test_source_funnel_orders_created(self, session):
        """orders_created is counted from order_logs for the client's source."""
        client = await _make_user_attributed(session, 90020, "Avito Client", "avito")
        order = await make_order(session, client.id)
        await _add_log(session, order.id, OrderLogAction.created, client.id)
        await session.commit()

        repo = AnalyticsRepo(session)
        rows = await repo.get_source_funnel()
        by_source = {r["source"]: r for r in rows}
        assert by_source.get("avito", {}).get("orders_created", 0) >= 1

    async def test_source_funnel_gross_revenue(self, session):
        """gross_revenue sums operator_earnings for orders from users of that source."""
        from app.repositories.earning_repo import EarningRepo
        from app.db.models.user import UserRole

        client = await _make_user_attributed(session, 90030, "AvClient Revenue", "avito")
        op = await make_user(session, 90031, "OpRev", UserRole.operator)
        order = await make_order(session, client.id)
        order_repo = OrderRepo(session)
        await order_repo.assign_operator(order, op.id, Decimal("5000"))
        await order_repo.confirm_payment(order)
        await order_repo.update_status(order, OrderStatus.completed)
        await EarningRepo(session).create_for_order(
            order_id=order.id, operator_id=op.id,
            gross_amount=Decimal("5000"), payout_percent=80.0,
        )
        await session.commit()

        repo = AnalyticsRepo(session)
        rows = await repo.get_source_funnel()
        by_source = {r["source"]: r for r in rows}
        assert by_source.get("avito", {}).get("gross_revenue", Decimal("0")) >= Decimal("5000")

    async def test_source_funnel_period_filter(self, session):
        """since filter returns zero results when set to future."""
        future = datetime.now(timezone.utc) + timedelta(days=365)
        repo = AnalyticsRepo(session)
        rows = await repo.get_source_funnel(since=future)
        assert rows == []

    async def test_source_funnel_sorted_by_registered_desc(self, session):
        """Rows sorted by registered count descending."""
        repo = AnalyticsRepo(session)
        rows = await repo.get_source_funnel()
        counts = [r["registered"] for r in rows]
        assert counts == sorted(counts, reverse=True)

    async def test_source_funnel_completion_rate_zero_when_no_orders(self, session):
        """completion_rate is 0.0 when orders_created = 0."""
        await _make_user_attributed(session, 90040, "NoOrder User", "direct")
        await session.commit()

        repo = AnalyticsRepo(session)
        rows = await repo.get_source_funnel()
        by_source = {r["source"]: r for r in rows}
        direct = by_source.get("direct")
        if direct and direct["orders_created"] == 0:
            assert direct["completion_rate"] == 0.0


# ── F. Campaign Funnel ────────────────────────────────────────────────────────

class TestCampaignFunnel:

    async def test_campaign_funnel_returns_list(self, session):
        """get_campaign_funnel returns a list."""
        repo = AnalyticsRepo(session)
        rows = await repo.get_campaign_funnel()
        assert isinstance(rows, list)

    async def test_campaign_funnel_has_required_keys(self, session):
        """Each row has source, campaign, and all funnel keys."""
        await _make_user_attributed(session, 91001, "Camp User", "avito", "ad1")
        await session.commit()

        repo = AnalyticsRepo(session)
        rows = await repo.get_campaign_funnel()
        assert rows
        for row in rows:
            for key in ("source", "campaign", "registered", "orders_created",
                        "orders_paid", "orders_completed", "gross_revenue", "completion_rate"):
                assert key in row, f"Missing key '{key}'"

    async def test_campaign_funnel_groups_by_campaign(self, session):
        """Two different campaigns within same source appear as separate rows."""
        await _make_user_attributed(session, 91010, "Ad1 U1", "avito", "ad1")
        await _make_user_attributed(session, 91011, "Ad1 U2", "avito", "ad1")
        await _make_user_attributed(session, 91012, "Ad2 U1", "avito", "ad2")
        await session.commit()

        repo = AnalyticsRepo(session)
        rows = await repo.get_campaign_funnel(source_filter="avito")

        avito_rows = {r["campaign"]: r for r in rows if r["source"] == "avito"}
        assert avito_rows.get("ad1", {}).get("registered", 0) >= 2
        assert avito_rows.get("ad2", {}).get("registered", 0) >= 1

    async def test_campaign_funnel_source_filter(self, session):
        """source_filter limits results to that source only."""
        await _make_user_attributed(session, 91020, "TG Camp", "tg_channel", "p1")
        await session.commit()

        repo = AnalyticsRepo(session)
        rows = await repo.get_campaign_funnel(source_filter="avito")
        sources = {r["source"] for r in rows}
        assert "tg_channel" not in sources

    async def test_campaign_funnel_null_campaign(self, session):
        """Users without campaign appear with campaign=None."""
        await _make_user_attributed(session, 91030, "NoCamp", "avito", None)
        await session.commit()

        repo = AnalyticsRepo(session)
        rows = await repo.get_campaign_funnel(source_filter="avito")
        campaigns = [r["campaign"] for r in rows if r["source"] == "avito"]
        assert None in campaigns

    async def test_campaign_funnel_period_filter_future(self, session):
        """since in future returns empty list."""
        future = datetime.now(timezone.utc) + timedelta(days=365)
        repo = AnalyticsRepo(session)
        rows = await repo.get_campaign_funnel(since=future)
        assert rows == []
