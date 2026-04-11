"""Unit tests for PaymentService.

Pure logic — no DB, no network, no bot.
Tests cover the two methods that are business-critical:
  - verify_callback: MD5 signature verification (Robokassa result callback)
  - generate_link: URL construction with pass1 signature
"""
import hashlib
from decimal import Decimal
from unittest.mock import patch

import pytest


def _make_sig_pass2(out_sum: str, inv_id: str, pass2: str) -> str:
    """Reference implementation of Robokassa result signature (pass2)."""
    return hashlib.md5(f"{out_sum}:{inv_id}:{pass2}".encode()).hexdigest().upper()


def _make_sig_pass1(login: str, out_sum: str, inv_id: str, pass1: str) -> str:
    """Reference implementation of Robokassa payment link signature (pass1)."""
    return hashlib.md5(f"{login}:{out_sum}:{inv_id}:{pass1}".encode()).hexdigest().upper()


# ── verify_callback ───────────────────────────────────────────────────────────

class TestVerifyCallback:
    """PaymentService.verify_callback — must match MD5(OutSum:InvId:pass2)."""

    def _svc(self, pass2: str = "secret_pass2"):
        from app.services.payment_service import PaymentService
        with patch("app.services.payment_service.settings") as s:
            s.robokassa_pass2 = pass2
            svc = PaymentService()
        # patch is gone here, but svc is already constructed — settings only used
        # at call time inside verify_callback, so we need to patch at call time too
        return svc, pass2

    def test_valid_signature_accepted(self):
        from app.services.payment_service import PaymentService
        pass2 = "secret_pass2"
        out_sum, inv_id = "1500.00", "42_1"
        sig = _make_sig_pass2(out_sum, inv_id, pass2)

        with patch("app.services.payment_service.settings") as s:
            s.robokassa_pass2 = pass2
            result = PaymentService().verify_callback(out_sum, inv_id, sig)
        assert result is True

    def test_wrong_signature_rejected(self):
        from app.services.payment_service import PaymentService
        with patch("app.services.payment_service.settings") as s:
            s.robokassa_pass2 = "correct_pass"
            result = PaymentService().verify_callback("1500.00", "42_1", "BADBADBADBAD")
        assert result is False

    def test_lowercase_signature_accepted(self):
        """Robokassa may send lowercase hex — verify_callback uppercases before compare."""
        from app.services.payment_service import PaymentService
        pass2 = "secret_pass2"
        out_sum, inv_id = "1500.00", "42_1"
        sig_lower = _make_sig_pass2(out_sum, inv_id, pass2).lower()  # force lowercase

        with patch("app.services.payment_service.settings") as s:
            s.robokassa_pass2 = pass2
            result = PaymentService().verify_callback(out_sum, inv_id, sig_lower)
        assert result is True

    def test_amount_mismatch_rejects(self):
        """Signature for a different amount must not verify against the stated amount."""
        from app.services.payment_service import PaymentService
        pass2 = "secret_pass2"
        # Generate signature for 2000, but pass 1500 as OutSum
        sig = _make_sig_pass2("2000.00", "42_1", pass2)

        with patch("app.services.payment_service.settings") as s:
            s.robokassa_pass2 = pass2
            result = PaymentService().verify_callback("1500.00", "42_1", sig)
        assert result is False

    def test_inv_id_mismatch_rejects(self):
        """Signature for a different InvId must not verify."""
        from app.services.payment_service import PaymentService
        pass2 = "secret_pass2"
        sig = _make_sig_pass2("1500.00", "42_1", pass2)  # signed for revision 1

        with patch("app.services.payment_service.settings") as s:
            s.robokassa_pass2 = pass2
            # Attempt to replay with a different revision
            result = PaymentService().verify_callback("1500.00", "42_2", sig)
        assert result is False

    def test_wrong_pass2_rejects(self):
        """Correct format but wrong pass2 secret → invalid."""
        from app.services.payment_service import PaymentService
        pass2_correct = "correct_pass"
        pass2_attacker = "attacker_pass"
        sig = _make_sig_pass2("1500.00", "42_1", pass2_attacker)

        with patch("app.services.payment_service.settings") as s:
            s.robokassa_pass2 = pass2_correct
            result = PaymentService().verify_callback("1500.00", "42_1", sig)
        assert result is False


# ── generate_link ─────────────────────────────────────────────────────────────

class TestGenerateLink:
    """PaymentService.generate_link — URL must contain correct params and signature."""

    def _url(self, inv_id: str, amount: Decimal, login="ShopLogin", pass1="p1", is_test=False) -> str:
        from app.services.payment_service import PaymentService
        with patch("app.services.payment_service.settings") as s:
            s.robokassa_login = login
            s.robokassa_pass1 = pass1
            s.robokassa_is_test = is_test
            return PaymentService().generate_link(inv_id, amount)

    def test_url_starts_with_robokassa_base(self):
        url = self._url("42_1", Decimal("1500"))
        assert "auth.robokassa.ru" in url

    def test_merchant_login_in_url(self):
        url = self._url("42_1", Decimal("1500"), login="MyStore")
        assert "MerchantLogin=MyStore" in url

    def test_amount_formatted_two_decimals(self):
        url = self._url("42_1", Decimal("1500"))
        assert "OutSum=1500.00" in url

    def test_fractional_amount(self):
        url = self._url("42_1", Decimal("1500.50"))
        assert "OutSum=1500.50" in url

    def test_inv_id_in_url(self):
        url = self._url("42_3", Decimal("1000"))
        assert "InvId=42_3" in url

    def test_signature_present(self):
        url = self._url("42_1", Decimal("1500"))
        assert "SignatureValue=" in url

    def test_signature_is_correct(self):
        """SignatureValue must match MD5(login:OutSum:InvId:pass1)."""
        from urllib.parse import urlparse, parse_qs
        login, pass1 = "ShopLogin", "p1"
        amount = Decimal("1500")
        inv_id = "42_1"
        url = self._url(inv_id, amount, login=login, pass1=pass1)

        params = parse_qs(urlparse(url).query)
        sig_in_url = params["SignatureValue"][0]
        expected = _make_sig_pass1(login, "1500.00", inv_id, pass1)
        assert sig_in_url == expected

    def test_test_mode_adds_is_test(self):
        url = self._url("42_1", Decimal("100"), is_test=True)
        assert "IsTest=1" in url

    def test_prod_mode_no_is_test(self):
        url = self._url("42_1", Decimal("100"), is_test=False)
        assert "IsTest" not in url

    def test_different_revisions_produce_different_urls(self):
        """Stale payment_revision → different InvId → different signature → old link invalid."""
        url1 = self._url("42_1", Decimal("1500"))
        url2 = self._url("42_2", Decimal("1500"))
        assert url1 != url2
        assert "InvId=42_1" in url1
        assert "InvId=42_2" in url2
