"""Unit tests for analytics layer — no DB required.

Tests:
  A. Source extraction from /start deeplink payload
  B. Period parsing helper (_parse_period)
  C. Conversion rate helper (_conv)
  D. KNOWN_SOURCES constant
"""
import pytest


class TestSourceExtraction:
    """Tests for _extract_source logic in user_register middleware."""

    def _make_event(self, text: str):
        """Build a minimal mock Update with message.text set."""
        from unittest.mock import MagicMock
        event = MagicMock()
        event.message = MagicMock()
        event.message.text = text
        event.callback_query = None
        return event

    def test_avito_deeplink(self):
        from app.bot.middlewares.user_register import _extract_source
        event = self._make_event("/start avito")
        assert _extract_source(event) == "avito"

    def test_tg_channel_deeplink(self):
        from app.bot.middlewares.user_register import _extract_source
        event = self._make_event("/start tg_channel")
        assert _extract_source(event) == "tg_channel"

    def test_unknown_payload_returns_direct(self):
        """Non-empty but unrecognised payload → 'direct' (user came via some link)."""
        from app.bot.middlewares.user_register import _extract_source
        event = self._make_event("/start ref_promo_xyz")
        assert _extract_source(event) == "direct"

    def test_start_without_payload_returns_unknown(self):
        from app.bot.middlewares.user_register import _extract_source
        event = self._make_event("/start")
        assert _extract_source(event) == "unknown"

    def test_non_start_command_returns_unknown(self):
        from app.bot.middlewares.user_register import _extract_source
        event = self._make_event("/help")
        assert _extract_source(event) == "unknown"

    def test_no_message_returns_unknown(self):
        from unittest.mock import MagicMock
        from app.bot.middlewares.user_register import _extract_source
        event = MagicMock()
        event.message = None
        assert _extract_source(event) == "unknown"

    def test_source_case_insensitive(self):
        """Payload comparison is lowercased."""
        from app.bot.middlewares.user_register import _extract_source
        event = self._make_event("/start AVITO")
        assert _extract_source(event) == "avito"

    def test_direct_source_deeplink(self):
        from app.bot.middlewares.user_register import _extract_source
        event = self._make_event("/start direct")
        assert _extract_source(event) == "direct"


class TestKnownSources:
    """KNOWN_SOURCES must contain the canonical attribution values."""

    def test_avito_known(self):
        from app.db.models.user import KNOWN_SOURCES
        assert "avito" in KNOWN_SOURCES

    def test_tg_channel_known(self):
        from app.db.models.user import KNOWN_SOURCES
        assert "tg_channel" in KNOWN_SOURCES

    def test_direct_known(self):
        from app.db.models.user import KNOWN_SOURCES
        assert "direct" in KNOWN_SOURCES

    def test_unknown_not_in_known_sources(self):
        """'unknown' is the default fallback — not a valid deeplink payload."""
        from app.db.models.user import KNOWN_SOURCES
        assert "unknown" not in KNOWN_SOURCES


class TestAnalyticsPeriodParsing:
    """Tests for _parse_period helper in analytics router."""

    def test_7d_returns_since(self):
        from app.bot.routers.admin.analytics import _parse_period
        from datetime import datetime, timezone
        since, label = _parse_period("7d")
        assert since is not None
        assert "7" in label
        assert isinstance(since, datetime)
        delta = datetime.now(timezone.utc) - since
        assert 6 < delta.days <= 7

    def test_30d_returns_since(self):
        from app.bot.routers.admin.analytics import _parse_period
        from datetime import datetime, timezone
        since, label = _parse_period("30d")
        assert since is not None
        delta = datetime.now(timezone.utc) - since
        assert 29 < delta.days <= 30

    def test_today_returns_midnight(self):
        from app.bot.routers.admin.analytics import _parse_period
        since, label = _parse_period("today")
        assert since is not None
        assert since.hour == 0 and since.minute == 0

    def test_none_returns_all_time(self):
        from app.bot.routers.admin.analytics import _parse_period
        since, label = _parse_period(None)
        assert since is None
        assert "всё" in label

    def test_all_returns_all_time(self):
        from app.bot.routers.admin.analytics import _parse_period
        since, label = _parse_period("all")
        assert since is None

    def test_unknown_token_returns_all_time(self):
        from app.bot.routers.admin.analytics import _parse_period
        since, label = _parse_period("1year")
        assert since is None


class TestConversionHelper:
    """Tests for _conv() conversion rate formatting."""

    def test_perfect_conversion(self):
        from app.bot.routers.admin.analytics import _conv
        assert _conv(100, 100) == "100.0%"

    def test_half_conversion(self):
        from app.bot.routers.admin.analytics import _conv
        assert _conv(100, 50) == "50.0%"

    def test_zero_prev_returns_dash(self):
        from app.bot.routers.admin.analytics import _conv
        assert _conv(0, 10) == "—"

    def test_zero_converted(self):
        from app.bot.routers.admin.analytics import _conv
        assert _conv(100, 0) == "0.0%"

    def test_partial_decimal(self):
        from app.bot.routers.admin.analytics import _conv
        result = _conv(3, 1)
        assert "33.3" in result
