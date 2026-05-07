"""
Input validators for the Telegram Bot PPOB/SMM Reseller.

Provides:
  - validate_topup_amount(amount) — enforce Rp 10.000 – Rp 10.000.000 range
  - generate_reference_code()     — generate a globally-unique reference code
  - TargetValidator               — validate order targets by service type

Requirements: 3.2, 3.3, 6.7
"""
from __future__ import annotations

import re
import uuid
from decimal import Decimal, InvalidOperation
from typing import Tuple

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
TOPUP_MIN = Decimal("10000")
TOPUP_MAX = Decimal("10000000")


# ---------------------------------------------------------------------------
# Top-up amount validation
# ---------------------------------------------------------------------------

def validate_topup_amount(amount: int | float | Decimal | str) -> Tuple[bool, str]:
    """Validate that *amount* is within the allowed top-up range.

    Returns a ``(valid, error_message)`` tuple.  When valid the error message
    is an empty string.

    Rules:
      - Must be a positive number
      - Minimum: Rp 10.000
      - Maximum: Rp 10.000.000

    Requirements: 3.2, 3.3
    """
    try:
        value = Decimal(str(amount))
    except (InvalidOperation, ValueError):
        return False, "Nominal tidak valid. Masukkan angka yang benar."

    if value < TOPUP_MIN:
        return (
            False,
            f"Nominal terlalu kecil. Minimum top up adalah Rp {int(TOPUP_MIN):,}."
            .replace(",", "."),
        )
    if value > TOPUP_MAX:
        return (
            False,
            f"Nominal terlalu besar. Maksimum top up adalah Rp {int(TOPUP_MAX):,}."
            .replace(",", "."),
        )
    return True, ""


# ---------------------------------------------------------------------------
# Reference code generation
# ---------------------------------------------------------------------------

def generate_reference_code() -> str:
    """Generate a globally-unique reference code for a top-up request.

    The code is based on a UUID4 (128-bit random), formatted as a compact
    uppercase hex string (32 characters) to keep it human-readable while
    guaranteeing uniqueness.

    Requirements: 3.1
    """
    return uuid.uuid4().hex.upper()


# ---------------------------------------------------------------------------
# Target validation
# ---------------------------------------------------------------------------

class TargetValidator:
    """Validate order targets according to the service type.

    Requirements: 6.7
    """

    # Full-match patterns — anchored at both ends to prevent partial matches.
    _URL_PATTERN = re.compile(r'^https?://[^\s]+$')
    _USERNAME_PATTERN = re.compile(r'^[a-zA-Z0-9_.]{1,100}$')
    _EMAIL_PATTERN = re.compile(r'^[a-zA-Z0-9_.+\-]+@[a-zA-Z0-9\-]+\.[a-zA-Z0-9.\-]+$')

    @staticmethod
    def validate_url(target: str) -> bool:
        """Return True if *target* is a valid HTTP or HTTPS URL.

        The URL must start with ``http://`` or ``https://`` and contain no
        whitespace characters.
        """
        if not target:
            return False
        return bool(TargetValidator._URL_PATTERN.match(target))

    @staticmethod
    def validate_email(target: str) -> bool:
        """Return True if *target* is a valid email address."""
        if not target:
            return False
        return bool(TargetValidator._EMAIL_PATTERN.match(target))

    @staticmethod
    def validate_username(target: str) -> bool:
        """Return True if *target* is a valid username.

        Rules:
          - 1 to 100 characters
          - Alphanumeric, underscores, dots (username.name format)
        """
        if not target:
            return False
        return bool(TargetValidator._USERNAME_PATTERN.match(target))

    @staticmethod
    def validate(service_type: str, target: str) -> bool:
        """Dispatch to the appropriate validator based on *service_type*.

        Known service types:
          - ``'url'``      → :meth:`validate_url`
          - ``'email'``    → :meth:`validate_email`
          - ``'username'`` → :meth:`validate_username`

        Any unknown service type is accepted (returns ``True``) to avoid
        blocking services whose target format is not yet defined.
        """
        validators = {
            "url": TargetValidator.validate_url,
            "email": TargetValidator.validate_email,
            "username": TargetValidator.validate_username,
        }
        validator_fn = validators.get(service_type, lambda x: True)
        return validator_fn(target)
