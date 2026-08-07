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
    stop_price: Price | None = Field(
        default=None,
        description="Protective stop still working at the venue. None means the "
        "position is unprotected, which the open-risk gate treats as full notional "
        "at risk rather than zero.",
    )
    correlation_group: str | None = Field(
        default=None,
        max_length=64,
        description="Bucket for correlated-exposure limits, e.g. a sector or theme. "
        "Assigned by control-plane configuration, not by a strategy.",
    )

    @property
    def open_risk(self) -> Decimal:
        """Currency still at risk if the stop fills.

        An unprotected position risks its whole notional. Reporting zero would
        make the portfolio-risk gate read best-case, which is the wrong
        direction to be wrong in.
        """
        if self.stop_price is None:
            return self.notional
        return abs(self.average_price - self.stop_price) * self.quantity

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
    realized_pnl_week: SignedAmount = Decimal("0")
    peak_equity: ExactDecimal | None = Field(
        default=None,
        gt=0,
        description="Equity high-water mark, for the drawdown gate. None means no "
        "history is available and the gate is skipped rather than guessed.",
    )
    consecutive_losses: int = Field(default=0, ge=0)

    @property
    def open_position_count(self) -> int:
        """Number of distinct open positions."""
        return len(self.positions)

    @property
    def gross_exposure(self) -> Decimal:
        """Sum of absolute position notionals."""
        return sum((p.notional for p in self.positions), Decimal("0"))

    @property
    def open_risk(self) -> Decimal:
        """Total currency at risk across every open position."""
        return sum((p.open_risk for p in self.positions), Decimal("0"))

    @property
    def drawdown_fraction(self) -> Decimal | None:
        """Decline from the high-water mark, or None when unknown."""
        if self.peak_equity is None or self.peak_equity <= 0:
            return None
        return max(Decimal("0"), (self.peak_equity - self.account_equity) / self.peak_equity)

    def exposure_in_group(self, group: str) -> Decimal:
        """Notional held within one correlation group."""
        return sum(
            (p.notional for p in self.positions if p.correlation_group == group), Decimal("0")
        )

    def position_for(self, symbol: Symbol) -> Position | None:
        """Return the open position in ``symbol``, if any."""
        return next((p for p in self.positions if p.symbol == symbol), None)
