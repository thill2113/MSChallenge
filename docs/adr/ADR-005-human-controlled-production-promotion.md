# ADR-005: Human-controlled production promotion

- **Status:** Accepted (amended by ADR-008)
- **Date:** 2026-08-07
- **Phase:** 0/1
- **Related:** ADR-004, ADR-008

> **Amendment note.** Human control over *promotion* is unchanged and is now
> enforced at two transitions rather than one: `SHADOW → PAPER` (first external
> venue) and `PAPER → LIMITED_LIVE` (first real money). What changed is that
> individual trades no longer require confirmation — see
> [ADR-008](ADR-008-automated-execution-and-hard-gates.md). Human authority moved
> to the control plane, where it bounds what an approved strategy may do instead
> of approving each thing it does. `PRODUCTION` is now called `LIMITED_LIVE`.

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

**No automated process may promote a strategy version into `PAPER` or `LIMITED_LIVE`.**

1. `authorize_promotion()` raises `PromotionAuthorityError` unless a
   `HumanApproval` is supplied for a transition into `PAPER` or `LIMITED_LIVE`.
2. `HumanApproval` records `approver` (a person, never a service account),
   `approved_at`, `evidence_uri` (PR, signed commit, ticket) and
   `version_fingerprint`.
3. **The approval binds to the fingerprint.** Approving `1.2.0` cannot be
   replayed to promote `1.2.1`, and if the parameters change after approval the
   fingerprint no longer matches and the promotion is refused with
   "the version changed after approval".
4. **Stages advance one at a time:** `DEVELOPMENT → BACKTEST → OUT_OF_SAMPLE →
   WALK_FORWARD → SHADOW → PAPER → LIMITED_LIVE`, plus `→ RETIRED` from
   anywhere, and `LIMITED_LIVE → PAPER` so demoting a misbehaving strategy never
   needs the same ceremony as promoting one. Skipping is refused *even with a
   valid approval*, because the intermediate stages are where the evidence for
   that approval is supposed to come from. Everything up to `SHADOW` runs against
   recorded or simulated data and can be driven unattended by an automated
   research pipeline.
5. `RETIRED` is terminal. A retired version cannot be revived; publish a new one.
6. Every transition appends a `PromotionRecord` carrying the approval, forming
   an audit trail of who authorised what and on what evidence.
7. The same principle gates execution mode, which lives in the control plane
   and changes only by recorded human approval. `ExecutionMode.LIVE` is refused
   by `apply_configuration_change` outright, and the strategy's stage must
   independently permit the mode. Live trading is not a flag waiting to be
   flipped — it is unbuilt.

## Consequences

**Good**

- A named person is accountable for every strategy that can trade real money,
  and for the envelope it trades inside.
- The approval is auditable: fingerprint, timestamp, and a link to the evidence.
- An automated pipeline can take a version all the way to `SHADOW` unattended,
  so the two human decisions are the only manual steps.

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
- `tests/unit/test_kill_switch_and_control_plane.py::TestControlPlaneAuthority`
