"""Unit tests for _parse_deadline and _parse_budget in order_create.py."""
from datetime import date, timedelta, timezone, datetime
from decimal import Decimal
import pytest

# We import the helpers directly — no Telegram/aiogram setup needed.
from app.bot.routers.client.order_create import _parse_deadline, _parse_budget

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _today():
    """Return today in MSK (UTC+3)."""
    from datetime import timezone, timedelta
    MSK = timezone(timedelta(hours=3))
    return datetime.now(MSK).date()


def _yesterday():
    return _today() - timedelta(days=1)


def _tomorrow():
    return _today() + timedelta(days=1)


# ===========================================================================
# _parse_deadline
# ===========================================================================

class TestParseDeadline:

    # --- happy path ---

    def test_future_date_ok(self):
        """Normal future date parses without error."""
        d = _parse_deadline("25.05.2026")
        assert d == date(2026, 5, 25)

    def test_today_ok(self):
        """Today is accepted (not in the past)."""
        today_str = _today().strftime("%d.%m.%Y")
        d = _parse_deadline(today_str)
        assert d == _today()

    def test_tomorrow_ok(self):
        tomorrow_str = _tomorrow().strftime("%d.%m.%Y")
        d = _parse_deadline(tomorrow_str)
        assert d == _tomorrow()

    # --- bad format: doesn't match DD.MM.YYYY pattern ---

    def test_garbage_format(self):
        with pytest.raises(ValueError, match="format"):
            _parse_deadline("1231.123123.123131")

    def test_iso_format_rejected(self):
        with pytest.raises(ValueError, match="format"):
            _parse_deadline("2026-05-25")

    def test_single_digit_day_rejected(self):
        """5.05.2026 is not DD.MM.YYYY (only one digit for day)."""
        with pytest.raises(ValueError, match="format"):
            _parse_deadline("5.05.2026")

    def test_slash_separator_rejected(self):
        with pytest.raises(ValueError, match="format"):
            _parse_deadline("25/05/2026")

    def test_plain_text_rejected(self):
        with pytest.raises(ValueError, match="format"):
            _parse_deadline("next monday")

    def test_empty_string_rejected(self):
        with pytest.raises(ValueError, match="format"):
            _parse_deadline("")

    # --- nonexistent date: pattern matches but date is impossible ---

    def test_day_32_nonexistent(self):
        with pytest.raises(ValueError, match="nonexistent"):
            _parse_deadline("32.01.2026")

    def test_feb_31_nonexistent(self):
        with pytest.raises(ValueError, match="nonexistent"):
            _parse_deadline("31.02.2026")

    def test_feb_29_non_leap_year_nonexistent(self):
        """2025 is not a leap year."""
        with pytest.raises(ValueError, match="nonexistent"):
            _parse_deadline("29.02.2025")

    def test_feb_29_leap_year_ok(self):
        """2024 is a leap year — 29.02.2024 existed, but it's in the past."""
        # This confirms the distinction: format ok, date exists, but past.
        with pytest.raises(ValueError, match="past"):
            _parse_deadline("29.02.2024")

    def test_month_13_nonexistent(self):
        with pytest.raises(ValueError, match="nonexistent"):
            _parse_deadline("01.13.2026")

    def test_day_00_nonexistent(self):
        with pytest.raises(ValueError, match="nonexistent"):
            _parse_deadline("00.01.2026")

    # --- past date ---

    def test_yesterday_past(self):
        yesterday_str = _yesterday().strftime("%d.%m.%Y")
        with pytest.raises(ValueError, match="past"):
            _parse_deadline(yesterday_str)

    def test_far_past_past(self):
        with pytest.raises(ValueError, match="past"):
            _parse_deadline("01.01.2020")


# ===========================================================================
# _parse_budget
# ===========================================================================

class TestParseBudget:

    # --- happy path ---

    def test_integer(self):
        assert _parse_budget("1500") == Decimal("1500")

    def test_space_thousands_separator(self):
        assert _parse_budget("1 500") == Decimal("1500")

    def test_ruble_suffix_cyrillic_lower(self):
        assert _parse_budget("1500р") == Decimal("1500")

    def test_ruble_suffix_sign(self):
        assert _parse_budget("1500₽") == Decimal("1500")

    def test_ruble_suffix_word(self):
        assert _parse_budget("1500руб") == Decimal("1500")

    def test_decimal_dot(self):
        assert _parse_budget("1500.50") == Decimal("1500.50")

    def test_decimal_comma(self):
        """Comma as decimal separator is allowed."""
        assert _parse_budget("1500,50") == Decimal("1500.50")

    def test_large_space_thousands(self):
        """10 000 with space — unambiguous, all digits."""
        assert _parse_budget("10 000") == Decimal("10000")

    # --- ambiguous — must be rejected ---

    def test_ambiguous_comma_thousands(self):
        """1,500 looks like thousands separator → reject."""
        with pytest.raises(ValueError, match="ambiguous"):
            _parse_budget("1,500")

    def test_ambiguous_dot_thousands(self):
        """1.500 looks like thousands separator → reject."""
        with pytest.raises(ValueError, match="ambiguous"):
            _parse_budget("1.500")

    def test_ambiguous_two_digits_comma(self):
        """12,500 — also matches pattern (1–3 digits before sep, 3 after)."""
        with pytest.raises(ValueError, match="ambiguous"):
            _parse_budget("12,500")

    # --- non-positive ---

    def test_zero_rejected(self):
        with pytest.raises(ValueError, match="non-positive"):
            _parse_budget("0")

    def test_negative_rejected(self):
        with pytest.raises(ValueError, match="non-positive"):
            _parse_budget("-100")

    # --- too large ---

    def test_over_limit(self):
        with pytest.raises(ValueError, match="too-large"):
            _parse_budget("999999999")

    # --- invalid / unparseable ---

    def test_text_rejected(self):
        from decimal import InvalidOperation
        with pytest.raises((ValueError, InvalidOperation)):
            _parse_budget("текст")

    def test_empty_rejected(self):
        with pytest.raises(ValueError, match="empty"):
            _parse_budget("")

    def test_only_spaces_rejected(self):
        with pytest.raises(ValueError, match="empty"):
            _parse_budget("   ")

    def test_only_currency_suffix_rejected(self):
        with pytest.raises(ValueError, match="empty"):
            _parse_budget("₽")
