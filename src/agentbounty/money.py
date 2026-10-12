"""Money as text. Amounts stay integers; no float ever touches them.

Every amount in the system is an integer count of the currency's smallest unit:
zatoshi for ZEC, lamports for SOL. Which currency that is follows from the wallet
the deployment uses. (The database columns and API fields are still named
`*_zatoshi`, from when Zcash was the only choice; they hold the smallest unit
of whatever the currency is.)
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, InvalidOperation


@dataclass(frozen=True)
class Currency:
    symbol: str
    decimals: int
    # What the smallest unit is called.
    unit: str
    # Whether payments hide who paid whom, and how much.
    private: bool

    @property
    def per_whole(self) -> int:
        result: int = 10**self.decimals
        return result


ZEC = Currency(symbol="ZEC", decimals=8, unit="zatoshi", private=True)
SOL = Currency(symbol="SOL", decimals=9, unit="lamports", private=False)

ZATOSHI_PER_ZEC = ZEC.per_whole


def format_amount(units: int, currency: Currency = ZEC) -> str:
    """Smallest units as a whole-currency amount with no trailing zeros: 250000000 -> '2.5'."""
    sign = "-" if units < 0 else ""
    whole, fraction = divmod(abs(units), currency.per_whole)
    digits = f"{fraction:0{currency.decimals}d}".rstrip("0")
    return f"{sign}{whole}.{digits}" if digits else f"{sign}{whole}"


def parse_amount(text: str, currency: Currency = ZEC) -> int:
    """An amount typed by a person, in smallest units. Raises ValueError if it is not exact."""
    try:
        amount = Decimal(text.strip())
    except InvalidOperation:
        raise ValueError(
            f"enter the reward as a number of {currency.symbol}, such as 0.5"
        ) from None
    units = amount * currency.per_whole
    if not amount.is_finite() or units != units.to_integral_value():
        raise ValueError(
            f"a {currency.symbol} amount has at most {currency.decimals} decimal places"
        )
    return int(units)


def format_zec(zatoshi: int) -> str:
    return format_amount(zatoshi, ZEC)


def parse_zec(text: str) -> int:
    return parse_amount(text, ZEC)
