# ADR-001: Deterministic trading authority

- **Status:** Accepted
- **Date:** 2026-08-07
- **Phase:** 0/1
- **Supersedes:** —

## Context

A hybrid system mixes deterministic code with a language model. Both can propose
ideas. Only one can be allowed to *decide*, because the two have incompatible
failure modes:

- Deterministic code fails **reproducibly**. Given the same inputs it makes the
  same mistake every time, which means the mistake can be found, tested against,
  and fixed once.
- A language model fails **plausibly**. It produces a different answer to the
  same question on different days, each answer well-argued. A bad trade from a
  model cannot be reliably reproduced, which means it cannot be reliably
  prevented.

Without an explicit rule, authority leaks. It leaks through helpful-looking
conveniences: a risk engine that "just rounds the size down", an execution
adapter that "adjusts the limit to the tick", an agent that returns a suggested
stop that someone then applies. Each is individually defensible. Together they
mean nobody can say which component chose the trade that lost money.

## Decision

**The strategy engine is the sole author of trading parameters.** Side,
quantity, order type, limit price, stop, target, time in force and account risk
fraction are written exactly once, by a versioned strategy, and are never
recomputed downstream.

Concretely:

1. A strategy evaluation is a pure function of a frozen
   `EvaluationContext`. It reads no clock, no network, no global state, and
   consumes no randomness.
2. Every evaluation terminates in exactly one of `TradeCandidate` or `NoTrade`.
   `None` is not a valid result, and neither is an exception for the ordinary
   "nothing to do" case.
3. Identifiers are **derived**, not minted. `TradeCandidate.derive_id` is a
   UUIDv5 over the decision's origin, so replaying an input produces the same id
   rather than a new one.
4. Every authoritative record exposes `authoritative_fingerprint()` — a SHA-256
   over the fields that determine what the market would feel. Commentary,
   timestamps and review ids are excluded, so annotating a decision does not
   change its economic identity.
5. **Downstream layers bind to that fingerprint.** A `RiskDecision` records the
   fingerprint it judged; an `OrderIntent` refuses to exist unless its embedded
   approval matches its own fingerprint. Substituting a bigger trade for an
   approved one is a caught error, not a silent success.
6. The risk engine **approves or rejects**. It does not resize, re-stop or
   re-target — that would make it a second author.
7. The execution layer **transmits**. `OrderIntent.from_approved` copies every
   parameter verbatim; no method in `execution/` computes a price or a size.

## Consequences

**Good**

- Any trade can be replayed from its inputs and will produce the same decision.
- The blame boundary is unambiguous. If the size was wrong, the strategy wrote
  it; if a wrong size got through, risk approved it.
- Backtests are predictive of live behaviour, because the live path runs the
  same pure function.

**Costs**

- Strategies must do their own sizing. Convenient "let risk figure out the
  quantity" designs are unavailable.
- Rejected trades stay rejected. There is no path where a slightly-too-large
  candidate is quietly trimmed to fit, so the strategy has to be right the first
  time or the opportunity is lost. We accept losing that trade.
- Every input must be explicit, including timestamps and account equity. This is
  more verbose at call sites than reading a global.

**Residual risk**

Pydantic's `model_copy(update=...)` bypasses validators, so an in-process actor
can construct a record that looks approved. `OrderIntent` re-checks the
decision's internal coherence, which catches the incoherent forgeries; a
perfectly-formed forgery is indistinguishable at the type level. This is
mitigated by recording which engine and limit set issued each decision in the
ledger, and is tracked as open question Q-3 in `docs/PHASE_1_STATUS.md`.

## Enforcement

- `tests/unit/test_risk_enforcement.py` — approval binding, bypass attempts.
- `tests/property/test_strategy_determinism.py` — determinism, with a
  deliberately non-deterministic negative control.
- `src/execution/models.py::assert_authorized` — the binding check itself.
