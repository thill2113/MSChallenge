# ADR-005: Human-controlled production promotion

- **Status:** Accepted
- **Date:** 2026-08-07
- **Phase:** 0/1
- **Related:** ADR-004

## Context

Promotion to production is the moment a piece of code acquires the ability to
move real money. Every other decision in this system is reversible on a
timescale of minutes; this one is not.

Automating it is easy and seductive: backtest passes, metrics clear a threshold,
promote. That design fails in a specific way — the thresholds are themselves
code, written by the same people, under the same assumptions, and validated
against the same historical data that produced the strategy. A strategy that
overfits the backtest will also satisfy the promotion criteria derived from it.
The automation does not add a check; it launders the absence of one.

## Decision

**No automated process may promote a strategy version to `PRODUCTION`.**

1. `authorize_promotion()` raises `PromotionAuthorityError` unless a
   `HumanApproval` is supplied for a transition into `PRODUCTION`.
2. `HumanApproval` records `approver` (a person, never a service account),
   `approved_at`, `evidence_uri` (PR, signed commit, ticket) and
   `version_fingerprint`.
3. **The approval binds to the fingerprint.** Approving `1.2.0` cannot be
   replayed to promote `1.2.1`, and if the parameters change after approval the
   fingerprint no longer matches and the promotion is refused with
   "the version changed after approval".
4. **Stages advance one at a time:** `RESEARCH → BACKTEST → PAPER → PRODUCTION`,
   plus `→ RETIRED` from anywhere. Skipping straight to production is refused
   *even with a valid approval*, because the intermediate stages are where the
   evidence for that approval is supposed to come from.
5. `RETIRED` is terminal. A retired version cannot be revived; publish a new one.
6. Every transition appends a `PromotionRecord` carrying the approval, forming
   an audit trail of who authorised what and on what evidence.
7. The same principle gates execution: `ExecutionGateway` refuses
   `ExecutionMode.LIVE` outright in Phase 0/1, and `RobinhoodMCPAdapter.submit`
   raises. Live trading is not a flag waiting to be flipped — it is unbuilt
   until this workflow exists end to end.

## Consequences

**Good**

- A named person is accountable for every strategy that can trade real money.
- The approval is auditable: fingerprint, timestamp, and a link to the evidence.
- An automated pipeline can take a version all the way to `PAPER` unattended,
  so the human decision is the only manual step.

**Costs**

- Promotion cannot happen out of hours or without a person. That is the intent,
  and it is a real operational constraint.
- `HumanApproval` is a record, not a cryptographic signature. It attests that a
  person approved; it does not prove it against a determined insider. Signing is
  deferred — see open decision D-3 in `docs/PHASE_1_STATUS.md`.
- The promotion history currently lives in memory only. Persisting it is
  remaining Phase 1 work.

## Enforcement

- `tests/unit/test_strategy_promotion.py::TestPromotionRequiresHumanApproval`
- `tests/unit/test_risk_enforcement.py::TestGatewayReVerifies::test_live_mode_is_refused_outright`
