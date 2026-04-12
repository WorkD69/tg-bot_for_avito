"""AnalyticsRepo — funnel, source attribution, campaign attribution, and business health queries.

All queries are read-only aggregations over existing tables.
No separate analytics_events table — order_logs is the source of truth for
the order funnel; users.created_at and users.source cover registration metrics.

Funnel stages (in order):
  users_registered   → users.created_at
  orders_created     → order_logs WHERE action='created'
  orders_got_bid     → DISTINCT order_ids WHERE action='bid_placed'
  orders_assigned    → DISTINCT order_ids WHERE action='operator_assigned'
  orders_pay_confirmed → DISTINCT order_ids WHERE action='payment_confirmed'
  orders_completed   → DISTINCT order_ids WHERE action='completed'
  orders_cancelled   → DISTINCT order_ids WHERE action='cancelled'
  orders_disputed    → DISTINCT order_ids WHERE action='dispute_opened'
  reviews_left       → reviews.created_at
"""
from __future__ import annotations

from datetime import datetime
from decimal import Decimal

from sqlalchemy import case, distinct, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models.operator_earning import EarningStatus, OperatorEarning
from app.db.models.order import Order
from app.db.models.order_log import OrderLog, OrderLogAction
from app.db.models.review import Review
from app.db.models.user import User


class AnalyticsRepo:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    # ── Funnel ────────────────────────────────────────────────────────────────

    async def get_funnel(self, since: datetime | None = None) -> dict:
        """Return absolute counts for each funnel stage.

        'since' filters by the event's created_at timestamp.
        All counts are independent (not cumulative sub-sets) to allow
        accurate conversion calculation per stage.
        """
        # 1. Users registered
        q_users = select(func.count(User.id))
        if since:
            q_users = q_users.where(User.created_at >= since)
        users_registered = (await self.session.execute(q_users)).scalar_one()

        # 2-8. Order log-based stages
        def _log_count(action: OrderLogAction, distinct_orders: bool = True):
            """Count distinct orders (or log rows) for a given action, optionally filtered by since."""
            col = distinct(OrderLog.order_id) if distinct_orders else OrderLog.id
            q = select(func.count(col)).where(OrderLog.action == action)
            if since:
                q = q.where(OrderLog.created_at >= since)
            return q

        orders_created = (await self.session.execute(_log_count(OrderLogAction.created))).scalar_one()
        orders_got_bid = (await self.session.execute(_log_count(OrderLogAction.bid_placed))).scalar_one()
        orders_assigned = (await self.session.execute(_log_count(OrderLogAction.operator_assigned))).scalar_one()
        orders_pay_confirmed = (await self.session.execute(_log_count(OrderLogAction.payment_confirmed))).scalar_one()
        orders_completed = (await self.session.execute(_log_count(OrderLogAction.completed))).scalar_one()
        orders_cancelled = (await self.session.execute(_log_count(OrderLogAction.cancelled))).scalar_one()
        orders_disputed = (await self.session.execute(_log_count(OrderLogAction.dispute_opened))).scalar_one()

        # 9. Reviews
        q_reviews = select(func.count(Review.id))
        if since:
            q_reviews = q_reviews.where(Review.created_at >= since)
        reviews_left = (await self.session.execute(q_reviews)).scalar_one()

        return {
            "users_registered": users_registered,
            "orders_created": orders_created,
            "orders_got_bid": orders_got_bid,
            "orders_assigned": orders_assigned,
            "orders_pay_confirmed": orders_pay_confirmed,
            "orders_completed": orders_completed,
            "orders_cancelled": orders_cancelled,
            "orders_disputed": orders_disputed,
            "reviews_left": reviews_left,
        }

    # ── Source Attribution ─────────────────────────────────────────────────────

    async def get_source_breakdown(self, since: datetime | None = None) -> list[dict]:
        """Return user count grouped by source, ordered by count desc."""
        q = select(User.source, func.count(User.id).label("count")).group_by(User.source)
        if since:
            q = q.where(User.created_at >= since)
        result = await self.session.execute(q)
        rows = result.all()
        total = sum(r.count for r in rows)
        return [
            {
                "source": r.source,
                "count": r.count,
                "pct": round(r.count / total * 100, 1) if total else 0.0,
            }
            for r in sorted(rows, key=lambda r: r.count, reverse=True)
        ]

    # ── Source/Campaign Funnel ─────────────────────────────────────────────────

    async def get_source_funnel(self, since: datetime | None = None) -> list[dict]:
        """Return conversion funnel metrics grouped by source.

        Each row: source, registered, orders_created, orders_paid,
                  orders_completed, gross_revenue, completion_rate
        """
        return await self._attribution_funnel(
            group_cols=[User.source],
            row_builder=lambda key: {"source": key[0], "campaign": None},
            key_fn=lambda row_map, r: (r.source,),
            source_filter=None,
            since=since,
        )

    async def get_campaign_funnel(
        self,
        source_filter: str | None = None,
        since: datetime | None = None,
    ) -> list[dict]:
        """Return conversion funnel metrics grouped by (source, campaign).

        If source_filter is given, only rows for that source are returned.
        Rows with campaign=NULL appear as campaign='(none)' for display.
        """
        return await self._attribution_funnel(
            group_cols=[User.source, User.campaign],
            row_builder=lambda key: {"source": key[0], "campaign": key[1]},
            key_fn=lambda row_map, r: (r.source, r.campaign),
            source_filter=source_filter,
            since=since,
        )

    async def _attribution_funnel(
        self,
        group_cols: list,
        row_builder,
        key_fn,
        source_filter: str | None,
        since: datetime | None,
    ) -> list[dict]:
        """Generic engine for source or campaign funnel aggregation."""

        def _where_source(q):
            if source_filter is not None:
                q = q.where(User.source == source_filter)
            return q

        # 1. Registrations
        q_reg = select(*group_cols, func.count(User.id).label("cnt")).group_by(*group_cols)
        if since:
            q_reg = q_reg.where(User.created_at >= since)
        q_reg = _where_source(q_reg)
        reg_rows = (await self.session.execute(q_reg)).all()

        data: dict[tuple, dict] = {}
        for r in reg_rows:
            key = key_fn(data, r)
            data[key] = {
                **row_builder(key),
                "registered": r.cnt,
                "orders_created": 0,
                "orders_paid": 0,
                "orders_completed": 0,
                "gross_revenue": Decimal("0"),
            }

        # Helper: order log count grouped by the same dimensions
        def _log_q(action: OrderLogAction):
            q = (
                select(*group_cols, func.count(distinct(OrderLog.order_id)).label("cnt"))
                .select_from(OrderLog)
                .join(Order, Order.id == OrderLog.order_id)
                .join(User, User.id == Order.client_id)
                .where(OrderLog.action == action)
                .group_by(*group_cols)
            )
            if since:
                q = q.where(OrderLog.created_at >= since)
            return _where_source(q)

        for action, field in [
            (OrderLogAction.created, "orders_created"),
            (OrderLogAction.payment_confirmed, "orders_paid"),
            (OrderLogAction.completed, "orders_completed"),
        ]:
            for r in (await self.session.execute(_log_q(action))).all():
                key = key_fn(data, r)
                if key in data:
                    data[key][field] = r.cnt

        # 2. Gross revenue
        q_gross = (
            select(*group_cols, func.coalesce(func.sum(OperatorEarning.gross_amount), 0).label("gross"))
            .select_from(OperatorEarning)
            .join(Order, Order.id == OperatorEarning.order_id)
            .join(User, User.id == Order.client_id)
            .group_by(*group_cols)
        )
        if since:
            q_gross = q_gross.where(OperatorEarning.created_at >= since)
        q_gross = _where_source(q_gross)
        for r in (await self.session.execute(q_gross)).all():
            key = key_fn(data, r)
            if key in data:
                data[key]["gross_revenue"] = r.gross

        # Compute completion_rate and sort
        rows = list(data.values())
        for row in rows:
            created = row["orders_created"]
            completed = row["orders_completed"]
            row["completion_rate"] = (
                round(completed / created * 100, 1) if created else 0.0
            )

        return sorted(rows, key=lambda r: r["registered"], reverse=True)

    # ── Business Summary ───────────────────────────────────────────────────────

    async def get_business_summary(self, since: datetime | None = None) -> dict:
        """Holistic business snapshot combining orders + payout data.

        Designed for the expanded /stats command.
        """
        # Order counts by status
        q_orders = select(Order.status, func.count(Order.id)).group_by(Order.status)
        if since:
            q_orders = q_orders.where(Order.created_at >= since)
        order_rows = (await self.session.execute(q_orders)).all()
        orders_by_status = {str(s.value): c for s, c in order_rows}

        # Total revenue (gross) from completed orders (via operator_earnings)
        q_gross = select(func.coalesce(func.sum(OperatorEarning.gross_amount), 0))
        if since:
            q_gross = q_gross.where(OperatorEarning.created_at >= since)
        total_gross = (await self.session.execute(q_gross)).scalar_one()

        # Payout snapshot
        q_pending_sum = select(func.coalesce(func.sum(OperatorEarning.operator_share), 0)).where(
            OperatorEarning.status.in_([EarningStatus.pending, EarningStatus.adjusted])
        )
        q_paid_sum = select(func.coalesce(func.sum(OperatorEarning.operator_share), 0)).where(
            OperatorEarning.status == EarningStatus.paid
        )
        q_on_hold_count = select(func.count(OperatorEarning.id)).where(
            OperatorEarning.status == EarningStatus.on_hold
        )
        q_on_hold_sum = select(func.coalesce(func.sum(OperatorEarning.operator_share), 0)).where(
            OperatorEarning.status == EarningStatus.on_hold
        )

        payable_sum = (await self.session.execute(q_pending_sum)).scalar_one()
        paid_sum = (await self.session.execute(q_paid_sum)).scalar_one()
        on_hold_count = (await self.session.execute(q_on_hold_count)).scalar_one()
        on_hold_sum = (await self.session.execute(q_on_hold_sum)).scalar_one()

        # Cancellation breakdown by who cancelled
        q_cancel = (
            select(Order.cancelled_by, func.count(Order.id).label("cnt"))
            .where(Order.cancelled_by.isnot(None))
            .group_by(Order.cancelled_by)
        )
        if since:
            q_cancel = q_cancel.where(Order.created_at >= since)
        cancel_rows = (await self.session.execute(q_cancel)).all()
        cancellations = {(r.cancelled_by or "unknown"): r.cnt for r in cancel_rows}

        # Follow-up growth stats: how many follow-up messages have been sent
        q_followups = select(func.count(Order.id)).where(Order.followup_sent_at.isnot(None))
        if since:
            q_followups = q_followups.where(Order.followup_sent_at >= since)
        followups_sent = (await self.session.execute(q_followups)).scalar_one()

        return {
            "orders_by_status": orders_by_status,
            "total_gross": total_gross,
            "payable_sum": payable_sum,
            "paid_sum": paid_sum,
            "on_hold_count": on_hold_count,
            "on_hold_sum": on_hold_sum,
            "cancellations": cancellations,
            "followups_sent": followups_sent,
        }
