"""Risk limit configuration.

**No production values are defined in this repository.** Every field below is
required and has no default, so a limit set cannot be constructed by accident,
cannot be half-specified, and cannot inherit a number that nobody chose. The
only limit sets in version control live under ``data/fixtures/`` and are labelled
as test data.

Choosing real numbers is a human decision recorded in docs/PHASE_1_STATUS.md
(open decision D-1). Until that happens the system cannot approve anything,
which is the correct failure mode.
"""

from __future__ import annotations

from typing import ClassVar, Self

from pydantic import Field, model_validator

from domain.base import AuthoritativeModel
from domain.enums import AssetClass
from domain.values import ExactDecimal, Ratio


class RiskLimits(AuthoritativeModel):
    """A complete, named, fingerprinted set of risk constraints.

    ``name`` and ``revision`` exist so a :class:`~risk.models.RiskDecision` can
    say which rules it applied. Reconstructing why a trade was approved six
    months ago requires knowing the limits in force at the time.
    """

    AUTHORITATIVE_FIELDS: ClassVar[tuple[str, ...]] = (
        "name",
        "revision",
        "max_account_risk_fraction",
        "max_position_notional_fraction",
        "max_gross_exposure_fraction",
        "max_open_positions",
        "max_daily_loss_fraction",
        "min_reward_risk_ratio",
        "allowed_asset_classes",
    )

    name: str = Field(min_length=1, max_length=64, pattern=r"^[a-z0-9_]+$")
    revision: int = Field(ge=1)

    max_account_risk_fraction: Ratio = Field(
        description="Ceiling on a single candidate's account_risk_fraction."
    )
    max_position_notional_fraction: Ratio = Field(
        description="Ceiling on one position's notional as a fraction of equity."
    )
    max_gross_exposure_fraction: ExactDecimal = Field(
        gt=0, description="Ceiling on total gross exposure as a multiple of equity."
    )
    max_open_positions: int = Field(ge=1)
    max_daily_loss_fraction: Ratio = Field(
        description="Realised loss for the day, as a positive fraction of equity, "
        "beyond which no new candidate is approved."
    )
    min_reward_risk_ratio: ExactDecimal = Field(
        gt=0,
        description="Minimum (target - reference) / (reference - stop). Applied only "
        "to candidates that declare a target.",
    )
    allowed_asset_classes: tuple[AssetClass, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def _reject_duplicate_asset_classes(self) -> Self:
        if len(set(self.allowed_asset_classes)) != len(self.allowed_asset_classes):
            raise ValueError("allowed_asset_classes contains duplicates")
        return self
