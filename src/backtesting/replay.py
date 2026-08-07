"""Deterministic decision replay.

This is **not** a P&L backtest. It replays a strategy over a fixed series of
snapshots and records what each layer decided: candidate or no-trade, approved
or rejected, vetoed or affirmed, transmitted or not. Positions are not
simulated and no equity curve is produced — modelling fills and slippage is a
later phase, and a number that looks like a return but isn't is worse than no
number at all.

What it does give you today is the thing Phase 1 needs: proof that a strategy,
a limit set and a reviewer configuration produce the same decisions every time
they see the same history.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import TYPE_CHECKING

from pydantic import Field

from agents.gate import AgentReviewer, run_reviewers
from domain.base import FrozenModel
from domain.enums import ExecutionStatus
from execution.gateway import ExecutionGateway, ExecutionMode, SimulatedBroker
from execution.models import OrderIntent
from market_data.models import MarketSnapshot
from portfolio.models import PortfolioState
from risk.engine import RiskEngine
from strategies.context import EvaluationContext
from strategies.determinism import decision_fingerprint
from strategies.models import NoTrade, TradeCandidate

if TYPE_CHECKING:
    from strategies.protocols import Strategy


class ReplayStep(FrozenModel):
    """What happened at one instant of the replay."""

    as_of: str
    symbol: str
    decision_fingerprint: str
    produced_candidate: bool
    risk_approved: bool | None = None
    risk_violation_codes: tuple[str, ...] = ()
    agent_vetoed: bool | None = None
    execution_status: ExecutionStatus | None = None


class ReplayReport(FrozenModel):
    """Aggregate outcome of a replay."""

    strategy_id: str
    strategy_version: str
    limits_name: str
    steps: tuple[ReplayStep, ...] = ()
    order_intents: tuple[OrderIntent, ...] = Field(default=(), repr=False)

    @property
    def candidate_count(self) -> int:
        """Snapshots that produced a candidate."""
        return sum(1 for s in self.steps if s.produced_candidate)

    @property
    def no_trade_count(self) -> int:
        """Snapshots that produced a no-trade."""
        return sum(1 for s in self.steps if not s.produced_candidate)

    @property
    def risk_rejected_count(self) -> int:
        """Candidates the risk engine rejected."""
        return sum(1 for s in self.steps if s.risk_approved is False)

    @property
    def agent_vetoed_count(self) -> int:
        """Risk-approved candidates an agent vetoed."""
        return sum(1 for s in self.steps if s.agent_vetoed is True)

    @property
    def transmitted_count(self) -> int:
        """Intents that reached the (simulated) broker."""
        return len(self.order_intents)

    def fingerprint(self) -> str:
        """Digest of every decision, in order. Equal reports replay identically."""
        from hashlib import sha256

        joined = "|".join(step.decision_fingerprint for step in self.steps)
        return sha256(joined.encode("utf-8")).hexdigest()


def run_replay(
    *,
    strategy: Strategy,
    snapshots: Sequence[MarketSnapshot],
    risk_engine: RiskEngine,
    portfolio: PortfolioState,
    reviewers: Sequence[AgentReviewer] = (),
    broker: SimulatedBroker | None = None,
) -> ReplayReport:
    """Replay ``strategy`` over ``snapshots`` through the full decision chain.

    Snapshots are processed in chronological order and each evaluation sees only
    the snapshots that preceded it, so a strategy cannot accidentally read the
    future through the history window.
    """
    gateway = ExecutionGateway(broker or SimulatedBroker(), mode=ExecutionMode.SIMULATED)
    ordered = sorted(snapshots, key=lambda s: (s.as_of, s.symbol))
    history: dict[str, list[MarketSnapshot]] = {}

    steps: list[ReplayStep] = []
    intents: list[OrderIntent] = []

    for snapshot in ordered:
        prior = tuple(history.get(snapshot.symbol, ()))
        context = EvaluationContext.build(snapshot=snapshot, history=prior)
        decision = strategy.evaluate(context)
        history.setdefault(snapshot.symbol, []).append(snapshot)

        if isinstance(decision, NoTrade):
            steps.append(
                ReplayStep(
                    as_of=snapshot.as_of.isoformat(),
                    symbol=snapshot.symbol,
                    decision_fingerprint=decision_fingerprint(decision),
                    produced_candidate=False,
                )
            )
            continue

        candidate: TradeCandidate = decision
        risk_decision = risk_engine.evaluate(candidate, portfolio, evaluated_at=snapshot.as_of)
        if not risk_decision.is_approved:
            steps.append(
                ReplayStep(
                    as_of=snapshot.as_of.isoformat(),
                    symbol=snapshot.symbol,
                    decision_fingerprint=decision_fingerprint(candidate),
                    produced_candidate=True,
                    risk_approved=False,
                    risk_violation_codes=tuple(v.code.value for v in risk_decision.violations),
                )
            )
            continue

        gate = run_reviewers(candidate, context, list(reviewers))
        if gate.vetoed:
            steps.append(
                ReplayStep(
                    as_of=snapshot.as_of.isoformat(),
                    symbol=snapshot.symbol,
                    decision_fingerprint=decision_fingerprint(candidate),
                    produced_candidate=True,
                    risk_approved=True,
                    agent_vetoed=True,
                )
            )
            continue

        intent = OrderIntent.from_approved(
            candidate,
            risk_decision,
            created_at=snapshot.as_of,
            agent_reviews=gate.reviews,
        )
        result = gateway.submit(intent)
        intents.append(intent)
        steps.append(
            ReplayStep(
                as_of=snapshot.as_of.isoformat(),
                symbol=snapshot.symbol,
                decision_fingerprint=decision_fingerprint(candidate),
                produced_candidate=True,
                risk_approved=True,
                agent_vetoed=False,
                execution_status=result.status,
            )
        )

    return ReplayReport(
        strategy_id=strategy.metadata.strategy_id,
        strategy_version=strategy.version,
        limits_name=risk_engine.limits.name,
        steps=tuple(steps),
        order_intents=tuple(intents),
    )
