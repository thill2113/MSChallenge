# Phase 1 Status

**Date:** 2026-08-07
**Branch:** `claude/hybrid-trading-foundation-ra4xfx`
**Scope:** Phase 0/1 engineering foundation only.

**Quality gate:** 166 tests passing · mypy `--strict` clean across 71 files ·
ruff clean · ruff format clean.

---

## ⚠️ Read this first

**`docs/PRD.md` does not exist in this repository.** The brief named it as the
source of truth; the repo contained only `README.md` at the start of this work.

Everything below was built from the Phase 0/1 task list, which was detailed
enough to be self-sufficient for scaffolding. Nothing was invented to fill the
PRD's place — no strategies, no risk values, no product decisions. But the PRD
may contain requirements that contradict choices made here, and if it does, the
PRD wins. See blocker **B-1**.

---

## Completed

### Repository and tooling
- [x] Modular monorepo: 13 packages under `src/`, one per bounded context.
- [x] Python 3.12+, `uv` with committed `uv.lock`.
- [x] Pydantic v2, SQLAlchemy 2.0 (typed ORM), PostgreSQL via psycopg 3, FastAPI.
- [x] pytest, Hypothesis, ruff, mypy `--strict` (with the pydantic plugin).
- [x] Dockerfile (multi-stage, non-root, healthcheck) and docker-compose.
- [x] GitHub Actions: 7 jobs — lint, typecheck, test, invariants, security,
      data-integrity, docker.
- [x] `Makefile` mirroring every CI step for local use.

### Domain models — all 10 required, plus supporting types
- [x] `TradeRecord`, `MarketSnapshot`, `TradeCandidate`, `NoTrade`,
      `RiskDecision`, `AgentReview`, `OrderIntent`, `ExecutionResult`,
      `StrategyMetadata`, `StrategyVersion`.
- [x] Supporting: `Bar`, `Quote`, `Signal`, `SignalSet`, `RegimeAssessment`,
      `Position`, `PortfolioState`, `RiskLimits`, `RiskViolation`,
      `EvaluationContext`, `HumanApproval`, `PromotionRecord`, `MCPToolCall`,
      `StoredDocument`.
- [x] Every model frozen (`frozen=True`) and closed (`extra="forbid"`).
- [x] `float` **rejected** for money and size — not coerced.
- [x] Naive datetimes rejected; everything normalised to UTC.
- [x] No wall-clock or random defaults anywhere in the domain; ids are derived
      (UUIDv5) so replays are byte-identical.
- [x] SHA-256 `authoritative_fingerprint()` with canonical decimal spelling.

### Boundary enforcement
- [x] **Strategy** is the sole author of trading parameters; evaluation is a
      pure function of a frozen `EvaluationContext`.
- [x] **Risk** approves or rejects only — no resize, no re-stop, no re-target.
- [x] **Agent** is veto-only, enforced structurally: no parameter fields exist,
      the forbidden set is derived from `TradeCandidate`, and an import-time
      assertion fails the package if that is ever violated.
- [x] **Execution** accepts `OrderIntent` and nothing else, re-verifying the
      embedded approval at submission.
- [x] `ExecutionMode.LIVE` refused at construction; `RobinhoodMCPAdapter.submit`
      raises; MCP tool access is a read-only allowlist.

### Tests (166 total, 77 marked `invariant`)
| Suite | Tests | Proves |
|---|---:|---|
| `test_agent_authority.py` | 23 | **Task 6** — AgentReview cannot alter parameters |
| `test_risk_enforcement.py` | 20 | **Task 7** — failed risk cannot be bypassed |
| `test_strategy_determinism.py` | 10 | **Task 8** — identical inputs → identical decisions |
| `test_domain_models.py` | 24 | Immutability, exactness, fingerprint canonicalisation |
| `test_importers.py` | 21 | Import interfaces exist; parsers refuse to guess |
| `test_observability.py` | 18 | JSON logging, redaction, no trading routes |
| `test_ledger.py` | 15 | All five sources round-trip through one schema |
| `test_strategy_promotion.py` | 14 | ADR-004/005 versioning and human gating |
| `test_decision_pipeline.py` | 11 | End-to-end composition |
| `test_evidence_immutability.py` | 10 | ADR-003 write-once evidence |

The determinism suite includes a **negative control**: a deliberately impure
strategy that the harness must reject. Without it, a passing determinism test
would prove only that the test runner works.

### Data and ledger
- [x] `data/raw` (immutable, content-addressed, `0444`), `data/normalized`
      (git-ignored), `data/fixtures` — each with a README stating its rules.
- [x] `RawDocumentStore`: atomic write-once storage, digest verification,
      permission hardening.
- [x] `scripts/check_raw_immutability.py` wired into CI — fails the build on any
      modification or deletion under `data/raw`.
- [x] Normalized ledger: `raw_documents`, `ingestion_runs`, `trade_records`.
      One `TradeRecord` shape covers Claude recommendations, Robinhood
      orders/fills, MCP tool calls and strategy decisions.
- [x] Imports idempotent on fingerprint; externally-sourced rows required to
      carry `raw_document_sha256`.
- [x] Import **interfaces** with four declared placeholders, each listing the
      specific questions a human must answer. **No parsers written.**

### Documentation
- [x] `docs/ARCHITECTURE.md` — full system, decision-flow diagram, dependency
      rules, deliberate non-choices.
- [x] ADR-001 … ADR-005, each with context, decision, consequences (including
      costs), and a pointer to the tests that enforce it.

---

## Remaining (Phase 1, not yet done)

| # | Item | Why it is not done |
|---|---|---|
| R-1 | Alembic migration for the initial schema | `alembic` is a dependency but no migration is generated. Trivial once the schema is reviewed — deferred so review can change it freely first. |
| R-2 | Persist `StrategyRegistry` and promotion history | Both are in-process only; version history does not survive a restart. Needs tables + repository. |
| R-3 | Automatic `code_fingerprint` computation | Currently supplied by hand, so ADR-004 depends on human honesty. See Q-1. |
| R-4 | Capture real payloads into `data/raw` | The store exists and is empty. This is the next task — see below. |
| R-5 | Write the four importers | Blocked on R-4 by design (ADR-003). |
| R-6 | Readiness check that actually pings the database | `/readyz` reports `not_configured` — honest, but not yet useful. |
| R-7 | Veto-rate and rejection-rate metrics | Needed before an agent is trusted in the loop. See Q-2. |
| R-8 | Verify the CI workflow on GitHub | Every job's commands were run locally and pass (`ruff`, `mypy --strict`, `pytest`, `pip-audit` — no known vulnerabilities, `bandit` — clean, raw-data check). The workflow YAML itself has not executed on GitHub yet. |
| R-9 | **Docker build is unverified** | No Docker daemon was available in the environment this was authored in, so `Dockerfile` and `docker-compose.yml` were never built or run. The `docker` CI job will be the first real execution. Treat a first-run failure there as expected rather than surprising. |

---

## Blockers

| # | Blocker | Impact | Needs |
|---|---|---|---|
| **B-1** | **`docs/PRD.md` is absent** | Everything here was derived from the task brief. Requirements in the PRD that contradict these choices are currently unimplemented and unknown. | You: add the PRD, or confirm the task brief is the source of truth. |
| **B-2** | No historical data has been captured | Importers cannot be written (ADR-003 requires inspecting real payloads first). The ledger schema is untested against real shapes. | You: export Claude recommendation history; authorise read-only MCP calls to pull Robinhood orders/fills. |
| **B-3** | No production risk values exist | The system cannot approve anything. This is the correct failure mode, but it is a hard stop for any real use. | You: decision D-1. |

---

## Decisions requiring human approval

These are **yours**, not engineering's. Each blocks specific work.

### D-1 — Production risk limits *(blocks any real approval)*
Every `RiskLimits` field is required with no default, and no production values
exist in the repository. Needed:

| Parameter | Question |
|---|---|
| `max_account_risk_fraction` | Most you will lose on one trade, as a fraction of equity |
| `max_position_notional_fraction` | Largest single position, as a fraction of equity |
| `max_gross_exposure_fraction` | Total exposure ceiling, as a multiple of equity |
| `max_open_positions` | Maximum concurrent positions |
| `max_daily_loss_fraction` | Realised daily loss that stops new risk |
| `min_reward_risk_ratio` | Minimum acceptable reward:risk |
| `allowed_asset_classes` | Equities only, or options too? |

I have not proposed numbers. Suggesting risk parameters would make them look
like a recommendation, and they are not mine to make.

### D-2 — Market data source
Robinhood MCP (already available, but rate-limited and not designed for bulk
history), or a dedicated vendor? Determines the first `MarketDataProvider`.

### D-3 — Is `HumanApproval` enough, or do you want signed approvals?
Today it is a record: approver, timestamp, evidence URI, version fingerprint. It
attests but does not prove. Cryptographic signing (or requiring a signed git tag)
is a bigger lift. My read: the record is proportionate for a single-operator
system; revisit if anyone else gains commit access.

### D-4 — Regime taxonomy
`MarketRegime` currently has `UNKNOWN`, `TRENDING_UP`, `TRENDING_DOWN`,
`RANGE_BOUND`, `HIGH_VOLATILITY`. Are these the right states, and where are the
boundaries? No classifier can be written until you define this — it is a trading
decision, not an engineering one.

### D-5 — Paper trading: when, and through what?
Live execution is refused at the gateway. Enabling *paper* trading needs: a
venue that supports it, a decision on whether paper fills feed the ledger as
`SIMULATED` or as real events, and agreement on what evidence gates the
`PAPER → PRODUCTION` promotion.

### D-6 — Scope of Claude recommendation history
How far back, and does it include conversational context or only the extracted
recommendations? Affects both the export and what the importer can reconstruct.

---

## Unresolved engineering questions

| # | Question | Current state |
|---|---|---|
| **Q-1** | How is `code_fingerprint` computed and kept honest? | Supplied by hand. Options: hash the strategy module's source at import, or hash the git tree of `src/strategies/impl/`. The second survives refactors better. Needs a decision before the first real strategy. |
| **Q-2** | How do we detect a pathological agent? | A mis-prompted reviewer can veto everything, silently reducing the system to no-trades. Veto rate needs a monitor and a threshold. Not built. |
| **Q-3** | Residual forgery risk via `model_copy` | `model_copy(update=...)` skips validators. `OrderIntent` catches *incoherent* forgeries (approved-with-violations); a perfectly-formed one is indistinguishable at the type level. Mitigated by ledger provenance. Acceptable for a single-operator system; not acceptable if the process ever runs untrusted code. |
| **Q-4** | Top-level package names | `src/risk`, `src/signals`, `src/execution` are generic and could shadow a PyPI package in a shared environment. Followed the brief's paths literally. A `hybrid_trading.*` namespace would be safer; changing it later is a mechanical rename. |
| **Q-5** | Should `RiskDecision` be persisted before an `OrderIntent` is built? | Currently the approval travels in memory inside the intent. Persisting first would make the audit trail crash-proof at the cost of a write on the hot path. |
| **Q-6** | Multi-fill reconciliation | One order producing five fills — five ledger rows, or one aggregated position event? Cannot be answered until real fill payloads are inspected (B-2). |

---

## Confirmed constraints honoured

| Constraint | How |
|---|---|
| No live trading | `ExecutionMode.LIVE` raises at gateway construction; tested |
| No autonomous brokerage execution | `RobinhoodMCPAdapter.submit` raises; read-only tool allowlist; tested |
| No strategies invented or modified | Zero strategies in `src/`. The only strategy-shaped object is a test double under `tests/`, documented as having no market thesis |
| No production risk values | Every `RiskLimits` field required, no defaults; only fixture values exist, labelled as test data |
| No credentials stored | No secret is read anywhere in `src/`. MCP sessions are host-supplied; DSNs come from the environment with no default and are redacted before logging; `.env` is git-ignored |
| No microservices | One repo, one process, in-process calls |
| No premature optimisation | No caching, no async, no message broker, no P&L simulation |

---

## Next recommended development task

**Capture real Robinhood and Claude history into `data/raw`, then write the
Robinhood order importer against it.**

Concretely, in order:

1. Add a capture script (`scripts/capture_robinhood_history.py`) that calls the
   read-only MCP tools through `RobinhoodMCPAdapter.fetch` and stores each raw
   response verbatim via `RawDocumentStore`. Redact before storing (ADR-003).
2. Run it once. Inspect the payloads by hand and answer the open questions
   listed in `RobinhoodOrderImporter.open_questions`.
3. Copy redacted samples into `data/fixtures/` and implement
   `RobinhoodOrderImporter.parse`, test-driven against them.
4. Add the Alembic migration (R-1) and load the parsed records into a real
   PostgreSQL instance via docker-compose.

**Why this first:** it is the only remaining task with no dependency on a
decision from you. Everything else — real limits, a real strategy, a regime
classifier, paper trading — waits on D-1 through D-5. It also converts B-2 from
a blocker into progress, and it validates the ledger schema against real data
while the schema is still cheap to change.

**Do not start with:** writing a strategy (out of Phase 0/1 scope), or wiring an
LLM reviewer (needs Q-2 answered first, and the agent layer has nothing to
review until a strategy exists).
