"""Kill switches and the control plane — where human authority actually lives
now that per-trade confirmation is gone (ADR-008).

Two asymmetries are under test:

* A kill switch is **easy to engage and hard to clear**. Automation may trip
  one; only a human may clear one that a risk control tripped.
* Configuration is **easy to read and hard to change**. Every component reads
  it; changing it needs a recorded human approval bound to the exact revision.
"""

from __future__ import annotations

from datetime import UTC, datetime, time
from decimal import Decimal

import pytest

from control_plane.config import (
    AccountAllocation,
    ControlPlaneConfig,
    InstrumentPermission,
    TradingSession,
    apply_configuration_change,
)
from domain.enums import AgentContextPolicy, AssetClass, ExecutionMode, KillSwitchScope
from domain.errors import AuthorityViolationError, ControlPlaneError, KillSwitchEngagedError
from execution.killswitch import (
    AUTO_CLEARABLE_TRIGGERS,
    KillSwitch,
    KillSwitchRegistry,
    KillSwitchTrigger,
)
from strategies.promotion import HumanApproval

pytestmark = pytest.mark.invariant

NOW = datetime(2026, 1, 2, 14, 30, tzinfo=UTC)


def _switch(
    scope=KillSwitchScope.SYSTEM,
    target="*",
    trigger=KillSwitchTrigger.DAILY_LOSS_LIMIT,
    engaged_by="risk_control",
) -> KillSwitch:
    return KillSwitch(
        scope=scope,
        target=target,
        trigger=trigger,
        reason="test",
        engaged_at=NOW,
        engaged_by=engaged_by,
    )


class TestKillSwitchLayers:
    def test_a_system_switch_blocks_everything(self):
        registry = KillSwitchRegistry()
        registry.engage(_switch())
        with pytest.raises(KillSwitchEngagedError, match="trading halted"):
            registry.assert_clear(strategy_key="any@1.0.0", symbol="ANY")

    def test_a_narrow_switch_blocks_only_its_target(self):
        registry = KillSwitchRegistry()
        registry.engage(_switch(scope=KillSwitchScope.SYMBOL, target="ACME"))
        registry.assert_clear(symbol="OTHR")  # does not raise
        with pytest.raises(KillSwitchEngagedError):
            registry.assert_clear(symbol="ACME")

    def test_all_five_scopes_are_checked(self):
        registry = KillSwitchRegistry()
        for scope, target in [
            (KillSwitchScope.SYSTEM, "*"),
            (KillSwitchScope.STRATEGY, "s@1.0.0"),
            (KillSwitchScope.SYMBOL, "ACME"),
            (KillSwitchScope.BROKER, "simulator"),
            (KillSwitchScope.ACCOUNT, "ACC"),
        ]:
            registry.engage(_switch(scope=scope, target=target))
        blocking = registry.blocking(
            strategy_key="s@1.0.0", symbol="ACME", broker_id="simulator", account_id="ACC"
        )
        assert len(blocking) == 5

    def test_re_engaging_keeps_the_original_diagnosis(self):
        # The first trigger is closest to the root cause; a later downstream
        # symptom must not overwrite it.
        registry = KillSwitchRegistry()
        first = registry.engage(_switch(trigger=KillSwitchTrigger.DAILY_LOSS_LIMIT))
        second = registry.engage(_switch(trigger=KillSwitchTrigger.BROKER_UNAVAILABLE))
        assert second is first
        assert first.trigger is KillSwitchTrigger.DAILY_LOSS_LIMIT


class TestClearingIsHarder:
    @pytest.mark.parametrize(
        "trigger",
        [
            KillSwitchTrigger.DAILY_LOSS_LIMIT,
            KillSwitchTrigger.WEEKLY_LOSS_LIMIT,
            KillSwitchTrigger.DRAWDOWN_LIMIT,
            KillSwitchTrigger.CONSECUTIVE_LOSSES,
            KillSwitchTrigger.RECONCILIATION_MISMATCH,
            KillSwitchTrigger.UNKNOWN_ORDER_STATE,
            KillSwitchTrigger.MANUAL,
        ],
    )
    def test_risk_triggers_cannot_be_cleared_automatically(self, trigger):
        registry = KillSwitchRegistry()
        registry.engage(_switch(trigger=trigger))
        with pytest.raises(AuthorityViolationError, match="cannot be cleared automatically"):
            registry.clear(KillSwitchScope.SYSTEM, "*", cleared_by="scheduler", automated=True)
        assert len(registry) == 1

    @pytest.mark.parametrize("trigger", sorted(AUTO_CLEARABLE_TRIGGERS))
    def test_transient_infrastructure_triggers_may_self_clear(self, trigger):
        # Connectivity returning and data arriving are directly observable
        # conditions, unlike "the losses stopped".
        registry = KillSwitchRegistry()
        registry.engage(_switch(trigger=trigger))
        registry.clear(KillSwitchScope.SYSTEM, "*", cleared_by="monitor", automated=True)
        assert len(registry) == 0

    def test_a_human_can_clear_anything(self):
        registry = KillSwitchRegistry()
        registry.engage(_switch(trigger=KillSwitchTrigger.DRAWDOWN_LIMIT))
        registry.clear(KillSwitchScope.SYSTEM, "*", cleared_by="timmy.hill23@gmail.com")
        assert len(registry) == 0

    def test_history_records_both_directions(self):
        registry = KillSwitchRegistry()
        registry.engage(_switch(trigger=KillSwitchTrigger.STALE_MARKET_DATA))
        registry.clear(KillSwitchScope.SYSTEM, "*", cleared_by="monitor", automated=True)
        actions = [action for action, _ in registry.history()]
        assert actions[0] == "ENGAGED"
        assert actions[1].startswith("CLEARED by monitor")

    def test_clearing_an_inactive_switch_is_a_no_op(self):
        KillSwitchRegistry().clear(KillSwitchScope.SYSTEM, "*", cleared_by="anyone")


class TestControlPlaneAuthority:
    def _approval(self, proposed: ControlPlaneConfig, **overrides: object) -> HumanApproval:
        payload: dict[str, object] = {
            "approver": "timmy.hill23@gmail.com",
            "version_key": f"control_plane@{proposed.revision}",
            "version_fingerprint": proposed.configuration_hash,
            "approved_at": NOW,
            "evidence_uri": "https://example.invalid/pr/7",
        }
        payload.update(overrides)
        return HumanApproval(**payload)  # type: ignore[arg-type]

    def test_a_change_needs_an_approval_bound_to_its_fingerprint(self, config):
        proposed = config.model_copy(update={"revision": 2, "agent_context_max_age_seconds": 300})
        with pytest.raises(ControlPlaneError, match="does not match the proposed configuration"):
            apply_configuration_change(
                config,
                proposed,
                self._approval(config),  # approval for the OLD revision
            )

    def test_a_correctly_approved_change_is_adopted(self, config):
        proposed = config.model_copy(update={"revision": 2, "agent_context_max_age_seconds": 300})
        adopted = apply_configuration_change(config, proposed, self._approval(proposed))
        assert adopted.revision == 2

    def test_revisions_must_increase(self, config):
        proposed = config.model_copy(update={"agent_context_max_age_seconds": 300})
        with pytest.raises(ControlPlaneError, match="revision must increase"):
            apply_configuration_change(config, proposed, self._approval(proposed))

    def test_live_mode_cannot_be_configured(self, config):
        proposed = config.model_copy(update={"revision": 2, "execution_mode": ExecutionMode.LIVE})
        with pytest.raises(ControlPlaneError, match="LIVE is reserved"):
            apply_configuration_change(config, proposed, self._approval(proposed))

    def test_configuration_is_immutable(self, config):
        from pydantic import ValidationError

        with pytest.raises(ValidationError):
            config.execution_mode = ExecutionMode.LIMITED_LIVE  # type: ignore[misc]

    def test_the_hash_changes_when_anything_material_changes(self, config):
        before = config.configuration_hash
        for update in (
            {"execution_mode": ExecutionMode.DISABLED, "enabled_strategy_keys": ()},
            {"agent_context_policy": AgentContextPolicy.REQUIRE_VALID_AGENT_CONTEXT},
            {"enabled_strategy_keys": ()},
            {"agent_context_max_age_seconds": 60},
        ):
            changed = config.model_copy(update={"revision": 2, **update})
            assert changed.configuration_hash != before, update

    def test_an_enabled_mode_with_no_strategies_is_incoherent(self, limits):
        from pydantic import ValidationError

        with pytest.raises(ValidationError, match="use DISABLED to express"):
            _config(limits, execution_mode=ExecutionMode.PAPER, enabled_strategy_keys=())

    def test_duplicate_instruments_are_rejected(self, limits):
        from pydantic import ValidationError

        duplicate = InstrumentPermission(
            symbol="ACME",
            max_position_notional_fraction=Decimal("0.1"),
        )
        with pytest.raises(ValidationError, match="duplicate symbols"):
            _config(limits, instruments=(duplicate, duplicate))

    def test_a_session_must_close_after_it_opens(self):
        from pydantic import ValidationError

        with pytest.raises(ValidationError, match="close must be after open"):
            TradingSession(
                name="broken",
                exchange_timezone="America/New_York",
                opens_at=time(16, 0),
                closes_at=time(9, 30),
            )


def _config(limits, **overrides: object) -> ControlPlaneConfig:
    payload: dict[str, object] = {
        "revision": 1,
        "execution_mode": ExecutionMode.SHADOW,
        "agent_context_policy": AgentContextPolicy.ALLOW_WITHOUT_AGENT_CONTEXT,
        "agent_context_max_age_seconds": 900,
        "enabled_strategy_keys": ("fixture_double@1.0.0",),
        "instruments": (
            InstrumentPermission(
                symbol="ACME",
                asset_class=AssetClass.EQUITY,
                max_position_notional_fraction=Decimal("0.25"),
            ),
        ),
        "allocation": AccountAllocation(
            account_id="TEST-ACCOUNT",
            allocated_equity_fraction=Decimal("1"),
            broker_id="simulator",
        ),
        "sessions": (
            TradingSession(
                name="regular",
                exchange_timezone="America/New_York",
                opens_at=time(9, 30),
                closes_at=time(16, 0),
            ),
        ),
        "risk_limits_name": limits.name,
        "risk_limits_fingerprint": limits.authoritative_fingerprint(),
        "approved_by": "timmy.hill23@gmail.com",
        "approved_at": NOW,
    }
    payload.update(overrides)
    return ControlPlaneConfig(**payload)  # type: ignore[arg-type]
