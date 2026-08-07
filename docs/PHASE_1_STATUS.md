# Status — Phase 0/1 + Architecture Amendment

**Date:** 2026-08-07
**Branch:** `claude/hybrid-trading-foundation-ra4xfx`

**Quality gate:** 291 tests passing (178 marked `invariant`) · mypy `--strict`
clean across 84 files · ruff + format clean · pip-audit clean · bandit clean.

---

## ⚠️ Read this first

**`docs/PRD.md` still does not exist in this repository.** Both the original
Phase 0/1 work and this amendment were built from the task briefs. Nothing was
invented to fill the PRD's place — no strategies, no risk values, no product
decisions. Blocker **B-1**.

---

## 1. Which existing work remains valid

**Most of it.** The amendment changed the *shape of the pipeline*, not the
principles the foundation was built on. Everything below survived unchanged or
nearly so:

| Component | Status |
|---|---|
| `domain/` — frozen models, fingerprinting, exact decimals, derived ids | **Unchanged.** This is the load-bearing layer and the amendment made it more valuable, not less: automated execution depends entirely on binding an approval to an exact fingerprint. |
| `market_data/`, `signals/`, `regime/`, `portfolio/` | **Unchanged** (portfolio gained fields for the new gates). |
| `strategies/` models, determinism harness, registry | **Unchanged** except the stage ladder. |
| `risk/` engine and decision model | **Unchanged**; limits gained portfolio-level fields. |
| `journaling/` — ledger, evidence store, import interfaces | **Entirely unchanged.** |
| `observability/logging` | **Unchanged.** |
| ADR-001 (deterministic authority), ADR-003 (immutable raw data), ADR-004 (versioning) | **Unchanged and still correct.** |
| Agent veto-only rule | **Unchanged.** It now applies to two paths instead of one. |
| Tests for determinism, evidence, importers, ledger | **Unchanged.** |

The foundation was built around "decisions are frozen, fingerprinted and bound."
That assumption is what made this amendment an extension rather than a rewrite.

---

## 2. What conflicted with the amendment

Five things, all now resolved:

| Conflict | Resolution |
|---|---|
| **Synchronous agent in the execution path.** `run_reviewers()` called a reviewer inline before building an intent. | Split. `agents/gate.py` (synchronous) retained for research and backtesting; `agents/context.py` + `agents/store.py` added for production. The hot path reads a cache. |
| **`ExecutionGateway`** — a thin submit wrapper with `SIMULATED / PAPER / LIVE` modes. | **Deleted.** Replaced by `ExecutionEngine` + `FinalValidator` + the amended mode set. |
| **`BrokerAdapter`** — three methods, shaped around Robinhood. | **Deleted.** Replaced by the nine-method `Broker` protocol with declared capabilities. |
| **Promotion ladder** `RESEARCH → BACKTEST → PAPER → PRODUCTION`. | Replaced by the seven-stage amended ladder; `PRODUCTION` → `LIMITED_LIVE`; human approval now required at two transitions. |
| **Per-trade human approval** implied by ADR-005. | Removed and *replaced* — see ADR-008. Human authority moved to the control plane. |

Nothing was discarded that still had value.

---

## 3. What was refactored, and what is new

**Refactored:**
- `execution/models.py` — `OrderIntent` gained `decision_id`, `risk_amount`,
  `configuration_hash`, `market_snapshot_hash`, `reference_price`, and a derived
  `idempotency_key`.
- `risk/limits.py` — added weekly loss, drawdown, consecutive losses, open
  portfolio risk, correlated exposure, and two data-freshness ceilings.
- `portfolio/models.py` — positions carry `stop_price` and `correlation_group`;
  state carries weekly P&L, peak equity and loss streaks. An unprotected
  position reports its **whole notional** as open risk, not zero.
- `backtesting/replay.py` — now drives the real engine, validator and cached
  context gate rather than a parallel path.
- `brokers/robinhood_mcp/` — reshaped to the `Broker` interface, declaring **no
  capabilities**.

**New:**

| Package / module | What it does |
|---|---|
| `control_plane/` | The human-approved envelope: mode, enabled strategies, instruments, allocation, sessions, limits binding. Frozen, fingerprinted, changeable only with an approval bound to its exact revision. |
| `agents/context.py`, `agents/store.py` | `AgentContext` (expiring, scoped, veto-only) and the non-blocking store; all three fallback policies. |
| `execution/validator.py` | `FinalValidator` — 25 hard gates, no override. |
| `execution/engine.py` | `ExecutionEngine` — idempotency, translation, submission, typed failure handling, reconciliation. |
| `execution/killswitch.py` | Five scopes; engaging is easy, clearing is hard. |
| `brokers/base.py` | The generic `Broker` protocol, capabilities, typed errors. |
| `brokers/simulated/` | A **full** simulator with switchable failure modes. |
| `observability/events.py` | Event bus with an explicit immediate/deferred split. |

---

## 4. Updated architecture

See [`ARCHITECTURE.md`](ARCHITECTURE.md). Summary:

```
                    ASYNCHRONOUS
   agents ──publish──► Context/Veto Store ──┐
                                            │ cached read, never waits
                                            ▼
Market Data → Market State → Strategy → Risk → FinalValidator → OrderIntent
                                                    ▲    ▲            │
                              control plane ────────┘    │            ▼
                              kill switches ─────────────┘     ExecutionEngine
                                                                      │
                                                               Broker adapter
                                                                      │
                                                                    Venue
```

New ADRs: [ADR-006](adr/ADR-006-asynchronous-agent-context.md) (async agent
context), [ADR-007](adr/ADR-007-broker-abstraction.md) (broker abstraction),
[ADR-008](adr/ADR-008-automated-execution-and-hard-gates.md) (automated
execution, hard gates, where human authority moved). ADR-002 and ADR-005 carry
amendment notes.

---

## 5. Updated repository structure

Two packages added; the rest is as before.

```
src/control_plane/     [NEW]  human-approved configuration
src/brokers/base.py    [NEW]  generic Broker interface
src/brokers/simulated/ [NEW]  full simulator
src/agents/context.py  [NEW]  \ asynchronous veto/context
src/agents/store.py    [NEW]  /
src/execution/validator.py  [NEW]  FinalValidator
src/execution/engine.py     [NEW]  ExecutionEngine
src/execution/killswitch.py [NEW]  layered kill switches
src/observability/events.py [NEW]  event bus
src/execution/gateway.py    [DELETED]
src/execution/protocols.py  [DELETED]
```

---

## 6. Updated implementation sequence

Amendment build order items **1–13 are complete**.

| # | Item | Status |
|---|---|---|
| 1 | Domain model / scaffold | ✅ |
| 2 | Canonical trade ledger | ✅ |
| 3 | `MarketSnapshot` | ✅ |
| 4 | `TradeCandidate` | ✅ |
| 5 | Deterministic `RiskDecision` | ✅ |
| 6 | Immutable `OrderIntent` | ✅ |
| 7 | `Broker` interface | ✅ |
| 8 | Mock/simulation broker | ✅ |
| 9 | `ExecutionEngine` | ✅ |
| 10 | Execution modes | ✅ |
| 11 | `FinalValidator` | ✅ |
| 12 | Kill-switch framework | ✅ |
| 13 | Event/audit model | ✅ |
| 14 | One paper/sandbox broker adapter | ⬜ blocked on **D-2** |
| 15 | Deterministic strategy v1 | ⬜ blocked on **D-7** |
| 16 | Shadow execution | 🟡 **infrastructure complete and tested**; needs a real strategy to be meaningful |
| 17 | Async AgentContext / veto store | ✅ (store + policies; no publisher) |
| 18 | Claude integration | ⬜ blocked on **Q-2** |
| 19 | Paper trading | ⬜ blocked on 14 |
| 20 | Performance comparison | ⬜ |

### §16 first working concept — scorecard

| Criterion | Status |
|---|---|
| Live money disabled | ✅ refused by the control plane and by capability declaration |
| Historical data can be normalized | 🟡 schema + interfaces ready; no parsers (by design, ADR-003) |
| Market data creates `MarketSnapshot` | ✅ (from fixtures; no live feed — D-2) |
| A deterministic strategy evaluates it | 🟡 **harness proven with a test double; no real strategy — D-7** |
| `RiskEngine` approves/rejects | ✅ |
| `FinalValidator` works | ✅ 25 gates, 42 tests |
| `OrderIntent` is immutable | ✅ |
| `ExecutionEngine` works | ✅ |
| Simulated broker accepts/rejects | ✅ including timeout, outage, partial fill, idempotent replay |
| Complete execution events recorded | ✅ |
| SHADOW works end to end | ✅ proven in `test_decision_pipeline.py` |
| **Claude not required to execute** | ✅ **proven directly** — `test_the_pipeline_runs_with_no_agent_store_at_all` |
| Automated tests verify risk cannot be bypassed | ✅ 178 invariant tests |

**Everything except a real strategy is done.** The pipeline is proven end to end
with a fixture double explicitly documented as having no market thesis. Writing
the real one is decision **D-7**, below.

---

## 7. Current blockers

| # | Blocker | Impact | Needs |
|---|---|---|---|
| **B-1** | `docs/PRD.md` absent | Requirements that contradict these choices are unknown | You: supply it, or confirm the briefs are the source of truth |
| **B-2** | No historical data captured | Importers cannot be written (ADR-003); ledger schema untested against real shapes | You: export Claude history; authorise read-only MCP pulls |
| **B-3** | No production risk values | Nothing can be approved. **Now higher-stakes than before** — the limits replaced per-trade confirmation as the backstop | You: **D-1** |
| **B-4** | No strategy exists | SHADOW mode has nothing meaningful to shadow | You: **D-7** |

---

## 8. Exact next tasks

In order, with dependencies:

1. **Capture Robinhood + Claude history into `data/raw`** *(no dependency — start here)*
   Write `scripts/capture_robinhood_history.py` using `RobinhoodMCPAdapter.fetch`
   and `RawDocumentStore`. Redact before storing; ADR-003 makes it permanent.
2. **Inspect the payloads and write `RobinhoodOrderImporter`** *(depends on 1)*
   Answers the questions in `RobinhoodOrderImporter.open_questions`.
3. **Alembic migration + PostgreSQL load** *(depends on 2)*
4. **Persist the strategy registry, promotion history and control-plane
   revisions** *(no dependency)* — all three are in-process only today.
5. **Veto-rate and rejection-rate metrics** *(no dependency)* — deferred event
   handlers over the existing bus. Needed before Claude is trusted in the loop.
6. **Strategy v1** *(blocked on D-7)*
7. **Tradier/Alpaca adapter** *(blocked on D-2)*

---

## 9. What coding/research agents can do autonomously

**Safe to hand to an agent** — bounded, verifiable, no capital exposure:

| Task | Why it's safe |
|---|---|
| Tasks 1–5 above | Infrastructure; every one has a test to satisfy |
| Write a new `Broker` adapter against a documented API | The interface and its tests already exist |
| Backtest/replay tooling, reporting, dashboards | Read-only over recorded data |
| Property-test expansion, fuzzing the validator | Adds evidence, changes no behaviour |
| Performance work on the hot path | Bounded by existing tests |
| Research: candidate strategies, parameter studies, regime analysis | Produces *proposals*, not promotions |
| Drive strategies `DEVELOPMENT → SHADOW` | Analysis against recorded data; requires no approval by design |
| Monitor CI, fix failures, dependency upgrades | Standard maintenance |

**Not safe to hand to an agent** — enforced structurally, not by policy:

| Task | Blocked by |
|---|---|
| Promote to `PAPER` or `LIMITED_LIVE` | `authorize_promotion` requires a `HumanApproval` bound to the fingerprint |
| Change execution mode, limits, instruments, allocation | `apply_configuration_change` requires the same |
| Clear a risk-triggered kill switch | `KillSwitchRegistry.clear(automated=True)` raises |
| Choose production risk values | D-1 |
| Enable live trading | Unbuilt; `ExecutionMode.LIVE` refused |

An agent that tries any of the second group gets an exception, not a warning.
That is the design: agents increasingly build, test, monitor and improve the
platform — they do not gain unrestricted movement of capital.

---

## 10. Decisions that still require you

### D-1 — Production risk limits *(blocks everything; now higher-stakes)*
Thirteen required fields, no defaults, no production set in the repo. **The
amendment raised the stakes here**: these limits replaced per-trade confirmation
as the backstop against a strategy misbehaving.

| Parameter | Question |
|---|---|
| `max_account_risk_fraction` | Most you'll lose on one trade |
| `max_position_notional_fraction` | Largest single position |
| `max_gross_exposure_fraction` | Total exposure ceiling |
| `max_open_positions` | Concurrent position limit |
| `max_daily_loss_fraction` | Daily realised loss that stops new risk |
| `max_weekly_loss_fraction` | Weekly equivalent |
| `max_drawdown_fraction` | Decline from high-water mark that halts trading |
| `max_consecutive_losses` | Losing streak that halts the strategy |
| `max_open_portfolio_risk_fraction` | Total risk on the table at once |
| `max_correlated_exposure_fraction` | Ceiling per correlation group |
| `min_reward_risk_ratio` | Minimum acceptable reward:risk |
| `max_market_data_age_seconds` | When a snapshot is too old to trade on |
| `max_account_state_age_seconds` | When account state must be refetched |

I have not proposed numbers. Suggesting risk parameters would make them look
like a recommendation, and they are not mine to make.

### D-2 — Broker and market data choice
Tradier, IBKR, Alpaca? Determines the first real adapter *and* the first market
data provider. Needed for build-order items 14 and 19.

### D-3 — Signed approvals, or is a record enough?
`HumanApproval` attests but does not prove. My read: proportionate for a
single-operator system; revisit if anyone else gains commit access.

### D-4 — Regime taxonomy
Five states are defined; the boundaries are not. A trading decision, not an
engineering one.

### D-5 — What gates `PAPER → LIMITED_LIVE`?
The mechanism exists. What evidence should be required — how long in paper, how
many trades, what agreement between shadow and paper results?

### D-6 — Scope of Claude recommendation history export

### D-7 — **What is strategy v1?** *(new)*
The pipeline is proven with a test double that has no market thesis. A real
strategy needs: instrument(s), entry condition, stop placement rule, target rule,
position sizing rule, and the signals it depends on. **I have not invented one** —
the original brief said not to, and the amendment did not revoke that. If you
want me to author one, say so explicitly and tell me the thesis; otherwise supply
it and I'll implement, version and test it.

### D-8 — **Which `AgentContextPolicy` for production?** *(new)*
Three implemented, no default:

| Policy | Behaviour when Claude is silent / stale / down |
|---|---|
| `ALLOW_WITHOUT_AGENT_CONTEXT` | Trade anyway. Maximum availability, minimum agent value. |
| `REQUIRE_VALID_AGENT_CONTEXT` | Block. Maximum agent value; Claude's uptime becomes yours. |
| `FAIL_CLOSED_ON_AGENT_FAILURE` | Trade if Claude never spoke; block if it spoke and went stale. |

The amendment said explicitly not to invent the fallback, so I did not.

---

## 11. Unresolved engineering questions

| # | Question | State |
|---|---|---|
| **Q-1** | How is `code_fingerprint` computed and kept honest? | Hand-supplied. Needs deciding before strategy v1. |
| **Q-2** | How do we detect a pathological agent? | Veto rate is unmonitored. Under `ALLOW_WITHOUT` a broken agent degrades silently to no vetoes; under `REQUIRE_VALID` it halts everything. Metrics needed before Claude is in the loop. |
| **Q-3** | Residual forgery risk via `model_copy` | Incoherent forgeries are caught; a perfectly-formed one is not distinguishable at the type level. Mitigated by ledger provenance. |
| **Q-4** | Top-level package names could shadow PyPI packages | Followed the brief's paths literally. A `hybrid_trading.*` namespace is a mechanical rename. |
| **Q-5** | Should `RiskDecision` be persisted before the intent is built? | Approval travels in memory. Persisting first is crash-proof at the cost of a hot-path write. |
| **Q-6** | Multi-fill reconciliation | Unanswerable until real fill payloads are inspected (B-2). |
| **Q-7** | **Position lifecycle after the entry fill** *(new)* | The engine opens positions. Exit management, stop movement and `PositionClosed` are not implemented. Native bracket orders cover the simple case; a venue without them needs application-side management. |
| **Q-8** | **Reconciliation on restart** *(new)* | `reconcile()` exists per-intent. There is no start-up sweep that compares venue state against expected state and trips `RECONCILIATION_MISMATCH`. |
| **Q-9** | **Hot-path latency is unmeasured** *(new)* | Designed for speed and free of I/O, but never benchmarked. |

---

## 12. Constraints honoured

| Constraint | How |
|---|---|
| No live trading | `ExecutionMode.LIVE` refused by the control plane; no adapter declares `LIVE_TRADING` |
| No autonomous brokerage execution | Robinhood adapter declares no capabilities; engine refuses it before building an order |
| No strategies invented | Zero in `src/`. The only strategy-shaped object is a `tests/` double documented as having no market thesis |
| No production risk values | 13 required fields, no defaults; fixture values labelled as test data |
| No credentials stored | No secret read anywhere in `src/`; MCP sessions host-supplied; DSNs from env, redacted before logging |
| No microservices | One repo, one process, in-process calls |
| No premature optimisation | No async, no broker, no cache, no P&L simulation |
| Claude not required to execute | Proven by test |
| Fallback behaviour not invented | Three policies, no default |
