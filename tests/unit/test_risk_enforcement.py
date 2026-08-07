"""Failed risk validation cannot be bypassed (task 7, ADR-001).

The bypasses attempted here are the ones that would actually be reached for
under time pressure: skip the engine, reuse an old approval, edit the candidate
after approval, edit the decision, or hand the execution layer something that
looks close enough.
"""

from __future__ import annotations

from datetime import timedelta
from decimal import Decimal

import pytest
from pydantic import ValidationError

from domain.enums import AssetClass, ExecutionStatus, RiskVerdict
from domain.errors import AuthorityViolationError, RiskBypassError
from execution.gateway import ExecutionGateway, ExecutionMode, SimulatedBroker
from execution.models import ExecutionResult, OrderIntent
from portfolio.models import PortfolioState, Position
from risk.models import RiskDecision, RiskViolation, RiskViolationCode
from strategies.models import TradeCandidate

pytestmark = pytest.mark.invariant


def _other_candidate_id(candidate: TradeCandidate) -> object:
    """A genuinely different candidate id, for approval-reuse tests."""
    return TradeCandidate.derive_id(
        strategy_id=candidate.strategy_id,
        strategy_version=candidate.strategy_version,
        symbol="OTHR",
        as_of=candidate.as_of,
    )


def _rejected_decision(candidate: TradeCandidate) -> RiskDecision:
    fingerprint = candidate.authoritative_fingerprint()
    return RiskDecision(
        decision_id=RiskDecision.derive_id(
            candidate_fingerprint=fingerprint, limits_fingerprint="b" * 64
        ),
        candidate_id=candidate.candidate_id,
        candidate_fingerprint=fingerprint,
        verdict=RiskVerdict.REJECTED,
        limits_name="fixture_limits",
        limits_fingerprint="b" * 64,
        evaluated_at=candidate.as_of,
        violations=(
            RiskViolation(
                code=RiskViolationCode.ACCOUNT_RISK_EXCEEDED,
                detail="too much",
                observed="0.5",
                limit="0.01",
            ),
        ),
    )


class TestEngineRejects:
    """The engine says no when it should, and says why."""

    def test_oversized_account_risk_is_rejected(self, risk_engine, portfolio, candidate, base_time):
        greedy = candidate.model_copy(update={"account_risk_fraction": Decimal("0.5")})
        decision = risk_engine.evaluate(greedy, portfolio, evaluated_at=base_time)
        assert not decision.is_approved
        assert RiskViolationCode.ACCOUNT_RISK_EXCEEDED in {v.code for v in decision.violations}

    def test_oversized_notional_is_rejected(self, risk_engine, portfolio, candidate, base_time):
        huge = candidate.model_copy(update={"quantity": Decimal("1000")})
        decision = risk_engine.evaluate(huge, portfolio, evaluated_at=base_time)
        assert not decision.is_approved
        assert RiskViolationCode.POSITION_NOTIONAL_EXCEEDED in {v.code for v in decision.violations}

    def test_disallowed_asset_class_is_rejected(self, risk_engine, portfolio, candidate, base_time):
        optioned = candidate.model_copy(update={"asset_class": AssetClass.OPTION})
        decision = risk_engine.evaluate(optioned, portfolio, evaluated_at=base_time)
        assert RiskViolationCode.ASSET_CLASS_NOT_PERMITTED in {v.code for v in decision.violations}

    def test_daily_loss_limit_blocks_new_risk(self, risk_engine, candidate, base_time):
        bleeding = PortfolioState(
            as_of=base_time,
            account_equity=Decimal("100000"),
            cash=Decimal("96000"),
            realized_pnl_today=Decimal("-4000"),
        )
        decision = risk_engine.evaluate(candidate, bleeding, evaluated_at=base_time)
        assert RiskViolationCode.DAILY_LOSS_LIMIT_BREACHED in {v.code for v in decision.violations}

    def test_poor_reward_risk_is_rejected(self, risk_engine, portfolio, candidate, base_time):
        thin = candidate.model_copy(update={"target_price": Decimal("100.50")})
        decision = risk_engine.evaluate(thin, portfolio, evaluated_at=base_time)
        assert RiskViolationCode.REWARD_RISK_TOO_LOW in {v.code for v in decision.violations}

    def test_existing_position_blocks_stacking(self, risk_engine, candidate, base_time):
        held = PortfolioState(
            as_of=base_time,
            account_equity=Decimal("100000"),
            cash=Decimal("50000"),
            positions=(
                Position(
                    symbol="ACME",
                    side=candidate.side,
                    quantity=Decimal("5"),
                    average_price=Decimal("99"),
                    mark_price=Decimal("100"),
                ),
            ),
        )
        decision = risk_engine.evaluate(candidate, held, evaluated_at=base_time)
        assert RiskViolationCode.DUPLICATE_POSITION in {v.code for v in decision.violations}

    def test_all_violations_are_reported_not_just_the_first(
        self, risk_engine, portfolio, candidate, base_time
    ):
        awful = candidate.model_copy(
            update={
                "account_risk_fraction": Decimal("0.9"),
                "quantity": Decimal("1000"),
                "target_price": Decimal("100.10"),
                "asset_class": AssetClass.OPTION,
            }
        )
        decision = risk_engine.evaluate(awful, portfolio, evaluated_at=base_time)
        assert len(decision.violations) >= 4


class TestRejectionCannotBeBypassed:
    """A rejected decision is a wall, not a suggestion."""

    def test_rejected_decision_cannot_produce_an_order_intent(self, candidate, base_time):
        with pytest.raises(RiskBypassError, match="requires an APPROVED risk decision"):
            OrderIntent.from_approved(
                candidate, _rejected_decision(candidate), created_at=base_time
            )

    def test_rejected_decision_cannot_be_edited_into_an_approval(self, candidate):
        rejected = _rejected_decision(candidate)
        with pytest.raises(ValidationError):
            rejected.verdict = RiskVerdict.APPROVED  # type: ignore[misc]

    def test_approved_verdict_with_violations_is_invalid(self, candidate):
        rejected = _rejected_decision(candidate)
        with pytest.raises(ValidationError, match="cannot carry violations"):
            RiskDecision.model_validate(rejected.model_dump() | {"verdict": "APPROVED"})

    def test_flipping_the_verdict_via_model_copy_is_caught_at_the_order_gate(
        self, candidate, base_time
    ):
        # ``model_copy(update=...)`` skips validators, so a rejected decision can
        # be made to *look* approved in memory. The order gate re-checks the
        # decision's coherence, so the forgery dies there instead of reaching a
        # broker. A forgery that also drops the violations is indistinguishable
        # from a real approval at the type level; that residual risk is covered
        # by the ledger recording which engine and limit set issued each
        # decision (docs/PHASE_1_STATUS.md, open question Q-3).
        forged = _rejected_decision(candidate).model_copy(update={"verdict": RiskVerdict.APPROVED})
        assert forged.is_approved
        with pytest.raises(RiskBypassError, match="claims APPROVED while carrying violations"):
            OrderIntent.from_approved(candidate, forged, created_at=base_time)

    def test_rejection_must_state_a_reason(self, candidate):
        fingerprint = candidate.authoritative_fingerprint()
        with pytest.raises(ValidationError, match="must state at least one violation"):
            RiskDecision(
                decision_id=RiskDecision.derive_id(
                    candidate_fingerprint=fingerprint, limits_fingerprint="b" * 64
                ),
                candidate_id=candidate.candidate_id,
                candidate_fingerprint=fingerprint,
                verdict=RiskVerdict.REJECTED,
                limits_name="fixture_limits",
                limits_fingerprint="b" * 64,
                evaluated_at=candidate.as_of,
                violations=(),
            )


class TestApprovalIsBoundToTheExactCandidate:
    """An approval for one trade is not an approval for a bigger one."""

    def test_widening_quantity_after_approval_invalidates_it(
        self, risk_engine, portfolio, candidate, base_time
    ):
        decision = risk_engine.evaluate(candidate, portfolio, evaluated_at=base_time)
        assert decision.is_approved
        widened = candidate.model_copy(update={"quantity": Decimal("500")})
        with pytest.raises(RiskBypassError, match="different version of this candidate"):
            OrderIntent.from_approved(widened, decision, created_at=base_time)

    def test_moving_the_stop_after_approval_invalidates_it(
        self, risk_engine, portfolio, candidate, base_time
    ):
        decision = risk_engine.evaluate(candidate, portfolio, evaluated_at=base_time)
        loosened = candidate.model_copy(update={"stop_price": Decimal("80.00")})
        with pytest.raises(RiskBypassError, match="different version of this candidate"):
            OrderIntent.from_approved(loosened, decision, created_at=base_time)

    def test_approval_for_another_candidate_cannot_be_reused(
        self, risk_engine, portfolio, candidate, base_time
    ):
        decision = risk_engine.evaluate(candidate, portfolio, evaluated_at=base_time)
        other = candidate.model_copy(
            update={"symbol": "OTHR", "candidate_id": _other_candidate_id(candidate)}
        )
        with pytest.raises(RiskBypassError, match="approves candidate"):
            OrderIntent.from_approved(other, decision, created_at=base_time)

    def test_intent_cannot_be_hand_built_around_the_binding(self, candidate, base_time):
        rejected = _rejected_decision(candidate)
        with pytest.raises(RiskBypassError):
            OrderIntent(
                order_intent_id=candidate.candidate_id,
                candidate_id=candidate.candidate_id,
                candidate_fingerprint=candidate.authoritative_fingerprint(),
                strategy_id=candidate.strategy_id,
                strategy_version=candidate.strategy_version,
                risk_decision=rejected,
                symbol=candidate.symbol,
                asset_class=candidate.asset_class,
                side=candidate.side,
                order_type=candidate.order_type,
                limit_price=candidate.limit_price,
                quantity=candidate.quantity,
                stop_price=candidate.stop_price,
                target_price=candidate.target_price,
                time_in_force=candidate.time_in_force,
                created_at=base_time,
            )


class TestGatewayReVerifies:
    """The last gate re-checks, because an intent may have been deserialised."""

    def test_gateway_refuses_a_non_intent(self, candidate):
        gateway = ExecutionGateway(SimulatedBroker(), mode=ExecutionMode.SIMULATED)
        with pytest.raises(TypeError, match="accepts OrderIntent only"):
            gateway.submit(candidate)

    def test_gateway_transmits_a_valid_intent(self, risk_engine, portfolio, candidate, base_time):
        broker = SimulatedBroker()
        gateway = ExecutionGateway(broker, mode=ExecutionMode.SIMULATED)
        decision = risk_engine.evaluate(candidate, portfolio, evaluated_at=base_time)
        intent = OrderIntent.from_approved(candidate, decision, created_at=base_time)
        result = gateway.submit(intent)
        assert result.status.value == "SIMULATED"
        assert broker.submitted == (intent,)

    def test_live_mode_is_refused_outright(self):
        with pytest.raises(AuthorityViolationError, match="live execution is not implemented"):
            ExecutionGateway(SimulatedBroker(), mode=ExecutionMode.LIVE)

    def test_result_timestamps_must_be_ordered(self, risk_engine, portfolio, candidate, base_time):
        decision = risk_engine.evaluate(candidate, portfolio, evaluated_at=base_time)
        intent = OrderIntent.from_approved(candidate, decision, created_at=base_time)
        with pytest.raises(ValidationError, match="cannot precede submitted_at"):
            ExecutionResult(
                result_id=intent.order_intent_id,
                order_intent_id=intent.order_intent_id,
                order_intent_fingerprint=intent.authoritative_fingerprint(),
                status=ExecutionStatus.ACCEPTED,
                venue="simulator",
                submitted_at=base_time,
                reported_at=base_time - timedelta(seconds=1),
            )
