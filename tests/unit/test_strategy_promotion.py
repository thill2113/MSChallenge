"""Strategy versioning and human-gated promotion (ADR-004, ADR-005)."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from domain.enums import PromotionStage
from domain.errors import PromotionAuthorityError
from strategies.models import StrategyParameter, StrategyVersion
from strategies.promotion import HumanApproval
from strategies.registry import StrategyRegistry

pytestmark = pytest.mark.invariant

APPROVED_AT = datetime(2026, 1, 3, 9, 0, tzinfo=UTC)


def _approval(version: StrategyVersion, **overrides: object) -> HumanApproval:
    payload: dict[str, object] = {
        "approver": "timmy.hill23@gmail.com",
        "version_key": version.key,
        "version_fingerprint": version.authoritative_fingerprint(),
        "approved_at": APPROVED_AT,
        "evidence_uri": "https://github.com/thill2113/MSChallenge/pull/1",
    }
    payload.update(overrides)
    return HumanApproval(**payload)  # type: ignore[arg-type]


def _advance_to_shadow(registry: StrategyRegistry, key: str) -> StrategyVersion:
    """Walk the unattended part of the ladder: everything up to SHADOW."""
    for stage in (
        PromotionStage.BACKTEST,
        PromotionStage.OUT_OF_SAMPLE,
        PromotionStage.WALK_FORWARD,
        PromotionStage.SHADOW,
    ):
        version = registry.promote(key, stage, occurred_at=APPROVED_AT)
    return version


def _advance_to_paper(registry: StrategyRegistry, key: str) -> StrategyVersion:
    """Reach PAPER, which needs the first human approval."""
    shadow = _advance_to_shadow(registry, key)
    return registry.promote(
        key, PromotionStage.PAPER, occurred_at=APPROVED_AT, approval=_approval(shadow)
    )


class TestVersionImmutability:
    def test_registering_the_same_version_twice_is_idempotent(self, strategy_version):
        registry = StrategyRegistry()
        registry.register(strategy_version)
        assert registry.register(strategy_version) is strategy_version
        assert len(registry) == 1

    def test_redefining_a_version_is_rejected(self, strategy_version):
        registry = StrategyRegistry()
        registry.register(strategy_version)
        altered = strategy_version.model_copy(
            update={"parameters": (StrategyParameter(name="lookback", value="20"),)}
        )
        with pytest.raises(ValueError, match="already registered with a different fingerprint"):
            registry.register(altered)

    def test_parameters_change_the_fingerprint(self, strategy_version):
        tuned = strategy_version.model_copy(
            update={"parameters": (StrategyParameter(name="lookback", value="20"),)}
        )
        assert tuned.authoritative_fingerprint() != strategy_version.authoritative_fingerprint()

    def test_stage_is_not_part_of_the_fingerprint(self, strategy_version):
        promoted = strategy_version.model_copy(update={"stage": PromotionStage.SHADOW})
        assert promoted.authoritative_fingerprint() == strategy_version.authoritative_fingerprint()

    def test_duplicate_parameter_names_are_rejected(self, base_time):
        with pytest.raises(ValueError, match="duplicate parameter names"):
            StrategyVersion(
                strategy_id="fixture_double",
                version="1.0.0",
                code_fingerprint="a" * 64,
                created_at=base_time,
                parameters=(
                    StrategyParameter(name="lookback", value="10"),
                    StrategyParameter(name="lookback", value="20"),
                ),
            )

    def test_versions_must_be_registered_at_research_stage(self, strategy_version):
        registry = StrategyRegistry()
        with pytest.raises(PromotionAuthorityError, match="must be registered at"):
            registry.register(
                strategy_version.model_copy(update={"stage": PromotionStage.LIMITED_LIVE})
            )


class TestPromotionRequiresHumanApproval:
    def test_paper_without_approval_is_refused(self, strategy_version):
        registry = StrategyRegistry()
        registry.register(strategy_version)
        _advance_to_shadow(registry, strategy_version.key)
        with pytest.raises(PromotionAuthorityError, match="requires a recorded human approval"):
            registry.promote(strategy_version.key, PromotionStage.PAPER, occurred_at=APPROVED_AT)

    def test_stages_up_to_shadow_need_no_approval(self, strategy_version):
        # Everything before PAPER is analysis against recorded data, so an
        # automated research pipeline can drive it unattended.
        registry = StrategyRegistry()
        registry.register(strategy_version)
        shadow = _advance_to_shadow(registry, strategy_version.key)
        assert shadow.stage is PromotionStage.SHADOW
        assert all(r.approval is None for r in registry.history())

    def test_limited_live_without_approval_is_refused(self, strategy_version):
        registry = StrategyRegistry()
        registry.register(strategy_version)
        _advance_to_paper(registry, strategy_version.key)
        with pytest.raises(PromotionAuthorityError, match="requires a recorded human approval"):
            registry.promote(
                strategy_version.key, PromotionStage.LIMITED_LIVE, occurred_at=APPROVED_AT
            )

    def test_limited_live_with_approval_succeeds(self, strategy_version):
        registry = StrategyRegistry()
        registry.register(strategy_version)
        paper = _advance_to_paper(registry, strategy_version.key)
        promoted = registry.promote(
            strategy_version.key,
            PromotionStage.LIMITED_LIVE,
            occurred_at=APPROVED_AT,
            approval=_approval(paper),
        )
        assert promoted.stage is PromotionStage.LIMITED_LIVE
        assert registry.production_versions() == (promoted,)

    def test_approval_for_another_version_is_refused(self, strategy_version):
        registry = StrategyRegistry()
        registry.register(strategy_version)
        paper = _advance_to_paper(registry, strategy_version.key)
        with pytest.raises(PromotionAuthorityError, match="approval is for"):
            registry.promote(
                strategy_version.key,
                PromotionStage.LIMITED_LIVE,
                occurred_at=APPROVED_AT,
                approval=_approval(paper, version_key="other_strategy@1.0.0"),
            )

    def test_approval_of_a_since_changed_version_is_refused(self, strategy_version):
        registry = StrategyRegistry()
        registry.register(strategy_version)
        paper = _advance_to_paper(registry, strategy_version.key)
        stale = _approval(paper, version_fingerprint="c" * 64)
        with pytest.raises(PromotionAuthorityError, match="version changed after approval"):
            registry.promote(
                strategy_version.key,
                PromotionStage.LIMITED_LIVE,
                occurred_at=APPROVED_AT,
                approval=stale,
            )

    def test_stages_cannot_be_skipped_even_with_approval(self, strategy_version):
        registry = StrategyRegistry()
        registry.register(strategy_version)
        with pytest.raises(PromotionAuthorityError, match="illegal promotion"):
            registry.promote(
                strategy_version.key,
                PromotionStage.LIMITED_LIVE,
                occurred_at=APPROVED_AT,
                approval=_approval(strategy_version),
            )

    def test_retired_versions_cannot_be_revived(self, strategy_version):
        registry = StrategyRegistry()
        registry.register(strategy_version)
        registry.promote(strategy_version.key, PromotionStage.RETIRED, occurred_at=APPROVED_AT)
        with pytest.raises(PromotionAuthorityError, match="illegal promotion"):
            registry.promote(strategy_version.key, PromotionStage.BACKTEST, occurred_at=APPROVED_AT)

    def test_promotion_history_is_recorded(self, strategy_version):
        registry = StrategyRegistry()
        registry.register(strategy_version)
        paper = _advance_to_paper(registry, strategy_version.key)
        registry.promote(
            strategy_version.key,
            PromotionStage.LIMITED_LIVE,
            occurred_at=APPROVED_AT,
            approval=_approval(paper),
        )
        history = registry.history()
        assert [r.to_stage for r in history] == [
            PromotionStage.BACKTEST,
            PromotionStage.OUT_OF_SAMPLE,
            PromotionStage.WALK_FORWARD,
            PromotionStage.SHADOW,
            PromotionStage.PAPER,
            PromotionStage.LIMITED_LIVE,
        ]
        assert history[-1].approval is not None
        assert history[-1].approval.approver == "timmy.hill23@gmail.com"

    def test_no_production_versions_exist_by_default(self, strategy_version):
        registry = StrategyRegistry()
        registry.register(strategy_version)
        assert registry.production_versions() == ()
