"""Domain model guarantees: immutability, closedness, exactness, fingerprinting."""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

import pytest
from pydantic import ValidationError

from domain.base import AuthoritativeModel, FrozenModel, canonical_form
from domain.enums import AssetClass, OrderType, Side, TimeInForce
from domain.identifiers import DeterministicId
from market_data.models import Bar, MarketSnapshot, Quote
from strategies.models import TradeCandidate

BASE_TIME = datetime(2026, 1, 2, 14, 30, tzinfo=UTC)


class TestImmutability:
    def test_snapshot_cannot_be_mutated(self, snapshot):
        with pytest.raises(ValidationError):
            snapshot.last_price = Decimal("1")

    def test_candidate_cannot_be_mutated(self, candidate):
        for field, value in [
            ("quantity", Decimal("1")),
            ("stop_price", Decimal("1")),
            ("side", Side.SELL),
        ]:
            with pytest.raises(ValidationError):
                setattr(candidate, field, value)

    def test_undeclared_fields_are_rejected(self, base_time):
        with pytest.raises(ValidationError, match=r"[Ee]xtra inputs are not permitted"):
            Quote(bid=Decimal("1"), ask=Decimal("2"), midpoint=Decimal("1.5"))  # type: ignore[call-arg]


class TestExactNumerics:
    def test_floats_are_rejected_for_prices(self):
        with pytest.raises(ValidationError, match="float is not accepted"):
            Quote(bid=1.5, ask=2.0)  # type: ignore[arg-type]

    def test_decimal_strings_are_accepted(self):
        quote = Quote(bid="1.50", ask="1.52")  # type: ignore[arg-type]
        assert quote.mid == Decimal("1.51")

    def test_naive_datetimes_are_rejected(self):
        with pytest.raises(ValidationError, match="naive datetimes are not accepted"):
            MarketSnapshot(
                symbol="ACME",
                as_of=datetime(2026, 1, 2, 14, 30),
                last_price=Decimal("10"),
                provider="fixture",
            )

    def test_symbols_are_normalised(self):
        snapshot = MarketSnapshot(
            symbol="  acme ", as_of=BASE_TIME, last_price=Decimal("10"), provider="fixture"
        )
        assert snapshot.symbol == "ACME"


class TestModelGeometry:
    def test_crossed_quote_is_rejected(self):
        with pytest.raises(ValidationError, match="crossed quote"):
            Quote(bid=Decimal("10"), ask=Decimal("9"))

    def test_bar_close_outside_range_is_rejected(self):
        with pytest.raises(ValidationError, match="close must fall within"):
            Bar(
                interval_seconds=60,
                open=Decimal("10"),
                high=Decimal("11"),
                low=Decimal("9"),
                close=Decimal("12"),
                volume=Decimal("100"),
            )

    def test_long_candidate_needs_stop_below_entry(self, base_time):
        with pytest.raises(ValidationError, match="stop_price below reference_price"):
            _candidate(base_time, side=Side.BUY, stop=Decimal("101"), target=Decimal("110"))

    def test_short_candidate_needs_stop_above_entry(self, base_time):
        with pytest.raises(ValidationError, match="stop_price above reference_price"):
            _candidate(base_time, side=Side.SELL, stop=Decimal("99"), target=Decimal("90"))

    def test_market_order_must_not_carry_a_limit_price(self, base_time):
        with pytest.raises(ValidationError, match="must not carry a limit_price"):
            _candidate(base_time, order_type=OrderType.MARKET, limit=Decimal("100"))

    def test_limit_order_requires_a_limit_price(self, base_time):
        with pytest.raises(ValidationError, match="requires a limit_price"):
            _candidate(base_time, order_type=OrderType.LIMIT, limit=None)


class TestFingerprinting:
    def test_equal_amounts_written_differently_fingerprint_the_same(self, candidate):
        restated = candidate.model_copy(
            update={"quantity": Decimal("10.000"), "stop_price": Decimal("98.0")}
        )
        assert restated.authoritative_fingerprint() == candidate.authoritative_fingerprint()

    def test_changing_a_trading_parameter_changes_the_fingerprint(self, candidate):
        for field, value in [
            ("quantity", Decimal("11")),
            ("stop_price", Decimal("97")),
            ("target_price", Decimal("105")),
            ("account_risk_fraction", Decimal("0.006")),
            ("time_in_force", TimeInForce.GTC),
        ]:
            moved = candidate.model_copy(update={field: value})
            assert moved.authoritative_fingerprint() != candidate.authoritative_fingerprint(), field

    def test_changing_commentary_does_not_change_the_fingerprint(self, candidate):
        reworded = candidate.model_copy(
            update={"rationale": "completely different words", "inputs_fingerprint": "f" * 64}
        )
        assert reworded.authoritative_fingerprint() == candidate.authoritative_fingerprint()

    def test_authoritative_fields_must_exist(self):
        with pytest.raises(TypeError, match="names undeclared fields"):

            class Broken(AuthoritativeModel):
                AUTHORITATIVE_FIELDS = ("nope",)

                real: int

    def test_model_without_declared_fields_cannot_be_fingerprinted(self):
        class Undeclared(AuthoritativeModel):
            value: int

        with pytest.raises(NotImplementedError, match="must declare AUTHORITATIVE_FIELDS"):
            Undeclared(value=1).authoritative_fingerprint()

    def test_canonical_form_rejects_unknown_types(self):
        class Opaque:
            pass

        with pytest.raises(TypeError, match="no canonical form defined"):
            canonical_form(Opaque())

    def test_canonical_form_normalises_decimal_spelling(self):
        assert canonical_form(Decimal("1.50")) == canonical_form(Decimal("1.5"))
        assert canonical_form(Decimal("1E+2")) == "100"


class TestDeterministicIds:
    def test_same_parts_give_the_same_id(self):
        assert DeterministicId.derive("a", "b") == DeterministicId.derive("a", "b")

    def test_part_boundaries_cannot_collide(self):
        assert DeterministicId.derive("ab", "cd") != DeterministicId.derive("a", "bcd")

    def test_no_parts_is_an_error(self):
        with pytest.raises(ValueError, match="at least one part"):
            DeterministicId.derive()


class TestFrozenModelIsHashable:
    def test_frozen_models_can_be_used_in_sets(self):
        class Point(FrozenModel):
            x: int

        assert len({Point(x=1), Point(x=1), Point(x=2)}) == 2


def _candidate(
    base_time: datetime,
    *,
    side: Side = Side.BUY,
    order_type: OrderType = OrderType.LIMIT,
    limit: Decimal | None = Decimal("100"),
    stop: Decimal = Decimal("98"),
    target: Decimal | None = Decimal("104"),
) -> TradeCandidate:
    return TradeCandidate(
        candidate_id=TradeCandidate.derive_id(
            strategy_id="fixture_double",
            strategy_version="1.0.0",
            symbol="ACME",
            as_of=base_time,
        ),
        strategy_id="fixture_double",
        strategy_version="1.0.0",
        symbol="ACME",
        as_of=base_time,
        asset_class=AssetClass.EQUITY,
        side=side,
        order_type=order_type,
        limit_price=limit,
        quantity=Decimal("10"),
        reference_price=Decimal("100"),
        stop_price=stop,
        target_price=target,
        time_in_force=TimeInForce.DAY,
        account_risk_fraction=Decimal("0.005"),
    )
