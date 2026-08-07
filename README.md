# Hybrid Trading System

A deterministic automated trading platform, with agents attached as
intelligence, research, auditing and veto layers.

**Phase 0/1 foundation + architecture amendment.** No live trading. No
strategies. No production risk values.

---

## The rule everything follows

**Deterministic code decides. Agents advise and may veto — asynchronously, from
cache. Humans approve the envelope, not each trade.**

The trading system runs without Claude. That is tested directly.

| Layer | May do | May never do |
|---|---|---|
| Strategy | Author every trading parameter | Read the clock, network, or global state |
| Risk | Approve or reject | Resize, re-stop, re-target |
| Agent | Veto (from cache); publish context | Alter a parameter; create a trade; change mode; clear a kill switch; promote |
| Final validator | Refuse an order | Be overridden — there is no flag |
| Execution | Validate, translate, transmit | Compute, round or adjust anything |
| Broker adapter | Translate to a venue | Reinterpret what was asked for |
| Human | Approve the control plane and promotions | — |

Each row is enforced by types and proven by tests — not by convention. Start
with [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md), then the
[ADRs](docs/adr/).

### The hot path

```
Market event → Strategy → Risk → cached veto check → FinalValidator → Broker
```

No synchronous inference, no database round trip, no human. 25 hard gates run on
every order, every time, and none of them can be skipped.

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
├── control_plane/   human-approved config: mode, instruments, sessions, limits
├── market_data/     MarketSnapshot, Bar, Quote, provider protocol
├── signals/         Signal, SignalSet, computer protocol
├── regime/          MarketRegime, RegimeAssessment, classifier protocol
├── strategies/      TradeCandidate, NoTrade, versioning, promotion, determinism
├── risk/            RiskLimits, RiskDecision, RiskEngine
├── portfolio/       Position, PortfolioState
├── agents/          AgentReview (research) + AgentContext/store (production)
├── execution/       OrderIntent, FinalValidator, ExecutionEngine, kill switches
├── brokers/         generic Broker interface + simulated + robinhood_mcp
├── backtesting/     deterministic decision replay through the real components
├── journaling/      trade ledger, evidence store, import interfaces
└── observability/   JSON logging, event bus, ops API

data/raw/          immutable source evidence (write-once, CI-enforced)
data/normalized/   regenerable output (git-ignored)
data/fixtures/     clearly-fake test data
docs/adr/          ADR-001 … ADR-008
tests/             291 tests, 178 marked `invariant`
```

## Execution modes

`DISABLED` → `SHADOW` → `PAPER` → `LIMITED_LIVE` → (`LIVE`, reserved and
unimplemented). Mode lives in the control plane and only a human changes it. A
strategy's promotion stage must independently permit the mode — two controls
must agree before an order leaves the building.

**SHADOW is a real path**: every gate runs, only the broker call is skipped.

---

## What is deliberately absent

- **Strategies.** None. The only strategy-shaped object is a test double under
  `tests/`, documented as having no market thesis.
- **Production risk values.** Every `RiskLimits` field is required with no
  default, so nothing can be approved until a human chooses real numbers.
- **Live execution.** `ExecutionMode.LIVE` is refused by the control plane, and
  no adapter declares live capability.
- **A real broker.** Only the simulator can place orders today. The Robinhood
  adapter declares no capabilities at all, so the engine refuses it before an
  order is even built.
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
from the Phase 0/1 brief and the architecture amendment. See blocker B-1.
