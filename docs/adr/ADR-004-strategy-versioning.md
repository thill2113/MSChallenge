# ADR-004: Strategy versioning

- **Status:** Accepted
- **Date:** 2026-08-07
- **Phase:** 0/1
- **Related:** ADR-001, ADR-005

## Context

A strategy is not one thing. It is a sequence of revisions with the same name,
and the differences between them are exactly what matters when reviewing
performance. "Strategy X made 4% last quarter" is meaningless if the parameters
changed twice during that quarter and nobody recorded when.

The failure mode is silent drift: someone tunes a threshold from 14 to 20,
commits it, and every backtest, ledger row and journal entry that says
"strategy X" now refers to two different things. The historical record becomes
uninterpretable, and it does so without any error ever being raised.

## Decision

**A strategy version is immutable, fingerprinted, and referenced by every
artifact it produces.**

1. `StrategyVersion` carries `(strategy_id, version, code_fingerprint,
   parameters)` and is frozen. `version` is semver; `code_fingerprint` is the
   SHA-256 of the implementation as promoted.
2. Its `authoritative_fingerprint()` covers id, version, code fingerprint and
   parameters. Changing any parameter produces a different fingerprint.
3. `stage` is deliberately **excluded** from the fingerprint. Promoting a
   version through the lifecycle is not a change to what it does, and an
   approval issued at `PAPER` must remain valid when it reaches `PRODUCTION`.
4. `StrategyRegistry.register` **rejects redefinition**. Registering the same
   key with a different fingerprint raises; registering identical content is
   idempotent. There is no way to "update" a version — you publish a new one.
5. Versions must be registered at `RESEARCH`. Stages are earned through
   `promote()`, not declared at construction.
6. `TradeCandidate`, `NoTrade`, `OrderIntent` and every `TradeRecord` carry
   `strategy_id` and `strategy_version`. Any historical decision resolves to the
   exact parameter set that produced it.
7. The same discipline extends to `Signal.computation_version` and
   `RegimeAssessment.classifier_version`, because a signal whose maths changed
   silently invalidates every strategy that consumed it by name.

**What requires a version bump:** any change to behaviour — logic, parameters,
the maths of a consumed signal, or the interpretation of an input. If a replay
of the same inputs would produce a different decision, it is a new version.

## Consequences

**Good**

- Any decision in the ledger resolves to the exact code and parameters behind
  it.
- Comparing two versions is a real comparison, not a comparison of a name
  against itself at two points in time.
- Parameter tuning leaves an audit trail instead of a diff nobody reads.

**Costs**

- Tuning a single number requires publishing a new version and re-promoting it
  through the stages. That is friction by design; it is also friction.
- `code_fingerprint` has to be produced and kept honest by the build. Nothing
  currently computes it automatically — see open question Q-1 in
  `docs/PHASE_1_STATUS.md`.
- The registry is in-process only. Version history does not yet survive a
  restart; persisting it is remaining Phase 1 work.

## Enforcement

- `tests/unit/test_strategy_promotion.py::TestVersionImmutability`
