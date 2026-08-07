# ADR-008: Automated execution, hard gates, and where human authority moved

- **Status:** Accepted
- **Date:** 2026-08-07
- **Phase:** Architecture amendment
- **Amends:** ADR-005 (which required human approval per trade)

## Context

The original design required a human to confirm each trade. That is a real
control, and removing it is the single most consequential change in this
amendment — so it needs to be replaced with something, not merely deleted.

It also had a defect. Per-trade confirmation looks like a safety mechanism and
degrades into a rubber stamp: an operator approving the fortieth mechanically
identical trade of the day is not evaluating it. The control that exists on
paper is not the control that exists in practice, and the gap is invisible
precisely because the approvals keep being granted.

Meanwhile the confirmation makes automated execution impossible: a system that
waits for a human cannot act at market speed, and the trades it misses are not
recorded anywhere as a cost.

## Decision

**Individual trades execute automatically. Human authority moves to the control
plane, and every per-trade check a careful person would have made becomes a
deterministic gate that runs every time.**

### 1. Humans approve the envelope, not the instances

The control plane is frozen, fingerprinted, and changeable only with a recorded
`HumanApproval` bound to its exact fingerprint. Humans approve:

- execution mode, enabled strategy versions, permitted instruments
- account allocation, trading sessions, broker configuration
- the risk limit set (bound by fingerprint, so it cannot be swapped after
  approval)
- promotion `SHADOW → PAPER` and `PAPER → LIMITED_LIVE`

Revisions must increase; reverting is an explicit new revision, not a silent
reversion. `ExecutionMode.LIVE` is refused outright.

### 2. Execution modes

`DISABLED` → `SHADOW` → `PAPER` → `LIMITED_LIVE` → (`LIVE`, reserved).

**`SHADOW` is a real path, not a dry run.** Every step executes including the
full validator; only the broker call is skipped. A shadow run that would have
been rejected is recorded as rejected — which is what makes shadow evidence
worth anything when deciding whether to promote.

Mode and strategy stage must **both** allow the trade. A strategy at `SHADOW`
cannot trade paper even if the control plane is in `PAPER` mode. Two independent
controls must agree.

### 3. The final validator

Runs immediately before submission. Every gate runs — no short-circuiting,
because an operator fixing one breach needs to know what is queued behind it.
Every gate is deterministic and reaches no network.

Strategy enabled · version approved · stage/mode agreement · configuration hash
match · limits fingerprint match · market data fresh · account state fresh ·
instrument permitted · trading session · position limit · duplicate position ·
per-trade risk · open portfolio risk · daily loss · weekly loss · drawdown ·
consecutive losses · correlated exposure · buying power · liquidity · spread ·
duplicate order · kill switches · agent context · broker connectivity.

Failure of any required check means `ORDER_REJECTED`. **There is no override, no
`force` flag, and no agent path around it** — asserted structurally in the tests
rather than left to convention.

### 4. Broker failure is handled by kind, not lumped together

A timeout means **the order may be working.** The engine records `UNKNOWN`,
engages a strategy kill switch, and stops. It does **not** retry: retrying is
how one intended position becomes two. Reconciliation — asking the venue what
actually happened — is the only thing that resolves it.

Idempotency keys derive from the intent's authoritative fingerprint alone,
deliberately *not* from the configuration hash or creation time, so a retry
after a timeout reaches the venue as the same logical order rather than a
second one.

### 5. Kill switches: easy to engage, hard to clear

Five scopes (`SYSTEM`, `STRATEGY`, `SYMBOL`, `BROKER`, `ACCOUNT`). Deterministic
controls may engage automatically. Agents may only *recommend*.

**A switch engaged by a risk failure cannot be cleared by any automated
process.** Only two triggers self-clear — `BROKER_UNAVAILABLE` and
`STALE_MARKET_DATA` — because both describe a transient infrastructure condition
that is directly observable. Everything else describes a risk event, and a risk
event that resolves itself without anyone looking is exactly what this mechanism
exists to prevent.

Re-engaging an active switch keeps the **original** trigger: the earliest one is
closest to the root cause, and overwriting it with a later downstream symptom
loses the diagnosis.

### 6. The hot path stays short

Market event → market state → `Strategy.evaluate()` → `Risk.evaluate()` →
cached context read → `FinalValidator` → `OrderIntent` → `Broker.submit_order()`.

No synchronous inference, no GitHub calls, no report generation, no database
round trip, no human. Journalling, analytics and agent analysis run as deferred
event handlers. A handler that raises is logged and counted, never propagated —
a broken metrics sink must not abort a trade that already passed every risk gate.

**Risk validation is never traded away for latency.** Every gate above runs on
every order.

## Consequences

**Good**

- The system can act at market speed.
- The controls that exist are the controls that run — 25 gates, every time,
  identically, with no attention budget to exhaust.
- Rejections are typed and countable. A spike in `SPREAD_TOO_WIDE` is a market
  condition; a spike in `ACCOUNT_STATE_STALE` is an outage. Free-text approval
  notes could never tell you that.
- Shadow mode produces real evidence for the promotion decision.

**Costs — read these carefully**

- **A bad strategy can now lose money quickly.** Per-trade confirmation was a
  slow, unreliable, but real backstop against a strategy behaving unexpectedly.
  What replaces it is the limit set — which means the limits are now load-bearing
  in a way they were not before, and choosing them (open decision D-1) is the
  highest-stakes decision on the list.
- **The gates are only as good as their configuration.** A generous
  `max_daily_loss_fraction` is a generous daily loss.
- **A veto can arrive too late** (ADR-006).
- Correctness now depends on state freshness — stale account state or market
  data blocks trading, which is right, but means data-pipeline reliability is
  now a trading concern.
- The kill-switch asymmetry means an operator must be available to clear a
  tripped switch. That is deliberate and it is an on-call cost.

## Enforcement

- `tests/unit/test_final_validator.py` — 42 tests, one or more per gate, plus a
  structural assertion that no override parameter exists.
- `tests/unit/test_kill_switch_and_control_plane.py` — the engage/clear
  asymmetry and control-plane approval binding.
- `tests/integration/test_decision_pipeline.py::TestBrokerFailures` — timeout,
  reconciliation, duplicate refusal, outage, partial fill.
