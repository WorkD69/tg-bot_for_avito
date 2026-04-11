"""EarningRepo — CRUD for operator_earnings table.

One OperatorEarning row per completed order. Tracks gross amount, operator share,
payout status, and full audit trail (who paid/froze/adjusted, when, why).

Safety contract
---------------
PAYABLE_STATUSES = (pending, adjusted) — the ONLY statuses included in /payouts and /markpaid.
on_hold earnings are NEVER auto-paid. They must be resolved first via /unfreezeearning or
/excludeearning before they can participate in a payout.
"""
from datetime import datetime, timezone
from decimal import Decimal, ROUND_HALF_UP

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.db.models.operator_earning import EarningStatus, OperatorEarning, PAYABLE_STATUSES


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

    async def get_payable(
        self,
        operator_id: int,
        since: datetime | None = None,
    ) -> list[OperatorEarning]:
        """Return ONLY payable earnings (pending + adjusted) for an operator.

        This is the safe query for /payouts and /markpaid.
        on_hold and excluded are never returned here.
        """
        q = (
            select(OperatorEarning)
            .where(
                OperatorEarning.operator_id == operator_id,
                OperatorEarning.status.in_(PAYABLE_STATUSES),
            )
            .options(selectinload(OperatorEarning.order))
            .order_by(OperatorEarning.created_at.desc())
        )
        if since is not None:
            q = q.where(OperatorEarning.created_at >= since)
        result = await self.session.execute(q)
        return list(result.scalars().all())

    async def get_on_hold(self, operator_id: int | None = None) -> list[OperatorEarning]:
        """Return all on_hold earnings. Optionally filtered to one operator."""
        q = (
            select(OperatorEarning)
            .where(OperatorEarning.status == EarningStatus.on_hold)
            .options(
                selectinload(OperatorEarning.operator),
                selectinload(OperatorEarning.order),
            )
            .order_by(OperatorEarning.frozen_at.desc())
        )
        if operator_id is not None:
            q = q.where(OperatorEarning.operator_id == operator_id)
        result = await self.session.execute(q)
        return list(result.scalars().all())

    async def get_all_payable(self) -> list[OperatorEarning]:
        """All payable earnings across all operators (for /payouts overview).

        Includes pending + adjusted. Explicitly excludes on_hold and excluded.
        """
        result = await self.session.execute(
            select(OperatorEarning)
            .where(OperatorEarning.status.in_(PAYABLE_STATUSES))
            .options(selectinload(OperatorEarning.operator), selectinload(OperatorEarning.order))
            .order_by(OperatorEarning.operator_id, OperatorEarning.created_at)
        )
        return list(result.scalars().all())

    async def get_summary_by_operator(
        self, operator_id: int, since: datetime | None = None
    ) -> dict:
        """Aggregate stats for one operator.

        Returns dict with:
          total_completed  — count of all earnings (any status)
          total_gross      — sum of gross_amount (all statuses)
          total_share      — sum of operator_share (all statuses, for reference)
          payable_count    — count of pending + adjusted (safe-to-pay)
          payable_sum      — sum of operator_share for payable earnings
          paid_sum         — sum of paid operator_share
          on_hold_count    — count of frozen earnings (requires manual review)
          on_hold_sum      — sum of operator_share for on_hold earnings
          excluded_count   — count of excluded earnings
          adjusted_count   — count of adjusted earnings (included in payable)
        """
        base = select(
            func.count(OperatorEarning.id).label("total"),
            func.coalesce(func.sum(OperatorEarning.gross_amount), 0).label("gross"),
            func.coalesce(func.sum(OperatorEarning.operator_share), 0).label("share"),
        ).where(OperatorEarning.operator_id == operator_id)
        if since:
            base = base.where(OperatorEarning.created_at >= since)
        total_row = (await self.session.execute(base)).one()

        async def _agg(statuses: tuple, sum_col=True) -> tuple:
            """Return (count, sum) for given status filter."""
            cols = [func.count(OperatorEarning.id).label("cnt")]
            if sum_col:
                cols.append(func.coalesce(func.sum(OperatorEarning.operator_share), 0).label("s"))
            q = select(*cols).where(
                OperatorEarning.operator_id == operator_id,
                OperatorEarning.status.in_(statuses),
            )
            if since:
                q = q.where(OperatorEarning.created_at >= since)
            row = (await self.session.execute(q)).one()
            if sum_col:
                return int(row.cnt), Decimal(str(row.s))
            return int(row.cnt), Decimal(0)

        payable_cnt, payable_sum = await _agg(PAYABLE_STATUSES)
        paid_cnt, paid_sum = await _agg((EarningStatus.paid,))
        on_hold_cnt, on_hold_sum = await _agg((EarningStatus.on_hold,))
        excluded_cnt, _ = await _agg((EarningStatus.excluded,), sum_col=False)
        adjusted_cnt, _ = await _agg((EarningStatus.adjusted,), sum_col=False)

        return {
            "total_completed": int(total_row.total),
            "total_gross": Decimal(str(total_row.gross)),
            "total_share": Decimal(str(total_row.share)),
            "payable_count": payable_cnt,
            "payable_sum": payable_sum,
            "paid_sum": paid_sum,
            "on_hold_count": on_hold_cnt,
            "on_hold_sum": on_hold_sum,
            "excluded_count": excluded_cnt,
            "adjusted_count": adjusted_cnt,
        }

    # ── Status mutations ──────────────────────────────────────────────────────

    async def mark_paid(
        self,
        earning: OperatorEarning,
        paid_by_id: int,
        note: str | None = None,
    ) -> None:
        """Mark a single earning as paid. Only call on PAYABLE_STATUSES earnings.

        Raises ValueError if earning is on_hold — must be unfrozen first.
        """
        if earning.status == EarningStatus.on_hold:
            raise ValueError(
                f"Earning {earning.id} (order #{earning.order_id}) is on_hold — "
                "unfreeze it first with /unfreezeearning before paying"
            )
        earning.status = EarningStatus.paid
        earning.paid_at = datetime.now(timezone.utc)
        earning.paid_by_id = paid_by_id
        if note:
            earning.note = note
        await self.session.flush()

    async def mark_paid_all_payable(
        self,
        operator_id: int,
        paid_by_id: int,
        note: str | None = None,
    ) -> list[OperatorEarning]:
        """Mark ALL payable (pending + adjusted) earnings for an operator as paid.

        on_hold earnings are NEVER touched — they stay frozen.
        Returns list of earnings that were actually paid.
        """
        payable = await self.get_payable(operator_id)
        for e in payable:
            await self.mark_paid(e, paid_by_id, note)
        return payable

    async def freeze(
        self,
        earning: OperatorEarning,
        frozen_by_id: int,
        note: str | None = None,
    ) -> None:
        """Freeze an earning — moves to on_hold, prevents auto-payout.

        Safe to call on pending or adjusted earnings.
        Calling on already-frozen earning is a no-op.
        Cannot freeze a paid or excluded earning.
        """
        if earning.status in (EarningStatus.paid, EarningStatus.excluded):
            raise ValueError(
                f"Cannot freeze earning {earning.id}: already {earning.status.value}"
            )
        if earning.status == EarningStatus.on_hold:
            return  # already frozen — no-op

        earning.status = EarningStatus.on_hold
        earning.frozen_at = datetime.now(timezone.utc)
        earning.frozen_by_id = frozen_by_id
        earning.note = note
        await self.session.flush()

    async def unfreeze(
        self,
        earning: OperatorEarning,
        note: str | None = None,
    ) -> None:
        """Unfreeze an on_hold earning — moves back to pending.

        Admin decision: issue resolved, earning is safe to pay.
        Preserves the note from freeze unless a new note is provided.
        """
        if earning.status != EarningStatus.on_hold:
            raise ValueError(
                f"Earning {earning.id} is not on_hold (status: {earning.status.value})"
            )
        earning.status = EarningStatus.pending
        earning.frozen_at = None
        earning.frozen_by_id = None
        if note is not None:
            earning.note = note
        await self.session.flush()

    async def exclude(
        self,
        earning: OperatorEarning,
        note: str | None = None,
    ) -> None:
        """Permanently exclude an earning from all payouts.

        Use when dispute is resolved against the operator, or for other final decisions.
        """
        if earning.status == EarningStatus.paid:
            raise ValueError(
                f"Cannot exclude earning {earning.id}: already paid"
            )
        earning.status = EarningStatus.excluded
        earning.note = note
        await self.session.flush()

    async def adjust(
        self,
        earning: OperatorEarning,
        new_share: Decimal,
        note: str | None = None,
    ) -> None:
        """Manually override operator_share. Sets status=adjusted so it's auditable.

        adjusted earnings ARE included in /payouts and /markpaid.
        If the earning is on_hold, it remains on_hold after adjustment — admin must
        explicitly unfreeze to make it payable again.
        """
        if earning.status == EarningStatus.paid:
            raise ValueError(
                f"Cannot adjust earning {earning.id}: already paid"
            )
        earning.operator_share = new_share
        # Only change status if not on_hold — on_hold takes priority
        if earning.status != EarningStatus.on_hold:
            earning.status = EarningStatus.adjusted
        earning.note = note
        await self.session.flush()
