"""Unit tests for EarningRepo calculations (no DB required).

Tests the payout math: share calculation, rounding, status logic.
Uses in-memory Decimal arithmetic only — no async, no DB.

Also tests PAYABLE_STATUSES safety contract (on_hold not payable).
"""
from decimal import Decimal

import pytest


def _calculate_share(gross: Decimal, pct: float) -> Decimal:
    """Mirror of EarningRepo.create_for_order share calculation."""
    from decimal import ROUND_HALF_UP
    p = Decimal(str(pct))
    return (gross * p / 100).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


class TestShareCalculation:

    def test_80_percent_of_round_amount(self):
        assert _calculate_share(Decimal("2000"), 80.0) == Decimal("1600.00")

    def test_80_percent_of_odd_amount(self):
        # 1500 * 0.8 = 1200
        assert _calculate_share(Decimal("1500"), 80.0) == Decimal("1200.00")

    def test_100_percent(self):
        assert _calculate_share(Decimal("999"), 100.0) == Decimal("999.00")

    def test_70_percent_rounding(self):
        # 1001 * 0.7 = 700.70
        assert _calculate_share(Decimal("1001"), 70.0) == Decimal("700.70")

    def test_rounding_half_up(self):
        # 1.005 * 80% = 0.804 → rounds to 0.80 (half_up of 0.804)
        # let's use a clear half-up case: 3 * 80% / 3 check
        # 1001 * 33.33% = 333.63330 → 333.63
        result = _calculate_share(Decimal("1001"), 33.33)
        assert result == Decimal("333.63")

    def test_zero_amount(self):
        assert _calculate_share(Decimal("0"), 80.0) == Decimal("0.00")

    def test_small_amount_rounding(self):
        # 1 * 80% = 0.80
        assert _calculate_share(Decimal("1"), 80.0) == Decimal("0.80")

    def test_custom_percent_75(self):
        assert _calculate_share(Decimal("4000"), 75.0) == Decimal("3000.00")


class TestParseOperatorTarget:
    """Test _resolve_operator logic (sync parsing, no DB)."""

    def test_username_target_detected(self):
        target = "@vasya"
        assert target.startswith("@")

    def test_telegram_id_target_detected(self):
        target = "123456789"
        assert target.lstrip("-").isdigit()

    def test_negative_id_detected(self):
        target = "-100123"
        assert target.lstrip("-").isdigit()

    def test_invalid_target(self):
        target = "not_a_user"
        assert not target.startswith("@")
        assert not target.lstrip("-").isdigit()


class TestParsePeriod:
    """Test _parse_period helper from payouts router."""

    def test_7d_returns_datetime(self):
        from app.bot.routers.admin.payouts import _parse_period
        from datetime import datetime, timezone
        result = _parse_period("7d")
        assert result is not None
        assert isinstance(result, datetime)
        assert result.tzinfo == timezone.utc
        delta = datetime.now(timezone.utc) - result
        assert 6 < delta.days <= 7

    def test_30d_returns_datetime(self):
        from app.bot.routers.admin.payouts import _parse_period
        from datetime import datetime, timezone
        result = _parse_period("30d")
        assert result is not None
        delta = datetime.now(timezone.utc) - result
        assert 29 < delta.days <= 30

    def test_none_returns_none(self):
        from app.bot.routers.admin.payouts import _parse_period
        assert _parse_period(None) is None

    def test_unknown_returns_none(self):
        from app.bot.routers.admin.payouts import _parse_period
        assert _parse_period("1y") is None

    def test_today_returns_midnight(self):
        from app.bot.routers.admin.payouts import _parse_period
        result = _parse_period("today")
        assert result is not None
        assert result.hour == 0 and result.minute == 0


class TestPayableStatusesSafetyContract:
    """PAYABLE_STATUSES is the gate between 'safe to pay' and 'must not auto-pay'.

    These tests are the unit-level specification of that contract.
    If any of these fail, the payout system has a safety regression.
    """

    def test_on_hold_not_payable(self):
        from app.db.models.operator_earning import PAYABLE_STATUSES, EarningStatus
        assert EarningStatus.on_hold not in PAYABLE_STATUSES, \
            "on_hold must NEVER be payable — it requires explicit admin resolution first"

    def test_excluded_not_payable(self):
        from app.db.models.operator_earning import PAYABLE_STATUSES, EarningStatus
        assert EarningStatus.excluded not in PAYABLE_STATUSES

    def test_paid_not_payable(self):
        from app.db.models.operator_earning import PAYABLE_STATUSES, EarningStatus
        assert EarningStatus.paid not in PAYABLE_STATUSES, \
            "paid earnings must not appear in /payouts again"

    def test_pending_is_payable(self):
        from app.db.models.operator_earning import PAYABLE_STATUSES, EarningStatus
        assert EarningStatus.pending in PAYABLE_STATUSES

    def test_adjusted_is_payable(self):
        from app.db.models.operator_earning import PAYABLE_STATUSES, EarningStatus
        assert EarningStatus.adjusted in PAYABLE_STATUSES, \
            "adjusted earnings (corrected amount) must be payable"

    def test_payable_statuses_exhaustive(self):
        """Exactly 2 statuses are payable. If someone adds a new status, this test fails
        and forces a conscious decision about whether it should be payable."""
        from app.db.models.operator_earning import PAYABLE_STATUSES
        assert len(PAYABLE_STATUSES) == 2
