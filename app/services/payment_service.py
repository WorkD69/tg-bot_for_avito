import hashlib
from decimal import Decimal
from urllib.parse import urlencode

from app.config import settings

ROBOKASSA_BASE_URL = "https://auth.robokassa.ru/Merchant/Index.aspx"


class PaymentService:
    def _md5(self, raw: str) -> str:
        return hashlib.md5(raw.encode()).hexdigest().upper()

    def generate_link(self, invoice_id: str, amount: Decimal) -> str:
        """Build Robokassa payment URL with signature (pass1).

        invoice_id must be order.payment_invoice_id — versioned as "{order_id}_{revision}".
        When price changes, payment_invoice_id is updated so old Robokassa links get "not found"
        on callback and are rejected automatically.
        """
        out_sum = f"{amount:.2f}"
        inv_id = invoice_id

        sig_raw = f"{settings.robokassa_login}:{out_sum}:{inv_id}:{settings.robokassa_pass1}"
        signature = self._md5(sig_raw)

        params = {
            "MerchantLogin": settings.robokassa_login,
            "OutSum": out_sum,
            "InvId": inv_id,
            "SignatureValue": signature,
        }
        if settings.robokassa_is_test:
            params["IsTest"] = "1"

        return f"{ROBOKASSA_BASE_URL}?{urlencode(params)}"

    def verify_callback(self, out_sum: str, inv_id: str, signature: str) -> bool:
        """Verify Robokassa result callback signature (pass2)."""
        sig_raw = f"{out_sum}:{inv_id}:{settings.robokassa_pass2}"
        expected = self._md5(sig_raw)
        return expected == signature.upper()
