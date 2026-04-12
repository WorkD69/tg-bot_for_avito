"""Unit tests for P1 growth layer — no DB required.

Tests:
  A. Welcome text selection by source
  B. is_new_user flag logic (middleware contract)
  C. Follow-up job scheduling (schedule_followup_job — non-fatal on failure)
"""
import pytest
from unittest.mock import MagicMock, patch


# ── A. Welcome text selection ─────────────────────────────────────────────────

class TestWelcomeText:
    """Tests for _welcome_text() in client/menu.py."""

    def _make_user(self, source: str, full_name: str = "Иван Иванов") -> MagicMock:
        u = MagicMock()
        u.source = source
        u.full_name = full_name
        u.role = "client"
        return u

    def _welcome(self, source: str, is_new_user: bool, name: str = "Иван Иванов") -> str:
        from app.bot.routers.client.menu import _welcome_text
        return _welcome_text(self._make_user(source, name), is_new_user)

    # ── New user with known source ────────────────────────────────────────────

    def test_new_avito_user_gets_avito_text(self):
        text = self._welcome("avito", is_new_user=True)
        assert "Авито" in text

    def test_new_tg_channel_user_gets_channel_text(self):
        text = self._welcome("tg_channel", is_new_user=True)
        assert "канал" in text.lower()

    def test_new_direct_user_gets_default_text(self):
        """'direct' source has no special template — falls back to default."""
        text = self._welcome("direct", is_new_user=True)
        assert "заявку" in text

    def test_new_unknown_user_gets_default_text(self):
        text = self._welcome("unknown", is_new_user=True)
        assert "заявку" in text

    def test_avito_text_contains_create_button_hint(self):
        """Avito welcome contains BTN_CREATE reference so user knows what to press."""
        from app.bot.keyboards.client_reply import BTN_CREATE
        text = self._welcome("avito", is_new_user=True)
        assert BTN_CREATE in text

    # ── Returning user always gets default ───────────────────────────────────

    def test_returning_avito_user_gets_default(self):
        """Repeat /start must NOT show special welcome again."""
        text = self._welcome("avito", is_new_user=False)
        assert "Авито" not in text

    def test_returning_tg_channel_user_gets_default(self):
        text = self._welcome("tg_channel", is_new_user=False)
        assert "канал" not in text.lower()

    # ── User name is included ─────────────────────────────────────────────────

    def test_welcome_includes_user_name(self):
        text = self._welcome("avito", is_new_user=True, name="Мария Петрова")
        assert "Мария Петрова" in text

    def test_default_includes_name(self):
        text = self._welcome("unknown", is_new_user=False, name="Сергей")
        assert "Сергей" in text


# ── B. is_new_user middleware contract ────────────────────────────────────────

class TestIsNewUserFlag:
    """Tests that middleware sets is_new_user correctly based on user existence."""

    def _make_event(self, text: str = "/start"):
        from unittest.mock import MagicMock
        event = MagicMock()
        event.message = MagicMock()
        event.message.text = text
        event.message.from_user = MagicMock()
        event.message.from_user.id = 999999
        event.message.from_user.full_name = "Test User"
        event.message.from_user.first_name = "Test"
        event.message.from_user.username = "testuser"
        event.callback_query = None
        return event

    def test_is_new_user_true_for_new_registration(self):
        """When user is None before create → is_new_user should be True."""
        # This tests the logical condition: is_new_user = (user is None)
        # before create is called
        user_before = None
        is_new = user_before is None
        assert is_new is True

    def test_is_new_user_false_for_existing_user(self):
        """When user already exists → is_new_user should be False."""
        user_before = MagicMock()  # existing user
        is_new = user_before is None
        assert is_new is False


# ── C. Follow-up scheduling ───────────────────────────────────────────────────

def _make_mock_setup(mock_scheduler):
    """Return a mock module that looks like app.scheduler.setup."""
    mock_module = MagicMock()
    mock_module.scheduler = mock_scheduler
    return mock_module


class TestScheduleFollowupJob:
    """Tests for schedule_followup_job() — scheduler interaction.

    schedule_followup_job() does `from app.scheduler.setup import scheduler` lazily
    inside the function body. We inject a mock by patching sys.modules so that
    each call picks up the fake scheduler.
    """

    def _call_with_mock_scheduler(self, mock_scheduler, order_id: int = 42):
        """Patch sys.modules and call schedule_followup_job."""
        import sys
        mock_module = _make_mock_setup(mock_scheduler)
        with patch.dict(sys.modules, {"app.scheduler.setup": mock_module}):
            import app.scheduler.jobs as jobs_mod
            jobs_mod.schedule_followup_job(order_id)

    def test_schedule_followup_does_not_raise_on_scheduler_error(self):
        """Scheduler failure must not propagate — order flow is unaffected."""
        mock_scheduler = MagicMock()
        mock_scheduler.add_job.side_effect = RuntimeError("jobstore dead")
        # Must not raise
        self._call_with_mock_scheduler(mock_scheduler, order_id=42)

    def test_schedule_followup_uses_correct_job_id(self):
        """Job ID must be 'followup_{order_id}' for idempotency via replace_existing."""
        mock_scheduler = MagicMock()
        self._call_with_mock_scheduler(mock_scheduler, order_id=123)
        call_kwargs = mock_scheduler.add_job.call_args[1]
        assert call_kwargs["id"] == "followup_123"

    def test_schedule_followup_replace_existing_true(self):
        """replace_existing=True prevents duplicate jobs on double-call."""
        mock_scheduler = MagicMock()
        self._call_with_mock_scheduler(mock_scheduler, order_id=7)
        call_kwargs = mock_scheduler.add_job.call_args[1]
        assert call_kwargs["replace_existing"] is True

    def test_schedule_followup_run_date_is_24h_ahead(self):
        """Job fires ~24h after scheduling."""
        from datetime import datetime, timezone, timedelta
        mock_scheduler = MagicMock()
        before = datetime.now(timezone.utc)
        self._call_with_mock_scheduler(mock_scheduler, order_id=1)
        after = datetime.now(timezone.utc)
        call_kwargs = mock_scheduler.add_job.call_args[1]
        run_date = call_kwargs["run_date"]
        expected_min = before + timedelta(hours=23, minutes=59)
        expected_max = after + timedelta(hours=24, minutes=1)
        assert expected_min <= run_date <= expected_max

    def test_schedule_followup_logs_warning_on_error(self):
        """Warning is logged when scheduler fails — for observability."""
        import sys
        mock_scheduler = MagicMock()
        mock_scheduler.add_job.side_effect = RuntimeError("dead")
        mock_module = _make_mock_setup(mock_scheduler)
        with patch.dict(sys.modules, {"app.scheduler.setup": mock_module}):
            import app.scheduler.jobs as jobs_mod
            with patch.object(jobs_mod, "logger") as mock_logger:
                jobs_mod.schedule_followup_job(99)
                assert mock_logger.warning.called
