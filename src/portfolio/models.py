"""Immutable portfolio snapshots."""

from __future__ import annotations

from decimal import Decimal

from pydantic import Field

from domain.base import FrozenModel
from domain.enums import AssetClass, Side
from domain.values import ExactDecimal, Price, Quantity, SignedAmount, Symbol, TimestampUTC


class Position(FrozenModel):
    """One open position."""

    symbol: Symbol
    asset_class: AssetClass = AssetClass.EQUITY
    side: Side
    quantity: Quantity
    average_price: Price
    mark_price: Price

    @property
    def notional(self) -> Decimal:
        """Current market value of the position."""
        return self.quantity * self.mark_price

    @property
    def unrealized_pnl(self) -> Decimal:
        """Mark-to-market P&L, signed by direction."""
        delta = self.mark_price - self.average_price
        if self.side is Side.SELL:
            delta = -delta
        return delta * self.quantity


class PortfolioState(FrozenModel):
    """A point-in-time view of the account.

    ``as_of`` is explicit rather than implied by "now", so a risk evaluation can
    be replayed against the exact state that produced the original decision.
    """

    as_of: TimestampUTC
    account_equity: ExactDecimal = Field(gt=0)
    cash: SignedAmount
    positions: tuple[Position, ...] = ()
    realized_pnl_today: SignedAmount = Decimal("0")

    @property
    def open_position_count(self) -> int:
        """Number of distinct open positions."""
        return len(self.positions)

    @property
    def gross_exposure(self) -> Decimal:
        """Sum of absolute position notionals."""
        return sum((p.notional for p in self.positions), Decimal("0"))

    def position_for(self, symbol: Symbol) -> Position | None:
        """Return the open position in ``symbol``, if any."""
        return next((p for p in self.positions if p.symbol == symbol), None)
