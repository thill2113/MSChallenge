# Hybrid Trading System

Deterministic strategy authority with AI veto-only review.

**Phase 0/1 foundation.** No live trading. No autonomous brokerage execution.
No strategies. No production risk values.

---

## The rule everything follows

**Deterministic code decides. AI reviews. Humans promote.**

| Layer | May do | May never do |
|---|---|---|
| Strategy | Author every trading parameter | Read the clock, network, or global state |
| Risk | Approve or reject | Resize, re-stop, re-target |
| Agent | Veto; attach rationale | Alter any parameter; create a trade |
| Execution | Transmit an approved intent | Compute, round or adjust anything |
| Human | Promote to production | — |

Each row is enforced by types and proven by tests — not by convention. Start
with [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md), then the
[ADRs](docs/adr/).

---

## Quick start

```bash
make install        # uv sync --extra dev
make check          # everything CI runs: lint, types, tests, security, data integrity
make invariants     # just the ADR-backed boundary tests
make api            # operations API on :8000 (health only — it cannot trade)
```

Requires Python 3.12+ and [uv](https://docs.astral.sh/uv/).

---

## Layout

```
src/
├── domain/          shared kernel — value types, frozen bases, fingerprints
├── market_data/     MarketSnapshot, Bar, Quote, provider protocol
├── signals/         Signal, SignalSet, computer protocol
├── regime/          MarketRegime, RegimeAssessment, classifier protocol
├── strategies/      TradeCandidate, NoTrade, versioning, promotion, determinism
├── risk/            RiskLimits, RiskDecision, RiskEngine
├── portfolio/       Position, PortfolioState
├── execution/       OrderIntent, ExecutionResult, gateway
├── brokers/robinhood_mcp/   read-only MCP adapter
├── agents/          AgentReview, veto gate
├── backtesting/     deterministic decision replay
├── journaling/      trade ledger, evidence store, import interfaces
└── observability/   JSON logging, ops API

data/raw/          immutable source evidence (write-once, CI-enforced)
data/normalized/   regenerable output (git-ignored)
data/fixtures/     clearly-fake test data
docs/adr/          ADR-001 … ADR-005
tests/             166 tests, 77 marked `invariant`
```

---

## What is deliberately absent

- **Strategies.** None. The only strategy-shaped object is a test double under
  `tests/`, documented as having no market thesis.
- **Production risk values.** Every `RiskLimits` field is required with no
  default, so nothing can be approved until a human chooses real numbers.
- **Live and paper execution.** `ExecutionMode.LIVE` raises at gateway
  construction. `RobinhoodMCPAdapter.submit` raises.
- **Import parsers.** Interfaces only. Writing a parser against a format nobody
  has inspected produces code that is confidently wrong — each placeholder
  records the questions a human must answer first.
- **Credentials.** No secret is read anywhere in `src/`. MCP sessions are
  host-supplied; database URLs come from the environment and are redacted before
  logging.

---

## Status

[`docs/PHASE_1_STATUS.md`](docs/PHASE_1_STATUS.md) — what is done, what remains,
what is blocked, and the decisions that need a human.

**`docs/PRD.md` is not present in this repository.** Everything here was built
from the Phase 0/1 task brief. See blocker B-1.
