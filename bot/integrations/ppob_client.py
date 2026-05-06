"""
PPOB API Client for toponepanel.com.

All requests use POST with Content-Type: application/x-www-form-urlencoded.
Authentication is via api_id (int) and api_key (str) sent in every request body.
The api_key is NEVER logged — it is masked as '***' in all log output.

Requirements: 9.1, 9.2, 9.3, 9.4
"""
from __future__ import annotations

import asyncio
import logging
from typing import Union

import httpx

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Custom exceptions
# ---------------------------------------------------------------------------


class PPOBError(Exception):
    """Base exception for PPOB API errors (network, HTTP, unexpected responses)."""


class PPOBOrderError(PPOBError):
    """Raised when the PPOB API returns status=false for an order-related request."""

    def __init__(self, message: str, response: dict | None = None) -> None:
        super().__init__(message)
        self.response = response or {}


# ---------------------------------------------------------------------------
# Client
# ---------------------------------------------------------------------------


class PPOBClient:
    """Async HTTP client for the toponepanel.com PPOB/SMM API.

    Usage::

        async with PPOBClient(api_id=11, api_key="secret") as client:
            balance = await client.get_balance()

    Or manage the lifecycle manually::

        client = PPOBClient(api_id=11, api_key="secret")
        await client.close()
    """

    BASE_URL = "https://toponepanel.com"

    def __init__(self, api_id: int, api_key: str) -> None:
        self._api_id = api_id
        self._api_key = api_key
        self._client = httpx.AsyncClient()

    # ------------------------------------------------------------------
    # Context-manager support
    # ------------------------------------------------------------------

    async def __aenter__(self) -> "PPOBClient":
        return self

    async def __aexit__(self, *_: object) -> None:
        await self.close()

    async def close(self) -> None:
        """Close the underlying HTTP client."""
        await self._client.aclose()

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _base_payload(self) -> dict:
        """Return the authentication payload included in every request."""
        return {"api_id": self._api_id, "api_key": self._api_key}

    def _masked_payload(self, payload: dict) -> dict:
        """Return a copy of *payload* with api_key replaced by '***' for logging."""
        masked = dict(payload)
        if "api_key" in masked:
            masked["api_key"] = "***"
        return masked

    async def _post(self, endpoint: str, extra_data: dict | None = None) -> dict:
        """POST to *endpoint* with exponential-backoff retry.

        Retries up to 3 times (delays: 1 s, 2 s, 4 s) on HTTP 5xx errors and
        timeouts.  4xx errors are raised immediately without retrying.

        The api_key is masked as '***' in all log output.

        Requirements: 9.1, 9.2, 9.4
        """
        payload = {**self._base_payload(), **(extra_data or {})}
        url = f"{self.BASE_URL}{endpoint}"

        for attempt in range(3):
            try:
                logger.debug(
                    "PPOB request [attempt %d/3] %s payload=%s",
                    attempt + 1,
                    url,
                    self._masked_payload(payload),
                )
                response = await self._client.post(url, data=payload, timeout=30.0)
                response.raise_for_status()
                result: dict = response.json()
                logger.debug(
                    "PPOB response %s status=%s body=%s",
                    url,
                    response.status_code,
                    result,
                )
                return result

            except httpx.HTTPStatusError as exc:
                # Do not retry client errors (4xx)
                if exc.response.status_code < 500:
                    logger.warning(
                        "PPOB client error %s %s: %s",
                        url,
                        exc.response.status_code,
                        exc.response.text,
                    )
                    raise PPOBError(
                        f"PPOB API returned HTTP {exc.response.status_code}: {exc.response.text}"
                    ) from exc

                # 5xx — retry unless this was the last attempt
                logger.warning(
                    "PPOB server error %s %s (attempt %d/3)",
                    url,
                    exc.response.status_code,
                    attempt + 1,
                )
                if attempt == 2:
                    raise PPOBError(
                        f"PPOB API returned HTTP {exc.response.status_code} after 3 attempts"
                    ) from exc

            except httpx.TimeoutException as exc:
                logger.warning(
                    "PPOB timeout %s (attempt %d/3)",
                    url,
                    attempt + 1,
                )
                if attempt == 2:
                    raise PPOBError(
                        f"PPOB API timed out after 3 attempts: {url}"
                    ) from exc

            # Exponential backoff: 1 s, 2 s, 4 s
            await asyncio.sleep(2**attempt)

        # Should never be reached, but satisfies type checkers
        raise PPOBError(f"PPOB API request failed after 3 attempts: {url}")

    # ------------------------------------------------------------------
    # Public API methods
    # ------------------------------------------------------------------

    async def get_balance(self) -> dict:
        """Check the provider account balance.

        Returns::

            {"status": true, "msg": "...", "balance": 123456}

        Requirements: 9.1, 9.4
        """
        return await self._post("/api/balance")

    async def get_services(self) -> list[dict]:
        """Fetch the list of available services from the provider.

        Returns a list of service dicts, each containing:
        ``id``, ``name``, ``type``, ``category``, ``price``, ``min``, ``max``,
        ``refill``, ``description``.

        Requirements: 9.1, 9.4
        """
        result = await self._post("/api/services")
        if not result.get("status"):
            raise PPOBError(
                f"get_services failed: {result.get('msg', 'unknown error')}"
            )
        return result.get("services", [])

    async def create_order(
        self,
        service_id: int,
        target: str,
        quantity: int,
        order_type: str = "default",
        **extra_params: object,
    ) -> dict:
        """Create a new order on the provider.

        Parameters are sent according to *order_type*:

        * ``default``          — service, target, quantity
        * ``package``          — service, target (no quantity)
        * ``custom_comment``   — service, target, comments (``\\r\\n`` or ``\\n`` separated)
        * ``mention_list``     — service, target, usernames (``\\r\\n`` or ``\\n`` separated)
        * ``mention_hashtag``  — service, target, quantity, hashtag
        * ``mention_follower`` — service, target, quantity, username
        * ``mention_media``    — service, target, quantity, media
        * ``poll``             — service, target, quantity, answer_number (int)
        * ``comment_reply``    — service, target, username, comments (``\\r\\n`` or ``\\n`` separated)
        * ``comment_likes``    — service, target, quantity, username

        Returns on success::

            {"status": true, "msg": "...", "order": <order_id>}

        Raises :class:`PPOBOrderError` when the provider returns ``status: false``.

        Requirements: 9.1, 9.2, 9.3, 9.4
        """
        data: dict = {"service": service_id, "target": target}

        if order_type == "default":
            data["quantity"] = quantity

        elif order_type == "package":
            # No quantity for package orders
            pass

        elif order_type == "custom_comment":
            comments = extra_params.get("comments", "")
            data["comments"] = comments

        elif order_type == "mention_list":
            usernames = extra_params.get("usernames", "")
            data["usernames"] = usernames

        elif order_type == "mention_hashtag":
            data["quantity"] = quantity
            data["hashtag"] = extra_params.get("hashtag", "")

        elif order_type == "mention_follower":
            data["quantity"] = quantity
            data["username"] = extra_params.get("username", "")

        elif order_type == "mention_media":
            data["quantity"] = quantity
            data["media"] = extra_params.get("media", "")

        elif order_type == "poll":
            data["quantity"] = quantity
            data["answer_number"] = int(extra_params.get("answer_number", 0))

        elif order_type == "comment_reply":
            data["username"] = extra_params.get("username", "")
            data["comments"] = extra_params.get("comments", "")

        elif order_type == "comment_likes":
            data["quantity"] = quantity
            data["username"] = extra_params.get("username", "")

        else:
            raise PPOBError(f"Unknown order_type: {order_type!r}")

        result = await self._post("/api/order", extra_data=data)

        if not result.get("status"):
            raise PPOBOrderError(
                result.get("msg", "Order creation failed"),
                response=result,
            )

        return result

    async def check_order_status(
        self, order_ids: Union[int, list[int]]
    ) -> dict:
        """Check the status of one or more orders.

        *order_ids* can be a single ``int`` or a list of up to 50 ``int`` values.
        When a list is provided the IDs are joined with commas.

        Single-order response::

            {
                "status": true,
                "order_id": 1107,
                "order_status": "processing",  # pending/processing/completed/canceled/partial
                "charge": 10000,
                "start_count": 10,
                "remains": 90
            }

        Bulk response::

            {
                "status": true,
                "orders": {
                    "1107": {"order_status": "processing", ...},
                    "1234": {"order_status": "completed", ...}
                }
            }

        Requirements: 9.1, 9.4, 9.5
        """
        if isinstance(order_ids, list):
            if len(order_ids) > 50:
                raise PPOBError(
                    f"check_order_status accepts at most 50 order IDs, got {len(order_ids)}"
                )
            id_str = ",".join(str(oid) for oid in order_ids)
        else:
            id_str = str(order_ids)

        return await self._post("/api/status", extra_data={"id": id_str})

    async def create_refill(self, order_id: int) -> dict:
        """Request a refill for a completed order.

        Returns on success::

            {"status": true, "msg": "...", "refill": <refill_id>}

        Raises :class:`PPOBOrderError` when the provider returns ``status: false``.

        Requirements: 9.1, 9.4
        """
        result = await self._post("/api/refill", extra_data={"id": order_id})

        if not result.get("status"):
            raise PPOBOrderError(
                result.get("msg", "Refill creation failed"),
                response=result,
            )

        return result

    async def check_refill_status(self, refill_id: int) -> dict:
        """Check the status of a refill request.

        Returns::

            {
                "status": true,
                "refill_id": 1107,
                "refill_status": "processing"  # pending/processing/completed/rejected/failed
            }

        Requirements: 9.1, 9.4
        """
        return await self._post("/api/refill/status", extra_data={"id": refill_id})
