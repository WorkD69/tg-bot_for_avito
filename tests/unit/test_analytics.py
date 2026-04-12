"""Unit tests for analytics layer — no DB required.

Tests:
  A. Source extraction from /start deeplink payload (_extract_source — backward compat)
  B. Payload parsing into (source, campaign) (_parse_start_payload)
  C. Period parsing helper (_parse_period)
  D. Conversion rate helper (_conv)
  E. KNOWN_SOURCES constant
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


class TestParseStartPayload:
    """Tests for _parse_start_payload — returns (source, campaign) tuple."""

    def _parse(self, payload: str):
        from app.bot.middlewares.user_register import _parse_start_payload
        return _parse_start_payload(payload)

    # ── Known source, no campaign ─────────────────────────────────────────────

    def test_avito_alone(self):
        assert self._parse("avito") == ("avito", None)

    def test_tg_channel_alone(self):
        assert self._parse("tg_channel") == ("tg_channel", None)

    def test_direct_alone(self):
        assert self._parse("direct") == ("direct", None)

    # ── Known source + campaign ───────────────────────────────────────────────

    def test_avito_with_campaign(self):
        assert self._parse("avito_ad1") == ("avito", "ad1")

    def test_avito_with_longer_campaign(self):
        assert self._parse("avito_summer_promo") == ("avito", "summer_promo")

    def test_tg_channel_with_campaign(self):
        assert self._parse("tg_channel_post1") == ("tg_channel", "post1")

    def test_tg_channel_with_campaign_underscore(self):
        assert self._parse("tg_channel_june_ad2") == ("tg_channel", "june_ad2")

    def test_direct_with_campaign(self):
        assert self._parse("direct_manual1") == ("direct", "manual1")

    # ── Unknown payload → direct + campaign preserved ─────────────────────────

    def test_unknown_payload_becomes_direct_campaign(self):
        """Non-matching payload → source=direct, campaign=payload (data not lost)."""
        assert self._parse("mylink123") == ("direct", "mylink123")

    def test_unknown_payload_with_underscores(self):
        assert self._parse("ref_promo_xyz") == ("direct", "ref_promo_xyz")

    # ── Edge cases ────────────────────────────────────────────────────────────

    def test_empty_payload_returns_unknown(self):
        assert self._parse("") == ("unknown", None)

    def test_case_insensitive_via_extract_function(self):
        """_extract_source_and_campaign lowercases before calling _parse_start_payload."""
        from app.bot.middlewares.user_register import _extract_source_and_campaign
        from unittest.mock import MagicMock
        event = MagicMock()
        event.message = MagicMock()
        event.message.text = "/start AVITO_AD1"
        assert _extract_source_and_campaign(event) == ("avito", "ad1")

    def test_avito_underscore_only_treats_empty_campaign_as_none(self):
        """'avito_' → source=avito, campaign=None (empty string stripped)."""
        assert self._parse("avito_") == ("avito", None)


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
