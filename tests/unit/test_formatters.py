"""Unit tests for formatting utilities in app/bot/formatters.py.

Pure functions — no DB, no bot, no network.
"""
from decimal import Decimal

import pytest


# ── _money ────────────────────────────────────────────────────────────────────

class TestMoney:
    """_money() must format Decimal as rubles without unnecessary decimals."""

    def test_integer_decimal(self):
        from app.bot.formatters import _money
        assert _money(Decimal("1500")) == "1500 ₽"

    def test_integer_from_int(self):
        from app.bot.formatters import _money
        assert _money(1500) == "1500 ₽"

    def test_no_trailing_zeros(self):
        """1000.00 must render as 1000, not 1000.00."""
        from app.bot.formatters import _money
        assert _money(Decimal("1000.00")) == "1000 ₽"

    def test_fractional_amount_kept(self):
        """1500.50 must keep the .50 because it's not a whole number."""
        from app.bot.formatters import _money
        assert _money(Decimal("1500.50")) == "1500.50 ₽"

    def test_none_returns_dash(self):
        from app.bot.formatters import _money
        assert _money(None) == "—"

    def test_zero(self):
        from app.bot.formatters import _money
        assert _money(Decimal("0")) == "0 ₽"

    def test_zero_point_zero(self):
        from app.bot.formatters import _money
        assert _money(Decimal("0.00")) == "0 ₽"

    def test_string_input_coerced(self):
        """_money converts via Decimal(str(amount)) — string '1500' must work."""
        from app.bot.formatters import _money
        assert _money("1500") == "1500 ₽"

    def test_large_amount(self):
        from app.bot.formatters import _money
        assert _money(Decimal("99999")) == "99999 ₽"


# ── _parse_comment_ts ─────────────────────────────────────────────────────────

class TestParseCommentTs:
    """_parse_comment_ts() must split 'ISO_TS|text' or fall back gracefully."""

    def test_valid_iso_timestamp_extracted(self):
        from app.bot.formatters import _parse_comment_ts
        ts, text = _parse_comment_ts("2024-01-15T07:30:00|Hello world")
        assert "15.01.2024" in ts
        assert "10:30" in ts  # UTC+3 (MSK) → 07:30 UTC = 10:30 MSK
        assert text == "Hello world"

    def test_plain_text_no_pipe(self):
        """No pipe character → timestamp is dash, text is the full input."""
        from app.bot.formatters import _parse_comment_ts
        ts, text = _parse_comment_ts("Just a plain comment")
        assert ts == "—"
        assert text == "Just a plain comment"

    def test_invalid_timestamp_fallback(self):
        """Pipe present but left side is not a valid ISO timestamp → full fallback."""
        from app.bot.formatters import _parse_comment_ts
        ts, text = _parse_comment_ts("not_a_date|some text")
        assert ts == "—"
        # Falls back to returning the full original string stripped
        assert "not_a_date" in text

    def test_whitespace_stripped_from_text(self):
        from app.bot.formatters import _parse_comment_ts
        _, text = _parse_comment_ts("2024-06-01T12:00:00|  trimmed  ")
        assert text == "trimmed"

    def test_pipe_in_content_not_split_again(self):
        """Only the first pipe is the separator — extra pipes are part of the text."""
        from app.bot.formatters import _parse_comment_ts
        ts, text = _parse_comment_ts("2024-06-01T12:00:00|text|with|pipes")
        assert "01.06.2024" in ts
        assert text == "text|with|pipes"

    def test_empty_text_after_pipe(self):
        from app.bot.formatters import _parse_comment_ts
        ts, text = _parse_comment_ts("2024-06-01T12:00:00|")
        assert "01.06.2024" in ts
        assert text == ""

    def test_plain_text_stripped(self):
        from app.bot.formatters import _parse_comment_ts
        _, text = _parse_comment_ts("  padded plain  ")
        assert text == "padded plain"
