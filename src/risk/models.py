"""Risk decisions.

A :class:`RiskDecision` is a *binding* verdict, not an opinion. It records the
fingerprint of the exact candidate it judged, so an approval cannot be
transplanted onto a different — larger, closer-stopped, differently-sided —
candidate downstream (ADR-001).

The risk engine approves or rejects. It never rewrites quantity, stop or target:
that would make it a second author of trading parameters and there is only one.
"""

from __future__ import annotations

from enum import StrEnum, unique
from typing import ClassVar, Self
from uuid import UUID

from pydantic import Field, model_validator

from domain.base import AuthoritativeModel, FrozenModel
from domain.enums import RiskVerdict
from domain.identifiers import DeterministicId
from domain.values import NonEmptyText, TimestampUTC


@unique
class RiskViolationCode(StrEnum):
    """Closed set of reasons a candidate can be rejected.

    Closed on purpose: an operator triaging a rejection needs a code they can
    grep for and count, not free text that varies by call site.
    """

    ACCOUNT_RISK_EXCEEDED = "ACCOUNT_RISK_EXCEEDED"
    POSITION_NOTIONAL_EXCEEDED = "POSITION_NOTIONAL_EXCEEDED"
    GROSS_EXPOSURE_EXCEEDED = "GROSS_EXPOSURE_EXCEEDED"
    MAX_OPEN_POSITIONS_EXCEEDED = "MAX_OPEN_POSITIONS_EXCEEDED"
    DAILY_LOSS_LIMIT_BREACHED = "DAILY_LOSS_LIMIT_BREACHED"
    REWARD_RISK_TOO_LOW = "REWARD_RISK_TOO_LOW"
    ASSET_CLASS_NOT_PERMITTED = "ASSET_CLASS_NOT_PERMITTED"
    DUPLICATE_POSITION = "DUPLICATE_POSITION"
    STALE_MARKET_DATA = "STALE_MARKET_DATA"
    STRATEGY_NOT_PROMOTED = "STRATEGY_NOT_PROMOTED"


class RiskViolation(FrozenModel):
    """One failed constraint, with the numbers that failed it."""

    code: RiskViolationCode
    detail: NonEmptyText
    observed: str = Field(description="Observed value, as an exact decimal string.")
    limit: str = Field(description="Configured ceiling, as an exact decimal string.")


class RiskDecision(AuthoritativeModel):
    """The risk engine's verdict on one candidate."""

    AUTHORITATIVE_FIELDS: ClassVar[tuple[str, ...]] = (
        "candidate_id",
        "candidate_fingerprint",
        "verdict",
        "limits_fingerprint",
    )

    decision_id: UUID
    candidate_id: UUID
    candidate_fingerprint: str = Field(
        pattern=r"^[0-9a-f]{64}$",
        description="Fingerprint of the candidate as judged. Execution re-checks this.",
    )
    verdict: RiskVerdict
    limits_name: str = Field(min_length=1, max_length=64)
    limits_fingerprint: str = Field(pattern=r"^[0-9a-f]{64}$")
    evaluated_at: TimestampUTC
    violations: tuple[RiskViolation, ...] = ()

    @model_validator(mode="after")
    def _verdict_matches_violations(self) -> Self:
        """An approval with violations attached is a contradiction, not a warning."""
        if self.verdict is RiskVerdict.APPROVED and self.violations:
            raise ValueError("APPROVED decision cannot carry violations; reject or clear them")
        if self.verdict is RiskVerdict.REJECTED and not self.violations:
            raise ValueError("REJECTED decision must state at least one violation")
        return self

    @property
    def is_approved(self) -> bool:
        """True only for an explicit APPROVED verdict."""
        return self.verdict is RiskVerdict.APPROVED

    @classmethod
    def derive_id(cls, *, candidate_fingerprint: str, limits_fingerprint: str) -> UUID:
        """Derive the decision id from what was judged and the rules applied."""
        return DeterministicId.derive("risk_decision", candidate_fingerprint, limits_fingerprint)
