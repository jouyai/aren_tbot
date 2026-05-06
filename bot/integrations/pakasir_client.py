"""
Pakasir QRIS payment gateway client.

Handles:
  - create_qris   — create a QRIS transaction and return QR image bytes
  - verify_transaction — verify a transaction via the Transaction Detail API
  - cancel_transaction — cancel an unpaid transaction

API key is NEVER logged.

Requirements: 4.1, 4.2, 4.3, 4.4
"""
from __future__ import annotations

import io
import logging
from typing import Any

import httpx

logger = logging.getLogger(__name__)

# Sentinel used to mask the API key in log output
_MASKED = "***"


class PakasirError(Exception):
    """Raised when the Pakasir API returns an unexpected response."""


class PakasirClient:
    """Async client for the Pakasir QRIS payment gateway.

    Parameters
    ----------
    project_slug:
        The project slug from the Pakasir dashboard
        (env var ``PAKASIR_PROJECT_SLUG``).
    api_key:
        The API key from the Pakasir dashboard
        (env var ``PAKASIR_API_KEY``).  Never logged.
    """

    BASE_URL = "https://app.pakasir.com"

    def __init__(self, project_slug: str, api_key: str) -> None:
        self._project = project_slug
        self._api_key = api_key  # never logged
        self._client = httpx.AsyncClient(timeout=30.0)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def create_qris(self, order_id: str, amount: int) -> bytes:
        """Create a QRIS transaction and return the QR code as PNG image bytes.

        Parameters
        ----------
        order_id:
            Must follow the format ``"TOPUP-<reference_code>"``.
        amount:
            The top-up amount in IDR (integer, e.g. 50000).

        Returns
        -------
        bytes
            PNG image bytes of the rendered QR code.

        Raises
        ------
        PakasirError
            If the API call fails or returns an unexpected response.
        """
        payload = {
            "project": self._project,
            "order_id": order_id,
            "amount": amount,
            "api_key": self._api_key,
        }
        response = await self._post("/api/transactioncreate/qris", payload)

        payment = response.get("payment", {})
        qr_string = payment.get("payment_number")
        if not qr_string:
            raise PakasirError(
                f"create_qris: 'payment_number' missing in response for order_id={order_id}"
            )

        # Render QR string to PNG image bytes
        qr_image_bytes = _render_qr_to_png(qr_string)

        logger.info(
            "QRIS created: order_id=%s amount=%s total_payment=%s expired_at=%s",
            order_id,
            amount,
            payment.get("total_payment"),
            payment.get("expired_at"),
        )
        return qr_image_bytes

    async def create_qris_with_details(self, order_id: str, amount: int) -> dict:
        """Create a QRIS transaction and return full payment details plus QR image bytes.

        Returns a dict with keys:
          - ``qr_image``: PNG bytes of the QR code
          - ``total_payment``: amount + fee (what the user actually pays)
          - ``expired_at``: ISO 8601 expiry timestamp string
          - ``payment_number``: raw QR string

        Raises
        ------
        PakasirError
            If the API call fails or returns an unexpected response.
        """
        payload = {
            "project": self._project,
            "order_id": order_id,
            "amount": amount,
            "api_key": self._api_key,
        }
        response = await self._post("/api/transactioncreate/qris", payload)

        payment = response.get("payment", {})
        qr_string = payment.get("payment_number")
        if not qr_string:
            raise PakasirError(
                f"create_qris: 'payment_number' missing in response for order_id={order_id}"
            )

        qr_image_bytes = _render_qr_to_png(qr_string)

        return {
            "qr_image": qr_image_bytes,
            "total_payment": payment.get("total_payment"),
            "expired_at": payment.get("expired_at"),
            "payment_number": qr_string,
        }

    async def verify_transaction(self, order_id: str, amount: int) -> bool:
        """Verify a transaction via the Transaction Detail API.

        Returns ``True`` if and only if:
          - ``transaction.status == "completed"``
          - ``transaction.amount == amount``

        This is the authoritative verification step — the webhook payload
        alone must never be trusted.

        Requirements: 4.3
        """
        params = {
            "project": self._project,
            "amount": amount,
            "order_id": order_id,
            "api_key": self._api_key,
        }
        try:
            response = await self._get("/api/transactiondetail", params)
        except PakasirError:
            logger.warning(
                "verify_transaction failed for order_id=%s amount=%s",
                order_id,
                amount,
            )
            return False

        transaction = response.get("transaction", {})
        status = transaction.get("status")
        tx_amount = transaction.get("amount")

        verified = status == "completed" and tx_amount == amount
        logger.info(
            "verify_transaction: order_id=%s amount=%s status=%s verified=%s",
            order_id,
            amount,
            status,
            verified,
        )
        return verified

    async def cancel_transaction(self, order_id: str, amount: int) -> dict:
        """Cancel an unpaid QRIS transaction.

        Returns the raw API response dict.

        Requirements: 4.1
        """
        payload = {
            "project": self._project,
            "order_id": order_id,
            "amount": amount,
            "api_key": self._api_key,
        }
        response = await self._post("/api/transactioncancel", payload)
        logger.info("cancel_transaction: order_id=%s amount=%s", order_id, amount)
        return response

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    async def _post(self, endpoint: str, payload: dict[str, Any]) -> dict:
        """POST JSON to *endpoint* and return the parsed response.

        The API key is masked in all log output.
        """
        safe_payload = {k: (_MASKED if k == "api_key" else v) for k, v in payload.items()}
        logger.debug("POST %s payload=%s", endpoint, safe_payload)

        try:
            resp = await self._client.post(
                f"{self.BASE_URL}{endpoint}",
                json=payload,
            )
            resp.raise_for_status()
            data = resp.json()
        except httpx.HTTPStatusError as exc:
            logger.error("POST %s HTTP error: %s", endpoint, exc.response.status_code)
            raise PakasirError(f"HTTP {exc.response.status_code} from {endpoint}") from exc
        except httpx.RequestError as exc:
            logger.error("POST %s request error: %s", endpoint, exc)
            raise PakasirError(f"Request error for {endpoint}: {exc}") from exc

        logger.debug("POST %s response=%s", endpoint, data)
        return data

    async def _get(self, endpoint: str, params: dict[str, Any]) -> dict:
        """GET *endpoint* with query params and return the parsed response.

        The API key is masked in all log output.
        """
        safe_params = {k: (_MASKED if k == "api_key" else v) for k, v in params.items()}
        logger.debug("GET %s params=%s", endpoint, safe_params)

        try:
            resp = await self._client.get(
                f"{self.BASE_URL}{endpoint}",
                params=params,
            )
            resp.raise_for_status()
            data = resp.json()
        except httpx.HTTPStatusError as exc:
            logger.error("GET %s HTTP error: %s", endpoint, exc.response.status_code)
            raise PakasirError(f"HTTP {exc.response.status_code} from {endpoint}") from exc
        except httpx.RequestError as exc:
            logger.error("GET %s request error: %s", endpoint, exc)
            raise PakasirError(f"Request error for {endpoint}: {exc}") from exc

        logger.debug("GET %s response=%s", endpoint, data)
        return data

    async def close(self) -> None:
        """Close the underlying HTTP client."""
        await self._client.aclose()


# ---------------------------------------------------------------------------
# QR rendering helper
# ---------------------------------------------------------------------------

def _render_qr_to_png(qr_string: str) -> bytes:
    """Render a QR code string to PNG image bytes using the ``qrcode`` library.

    Parameters
    ----------
    qr_string:
        The raw QR code data string (e.g. the ``payment_number`` from Pakasir).

    Returns
    -------
    bytes
        PNG-encoded image bytes ready to be sent as a Telegram photo.
    """
    import qrcode  # imported here to keep the module importable without PIL

    qr = qrcode.QRCode(
        version=None,
        error_correction=qrcode.constants.ERROR_CORRECT_M,
        box_size=10,
        border=4,
    )
    qr.add_data(qr_string)
    qr.make(fit=True)

    img = qr.make_image(fill_color="black", back_color="white")
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    buf.seek(0)
    return buf.read()
