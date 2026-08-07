"""Constrained value types.

Two rules are enforced here and relied on everywhere else:

1. **No binary floats for money or size.** ``float`` inputs are rejected rather
   than coerced, because ``Decimal(0.1)`` is not ``Decimal("0.1")`` and a
   fingerprint computed over a silently-widened float is not reproducible.
2. **No naive datetimes.** Every timestamp carries an explicit UTC offset, so
   ordering in the ledger is total and unambiguous.
"""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from typing import Annotated, Any

from pydantic import AfterValidator, BeforeValidator, Field, StringConstraints


def _reject_float(value: Any) -> Any:
    """Refuse binary floats where an exact decimal is required."""
    if isinstance(value, float):
        raise ValueError(
            "float is not accepted for exact numeric fields; pass Decimal or str "
            "(e.g. Decimal('10.25') or '10.25')"
        )
    return value


def _require_utc(value: datetime) -> datetime:
    """Require an aware datetime and normalise it to UTC."""
    if value.tzinfo is None:
        raise ValueError("naive datetimes are not accepted; supply an explicit tzinfo")
    return value.astimezone(UTC)


ExactDecimal = Annotated[Decimal, BeforeValidator(_reject_float), Field(allow_inf_nan=False)]
"""A decimal that never originates from a binary float."""

Price = Annotated[ExactDecimal, Field(gt=0, max_digits=20, decimal_places=8)]
"""A strictly positive price."""

Quantity = Annotated[ExactDecimal, Field(gt=0, max_digits=20, decimal_places=8)]
"""A strictly positive size. Direction lives in :class:`~domain.enums.Side`."""

SignedAmount = Annotated[ExactDecimal, Field(max_digits=24, decimal_places=8)]
"""A monetary amount that may be negative (P&L, cash deltas)."""

Ratio = Annotated[ExactDecimal, Field(ge=0, le=1, max_digits=12, decimal_places=8)]
"""A unit-interval fraction, e.g. a fraction of account equity at risk."""


def _normalize_symbol(value: Any) -> Any:
    """Trim and upper-case before the pattern is applied.

    ``StringConstraints`` checks its pattern against the raw input, so the
    normalisation has to happen in a before-validator or ``" acme "`` would be
    rejected rather than tidied.
    """
    if isinstance(value, str):
        return value.strip().upper()
    return value


Symbol = Annotated[
    str,
    BeforeValidator(_normalize_symbol),
    StringConstraints(min_length=1, max_length=16, pattern=r"^[A-Z0-9.\-/]+$"),
]
"""A venue-agnostic instrument symbol, normalised to upper case."""

TimestampUTC = Annotated[datetime, AfterValidator(_require_utc)]
"""A timezone-aware instant, normalised to UTC."""

NonEmptyText = Annotated[
    str, StringConstraints(strip_whitespace=True, min_length=1, max_length=4000)
]
"""Free text that must actually say something (rationales, reasons)."""
