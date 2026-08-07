# ADR-006: Asynchronous agent context

- **Status:** Accepted
- **Date:** 2026-08-07
- **Phase:** Architecture amendment
- **Amends:** ADR-002 (which assumed a synchronous reviewer)

## Context

ADR-002 established that the agent layer may veto and nothing else. It assumed
the agent sat *in* the decision path: strategy → risk → Claude → execution.

That shape has a defect the veto-only rule does not fix. A synchronous inference
puts a variable-latency, externally-hosted, rate-limited dependency between a
signal and a fill. Three consequences follow, and none of them are about safety:

- **Latency.** An inference is hundreds of milliseconds to seconds. The setup a
  strategy identified may not exist by the time the answer arrives.
- **Availability.** If Claude is down, rate-limited, or slow, the trading system
  is down. A deterministic engine that cannot trade without an LLM has inherited
  the LLM's uptime.
- **Non-determinism at the wrong moment.** The same candidate reviewed twice can
  get two answers. Inside the execution path, that makes execution itself
  irreproducible.

## Decision

**Agents run out of band and publish. The execution path reads a cache and never
waits.**

1. Agents analyse context on their own schedule — regime uncertainty, catalysts,
   contradictory evidence, abnormal conditions, strategy weakness, portfolio
   context — and publish an :class:`AgentContext` into a store.
2. The store lookup is a **dictionary read**. `AgentContextStore` implementations
   must not block on network I/O; a store that occasionally takes 400 ms is a
   store that occasionally costs a fill.
3. **Every context expires.** `expires_at` is mandatory. An opinion formed at
   09:31 about a fast-moving symbol is not an opinion about 15:45, and a cache
   that never expires is a cache that eventually lies.
4. The control plane sets an **independent ceiling** (`agent_context_max_age_seconds`).
   A publisher claiming a week-long expiry cannot thereby keep its opinion alive
   for a week.
5. Contexts are **scoped** to `(symbol, strategy_id)`, with a strategy-specific
   context beating a wildcard. A concern about one strategy's setup does not
   silently halt an unrelated strategy on the same symbol.
6. **A veto blocks under every policy.** That is the agent's one power and no
   configuration disables it.
7. **The fallback is configured, not invented.** Three policies, no default:

   | Policy | Agent silent | Context expired | Agent unhealthy |
   |---|---|---|---|
   | `ALLOW_WITHOUT_AGENT_CONTEXT` | trade | trade | trade |
   | `REQUIRE_VALID_AGENT_CONTEXT` | **block** | **block** | **block** |
   | `FAIL_CLOSED_ON_AGENT_FAILURE` | trade | **block** | **block** |

   The third exists because "never spoke" and "was speaking and stopped" are
   different facts. An agent that has no opinion is not a failure; an agent
   whose last opinion has gone stale might be.

8. **The authority limits are unchanged and extended.** `AgentContext` declares
   no trading parameter, forbids undeclared keys, and is checked at import time
   — the same guard as `AgentReview`, applied to the path that reaches
   production. The only fields it may share with a candidate are `symbol` and
   `strategy_id`, which are *addressing*: they narrow what a veto covers and
   cannot widen anything.

`agents.gate` (synchronous review) is retained for research and backtesting,
where waiting for a model is fine.

## Consequences

**Good**

- The trading system survives Claude being slow, rate-limited or down.
- Execution latency no longer depends on inference latency.
- Agent non-determinism cannot make execution irreproducible: the cached context
  either exists at decision time or it does not.
- Agents can now do *deeper* analysis, because nothing is waiting on them.

**Costs**

- **A veto can arrive too late.** Between publication cycles, a trade can execute
  that the agent would have blocked. That is the direct price of not waiting, and
  it is the right trade: a missed veto costs one trade, a stalled execution path
  costs every trade.
- Context staleness becomes a real operational concern with its own gate,
  ceiling and policy — three things that did not exist before.
- Agents need their own scheduling, health reporting and monitoring.

**Residual risk**

Veto rate is unmonitored. A mis-prompted agent under
`ALLOW_WITHOUT_AGENT_CONTEXT` degrades quietly to no vetoes; under
`REQUIRE_VALID_AGENT_CONTEXT` it halts trading entirely. Both need alerting
before an agent is trusted in the loop (open question Q-2).

## Enforcement

- `tests/unit/test_agent_context.py` — shape, expiry, scoping, all three
  policies, and the case where no store exists at all.
- `tests/integration/test_decision_pipeline.py::test_the_pipeline_runs_with_no_agent_store_at_all`
  — the load-bearing claim, tested directly.
- `src/agents/context.py::assert_context_carries_no_trading_authority` — import-time.
