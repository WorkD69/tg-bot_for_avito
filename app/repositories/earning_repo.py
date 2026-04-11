"""EarningRepo — CRUD for operator_earnings table.

One OperatorEarning row per completed order. Tracks gross amount, operator share,
payout status, and audit trail (who paid, when, why adjusted).
"""
from datetime import datetime, timezone
from decimal import Decimal, ROUND_HALF_UP

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.db.models.operator_earning import EarningStatus, OperatorEarning


class EarningRepo:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    # ── Creation ──────────────────────────────────────────────────────────────

    async def create_for_order(
        self,
        order_id: int,
        operator_id: int,
        gross_amount: Decimal,
        payout_percent: float,
    ) -> OperatorEarning:
        """Create an earning record when an order completes.

        Idempotent: if an earning already exists for this order_id, returns it unchanged.
        (Guard against double-completion bugs.)
        """
        existing = await self.get_by_order_id(order_id)
        if existing:
            return existing

        pct = Decimal(str(payout_percent))
        share = (gross_amount * pct / 100).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)

        earning = OperatorEarning(
            order_id=order_id,
            operator_id=operator_id,
            gross_amount=gross_amount,
            operator_share=share,
            payout_percent=pct,
            status=EarningStatus.pending,
        )
        self.session.add(earning)
        await self.session.flush()
        return earning

    # ── Lookups ───────────────────────────────────────────────────────────────

    async def get_by_order_id(self, order_id: int) -> OperatorEarning | None:
        result = await self.session.execute(
            select(OperatorEarning).where(OperatorEarning.order_id == order_id)
        )
        return result.scalar_one_or_none()

    async def get_by_operator(
        self,
        operator_id: int,
        status: EarningStatus | None = None,
        since: datetime | None = None,
    ) -> list[OperatorEarning]:
        """Return earnings for an operator, optionally filtered by status and date."""
        q = (
            select(OperatorEarning)
            .where(OperatorEarning.operator_id == operator_id)
            .options(selectinload(OperatorEarning.order))
            .order_by(OperatorEarning.created_at.desc())
        )
        if status is not None:
            q = q.where(OperatorEarning.status == status)
        if since is not None:
            q = q.where(OperatorEarning.created_at >= since)
        result = await self.session.execute(q)
        return list(result.scalars().all())

    async def get_all_pending(self) -> list[OperatorEarning]:
        """All pending earnings across all operators (for /payouts overview)."""
        result = await self.session.execute(
            select(OperatorEarning)
            .where(OperatorEarning.status == EarningStatus.pending)
            .options(selectinload(OperatorEarning.operator), selectinload(OperatorEarning.order))
            .order_by(OperatorEarning.operator_id, OperatorEarning.created_at)
        )
        return list(result.scalars().all())

    async def get_summary_by_operator(
        self, operator_id: int, since: datetime | None = None
    ) -> dict:
        """Aggregate stats for one operator.

        Returns dict with:
          total_completed  — count of all completed orders (earnings exist)
          total_gross      — sum of gross_amount
          total_share      — sum of operator_share
          pending_count    — count of pending earnings
          pending_sum      — sum of pending operator_share
          paid_sum         — sum of paid operator_share
          excluded_count   — count of excluded earnings
        """
        base = select(
            func.count(OperatorEarning.id).label("total"),
            func.coalesce(func.sum(OperatorEarning.gross_amount), 0).label("gross"),
            func.coalesce(func.sum(OperatorEarning.operator_share), 0).label("share"),
        ).where(OperatorEarning.operator_id == operator_id)

        if since:
            base = base.where(OperatorEarning.created_at >= since)

        total_row = (await self.session.execute(base)).one()

        # pending
        pq = select(
            func.count(OperatorEarning.id).label("cnt"),
            func.coalesce(func.sum(OperatorEarning.operator_share), 0).label("s"),
        ).where(
            OperatorEarning.operator_id == operator_id,
            OperatorEarning.status == EarningStatus.pending,
        )
        if since:
            pq = pq.where(OperatorEarning.created_at >= since)
        pending_row = (await self.session.execute(pq)).one()

        # paid
        paid_q = select(
            func.coalesce(func.sum(OperatorEarning.operator_share), 0).label("s"),
        ).where(
            OperatorEarning.operator_id == operator_id,
            OperatorEarning.status == EarningStatus.paid,
        )
        if since:
            paid_q = paid_q.where(OperatorEarning.created_at >= since)
        paid_row = (await self.session.execute(paid_q)).one()

        # excluded
        excl_q = select(func.count(OperatorEarning.id).label("cnt")).where(
            OperatorEarning.operator_id == operator_id,
            OperatorEarning.status == EarningStatus.excluded,
        )
        if since:
            excl_q = excl_q.where(OperatorEarning.created_at >= since)
        excl_row = (await self.session.execute(excl_q)).one()

        return {
            "total_completed": int(total_row.total),
            "total_gross": Decimal(str(total_row.gross)),
            "total_share": Decimal(str(total_row.share)),
            "pending_count": int(pending_row.cnt),
            "pending_sum": Decimal(str(pending_row.s)),
            "paid_sum": Decimal(str(paid_row.s)),
            "excluded_count": int(excl_row.cnt),
        }

    # ── Status mutations ──────────────────────────────────────────────────────

    async def mark_paid(
        self,
        earning: OperatorEarning,
        paid_by_id: int,
        note: str | None = None,
    ) -> None:
        earning.status = EarningStatus.paid
        earning.paid_at = datetime.now(timezone.utc)
        earning.paid_by_id = paid_by_id
        if note:
            earning.note = note
        await self.session.flush()

    async def mark_paid_all_pending(
        self,
        operator_id: int,
        paid_by_id: int,
        note: str | None = None,
    ) -> list[OperatorEarning]:
        """Mark ALL pending earnings for an operator as paid. Returns updated rows."""
        pending = await self.get_by_operator(operator_id, status=EarningStatus.pending)
        for e in pending:
            await self.mark_paid(e, paid_by_id, note)
        return pending

    async def exclude(
        self,
        earning: OperatorEarning,
        note: str | None = None,
    ) -> None:
        earning.status = EarningStatus.excluded
        earning.note = note
        await self.session.flush()

    async def adjust(
        self,
        earning: OperatorEarning,
        new_share: Decimal,
        note: str | None = None,
    ) -> None:
        """Manually override operator_share. Sets status=adjusted so it's auditable."""
        earning.operator_share = new_share
        earning.status = EarningStatus.adjusted
        earning.note = note
        await self.session.flush()
