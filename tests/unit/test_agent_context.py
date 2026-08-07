"""The asynchronous agent layer cannot reach into trading parameters, and the
execution path never waits for it (ADR-002, ADR-006).

Three claims are under test:

1. ``AgentContext`` is structurally incapable of carrying a trading parameter —
   the same guarantee as ``AgentReview``, applied to the path that reaches
   production.
2. A veto blocks under **every** policy; the policies differ only in how silence
   and staleness are treated.
3. Reading the store is a pure, non-blocking lookup that works when the agent is
   absent entirely.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from pydantic import ValidationError

from agents.context import (
    FORBIDDEN_CONTEXT_FIELDS,
    SCOPING_FIELDS,
    AgentContext,
    ContextReasonCode,
    ContextualRisk,
)
from agents.store import InMemoryAgentContextStore, resolve_agent_gate
from domain.enums import AgentContextPolicy
from strategies.models import TradeCandidate

pytestmark = pytest.mark.invariant

NOW = datetime(2026, 1, 2, 15, 0, tzinfo=UTC)
MAX_AGE = timedelta(minutes=15)


def _context(**overrides: object) -> AgentContext:
    payload: dict[str, object] = {
        "context_id": AgentContext.derive_id(
            symbol="ACME", strategy_id=None, publisher="claude", created_at=NOW
        ),
        "symbol": "ACME",
        "veto": False,
        "publisher": "claude",
        "publisher_version": "1",
        "created_at": NOW,
        "expires_at": NOW + timedelta(minutes=10),
    }
    payload.update(overrides)
    return AgentContext(**payload)  # type: ignore[arg-type]


class TestContextShape:
    """No trading parameter can be expressed, let alone applied."""

    def test_context_declares_no_trading_parameters(self):
        assert set(AgentContext.model_fields) & FORBIDDEN_CONTEXT_FIELDS == set()

    def test_scoping_fields_are_the_only_candidate_fields_permitted(self):
        # symbol and strategy_id are addressing, not authorship: they narrow
        # what a veto covers and cannot widen anything.
        assert {"symbol", "strategy_id"} == SCOPING_FIELDS
        assert set(TradeCandidate.AUTHORITATIVE_FIELDS) - SCOPING_FIELDS == FORBIDDEN_CONTEXT_FIELDS

    @pytest.mark.parametrize(
        ("field", "value"),
        [
            ("stop_price", Decimal("50")),
            ("quantity", Decimal("9999")),
            ("target_price", Decimal("500")),
            ("account_risk_fraction", Decimal("0.99")),
            ("side", "SELL"),
            ("order_type", "MARKET"),
            ("execution_mode", "LIMITED_LIVE"),
            ("kill_switch", "CLEAR"),
        ],
    )
    def test_smuggling_a_parameter_is_rejected(self, field, value):
        with pytest.raises(ValidationError, match=r"[Ee]xtra inputs are not permitted"):
            _context(**{field: value})

    def test_context_is_immutable(self):
        context = _context()
        with pytest.raises(ValidationError):
            context.veto = True  # type: ignore[misc]

    def test_a_veto_must_state_a_reason(self):
        with pytest.raises(ValidationError, match="must state at least one reason code"):
            _context(veto=True)

    def test_expiry_must_follow_creation(self):
        with pytest.raises(ValidationError, match="expires_at must be after created_at"):
            _context(expires_at=NOW - timedelta(seconds=1))

    def test_contextual_risk_severity_is_advisory_only(self):
        risk = ContextualRisk(
            code=ContextReasonCode.ABNORMAL_VOLATILITY, severity=Decimal("1"), detail="wide"
        )
        # Severity is a number the agent chose; it has no field to act through.
        assert set(ContextualRisk.model_fields) & FORBIDDEN_CONTEXT_FIELDS == set()
        assert risk.severity == Decimal("1")


class TestExpiry:
    """An opinion outlives its evidence unless something stops it."""

    def test_a_fresh_context_is_valid(self):
        assert _context().is_valid_at(NOW + timedelta(minutes=5), max_age=MAX_AGE)

    def test_a_context_past_its_own_expiry_is_not(self):
        assert not _context().is_valid_at(NOW + timedelta(minutes=11), max_age=MAX_AGE)

    def test_the_control_plane_ceiling_overrides_a_generous_publisher(self):
        # A publisher claiming a week-long expiry cannot keep its opinion alive
        # for a week: the human-configured max_age wins.
        long_lived = _context(expires_at=NOW + timedelta(days=7))
        assert long_lived.is_valid_at(NOW + timedelta(minutes=10), max_age=MAX_AGE)
        assert not long_lived.is_valid_at(NOW + timedelta(minutes=20), max_age=MAX_AGE)


class TestStoreScoping:
    def test_a_strategy_scoped_context_beats_a_wildcard(self):
        store = InMemoryAgentContextStore()
        store.publish(_context(summary="wildcard"))
        store.publish(
            _context(
                context_id=AgentContext.derive_id(
                    symbol="ACME", strategy_id="fixture_double", publisher="claude", created_at=NOW
                ),
                strategy_id="fixture_double",
                summary="specific",
            )
        )
        specific = store.get("ACME", "fixture_double")
        wildcard = store.get("ACME", "other_strategy")
        assert specific is not None and specific.summary == "specific"
        assert wildcard is not None and wildcard.summary == "wildcard"

    def test_publishing_replaces_the_previous_opinion_for_a_scope(self):
        store = InMemoryAgentContextStore()
        store.publish(_context(summary="first"))
        store.publish(_context(summary="second"))
        assert len(store) == 1
        latest = store.get("ACME", "any")
        assert latest is not None and latest.summary == "second"

    def test_an_unrelated_symbol_never_matches(self):
        store = InMemoryAgentContextStore()
        store.publish(_context())
        assert store.get("OTHR", "fixture_double") is None


class TestPolicies:
    """A veto always blocks. Silence is what the policies disagree about."""

    def _gate(self, store, policy, now=NOW + timedelta(minutes=1)):
        return resolve_agent_gate(
            store=store,
            symbol="ACME",
            strategy_id="fixture_double",
            now=now,
            policy=policy,
            max_age=MAX_AGE,
        )

    @pytest.mark.parametrize("policy", list(AgentContextPolicy))
    def test_a_valid_veto_blocks_under_every_policy(self, policy):
        store = InMemoryAgentContextStore()
        store.publish(_context(veto=True, reason_codes=(ContextReasonCode.MATERIAL_NEWS,)))
        outcome = self._gate(store, policy)
        assert outcome.allowed is False
        assert ContextReasonCode.MATERIAL_NEWS in outcome.reason_codes

    @pytest.mark.parametrize("policy", list(AgentContextPolicy))
    def test_a_valid_affirmation_never_blocks(self, policy):
        store = InMemoryAgentContextStore()
        store.publish(_context(veto=False))
        assert self._gate(store, policy).allowed is True

    def test_allow_without_context_trades_when_the_agent_is_silent(self):
        outcome = self._gate(
            InMemoryAgentContextStore(), AgentContextPolicy.ALLOW_WITHOUT_AGENT_CONTEXT
        )
        assert outcome.allowed is True
        assert outcome.context_was_absent

    def test_require_valid_context_blocks_when_the_agent_is_silent(self):
        outcome = self._gate(
            InMemoryAgentContextStore(), AgentContextPolicy.REQUIRE_VALID_AGENT_CONTEXT
        )
        assert outcome.allowed is False

    def test_fail_closed_permits_silence_but_blocks_staleness(self):
        # The distinction the policy exists for: "never spoke" is not the same
        # as "was speaking and stopped".
        silent = self._gate(
            InMemoryAgentContextStore(), AgentContextPolicy.FAIL_CLOSED_ON_AGENT_FAILURE
        )
        assert silent.allowed is True

        store = InMemoryAgentContextStore()
        store.publish(_context())
        stale = self._gate(
            store,
            AgentContextPolicy.FAIL_CLOSED_ON_AGENT_FAILURE,
            now=NOW + timedelta(minutes=30),
        )
        assert stale.allowed is False
        assert stale.context_was_stale

    def test_fail_closed_blocks_an_unhealthy_agent(self):
        store = InMemoryAgentContextStore(healthy=False)
        outcome = self._gate(store, AgentContextPolicy.FAIL_CLOSED_ON_AGENT_FAILURE)
        assert outcome.allowed is False
        assert outcome.agent_unhealthy

    def test_allow_without_context_ignores_an_unhealthy_agent(self):
        store = InMemoryAgentContextStore(healthy=False)
        assert self._gate(store, AgentContextPolicy.ALLOW_WITHOUT_AGENT_CONTEXT).allowed is True

    def test_no_store_at_all_is_handled(self):
        # Claude entirely absent — not merely quiet.
        outcome = resolve_agent_gate(
            store=None,
            symbol="ACME",
            strategy_id="fixture_double",
            now=NOW,
            policy=AgentContextPolicy.ALLOW_WITHOUT_AGENT_CONTEXT,
            max_age=MAX_AGE,
        )
        assert outcome.allowed is True

    def test_the_policy_has_no_default(self):
        # Choosing between these is a risk decision reserved for a human, so
        # there is no way to construct a config without stating one.
        from control_plane.config import ControlPlaneConfig

        field = ControlPlaneConfig.model_fields["agent_context_policy"]
        assert field.is_required()
