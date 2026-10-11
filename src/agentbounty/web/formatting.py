"""Money as text. Amounts stay integers; no float ever touches them."""

from __future__ import annotations

from decimal import Decimal, InvalidOperation

ZATOSHI_PER_ZEC = 100_000_000


def format_zec(zatoshi: int) -> str:
    """Zatoshi as a ZEC amount with no trailing zeros: 250000000 -> '2.5'."""
    sign = "-" if zatoshi < 0 else ""
    whole, fraction = divmod(abs(zatoshi), ZATOSHI_PER_ZEC)
    digits = f"{fraction:08d}".rstrip("0")
    return f"{sign}{whole}.{digits}" if digits else f"{sign}{whole}"


def parse_zec(text: str) -> int:
    """A ZEC amount typed by a person, as zatoshi. Raises ValueError if it is not exact."""
    try:
        amount = Decimal(text.strip())
    except InvalidOperation:
        raise ValueError("enter the reward as a number of ZEC, such as 0.5") from None
    zatoshi = amount * ZATOSHI_PER_ZEC
    if not amount.is_finite() or zatoshi != zatoshi.to_integral_value():
        raise ValueError("a ZEC amount has at most 8 decimal places")
    return int(zatoshi)
