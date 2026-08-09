"""The complete, closed input to a strategy evaluation.

Everything a strategy is allowed to read is in this object. Anything not in here
— the clock, the network, a global, the broker — is off limits. Bundling the
inputs into one frozen record is what lets a test say "same input, same output"
and mean it.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Sequence

from pydantic import Field

from domain.base import FrozenModel, canonical_form
from domain.values import Symbol, TimestampUTC
from market_data.models import MarketSnapshot
from regime.models import RegimeAssessment
from signals.models import SignalSet


class PortfolioView(FrozenModel):
    """What the strategy may know about current exposure.

    Deliberately narrow: a strategy sizes a trade against account risk, it does
    not get to inspect the whole book and improvise.
    """

    account_equity: str = Field(
        description="Account equity as an exact decimal string. String-typed so the "
        "value that entered the decision is the value that gets fingerprinted."
    )
    open_position_quantity: str = "0"
    open_position_symbols: tuple[Symbol, ...] = ()


class EvaluationContext(FrozenModel):
    """Immutable bundle of every input to one strategy evaluation."""

    symbol: Symbol
    as_of: TimestampUTC
    snapshot: MarketSnapshot
    history: tuple[MarketSnapshot, ...] = ()
    signals: SignalSet | None = None
    regime: RegimeAssessment | None = None
    portfolio: PortfolioView | None = None

    def fingerprint(self) -> str:
        """SHA-256 over every input.

        Stored on the resulting decision as ``inputs_fingerprint``, giving a
        two-sided audit trail: which inputs produced this decision, and whether
        those inputs have since changed.

        Built as a digest *of digests* rather than a deep walk of every nested
        field. Each member already fingerprints itself and memoises the result,
        so a snapshot shared by two hundred overlapping look-back windows is
        hashed once rather than two hundred times. The security property is the
        same — a change anywhere still changes the answer — and the cost drops
        from quadratic to linear, which matters in the hot path as much as in a
        backtest.
        """
        parts: list[str] = [
            "symbol",
            self.symbol,
            "as_of",
            self.as_of.isoformat(),
            "snapshot",
            self.snapshot.authoritative_fingerprint(),
            "history",
            *(s.authoritative_fingerprint() for s in self.history),
        ]
        for name in ("signals", "regime", "portfolio"):
            member = getattr(self, name)
            parts.append(name)
            parts.append(
                "\x00"
                if member is None
                else hashlib.sha256(
                    json.dumps(
                        canonical_form(member), sort_keys=True, separators=(",", ":")
                    ).encode()
                ).hexdigest()
            )
        return hashlib.sha256("\x1f".join(parts).encode("utf-8")).hexdigest()

    @classmethod
    def build(
        cls,
        *,
        snapshot: MarketSnapshot,
        history: Sequence[MarketSnapshot] = (),
        signals: SignalSet | None = None,
        regime: RegimeAssessment | None = None,
        portfolio: PortfolioView | None = None,
    ) -> EvaluationContext:
        """Construct a context anchored to ``snapshot``'s symbol and instant."""
        return cls(
            symbol=snapshot.symbol,
            as_of=snapshot.as_of,
            snapshot=snapshot,
            history=tuple(history),
            signals=signals,
            regime=regime,
            portfolio=portfolio,
        )
