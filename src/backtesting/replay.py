"""Deterministic decision replay.

This is **not** a P&L backtest. It replays a strategy over a fixed series of
snapshots and records what each layer decided: candidate or no-trade, approved
or rejected, agent-gated or clear, validated or refused, transmitted or not.
Positions are not simulated and no equity curve is produced — modelling fills
and slippage is a later phase, and a number that looks like a return but isn't
is worse than no number at all.

The replay drives the **same** components production uses: the same risk engine,
the same cached agent-context gate, the same final validator, the same execution
engine. A replay that exercised a parallel code path would prove nothing about
the path that trades.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import TYPE_CHECKING

from pydantic import Field

from agents.store import AgentContextStore
from brokers.simulated import SimulatedBroker
from control_plane.config import ControlPlaneConfig
from domain.base import FrozenModel
from domain.enums import ExecutionStatus
from execution.engine import ExecutionEngine
from execution.killswitch import KillSwitchRegistry
from execution.models import OrderIntent
from execution.validator import FinalValidator, ValidationRequest
from market_data.models import MarketSnapshot
from portfolio.models import PortfolioState
from risk.engine import RiskEngine
from strategies.context import EvaluationContext
from strategies.determinism import decision_fingerprint
from strategies.models import NoTrade, StrategyVersion, TradeCandidate

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
    validation_approved: bool | None = None
    rejection_codes: tuple[str, ...] = ()
    execution_status: ExecutionStatus | None = None


class ReplayReport(FrozenModel):
    """Aggregate outcome of a replay."""

    strategy_id: str
    strategy_version: str
    limits_name: str
    execution_mode: str
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
    def validation_rejected_count(self) -> int:
        """Intents the final validator refused."""
        return sum(1 for s in self.steps if s.validation_approved is False)

    @property
    def agent_vetoed_count(self) -> int:
        """Intents refused specifically because agent context blocked them."""
        return sum(1 for s in self.steps if "AGENT_CONTEXT_BLOCKS" in s.rejection_codes)

    @property
    def transmitted_count(self) -> int:
        """Intents that reached the execution engine and passed every gate."""
        return len(self.order_intents)

    def rejection_counts(self) -> dict[str, int]:
        """Histogram of rejection codes. The first thing to look at after a run."""
        counts: dict[str, int] = {}
        for step in self.steps:
            for code in step.rejection_codes:
                counts[code] = counts.get(code, 0) + 1
        return counts

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
    config: ControlPlaneConfig,
    strategy_version: StrategyVersion | None = None,
    agent_store: AgentContextStore | None = None,
    kill_switches: KillSwitchRegistry | None = None,
    broker: SimulatedBroker | None = None,
) -> ReplayReport:
    """Replay ``strategy`` over ``snapshots`` through the full decision chain.

    Snapshots are processed in chronological order and each evaluation sees only
    the snapshots that preceded it, so a strategy cannot accidentally read the
    future through the history window.
    """
    switches = kill_switches or KillSwitchRegistry()
    sim = broker or SimulatedBroker()
    engine = ExecutionEngine(
        broker=sim,
        validator=FinalValidator(kill_switches=switches, agent_store=agent_store),
        kill_switches=switches,
    )

    ordered = sorted(snapshots, key=lambda s: (s.as_of, s.symbol))
    history: dict[str, list[MarketSnapshot]] = {}
    steps: list[ReplayStep] = []
    intents: list[OrderIntent] = []

    for snapshot in ordered:
        sim.set_clock(snapshot.as_of)
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

        intent = OrderIntent.from_approved(
            candidate,
            risk_decision,
            created_at=snapshot.as_of,
            configuration_hash=config.configuration_hash,
            market_snapshot_hash=snapshot.authoritative_fingerprint(),
        )
        result = engine.submit(
            intent,
            config=config,
            validation=ValidationRequest(
                intent=intent,
                now=snapshot.as_of,
                config=config,
                limits=risk_engine.limits,
                portfolio=portfolio,
                snapshot=snapshot,
                account_state=None,
                broker_health=sim.health_check(),
                strategy_version=strategy_version,
            ),
        )
        rejected = result.status is ExecutionStatus.REJECTED
        codes = (
            tuple(result.message.removeprefix("ORDER_REJECTED: ").split(", "))
            if rejected and result.message
            else ()
        )
        if not rejected:
            intents.append(intent)

        steps.append(
            ReplayStep(
                as_of=snapshot.as_of.isoformat(),
                symbol=snapshot.symbol,
                decision_fingerprint=decision_fingerprint(candidate),
                produced_candidate=True,
                risk_approved=True,
                validation_approved=not rejected,
                rejection_codes=codes,
                execution_status=result.status,
            )
        )

    return ReplayReport(
        strategy_id=strategy.metadata.strategy_id,
        strategy_version=strategy.version,
        limits_name=risk_engine.limits.name,
        execution_mode=config.execution_mode.value,
        steps=tuple(steps),
        order_intents=tuple(intents),
    )
