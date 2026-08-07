"""Deterministic risk validation.

The engine is a pure function of ``(candidate, limits, portfolio)``. It collects
*every* violation rather than short-circuiting on the first, because an operator
fixing one breach needs to know whether three more are waiting behind it.
"""

from __future__ import annotations

from decimal import Decimal

from domain.enums import RiskVerdict
from domain.values import TimestampUTC
from portfolio.models import PortfolioState
from risk.limits import RiskLimits
from risk.models import RiskDecision, RiskViolation, RiskViolationCode
from strategies.models import TradeCandidate


def _violation(
    code: RiskViolationCode, detail: str, observed: Decimal | int, limit: Decimal | int
) -> RiskViolation:
    return RiskViolation(code=code, detail=detail, observed=str(observed), limit=str(limit))


class RiskEngine:
    """Approves or rejects candidates against a named limit set.

    The engine holds no mutable state. Two calls with the same arguments produce
    the same decision, including the same ``decision_id``.
    """

    def __init__(self, limits: RiskLimits) -> None:
        self._limits = limits

    @property
    def limits(self) -> RiskLimits:
        """The limit set this engine applies."""
        return self._limits

    def evaluate(
        self,
        candidate: TradeCandidate,
        portfolio: PortfolioState,
        *,
        evaluated_at: TimestampUTC,
    ) -> RiskDecision:
        """Judge ``candidate`` against the configured limits and current book."""
        limits = self._limits
        violations: list[RiskViolation] = []

        if candidate.asset_class not in limits.allowed_asset_classes:
            violations.append(
                _violation(
                    RiskViolationCode.ASSET_CLASS_NOT_PERMITTED,
                    f"{candidate.asset_class} is not in the permitted asset classes",
                    observed=Decimal(0),
                    limit=Decimal(0),
                )
            )

        if candidate.account_risk_fraction > limits.max_account_risk_fraction:
            violations.append(
                _violation(
                    RiskViolationCode.ACCOUNT_RISK_EXCEEDED,
                    "candidate risks more of the account than the limit permits",
                    observed=candidate.account_risk_fraction,
                    limit=limits.max_account_risk_fraction,
                )
            )

        notional = candidate.quantity * candidate.reference_price
        notional_fraction = notional / portfolio.account_equity
        if notional_fraction > limits.max_position_notional_fraction:
            violations.append(
                _violation(
                    RiskViolationCode.POSITION_NOTIONAL_EXCEEDED,
                    "position notional exceeds the permitted fraction of equity",
                    observed=notional_fraction,
                    limit=limits.max_position_notional_fraction,
                )
            )

        projected_gross = (portfolio.gross_exposure + notional) / portfolio.account_equity
        if projected_gross > limits.max_gross_exposure_fraction:
            violations.append(
                _violation(
                    RiskViolationCode.GROSS_EXPOSURE_EXCEEDED,
                    "projected gross exposure exceeds the permitted multiple of equity",
                    observed=projected_gross,
                    limit=limits.max_gross_exposure_fraction,
                )
            )

        existing = portfolio.position_for(candidate.symbol)
        if existing is not None:
            violations.append(
                _violation(
                    RiskViolationCode.DUPLICATE_POSITION,
                    f"an open position in {candidate.symbol} already exists; "
                    "stacking is not permitted in phase 1",
                    observed=existing.quantity,
                    limit=Decimal(0),
                )
            )
        elif portfolio.open_position_count >= limits.max_open_positions:
            violations.append(
                _violation(
                    RiskViolationCode.MAX_OPEN_POSITIONS_EXCEEDED,
                    "opening this position would exceed the concurrent position limit",
                    observed=portfolio.open_position_count + 1,
                    limit=limits.max_open_positions,
                )
            )

        if portfolio.realized_pnl_today < 0:
            loss_fraction = -portfolio.realized_pnl_today / portfolio.account_equity
            if loss_fraction >= limits.max_daily_loss_fraction:
                violations.append(
                    _violation(
                        RiskViolationCode.DAILY_LOSS_LIMIT_BREACHED,
                        "the daily loss limit has been reached; no new risk today",
                        observed=loss_fraction,
                        limit=limits.max_daily_loss_fraction,
                    )
                )

        if candidate.target_price is not None:
            reward = abs(candidate.target_price - candidate.reference_price)
            reward_risk = reward / candidate.risk_per_unit
            if reward_risk < limits.min_reward_risk_ratio:
                violations.append(
                    _violation(
                        RiskViolationCode.REWARD_RISK_TOO_LOW,
                        "reward-to-risk ratio is below the configured minimum",
                        observed=reward_risk,
                        limit=limits.min_reward_risk_ratio,
                    )
                )

        candidate_fingerprint = candidate.authoritative_fingerprint()
        limits_fingerprint = limits.authoritative_fingerprint()
        verdict = RiskVerdict.REJECTED if violations else RiskVerdict.APPROVED

        return RiskDecision(
            decision_id=RiskDecision.derive_id(
                candidate_fingerprint=candidate_fingerprint,
                limits_fingerprint=limits_fingerprint,
            ),
            candidate_id=candidate.candidate_id,
            candidate_fingerprint=candidate_fingerprint,
            verdict=verdict,
            limits_name=limits.name,
            limits_fingerprint=limits_fingerprint,
            evaluated_at=evaluated_at,
            violations=tuple(violations),
        )
