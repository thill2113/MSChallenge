# ADR-002: AI veto-only authority

- **Status:** Accepted (amended by ADR-006)
- **Date:** 2026-08-07
- **Phase:** 0/1
- **Related:** ADR-001, ADR-006

> **Amendment note.** The veto-only rule below is unchanged and now applies to
> two paths. [ADR-006](ADR-006-asynchronous-agent-context.md) moved the
> production agent *out* of the execution path: agents publish expiring
> `AgentContext` records out of band and the hot path reads a cache. The
> synchronous `AgentReview` flow described here is retained for research and
> backtesting. Both models are structurally incapable of carrying a trading
> parameter, and both are checked at import time.

## Context

The agent layer exists because a language model is genuinely good at noticing
things a rule cannot encode: an earnings release the strategy did not know
about, a candidate that contradicts a position taken an hour ago, a setup that
looks mechanically valid but is obviously a data artifact.

That is a real capability, and it is tempting to let it act on what it notices.
"The stop is too tight, widen it to 2×ATR" is a reasonable-sounding sentence and
a catastrophic API. The moment a model can adjust a parameter, three things
become true at once:

- The trade that executes is no longer the trade that was backtested.
- The risk approval was computed for a candidate that no longer exists.
- The size of a loss becomes a function of model output, which is unbounded and
  irreproducible.

The failure is asymmetric, and that asymmetry is the whole argument. An agent
that wrongly blocks a good trade costs one opportunity, bounded and visible. An
agent that wrongly widens a stop costs an unbounded amount of money, and does it
quietly.

## Decision

**The agent layer may veto. It may do nothing else.**

The prohibition is structural, not procedural — enforced by the shape of the
types, so that violating it requires changing the domain model rather than
forgetting a check:

1. `AgentReview` declares **no** trading parameter fields. There is physically
   nowhere to put a revised stop, size or target.
2. `extra="forbid"` rejects undeclared keys, so a caller cannot smuggle
   `stop_price` in through `model_validate` or a deserialised payload.
3. `ReviewVerdict` has exactly two members: `AFFIRM` and `VETO`. There is no
   `AMEND`.
4. `FORBIDDEN_REVIEW_FIELDS` is **derived from**
   `TradeCandidate.AUTHORITATIVE_FIELDS`, so adding a new trading parameter to
   candidates automatically extends the prohibition without anyone updating a
   list.
5. `assert_review_carries_no_trading_authority()` runs at **import time**. If
   someone adds an offending field to `AgentReview`, the package fails to
   import.
6. `apply_reviews` returns the candidate by **identity** (`result.proceed() is
   candidate`), not a copy. "The agent modified the trade" cannot hide inside a
   diff between two similar records.
7. Reviews bind to `candidate_fingerprint`. A review written against a
   100-share candidate does not authorise a 1,000-share one; the gate rejects
   the mismatch rather than ignoring it.
8. A veto reaching `OrderIntent.from_approved` raises. A veto cannot be laundered
   into an order by dropping it on the floor.

Agents may attach `rationale`, `concerns` and `confidence`. All three are
advisory text and none of them are part of any fingerprint.

## Consequences

**Good**

- Model output cannot change the size of a loss. The worst an agent can do is
  block trades, which is recoverable.
- Model non-determinism does not leak into the trade record. The executed trade
  is byte-identical to the backtested one.
- Prompt injection through market commentary or a broker payload cannot alter a
  trading parameter, because there is no parameter field to alter.

**Costs**

- The agent's genuinely useful observations ("the stop is inside the noise
  band") cannot be acted on automatically. They land in `concerns` and become a
  human's input to the next strategy version. This is slower and we accept it.
- A pathological or mis-prompted agent can veto everything, silently reducing
  the system to no-trades. Veto rate needs monitoring — tracked as open question
  Q-2 in `docs/PHASE_1_STATUS.md`.

## Enforcement

- `tests/unit/test_agent_authority.py` — 20 tests attacking the boundary from
  five directions: construction, `model_validate` smuggling, post-hoc mutation,
  gate mutation, and execution-boundary laundering.
- `src/agents/models.py::assert_review_carries_no_trading_authority` — import-time
  self-check.
